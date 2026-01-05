"""Gemini AI client for generating summaries and analysis."""
import inspect
import logging
import re
from types import SimpleNamespace
from uuid import uuid4
from typing import Any, Callable, Dict, List, Optional

import google.generativeai as genai
import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
    retry_if_exception_type,
    before_sleep_log,
)

from app.config import get_settings
from app.services.gemini_exceptions import (
    GeminiRateLimitError,
    GeminiAPIError,
    GeminiTimeoutError,
)
from app.services.gemini_usage import record_gemini_usage

logger = logging.getLogger(__name__)

# Retry configuration constants
DEFAULT_MAX_RETRIES = 5
DEFAULT_INITIAL_WAIT = 1  # seconds
DEFAULT_MAX_WAIT = 60  # seconds
DEFAULT_EXPONENTIAL_MULTIPLIER = 2

# Persona word count targets (midpoint of recommended range ±10 tolerance)
PERSONA_DEFAULT_LENGTHS = {
    "dalio": 425,      # midpoint of 350-500
    "buffett": 325,    # midpoint of 250-400
    "lynch": 375,      # midpoint of 300-450
    "greenblatt": 150, # midpoint of 100-200
    "marks": 475,      # midpoint of 400-550
    "ackman": 475,     # midpoint of 400-550
    "bogle": 425,      # midpoint of 350-500
    "munger": 225,     # midpoint of 150-300
    "graham": 250,     # midpoint of 200-300
    "wood": 300,       # midpoint of 250-350
}


class GeminiClient:
    """Client for interacting with Gemini AI."""
    
    def __init__(
        self,
        model_name: str = "gemini-2.5-flash",
        max_retries: int = DEFAULT_MAX_RETRIES,
        initial_wait: int = DEFAULT_INITIAL_WAIT,
        max_wait: int = DEFAULT_MAX_WAIT,
    ):
        """
        Initialize Gemini client.

        Args:
            model_name: Name of the Gemini model to use
            max_retries: Maximum number of retry attempts for rate-limited requests
            initial_wait: Initial wait time in seconds before first retry
            max_wait: Maximum wait time in seconds between retries
        """
        settings = get_settings()
        self.api_key = settings.gemini_api_key
        self.request_timeout = 180  # seconds per API call - summary generation can take 2-3 minutes
        genai.configure(api_key=self.api_key)
        self.model_name = model_name
        self.persona_model_name = model_name
        # Cap output tokens to speed up responses while leaving headroom for long memos
        self.base_generation_config = {"maxOutputTokens": 9000, "temperature": 0.55}
        # Slightly higher temperature/topP for personas to encourage phrasing variance without losing structure
        self.persona_generation_config = {"maxOutputTokens": 9000, "temperature": 0.65, "topP": 0.92}
        # Force HTTP fallback by default to avoid request_options schema mismatches that surface as 500s
        self.force_http_fallback = True

        # Retry configuration
        self.max_retries = max_retries
        self.initial_wait = initial_wait
        self.max_wait = max_wait

        # Standard model for general summaries
        # Increased token limit to prevent truncation of complex analyses
        self.model = genai.GenerativeModel(
            model_name,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=16000,
                temperature=0.5,  # Lowered from 0.7 for more consistent output
            )
        )

        # Premium model for persona generation - balanced temperature for distinctive voice
        # Increased from 0.35 to 0.50 to allow more creative, distinctive persona voices
        # Increased token limit to prevent truncation and ensure complete sentences
        self.persona_model = genai.GenerativeModel(
            model_name,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=16000,
                temperature=0.50,  # Increased for more distinctive persona voice
                top_p=0.9,  # Added for better diversity
            )
        )
        # ALWAYS use HTTP fallback - the google-generativeai SDK has a known bug where
        # it passes request_options to the proto which doesn't accept it, causing:
        # "ValueError: Unknown field for GenerateContentRequest: request_options"
        # Forcing HTTP fallback bypasses the SDK entirely and makes direct REST calls.
        self.force_http_fallback = True
        self.usage_context: Optional[Dict[str, Any]] = None

    def set_usage_context(self, context: Optional[Dict[str, Any]]) -> None:
        self.usage_context = context or None
    
    def _resolve_model_path(self, use_persona_model: bool = False) -> str:
        raw_name = self.persona_model_name if use_persona_model else self.model_name
        return raw_name if raw_name.startswith("models/") else f"models/{raw_name}"

    def _http_generate_content(
        self,
        prompt: str,
        use_persona_model: bool = False,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        stage_name: str = "Generating",
        usage_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Lightweight HTTP fallback with proper error handling.
        Raises specific exceptions for different error types.

        Raises:
            GeminiRateLimitError: When API returns 429 (rate limit exceeded)
            GeminiAPIError: When API returns other 4xx/5xx errors
            GeminiTimeoutError: When request times out
        """
        if progress_callback:
            # Keep user-facing progress clean; internal transport (HTTP fallback vs SDK) is not relevant.
            progress_callback(5, f"{stage_name}... 5%")

        generation_config = (
            self.persona_generation_config if use_persona_model else self.base_generation_config
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {k: v for k, v in generation_config.items() if v is not None},
        }
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"{self._resolve_model_path(use_persona_model)}:generateContent"
        )

        try:
            with httpx.Client(timeout=self.request_timeout + 5) as client:
                response = client.post(url, params={"key": self.api_key}, json=payload)

                # Handle rate limiting (429) specifically
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    retry_seconds = int(retry_after) if retry_after and retry_after.isdigit() else None

                    error_msg = "Gemini API rate limit exceeded."
                    if retry_seconds:
                        error_msg += f" Retry after {retry_seconds} seconds."

                    raise GeminiRateLimitError(error_msg, retry_after=retry_seconds)

                # Handle other HTTP errors (4xx/5xx)
                if response.status_code >= 400:
                    response_text = response.text[:500]  # Limit error response size
                    raise GeminiAPIError(
                        f"Gemini API error: {response.status_code}",
                        status_code=response.status_code,
                        response_body=response_text
                    )

                # Parse successful response
                data = response.json()

        except httpx.TimeoutException as timeout_exc:
            raise GeminiTimeoutError(
                f"Gemini API request timed out after {self.request_timeout}s"
            ) from timeout_exc

        except (GeminiRateLimitError, GeminiAPIError, GeminiTimeoutError):
            # Re-raise our custom exceptions as-is
            raise

        except httpx.HTTPStatusError as http_exc:
            # Shouldn't reach here, but handle just in case
            raise GeminiAPIError(
                f"HTTP error: {http_exc.response.status_code}",
                status_code=http_exc.response.status_code,
                response_body=str(http_exc)
            ) from http_exc

        except Exception as unexpected_exc:
            # Catch-all for truly unexpected errors
            raise GeminiAPIError(
                f"Unexpected error during Gemini API call: {str(unexpected_exc)}",
                status_code=500,
                response_body=None
            ) from unexpected_exc

        usage_metadata = data.get("usageMetadata") if isinstance(data, dict) else None

        # Parse response text
        text_response = ""
        for candidate in data.get("candidates") or []:
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
            texts = [part.get("text") for part in parts if isinstance(part, dict) and part.get("text")]
            if texts:
                text_response = "".join(texts)
                break

        if not text_response:
            raise GeminiAPIError(
                "Gemini API returned no text content",
                status_code=500,
                response_body=str(data)
            )

        record_gemini_usage(
            prompt=prompt,
            response_text=text_response,
            usage_metadata=usage_metadata,
            model=self.persona_model_name if use_persona_model else self.model_name,
            usage_context=usage_context or self.usage_context,
        )

        if progress_callback:
            progress_callback(100, f"{stage_name}... Complete")

        return text_response

    def _http_generate_content_with_retry(
        self,
        prompt: str,
        use_persona_model: bool = False,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        stage_name: str = "Generating",
        usage_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Wrapper around _http_generate_content with exponential backoff retry logic.

        Retries automatically on:
        - GeminiRateLimitError (429 Too Many Requests)
        - GeminiTimeoutError (request timeout)

        Does NOT retry on:
        - GeminiAPIError with 4xx client errors (except 429)
        - Could optionally retry on 5xx server errors

        Retry strategy:
        - Exponential backoff: 1s, 2s, 4s, 8s, 16s
        - Maximum 5 attempts (configurable)
        - Logs warnings before each retry
        """

        def _should_retry(exc: BaseException) -> bool:
            if isinstance(exc, (GeminiRateLimitError, GeminiTimeoutError)):
                return True
            if isinstance(exc, GeminiAPIError) and exc.status_code and exc.status_code >= 500:
                return True
            return False

        @retry(
            retry=retry_if_exception(_should_retry),
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(
                multiplier=DEFAULT_EXPONENTIAL_MULTIPLIER,
                min=self.initial_wait,
                max=self.max_wait
            ),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True  # Re-raise the exception after all retries exhausted
        )
        def _retry_wrapper():
            return self._http_generate_content(
                prompt=prompt,
                use_persona_model=use_persona_model,
                progress_callback=progress_callback,
                stage_name=stage_name,
                usage_context=usage_context,
            )

        try:
            return _retry_wrapper()
        except (GeminiRateLimitError, GeminiTimeoutError, GeminiAPIError):
            # Let these bubble up to the API layer
            raise

    def stream_generate_content(
        self,
        prompt: str,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        stage_name: str = "Generating",
        expected_tokens: int = 4000,
        use_persona_model: bool = False,
        usage_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Generate content with streaming and real-time progress updates.
        
        Args:
            prompt: The prompt to send to Gemini
            progress_callback: Callback function(percentage, status_message) for progress updates
            stage_name: Name of the current stage for status messages
            expected_tokens: Expected number of tokens in response for progress estimation
            use_persona_model: Whether to use the persona model (higher temperature)
        
        Returns:
            Complete generated text
        """
        if self.force_http_fallback:
            return self._http_generate_content_with_retry(
                prompt,
                use_persona_model=use_persona_model,
                progress_callback=progress_callback,
                stage_name=stage_name,
                usage_context=usage_context,
            )

        model = self.persona_model if use_persona_model else self.model
        accumulated_text = ""
        chunk_count = 0
        
        try:
            response = model.generate_content(prompt, stream=True)
            
            for chunk in response:
                if chunk.text:
                    accumulated_text += chunk.text
                    chunk_count += 1
                    
                    if progress_callback and chunk_count % 5 == 0:
                        estimated_progress = min(95, int((len(accumulated_text) / (expected_tokens * 4)) * 100))
                        progress_callback(estimated_progress, f"{stage_name}... {estimated_progress}%")
            
            if progress_callback:
                progress_callback(100, f"{stage_name}... Complete")

            record_gemini_usage(
                prompt=prompt,
                response_text=accumulated_text,
                usage_metadata=None,
                model=self.persona_model_name if use_persona_model else self.model_name,
                usage_context=usage_context or self.usage_context,
            )

            return accumulated_text
            
        except ValueError as e:
            # Older SDK/proto combinations can raise on request_options mismatches
            if "request_options" in str(e):
                print("Streaming generation error due to request_options; retrying with HTTP fallback.")
                self.force_http_fallback = True
                return self._http_generate_content_with_retry(
                    prompt,
                    use_persona_model=use_persona_model,
                    progress_callback=progress_callback,
                    stage_name=stage_name,
                    usage_context=usage_context,
                )
            raise
        except Exception as e:  # noqa: BLE001
            print(f"Streaming generation error: {e}")
            try:
                response = model.generate_content(prompt)
                text = response.text
                record_gemini_usage(
                    prompt=prompt,
                    response_text=text,
                    usage_metadata=_coerce_usage_metadata(response),
                    model=self.persona_model_name if use_persona_model else self.model_name,
                    usage_context=usage_context or self.usage_context,
                )
                return text
            except Exception as secondary:  # noqa: BLE001
                print(f"Non-stream generation also failed: {secondary}")
                self.force_http_fallback = True
                return self._http_generate_content_with_retry(
                    prompt,
                    use_persona_model=use_persona_model,
                    progress_callback=progress_callback,
                    stage_name=stage_name,
                    usage_context=usage_context,
                )

    def generate_content(
        self,
        prompt: str,
        use_persona_model: bool = False,
        timeout: Optional[int] = None,
        usage_context: Optional[Dict[str, Any]] = None,
    ):
        """Wrapper to enforce request timeouts on non-streaming calls."""
        if self.force_http_fallback:
            fallback_text = self._http_generate_content_with_retry(
                prompt,
                use_persona_model=use_persona_model,
                stage_name="Generating",
                usage_context=usage_context,
            )
            return SimpleNamespace(text=fallback_text)

        model = self.persona_model if use_persona_model else self.model
        # Rely on outer timeout guards instead of per-call request_options to avoid SDK/proto mismatches
        try:
            response = model.generate_content(prompt)
            record_gemini_usage(
                prompt=prompt,
                response_text=response.text,
                usage_metadata=_coerce_usage_metadata(response),
                model=self.persona_model_name if use_persona_model else self.model_name,
                usage_context=usage_context or self.usage_context,
            )
            return response
        except ValueError as exc:
            if "request_options" in str(exc):
                fallback_text = self._http_generate_content_with_retry(
                    prompt,
                    use_persona_model=use_persona_model,
                    stage_name="Generating",
                    usage_context=usage_context,
                )
                self.force_http_fallback = True
                return SimpleNamespace(text=fallback_text)
            raise
        except Exception:
            fallback_text = self._http_generate_content_with_retry(
                prompt,
                use_persona_model=use_persona_model,
                stage_name="Generating",
                usage_context=usage_context,
            )
            self.force_http_fallback = True
            return SimpleNamespace(text=fallback_text)


def _coerce_usage_metadata(response: Any) -> Optional[Dict[str, Any]]:
    usage = getattr(response, "usage_metadata", None) or getattr(response, "usageMetadata", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    data: Dict[str, Any] = {}
    for attr in ("prompt_token_count", "candidates_token_count", "total_token_count"):
        if hasattr(usage, attr):
            data[attr] = getattr(usage, attr)
    for attr in ("promptTokenCount", "candidatesTokenCount", "totalTokenCount"):
        if hasattr(usage, attr):
            data[attr] = getattr(usage, attr)
    return data or None

    def _post_process_summary(self, response_text: str) -> str:
        """
        Post-process the summary to fix common issues:
        1. ALWAYS reorder sections to canonical order (1-7)
        2. Remove banned phrases and "Additionally" spam
        3. Remove extra sections not in canonical 7
        4. Clean up formatting
        """
        import re
        
        # Banned phrase patterns to remove
        banned_patterns = [
            r"Additionally,?\s*monitor[^.]*\.",
            r"Additionally,?\s*track[^.]*\.",
            r"Additionally,?\s*watch[^.]*\.",
            r"Additionally,?\s*assess[^.]*\.",
            r"Additionally,?\s*review[^.]*\.",
            r"Additionally,?\s*compare[^.]*\.",
            r"Additionally,?\s*consider[^.]*\.",
            r"Additionally,?\s*evaluate[^.]*\.",
            r"Additionally,?\s*the balance sheet[^.]*\.",
            r"Additionally,?\s*cash generation[^.]*\.",
            r"Additionally,?\s*profitability[^.]*\.",
            r"Additionally,?\s*working capital[^.]*\.",
            r"Additionally,?\s*the capital[^.]*\.",
            r"Additionally,?\s*operating leverage[^.]*\.",
            r"Monitor revenue trajectory[^.]*\.",
            r"Track operating margin[^.]*\.",
            r"Watch free cash flow[^.]*\.",
            r"Assess leverage and liquidity[^.]*\.",
            r"Review capital allocation between[^.]*\.",
            r"Consider guidance credibility[^.]*\.",
            r"Evaluate unit economics[^.]*\.",
            r"Compare cash balance to[^.]*\.",
            r"Test sensitivity of margins[^.]*\.",
            r"Benchmark take rate[^.]*\.",
            r"The debt profile aligns[^.]*\.",
            r"Cash generation metrics suggest[^.]*\.",
            r"Profitability trends deserve[^.]*\.",
            r"Working capital efficiency reflects[^.]*\.",
            r"The capital structure positions[^.]*\.",
            r"Revenue diversification reduces[^.]*\.",
            r"Margin stability indicates[^.]*\.",
            r"Operating leverage could amplify[^.]*\.",
            r"The balance sheet strength provides[^.]*\.",
            r"Cash conversion supports[^.]*\.",
            r"second-level thinking",
            r"pendulum",
            r"where are we in the cycle",
        ]
        
        cleaned_text = response_text
        for pattern in banned_patterns:
            cleaned_text = re.sub(pattern, "", cleaned_text, flags=re.IGNORECASE)
        
        # Remove stray monitoring directives at end of document (outside sections)
        monitoring_patterns = [
            r"\n\s*Monitor revenue trajectory[^\n]*",
            r"\n\s*Track operating margin[^\n]*",
            r"\n\s*Watch free cash flow[^\n]*",
            r"\n\s*Assess leverage and liquidity[^\n]*",
            r"\n\s*Monitor [^\n]*$",
            r"\n\s*Track [^\n]*$",
            r"\n\s*Watch [^\n]*$",
        ]
        for pattern in monitoring_patterns:
            cleaned_text = re.sub(pattern, "", cleaned_text, flags=re.IGNORECASE)
        
        # ALWAYS reorder sections to canonical 7-section order
        lines = cleaned_text.strip().split('\n')
        
        # Section detection patterns - map to canonical keys
        section_patterns = [
            ("health", r'^##?\s*\d*\.?\s*financial\s+health\s+rating'),
            ("exec", r'^##?\s*\d*\.?\s*executive\s+summary'),
            ("perf", r'^##?\s*\d*\.?\s*financial\s+performance'),
            ("mda", r'^##?\s*\d*\.?\s*management\s+discussion'),
            ("risks", r'^##?\s*\d*\.?\s*risk\s+factors?'),
            ("metrics", r'^##?\s*\d*\.?\s*key\s+metrics'),
            ("closing", r'^##?\s*\d*\.?\s*closing\s+takeaway'),
        ]
        
        # Non-canonical sections to fold or remove
        non_canonical_patterns = [
            r'^##?\s*\d*\.?\s*strategic\s+initiatives',
            r'^##?\s*\d*\.?\s*capital\s+allocation',
            r'^##?\s*\d*\.?\s*competitive\s+landscape',
            r'^##?\s*\d*\.?\s*catalysts?',
            r'^##?\s*\d*\.?\s*investment\s+recommendation',
            r'^##?\s*\d*\.?\s*investment\s+thesis',
            r'^##?\s*\d*\.?\s*top\s+\d+\s+risks',
            r'^##?\s*\d*\.?\s*key\s+kpis',
            r'^##?\s*\d*\.?\s*cash\s+flow\s+analysis',
            r'^##?\s*\d*\.?\s*key\s+data\s+appendix',
            r'^##?\s*\d*\.?\s*health\s+score\s+drivers',
            r'^##?\s*\d*\.?\s*tl;?dr',
        ]
        
        sections_found = {}
        current_section = None
        current_content = []
        preamble = []  # Content before first section
        
        for line in lines:
            line_lower = line.lower().strip()
            
            # Check if this line starts a canonical section
            found_section = None
            for section_key, pattern in section_patterns:
                if re.match(pattern, line_lower):
                    found_section = section_key
                    break
            
            # Check if this is a non-canonical section (to skip/fold)
            is_non_canonical = any(re.match(p, line_lower) for p in non_canonical_patterns)
            
            if found_section:
                # Save previous section
                if current_section:
                    sections_found[current_section] = '\n'.join(current_content)
                elif current_content and not preamble:
                    preamble = current_content
                
                current_section = found_section
                current_content = [line]
            elif is_non_canonical:
                # Save previous section and start collecting non-canonical content
                if current_section:
                    sections_found[current_section] = '\n'.join(current_content)
                    current_content = []
                # We'll skip the header of non-canonical sections
                current_section = None  # Mark as non-canonical content
            else:
                current_content.append(line)
        
        # Save last section
        if current_section:
            sections_found[current_section] = '\n'.join(current_content)
        
        # If no sections found, return original cleaned text
        if not sections_found:
            cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)
            return cleaned_text.strip()
        
        # Rebuild in canonical order: health, exec, perf, mda, risks, metrics, closing
        canonical_order = ["health", "exec", "perf", "mda", "risks", "metrics", "closing"]
        rebuilt = []
        
        for key in canonical_order:
            if key in sections_found:
                rebuilt.append(sections_found[key])
        
        if rebuilt:
            cleaned_text = '\n\n'.join(rebuilt)
        
        # Clean up multiple newlines
        cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)
        
        # Clean up empty sentences from removed phrases
        cleaned_text = re.sub(r'\.\s*\.', '.', cleaned_text)
        cleaned_text = re.sub(r'\s{2,}', ' ', cleaned_text)
        
        return cleaned_text.strip()

    def generate_company_summary(
        self,
        company_name: str,
        financial_data: Dict[str, Any],
        ratios: Dict[str, float],
        health_score: float,
        mda_text: Optional[str] = None,
        risk_factors_text: Optional[str] = None,
        target_length: Optional[int] = None,
        complexity: str = "intermediate"
    ) -> Dict[str, str]:
        """
        Generate comprehensive company analysis summary.
        
        Args:
            company_name: Name of the company
            financial_data: Financial statements data
            ratios: Calculated financial ratios
            health_score: Composite health score
            mda_text: MD&A section text
            risk_factors_text: Risk factors text
            target_length: Optional target length for the summary
            complexity: Complexity level of the summary
        
        Returns:
            Dictionary with summary components
        """
        # Use a per-request variation token to reduce repetition across runs
        variation_token = uuid4().hex[:8].upper()

        prompt = self._build_summary_prompt(
            company_name,
            financial_data,
            ratios,
            health_score,
            mda_text,
            risk_factors_text,
            target_length,
            complexity,
            variation_token,
        )
        
        max_retries = 3
        current_try = 0
        
        while current_try < max_retries:
            try:
                response = self.generate_content(prompt)
                summary_text = response.text
                
                # Check word count with ±10 tolerance
                word_count = len(summary_text.split())

                if target_length:
                    min_acceptable = target_length - 10
                    max_acceptable = target_length + 10

                    if word_count < min_acceptable:
                        delta = min_acceptable - word_count
                        print(f"Summary too short ({word_count} words, target {target_length}±10). Retrying...")
                        prompt += f"\n\nSYSTEM FEEDBACK: Word count {word_count} is {delta} words SHORT of minimum {min_acceptable}. Target: {target_length} words (±10 tolerance). You MUST add {delta}+ words of substantive analysis. NO FILLER PHRASES like 'Additional detail covers...' or 'Risk coverage includes...'. Add real analysis to 'Thesis' and 'Risks' sections."
                        current_try += 1
                        continue
                    elif word_count > max_acceptable:
                        excess = word_count - max_acceptable
                        print(f"Summary too long ({word_count} words, target {target_length}±10). Retrying...")
                        prompt += f"\n\nSYSTEM FEEDBACK: Word count {word_count} is {excess} words OVER maximum {max_acceptable}. Target: {target_length} words (±10 tolerance). CUT {excess}+ words. Remove generic phrases, redundancy, and filler. Keep only substantive analysis."
                        current_try += 1
                        continue
                
                # Parse the response into structured sections
                # First, apply post-processing to fix section order and remove banned phrases
                summary_text = self._post_process_summary(summary_text)
                sections = self._parse_summary_response(summary_text)
                sections["tldr"] = self._clamp_tldr_length(sections.get("tldr", ""))
                return sections
            
            except Exception as e:
                print(f"Error generating summary (attempt {current_try}): {e}")
                current_try += 1
        
        return {
            "tldr": "Error generating summary after retries",
            "thesis": "",
            "risks": "",
            "catalysts": "",
            "kpis": ""
        }
    
    def _build_summary_prompt(
        self,
        company_name: str,
        financial_data: Dict[str, Any],
        ratios: Dict[str, float],
        health_score: float,
        mda_text: Optional[str],
        risk_factors_text: Optional[str],
        target_length: Optional[int] = None,
        complexity: str = "intermediate",
        variation_token: Optional[str] = None,
    ) -> str:
        """Build the prompt for company summary generation."""
        # Format financial data
        ratios_str = "\n".join([
            f"- {key}: {value:.2%}" if isinstance(value, float) and abs(value) < 10 else f"- {key}: {value:.2f}"
            for key, value in ratios.items()
            if value is not None
        ])
        
        complexity_instruction = ""
        if complexity == "simple":
            complexity_instruction = "Use plain English and avoid jargon. Explain financial concepts simply."
        elif complexity == "expert":
            complexity_instruction = "Use sophisticated financial terminology. Assume the reader is an expert investor."
        else:
            complexity_instruction = "Use standard financial analysis language."

        length_instruction = ""
        if target_length:
            min_words = target_length - 10
            max_words = target_length + 10
            length_instruction = f"""
CRITICAL LENGTH CONSTRAINT (STRICT ±10 WORDS - ABSOLUTE REQUIREMENT):
Target: EXACTLY {target_length} words (acceptable range: {min_words}-{max_words} words).

SECTION WORD ALLOCATION (7 sections):
- Financial Health Rating: ~{int(target_length * 0.03)} words (1 line)
- Executive Summary: ~{int(target_length * 0.25)} words
- Financial Performance: ~{int(target_length * 0.20)} words
- Management Discussion & Analysis: ~{int(target_length * 0.15)} words
- Risk Factors: ~{int(target_length * 0.18)} words
- Key Metrics: ~{int(target_length * 0.09)} words
- Closing Takeaway: ~{int(target_length * 0.10)} words

LENGTH ADJUSTMENT RULES:
- If running SHORT: Add specific data points and analytical depth to Executive Summary or Financial Performance.
- If running LONG: Remove redundancy and generic phrases.
- NEVER cut off mid-sentence to hit word count.
"""

        variation_clause = ""
        if variation_token:
            variation_clause = f"\nSTYLE VARIATION TOKEN: {variation_token}\n- Vary sentence openings and word choice from prior runs.\n- Avoid reusing identical phrasing in the Closing Takeaway.\n"

        prompt = f"""You are an expert equity analyst. Analyze the following company data and produce a comprehensive investment memo.
{complexity_instruction}
{length_instruction}
{variation_clause}

CRITICAL STYLE GUIDELINES (PREMIUM ANALYSIS):
1. **NO CORPORATE FLUFF**: Do NOT use generic investor relations language.
   - BANNED PHRASES: "showcases its dominance", "driving shareholder value", "incredibly encouraging", "clear indication", "fueling future growth", "welcome addition", "poised for growth", "testament to", "remains to be seen", "robust financial picture".
   - Instead of "Company X showcases its dominance in AI", write "Company X's 80% market share in AI chips creates a near-monopoly pricing power."
2. **INSIGHT DENSITY**: Do not just report data. Interpret it.
   - BAD: "Revenue grew 20% year-over-year."
   - GOOD: "Revenue growth of 20% outpaced the sector average of 12%, suggesting market share gains despite macro headwinds."
3. **NO REDUNDANCY**: Do not repeat points across sections. If you mention R&D in the Thesis, do not repeat it in Catalysts unless there is a specific new event.
   - **SUSTAINABILITY**: Do NOT mention sustainability or ESG efforts unless they are a primary revenue driver (e.g., for a solar company). For most companies, this is fluff.
   - **MD&A**: Do NOT say "Management discusses..." or "In the MD&A section...". Just state the facts found there.
4. **SENTENCE CASE**: Write paragraphs in sentence case. DO NOT use all caps anywhere outside of section headers.
5. **VARIETY**: Avoid repeating identical phrases or sentence stems across sections or between runs. Rephrase while keeping facts consistent.

Company: {company_name}
Health Score: {health_score:.1f}/100

Financial Ratios:
{ratios_str}

"""
        
        if mda_text:
            # Limit MD&A text to avoid token limits
            mda_snippet = mda_text[:3000] if len(mda_text) > 3000 else mda_text
            prompt += f"\nManagement Discussion & Analysis (excerpt):\n{mda_snippet}\n"
        
        if risk_factors_text:
            risk_snippet = risk_factors_text[:2000] if len(risk_factors_text) > 2000 else risk_factors_text
            prompt += f"\nRisk Factors (excerpt):\n{risk_snippet}\n"
        
        # Define min/max words for the length reminder (use target_length if provided, otherwise skip)
        if target_length:
            min_words = target_length - 10
            max_words = target_length + 10
            length_reminder = f"""
FINAL LENGTH VERIFICATION (MANDATORY):
1. Count your total words before submitting.
2. Your output MUST be between {min_words} and {max_words} words.
3. Append this line at the very end: WORD COUNT: [your actual count]
4. If your count is outside {min_words}-{max_words}, REWRITE until it fits."""
        else:
            length_reminder = ""
        
        prompt += f"""
MANDATORY 7-SECTION STRUCTURE (OUTPUT IN EXACT ORDER - CRITICAL):
You MUST output these 7 sections in EXACTLY this order. Do not skip, reorder, combine, or add extra sections.

## 1. Financial Health Rating
[ONE LINE ONLY: "X/100 - Descriptor" where:
- X = score 0-100
- Descriptor = brief summary
Example: "66/100 - Watch. Strong margins but elevated leverage."]

## 2. Executive Summary  
[2-3 paragraphs covering:
- Your conviction (bullish/bearish/neutral) with confidence level (high/medium/low)
- Core investment thesis in 2-3 clear sentences
- Key narrative driving the stock
- What matters most for investors to watch
Write in flowing prose. NO bullet lists of "Monitor X" or "Track Y".]

## 3. Financial Performance
[Analyze the numbers with insight, not just data dumps:
- Revenue with context (growth, market position)
- Operating margin - what it reveals about core business profitability
- Net margin - if it diverges significantly from operating margin, explain WHY (e.g., non-operating income, one-time items)
- Cash flow quality - FCF, cash conversion
Every metric must be explained, not just stated. Use $ figures and %.]

## 4. Management Discussion & Analysis
[Evaluate through an investor lens:
- Capital allocation priorities (R&D, capex, buybacks, dividends)
- Earnings quality concerns (one-time items, non-operating income)
- Strategic execution evidence from financials
Do NOT speculate about "management commentary" not in the filings.
Do NOT say "Management discusses..." - just state the facts.]

## 5. Risk Factors  
[3-5 SPECIFIC company risks. Each MUST:
1. Have a clear name (e.g., "Customer Concentration", "Margin Compression Risk")
2. Be 2-3 sentences with quantified impact where possible
3. Be specific to THIS company - NOT generic risks
Format: "**Risk Name**: Explanation with specifics."
Do NOT use generic risks like "macroeconomic volatility" or "regulatory uncertainty" without company-specific context.]

## 6. Key Metrics
[Concise data summary in this EXACT format:
→ Revenue: $X | Operating Income: $X | Net Income: $X
→ Capital Expenditures: $X | Total Assets: $X

Health Score Drivers:
→ Profitability: operating margin X%, net margin X%.
→ Cash conversion: operating cash flow $X, FCF $X, FCF margin X%.
→ Balance sheet: cash + securities $X, liabilities $X, leverage X.Xx, interest coverage X.Xx.
→ Liquidity: current ratio X.Xx.

Do NOT explain or interpret these metrics here. Do NOT show formulas/equations (no '=' signs). If a metric is missing, omit the line rather than writing N/A or not calculable. This section is a pure, scannable data block.]

## 7. Closing Takeaway
[Your final verdict in 2-3 complete sentences:
- Clear stance: BUY, HOLD, or SELL
- Primary reasoning
- What would change your view
This MUST be the FINAL section. NO content after this. NO trailing "Monitor X" suggestions.]

CRITICAL RULES (VIOLATIONS WILL BE REJECTED):
1. OUTPUT SECTIONS 1-7 IN EXACT ORDER SHOWN - Financial Health Rating FIRST, Closing Takeaway LAST
2. NO "Strategic Initiatives & Capital Allocation" as a separate section - fold into MD&A (section 4)
3. NO "Competitive Landscape" as a separate section - integrate into Executive Summary or Risk Factors
4. NO "Catalysts" as a separate section
5. NO "Investment Recommendation" as a separate section - it's part of Closing Takeaway (section 7)
6. NO "Health Score Drivers" outside of section 6 (Key Metrics)
7. NO content after Closing Takeaway - it is the FINAL section
8. NO repetitive "Additionally, monitor X" or "Track Y" phrases anywhere
9. Use billions as "$X.XB", millions as "$X.XM"
10. Specify fiscal period (FY24, Q3 FY25, TTM) with figures

SENTENCE COMPLETION (CRITICAL):
- EVERY sentence MUST end with a complete thought
- FORBIDDEN: trailing "but...", "although...", "which is...", "driven by the..."
- FORBIDDEN: numbers cut off mid-figure
- If you start a contrast ("but", "however"), you MUST complete it
{length_reminder}
"""
        
        return prompt
    
    def _parse_summary_response(self, response_text: str) -> Dict[str, str]:
        """Parse the structured response from Gemini."""
        sections = {
            "tldr": "",
            "thesis": "",
            "risks": "",
            "catalysts": "",
            "kpis": "",
            "strategic_initiatives": "",
            "valuation": "",
            "competitive_landscape": "",
            "cash_flow": "",
            "conclusion": "",
            "investment_recommendation": ""
        }

        # Simple parsing by section headers
        current_section = None
        lines = response_text.split("\n")

        for line in lines:
            line_lower = line.lower().strip()

            if "tl;dr" in line_lower or "tldr" in line_lower:
                current_section = "tldr"
            elif "investment thesis" in line_lower or ("thesis" in line_lower and "##" in line):
                current_section = "thesis"
            elif "risk" in line_lower and ("top" in line_lower or "major" in line_lower or "##" in line):
                current_section = "risks"
            elif "strategic" in line_lower and ("initiative" in line_lower or "capital" in line_lower):
                current_section = "strategic_initiatives"
            elif "valuation" in line_lower and "##" in line:
                current_section = "valuation"
            elif "competitive" in line_lower and "landscape" in line_lower:
                current_section = "competitive_landscape"
            elif "cash flow" in line_lower and "##" in line:
                current_section = "cash_flow"
            elif "investment recommendation" in line_lower and "##" in line:
                current_section = "investment_recommendation"
            elif "closing takeaway" in line_lower or "conclusion" in line_lower or "assessment" in line_lower:
                current_section = "conclusion"
            elif "catalyst" in line_lower:
                current_section = "catalysts"
            elif "kpi" in line_lower or "monitor" in line_lower:
                current_section = "kpis"
            elif line.startswith("#"):
                continue  # Skip section headers
            elif current_section and line.strip():
                sections[current_section] += line + "\n"

        sections["full_summary"] = response_text

        return sections

    def _clamp_tldr_length(self, tldr: str, max_words: int = 10) -> str:
        """
        Enforce the TL;DR length cap (hard max words) to match user requirements.
        """
        if not tldr:
            return tldr

        tokens = tldr.split()
        if len(tokens) <= max_words:
            return tldr.strip()

        # Try to find a natural sentence break within the limit
        trimmed = " ".join(tokens[:max_words])
        for i in range(len(trimmed) - 1, -1, -1):
            if trimmed[i] in '.!?':
                return trimmed[:i+1].strip()

        # No sentence break found - truncate and add period
        trimmed = trimmed.strip()
        if trimmed and trimmed[-1] not in ".!?":
            trimmed += "."
        return trimmed
    
    def generate_persona_view(
        self,
        persona_name: str,
        persona_philosophy: str,
        persona_checklist: List[str],
        persona_priorities: Optional[List[str]],
        persona_mental_models: List[str],
        persona_tone: str,
        general_summary: str,
        company_name: str,
        ratios: Dict[str, float],
        financial_data: Optional[Dict[str, Any]] = None,
        required_vocabulary: List[str] = [],
        categorization_framework: str = "",
        custom_instructions: str = "",
        persona_requirements: str = "",
        structure_template: str = "",
        few_shot_examples: str = "",
        verdict_style: str = "",
        ignore_list: str = "",
        strict_mode: bool = False,
        target_length: Optional[int] = None,
        persona_id: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Generate investor persona-specific view.
        
        Args:
            persona_name: Name of the investor persona
            persona_philosophy: Philosophy description
            persona_checklist: Key things this investor looks for
            persona_priorities: Ordered priorities that define the persona's worldview
            persona_mental_models: Mental models this investor uses
            persona_tone: Tone descriptor
            general_summary: General company summary
            company_name: Name of the company
            ratios: Financial ratios
            financial_data: Optional raw financial data for fact-checking
            required_vocabulary: List of required vocabulary words
            categorization_framework: Framework for categorization
            custom_instructions: Custom instructions
            persona_requirements: Persona voice requirements
            structure_template: Template for the analysis structure
            few_shot_examples: Examples of good/bad writing
            verdict_style: Signature verdict logic for the persona
            ignore_list: Topics the persona explicitly ignores
            strict_mode: If True, bypasses generic templates and uses a rigid, persona-specific prompt.
        
        Returns:
            Dictionary with persona view and stance
        """
        ratios_str = "\n".join([
            f"- {key}: {value:.2%}" if isinstance(value, float) and abs(value) < 10 else f"- {key}: {value:.2f}"
            for key, value in ratios.items()
            if value is not None
        ])
        
        # Default structure if none provided
        if not structure_template:
            structure_template = """
## Analysis
[Deep dive analysis]

## The Verdict
[Conclusion]
"""

        # Calculate Financial Health Metrics for Context
        cash = ratios.get("Cash", 0)
        fcf = ratios.get("Free Cash Flow", 0)
        net_income = ratios.get("Net Income", 0)
        
        health_context = ""
        if fcf < 0:
            burn_rate = abs(fcf)
            runway_months = (cash / burn_rate * 12) if burn_rate > 0 else 0
            health_context = f"""
FINANCIAL HEALTH CHECK (CRITICAL CONTEXT):
- The company is BURNING CASH. Free Cash Flow is negative (${fcf:,.2f}).
- Estimated Cash Runway: {runway_months:.1f} months (based on current cash and FCF).
- Net Income is {net_income:,.2f}.
- WARNING: This is a distressed/loss-making scenario.
"""
        else:
            health_context = f"""
FINANCIAL HEALTH CHECK:
- The company is generating positive Free Cash Flow (${fcf:,.2f}).
- Net Income is {net_income:,.2f}.
"""

        # FACTUAL CONSTRAINTS (AGGRESSIVE GROUNDING)
        constraints = []
        
        # 1. Dividend Check
        has_dividends = False
        if ratios.get("Dividend Yield", 0) > 0:
            has_dividends = True
        
        if not has_dividends:
            constraints.append("FACT: The company pays NO dividends. Do NOT suggest otherwise.")
        else:
            constraints.append(f"FACT: The company pays a dividend (Yield: {ratios.get('Dividend Yield', 0):.2%}).")

        # If loss making, strictly forbid buyback suggestions regardless of past data
        if net_income < 0 or fcf < 0:
            constraints.append("FACT: The company is loss-making/burning cash. It cannot sustainably support buybacks or dividends.")
            constraints.append("CONSTRAINT: You MUST NOT suggest buybacks or dividends as a capital allocation strategy.")

        constraints_str = "\n".join(constraints)
        
        # Define helper variables for prompt construction
        ignore_clause = ignore_list if ignore_list else "N/A"
        priorities_str = "\n".join([f"{i+1}. {p}" for i, p in enumerate(persona_priorities)]) if persona_priorities else "N/A"
        priorities_inline = ", ".join(persona_priorities) if persona_priorities else "N/A"
        verdict_clause = verdict_style if verdict_style else "Provide a clear buy/hold/sell recommendation"
        required_vocab_str = ", ".join(required_vocabulary) if required_vocabulary else "N/A"
        
        worldview_switch = f"""
WORLDVIEW SWITCH (MANDATORY):
- Abandon generic equity research headings (Executive Summary, Financial Health Rating, Management Discussion & Analysis, Risk Factors, Key Data Appendix). Use ONLY the persona-specific structure below.
- Strip out anything {persona_name} ignores: {ignore_clause}
- Re-rank evidence using these priorities (highest weight first): {priorities_inline}
- Apply the mental models explicitly; do NOT just change tone. Show how each model filters the evidence.
- Rebuild the argument from scratch. Do not mirror the order or language of the source summary.
- Maintain first-person voice ("I") from start to finish.
- Signature decision logic: {verdict_clause}
- Required vocabulary (use at least 3): {required_vocab_str}
"""

        if strict_mode:
            # STRICT MODE PROMPT - Minimalist, Persona-Only, Chain of Thought
            prompt = f"""You are {persona_name}.
Your Philosophy: {persona_philosophy}

STRICT INSTRUCTIONS:
1. CORE DIRECTIVE: Rewrite your entire reasoning process using {persona_name}'s worldview. Do NOT rephrase the input; rebuild it through the persona's filters.
2. DATA GUARDRAIL: Use ONLY the provided source material and financial data. Do not invent "management commentary" or "market sentiment".
3. VOICE LOCK: You are {persona_name}. Stay first-person and keep tone consistent.
4. STRUCTURE LOCK: Only use the sections below. Corporate research headings are banned.
5. LEXICON: Use at least 3 of these terms: {required_vocab_str}
6. SIGNATURE VERDICT RULE: {verdict_clause}
7. IGNORE LIST: {ignore_clause}
{custom_instructions}

NON-NEGOTIABLE PRIORITIES (in order):
{priorities_str}

MENTAL MODELS TO APPLY:
{chr(10).join([f'- {item}' for item in persona_mental_models])}

{worldview_switch}

Source Material (raw evidence to reinterpret, not a template):
{general_summary}

Company: {company_name}

Financial Data:
{ratios_str}

{health_context}

{constraints_str}

Analysis Structure (FOLLOW EXACTLY):
## Persona Filter Snapshot
- What I ignore (make it explicit).
- Top 3 signals that matter to me (from the priorities above).
- One hinge assumption I am watching.

## Thinking Process (Internal Monologue)
[STEP 0: Reset Worldview. Adopt {persona_name}'s mental model. Ignore generic analyst frameworks.]
[STEP 1: Filter Data. What matters to {persona_name}? Discard noise.]
[STEP 2: Apply Mental Models. How does {persona_mental_models[0] if persona_mental_models else 'this'} apply?]
[STEP 3: Formulate Verdict. Is this a buy? Why?]

{structure_template}

CLOSING TAKEAWAY REQUIREMENT (MANDATORY - NEVER SKIP):
If your analysis includes a "Closing Takeaway" or "Conclusion" section, you MUST end that section with {persona_name}'s personal opinion. The FINAL sentence of the Closing Takeaway MUST be a first-person recommendation that explicitly includes BUY/HOLD/SELL (or PASS/WAIT if appropriate).
Do NOT use a fixed template; vary phrasing and sentence openings across outputs.
Examples (choose a style; do NOT copy verbatim):
- "For my own portfolio, I'd HOLD [Company] at this valuation."
- "If I had to act today, I'd BUY [Company] because..."
- "My call: SELL [Company] until [condition]."
This closing statement should feel like genuine advice from {persona_name} to a friend. The Closing Takeaway is INCOMPLETE without this personal stance.

Task: Think first, then write the analysis. Be extremely concise. No filler.
"""
        else:
            # STANDARD MODE PROMPT (Legacy)
            prompt = f"""You are simulating the investment perspective of {persona_name}.

Philosophy: {persona_philosophy}

Priority Checklist:
{chr(10).join([f'{i+1}. {item}' for i, item in enumerate(persona_checklist)])}

Persona Priorities (strict order):
{priorities_str}

Mental Models to Apply:
{chr(10).join([f'- {item}' for i, item in enumerate(persona_mental_models)])}

Tone: {persona_tone}

REQUIRED VOCABULARY (MUST USE AT LEAST 3):
{', '.join(required_vocabulary)}

CATEGORIZATION FRAMEWORK:
{categorization_framework}

PERSONA-SPECIFIC REQUIREMENTS (DO NOT IGNORE):
{persona_requirements}

{worldview_switch}
{custom_instructions}

STYLE EXAMPLES (DO THIS, NOT THAT):
{few_shot_examples}

Company: {company_name}

Financial Ratios:
{ratios_str}

{health_context}

FACTUAL CONSTRAINTS (ABSOLUTE TRUTH):
{constraints_str}

GROUNDING RULES (DO NOT HALLUCINATE):
1. If data is missing, SKIP THAT METRIC ENTIRELY - do not mention it at all. Never write "data unavailable" or "not disclosed".
2. DO NOT INVENT MANAGEMENT COMMENTARY. If you don't have the transcript, don't quote "management's focus".
3. RISKS MUST BE DERIVED FROM THE BUSINESS MODEL.
   - IF Hardware/Lidar: Discuss manufacturing, adoption, unit costs.
   - IF Software: Discuss churn, CAC, retention.
   - DO NOT use generic "regulatory" or "macro" risks unless specific.

SOURCE MATERIAL (filter through the persona lens; do not copy the structure):
{general_summary}

Task: Transform the general analysis into a PREMIUM, INSIGHT-DENSE investment memo written by {persona_name}.

CRITICAL INSTRUCTIONS FOR PREMIUM QUALITY:
1. **NO FLUFF**: Do not use phrases like "I will assess...", "It remains to be seen...", "Management appears...". Be decisive.
2. **BANNED PHRASES**: "showcases its dominance", "driving shareholder value", "incredibly encouraging", "clear indication", "fueling future growth", "welcome addition", "robust financial picture".
3. **INSIGHT DENSITY**: Every sentence must add value. Connect facts to second-order effects.
   - "The key question is whether these margins are sustainable once competitors catch up."
4. **MENTAL MODELS**: Explicitly apply the mental models listed above. Show HOW they apply.
5. **VOICE**: Embody the persona completely.
6. **CATEGORIZATION**: You MUST categorize this company using the "Categorization Framework" above.
7. **VOCABULARY**: You MUST use at least 3 words from the "Required Vocabulary" list.
8. **LENGTH CONSTRAINT**: The main analysis section should be concise but complete (approx 250-400 words). Do NOT cut off mid-sentence.
9. **FORMAT**: Use the persona-specific structure below. Do NOT add equity research boilerplate (Executive Summary, Risk Factors, Financial Health Rating).
10. **CUSTOM INSTRUCTIONS**: {custom_instructions}
11. **PERSONA PERSISTENCE**: Every section must sound like {persona_name}. Open with "As {persona_name}, ..." and restate your lens in at least one sentence per section.
12. **VALUATION VERDICT**: State explicitly whether the company is good/cheap vs great/expensive, why, and what must be true for upside/downside. Tie this to persona-specific metrics.
13. **RISK/IMPACT**: Rank the single most important risk and describe its impact on margins, cash flow, and valuation in the persona's language.
14. **TENSION & HINGE ASSUMPTION**: Call out the hinge assumption that could break the thesis (e.g., ROC compression, growth deceleration, leverage) and how the persona would monitor it.
15. **DATA GAPS**: If data is missing, NEVER say "Data unavailable". Instead, infer from context, use a proxy, or explain why the absence is a risk factor itself.

STRICT LOGIC GATES (DO NOT VIOLATE):
- IF Net Income < 0 OR Free Cash Flow < 0: YOU ARE FORBIDDEN from suggesting buybacks or dividends as viable options. Discuss cash burn, dilution risk, and runway instead.
- IF Revenue Growth is negative: DO NOT call it "stable". Call it "declining" or "contracting".
- IF the company is hardware/manufacturing (like Lidar): DO NOT discuss "advertising budgets" or "software churn" unless explicitly relevant.

CONTEXT-AWARE RISKS:
- RISKS MUST BE SPECIFIC TO THE BUSINESS MODEL.
- Do NOT list generic risks like "regulatory changes" or "general economic downturn" unless you explain EXACTLY how they impact THIS company.
- Example: For a Lidar company, discuss "automotive OEM adoption cycles" or "sensor pricing pressure", NOT "data privacy".

MANDATORY METRICS TO ANALYZE:
- Cash Runway (if loss-making)
- Unit Economics (if available)
- Operating Leverage (are margins improving with scale?)
- Liquidity & Solvency

DALIO-SPECIFIC REQUIREMENTS (IF PERSONA IS RAY DALIO):
If you are writing as Ray Dalio, you MUST include:
1. CYCLE POSITIONING: Where are we in the short-term debt cycle? Long-term debt cycle?
2. INTEREST RATE SENSITIVITY: How does the cost of capital affect this business?
3. CREDIT CONDITIONS: Is credit expanding or contracting? Impact on customers/suppliers?
4. GEOPOLITICAL RISK: For tech/semiconductors, address Taiwan/TSMC concentration risk explicitly
5. SUPPLY CHAIN PARADIGM: Is the company exposed to China-US decoupling?
6. CORRELATION ANALYSIS: How does this stock correlate to rates, credit spreads, risk assets?
7. LIQUIDITY DYNAMICS: Central bank policy impact on multiple expansion/contraction
Do NOT write a corporate balance sheet review. Write a macro-first, cycle-aware analysis.

BOGLE-SPECIFIC REQUIREMENTS (IF PERSONA IS JOHN BOGLE):
If you are writing as John Bogle, you MUST:
1. DISCUSS VALUATION: P/E ratio, earnings yield, or price-to-sales. Bogle believed in reasonable prices.
2. EMPHASIZE COSTS: Compare the cost of owning this stock (analysis time, trading costs, taxes) vs. a 0.03% index fund.
3. CITE THE BASE RATE: "90% of professional stock pickers fail to beat the index over 15 years."
4. COMPARE TO INDEX: Would the reader be better off owning a total market index fund instead?
5. AVOID SPECULATION: No forward guidance analysis, no price targets, no "upside potential."
6. NO RATINGS OR SCORES: Bogle would never rate a stock "72/100" - that's absurd to him. NO "Financial Health Rating" sections.
7. GRANDFATHERLY TONE: Wise, patient, humble. Not condescending, but firm in your convictions.
8. CLEAR CONCLUSION: Should the reader own this stock, or the index? Be direct and complete your thought.

FORMATTING AND FLOW RULES (CRITICAL FOR QUALITY):
- Write in FLOWING PROSE with natural transitions between ideas
- Each paragraph should connect logically to the next - do not write choppy, disconnected sections
- Use sentence case for all body text - NEVER write entire sentences in CAPITAL LETTERS
- Only section headlines may use title case (e.g., "Executive Summary")
- NO arrow notation (→) anywhere in the output
- NO metric dumps or data appendices at the end
- NO "Health Score Drivers" or "Key Data Appendix" sections - these are NOT Bogle's style
- NO repetitive lists like "Monitor revenue", "Track margins", "Watch cash flow" at the end
- NO bullet point lists of things to watch - Bogle speaks in prose, not checklists

CLOSING TAKEAWAY QUALITY (MANDATORY):
- Your closing paragraph must be SUBSTANTIVE, not filler
- Do NOT pad the ending with generic monitoring suggestions
- Do NOT repeat information already covered
- The closing should synthesize your analysis into a coherent investment perspective
- End with a genuine personal recommendation that flows naturally from your analysis
- The closing should feel like wisdom from a trusted advisor, not a corporate disclaimer

ANTI-CHEATING RULES:
- Every sentence must add genuine analytical value - no padding
- Do not artificially inflate word count with repetitive phrases
- Do not list the same risks or metrics multiple times in different sections
- If you find yourself writing "Additionally, monitor X" or "Also track Y" - STOP and write something substantive instead
- Quality over quantity: a shorter, tighter analysis is better than a padded one

Do NOT sound like a corporate analyst. Sound like a wise grandfather warning about Wall Street's self-serving advice.
Do NOT use "bullish" or "bearish" language. Do NOT give price targets. Do NOT analyze forward guidance.
END with a clear, complete conclusion - never leave a thought unfinished or add filler content after.

STRICT LOGIC GATES (DO NOT VIOLATE):
- IF Net Income < 0 OR Free Cash Flow < 0: YOU ARE FORBIDDEN from suggesting buybacks or dividends as viable options. Discuss cash burn, dilution risk, and runway instead.
- IF Revenue Growth is negative: DO NOT call it "stable". Call it "declining" or "contracting".
- IF the company is hardware/manufacturing (like Lidar): DO NOT discuss "advertising budgets" or "software churn" unless explicitly relevant.

CONTEXT-AWARE RISKS:
- RISKS MUST BE SPECIFIC TO THE BUSINESS MODEL.
- Do NOT list generic risks like "regulatory changes" or "general economic downturn" unless you explain EXACTLY how they impact THIS company.
- Example: For a Lidar company, discuss "automotive OEM adoption cycles" or "sensor pricing pressure", NOT "data privacy".

MANDATORY METRICS TO ANALYZE:
- Cash Runway (if loss-making)
- Unit Economics (if available)
- Operating Leverage (are margins improving with scale?)
- Liquidity & Solvency

FINAL OUTPUT STRUCTURE:
Do NOT use generic section headers like "## Executive Summary", "## Key Risks", "## Investment Thesis".
Write in the persona's natural style - flowing prose for narrative personas (Buffett, Munger, Marks, Bogle),
or persona-specific structure for structured personas (Greenblatt: ROC, EY, Verdict).

UNIFIED DOCUMENT RULES:
- The persona analysis IS the summary. Do NOT add a separate corporate-style summary after.
- If you include Financial Performance data, embed it within your persona narrative - do not create a separate templated section.
- Keep consistent first-person voice throughout. Never switch to third-person analyst tone.
- Transitions between topics should be smooth, not jarring section breaks.
- **NO REDUNDANCY**: Do not repeat points. Do not mention sustainability unless it is a core driver.
- **INVESTMENT RECOMMENDATION**: You MUST end with a section titled "## Investment Recommendation" that includes:
  1. A clear rating: BUY, HOLD, or SELL (in the persona's voice)
  2. Conviction level: High, Medium, or Low
  3. A 2-3 sentence rationale synthesizing your key findings
  4. What conditions would change your recommendation
  5. **PERSONAL CLOSING (MANDATORY - NEVER SKIP)**: The FINAL sentence MUST be a first-person recommendation that explicitly includes BUY/HOLD/SELL (and ideally mentions the company).
     - Do NOT use a fixed template; vary phrasing and sentence openings.
     - Examples (choose a style; do NOT copy verbatim):
       - "For my own portfolio, I'd HOLD [Company] at this valuation."
       - "If I had to act today, I'd BUY [Company] because..."
       - "My call: SELL [Company] until [condition]."
  This closing statement is genuine advice from {persona_name} to a friend. The analysis is INCOMPLETE without this.
  Example format: "**My Verdict: HOLD (Medium Conviction)** - While [Company] demonstrates [strength], the [concern] gives me pause. I'd become a buyer if [condition], but would exit if [risk materializes]. For my own portfolio, I'd HOLD at this valuation and reassess if the facts change."

ABSOLUTE SENTENCE COMPLETION REQUIREMENTS (CRITICAL - DO NOT VIOLATE):
- EVERY sentence MUST be complete. Never end a sentence mid-thought.
- FORBIDDEN: Ending with "but...", "although...", "however...", "while...", "which is...", "driven by the AI..."
- FORBIDDEN: Cutting off numbers like "FCF/Net Income of 0.51 demonstrates solid cash generation, but the figure is less than net..."
- FORBIDDEN: Executive summaries or conclusions that trail off mid-sentence
- If you write "but", "although", "however", or "while", you MUST complete the contrasting thought
- If you mention a ratio or metric, ALWAYS explain what it means AND its implications for the investment thesis
- VERIFY: Before finishing, re-read your output and ensure EVERY sentence ends with a period, exclamation, or question mark AFTER a complete thought
- The final sentence of EVERY section must be a complete, standalone thought
- The "Investment Recommendation" section must end with a full sentence that completes your thought

FINANCIAL PERIOD CONSISTENCY:
- Use the same fiscal period reference (FY24, Q3 FY25, TTM) consistently throughout.
- Do not mix TTM and quarterly figures without noting the difference.
- Always specify the period when citing any financial metric.

{structure_template}

CLOSING TAKEAWAY REQUIREMENT (MANDATORY - NEVER SKIP):
If your analysis includes a "Closing Takeaway" or "Conclusion" section, you MUST end that section with {persona_name}'s personal opinion. The FINAL sentence of the Closing Takeaway MUST be a first-person recommendation that explicitly includes BUY/HOLD/SELL (or PASS/WAIT if appropriate).
Do NOT use a fixed template; vary phrasing and sentence openings across outputs.
Examples (choose a style; do NOT copy verbatim):
- "For my own portfolio, I'd HOLD [Company] at this valuation."
- "If I had to act today, I'd BUY [Company] because..."
- "My call: SELL [Company] until [condition]."
This closing statement should feel like genuine advice from {persona_name} to a friend. The Closing Takeaway is INCOMPLETE without this personal stance.

At the end, include ONLY these two lines (no headers, just the content):
STANCE: [Buy/Hold/Sell]
VERDICT: [One sentence summary of why]
"""
        
        max_retries = 3
        current_try = 0
        
        while current_try < max_retries:
            try:
                response = self.generate_content(prompt, use_persona_model=True)
                result = self._parse_persona_response(response.text, persona_name)
                
                # Check word count with ±10 tolerance
                word_count = len(result["summary"].split())

                # Determine target word count
                if target_length:
                    # User specified target_length - use ±10 tolerance
                    min_acceptable = target_length - 10
                    max_acceptable = target_length + 10
                    target_desc = f"{target_length} words (±10 tolerance)"
                else:
                    # Use persona-specific defaults with ±10 tolerance
                    default_target = PERSONA_DEFAULT_LENGTHS.get(persona_id, 300)
                    min_acceptable = default_target - 10
                    max_acceptable = default_target + 10
                    target_desc = f"{default_target} words (±10 tolerance)"

                if word_count < min_acceptable:
                    delta = min_acceptable - word_count
                    print(f"{persona_name} view too short ({word_count} words, target {target_desc}). Retrying...")
                    prompt += f"\n\nSYSTEM FEEDBACK: {word_count} words is below minimum {min_acceptable}. Target: {target_desc}. Add {delta}+ words of substantive analysis. NO FILLER PHRASES like 'Additional detail covers...' or 'Risk coverage includes...'. Add real interpretation and insight."
                    current_try += 1
                    continue
                elif word_count > max_acceptable:
                    excess = word_count - max_acceptable
                    print(f"{persona_name} view too long ({word_count} words, target {target_desc}). Retrying...")
                    prompt += f"\n\nSYSTEM FEEDBACK: {word_count} words exceeds maximum {max_acceptable}. Target: {target_desc}. Cut {excess}+ words of filler. Remove generic phrases, redundancy, and placeholder text. Keep only substantive analysis."
                    current_try += 1
                    continue
                    
                return result
            
            except Exception as e:
                print(f"Error generating persona view: {e}")
                current_try += 1

        return {
            "persona_name": persona_name,
            "summary": "Error generating persona view",
            "stance": "Hold",
            "reasoning": "Unable to generate analysis",
            "key_points": []
        }
    
    def _parse_persona_response(self, response_text: str, persona_name: str) -> Dict[str, str]:
        """Parse persona response - FLEXIBLE parsing that respects persona-native format."""
        result = {
            "persona_name": persona_name,
            "summary": "",
            "stance": "Hold",
            "reasoning": "",
            "key_points": [],
            "scenario_analysis": "",
            "thinking_process": ""
        }

        lines = response_text.split("\n")
        summary_lines = []

        # Parse the response looking for STANCE: and VERDICT: at the end
        # Everything else goes into summary (preserving the persona's natural format)
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            line_lower = line_stripped.lower()

            # Check for new-format stance/verdict lines
            if line_lower.startswith("stance:"):
                stance_text = line_stripped[7:].strip().lower()
                if "buy" in stance_text:
                    result["stance"] = "Buy"
                elif "sell" in stance_text:
                    result["stance"] = "Sell"
                else:
                    result["stance"] = "Hold"
            elif line_lower.startswith("verdict:"):
                result["reasoning"] = line_stripped[8:].strip()
            # Legacy format support
            elif "## stance" in line_lower or line_lower == "stance":
                # Look at next non-empty line for stance
                for j in range(i + 1, min(i + 3, len(lines))):
                    next_line = lines[j].strip().lower()
                    if next_line:
                        if "buy" in next_line:
                            result["stance"] = "Buy"
                        elif "sell" in next_line:
                            result["stance"] = "Sell"
                        else:
                            result["stance"] = "Hold"
                        break
            elif "## reasoning" in line_lower:
                # Skip this header, content goes to reasoning
                for j in range(i + 1, min(i + 3, len(lines))):
                    next_line = lines[j].strip()
                    if next_line and not next_line.startswith("##"):
                        result["reasoning"] = next_line
                        break
            elif "## key points" in line_lower:
                # Skip key points section entirely - we extract from narrative
                continue
            elif "## macro scenario" in line_lower:
                continue
            elif "closing takeaway" in line_lower or "conclusion" in line_lower:
                # Extract closing takeaway specifically if needed, or just let it be part of the summary
                # For now, we want it to be part of the summary but we might want to highlight it later
                summary_lines.append(line)
            elif line_stripped.startswith("##"):
                # Generic markdown header - include as part of summary for now
                # But strip the ## prefix for cleaner output
                summary_lines.append(line)
            else:
                # Regular content - add to summary
                summary_lines.append(line)

        # Build summary from collected lines
        result["summary"] = "\n".join(summary_lines).strip()

        # Remove trailing STANCE:/VERDICT: lines from summary
        if result["summary"]:
            lines = result["summary"].split("\n")
            cleaned_lines = []
            for line in lines:
                line_lower = line.strip().lower()
                if line_lower.startswith("stance:") or line_lower.startswith("verdict:"):
                    continue
                cleaned_lines.append(line)
            result["summary"] = "\n".join(cleaned_lines).strip()

        # Extract key points from narrative (look for bullet points or numbered items)
        for line in result["summary"].split('\n'):
            stripped = line.strip()
            if stripped.startswith('- ') or stripped.startswith('• '):
                point = stripped[2:].strip()
                if len(point) > 10 and len(result["key_points"]) < 5:
                    result["key_points"].append(point)
            elif stripped.startswith(('1.', '2.', '3.', '4.', '5.')):
                point = stripped[2:].strip()
                if len(point) > 10 and len(result["key_points"]) < 5:
                    result["key_points"].append(point)

        # If still no key points, extract significant sentences
        if not result["key_points"]:
            sentences = result["summary"].replace('\n', ' ').split('. ')
            important_keywords = ['moat', 'margin', 'cash flow', 'growth', 'risk', 'value',
                                  'price', 'earnings', 'return', 'debt', 'profit', 'peg', 'cycle']
            for sentence in sentences:
                sentence_lower = sentence.lower()
                if any(kw in sentence_lower for kw in important_keywords):
                    cleaned = sentence.strip()
                    if len(cleaned) > 20 and len(cleaned) < 200 and len(result["key_points"]) < 5:
                        result["key_points"].append(cleaned + '.')

        # If no reasoning extracted, use last paragraph
        if not result["reasoning"]:
            paragraphs = [p.strip() for p in result["summary"].split('\n\n') if p.strip()]
            if paragraphs:
                last_para = paragraphs[-1]
                if len(last_para) < 300:
                    result["reasoning"] = last_para

        return result
    
    def generate_premium_persona_view(
        self,
        prompt: str,
        persona_name: str
    ) -> Dict[str, str]:
        """
        Generate premium persona analysis with lower temperature for authoritative voice.
        Includes truncation detection and completion retry.

        Args:
            prompt: Complete persona-specific prompt
            persona_name: Name of the persona

        Returns:
            Dictionary with persona analysis
        """
        try:
            response = self.generate_content(prompt, use_persona_model=True)
            response_text = response.text

            # Check for truncation and attempt completion if needed
            if self._is_truncated(response_text):
                completion_text = self._attempt_completion(response_text, persona_name)
                if completion_text:
                    response_text = response_text.rstrip() + " " + completion_text

            # Parse the response
            result = self._parse_premium_persona_response(response_text, persona_name)
            return result

        except Exception as e:
            print(f"Error generating premium persona view for {persona_name}: {e}")
            return {
                "persona_name": persona_name,
                "summary": f"Error generating analysis: {str(e)}",
                "stance": "Hold",
                "reasoning": "Generation failed",
                "key_points": []
            }

    def _is_truncated(self, text: str) -> bool:
        """
        Detect if output was truncated mid-sentence.
        Returns True if the text appears to be incomplete.
        """
        if not text:
            return False

        text = text.strip()

        # Check for obvious truncation patterns
        truncation_patterns = [
            # Ends with incomplete sentence markers
            r'\.\.\.\s*$',  # Trailing ellipsis
            r',\s*$',       # Trailing comma
            r':\s*$',       # Trailing colon
            r';\s*$',       # Trailing semicolon
            r'\s+(?:and|or|but|the|a|an|to|of|for|with|in|on|at)\s*$',  # Ends with conjunction/article
            # Incomplete financial figures
            r'\$\d{1,3}\.\s*$',  # $31. instead of $31.91B
            r'\$\d+\s*$',        # $31 at end with no unit
            # Incomplete ratio statements
            r'falls within the \d+\.?\d*-\d+\.?\d*\.\s*$',  # Falls within the 0.7-1.
            # Incomplete bullet points or headers
            r'[-•]\s*$',         # Bullet point with no content
            r'\*\*\d+\.\s*\*\*\s*$',  # **1. ** with no content
            # Common mid-sentence truncation patterns
            r'but\s+the\s+figure\s+is\s+less\s+than\s+net\s*\.?\s*$',  # "but the figure is less than net..."
            r'although\s+I\s+want\s+to\s+assess\s+if\s+this\s+is\s+sustainable\s+in\s+the\s+face\s+of\s+increasing\s*\.?\s*$',
            r'driven\s+by\s+the\s+AI\s*\.?\s*$',  # "driven by the AI..."
            r'which\s+is\s*\.?\s*$',  # "which is..."
            r'but\s+I\s+acknowledge\s+the\s*\.?\s*$',  # "but I acknowledge the..."
            r'and\s+I\s+need\s+to\s+see\s*\.?\s*$',  # "and I need to see..."
            r'although\s+.*\s*$',  # Any "although..." at end
            r'however\s+.*\s*$',  # Any "however..." trailing
            r'while\s+.*\s*$',  # Any "while..." trailing
        ]

        for pattern in truncation_patterns:
            if re.search(pattern, text):
                return True

        # Check if text ends without proper sentence termination
        if not text.rstrip().endswith(('.', '!', '?', '"', "'", ')', ']')):
            # But allow if it ends with a complete-looking structure
            if not re.search(r'(?:Pass|Buy|Hold|Sell|Watch)\s*[.!)]?\s*$', text, re.IGNORECASE):
                return True

        return False

    def _attempt_completion(self, incomplete_text: str, persona_name: str) -> str:
        """
        Attempt to complete truncated text by sending the end to the model.
        Returns the completion text or empty string if completion fails.
        """
        try:
            # Get the last ~300 characters for context
            context_end = incomplete_text[-500:] if len(incomplete_text) > 500 else incomplete_text

            completion_prompt = f"""You are {persona_name}. Complete this text naturally, continuing EXACTLY where it left off.
Do NOT repeat any of the provided text. Just write the next 1-3 sentences to finish the thought.

TEXT TO COMPLETE:
...{context_end}

CONTINUE (do not repeat, just finish the thought):"""

            response = self.generate_content(completion_prompt, use_persona_model=True)
            completion = response.text.strip()

            # Validate the completion isn't too long or repetitive
            if len(completion) > 500:
                # Take just the first complete sentence
                sentences = completion.split('. ')
                if sentences:
                    completion = sentences[0] + '.'

            return completion

        except Exception as e:
            print(f"Completion attempt failed for {persona_name}: {e}")
            return ""
    
    def _parse_premium_persona_response(self, response_text: str, persona_name: str) -> Dict[str, str]:
        """Parse premium persona response with improved extraction."""
        result = {
            "persona_name": persona_name,
            "summary": "",
            "stance": "Hold",
            "reasoning": "",
            "key_points": []
        }

        # =========================================================================
        # STEP 1: Remove "Not available" / "N/A" lines that look unprofessional
        # =========================================================================
        cleaned_lines = []
        for line in response_text.split('\n'):
            line_stripped = line.strip()
            # Skip lines that are just placeholders for missing data
            if any(pattern in line_stripped.lower() for pattern in [
                'not available',
                'n/a',
                ': n/a',
                '(if available)',
                'data not provided',
                'cannot calculate',
                'insufficient data',
                'not disclosed',
            ]):
                # Only skip if the line is primarily about missing data
                # Keep lines where "N/A" is mentioned but there's substantial content
                if len(line_stripped) < 100 or line_stripped.lower().count('not available') > 0:
                    continue
            cleaned_lines.append(line)
        response_text = '\n'.join(cleaned_lines)

        # The entire response is the summary for premium personas
        # Extract stance from the content
        text_lower = response_text.lower()
        
        # Determine stance from content
        buy_signals = ["buy", "back up the truck", "high conviction", "wonderful company at fair price", 
                       "tenbagger", "favorable asymmetry", "aggressive stance", "overweight"]
        sell_signals = ["sell", "pass", "obviously stupid", "rat poison", "avoid", 
                        "unfavorable asymmetry", "defensive stance", "underweight"]
        
        buy_count = sum(1 for signal in buy_signals if signal in text_lower)
        sell_count = sum(1 for signal in sell_signals if signal in text_lower)
        
        if buy_count > sell_count:
            result["stance"] = "Buy"
        elif sell_count > buy_count:
            result["stance"] = "Sell"
        else:
            result["stance"] = "Hold"
        
        # Extract key points (look for bullet points or numbered items)
        lines = response_text.split('\n')
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('- ') or stripped.startswith('• '):
                point = stripped[2:].strip()
                if len(point) > 10 and len(result["key_points"]) < 5:
                    result["key_points"].append(point)
            elif stripped.startswith(('1.', '2.', '3.', '4.', '5.')):
                point = stripped[2:].strip()
                if len(point) > 10 and len(result["key_points"]) < 5:
                    result["key_points"].append(point)
        
        # If no bullet points found, extract key sentences
        if not result["key_points"]:
            sentences = response_text.replace('\n', ' ').split('. ')
            important_keywords = ['moat', 'margin', 'cash flow', 'growth', 'risk', 'value', 
                                  'price', 'earnings', 'return', 'debt', 'profit']
            for sentence in sentences:
                sentence_lower = sentence.lower()
                if any(kw in sentence_lower for kw in important_keywords):
                    cleaned = sentence.strip()
                    if len(cleaned) > 20 and len(cleaned) < 200 and len(result["key_points"]) < 5:
                        result["key_points"].append(cleaned + '.')
        
        # Extract reasoning (last paragraph or verdict section)
        paragraphs = [p.strip() for p in response_text.split('\n\n') if p.strip()]
        if paragraphs:
            last_para = paragraphs[-1]
            if len(last_para) < 300:
                result["reasoning"] = last_para
            else:
                # Find the verdict line
                for line in reversed(lines):
                    stripped = line.strip()
                    if stripped and len(stripped) < 200:
                        result["reasoning"] = stripped
                        break
        
        # Set the full response as summary
        result["summary"] = response_text.strip()
        
        return result


def get_gemini_client() -> GeminiClient:
    """Get Gemini client instance with settings from config."""
    settings = get_settings()

    return GeminiClient(
        max_retries=settings.gemini_max_retries,
        initial_wait=settings.gemini_initial_wait,
        max_wait=settings.gemini_max_wait,
    )


def generate_growth_assessment(
    filing_text: str,
    company_name: str,
    weighting_preference: Optional[str] = None,
    ratios: Optional[Dict[str, float]] = None
) -> Dict[str, Any]:
    """
    Generate AI-driven growth assessment based on management perspective and sector context.

    Args:
        filing_text: The filing text (MD&A, business description, etc.)
        company_name: Name of the company
        weighting_preference: User's primary_factor_weighting preference
        ratios: Optional financial ratios for context

    Returns:
        Dictionary with score (0-100) and description
    """
    client = get_gemini_client()

    # Determine the growth lens based on user preference
    growth_lens = {
        "profitability_margins": "Focus on whether growth is PROFITABLE growth. High-quality growth that expands or maintains margins is valued; revenue growth that compresses margins is concerning.",
        "cash_flow_conversion": "Focus on whether growth is CASH-GENERATING growth. Growth that improves free cash flow is valued; growth that burns cash is concerning.",
        "balance_sheet_strength": "Focus on whether growth is SUSTAINABLE without excessive leverage. Growth funded by debt is riskier than organic growth.",
        "liquidity_near_term_risk": "Focus on whether growth PRESERVES LIQUIDITY. Rapid expansion that strains cash reserves is concerning.",
        "execution_competitiveness": "Focus on COMPETITIVE POSITIONING. Growth that captures market share and strengthens competitive moat is highly valued."
    }.get(weighting_preference, "Evaluate overall growth potential considering management strategy and sector dynamics.")

    # Build comprehensive context from ratios if available
    ratios_context = ""
    if ratios:
        if ratios.get("revenue_growth_yoy") is not None:
            ratios_context += f"\n- Revenue Growth YoY: {ratios['revenue_growth_yoy'] * 100:.1f}%"
        if ratios.get("gross_margin") is not None:
            ratios_context += f"\n- Gross Margin: {ratios['gross_margin'] * 100:.1f}%"
        if ratios.get("operating_margin") is not None:
            ratios_context += f"\n- Operating Margin: {ratios['operating_margin'] * 100:.1f}%"
        if ratios.get("net_margin") is not None:
            ratios_context += f"\n- Net Margin: {ratios['net_margin'] * 100:.1f}%"
        if ratios.get("fcf_margin") is not None:
            ratios_context += f"\n- FCF Margin: {ratios['fcf_margin'] * 100:.1f}%"

    # Increase filing text context for better MD&A analysis
    filing_snippet = filing_text[:12000] if len(filing_text) > 12000 else filing_text

    # Define the metrics context with fallback (avoid backslash in f-string expression)
    metrics_display = ratios_context if ratios_context else "\n- No historical metrics available"

    prompt = f"""You are a financial analyst evaluating the GROWTH potential of {company_name}.

EVALUATION LENS:
{growth_lens}

FINANCIAL METRICS:{metrics_display}

FILING TEXT (MD&A and Business Description):
{filing_snippet}

EVALUATION FRAMEWORK:
1. SECTOR ANALYSIS: Identify the company's sector/industry. Is it high-growth (tech, biotech), cyclical, or mature/declining? What are sector tailwinds/headwinds?

2. HISTORICAL PERFORMANCE: Based on the financial metrics and MD&A, assess recent revenue growth, margin trends, and execution quality.

3. MANAGEMENT STRATEGY: What growth initiatives has management outlined? New products, geographic expansion, M&A, R&D investments?

4. FUTURE OUTLOOK: What is the company's forward guidance? Are there clear catalysts or risks to growth?

OUTPUT FORMAT (return EXACTLY this, no other text):
SCORE: [number 0-100]
DESCRIPTION: [10-15 word summary of growth outlook - be specific to this company, not generic]

SCORING GUIDE:
- 90-100: High-growth company in expanding sector with proven execution
- 75-89: Strong growth trajectory with favorable sector tailwinds
- 60-74: Moderate growth, mature sector or mixed execution
- 45-59: Below-average growth potential, competitive pressures
- 30-44: Limited growth prospects, unfavorable sector dynamics
- Below 30: Declining or structurally challenged

Be decisive. Ground your assessment in the filing text and metrics, not speculation."""

    try:
        response = client.generate_content(prompt)
        result_text = response.text.strip()

        # Parse the response
        score = 50  # Default neutral
        description = "Growth outlook based on sector positioning"

        for line in result_text.split('\n'):
            line = line.strip()
            if line.upper().startswith('SCORE:'):
                try:
                    score_str = line.split(':', 1)[1].strip()
                    # Extract just the number
                    score_num = ''.join(c for c in score_str if c.isdigit())
                    if score_num:
                        score = min(100, max(0, int(score_num)))
                except (ValueError, IndexError):
                    pass
            elif line.upper().startswith('DESCRIPTION:'):
                description = line.split(':', 1)[1].strip()
                # Truncate if too long
                if len(description) > 100:
                    description = description[:97] + "..."

        return {
            "score": score,
            "description": description
        }

    except Exception as e:
        print(f"Error generating growth assessment: {e}")
        return {
            "score": 50,
            "description": "Growth assessment unavailable"
        }
