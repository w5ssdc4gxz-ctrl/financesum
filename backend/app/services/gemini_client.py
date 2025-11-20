"""Gemini AI client for generating summaries and analysis."""
import google.generativeai as genai
from typing import Dict, List, Optional, Any
from app.config import get_settings


class GeminiClient:
    """Client for interacting with Gemini AI."""
    
    def __init__(self, model_name: str = "gemini-2.0-flash-lite"):
        """
        Initialize Gemini client.
        
        Args:
            model_name: Name of the Gemini model to use
        """
        settings = get_settings()
        genai.configure(api_key=settings.gemini_api_key)
        self.model = genai.GenerativeModel(model_name)
    
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
        prompt = self._build_summary_prompt(
            company_name,
            financial_data,
            ratios,
            health_score,
            mda_text,
            risk_factors_text,
            target_length,
            complexity
        )
        
        try:
            response = self.model.generate_content(prompt)
            summary_text = response.text
            
            # Parse the response into structured sections
            return self._parse_summary_response(summary_text)
        
        except Exception as e:
            print(f"Error generating summary: {e}")
            return {
                "tldr": "Error generating summary",
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
        complexity: str = "intermediate"
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
CRITICAL LENGTH CONSTRAINT:
The total output MUST be between {min_words} and {max_words} words.
This is a HARD REQUIREMENT. Do not write less than {min_words} words. Do not write more than {max_words} words.
Check your word count before finishing.
"""

        prompt = f"""You are an expert equity analyst. Analyze the following company data and produce a comprehensive investment memo.
{complexity_instruction}
{length_instruction}

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
        
        prompt += """
Please provide the following analysis in a structured format:

## TL;DR (3 sentences)
[Provide a concise 3-sentence investment summary]

## Investment Thesis (5 bullet points)
[List 5 key reasons why this company could be an attractive investment, with brief explanations]

## Top 5 Risks
[List 5 major risks with short explanations. Include citations like [10-K:Risk Factors] when applicable]

## Catalysts (3 items)
[List 3 potential catalysts with expected time horizons]

## Key KPIs to Monitor (5 items)
[List 5 key performance indicators investors should track]

Keep tone: factual, succinct, and evidence-based. Include citations where possible.

REMINDER: Your total word count MUST be between {min_words} and {max_words} words.
"""
        
        return prompt
    
    def _parse_summary_response(self, response_text: str) -> Dict[str, str]:
        """Parse the structured response from Gemini."""
        sections = {
            "tldr": "",
            "thesis": "",
            "risks": "",
            "catalysts": "",
            "kpis": ""
        }
        
        # Simple parsing by section headers
        current_section = None
        lines = response_text.split("\n")
        
        for line in lines:
            line_lower = line.lower().strip()
            
            if "tl;dr" in line_lower or "tldr" in line_lower:
                current_section = "tldr"
            elif "investment thesis" in line_lower or "thesis" in line_lower:
                current_section = "thesis"
            elif "risk" in line_lower and ("top" in line_lower or "major" in line_lower):
                current_section = "risks"
            elif "catalyst" in line_lower:
                current_section = "catalysts"
            elif "kpi" in line_lower or "monitor" in line_lower:
                current_section = "kpis"
            elif line.startswith("#"):
                continue  # Skip section headers
            elif current_section and line.strip():
                sections[current_section] += line + "\n"
        
        # Combine all sections into full markdown
        full_summary = f"""# Investment Analysis: {response_text}

{response_text}
"""
        
        sections["full_summary"] = full_summary
        
        return sections
    
    def generate_persona_view(
        self,
        persona_name: str,
        persona_philosophy: str,
        persona_checklist: List[str],
        persona_tone: str,
        general_summary: str,
        company_name: str,
        ratios: Dict[str, float]
    ) -> Dict[str, str]:
        """
        Generate investor persona-specific view.
        
        Args:
            persona_name: Name of the investor persona
            persona_philosophy: Philosophy description
            persona_checklist: Key things this investor looks for
            persona_tone: Tone descriptor
            general_summary: General company summary
            company_name: Name of the company
            ratios: Financial ratios
        
        Returns:
            Dictionary with persona view and stance
        """
        ratios_str = "\n".join([
            f"- {key}: {value:.2%}" if isinstance(value, float) and abs(value) < 10 else f"- {key}: {value:.2f}"
            for key, value in ratios.items()
            if value is not None
        ])
        
        prompt = f"""You are simulating the investment perspective of {persona_name}.

Philosophy: {persona_philosophy}

Priority Checklist:
{chr(10).join([f'{i+1}. {item}' for i, item in enumerate(persona_checklist)])}

Tone: {persona_tone}

Company: {company_name}

Financial Ratios:
{ratios_str}

General Analysis:
{general_summary}

Task: Transform the general analysis into how {persona_name} would assess this company. Focus on what they'd praise, what they'd worry about, and whether they'd buy/hold/sell (simulated stance).

**IMPORTANT DISCLAIMER**: This is a simulation based on publicly available writings and investment philosophies. It does not represent the real person's current views or actions.

Provide your response in the following format:

## Simulated {persona_name} View

[300-600 word analysis in {persona_name}'s style and focus areas]

## Stance
[One word: Buy, Hold, or Sell]

## Reasoning (25 words max)
[Brief reason for the stance]

## Key Points
- [3-5 bullet points of main considerations from {persona_name}'s perspective]
"""
        
        try:
            response = self.model.generate_content(prompt)
            return self._parse_persona_response(response.text, persona_name)
        
        except Exception as e:
            print(f"Error generating persona view: {e}")
            return {
                "persona_name": persona_name,
                "summary": "Error generating persona view",
                "stance": "Hold",
                "reasoning": "Unable to generate analysis",
                "key_points": []
            }
    
    def _parse_persona_response(self, response_text: str, persona_name: str) -> Dict[str, str]:
        """Parse persona response."""
        result = {
            "persona_name": persona_name,
            "summary": "",
            "stance": "Hold",
            "reasoning": "",
            "key_points": []
        }
        
        lines = response_text.split("\n")
        current_section = None
        
        for line in lines:
            line_lower = line.lower().strip()
            
            if "## stance" in line_lower or line_lower == "stance":
                current_section = "stance"
            elif "## reasoning" in line_lower or line_lower == "reasoning":
                current_section = "reasoning"
            elif "## key points" in line_lower or "key points" in line_lower:
                current_section = "key_points"
            elif line.startswith("##"):
                current_section = "summary"
            elif current_section == "summary" and line.strip():
                result["summary"] += line + "\n"
            elif current_section == "stance" and line.strip():
                # Extract stance (Buy/Hold/Sell)
                if "buy" in line_lower:
                    result["stance"] = "Buy"
                elif "sell" in line_lower:
                    result["stance"] = "Sell"
                else:
                    result["stance"] = "Hold"
            elif current_section == "reasoning" and line.strip():
                result["reasoning"] += line + " "
            elif current_section == "key_points" and line.strip() and line.strip().startswith("-"):
                result["key_points"].append(line.strip()[1:].strip())
        
        result["summary"] = result["summary"].strip()
        result["reasoning"] = result["reasoning"].strip()
        
        return result


def get_gemini_client() -> GeminiClient:
    """Get Gemini client instance."""
    return GeminiClient()









