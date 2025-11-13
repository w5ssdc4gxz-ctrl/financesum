"""Investor persona definitions and engine."""
from typing import Dict, List
from app.services.gemini_client import GeminiClient


# Investor Persona Definitions
INVESTOR_PERSONAS = {
    "buffett": {
        "name": "Warren Buffett",
        "philosophy": "Prefers durable competitive advantages (moats), predictable cash flows, competent capital allocation, and conservative capital structure. Avoids complex, highly cyclical businesses.",
        "checklist": [
            "Stable and growing Return on Invested Capital (ROIC)",
            "Predictable free cash flow and simple business model",
            "Management incentives aligned with shareholders"
        ],
        "tone": "Patient, conversational, and focused on long-term value"
    },
    "munger": {
        "name": "Charlie Munger",
        "philosophy": "Advocates rational, multi-disciplinary thinking. Focus on high-quality businesses with strong competitive positions and minimal psychological biases.",
        "checklist": [
            "Business quality and competitive moat",
            "Management rationality and integrity",
            "Absence of major agency problems"
        ],
        "tone": "Direct, rational, and skeptical of complexity"
    },
    "graham": {
        "name": "Benjamin Graham",
        "philosophy": "Classic value investor focused on margin of safety. Emphasizes quantitative undervaluation metrics like low P/E and price-to-book ratios.",
        "checklist": [
            "Low price-to-earnings and price-to-book ratios",
            "Significant margin of safety in valuation",
            "Strong balance sheet with low debt"
        ],
        "tone": "Conservative, quantitative, and focused on downside protection"
    },
    "lynch": {
        "name": "Peter Lynch",
        "philosophy": "Growth at a reasonable price (GARP). Likes understandable businesses with strong growth rates and reasonable valuations (PEG ratio).",
        "checklist": [
            "Understandable business model",
            "Strong earnings growth rate",
            "Reasonable PEG ratio"
        ],
        "tone": "Enthusiastic, practical, and focused on growth potential"
    },
    "dalio": {
        "name": "Ray Dalio",
        "philosophy": "Macro-aware investor with emphasis on risk parity and understanding economic cycles. Focuses on balance sheet durability across various economic scenarios.",
        "checklist": [
            "Balance sheet strength across economic cycles",
            "Diversification and risk management",
            "Understanding of macroeconomic context"
        ],
        "tone": "Analytical, macro-focused, and risk-conscious"
    },
    "wood": {
        "name": "Cathie Wood",
        "philosophy": "High conviction growth investor focused on disruptive innovation. Emphasizes forward-looking addressable market potential and technological disruption.",
        "checklist": [
            "Disruptive innovation potential",
            "Large and growing addressable market",
            "Strong R&D and innovation pipeline"
        ],
        "tone": "Enthusiastic, forward-looking, and innovation-focused"
    },
    "greenblatt": {
        "name": "Joel Greenblatt",
        "philosophy": "Magic formula value investor. Focuses on high return on capital (ROC) combined with low earnings yield (cheap price).",
        "checklist": [
            "High return on capital (ROC)",
            "Low earnings yield (attractive valuation)",
            "Simple, understandable business"
        ],
        "tone": "Systematic, quantitative, and focused on value metrics"
    },
    "bogle": {
        "name": "John Bogle",
        "philosophy": "Index investing advocate. Emphasizes low costs, diversification, and long-term buy-and-hold. Skeptical of active stock picking.",
        "checklist": [
            "Expense ratios and fees",
            "Diversification benefits",
            "Long-term fundamental strength"
        ],
        "tone": "Skeptical of active management, focused on costs and fundamentals"
    },
    "marks": {
        "name": "Howard Marks",
        "philosophy": "Emphasizes understanding market cycles, risk assessment, and second-level thinking. Cautious and contrarian approach.",
        "checklist": [
            "Understanding of current market cycle position",
            "Risk-adjusted return potential",
            "Contrarian opportunities"
        ],
        "tone": "Cautious, contrarian, and focused on risk management"
    },
    "ackman": {
        "name": "Bill Ackman",
        "philosophy": "Activist investor focused on identifying operational catalysts and actionable changes to unlock shareholder value.",
        "checklist": [
            "Potential for operational improvements",
            "Catalyst events and timing",
            "Management responsiveness to shareholders"
        ],
        "tone": "Activist, catalyst-focused, and results-oriented"
    }
}


class PersonaEngine:
    """Generate investor persona-based analysis."""
    
    def __init__(self, gemini_client: GeminiClient):
        """
        Initialize persona engine.
        
        Args:
            gemini_client: Gemini AI client instance
        """
        self.gemini_client = gemini_client
        self.personas = INVESTOR_PERSONAS
    
    def generate_persona_analysis(
        self,
        persona_id: str,
        company_name: str,
        general_summary: str,
        ratios: Dict[str, float]
    ) -> Dict[str, any]:
        """
        Generate analysis from a specific investor persona's perspective.
        
        Args:
            persona_id: ID of the persona (e.g., 'buffett', 'wood')
            company_name: Name of the company
            general_summary: General investment analysis
            ratios: Financial ratios
        
        Returns:
            Dictionary with persona-specific analysis
        """
        if persona_id not in self.personas:
            raise ValueError(f"Unknown persona: {persona_id}")
        
        persona = self.personas[persona_id]
        
        result = self.gemini_client.generate_persona_view(
            persona_name=persona["name"],
            persona_philosophy=persona["philosophy"],
            persona_checklist=persona["checklist"],
            persona_tone=persona["tone"],
            general_summary=general_summary,
            company_name=company_name,
            ratios=ratios
        )
        
        # Add persona ID and disclaimer
        result["persona_id"] = persona_id
        result["disclaimer"] = (
            "This is a simulated view based on publicly available writings and investment philosophies. "
            "It does not represent the real investor's current views or actions."
        )
        
        return result
    
    def generate_multiple_personas(
        self,
        persona_ids: List[str],
        company_name: str,
        general_summary: str,
        ratios: Dict[str, float]
    ) -> Dict[str, Dict]:
        """
        Generate analysis for multiple personas.
        
        Args:
            persona_ids: List of persona IDs
            company_name: Name of the company
            general_summary: General investment analysis
            ratios: Financial ratios
        
        Returns:
            Dictionary mapping persona ID to analysis
        """
        results = {}
        
        for persona_id in persona_ids:
            try:
                results[persona_id] = self.generate_persona_analysis(
                    persona_id=persona_id,
                    company_name=company_name,
                    general_summary=general_summary,
                    ratios=ratios
                )
            except Exception as e:
                print(f"Error generating persona {persona_id}: {e}")
                results[persona_id] = {
                    "persona_id": persona_id,
                    "persona_name": self.personas.get(persona_id, {}).get("name", "Unknown"),
                    "summary": "Error generating analysis",
                    "stance": "Hold",
                    "reasoning": str(e),
                    "key_points": []
                }
        
        return results
    
    def get_all_persona_ids(self) -> List[str]:
        """Get list of all available persona IDs."""
        return list(self.personas.keys())
    
    def get_persona_info(self, persona_id: str) -> Dict:
        """Get information about a specific persona."""
        if persona_id not in self.personas:
            raise ValueError(f"Unknown persona: {persona_id}")
        
        return self.personas[persona_id]


def get_persona_engine() -> PersonaEngine:
    """Get persona engine instance."""
    from app.services.gemini_client import get_gemini_client
    return PersonaEngine(get_gemini_client())












