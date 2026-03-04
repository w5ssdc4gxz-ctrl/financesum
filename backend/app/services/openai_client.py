"""OpenAI GPT-5.2 client for generating summaries and analysis."""

import base64
import inspect
import json
import logging
import os
import re
import time
from types import SimpleNamespace
from uuid import uuid4
from typing import Any, Callable, Dict, List, Optional

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
from app.services.ai_exceptions import (
    AIClientError,
    AIRateLimitError,
    AIAPIError,
    AITimeoutError,
)
from app.services.ai_usage import record_ai_usage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry configuration constants
# ---------------------------------------------------------------------------
DEFAULT_MAX_RETRIES = 2
DEFAULT_INITIAL_WAIT = 1  # seconds
DEFAULT_MAX_WAIT = 15
DEFAULT_EXPONENTIAL_MULTIPLIER = 2

# ---------------------------------------------------------------------------
# Persona word count targets (midpoint of recommended range ±10 tolerance)
# ---------------------------------------------------------------------------
PERSONA_DEFAULT_LENGTHS = {
    "dalio": 425,
    "buffett": 325,
    "lynch": 375,
    "greenblatt": 150,
    "marks": 475,
    "ackman": 475,
    "bogle": 425,
    "munger": 225,
    "graham": 250,
    "wood": 300,
}

# ---------------------------------------------------------------------------
# OpenAI model names
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "gpt-5.2"
DEFAULT_PERSONA_MODEL = "gpt-5.2"

# OpenAI API base URL
OPENAI_API_BASE = "https://api.openai.com/v1"
DEFAULT_SUMMARY_AGENT1_MAX_OUTPUT_TOKENS = 700
DEFAULT_SUMMARY_RESEARCH_MAX_WORDS = 350
TLDR_EXACT_WORD_TARGET = 10
TLDR_REWRITE_MAX_ATTEMPTS = 5
SUMMARY_TARGET_BAND_TOLERANCE = 20

_TLDR_END_PUNCT_RE = re.compile(r'[.!?](?:["\')\]]+)?$')
_TLDR_DANGLING_ENDINGS = {
    "and",
    "or",
    "but",
    "with",
    "to",
    "for",
    "because",
    "if",
}
_TLDR_REPETITION_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "if",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "so",
    "that",
    "the",
    "their",
    "there",
    "they",
    "this",
    "to",
    "was",
    "we",
    "with",
}
_TLDR_OUTER_QUOTES = (
    ('"', '"'),
    ("'", "'"),
    ("\u201c", "\u201d"),
    ("\u2018", "\u2019"),
)


class TLDRContractError(AIClientError):
    """Raised when the TL;DR exact-word contract cannot be satisfied."""


def normalize_tldr_contract_text(text: str) -> str:
    """Normalize a TL;DR line for exact-word contract validation/counting."""
    out = str(text or "")
    out = out.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    out = re.sub(r"\s+", " ", out).strip()

    for _ in range(3):
        prev = out
        out = re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+)+", "", out).strip()
        out = re.sub(r"^(?:tl;?dr|tldr)\s*[:\-]\s*", "", out, flags=re.IGNORECASE).strip()
        if out == prev:
            break

    if len(out) >= 2:
        for left, right in _TLDR_OUTER_QUOTES:
            if out.startswith(left) and out.endswith(right):
                out = out[len(left) : len(out) - len(right)].strip()
                break

    out = re.sub(r"\s+", " ", out).strip()
    return out


def count_tldr_contract_words(text: str) -> int:
    normalized = normalize_tldr_contract_text(text)
    return len(normalized.split()) if normalized else 0


def _canonicalize_tldr_token(token: str) -> str:
    lowered = str(token or "").lower().strip()
    lowered = re.sub(r"^[^\w']+|[^\w']+$", "", lowered)
    return lowered


def _tldr_context_excerpt(text: str, max_words: int = 40) -> str:
    if not text:
        return ""
    cleaned = str(text)
    cleaned = re.sub(r"[`#>*_]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    return _truncate_text_to_max_words(cleaned, max_words)


def validate_tldr_contract(
    text: str,
    *,
    target_words: int = TLDR_EXACT_WORD_TARGET,
) -> Dict[str, Any]:
    """Validate the exact-word TL;DR contract and return machine-readable reasons."""
    normalized = normalize_tldr_contract_text(text)
    tokens = normalized.split() if normalized else []
    canonical_tokens = [_canonicalize_tldr_token(tok) for tok in tokens]
    canonical_nonempty = [tok for tok in canonical_tokens if tok]
    reasons: List[str] = []

    def _add_reason(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    word_count = len(tokens)
    if not normalized:
        _add_reason("empty")
    if word_count != int(target_words):
        _add_reason(f"word_count_mismatch:{word_count}")
    if normalized and not _TLDR_END_PUNCT_RE.search(normalized):
        _add_reason("missing_terminal_punctuation")

    for prev, curr in zip(canonical_tokens, canonical_tokens[1:]):
        if prev and curr and prev == curr:
            _add_reason("consecutive_duplicate_token")
            break

    repeated_nonstopword = None
    counts: Dict[str, int] = {}
    for tok in canonical_nonempty:
        counts[tok] = counts.get(tok, 0) + 1
        if tok not in _TLDR_REPETITION_STOPWORDS and counts[tok] > 2:
            repeated_nonstopword = tok
            break
    if repeated_nonstopword:
        _add_reason(f"repeated_nonstopword_token:{repeated_nonstopword}")

    if canonical_nonempty and len(set(canonical_nonempty)) < 7:
        _add_reason("low_unique_token_count")

    last_token = next((tok for tok in reversed(canonical_tokens) if tok), "")
    if last_token in _TLDR_DANGLING_ENDINGS:
        _add_reason(f"dangling_ending_token:{last_token}")

    return {
        "normalized": normalized,
        "word_count": word_count,
        "reasons": reasons,
    }


def build_company_research_prompt(
    *,
    company_name: str,
    ticker: str,
    sector: str = "",
    industry: str = "",
    filing_type: str = "",
    filing_date: str = "",
) -> str:
    """Build the standardized Agent-1 company research prompt."""
    sector_line = f" in the {sector}/{industry} sector" if sector or industry else ""
    date_line = (
        f" This brief is filing-date grounded for {filing_date}."
        if filing_date
        else ""
    )
    as_of_block = ""
    if filing_date:
        as_of_block = (
            f"\nTIME-BOUND RULES (STRICT):\n"
            f"- Use only facts/events dated on or before {filing_date}.\n"
            f"- Do NOT reference events after {filing_date}.\n"
            f"- If a source date cannot be verified, omit that fact.\n"
        )
    return (
        f"You are a financial research assistant. Gather filing-date-grounded background knowledge "
        f"about {company_name} ({ticker}){sector_line}.{date_line}\n\n"
        f"Research the following and produce a concise brief (300-400 words max):\n\n"
        f"1. BUSINESS MODEL: What does the company actually do? Main revenue streams, products, services?\n"
        f"2. COMPETITIVE LANDSCAPE: Main competitors? Competitive advantage or moat?\n"
        f"3. PERIOD DEVELOPMENTS: Major news, launches, acquisitions, or strategic shifts relevant to the filing period.\n"
        f"4. MANAGEMENT: CEO/key leadership and notable management commentary or strategic direction around the filing period.\n"
        f"5. INDUSTRY TRENDS: What macro trends affect this company's sector?\n"
        f"6. KEY RISKS: Real company-specific business risks — specific competitors, products, regulations, or customer dynamics.\n\n"
        f"{as_of_block}"
        f"Rules:\n"
        f"- Be factual and concise. No fluff or generic statements.\n"
        f"- Focus on qualitative business understanding, not stock price or market cap.\n"
        f"- Prefer company-specific management statements and strategic context over generic market commentary.\n"
        f"- This brief will NOT be shown to users — it is background context for an "
        f"analyst writing an investment memo from a {filing_type or 'SEC filing'}."
    )


def _model_uses_max_completion_tokens(model_name: Optional[str]) -> bool:
    """GPT-5 family uses `max_completion_tokens` on chat/completions."""
    if not model_name:
        return False
    return str(model_name).strip().lower().startswith("gpt-5")


def _int_env(*names: str, default: int) -> int:
    """Read the first valid integer env var from names."""
    for name in names:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return int(str(raw).strip())
        except ValueError:
            continue
    return int(default)


def _summary_agent1_max_output_tokens() -> int:
    return max(
        100,
        _int_env(
            "SUMMARY_AGENT1_MAX_OUTPUT_TOKENS",
            default=DEFAULT_SUMMARY_AGENT1_MAX_OUTPUT_TOKENS,
        ),
    )


def _summary_research_max_words() -> int:
    return max(
        0,
        _int_env("SUMMARY_RESEARCH_MAX_WORDS", default=DEFAULT_SUMMARY_RESEARCH_MAX_WORDS),
    )


def _truncate_text_to_max_words(text: str, max_words: int) -> str:
    """Deterministically cap free-form text by word count."""
    if not text:
        return ""
    limit = max(0, int(max_words))
    if limit <= 0:
        return ""
    matches = list(re.finditer(r"\S+", text))
    if len(matches) <= limit:
        return text.strip()
    end_idx = matches[limit - 1].end()
    return text[:end_idx].strip()


class OpenAIClient:
    """Client for interacting with OpenAI GPT-5.2.

    Exposes a stable interface for filing summaries, personas, and KPI extraction.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        initial_wait: int = DEFAULT_INITIAL_WAIT,
        max_wait: int = DEFAULT_MAX_WAIT,
    ):
        settings = get_settings()

        self.api_key = settings.openai_api_key

        self.request_timeout = _int_env("OPENAI_REQUEST_TIMEOUT_SECONDS", default=45)
        self.request_timeout = max(8, int(self.request_timeout))
        self.model_name = model_name
        self.persona_model_name = model_name

        default_out = int(os.getenv("OPENAI_DEFAULT_MAX_OUTPUT_TOKENS", "4500"))
        self.base_generation_config: Dict[str, Any] = {
            "max_tokens": default_out,
            "temperature": 0.45,
        }
        self.persona_generation_config: Dict[str, Any] = {
            "max_tokens": max(default_out, 5200),
            "temperature": 0.55,
            "top_p": 0.92,
        }
        # kept for interface compat – not used by OpenAI path
        self.force_http_fallback = True

        self.max_retries = max_retries
        self.initial_wait = initial_wait
        self.max_wait = max_wait

        self.usage_context: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------

    def set_usage_context(self, context: Optional[Dict[str, Any]]) -> None:
        self.usage_context = context or None

    # ------------------------------------------------------------------
    # File upload (OpenAI files API for Assistants / vision)
    # ------------------------------------------------------------------

    def upload_file_bytes(
        self,
        *,
        data: bytes,
        mime_type: str,
        display_name: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Upload bytes to the OpenAI Files API and return a file reference.

        Returns a dict that mimics the old Gemini file object:
        ``{"uri": <file_id>, "mimeType": <mime_type>, "name": <file_id>}``
        so that callers like spotlight_kpi pipelines work unchanged.
        """
        if not self.api_key:
            raise AIAPIError("OpenAI API key not configured", status_code=401)

        timeout = float(timeout_seconds) if timeout_seconds is not None else float(self.request_timeout + 10)
        fname = display_name or f"upload_{int(time.time())}"

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    f"{OPENAI_API_BASE}/files",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files={"file": (fname, data, mime_type)},
                    data={"purpose": "assistants"},
                )

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    retry_seconds = int(retry_after) if retry_after and str(retry_after).isdigit() else None
                    raise AIRateLimitError("OpenAI API rate limit exceeded.", retry_after=retry_seconds)

                if response.status_code >= 400:
                    raise AIAPIError(
                        f"OpenAI Files API error: {response.status_code}",
                        status_code=response.status_code,
                        response_body=(response.text or "")[:2000],
                    )

                payload = response.json()
        except httpx.TimeoutException as exc:
            raise AITimeoutError(f"OpenAI Files API request timed out after {timeout}s") from exc

        file_id = payload.get("id", "")
        return {"uri": file_id, "mimeType": mime_type, "name": file_id}

    # ------------------------------------------------------------------
    # Core generation helpers
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        prompt: str,
        *,
        system_message: Optional[str] = None,
        image_data: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Build an OpenAI chat messages array from a prompt string."""
        messages: List[Dict[str, Any]] = []
        if system_message:
            messages.append({"role": "system", "content": system_message})

        if image_data:
            # Multi-modal: text + images
            content_parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
            for img in image_data:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": img["url"], "detail": img.get("detail", "auto")},
                })
            messages.append({"role": "user", "content": content_parts})
        else:
            messages.append({"role": "user", "content": prompt})

        return messages

    def _resolve_gen_config(
        self,
        use_persona_model: bool = False,
        generation_config_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Merge base/persona config with caller overrides."""
        config = dict(self.persona_generation_config if use_persona_model else self.base_generation_config)

        if isinstance(generation_config_override, dict) and generation_config_override:
            # Map Gemini-style keys to OpenAI equivalents for backward compat
            mapping = {
                "maxOutputTokens": "max_tokens",
                "max_output_tokens": "max_tokens",
                "maxCompletionTokens": "max_completion_tokens",
                "topP": "top_p",
                "responseMimeType": None,  # not applicable
                "responseSchema": None,
                "thinkingConfig": None,
            }
            for k, v in generation_config_override.items():
                if v is None:
                    continue
                mapped = mapping.get(k, k)
                if mapped is not None:
                    config[mapped] = v

        return {k: v for k, v in config.items() if v is not None}

    def _build_responses_metadata(
        self,
        *,
        usage_context: Optional[Dict[str, Any]],
        stage_name: Optional[str] = None,
        stage_override: Optional[str] = None,
        pipeline_mode: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        """Build a compact metadata payload for OpenAI Responses logs."""
        merged: Dict[str, Any] = {}
        if isinstance(self.usage_context, dict):
            merged.update(self.usage_context)
        if isinstance(usage_context, dict):
            merged.update(usage_context)

        stage = (stage_override or "").strip()
        if not stage:
            normalized_stage = (stage_name or "").strip().lower()
            if "research" in normalized_stage:
                stage = "agent_1_research"
            elif normalized_stage:
                stage = "agent_2_summary"
        if stage:
            merged["agent_stage"] = stage

        if pipeline_mode:
            merged["pipeline_mode"] = str(pipeline_mode).strip()

        keep_keys = (
            "request_id",
            "request_type",
            "user_id",
            "filing_id",
            "company_id",
            "model",
            "pipeline_mode",
            "agent_stage",
            "call_type",
        )
        metadata: Dict[str, str] = {}
        for key in keep_keys:
            value = merged.get(key)
            if value is None:
                continue
            try:
                text = str(value).strip()
            except Exception:
                continue
            if not text:
                continue
            metadata[key] = text[:500]

        return metadata or None

    def _call_openai_chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        gen_config: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[float] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Low-level OpenAI chat completions call."""
        if not self.api_key:
            raise AIAPIError("OpenAI API key not configured", status_code=401)

        selected_model = model or self.model_name
        payload: Dict[str, Any] = {
            "model": selected_model,
            "messages": messages,
        }
        if gen_config:
            for k in ("temperature", "top_p"):
                if k in gen_config:
                    payload[k] = gen_config[k]
            if "max_completion_tokens" in gen_config:
                payload["max_completion_tokens"] = gen_config["max_completion_tokens"]
            elif "max_tokens" in gen_config:
                token_key = (
                    "max_completion_tokens"
                    if _model_uses_max_completion_tokens(selected_model)
                    else "max_tokens"
                )
                payload[token_key] = gen_config["max_tokens"]
        if tools:
            payload["tools"] = tools

        # Support response_format for JSON mode
        if gen_config and "response_format" in gen_config:
            payload["response_format"] = gen_config["response_format"]

        timeout = float(timeout_seconds) if timeout_seconds is not None else float(self.request_timeout + 5)

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    f"{OPENAI_API_BASE}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    retry_seconds = int(retry_after) if retry_after and str(retry_after).isdigit() else None
                    raise AIRateLimitError("OpenAI API rate limit exceeded.", retry_after=retry_seconds)

                if response.status_code >= 400:
                    raise AIAPIError(
                        f"OpenAI API error: {response.status_code}",
                        status_code=response.status_code,
                        response_body=(response.text or "")[:2000],
                    )

                return response.json()

        except httpx.TimeoutException as exc:
            raise AITimeoutError(f"OpenAI API request timed out after {timeout}s") from exc
        except (AIRateLimitError, AIAPIError, AITimeoutError):
            raise
        except httpx.HTTPStatusError as exc:
            raise AIAPIError(
                f"HTTP error: {exc.response.status_code}",
                status_code=exc.response.status_code,
                response_body=str(exc),
            ) from exc
        except Exception as exc:
            raise AIClientError(f"Unexpected OpenAI error: {exc}") from exc

    def _call_openai_responses(
        self,
        input_text: str,
        *,
        model: Optional[str] = None,
        gen_config: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[float] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Low-level OpenAI Responses API call."""
        if not self.api_key:
            raise AIAPIError("OpenAI API key not configured", status_code=401)

        selected_model = model or self.model_name
        payload: Dict[str, Any] = {
            "model": selected_model,
            "input": input_text,
        }
        if gen_config:
            for k in ("temperature", "top_p"):
                if k in gen_config:
                    payload[k] = gen_config[k]
            if "max_completion_tokens" in gen_config:
                payload["max_output_tokens"] = gen_config["max_completion_tokens"]
            elif "max_tokens" in gen_config:
                payload["max_output_tokens"] = gen_config["max_tokens"]
        if tools:
            payload["tools"] = tools
        if metadata:
            payload["metadata"] = metadata

        timeout = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(self.request_timeout + 5)
        )

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    f"{OPENAI_API_BASE}/responses",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    retry_seconds = (
                        int(retry_after)
                        if retry_after and str(retry_after).isdigit()
                        else None
                    )
                    raise AIRateLimitError(
                        "OpenAI API rate limit exceeded.", retry_after=retry_seconds
                    )

                if response.status_code >= 400:
                    raise AIAPIError(
                        f"OpenAI Responses API error: {response.status_code}",
                        status_code=response.status_code,
                        response_body=(response.text or "")[:2000],
                    )

                return response.json()
        except httpx.TimeoutException as exc:
            raise AITimeoutError(
                f"OpenAI Responses API request timed out after {timeout}s"
            ) from exc
        except (AIRateLimitError, AIAPIError, AITimeoutError):
            raise
        except Exception as exc:
            raise AIClientError(f"Unexpected OpenAI error: {exc}") from exc

    def _extract_text_from_response(self, data: Dict[str, Any]) -> str:
        """Extract the assistant message text from an OpenAI chat response."""
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return (message.get("content") or "").strip()

    def _extract_usage(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract usage metadata from an OpenAI response."""
        usage = data.get("usage")
        if not usage or not isinstance(usage, dict):
            return None
        return {
            "prompt_token_count": usage.get("prompt_tokens"),
            "candidates_token_count": usage.get("completion_tokens"),
            "total_token_count": usage.get("total_tokens"),
        }

    def _extract_text_from_responses(self, data: Dict[str, Any]) -> str:
        text = str(data.get("output_text") or "").strip()
        if text:
            return text

        output_items = data.get("output") or []
        for item in output_items:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") in {"output_text", "text"}:
                    text = str(content.get("text") or "").strip()
                    if text:
                        return text
        return ""

    def _extract_usage_from_responses(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        usage = data.get("usage")
        if not usage or not isinstance(usage, dict):
            return None
        return {
            "prompt_token_count": usage.get("input_tokens"),
            "candidates_token_count": usage.get("output_tokens"),
            "total_token_count": usage.get("total_tokens"),
        }

    # ------------------------------------------------------------------
    # Retry-wrapped generation
    # ------------------------------------------------------------------

    def _generate_with_retry(
        self,
        prompt: str,
        *,
        use_persona_model: bool = False,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        stage_name: str = "Generating",
        usage_context: Optional[Dict[str, Any]] = None,
        generation_config_override: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[float] = None,
        system_message: Optional[str] = None,
        image_data: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Generate text with automatic retry on transient errors."""
        if progress_callback:
            progress_callback(5, stage_name)

        gen_config = self._resolve_gen_config(use_persona_model, generation_config_override)
        api_mode = str(gen_config.pop("api_mode", "chat")).strip().lower()
        use_responses_api = bool(gen_config.pop("use_responses_api", False)) or api_mode == "responses"
        stage_override = str(gen_config.pop("agent_stage", "") or "").strip() or None
        pipeline_mode = str(gen_config.pop("pipeline_mode", "") or "").strip() or None
        metadata = self._build_responses_metadata(
            usage_context=usage_context,
            stage_name=stage_name,
            stage_override=stage_override,
            pipeline_mode=pipeline_mode,
        )
        messages = self._build_messages(
            prompt, system_message=system_message, image_data=image_data
        )

        model = self.persona_model_name if use_persona_model else self.model_name

        last_exc: Optional[Exception] = None
        for attempt in range(max(1, self.max_retries)):
            try:
                if use_responses_api and not image_data:
                    response_prompt = prompt
                    if system_message:
                        response_prompt = f"{system_message}\n\n{prompt}"
                    data = self._call_openai_responses(
                        response_prompt,
                        model=model,
                        gen_config=gen_config,
                        timeout_seconds=timeout_seconds,
                        tools=tools,
                        metadata=metadata,
                    )
                    text = self._extract_text_from_responses(data)
                    usage_meta = self._extract_usage_from_responses(data)
                else:
                    data = self._call_openai_chat(
                        messages,
                        model=model,
                        gen_config=gen_config,
                        timeout_seconds=timeout_seconds,
                        tools=tools,
                    )
                    text = self._extract_text_from_response(data)
                    usage_meta = self._extract_usage(data)

                record_ai_usage(
                    prompt=prompt,
                    response_text=text,
                    usage_metadata=usage_meta,
                    model=model,
                    usage_context=usage_context or self.usage_context,
                )

                if progress_callback:
                    progress_callback(100, stage_name)

                return text

            except AIRateLimitError as exc:
                last_exc = exc
                wait = exc.retry_after or min(self.max_wait, self.initial_wait * (2 ** attempt))
                logger.warning("Rate limited (attempt %d/%d), waiting %ds", attempt + 1, self.max_retries, wait)
                time.sleep(wait)

            except AITimeoutError as exc:
                last_exc = exc
                if attempt + 1 < self.max_retries:
                    wait = min(self.max_wait, self.initial_wait * (2 ** attempt))
                    logger.warning("Timeout (attempt %d/%d), waiting %ds", attempt + 1, self.max_retries, wait)
                    time.sleep(wait)

            except AIAPIError:
                raise  # Non-transient errors bubble up immediately

        if last_exc is not None:
            raise last_exc
        raise AIClientError("Generation failed after retries")

    # ------------------------------------------------------------------
    # Public generation interface (matches former GeminiClient)
    # ------------------------------------------------------------------

    def stream_generate_content(
        self,
        prompt: str,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        stage_name: str = "Generating",
        expected_tokens: int = 4000,
        use_persona_model: bool = False,
        usage_context: Optional[Dict[str, Any]] = None,
        generation_config_override: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[float] = None,
        retry: bool = True,
    ) -> str:
        """Generate content (returns full text string).

        Name kept for interface compatibility; uses standard completions internally.
        """
        max_retries_saved = self.max_retries
        if not retry:
            self.max_retries = 1
        try:
            return self._generate_with_retry(
                prompt,
                use_persona_model=use_persona_model,
                progress_callback=progress_callback,
                stage_name=stage_name,
                usage_context=usage_context,
                generation_config_override=generation_config_override,
                timeout_seconds=timeout_seconds,
            )
        finally:
            self.max_retries = max_retries_saved

    def stream_generate_content_with_file_uri(
        self,
        *,
        file_uri: str,
        file_mime_type: str,
        prompt: str,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        stage_name: str = "Generating",
        expected_tokens: int = 4000,
        use_persona_model: bool = False,
        usage_context: Optional[Dict[str, Any]] = None,
        generation_config_override: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[float] = None,
        retry: bool = True,
    ) -> str:
        """Generate content using an uploaded file reference.

        For OpenAI, we download the file content from the Files API and
        include it as a vision image (for PDFs) or as text context.
        """
        # If the file_uri is an OpenAI file ID, we reference it in the prompt.
        # For PDFs/images, use the vision pathway.
        image_data: Optional[List[Dict[str, Any]]] = None

        if file_mime_type and file_mime_type.startswith("image/"):
            # For images, we can use the vision API directly via data URL
            # But since we only have a file_id, include a note in prompt
            enhanced_prompt = (
                f"[Attached file: {file_uri} ({file_mime_type})]\n\n{prompt}"
            )
        elif file_mime_type and file_mime_type == "application/pdf":
            # For PDFs with OpenAI, include file reference in the prompt context
            enhanced_prompt = (
                f"[Attached PDF document (file ID: {file_uri})]\n\n{prompt}"
            )
        else:
            enhanced_prompt = f"[Attached file: {file_uri} ({file_mime_type})]\n\n{prompt}"

        max_retries_saved = self.max_retries
        if not retry:
            self.max_retries = 1
        try:
            return self._generate_with_retry(
                enhanced_prompt,
                use_persona_model=use_persona_model,
                progress_callback=progress_callback,
                stage_name=stage_name,
                usage_context=usage_context,
                generation_config_override=generation_config_override,
                timeout_seconds=timeout_seconds,
                image_data=image_data,
            )
        finally:
            self.max_retries = max_retries_saved

    def generate_content(
        self,
        prompt: str,
        use_persona_model: bool = False,
        timeout: Optional[int] = None,
        usage_context: Optional[Dict[str, Any]] = None,
        generation_config_override: Optional[Dict[str, Any]] = None,
    ):
        """Generate content and return SimpleNamespace(text=...) for compat."""
        text = self._generate_with_retry(
            prompt,
            use_persona_model=use_persona_model,
            usage_context=usage_context,
            generation_config_override=generation_config_override,
            timeout_seconds=float(timeout) if timeout else None,
        )
        return SimpleNamespace(text=text)

    # ------------------------------------------------------------------
    # Company web research (uses GPT-5.2 with web search tool)
    # ------------------------------------------------------------------

    def research_company_background(
        self,
        company_name: str,
        ticker: str,
        sector: str = "",
        industry: str = "",
        filing_type: str = "",
        filing_date: str = "",
        timeout_seconds: float = 20.0,
    ) -> str:
        """Use GPT-5.2 with web search to gather background about a company.

        Returns a concise research brief for injection into the summary prompt.
        Falls back to empty string on any error.
        """
        research_prompt = build_company_research_prompt(
            company_name=company_name,
            ticker=ticker,
            sector=sector,
            industry=industry,
            filing_type=filing_type,
            filing_date=filing_date,
        )

        try:
            metadata = self._build_responses_metadata(
                usage_context=self.usage_context,
                stage_name="Researching Company Background",
                stage_override="agent_1_research",
                pipeline_mode="two_agent",
            )
            response_payload = self._call_openai_responses(
                research_prompt,
                model=self.model_name,
                gen_config={
                    "temperature": 0.2,
                    "max_tokens": _summary_agent1_max_output_tokens(),
                },
                timeout_seconds=timeout_seconds,
                tools=[{"type": "web_search_preview"}],
                metadata=metadata,
            )

            text = self._extract_text_from_responses(response_payload)

            sources: List[str] = []
            output_items = response_payload.get("output") or []
            for item in output_items:
                if not isinstance(item, dict):
                    continue
                for content in item.get("content") or []:
                    if not isinstance(content, dict):
                        continue
                    for annotation in content.get("annotations") or []:
                        if not isinstance(annotation, dict):
                            continue
                        url = str(annotation.get("url") or "").strip()
                        if url and url not in sources:
                            sources.append(url)

            if sources:
                source_block = "\n".join(f"- {url}" for url in sources[:8])
                text = f"{text}\n\nSources:\n{source_block}"

            # Keep Agent-1 research payload bounded for downstream prompt injection.
            text = _truncate_text_to_max_words(text, _summary_research_max_words())

            record_ai_usage(
                prompt=research_prompt,
                response_text=text,
                usage_metadata=self._extract_usage_from_responses(response_payload),
                model=self.model_name,
                usage_context={
                    **(self.usage_context or {}),
                    "call_type": "company_research",
                },
            )

            return text.strip()

        except httpx.TimeoutException as exc:
            logger.warning("Company research call timed out for %s (%s): %s", company_name, ticker, exc)
            return ""
        except Exception as exc:
            logger.warning("Company research call failed for %s (%s): %s", company_name, ticker, exc)
            return ""

    # ------------------------------------------------------------------
    # Agent pipeline methods (used by summary_agents.py)
    # ------------------------------------------------------------------

    def research_company_intelligence(
        self,
        prompt: str,
        system_message: str = "",
        timeout_seconds: float = 30.0,
    ) -> Optional[Dict[str, Any]]:
        """Use GPT-5.2 via Chat Completions to generate a structured company
        intelligence profile.  Returns parsed JSON dict, or None on failure.

        Used by Agent 1 in the 3-agent summary pipeline.
        """
        messages = self._build_messages(prompt, system_message=system_message)

        gen_config: Dict[str, Any] = {
            "max_tokens": 2500,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }

        try:
            data = self._call_openai_chat(
                messages,
                model=self.model_name,
                gen_config=gen_config,
                timeout_seconds=timeout_seconds,
            )
            text = self._extract_text_from_response(data)
            usage_meta = self._extract_usage(data)

            if not text:
                return None

            record_ai_usage(
                prompt=prompt,
                response_text=text,
                usage_metadata=usage_meta,
                model=self.model_name,
                usage_context={
                    **(self.usage_context or {}),
                    "call_type": "company_intelligence",
                },
            )

            # Parse JSON from response
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # Try to extract JSON from markdown code blocks
                json_match = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group(1).strip())
                logger.warning("Agent 1: Could not parse JSON from response")
                return None

        except (AIRateLimitError, AIAPIError, AITimeoutError):
            raise
        except Exception as exc:
            logger.warning("Company intelligence call failed: %s", exc)
            return None

    def research_company_intelligence_with_web(
        self,
        prompt: str,
        system_message: str = "",
        filing_date: str = "",
        timeout_seconds: float = 30.0,
    ) -> Optional[Dict[str, Any]]:
        """Use GPT-5.2 via Responses API with web search to generate a structured
        company intelligence profile.  Time-aware: focuses research around the
        filing date.

        Returns parsed JSON dict, or None on failure.
        Used by Agent 1 in the 3-agent summary pipeline (primary path).
        """
        # Build time-aware preamble
        time_context = ""
        if filing_date:
            time_context = (
                f"\n\nIMPORTANT TIME CONTEXT: This analysis is for a filing dated "
                f"{filing_date}. Focus your research on the company's situation "
                f"around that time period. Use only facts dated on or before "
                f"{filing_date}; do NOT reference events after {filing_date}. "
                f"If a source date is unclear, omit that fact.\n"
            )

        # Combine system + user prompt for Responses API format
        combined_input = ""
        if system_message:
            combined_input = f"{system_message}{time_context}\n\n{prompt}"
        else:
            combined_input = f"{time_context}\n\n{prompt}" if time_context else prompt

        gen_config: Dict[str, Any] = {
            "temperature": 0.2,
            "max_tokens": 2500,
        }

        try:
            metadata = self._build_responses_metadata(
                usage_context=self.usage_context,
                stage_name="Company Intelligence (Web)",
                stage_override="agent_1_research_web",
                pipeline_mode="three_agent",
            )
            data = self._call_openai_responses(
                combined_input,
                model=self.model_name,
                gen_config=gen_config,
                timeout_seconds=timeout_seconds,
                tools=[{"type": "web_search_preview"}],
                metadata=metadata,
            )
            text = self._extract_text_from_responses(data)
            usage_meta = self._extract_usage_from_responses(data)

            if not text:
                return None

            record_ai_usage(
                prompt=prompt,
                response_text=text,
                usage_metadata=usage_meta,
                model=self.model_name,
                usage_context={
                    **(self.usage_context or {}),
                    "call_type": "company_intelligence_web",
                },
            )

            # Parse JSON — web search responses sometimes have annotations mixed in
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # Try extracting JSON from markdown code blocks
                json_match = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
                if json_match:
                    try:
                        return json.loads(json_match.group(1).strip())
                    except json.JSONDecodeError:
                        pass
                # Try extracting the first JSON object from the text
                brace_match = re.search(r'\{[\s\S]*\}', text)
                if brace_match:
                    try:
                        return json.loads(brace_match.group())
                    except json.JSONDecodeError:
                        pass
                logger.warning("Agent 1 (web): Could not parse JSON from response")
                return None

        except Exception as exc:
            logger.warning("Company intelligence (web) call failed: %s", exc)
            return None

    def research_company_current_context(
        self,
        company_name: str,
        ticker: str,
        filing_date: str,
        filing_type: str = "",
        timeout_seconds: float = 20.0,
    ) -> str:
        """Use GPT-5.2 via Responses API with web search to gather time-aware
        context about what was happening with a company around a filing date.

        Returns plain text string, empty on failure (non-blocking).
        Used by Agent 2 in the 3-agent summary pipeline.
        """
        prompt = (
            f"Research what was happening with {company_name} ({ticker}) around "
            f"{filing_date}. This is for analyzing a {filing_type or 'SEC filing'} "
            f"from that date.\n\n"
            f"Focus on:\n"
            f"1. Major news or events within 3 months of {filing_date}\n"
            f"2. Analyst sentiment and consensus around that time\n"
            f"3. Industry developments affecting the company\n"
            f"4. Management changes or strategic announcements\n"
            f"5. Competitive dynamics at that time\n\n"
            f"CRITICAL: Do NOT reference events that occurred after {filing_date}. "
            f"Keep the context grounded in the time period of the filing.\n\n"
            f"Provide a concise 3-5 paragraph summary of the company's situation."
        )

        gen_config: Dict[str, Any] = {
            "temperature": 0.2,
            "max_tokens": 800,
        }

        try:
            metadata = self._build_responses_metadata(
                usage_context=self.usage_context,
                stage_name="Company Current Context",
                stage_override="agent_2_context_research",
                pipeline_mode="three_agent",
            )
            data = self._call_openai_responses(
                prompt,
                model=self.model_name,
                gen_config=gen_config,
                timeout_seconds=timeout_seconds,
                tools=[{"type": "web_search_preview"}],
                metadata=metadata,
            )
            text = self._extract_text_from_responses(data)
            usage_meta = self._extract_usage_from_responses(data)

            if text:
                record_ai_usage(
                    prompt=prompt,
                    response_text=text,
                    usage_metadata=usage_meta,
                    model=self.model_name,
                    usage_context={
                        **(self.usage_context or {}),
                        "call_type": "company_current_context",
                    },
                )

            return (text or "").strip()

        except Exception as exc:
            logger.warning(
                "Company current context call failed for %s (%s): %s",
                company_name, ticker, exc,
            )
            return ""

    def analyze_filing_with_context(
        self,
        prompt: str,
        system_message: str = "",
        timeout_seconds: float = 45.0,
    ) -> Optional[Dict[str, Any]]:
        """Use GPT-5.2 via Chat Completions to analyze a filing with company
        context.  Returns parsed JSON dict, or None on failure.

        Used by Agent 2 in the 3-agent summary pipeline.
        """
        messages = self._build_messages(prompt, system_message=system_message)

        gen_config: Dict[str, Any] = {
            "max_tokens": 4000,
            "temperature": 0.15,
            "response_format": {"type": "json_object"},
        }

        try:
            data = self._call_openai_chat(
                messages,
                model=self.model_name,
                gen_config=gen_config,
                timeout_seconds=timeout_seconds,
            )
            text = self._extract_text_from_response(data)
            usage_meta = self._extract_usage(data)

            if not text:
                return None

            record_ai_usage(
                prompt=prompt,
                response_text=text,
                usage_metadata=usage_meta,
                model=self.model_name,
                usage_context={
                    **(self.usage_context or {}),
                    "call_type": "filing_analysis",
                },
            )

            try:
                return json.loads(text)
            except json.JSONDecodeError:
                json_match = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group(1).strip())
                logger.warning("Agent 2: Could not parse JSON from response")
                return None

        except (AIRateLimitError, AIAPIError, AITimeoutError):
            raise
        except Exception as exc:
            logger.warning("Filing analysis call failed: %s", exc)
            return None

    def compose_summary(
        self,
        prompt: str,
        system_message: str = "",
        max_output_tokens: int = 5000,
        temperature: float = 0.4,
        timeout_seconds: float = 60.0,
    ) -> str:
        """Generate the final summary text using Chat Completions API.

        Used by Agent 3 in the 3-agent summary pipeline.  Returns raw text.
        """
        messages = self._build_messages(prompt, system_message=system_message)

        gen_config: Dict[str, Any] = {
            "max_tokens": max_output_tokens,
            "temperature": temperature,
        }

        try:
            data = self._call_openai_chat(
                messages,
                model=self.model_name,
                gen_config=gen_config,
                timeout_seconds=timeout_seconds,
            )
            text = self._extract_text_from_response(data)
            usage_meta = self._extract_usage(data)

            record_ai_usage(
                prompt=prompt,
                response_text=text,
                usage_metadata=usage_meta,
                model=self.model_name,
                usage_context={
                    **(self.usage_context or {}),
                    "call_type": "summary_composition",
                },
            )

            return text
        except Exception:
            raise

    # ------------------------------------------------------------------
    # High-level summary generation (matches GeminiClient interface)
    # ------------------------------------------------------------------

    def generate_company_summary(
        self,
        company_name: str,
        financial_data: Dict[str, Any],
        ratios: Dict[str, float],
        health_score: float,
        mda_text: Optional[str] = None,
        risk_factors_text: Optional[str] = None,
        target_length: Optional[int] = None,
        complexity: str = "intermediate",
    ) -> Dict[str, str]:
        """Generate comprehensive company analysis summary."""
        variation_token = uuid4().hex[:8].upper()

        prompt = self._build_summary_prompt(
            company_name, financial_data, ratios, health_score,
            mda_text, risk_factors_text, target_length, complexity, variation_token,
        )

        max_retries = 5 if target_length else 3
        current_try = 0

        while current_try < max_retries:
            try:
                response = self.generate_content(prompt)
                summary_text = response.text
                word_count = len(summary_text.split())

                if target_length:
                    target_words = max(1, int(target_length))
                    tolerance = SUMMARY_TARGET_BAND_TOLERANCE
                    lower_acceptable = max(1, target_words - tolerance)
                    upper_acceptable = target_words + tolerance
                    if not (lower_acceptable <= word_count <= upper_acceptable):
                        if word_count > upper_acceptable:
                            excess = word_count - upper_acceptable
                            prompt += (
                                f"\n\nSYSTEM FEEDBACK: Full-output word count {word_count} is ABOVE "
                                f"the required band ({lower_acceptable}-{upper_acceptable}) by {excess} words. "
                                f"CUT at least {excess} words by removing redundancy and generic filler while preserving substance. "
                                f"Use a code-style word-count check on the FULL output (normalized whitespace split), "
                                f"adjust and recount until the FULL output is within ±{tolerance} words of {target_words}. "
                                "Once done, return the full structured output only."
                            )
                        else:
                            deficit_to_band = lower_acceptable - word_count
                            deficit_to_target = target_words - word_count
                            prompt += (
                                f"\n\nSYSTEM FEEDBACK: Full-output word count {word_count} is BELOW "
                                f"the required band ({lower_acceptable}-{upper_acceptable}) by {deficit_to_band} words "
                                f"(and {deficit_to_target} words below the exact target). "
                                "ADD substantive, filing-grounded analysis depth (causal reasoning, scenario implications, and decision-relevant interpretation) "
                                "without filler or repetition. "
                                f"Use a code-style word-count check on the FULL output (normalized whitespace split), "
                                f"adjust and recount until the FULL output is within ±{tolerance} words of {target_words}. "
                                "Once done, return the full structured output only."
                            )
                        current_try += 1
                        continue

                summary_text = self._post_process_summary(
                    summary_text, company_name=company_name, ratios=ratios,
                )
                if target_length:
                    post_wc = len(summary_text.split())
                    target_words = max(1, int(target_length))
                    tolerance = SUMMARY_TARGET_BAND_TOLERANCE
                    lower_acceptable = max(1, target_words - tolerance)
                    upper_acceptable = target_words + tolerance
                    if not (lower_acceptable <= post_wc <= upper_acceptable):
                        if post_wc > upper_acceptable:
                            excess = post_wc - upper_acceptable
                            prompt += (
                                f"\n\nSYSTEM FEEDBACK: After post-processing, full-output word count {post_wc} is ABOVE "
                                f"the required band ({lower_acceptable}-{upper_acceptable}) by {excess} words. "
                                f"CUT at least {excess} words while preserving substance and structure. "
                                f"Use a code-style word-count check on the FULL output (normalized whitespace split), "
                                f"adjust and recount until the FULL output is within ±{tolerance} words of {target_words}. "
                                "Once done, return the full structured output only."
                            )
                        else:
                            deficit_to_band = lower_acceptable - post_wc
                            deficit_to_target = target_words - post_wc
                            prompt += (
                                f"\n\nSYSTEM FEEDBACK: After post-processing, full-output word count {post_wc} is BELOW "
                                f"the required band ({lower_acceptable}-{upper_acceptable}) by {deficit_to_band} words "
                                f"(and {deficit_to_target} words below the exact target). "
                                "ADD substantive, filing-grounded analysis depth without filler, boilerplate, or repeated framework sentences. "
                                f"Use a code-style word-count check on the FULL output (normalized whitespace split), "
                                f"adjust and recount until the FULL output is within ±{tolerance} words of {target_words}. "
                                "Once done, return the full structured output only."
                            )
                        current_try += 1
                        continue

                sections = self._parse_summary_response(summary_text)
                sections = self.normalize_summary_sections(sections)
                sections["tldr"] = self._enforce_exact_tldr_contract(
                    tldr=sections.get("tldr", ""),
                    company_name=company_name,
                    thesis=sections.get("thesis", ""),
                    risks=sections.get("risks", ""),
                    catalysts=sections.get("catalysts", ""),
                    target_words=TLDR_EXACT_WORD_TARGET,
                )
                return sections

            except TLDRContractError:
                raise
            except Exception as e:
                logger.error("Error generating summary (attempt %d): %s", current_try, e)
                current_try += 1

        return {
            "tldr": "Error generating summary after retries",
            "thesis": "",
            "risks": "",
            "catalysts": "",
            "kpis": "",
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
        section_budgets: Optional[Dict[str, int]] = None,
        company_research_brief: Optional[str] = None,
    ) -> str:
        """Build the prompt for company summary generation."""
        ratios_str = "\n".join(
            f"- {key}: {value:.2%}" if isinstance(value, float) and abs(value) < 10
            else f"- {key}: {value:.2f}"
            for key, value in ratios.items() if value is not None
        )

        complexity_instruction = {
            "simple": "Use plain English and avoid jargon. Explain financial concepts simply.",
            "expert": "Use sophisticated financial terminology. Assume the reader is an expert investor.",
        }.get(complexity, "Use standard financial analysis language.")

        length_instruction = ""
        if target_length:
            if int(target_length) >= 1000:
                length_instruction = f"""
CRITICAL LENGTH CONSTRAINT (HARD TARGET):
Target length: {target_length} words (final output should land within ±{SUMMARY_TARGET_BAND_TOLERANCE} words).
- Long-form output must add substantive analysis depth, not filler.
- Expand by adding mechanisms, scenarios, and filing-grounded implications.
- Do NOT pad with repeated framework sentences or checklist items.
- NEVER cut off mid-sentence to manage length.
- Before finalizing, use a code-style word-count check on the FULL structured output (normalized whitespace split).
- Adjust and recount the FULL output until it lands within ±{SUMMARY_TARGET_BAND_TOLERANCE} words of {target_length}.
- Once the FULL output word-count band is satisfied, produce the full structured output.
"""
            else:
                length_instruction = f"""
CRITICAL LENGTH CONSTRAINT (HARD TARGET):
Target length: {target_length} words (final output should land within ±{SUMMARY_TARGET_BAND_TOLERANCE} words).
- Keep only the most decision-relevant analysis while staying complete.
- Do NOT pad with generic filler or repeated framework sentences.
- NEVER cut off mid-sentence to manage length.
- Before finalizing, use a code-style word-count check on the FULL structured output (normalized whitespace split).
- Adjust and recount the FULL output until it lands within ±{SUMMARY_TARGET_BAND_TOLERANCE} words of {target_length}.
- Once the FULL output word-count band is satisfied, produce the full structured output.
"""

        variation_clause = ""
        if variation_token:
            variation_clause = f"\nSTYLE VARIATION TOKEN: {variation_token}\n- Vary sentence openings and word choice from prior runs.\n- Avoid reusing identical phrasing in the Closing Takeaway.\n"

        research_clause = ""
        if company_research_brief:
            research_clause = f"""
COMPANY BACKGROUND (from web research — use as context, do NOT quote directly):
{company_research_brief}
"""

        prompt = f"""You are a senior equity research analyst writing an investment analysis for {company_name}.

{complexity_instruction}
{length_instruction}
{variation_clause}
{research_clause}

FINANCIAL DATA:
{json.dumps(financial_data, indent=2, default=str)[:5000]}

KEY RATIOS:
{ratios_str}

HEALTH SCORE: {health_score:.1f}/100

{"MD&A TEXT:" + chr(10) + mda_text[:3000] if mda_text else ""}
{"RISK FACTORS:" + chr(10) + risk_factors_text[:3000] if risk_factors_text else ""}

Produce a structured analysis with these sections:
## TL;DR
[One sentence. Decision-oriented verdict plus the single biggest driver.
HARD REQUIREMENT: EXACTLY 10 words. No more, no less.
Use code to calculate the word count and adjust until you get it right.
No repeated words. Must end with period, exclamation, or question mark.
Example calibration — these are each exactly 10 words:
- "Strong margins and cash conversion support sustained upside through execution."
- "Cautious view: leverage and weaker cash conversion elevate downside risk."
Once the TL;DR is exactly 10 words, produce the full output.]

## Investment Thesis
[Core argument for/against investment]

## Top 5 Risks
[Five specific, company-specific risks]

## Catalysts
[Near-term positive catalysts]

## Key KPIs
[5 key metrics to monitor]

GLOBAL WRITING RULES:
- Never repeat words to satisfy length requirements.
- Before finalizing, verify the TL;DR is EXACTLY 10 words and rewrite it until exact.
- Use a code-style word-count check for the TL;DR (normalized whitespace split after removing any 'TL;DR:' prefix).
- Once the TL;DR count is exact, produce the full structured output.
"""
        return prompt

    # ------------------------------------------------------------------
    # Post-processing (ported from GeminiClient)
    # ------------------------------------------------------------------

    def _post_process_summary(
        self,
        response_text: str,
        *,
        company_name: str = "the company",
        ratios: Optional[Dict[str, float]] = None,
    ) -> str:
        """Post-process summary: reorder sections, remove banned phrases."""
        banned_patterns = [
            r"Additionally,?\s*monitor[^.]*\.",
            r"Additionally,?\s*track[^.]*\.",
            r"Additionally,?\s*watch[^.]*\.",
            r"Additionally,?\s*assess[^.]*\.",
            r"Additionally,?\s*review[^.]*\.",
            r"Additionally,?\s*compare[^.]*\.",
            r"Additionally,?\s*consider[^.]*\.",
            r"Additionally,?\s*evaluate[^.]*\.",
            r"Monitor revenue trajectory[^.]*\.",
            r"Track operating margin[^.]*\.",
            r"Watch free cash flow[^.]*\.",
            r"second-level thinking",
            r"pendulum",
            r"where are we in the cycle",
        ]

        cleaned_text = response_text
        for pattern in banned_patterns:
            cleaned_text = re.sub(pattern, "", cleaned_text, flags=re.IGNORECASE)

        monitoring_patterns = [
            r"\n\s*Monitor [^\n]*$",
            r"\n\s*Track [^\n]*$",
            r"\n\s*Watch [^\n]*$",
        ]
        for pattern in monitoring_patterns:
            cleaned_text = re.sub(pattern, "", cleaned_text, flags=re.IGNORECASE)

        return cleaned_text.strip()

    def _parse_summary_response(self, response_text: str) -> Dict[str, str]:
        """Parse markdown sections into a dict."""
        sections: Dict[str, str] = {}
        current_key: Optional[str] = None
        current_lines: List[str] = []

        key_map = {
            "tl;dr": "tldr",
            "tldr": "tldr",
            "investment thesis": "thesis",
            "top 5 risks": "risks",
            "risk factors": "risks",
            "catalysts": "catalysts",
            "key kpis": "kpis",
            "key metrics": "kpis",
            "executive summary": "thesis",
            "financial performance": "performance",
            "financial health": "health",
            "management discussion": "mda",
            "closing takeaway": "closing",
            "strategic initiatives": "strategic_initiatives",
            "overall takeaway": "closing",
        }

        for line in response_text.split("\n"):
            stripped = line.strip()
            header_match = re.match(r"^##?\s*\d*\.?\s*(.*)", stripped)
            if header_match:
                if current_key is not None:
                    sections[current_key] = "\n".join(current_lines).strip()
                header_text = header_match.group(1).strip().lower()
                header_text = re.sub(r"[*#]+", "", header_text).strip()
                current_key = key_map.get(header_text, header_text.replace(" ", "_"))
                current_lines = []
            elif current_key is not None:
                current_lines.append(line)

        if current_key is not None:
            sections[current_key] = "\n".join(current_lines).strip()

        return sections

    def normalize_summary_sections(self, sections: Dict[str, str]) -> Dict[str, str]:
        """Normalize section keys and clean up content."""
        normalized: Dict[str, str] = {}
        key_map = {
            "tl_dr": "tldr",
            "tl;dr": "tldr",
            "investment_thesis": "thesis",
            "top_5_risks": "risks",
            "risk_factors": "risks",
            "key_kpis": "kpis",
            "key_metrics": "kpis",
        }
        for key, value in sections.items():
            normalized_key = key_map.get(key.lower(), key.lower())
            if normalized_key in normalized and normalized[normalized_key]:
                continue  # Keep first occurrence
            normalized[normalized_key] = value
        return normalized

    def _clamp_tldr_length(self, tldr: str, max_words: int = 10) -> str:
        """Clamp TL;DR to max words."""
        if not tldr:
            return tldr
        words = tldr.strip().split()
        if len(words) <= max_words:
            return tldr.strip()
        clamped = " ".join(words[:max_words]).strip()
        if clamped and clamped[-1] not in ".!?":
            clamped += "."
        return clamped

    def _rewrite_tldr_to_exact_words(
        self,
        *,
        company_name: str,
        current_tldr: str,
        thesis: str,
        risks: str,
        catalysts: str,
        target_words: int,
        prior_reasons: List[str],
    ) -> str:
        system_message = (
            "You are an elite financial headline editor. Output only the final "
            "sentence. No commentary, no counts, no scratch work, no markdown."
        )
        current_display = normalize_tldr_contract_text(current_tldr) or "(missing)"
        thesis_excerpt = _tldr_context_excerpt(thesis, 45) or "(none)"
        risks_excerpt = _tldr_context_excerpt(risks, 35) or "(none)"
        catalysts_excerpt = _tldr_context_excerpt(catalysts, 35) or "(none)"
        reasons_text = ", ".join(prior_reasons) if prior_reasons else "invalid_contract"

        current_word_count = count_tldr_contract_words(current_tldr)
        delta = current_word_count - int(target_words)
        if delta > 0:
            delta_instruction = f"Your sentence has {current_word_count} words. Remove exactly {delta} word{'s' if delta != 1 else ''}."
        elif delta < 0:
            delta_instruction = f"Your sentence has {current_word_count} words. Add exactly {abs(delta)} word{'s' if abs(delta) != 1 else ''}."
        else:
            delta_instruction = f"Your sentence has {current_word_count} words (correct count) but failed quality checks."

        prompt = f"""Rewrite the TL;DR verdict for {company_name}.

Target: EXACTLY {int(target_words)} words.
{delta_instruction}

Constraints:
- One sentence only with final punctuation.
- Decision-oriented verdict plus the single biggest driver/cause.
- No repeated words.
- No markdown.
- No prefixes like TL;DR:
- Preserve meaning and tone as much as possible.
- Count each word in your response before submitting. Your output must contain exactly {int(target_words)} space-separated words.
- Make sure it is {int(target_words)} words exactly. Use code to calculate the word count and adjust the output until you get it right. Once done, produce the full output.
- If the candidate is missing or unusable, write a fresh verdict from context.

Example calibration — these are each exactly {int(target_words)} words:
- "Strong margins and cash conversion support sustained upside through execution."
- "Cautious view: leverage and weaker cash conversion elevate downside risk."
- "Revenue growth and margin expansion drive an increasingly constructive investment case."

Previous candidate:
{current_display}

Why it failed:
{reasons_text}

Context (for meaning only):
Thesis: {thesis_excerpt}
Risks: {risks_excerpt}
Catalysts: {catalysts_excerpt}

Return only the final sentence."""

        usage_context = {
            **(self.usage_context or {}),
            "call_type": "tldr_contract_rewrite",
        }
        return self._generate_with_retry(
            prompt,
            use_persona_model=False,
            usage_context=usage_context,
            generation_config_override={
                "temperature": 0.2,
                "max_output_tokens": 120,
            },
            timeout_seconds=12.0,
            stage_name="TLDR Contract Rewrite",
            system_message=system_message,
        ).strip()

    # Ranked list of removable word categories for deterministic trimming.
    # Order: articles, prepositions, adverbs, conjunctions — least meaningful first.
    _TRIM_CANDIDATES = [
        # articles
        {"a", "an", "the"},
        # weak prepositions
        {"of", "in", "on", "at", "by", "to", "for", "from", "with"},
        # filler adverbs
        {
            "very", "quite", "rather", "somewhat", "increasingly",
            "relatively", "particularly", "notably", "significantly",
            "overall", "generally", "largely", "mostly", "still",
        },
        # conjunctions (less preferred — removing changes structure more)
        {"and", "but", "or", "yet", "so", "while"},
    ]

    def _deterministic_tldr_adjust(
        self,
        text: str,
        target_words: int = TLDR_EXACT_WORD_TARGET,
    ) -> Optional[str]:
        """Deterministically adjust a TL;DR to exactly *target_words* words.

        Returns the adjusted text if the input is within ±2 words AND can be
        fixed without destroying readability.  Returns ``None`` when the gap is
        too large for a safe deterministic fix (caller should retry with model).
        """
        normalized = normalize_tldr_contract_text(text)
        words = normalized.split() if normalized else []
        count = len(words)
        delta = count - target_words

        if delta == 0:
            return normalized  # already exact

        # --- Too far off — punt back to model ---
        if delta > 2 or delta < -2:
            return None

        # --- Trim (11-12 words → 10) ---
        if delta > 0:
            trimmed_words = list(words)
            for _ in range(delta):
                removed = False
                for candidate_set in self._TRIM_CANDIDATES:
                    # Walk backwards so removing earlier words doesn't shift
                    # indices of later candidates, but never remove the first
                    # or last word (verdict anchor + punctuation).
                    for idx in range(len(trimmed_words) - 2, 0, -1):
                        token_lower = re.sub(
                            r"^[^\w']+|[^\w']+$", "", trimmed_words[idx].lower()
                        )
                        if token_lower in candidate_set:
                            trimmed_words.pop(idx)
                            removed = True
                            break
                    if removed:
                        break
                if not removed:
                    return None  # cannot trim safely

            result = " ".join(trimmed_words)
            # Ensure terminal punctuation
            if result and not _TLDR_END_PUNCT_RE.search(result):
                result = result.rstrip() + "."
            return result

        # --- Expand (8-9 words → 10): not safe deterministically ---
        # Caller should use a targeted model call instead.
        return None

    def _enforce_exact_tldr_contract(
        self,
        *,
        tldr: str,
        company_name: str,
        thesis: str = "",
        risks: str = "",
        catalysts: str = "",
        target_words: int = TLDR_EXACT_WORD_TARGET,
        max_attempts: int = TLDR_REWRITE_MAX_ATTEMPTS,
    ) -> str:
        initial = validate_tldr_contract(tldr, target_words=target_words)
        logger.info(
            "TLDR contract validation mode=initial word_count=%d reasons=%s",
            int(initial.get("word_count", 0) or 0),
            initial.get("reasons") or [],
        )
        if not initial["reasons"]:
            logger.info("TLDR contract accepted mode=direct word_count=%d", initial["word_count"])
            return str(initial["normalized"] or "").strip()

        current_tldr = str(initial.get("normalized") or tldr or "").strip()

        # Track the best candidate seen (closest to target that passes quality
        # checks aside from word count) for the hard fallback.
        best_candidate: Optional[str] = None
        best_delta = 999

        for attempt in range(max(1, int(max_attempts))):
            rewritten = self._rewrite_tldr_to_exact_words(
                company_name=company_name,
                current_tldr=current_tldr,
                thesis=thesis,
                risks=risks,
                catalysts=catalysts,
                target_words=target_words,
                prior_reasons=list(initial.get("reasons") or []),
            )
            report = validate_tldr_contract(rewritten, target_words=target_words)
            logger.info(
                "TLDR contract validation mode=rewrite attempt=%d word_count=%d reasons=%s",
                attempt + 1,
                int(report.get("word_count", 0) or 0),
                report.get("reasons") or [],
            )
            if not report["reasons"]:
                logger.info(
                    "TLDR contract accepted mode=rewritten attempt=%d word_count=%d",
                    attempt + 1,
                    int(report["word_count"]),
                )
                return str(report["normalized"] or "").strip()

            # --- Deterministic adjustment if word count is off by 1-2 ---
            rewrite_wc = int(report.get("word_count", 0) or 0)
            wc_delta = abs(rewrite_wc - target_words)
            only_wc_issue = all(
                r.startswith("word_count_mismatch") for r in report["reasons"]
            )

            # Track best candidate for fallback
            if wc_delta < best_delta:
                # Accept as best if only issue is word count, or if it's
                # still closer than anything we've seen
                quality_reasons = [
                    r for r in report["reasons"]
                    if not r.startswith("word_count_mismatch")
                ]
                if not quality_reasons:
                    best_candidate = str(report.get("normalized") or rewritten or "").strip()
                    best_delta = wc_delta

            if only_wc_issue and 1 <= wc_delta <= 2:
                adjusted = self._deterministic_tldr_adjust(
                    str(report.get("normalized") or rewritten or ""),
                    target_words=target_words,
                )
                if adjusted is not None:
                    adj_report = validate_tldr_contract(adjusted, target_words=target_words)
                    logger.info(
                        "TLDR contract validation mode=deterministic attempt=%d word_count=%d reasons=%s",
                        attempt + 1,
                        int(adj_report.get("word_count", 0) or 0),
                        adj_report.get("reasons") or [],
                    )
                    if not adj_report["reasons"]:
                        logger.info(
                            "TLDR contract accepted mode=deterministic attempt=%d word_count=%d",
                            attempt + 1,
                            int(adj_report["word_count"]),
                        )
                        return str(adj_report["normalized"] or "").strip()

            initial = report
            current_tldr = str(report.get("normalized") or rewritten or "").strip()

        # --- Hard fallback: deterministic adjust on best candidate ---
        if best_candidate is not None:
            adjusted = self._deterministic_tldr_adjust(
                best_candidate, target_words=target_words
            )
            if adjusted is not None:
                final_report = validate_tldr_contract(adjusted, target_words=target_words)
                logger.info(
                    "TLDR contract validation mode=hard_fallback word_count=%d reasons=%s",
                    int(final_report.get("word_count", 0) or 0),
                    final_report.get("reasons") or [],
                )
                if not final_report["reasons"]:
                    logger.info(
                        "TLDR contract accepted mode=hard_fallback word_count=%d",
                        int(final_report["word_count"]),
                    )
                    return str(final_report["normalized"] or "").strip()

        raise TLDRContractError(
            "Unable to satisfy exact TL;DR 10-word contract after bounded rewrites"
        )

    # ------------------------------------------------------------------
    # Persona generation (matches GeminiClient interface)
    # ------------------------------------------------------------------

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
        persona_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Generate investor persona-specific view."""
        ratios_str = "\n".join(
            f"- {key}: {value:.2%}" if isinstance(value, float) and abs(value) < 10
            else f"- {key}: {value:.2f}"
            for key, value in ratios.items() if value is not None
        )

        if not structure_template:
            structure_template = "## Analysis\n[Deep dive analysis]\n\n## The Verdict\n[Conclusion]"

        priorities_str = (
            "\n".join([f"{i + 1}. {p}" for i, p in enumerate(persona_priorities)])
            if persona_priorities else "N/A"
        )
        required_vocab_str = ", ".join(required_vocabulary) if required_vocabulary else "N/A"
        verdict_clause = verdict_style if verdict_style else "Provide a clear buy/hold/sell recommendation"
        ignore_clause = ignore_list if ignore_list else "N/A"

        prompt = f"""You are {persona_name}.
Your Philosophy: {persona_philosophy}
Tone: {persona_tone}

INSTRUCTIONS:
1. Rewrite the entire analysis using {persona_name}'s worldview. Do NOT rephrase the input.
2. Use ONLY the provided data. Do not invent data.
3. Stay first-person and keep tone consistent.
4. Use at least 3 of these terms: {required_vocab_str}
5. Signature verdict: {verdict_clause}
6. Ignore: {ignore_clause}
{custom_instructions}

PRIORITIES (in order):
{priorities_str}

MENTAL MODELS:
{chr(10).join([f"- {item}" for item in persona_mental_models])}

Source Material:
{general_summary}

Company: {company_name}

Financial Data:
{ratios_str}

Structure:
{structure_template}
        """
        if target_length:
            prompt += f"\nTarget length: {target_length} words (±10 tolerance).\n"

        try:
            response = self.generate_content(prompt, use_persona_model=True)
            response_text = response.text
            return self._parse_persona_response(response_text, persona_name)
        except Exception as e:
            logger.error("Error generating persona view for %s: %s", persona_name, e)
            return {
                "persona_name": persona_name,
                "summary": f"Error generating analysis: {e}",
                "stance": "Hold",
                "reasoning": "Generation failed",
                "key_points": [],
            }

    def generate_premium_persona_view(
        self, prompt: str, persona_name: str
    ) -> Dict[str, str]:
        """Generate premium persona analysis with completion retry on truncation."""
        try:
            response = self.generate_content(prompt, use_persona_model=True)
            response_text = response.text

            if self._is_truncated(response_text):
                completion_text = self._attempt_completion(response_text, persona_name)
                if completion_text:
                    response_text = response_text.rstrip() + " " + completion_text

            return self._parse_premium_persona_response(response_text, persona_name)

        except Exception as e:
            logger.error("Error generating premium persona view for %s: %s", persona_name, e)
            return {
                "persona_name": persona_name,
                "summary": f"Error generating analysis: {str(e)}",
                "stance": "Hold",
                "reasoning": "Generation failed",
                "key_points": [],
            }

    def _is_truncated(self, text: str) -> bool:
        """Detect if output was truncated mid-sentence."""
        if not text:
            return False
        text = text.strip()

        truncation_patterns = [
            r"\.\.\.\s*$",
            r",\s*$",
            r":\s*$",
            r";\s*$",
            r"\s+(?:and|or|but|the|a|an|to|of|for|with|in|on|at)\s*$",
            r"\$\d{1,3}\.\s*$",
            r"\$\d+\s*$",
            r"[-•]\s*$",
        ]

        for pattern in truncation_patterns:
            if re.search(pattern, text):
                return True

        if not text.rstrip().endswith((".", "!", "?", '"', "'", ")", "]")):
            if not re.search(r"(?:Pass|Buy|Hold|Sell|Watch)\s*[.!)]?\s*$", text, re.IGNORECASE):
                return True

        return False

    def _attempt_completion(self, incomplete_text: str, persona_name: str) -> str:
        """Attempt to complete truncated text."""
        try:
            context_end = incomplete_text[-500:] if len(incomplete_text) > 500 else incomplete_text
            completion_prompt = (
                f"You are {persona_name}. Complete this text naturally, continuing EXACTLY where it left off.\n"
                f"Do NOT repeat any of the provided text. Just write the next 1-3 sentences.\n\n"
                f"TEXT TO COMPLETE:\n...{context_end}\n\nCONTINUE:"
            )
            response = self.generate_content(completion_prompt, use_persona_model=True)
            completion = response.text.strip()
            if len(completion) > 500:
                sentences = completion.split(". ")
                if sentences:
                    completion = sentences[0] + "."
            return completion
        except Exception as e:
            logger.warning("Completion attempt failed for %s: %s", persona_name, e)
            return ""

    def _parse_persona_response(self, response_text: str, persona_name: str) -> Dict[str, str]:
        """Parse persona response text."""
        result: Dict[str, Any] = {
            "persona_name": persona_name,
            "summary": response_text.strip(),
            "stance": "Hold",
            "reasoning": "",
            "key_points": [],
        }

        text_lower = response_text.lower()
        buy_signals = ["buy", "back up the truck", "high conviction", "tenbagger", "overweight"]
        sell_signals = ["sell", "pass", "avoid", "rat poison", "underweight"]

        for signal in buy_signals:
            if signal in text_lower:
                result["stance"] = "Buy"
                break
        for signal in sell_signals:
            if signal in text_lower:
                result["stance"] = "Sell"
                break

        return result

    def _parse_premium_persona_response(self, response_text: str, persona_name: str) -> Dict[str, str]:
        """Parse premium persona response."""
        # Clean N/A lines
        cleaned_lines = []
        for line in response_text.split("\n"):
            line_stripped = line.strip()
            if any(p in line_stripped.lower() for p in [
                "not available", "n/a", ": n/a", "data not provided",
                "cannot calculate", "insufficient data",
            ]):
                if len(line_stripped) < 100:
                    continue
            cleaned_lines.append(line)
        response_text = "\n".join(cleaned_lines)

        result: Dict[str, Any] = {
            "persona_name": persona_name,
            "summary": response_text.strip(),
            "stance": "Hold",
            "reasoning": "",
            "key_points": [],
        }

        text_lower = response_text.lower()
        buy_signals = ["buy", "back up the truck", "high conviction", "tenbagger", "overweight"]
        sell_signals = ["sell", "pass", "avoid", "rat poison", "underweight"]

        for signal in buy_signals:
            if signal in text_lower:
                result["stance"] = "Buy"
                break
        for signal in sell_signals:
            if signal in text_lower:
                result["stance"] = "Sell"
                break

        return result


# ---------------------------------------------------------------------------
# Module-level factory (matches the former get_gemini_client signature)
# ---------------------------------------------------------------------------

def get_openai_client(model_name: Optional[str] = None) -> OpenAIClient:
    """Get OpenAI client instance with settings from config."""
    resolved_model_name = (
        (model_name or "").strip()
        or (os.getenv("OPENAI_MODEL_NAME") or "").strip()
        or DEFAULT_MODEL
    )
    settings = get_settings()
    return OpenAIClient(
        model_name=resolved_model_name,
        max_retries=settings.openai_max_retries,
        initial_wait=settings.openai_initial_wait,
        max_wait=settings.openai_max_wait,
    )


# ---------------------------------------------------------------------------
# Backward-compat aliases so existing imports work during transition
# ---------------------------------------------------------------------------
GeminiClient = OpenAIClient
get_gemini_client = get_openai_client


def generate_growth_assessment(
    filing_text: str,
    company_name: str,
    weighting_preference: Optional[str] = None,
    ratios: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Generate AI-driven growth assessment."""
    client = get_openai_client()

    growth_lens = {
        "profitability_margins": "Focus on whether growth is PROFITABLE growth.",
        "cash_flow_conversion": "Focus on whether growth is CASH-GENERATING growth.",
        "balance_sheet_strength": "Focus on whether growth is SUSTAINABLE without excessive leverage.",
        "liquidity_near_term_risk": "Focus on whether growth PRESERVES LIQUIDITY.",
        "execution_competitiveness": "Focus on COMPETITIVE POSITIONING.",
    }.get(weighting_preference, "Evaluate overall growth potential.")

    ratios_context = ""
    if ratios:
        ratios_context = "\n".join(
            f"- {k}: {v:.2%}" if isinstance(v, float) and abs(v) < 10 else f"- {k}: {v}"
            for k, v in ratios.items() if v is not None
        )

    prompt = f"""You are a growth analyst evaluating {company_name}.

GROWTH LENS: {growth_lens}

FILING TEXT (excerpt):
{filing_text[:5000]}

FINANCIAL RATIOS:
{ratios_context}

Provide a JSON object with:
- "score": integer 0-100 (growth assessment)
- "description": 2-3 sentence summary of growth outlook
- "key_drivers": list of 3 key growth drivers
- "risks": list of 2 key growth risks
"""

    try:
        response = client.generate_content(
            prompt,
            generation_config_override={"temperature": 0.3, "max_tokens": 800},
        )
        text = response.text.strip()

        # Try to parse JSON from the response
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())

        return {"score": 50, "description": text[:200], "key_drivers": [], "risks": []}
    except Exception as e:
        logger.error("Growth assessment error: %s", e)
        return {"score": 50, "description": f"Assessment unavailable: {e}", "key_drivers": [], "risks": []}
