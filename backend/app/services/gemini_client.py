"""Gemini AI client for generating summaries and analysis."""
import re
import google.generativeai as genai
from typing import Dict, List, Optional, Any, Callable
from app.config import get_settings


class GeminiClient:
    """Client for interacting with Gemini AI."""
    
    def __init__(self, model_name: str = "gemini-2.0-flash"):
        """
        Initialize Gemini client.
        
        Args:
            model_name: Name of the Gemini model to use
        """
        settings = get_settings()
        genai.configure(api_key=settings.gemini_api_key)
        
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
    
    def stream_generate_content(
        self,
        prompt: str,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        stage_name: str = "Generating",
        expected_tokens: int = 4000,
        use_persona_model: bool = False
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
            
            return accumulated_text
            
        except Exception as e:
            print(f"Streaming generation error: {e}")
            response = model.generate_content(prompt)
            return response.text

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
        
        max_retries = 3
        current_try = 0
        
        while current_try < max_retries:
            try:
                response = self.model.generate_content(prompt)
                summary_text = response.text
                
                # Check word count
                word_count = len(summary_text.split())
                min_words = target_length if target_length else 0
                
                if target_length and word_count < min_words:
                    print(f"Summary too short ({word_count} words). Retrying...")
                    prompt += f"\n\nSYSTEM FEEDBACK: You generated {word_count} words. The requirement is {min_words} words. You are {min_words - word_count} words short. FAILED. EXPAND the 'Thesis' and 'Risks' sections with specific examples and historical context to meet the length requirement."
                    current_try += 1
                    continue
                
                # Parse the response into structured sections
                return self._parse_summary_response(summary_text)
            
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
        complexity: str = "intermediate"
    ) -> str:
        """Build the prompt for company summary generation."""
        # Format financial data
        ratios_str = "\n".join([
            f"- {key}: {value:.2%}" if isinstance(value, float) and abs(value) < 10 else f"- {key}: {value:.2f}"
            for key, value in ratios.items()
            if value is not None
        ])
        
        priorities = persona_priorities or persona_checklist or []
        priorities_str = "\n".join([f"- {item}" for item in priorities]) if priorities else "N/A"
        priorities_inline = ", ".join(priorities) if priorities else "N/A"
        ignore_clause = ignore_list or "Generic market noise or anything outside this persona's circle of competence."
        required_vocab_str = ", ".join(required_vocabulary) if required_vocabulary else "None provided"
        verdict_clause = verdict_style or "Buy / Hold / Sell based on the persona's own rule."
        
        complexity_instruction = ""
        if complexity == "simple":
            complexity_instruction = "Use plain English and avoid jargon. Explain financial concepts simply."
        elif complexity == "expert":
            complexity_instruction = "Use sophisticated financial terminology. Assume the reader is an expert investor."
        else:
            complexity_instruction = "Use standard financial analysis language."

        length_instruction = ""
        if target_length:
            min_words = target_length
            max_words = target_length + 50
            length_instruction = f"""
CRITICAL LENGTH CONSTRAINT:
The total output MUST be at least {min_words} words.
Target range: {min_words} - {max_words} words.
If you write fewer than {min_words} words, you have FAILED.
Expand on the analysis, add more context, and go deeper into the "Why" and "So What" to meet the length requirement.
"""

        prompt = f"""You are an expert equity analyst. Analyze the following company data and produce a comprehensive investment memo.
{complexity_instruction}
{length_instruction}

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

## TL;DR (STRICT: 10 words max)
[Write ONE powerful sentence. MAXIMUM 10 words. Count them. Include stance (bullish/bearish/neutral). Examples: "AI chip monopoly, 75% margins. Bullish." (7 words) | "Overvalued growth stock burning cash. Bearish." (6 words)]

## Investment Thesis (5 bullet points)
[List 5 key reasons why this company could be an attractive investment, with brief explanations]

## Top 5 Risks
[List EXACTLY 5 major company-specific risks. Each risk MUST be:
1. Specific to THIS company's business model (not generic "regulatory risk" or "macro headwinds")
2. Named clearly (e.g., "Customer Concentration Risk", "Patent Cliff", "China Revenue Exposure")
3. Explained in 2-3 sentences with concrete detail and quantified impact where possible
Example: "Customer concentration: Top 3 hyperscalers (Microsoft, Amazon, Google) represent ~45% of Data Center revenue, creating dependency risk."]

## Strategic Initiatives & Capital Allocation
[Analyze how the company deploys capital:
- R&D intensity (as % of revenue) and what it funds
- Capital expenditure priorities (distinguish from shareholder returns)
- Buybacks and dividends (as % of free cash flow) - these fund shareholder returns, NOT growth
- M&A history and strategic rationale
- Management's stated priorities for capital deployment]

## Competitive Landscape
[Dedicated section analyzing the competitive environment:
- Identify key competitors (e.g., AMD, Intel, Hyperscalers for NVDA)
- Discuss competitive threats (e.g., custom silicon, pricing pressure)
- Analyze moat sustainability]

## Cash Flow Analysis
[Decompose the cash flow:
- Operating Cash Flow (CFO) trends
- Capex requirements
- Working capital impact
- Free Cash Flow (FCF) sustainability comment]

## Catalysts (3-5 items)
[List 3-5 potential catalysts with expected time horizons]

## Key KPIs to Monitor (5 items)
[List 5 key performance indicators investors should track]

## Investment Recommendation
[Provide a clear, actionable investment recommendation. This section MUST include:
1. **Rating**: State explicitly: BUY, HOLD, or SELL
2. **Conviction Level**: High, Medium, or Low
3. **Summary Rationale**: 2-3 sentences synthesizing the key findings from the entire analysis
4. **Key Conditions**: What would change your recommendation (upside triggers for HOLD/SELL, downside risks for BUY)

Format example:
"**Rating: HOLD (Medium Conviction)**
NVDA demonstrates exceptional profitability and market leadership in AI/accelerated computing, but the premium valuation leaves limited margin of safety. The recommendation shifts to BUY if margins remain above 40% for two consecutive quarters while revenue growth exceeds 20%, or to SELL if customer concentration risk materializes with hyperscaler order reductions exceeding 15%."]

CRITICAL FORMATTING RULES:
- Use consistent number formatting: billions as "X.XB" (e.g., $26.2B), millions as "X.XM"
- ALWAYS specify time period: (FY24), (Q3 FY25), (TTM) after each figure
- DISTINGUISH: Capex funds growth. Buybacks/dividends fund shareholder returns. Never conflate them.

ABSOLUTE SENTENCE COMPLETION REQUIREMENTS (CRITICAL - DO NOT VIOLATE):
- EVERY sentence MUST be complete. Never end a sentence mid-thought.
- FORBIDDEN: Ending with "but...", "although...", "however...", "while...", "which is...", "driven by the..."
- FORBIDDEN: Cutting off numbers like "FCF/Net Income of 0.51 demonstrates solid cash generation, but the figure is less than net..."
- FORBIDDEN: Executive summaries or conclusions that trail off mid-sentence
- If you write "but", "although", "however", or "while", you MUST complete the contrasting thought
- If you mention a ratio or metric, ALWAYS explain what it means, don't just state the number
- VERIFY: Before finishing, re-read your output and ensure EVERY sentence ends with a period, exclamation, or question mark AFTER a complete thought
- The final sentence of EVERY section must be a complete, standalone thought

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
        strict_mode: bool = False
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
13. **RISK/IMPACT**: Rank the single most important risk and describe its impact on margins, cash flow, and valuation in the persona’s language.
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
6. NO RATINGS OR SCORES: Bogle would never rate a stock "72/100" - that's absurd to him.
7. GRANDFATHERLY TONE: Wise, patient, humble. Not condescending, but firm in your convictions.
8. CLEAR CONCLUSION: Should the reader own this stock, or the index? Be direct and complete your thought.
Do NOT sound like a corporate analyst. Sound like a wise grandfather warning about Wall Street's self-serving advice.
Do NOT use "bullish" or "bearish" language. Do NOT give price targets. Do NOT analyze forward guidance.
END with a clear, complete conclusion - never leave a thought unfinished.

STORYTELLING MODE (MANDATORY FOR BUFFETT/MUNGER/LYNCH):
- Use METAPHORS and ANALOGIES. (e.g., "This business is a castle," "The CEO is the jockey.")
- AVOID JARGON. Do not say "operating margin expansion." Say "they are keeping more pennies from every dollar."
- NO BULLET POINTS FOR RISKS. Tell a story about what could go wrong.
- NO NUMERIC RATINGS. (e.g., "9/10"). Use words like "Wonderful," "Fair," or "Terrible."

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
  Example format: "**My Verdict: HOLD (Medium Conviction)** - While [Company] demonstrates [strength], the [concern] gives me pause. I would become a buyer if [condition], but would exit if [risk materializes]."

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

At the end, include ONLY these two lines (no headers, just the content):
STANCE: [Buy/Hold/Sell]
VERDICT: [One sentence summary of why]
"""
        
        max_retries = 3
        current_try = 0
        
        while current_try < max_retries:
            try:
                response = self.model.generate_content(prompt)
                result = self._parse_persona_response(response.text, persona_name)
                
                # Check word count of the summary section
                word_count = len(result["summary"].split())
                if word_count < 150:
                    print(f"Persona view too short ({word_count} words). Retrying...")
                    prompt += f"\n\nSYSTEM FEEDBACK: Your analysis was {word_count} words. The requirement is 150-250 words. FAILED. EXPAND your analysis. Add more interpretation of the data."
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
            response = self.persona_model.generate_content(prompt)
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

            response = self.persona_model.generate_content(completion_prompt)
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
    """Get Gemini client instance."""
    return GeminiClient()
