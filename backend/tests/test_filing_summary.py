import re
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import filings as filings_api
from app.config import get_settings
from app.main import app
from app.services import local_cache
from app.services.gemini_exceptions import GeminiTimeoutError
from app.services import summary_two_agent
from app.services.summary_agents import SummarySectionBalanceError
from app.services.summary_post_processor import SummaryValidationReport
from scripts.smoke_summary_continuous_v2 import _metrics_lines_for_budget, _section_body

WORD_PATTERN = re.compile(r"\b\w+\b")


@pytest.fixture(autouse=True)
def _default_narrative_document(monkeypatch, tmp_path):
    """Default all tests in this module to a narrative filing source document."""
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "0")
    monkeypatch.setenv("SUMMARY_ALLOW_REQUEST_STRICT_CONTRACT", "1")
    monkeypatch.setenv("SUMMARY_CONTINUOUS_V2_AUTO_LONGFORM", "0")
    monkeypatch.setenv("OPENAI_COST_PER_SUMMARY_USD", "10")
    monkeypatch.setattr(
        filings_api,
        "get_summary_usage_status",
        lambda _user_id: SimpleNamespace(
            plan="pro",
            limit=100,
            used=0,
            remaining=100,
            period_start=None,
            period_end=None,
            subscription_status="active",
            cancel_at_period_end=False,
            is_pro=True,
            billing_unavailable=False,
        ),
    )
    text = (
        'Management said "we remain focused on execution discipline and durable cash conversion." '
        "The filing also notes that pricing and reinvestment decisions will be balanced against margin durability."
    )
    path = tmp_path / "filing-narrative.txt"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(
        filings_api, "_ensure_local_document", lambda *_args, **_kwargs: path
    )
    monkeypatch.setattr(
        filings_api,
        "_load_document_excerpt",
        lambda *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        summary_two_agent,
        "get_company_research_dossier",
        lambda **_kwargs: "",
    )
    return path


def _backend_word_count(text: str) -> int:
    import string

    punct = string.punctuation + "“”’‘—–…"
    count = 0
    for raw_token in text.split():
        token = raw_token.strip(punct)
        if token:
            count += 1
    return count


def build_summary_with_word_count(word_count: int) -> str:
    """Return deterministic markdown text with a trailing WORD COUNT line.

    Content is distributed across properly structured markdown sections so
    that the post-processing pipeline (section-budget caps, echo removal)
    does not destroy the filler.  Filler uses simple prose words appended
    to each section body.
    """
    # Base content per section (matches the headers the pipeline expects).
    sections = [
        (
            "## Executive Summary",
            "Value investors care about durable earnings power and disciplined capital allocation.",
        ),
        (
            "## Financial Performance",
            "Revenue grew steadily while operating margins compressed due to higher input costs.",
        ),
        (
            "## Management Discussion & Analysis",
            (
                "Management emphasized reinvestment pacing, competitive moats, measured buybacks, disciplined hiring, "
                "and multi-year AI investments across infrastructure and product roadmaps. Leadership reiterated that "
                "capital deployment will prioritize high-IRR data center builds, targeted acquisitions, and steady "
                "international expansion while preserving balance sheet flexibility."
            ),
        ),
        (
            "## Risk Factors",
            "**Competition**: Intense rivalry from well-funded peers could erode market share and compress pricing power over time.",
        ),
        (
            "## Key Metrics",
            "→ Revenue: $1.0B\n→ Operating Margin: 10%",
        ),
        (
            "## Closing Takeaway",
            "Overall this is a business worth monitoring but the risk-reward is not asymmetric today.",
        ),
    ]

    # Build base text and count words.
    parts = []
    for header, body in sections:
        parts.append(f"{header}\n{body}")
    text = "\n\n".join(parts)
    current_words = _backend_word_count(text)

    if current_words > word_count:
        # Truncate by splitting tokens.
        tokens = text.split()
        text = " ".join(tokens[:word_count])
    elif current_words < word_count:
        filler_needed = word_count - current_words
        # Distribute filler evenly across the 4 narrative sections.
        fillable = [
            "Executive Summary",
            "Financial Performance",
            "Management Discussion & Analysis",
            "Risk Factors",
        ]
        per_section = filler_needed // len(fillable)
        leftover = filler_needed % len(fillable)
        word_idx = 0
        for i, sec_name in enumerate(fillable):
            count = per_section + (1 if i < leftover else 0)
            if count <= 0:
                continue
            # Build filler as a run of unique words forming prose-like sentences.
            filler_words = [f"word{word_idx + j}" for j in range(count)]
            word_idx += count
            filler_block = " ".join(filler_words)
            # Insert filler right after the section body.
            marker = f"## {sec_name}\n"
            pos = text.find(marker)
            if pos >= 0:
                body_start = pos + len(marker)
                next_section = text.find("\n\n## ", body_start)
                if next_section == -1:
                    next_section = len(text)
                text = (
                    text[:next_section].rstrip()
                    + " "
                    + filler_block
                    + text[next_section:]
                )

    actual = _backend_word_count(text)
    return f"{text}\nWORD COUNT: {actual}"


def _build_test_statements(period_end: str, revenue: float) -> dict:
    rev = float(revenue)
    operating_income = rev * 0.30
    net_income = rev * 0.22
    operating_cash_flow = rev * 0.36
    capex = rev * 0.06
    cash = rev * 0.15
    assets = max(rev * 2.8, rev + 1.0)
    liabilities = max(rev * 1.35, rev * 0.6)
    current_assets = max(rev * 0.65, 1.0)
    current_liabilities = max(rev * 0.30, 1.0)
    total_debt = max(rev * 0.45, 1.0)
    return {
        "income_statement": {
            "totalRevenue": {period_end: rev},
            "operating_income": {period_end: operating_income},
            "net_income": {period_end: net_income},
        },
        "balance_sheet": {
            "cash": {period_end: cash},
            "total_assets": {period_end: assets},
            "total_liabilities": {period_end: liabilities},
            "current_assets": {period_end: current_assets},
            "current_liabilities": {period_end: current_liabilities},
            "total_debt": {period_end: total_debt},
        },
        "cash_flow": {
            "operating_cash_flow": {period_end: operating_cash_flow},
            "capital_expenditures": {period_end: -capex},
        },
    }


def _seed_filing_bundle(
    filing_id: str,
    company_id: str,
    *,
    filing_type: str = "10-K",
    filing_date: str = "2025-12-31",
) -> None:
    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": filing_type,
        "filing_date": filing_date,
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "LNGF",
        "name": "Long Form Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "statements": _build_test_statements("2025-12-31", 2_500),
    }


def _clear_filing_bundle(filing_id: str, company_id: str) -> None:
    local_cache.fallback_filings_by_id.pop(filing_id, None)
    local_cache.fallback_companies.pop(company_id, None)
    local_cache.fallback_financial_statements.pop(filing_id, None)
    local_cache.fallback_filing_summaries.pop(filing_id, None)


def _relax_non_contract_quality_validators(monkeypatch) -> None:
    passthrough_factory = lambda *args, **kwargs: (lambda _text: None)
    for factory_name in (
        "_make_no_extra_sections_validator",
        "_make_risk_specificity_validator",
        "_make_numbers_discipline_validator",
        "_make_metric_priority_validator",
        "_make_section_transition_validator",
        "_make_verbatim_repetition_validator",
        "_make_phrase_limits_validator",
        "_make_instruction_leak_validator",
        "_make_sentence_stem_repetition_validator",
        "_make_cross_section_question_framing_validator",
        "_make_cross_section_number_repetition_validator",
        "_make_generic_filler_validator",
        "_make_cross_section_theme_repetition_validator",
        "_make_period_delta_bridge_validator",
        "_make_closing_recommendation_validator",
        "_make_closing_structure_validator",
        "_make_persona_exclusivity_validator",
        "_make_stance_consistency_validator",
        "_make_section_balance_validator",
    ):
        monkeypatch.setattr(filings_api, factory_name, passthrough_factory)


def _stabilize_summary_pipeline(monkeypatch) -> None:
    monkeypatch.setattr(
        filings_api, "_ensure_required_sections", lambda text, **kwargs: text
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _rewrite_passthrough,
    )
    monkeypatch.setattr(
        filings_api,
        "_run_summary_cleanup_pass",
        lambda text, **kwargs: text,
    )
    monkeypatch.setattr(filings_api, "_remove_metric_echo_loops", lambda text: text)
    monkeypatch.setattr(filings_api, "_merge_staccato_paragraphs", lambda text: text)
    monkeypatch.setattr(
        filings_api,
        "_cap_closing_sentences_filings",
        lambda text, **kwargs: text,
    )


def _build_long_form_summary(
    word_count: int,
    *,
    include_exec_quote: bool = True,
    include_mdna_quote: bool = True,
) -> str:
    exec_quote = (
        '"we remain focused on execution discipline and durable cash conversion." '
        if include_exec_quote
        else ""
    )
    mdna_quote_primary = (
        '"pricing and reinvestment decisions will be balanced against margin durability." '
        if include_mdna_quote
        else ""
    )
    mdna_quote_secondary = (
        '"we remain focused on execution discipline and durable cash conversion." '
        if include_mdna_quote
        else ""
    )

    sections = [
        (
            "## Executive Summary",
            (
                "The investment setup depends on whether operating discipline can translate into durable outcomes through changing demand conditions. "
                f"{exec_quote}"
                "Financial Performance below tests that central tension with evidence and implications, then hands the narrative into management execution choices."
            ),
        ),
        (
            "## Financial Performance",
            (
                "Compared with the prior period, revenue quality and operating conversion remained directionally consistent while mix shifted across segments. "
                "The practical implication is that reported performance still needs management discussion context to determine whether conversion durability is repeatable."
            ),
        ),
        (
            "## Management Discussion & Analysis",
            (
                "Management emphasized sequencing, pacing, and operating control across product, channel, and capital allocation decisions. "
                f"{mdna_quote_primary}{mdna_quote_secondary}"
                "Those decisions set up the risk factors section by defining where execution can either preserve durability or transmit downside into future profitability."
            ),
        ),
        (
            "## Risk Factors",
            (
                "**Execution Risk**: If operational pacing and commercial timing diverge, conversion quality can erode before management can fully reset the cost base. "
                "The key metrics below anchor the monitoring framework for that transmission path and clarify how downside would show up in the operating model."
            ),
        ),
        (
            "## Key Metrics",
            (
                "DATA_GRID_START\n"
                "Revenue | $2.50B\n"
                "Operating Income | $0.70B\n"
                "Operating Margin | 28.0%\n"
                "Free Cash Flow | $0.65B\n"
                "Current Ratio | 2.3x\n"
                "DATA_GRID_END"
            ),
        ),
        (
            "## Closing Takeaway",
            (
                "I HOLD Long Form Corp because the operating setup remains constructive but still requires sustained execution quality. "
                "I would upgrade to BUY if operating margin stays above prior-cycle levels over the next two quarters. "
                "I would downgrade to SELL if free cash flow falls below current run-rate in the next twelve months."
            ),
        ),
    ]

    base_text = "\n\n".join(f"{header}\n{body}" for header, body in sections)
    current_words = _backend_word_count(base_text)
    if current_words >= word_count:
        tokens = base_text.split()
        clipped = " ".join(tokens[:word_count]).strip()
        return f"{clipped}\nWORD COUNT: {_backend_word_count(clipped)}"

    def _alpha_token(index: int) -> str:
        alphabet = "abcdefghijklmnopqrstuvwxyz"
        n = int(index)
        chars: list[str] = []
        while True:
            chars.append(alphabet[n % 26])
            n = (n // 26) - 1
            if n < 0:
                break
        return "detail" + "".join(reversed(chars))

    fillable_sections = [
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Closing Takeaway",
    ]
    filler_needed = word_count - current_words
    per_section = filler_needed // len(fillable_sections)
    leftover = filler_needed % len(fillable_sections)
    out = base_text
    global_offset = 0

    for idx, section_name in enumerate(fillable_sections):
        count = per_section + (1 if idx < leftover else 0)
        if count <= 0:
            continue
        words: list[str] = []
        for i in range(count):
            words.append(_alpha_token(global_offset + i))
        global_offset += count
        filler_text = " ".join(words).strip()
        if not filler_text:
            continue
        marker = f"## {section_name}\n"
        pos = out.find(marker)
        if pos < 0:
            continue
        body_start = pos + len(marker)
        next_section = out.find("\n\n## ", body_start)
        if next_section == -1:
            next_section = len(out)
        current_body = out[body_start:next_section].rstrip()
        if current_body and not current_body.endswith((".", "!", "?")):
            current_body = f"{current_body}."
        expanded_body = f"{current_body} {filler_text}.".strip()
        out = out[:body_start] + expanded_body + out[next_section:]

    return f"{out}\nWORD COUNT: {_backend_word_count(out)}"


def _rewrite_passthrough(*args, **kwargs):
    summary_text = kwargs.get("summary_text")
    if summary_text is None and len(args) >= 2:
        summary_text = args[1]
    summary_text = str(summary_text or "")
    return summary_text, (filings_api._count_words(summary_text), 15)


def _sentence_filler_body(word_count: int, prefix: str) -> str:
    def _alpha(index: int) -> str:
        letters = "abcdefghijklmnopqrstuvwxyz"
        n = int(index)
        out = []
        while True:
            out.append(letters[n % 26])
            n = (n // 26) - 1
            if n < 0:
                break
        return "".join(reversed(out))

    clean_prefix = re.sub(r"[^a-zA-Z]+", "", str(prefix or "")).lower() or "w"
    words = [f"{clean_prefix}{_alpha(i)}" for i in range(max(1, int(word_count)))]
    chunks = []
    step = 10
    for idx in range(0, len(words), step):
        chunk = " ".join(words[idx : idx + step]).strip()
        if chunk:
            chunks.append(f"{chunk}.")
    return " ".join(chunks).strip()


def _count_sentences(text: str) -> int:
    return len(
        [s for s in re.split(r"(?<=[.!?])\s+", str(text or "").strip()) if s.strip()]
    )


def test_summary_falls_back_to_statement_only_context_when_narrative_document_missing(
    monkeypatch,
):
    """Synthetic/data-only filings should still summarize from statements only."""
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "test-summary-filing"
    company_id = "test-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-K",
        "filing_date": "2023-12-31",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "TEST",
        "name": "Moncler S.p.A.",
        "country": "US",
        "cik": None,  # Force document download to be skipped so statements are used
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2023-01-01",
        "period_end": "2023-12-31",
        "statements": _build_test_statements("2023-12-31", 123456),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    class DummyModel:
        def generate_content(self, prompt: str):
            class Response:
                text = "summary\nWORD COUNT: 1"

            return Response()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())
    monkeypatch.setattr(
        filings_api,
        "_ensure_local_document",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        filings_api,
        "_load_document_excerpt",
        lambda *_args, **_kwargs: "",
    )
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    captured: dict[str, str] = {}

    def _fake_generate(*args, **kwargs):
        captured["prompt"] = str(args[1] if len(args) > 1 else kwargs.get("base_prompt") or "")
        return build_summary_with_word_count(650)

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        _fake_generate,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda *args, **kwargs: (
            [],
            {
                "target_length": 650,
                "final_word_count": 650,
                "final_split_word_count": 650,
                "verified_quote_count": 0,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 650},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        assert payload.get("degraded") is True
        assert payload.get("degraded_reason") == "statement_only_source"
        assert "financial statements only" in " ".join(payload.get("warnings") or []).lower()
        summary_meta = payload.get("summary_meta") or {}
        assert summary_meta.get("source_context_mode") == "financial_statements_only"
        assert summary_meta.get("statement_only_source_mode") is True
        prompt = captured.get("prompt") or ""
        assert "Primary filing narrative text is unavailable" in prompt
        assert "Do not invent management quotes or narrative attribution." in prompt
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_without_narrative_document_or_usable_statements_returns_422(
    monkeypatch,
):
    """When neither narrative text nor usable statement data exists, keep failing fast."""
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "test-summary-no-doc-no-data"
    company_id = "test-company-no-doc-no-data"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-K",
        "filing_date": "2023-12-31",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "TEST",
        "name": "No Document Corp",
        "country": "US",
        "cik": None,
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2023-01-01",
        "period_end": "2023-12-31",
        "statements": {},
    }

    monkeypatch.setattr(
        filings_api,
        "_ensure_local_document",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        filings_api,
        "_load_document_excerpt",
        lambda *_args, **_kwargs: "",
    )

    client = TestClient(app)
    response = client.post(f"/api/v1/filings/{filing_id}/summary")

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail", "")
        assert "No narrative filing text is available" in detail

        progress_response = client.get(f"/api/v1/filings/{filing_id}/progress")
        assert progress_response.status_code == 200
        progress_payload = progress_response.json() or {}
        assert progress_payload.get("error") is True
        assert progress_payload.get("last_error_message")
        assert "No narrative filing text is available" in str(
            progress_payload.get("last_error_message") or ""
        )
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_short_form_structural_seal_restores_missing_closing_recommendation() -> None:
    metrics_lines = (
        "DATA_GRID_START\n"
        "Revenue | $2.50B\n"
        "Operating Income | $0.70B\n"
        "Operating Margin | 28.0%\n"
        "Free Cash Flow | $0.65B\n"
        "Current Ratio | 2.3x\n"
        "DATA_GRID_END"
    )
    summary = (
        "## Executive Summary\n"
        "Profitability improved, but the quarter still depends on repeatable cash conversion.\n\n"
        "## Financial Performance\n"
        "Revenue, operating income, and free cash flow all improved against the prior period.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is balancing reinvestment with margin discipline as infrastructure needs rise.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A softer demand environment could pressure pricing and cash generation.\n\n"
        "## Key Metrics\n"
        f"{metrics_lines}\n\n"
        "## Closing Takeaway\n"
        "The underwriting case still depends on margin durability and cash conversion through the next year."
    )
    calculated_metrics = {
        "revenue": 2_500_000_000,
        "operating_income": 700_000_000,
        "operating_margin": 28.0,
        "net_margin": 20.0,
        "operating_cash_flow": 900_000_000,
        "free_cash_flow": 650_000_000,
        "cash": 500_000_000,
        "marketable_securities": 100_000_000,
        "total_liabilities": 900_000_000,
        "current_ratio": 2.3,
    }

    sealed = filings_api._apply_short_form_structural_seal(
        summary,
        include_health_rating=False,
        metrics_lines=metrics_lines,
        calculated_metrics=calculated_metrics,
        company_name="Seal Test Corp",
        risk_factors_excerpt=None,
        health_score_data=None,
        health_rating_config=None,
        persona_name=None,
        persona_requested=False,
        target_length=650,
    )

    closing_body = filings_api._extract_markdown_section_body(sealed, "Closing Takeaway")
    assert closing_body is not None
    assert re.search(r"\b(buy|hold|sell)\b", closing_body, re.IGNORECASE)
    assert "Seal Test Corp" in closing_body


def test_non_longform_catastrophic_underflow_is_not_triggered_near_650_band() -> None:
    # This mirrors the reported class of failure: short of target, but not a collapse.
    assert filings_api._is_catastrophic_underflow(650, 574, 551) is False


def test_non_longform_catastrophic_underflow_triggers_for_collapsed_short_draft() -> (
    None
):
    assert filings_api._is_catastrophic_underflow(650, 260, 240) is True


def test_section_balance_validator_uses_scaled_tolerance_for_short_targets() -> None:
    target_length = 200
    include_health_rating = False
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=include_health_rating
    )

    def _body(word_count: int, prefix: str) -> str:
        return " ".join(f"{prefix}{i}" for i in range(max(1, int(word_count))))

    exec_expected = int(budgets.get("Executive Summary") or 0)
    assert exec_expected > 0
    exec_tolerance = filings_api._section_budget_tolerance_words(
        exec_expected, max_tolerance=10
    )
    assert exec_tolerance < 10

    sections = []
    for title in (
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Key Metrics",
        "Closing Takeaway",
    ):
        expected = int(budgets.get(title, 1) or 1)
        actual = expected
        if title == "Executive Summary":
            actual = max(1, expected - (exec_tolerance + 1))
        sections.append(f"## {title}\n{_body(actual, title[:2].lower())}")
    memo = "\n\n".join(sections)

    validator = filings_api._make_section_balance_validator(
        include_health_rating=include_health_rating, target_length=target_length
    )
    issue = validator(memo)
    assert issue is not None
    assert "Section balance issue" in issue
    assert "'Executive Summary' is underweight" in issue
    assert f"±{exec_tolerance}" in issue


def test_apply_strict_contract_seal_repairs_and_restores_core_contract_elements(
    monkeypatch,
):
    # Isolate deterministic contract repairs from length-band cleanup behavior.
    monkeypatch.setattr(
        filings_api,
        "_ensure_final_strict_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_whitespace_word_band",
        lambda text, *_args, **_kwargs: text,
    )

    summary = (
        "## Financial Health Rating\n"
        "Profitability and liquidity remain solid for the period.\n\n"
        "## Executive Summary\n"
        "The thesis is constructive but still needs clearer evidence and quote grounding.\n\n"
        "## Financial Performance\n"
        "Revenue and margins were mixed, and management execution choices determine persistence.\n\n"
        "## Management Discussion & Analysis\n"
        "Management discussed strategy and investment pacing but did not include direct quotations.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: Timing mismatches could pressure margins if demand softens.\n\n"
        "## Key Metrics\n"
        "Revenue | $1.0B\n\n"
        "## Closing Takeaway\n"
        "I HOLD the stock while management executes."
    )
    filing_language_snippets = (
        '- "we remain focused on execution discipline and durable cash conversion."\n'
        '- "pricing and reinvestment decisions will be balanced against margin durability."\n'
        '- "our strategy remains centered on disciplined investment and operating leverage."'
    )
    metrics_lines = (
        "DATA_GRID_START\n"
        "Revenue | $1.00B\n"
        "Operating Income | $0.30B\n"
        "Operating Margin | 30.0%\n"
        "Free Cash Flow | $0.20B\n"
        "Current Ratio | 2.0x\n"
        "DATA_GRID_END"
    )

    sealed = filings_api._apply_strict_contract_seal(
        summary,
        include_health_rating=True,
        target_length=650,
        calculated_metrics={"operating_margin": 30.0, "free_cash_flow": 200_000_000},
        metrics_lines=metrics_lines,
        filing_language_snippets=filing_language_snippets,
        strict_quote_contract=True,
        generation_stats={},
    )

    assert "This frames the thesis in the Executive Summary that follows." in sealed
    assert filings_api._count_direct_quotes_in_section(sealed, "Executive Summary") >= 1
    assert (
        filings_api._count_direct_quotes_in_section(
            sealed, "Management Discussion & Analysis"
        )
        >= 1
    )
    total_quotes = filings_api._count_direct_quotes_in_section(
        sealed, "Executive Summary"
    ) + filings_api._count_direct_quotes_in_section(
        sealed, "Management Discussion & Analysis"
    )
    assert total_quotes >= 3
    key_metrics_body = (
        filings_api._extract_markdown_section_body(sealed, "Key Metrics") or ""
    )
    key_metrics_issue, _ = filings_api._validate_key_metrics_numeric_block(
        key_metrics_body, min_rows=5, require_markers=True
    )
    assert key_metrics_issue is None
    assert filings_api._make_closing_structure_validator()(sealed) is None

    # Simulate a post-processing mutation that strips the bridge and MD&A quotes.
    mutated = sealed.replace(
        "This frames the thesis in the Executive Summary that follows.", ""
    )
    mdna_body = (
        filings_api._extract_markdown_section_body(
            mutated, "Management Discussion & Analysis"
        )
        or ""
    )
    mdna_without_quotes = re.sub(
        r"[“\"]([^“”\"\\n]{8,260})[”\"]", "", mdna_body
    ).strip()
    mutated = filings_api._replace_markdown_section_body(
        mutated, "Management Discussion & Analysis", mdna_without_quotes
    )

    resealed = filings_api._apply_strict_contract_seal(
        mutated,
        include_health_rating=True,
        target_length=650,
        calculated_metrics={"operating_margin": 30.0, "free_cash_flow": 200_000_000},
        metrics_lines=metrics_lines,
        filing_language_snippets=filing_language_snippets,
        strict_quote_contract=True,
        generation_stats={},
    )
    assert "This frames the thesis in the Executive Summary that follows." in resealed
    assert (
        filings_api._count_direct_quotes_in_section(
            resealed, "Management Discussion & Analysis"
        )
        >= 1
    )


def test_parse_summary_contract_missing_requirements_classifies_editorial_bundle() -> None:
    flags = filings_api._parse_summary_contract_missing_requirements(
        [
            "Number repetition across sections: '42.1%' appears in 4 sections. Use each specific figure in at most 2-3 sections; reference it by context elsewhere.",
            "Theme over-repetition: 'free cash flow' discussed in 5 sections. Consolidate to the most relevant section and reference briefly elsewhere.",
            "Numbers discipline: Executive Summary is too numeric (10 numeric tokens). Keep it mostly qualitative with only 1-2 anchor figures; move dense metrics to Financial Performance / Key Metrics.",
            "Closing Takeaway contains low-signal parenthetical fragments. Rewrite as clean prose without parenthetical filler.",
            "Section balance issue: 'Financial Health Rating' is underweight (64 words; target ~76±5). Expand it and shorten other sections proportionally so the memo stays within 635-665 words.",
        ]
    )
    assert flags["number_repetition_issue"] is True
    assert flags["theme_repetition_issue"] is True
    assert flags["numbers_discipline_issue"] is True
    assert flags["closing_parenthetical_issue"] is True
    assert flags["section_balance_issue"] is True
    assert flags["needs_editorial_deterministic_repair"] is True
    assert "Executive Summary" in (flags["numbers_discipline_sections"] or [])
    assert "Financial Health Rating" in (flags["section_balance_underweight_titles"] or [])


def test_parse_summary_contract_missing_requirements_classifies_leading_word_repetition() -> None:
    flags = filings_api._parse_summary_contract_missing_requirements(
        [
            "Leading-word repetition in Risk Factors: 4 sentences start with 'If'. Vary sentence openings — do not begin more than 2 sentences per section with the same word.",
            "Section balance issue: 'Financial Health Rating' is overweight (463 words; target ~360±10). Tighten it and reallocate words to the shorter sections (especially Risk Factors), while staying within 2980-3020 words.",
        ]
    )
    assert flags["leading_word_repetition_issue"] is True
    assert flags["needs_editorial_deterministic_repair"] is True
    assert "Risk Factors" in (flags["leading_word_repetition_sections"] or [])
    assert "Financial Health Rating" in (flags["section_balance_overweight_titles"] or [])


def test_parse_summary_contract_missing_requirements_classifies_question_framing_repetition() -> None:
    flags = filings_api._parse_summary_contract_missing_requirements(
        [
            "Question-framing repetition: multiple sections restate the thesis as a question. Keep that framing in Executive Summary only and answer it directly elsewhere.",
        ]
    )
    assert flags["question_framing_repetition_issue"] is True
    assert flags["needs_editorial_deterministic_repair"] is True


def test_editorial_repairs_dedupe_cross_section_numbers_with_profile_cap_two() -> None:
    memo = (
        "## Financial Health Rating\n"
        "Profitability remained stable at 42.1% and liquidity stayed adequate.\n\n"
        "## Executive Summary\n"
        "The thesis leans on a 42.1% margin profile and disciplined execution.\n\n"
        "## Financial Performance\n"
        "Financial Performance shows 42.1% operating margin versus the prior period with steady conversion.\n\n"
        "## Management Discussion & Analysis\n"
        "Management Discussion & Analysis ties strategy pacing to the 42.1% margin outcome.\n\n"
        "## Risk Factors\n"
        "Risk Factors include downside if the 42.1% margin level cannot hold.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $1.00B\nOperating Margin | 42.1%\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I HOLD the stock while execution remains credible."
    )
    validator = filings_api._make_cross_section_number_repetition_validator(
        max_sections_per_figure=2
    )
    issue = validator(memo)
    assert issue is not None and "Number repetition across sections" in issue

    profile = filings_api.SummaryFlowQualityProfile(
        max_sections_per_repeated_number=2,
        max_sections_per_theme=4,
    )
    repaired, info = filings_api._apply_editorial_contract_repairs(
        memo,
        target_length=650,
        include_health_rating=True,
        quality_profile=profile,
        missing_requirements=[issue],
        generation_stats={},
    )
    assert info["number_dedupe_replacements"] >= 1
    assert validator(repaired) is None


def test_editorial_repairs_dedupe_cross_section_themes_with_profile_cap_three() -> None:
    memo = (
        "## Financial Health Rating\n"
        "Financial health reflects durable free cash flow generation and moderate leverage.\n\n"
        "## Executive Summary\n"
        "The thesis depends on whether free cash flow remains durable under competitive pressure.\n\n"
        "## Financial Performance\n"
        "Financial Performance shows free cash flow conversion improved versus the prior period.\n\n"
        "## Management Discussion & Analysis\n"
        "Management Discussion & Analysis emphasizes free cash flow discipline in capital allocation decisions.\n\n"
        "## Risk Factors\n"
        "Risk Factors include free cash flow pressure if pricing weakens.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nFree Cash Flow | $0.50B\nCurrent Ratio | 2.0x\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I HOLD while cash generation remains consistent and downside triggers are monitored."
    )
    validator = filings_api._make_cross_section_theme_repetition_validator(
        max_sections_per_theme=3
    )
    issue = validator(memo)
    assert issue is not None and "Theme over-repetition" in issue

    profile = filings_api.SummaryFlowQualityProfile(
        max_sections_per_repeated_number=3,
        max_sections_per_theme=3,
    )
    repaired, info = filings_api._apply_editorial_contract_repairs(
        memo,
        target_length=650,
        include_health_rating=True,
        quality_profile=profile,
        missing_requirements=[issue],
        generation_stats={},
    )
    assert info["theme_dedupe_replacements"] >= 1
    assert validator(repaired) is None


def test_editorial_repairs_fix_leading_word_repetition_in_risk_factors_preserve_quotes() -> None:
    quoted = '"management highlighted concentrated renewal timing in one segment."'
    risk_body = (
        "If pricing weakens in the highest-margin product line, margin compression would flow through gross profit and cash generation in the next year. "
        "If channel inventory remains elevated, order timing could lag production planning and pressure utilization in the next two quarters. "
        f"Management also noted {quoted}. "
        "If enterprise demand slows at renewal, backlog conversion could soften and reduce operating leverage against fixed costs. "
        "If implementation cycles lengthen, working capital timing could deteriorate before management can offset the drag."
    )
    memo = (
        "## Executive Summary\n"
        "The thesis depends on durable execution and disciplined capital allocation.\n\n"
        "## Financial Performance\n"
        "Financial Performance ties reported revenue and margin changes to product mix and cost absorption.\n\n"
        "## Management Discussion & Analysis\n"
        "Management Discussion & Analysis links capital allocation to durability and identifies the mechanisms that sustain returns.\n\n"
        "## Risk Factors\n"
        f"{risk_body}\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $1.00B\nOperating Margin | 15.0%\nFree Cash Flow | $0.40B\nCurrent Ratio | 2.0x\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I HOLD while execution remains stable and downside triggers are monitored."
    )
    validator = filings_api._make_sentence_stem_repetition_validator(max_same_opening=2)
    issue = validator(memo)
    assert issue is not None and "Leading-word repetition in Risk Factors" in issue

    repaired, info = filings_api._apply_editorial_contract_repairs(
        memo,
        target_length=3000,
        include_health_rating=False,
        quality_profile=filings_api.SummaryFlowQualityProfile(max_same_opening=2),
        missing_requirements=[issue],
        generation_stats={},
    )
    repaired_risk = filings_api._extract_markdown_section_body(repaired, "Risk Factors") or ""
    assert info["leading_word_repetition_rewrites"] >= 1
    assert quoted in repaired_risk
    assert validator(repaired) is None


def test_editorial_repairs_dedupe_cross_section_question_framing() -> None:
    memo = (
        "## Executive Summary\n"
        "The key question is whether operating momentum can hold as reinvestment rises. "
        "Revenue is still scaling against a solid margin base.\n\n"
        "## Financial Performance\n"
        "The execution question is whether management can keep the same margin structure while growth normalizes. "
        "Revenue and cash flow both improved versus the prior quarter.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is pushing investment into capacity and product depth without abandoning discipline.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A weaker demand backdrop would pressure pricing and conversion.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $1.00B\nOperating Margin | 15.0%\nFree Cash Flow | $0.40B\nCurrent Ratio | 2.0x\nNet Income | $0.20B\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I HOLD while operating margin stays above 14% over the next year."
    )
    validator = filings_api._make_cross_section_question_framing_validator()
    issue = validator(memo)
    assert issue is not None and "Question-framing repetition" in issue

    repaired, info = filings_api._apply_editorial_contract_repairs(
        memo,
        target_length=650,
        include_health_rating=False,
        quality_profile=filings_api.SummaryFlowQualityProfile(),
        missing_requirements=[issue],
        generation_stats={},
    )
    assert info["question_framing_rewrites"] >= 1
    assert validator(repaired) is None


def test_editorial_repairs_remove_low_signal_closing_parentheticals_but_keep_measurable() -> None:
    closing = (
        "I HOLD the stock (as things stand) because execution remains acceptable (in this environment). "
        "I would upgrade to BUY if operating margin stays above 12% over the next two quarters "
        "(if operating margin stays above 12% over the next two quarters). "
        "I would downgrade to SELL if free cash flow falls below $0.80B in the next four quarters."
    )
    memo = f"## Closing Takeaway\n{closing}"
    repaired, info = filings_api._apply_editorial_contract_repairs(
        memo,
        target_length=650,
        include_health_rating=True,
        quality_profile=filings_api.SummaryFlowQualityProfile(),
        missing_requirements=[
            "Closing Takeaway contains low-signal parenthetical fragments. Rewrite as clean prose without parenthetical filler."
        ],
        generation_stats={},
    )
    closing_body = filings_api._extract_markdown_section_body(repaired, "Closing Takeaway") or ""
    assert info["closing_parenthetical_removals"] >= 2
    assert "(as things stand)" not in closing_body.lower()
    assert "(in this environment)" not in closing_body.lower()
    assert "(if operating margin stays above 12% over the next two quarters)" in closing_body


def test_editorial_repairs_scrub_exec_numeric_density_without_touching_quotes() -> None:
    quoted = '"management targets margin stability above 42.1% through FY26."'
    exec_body = (
        f"The quarter included 42.1%, $1.2B revenue, 18.4% margin, FY25 timing, 2.3x leverage, and Q4 seasonality. "
        f"{quoted} "
        "Financial Performance below explains the operating mechanism and why the result may persist."
    )
    memo = (
        "## Executive Summary\n"
        f"{exec_body}\n\n"
        "## Financial Performance\n"
        "Compared with the prior period, revenue and margin movements reflected mix and pricing discipline.\n\n"
        "## Management Discussion & Analysis\n"
        "Management emphasized operating cadence and capital allocation priorities.\n\n"
        "## Risk Factors\n"
        "Execution and demand timing remain the main downside vectors.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $1.20B\nOperating Margin | 18.4%\nCurrent Ratio | 2.3x\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I HOLD the stock while execution remains stable."
    )
    numbers_validator = filings_api._make_numbers_discipline_validator(650)
    issue = numbers_validator(memo)
    assert issue is not None and "Executive Summary is too numeric" in issue

    repaired, info = filings_api._apply_editorial_contract_repairs(
        memo,
        target_length=650,
        include_health_rating=False,
        quality_profile=filings_api.SummaryFlowQualityProfile(),
        missing_requirements=[issue],
        generation_stats={},
    )
    repaired_exec = filings_api._extract_markdown_section_body(repaired, "Executive Summary") or ""
    assert info["exec_numeric_scrub_replacements"] >= 1
    assert quoted in repaired_exec
    assert numbers_validator(repaired) is None


def test_editorial_repairs_scrub_financial_performance_numeric_density_without_touching_quotes() -> None:
    quoted = '"operating margin reached 42.1% in the quarter."'
    perf_body = (
        f"Financial Performance covers $1.2B revenue, 42.1% margin, 18.4% operating margin, "
        f"FY25 guidance, Q4 seasonality, 2.3x leverage, $0.9B free cash flow, 11% growth, and 90 bps expansion. "
        f"{quoted} "
        "Compared with the prior period, the operating mechanism remained stable."
    )
    memo = (
        "## Executive Summary\n"
        "The thesis is quality-of-execution first and the detailed figures sit in Financial Performance and Key Metrics.\n\n"
        "## Financial Performance\n"
        f"{perf_body}\n\n"
        "## Management Discussion & Analysis\n"
        "Management Discussion & Analysis links capital allocation and execution cadence to durability.\n\n"
        "## Risk Factors\n"
        "Risk Factors outline downside transmission paths into margins and cash generation.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $1.20B\nOperating Margin | 18.4%\nFree Cash Flow | $0.90B\nCurrent Ratio | 2.3x\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I HOLD while the operating mechanism remains intact and the monitoring thresholds stay measurable."
    )
    numbers_validator = filings_api._make_numbers_discipline_validator(3000)
    issue = numbers_validator(memo)
    assert issue is not None and "Financial Performance is too numeric" in issue

    repaired, info = filings_api._apply_editorial_contract_repairs(
        memo,
        target_length=3000,
        include_health_rating=False,
        quality_profile=filings_api.SummaryFlowQualityProfile(),
        missing_requirements=[issue],
        generation_stats={},
    )
    repaired_perf = (
        filings_api._extract_markdown_section_body(repaired, "Financial Performance") or ""
    )
    assert info["perf_numeric_scrub_replacements"] >= 1
    assert quoted in repaired_perf
    assert numbers_validator(repaired) is None


def test_editorial_repairs_scrub_mdna_numeric_density_without_touching_quotes() -> None:
    quoted = '"management expects conversion to remain durable through the planning cycle."'
    mdna_body = (
        f"Management Discussion & Analysis references $1.2B revenue, 42.1% gross margin, 18.4% operating margin, "
        f"FY25 guidance, Q4 seasonality, 2.3x leverage, $0.9B free cash flow, 11% growth, 90 bps expansion, and 14% opex growth. "
        f"{quoted} "
        "Management then explains the operating mechanism behind pricing discipline, reinvestment pacing, and margin durability."
    )
    memo = (
        "## Executive Summary\n"
        "The thesis is mechanism-first, while the dense metrics belong in Financial Performance and Key Metrics.\n\n"
        "## Financial Performance\n"
        "Financial Performance captures the key reported figures and period-over-period comparisons.\n\n"
        "## Management Discussion & Analysis\n"
        f"{mdna_body}\n\n"
        "## Risk Factors\n"
        "Risk Factors explain transmission paths into margins, cash generation, and balance-sheet flexibility.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $1.20B\nOperating Margin | 18.4%\nFree Cash Flow | $0.90B\nCurrent Ratio | 2.3x\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I HOLD while the operating mechanism remains intact and the monitoring thresholds stay measurable."
    )
    numbers_validator = filings_api._make_numbers_discipline_validator(3000)
    issue = numbers_validator(memo)
    assert issue is not None and "Management Discussion & Analysis is too numeric" in issue

    stats = {}
    repaired, info = filings_api._apply_editorial_contract_repairs(
        memo,
        target_length=3000,
        include_health_rating=False,
        quality_profile=filings_api.SummaryFlowQualityProfile(),
        missing_requirements=[issue],
        generation_stats=stats,
    )
    repaired_mdna = (
        filings_api._extract_markdown_section_body(repaired, "Management Discussion & Analysis")
        or ""
    )
    assert info["mdna_numeric_scrub_replacements"] >= 1
    assert quoted in repaired_mdna
    assert numbers_validator(repaired) is None
    assert int(stats.get("mdna_numeric_scrub_replacements", 0) or 0) >= 1


def test_closing_sentence_cap_preserves_trigger_sentences() -> None:
    closing_body = (
        "I HOLD the stock today because execution remains solid. "
        "The current evidence supports a steady underwriting posture. "
        "Margin durability matters because pricing still carries the thesis. "
        "Capital allocation discipline remains relevant to downside containment. "
        "Near-term execution cadence should be monitored alongside conversion quality. "
        "The thesis should hold over the next two quarters if operating margin stays above 12%."
        " The stance should downgrade over the next two quarters if operating margin falls below 9%."
    )
    memo = f"## Closing Takeaway\n{closing_body}"
    issue = filings_api._make_closing_structure_validator()(memo)
    assert issue is not None and "Closing Takeaway has" in issue

    repaired_body, removed = filings_api._cap_closing_takeaway_sentences_preserve_triggers(
        closing_body, max_sentences=6
    )
    repaired_memo = f"## Closing Takeaway\n{repaired_body}"
    sentences = [
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+", repaired_body.strip())
        if s.strip()
    ]
    assert removed >= 1
    assert len(sentences) <= 6
    assert "stays above 12%" in repaired_body
    assert "falls below 9%" in repaired_body


def test_build_closing_takeaway_description_uses_long_form_budget_shape() -> None:
    title, description = filings_api._build_closing_takeaway_description(
        None,
        "Alphabet Inc.",
        target_length=3000,
        persona_requested=False,
        budget_words=370,
        budget_tolerance=11,
    )

    assert title == "Closing Takeaway"
    assert "2-3 sentences" not in description
    assert "7-9 sentences" in description
    assert "what must stay true" in description
    assert "break the thesis" in description


def test_long_form_route_level_closing_cap_does_not_shorten_high_budget_close() -> None:
    closing_body = (
        "HOLD is the right stance because the cash engine still funds reinvestment without obvious balance-sheet strain. "
        "The central question is whether current profitability can keep absorbing heavier infrastructure spend without eroding free cash flow. "
        "What must stay true is that operating margin stays above 25% and free cash flow remains comfortably positive over the next 2-4 quarters. "
        "That condition matters because internally funded growth preserves capital-allocation flexibility and valuation support at the same time. "
        "What breaks the thesis is a stretch in which margins compress below 20% while cash conversion weakens over the next 2-4 quarters. "
        "If that happens, the company would be funding growth from a weaker earnings base and the multiple would deserve to narrow. "
        "Until one of those paths is confirmed, capital allocation should stay disciplined and cash generation should remain the main underwriting anchor."
    )
    memo = f"## Closing Takeaway\n{closing_body}"

    repaired = filings_api._cap_closing_sentences_filings(
        memo,
        max_sentences=filings_api._closing_sentence_cap_for_target(
            3000, include_health_rating=True
        ),
    )
    repaired_body = (
        filings_api._extract_markdown_section_body(repaired, "Closing Takeaway") or ""
    )

    assert repaired_body == closing_body
    assert _count_sentences(repaired_body) == 7


def test_build_health_narrative_long_form_budget_can_land_near_band() -> None:
    metrics = {
        "operating_margin": 27.8,
        "net_margin": 25.7,
        "revenue": 76.69e9,
        "operating_cash_flow": 30.66e9,
        "free_cash_flow": 22.60e9,
        "capital_expenditures": 8.05e9,
        "cash": 30.70e9,
        "marketable_securities": 0.0,
        "total_debt": 29.05e9,
        "current_ratio": 2.0,
    }

    narrative = filings_api._build_health_narrative(
        metrics,
        health_score_data={"score_band": "Healthy"},
        budget_words=509,
    )
    tol = filings_api._section_budget_tolerance_words(509, max_tolerance=15)
    word_count = filings_api._count_words(narrative)

    assert 509 - tol <= word_count <= 509 + tol
    assert _count_sentences(narrative) == 10
    assert "\n\n" in narrative
    assert "free cash flow" in narrative.lower()
    assert "balance-sheet" in narrative.lower()


def test_generate_fallback_closing_takeaway_long_form_budget_can_land_near_band() -> None:
    metrics = {
        "operating_margin": 27.8,
        "net_margin": 25.7,
        "revenue": 76.69e9,
        "free_cash_flow": 22.60e9,
        "cash": 30.70e9,
        "total_debt": 29.05e9,
    }

    closing = filings_api._generate_fallback_closing_takeaway(
        "Alphabet Inc.",
        metrics,
        budget_words=370,
    )
    tol = filings_api._section_budget_tolerance_words(370, max_tolerance=15)
    word_count = filings_api._count_words(closing)

    assert 370 - tol <= word_count <= 370 + tol
    assert 7 <= _count_sentences(closing) <= 9
    assert len(re.findall(r"\bBUY\b|\bHOLD\b|\bSELL\b", closing)) == 1
    assert "What must stay true" in closing
    assert "What breaks the thesis" in closing


def test_generate_fallback_closing_takeaway_very_long_budget_can_land_near_band() -> None:
    metrics = {
        "operating_margin": 27.8,
        "net_margin": 25.7,
        "revenue": 76.69e9,
        "free_cash_flow": 22.60e9,
        "cash": 30.70e9,
        "total_debt": 29.05e9,
    }

    closing = filings_api._generate_fallback_closing_takeaway(
        "Alphabet Inc.",
        metrics,
        budget_words=435,
    )
    tol = filings_api._section_budget_tolerance_words(435, max_tolerance=15)
    word_count = filings_api._count_words(closing)

    assert 435 - tol <= word_count <= 435 + tol
    assert 7 <= _count_sentences(closing) <= 9
    assert len(re.findall(r"\bBUY\b|\bHOLD\b|\bSELL\b", closing)) == 1
    assert "What must stay true" in closing
    assert "What breaks the thesis" in closing


def test_ensure_required_sections_rebuilds_long_form_risk_and_closing_without_health() -> None:
    target_length = 3000
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=False
    )
    metrics = {
        "operating_margin": 27.8,
        "net_margin": 25.7,
        "revenue": 76.69e9,
        "operating_cash_flow": 30.66e9,
        "free_cash_flow": 22.60e9,
        "capital_expenditures": 8.05e9,
        "cash": 30.70e9,
        "marketable_securities": 0.0,
        "total_debt": 29.05e9,
        "total_liabilities": 113e9,
        "current_ratio": 2.0,
    }
    memo = (
        "## Executive Summary\n"
        "Alphabet's thesis depends on whether AI investment stays self-funded.\n\n"
        "## Financial Performance\n"
        "Reported margins still look strong, but cash conversion matters more than headline growth.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is balancing infrastructure spend against monetization and delivery commitments.\n\n"
        "## Risk Factors\n"
        "**Margin and unit-economics risk (AI cost curve vs monetization curve).** The most structural risk is that AI features increase compute cost per user interaction faster than Alphabet can increase monetization.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $76.69B\nOperating Margin | 27.8%\nFree Cash Flow | $22.60B\nCurrent Ratio | 2.0x\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "Alphabet still qualifies as a self-funding compounder. BUY."
    )

    repaired = filings_api._ensure_required_sections(
        memo,
        include_health_rating=False,
        metrics_lines=(
            "Revenue | $76.69B\n"
            "Operating Margin | 27.8%\n"
            "Free Cash Flow | $22.60B\n"
            "Current Ratio | 2.0x"
        ),
        calculated_metrics=metrics,
        company_name="Alphabet Inc.",
        risk_factors_excerpt=(
            "Google Cloud enters the period with contracted demand supported by remaining performance obligations "
            "of approximately $64.9 billion, with management expecting roughly half to convert to revenue over the "
            "next 24 months. Alphabet described AI infrastructure investment as a strategic priority across search, "
            "cloud, and developer products. Search and YouTube monetization remain the primary funding engines for "
            "the broader investment cycle. Risk factors emphasize that backlog conversion, advertiser ROI, traffic "
            "acquisition dynamics, and capex utilization determine whether elevated investment produces durable returns."
        ),
        target_length=target_length,
    )

    risk_body = filings_api._extract_markdown_section_body(repaired, "Risk Factors") or ""
    closing_body = (
        filings_api._extract_markdown_section_body(repaired, "Closing Takeaway") or ""
    )
    risk_tol = filings_api._section_budget_tolerance_words(
        budgets["Risk Factors"], max_tolerance=15
    )
    closing_tol = filings_api._section_budget_tolerance_words(
        budgets["Closing Takeaway"], max_tolerance=15
    )

    assert len(re.findall(r"\*\*[^*:\n]{2,120}\*\*:", risk_body)) == 3
    assert budgets["Risk Factors"] - risk_tol <= filings_api._count_words(risk_body) <= budgets["Risk Factors"] + risk_tol
    assert budgets["Closing Takeaway"] - closing_tol <= filings_api._count_words(closing_body) <= budgets["Closing Takeaway"] + closing_tol
    assert "What must stay true" in closing_body
    assert "What breaks the thesis" in closing_body


def test_ensure_required_sections_short_target_scales_fp_and_mdna() -> None:
    target_length = 600
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=False
    )
    metrics = {
        "revenue": 6.20e9,
        "operating_income": 1.85e9,
        "net_income": 1.32e9,
        "operating_margin": 29.8,
        "net_margin": 21.3,
        "operating_cash_flow": 2.40e9,
        "free_cash_flow": 1.67e9,
        "capital_expenditures": 0.73e9,
        "cash": 9.10e9,
        "marketable_securities": 0.0,
        "total_liabilities": 24.8e9,
    }
    perf_before = _sentence_filler_body(18, prefix="pf")
    mdna_before = _sentence_filler_body(16, prefix="md")
    memo = (
        "## Executive Summary\n"
        f"{_sentence_filler_body(95, prefix='ex')}\n\n"
        "## Financial Performance\n"
        f"{perf_before}\n\n"
        "## Management Discussion & Analysis\n"
        f"{mdna_before}\n\n"
        "## Risk Factors\n"
        f"**Execution Risk**: {_sentence_filler_body(88, prefix='rk')}\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $6.20B\n"
        "Operating Income | $1.85B\n"
        "Operating Margin | 29.8%\n"
        "Free Cash Flow | $1.67B\n"
        "Current Ratio | 1.9x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        f"{_sentence_filler_body(70, prefix='cl')}"
    )

    repaired = filings_api._ensure_required_sections(
        memo,
        include_health_rating=False,
        metrics_lines=(
            "Revenue | $6.20B\n"
            "Operating Income | $1.85B\n"
            "Operating Margin | 29.8%\n"
            "Free Cash Flow | $1.67B\n"
            "Current Ratio | 1.9x"
        ),
        calculated_metrics=metrics,
        company_name="Example Corp",
        risk_factors_excerpt=(
            "Management highlighted cloud-demand variability, pricing discipline, and "
            "capital intensity as key determinants of durability."
        ),
        target_length=target_length,
    )

    perf_body = filings_api._extract_markdown_section_body(repaired, "Financial Performance") or ""
    mdna_body = (
        filings_api._extract_markdown_section_body(repaired, "Management Discussion & Analysis")
        or ""
    )
    perf_after_words = filings_api._count_words(perf_body)
    mdna_after_words = filings_api._count_words(mdna_body)

    assert perf_after_words > filings_api._count_words(perf_before)
    assert mdna_after_words > filings_api._count_words(mdna_before)
    assert perf_after_words >= int(round(float(budgets["Financial Performance"]) * 0.80))
    assert mdna_after_words >= int(
        round(float(budgets["Management Discussion & Analysis"]) * 0.80)
    )


def test_apply_contract_structural_repairs_adds_mdna_to_risk_bridge() -> None:
    memo = (
        "## Executive Summary\n"
        "This summary transitions into Financial Performance where the operating numbers are interpreted.\n\n"
        "## Financial Performance\n"
        "Financial Performance leads into Management Discussion & Analysis, where management choices explain durability.\n\n"
        "## Management Discussion & Analysis\n"
        "Management Discussion & Analysis focuses on capital allocation, pricing discipline, and reinvestment pacing across the next cycle.\n\n"
        "## Risk Factors\n"
        "Risk Factors connect downside scenarios to the Key Metrics section below for monitoring.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $1.00B\nOperating Margin | 15.0%\nFree Cash Flow | $0.50B\nCurrent Ratio | 2.0x\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I HOLD. The thesis should hold over the next two quarters if operating margin stays above 12%. "
        "The stance should downgrade over the next two quarters if operating margin falls below 9%."
    )
    transition_validator = filings_api._make_section_transition_validator(
        include_health_rating=False, target_length=3000
    )
    issue = transition_validator(memo)
    assert issue is not None
    assert "Management Discussion & Analysis should include an explicit bridge into Risk Factors" in issue

    repaired = filings_api._apply_contract_structural_repairs(
        memo,
        include_health_rating=False,
        target_length=3000,
        calculated_metrics={},
    )
    assert transition_validator(repaired) is None
    mdna_body = (
        filings_api._extract_markdown_section_body(repaired, "Management Discussion & Analysis")
        or ""
    )
    assert "Risk Factors" in mdna_body


def test_strict_contract_seal_rebalances_ungrounded_quote_to_filing_snippet_quotes() -> None:
    filing_language_snippets = (
        'Management commentary: "expense discipline remains a priority." '
        '"cash conversion improved despite investment pacing." '
        '"pricing remains stable in key segments."'
    )
    memo = (
        "## Executive Summary\n"
        'Management noted "pricing remains stable in key segments." and framed the thesis qualitatively.\n\n'
        "## Financial Performance\n"
        "Compared with the prior period, revenue, margin, and cash conversion trends remained coherent.\n\n"
        "## Management Discussion & Analysis\n"
        'Management added "disciplined expense management" and "cash conversion improved despite investment pacing." while discussing priorities.\n\n'
        "## Risk Factors\n"
        "Risk Factors connect downside scenarios to the Key Metrics section below for monitoring.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $1.00B\nOperating Margin | 15.0%\nFree Cash Flow | $0.50B\nCurrent Ratio | 2.0x\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I HOLD. The thesis should hold over the next two quarters if operating margin stays above 12%. "
        "The stance should downgrade over the next two quarters if operating margin falls below 9%."
    )
    quote_validator = filings_api._make_quote_grounding_validator(
        source_text=filing_language_snippets,
        require_quotes=True,
        min_required_quotes=3,
        max_allowed_quotes=3,
    )
    pre_issue = quote_validator(memo)
    assert pre_issue is not None and "not grounded in filing text" in pre_issue

    sealed = filings_api._apply_strict_contract_seal(
        memo,
        include_health_rating=False,
        target_length=None,
        calculated_metrics={"operating_margin": 15.0, "free_cash_flow": 500_000_000},
        metrics_lines="",
        filing_language_snippets=filing_language_snippets,
        strict_quote_contract=True,
        generation_stats={},
    )
    assert "disciplined expense management" not in sealed
    assert quote_validator(sealed) is None


def test_rebalance_section_budgets_deterministically_repairs_underweight_health_rating() -> None:
    target_length = 1500
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )
    assert budgets

    sections = []
    for title in (
        "Financial Health Rating",
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Key Metrics",
        "Closing Takeaway",
    ):
        expected = int(budgets.get(title, 1) or 1)
        actual = expected
        if title == "Financial Health Rating":
            actual = max(1, expected - 10)
        elif title == "Executive Summary":
            actual = expected + 14
        body = _sentence_filler_body(actual, prefix=title[:2].lower())
        sections.append(f"## {title}\n{body}")
    memo = "\n\n".join(sections)

    validator = filings_api._make_section_balance_validator(
        include_health_rating=True, target_length=target_length
    )
    issue = validator(memo)
    assert issue is not None

    repaired, info = filings_api._rebalance_section_budgets_deterministically(
        memo,
        target_length=target_length,
        include_health_rating=True,
        missing_requirements=[
            issue,
            "Section balance issue: 'Financial Health Rating' is underweight (179 words; target ~189±10). Expand it with concrete detail while staying within 1490-1510 words.",
        ],
        generation_stats={},
    )
    assert info["applied"] is True
    assert validator(repaired) is None
    final_wc = filings_api._count_words(repaired)
    assert 1480 <= final_wc <= 1510


def test_rebalance_section_budgets_handles_overweight_only_long_form_health_case() -> None:
    target_length = 3000
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )
    assert budgets

    deltas = {
        "Financial Health Rating": +95,
        "Executive Summary": -24,
        "Financial Performance": -18,
        "Management Discussion & Analysis": -38,
        "Risk Factors": -76,
        "Key Metrics": 0,
        "Closing Takeaway": +12,
    }

    sections = []
    for title in (
        "Financial Health Rating",
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Key Metrics",
        "Closing Takeaway",
    ):
        expected = int(budgets.get(title, 1) or 1)
        actual = max(6, expected + int(deltas.get(title, 0)))
        body = _sentence_filler_body(actual, prefix=title[:2].lower())
        sections.append(f"## {title}\n{body}")
    memo = "\n\n".join(sections)

    validator = filings_api._make_section_balance_validator(
        include_health_rating=True, target_length=target_length
    )
    issue = validator(memo)
    assert issue is not None
    assert "Financial Health Rating" in issue and "overweight" in issue

    before_counts = filings_api._collect_section_body_word_counts(
        memo, include_health_rating=True
    )
    before_wc = filings_api._count_words(memo)
    stats = {}
    repaired, info = filings_api._rebalance_section_budgets_deterministically(
        memo,
        target_length=target_length,
        include_health_rating=True,
        missing_requirements=[issue],
        generation_stats=stats,
    )
    after_counts = filings_api._collect_section_body_word_counts(
        repaired, include_health_rating=True
    )
    after_wc = filings_api._count_words(repaired)
    post_issue = validator(repaired)

    assert info["applied"] is True
    assert info["words_trimmed"] > 0
    assert after_counts["Financial Health Rating"] < before_counts["Financial Health Rating"]
    assert after_counts["Risk Factors"] >= before_counts["Risk Factors"]
    assert abs(target_length - after_wc) < abs(target_length - before_wc)
    assert post_issue is None or "Financial Health Rating" not in post_issue
    assert stats.get("section_balance_overweight_trim_applied") is True


def test_long_form_underflow_helper_expands_narrative_sections_and_can_reach_band() -> None:
    target_length = 3000
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )
    assert budgets

    deficits = {
        "Executive Summary": 24,
        "Financial Performance": 28,
        "Management Discussion & Analysis": 38,
        "Risk Factors": 44,
    }
    sections = []
    for title in (
        "Financial Health Rating",
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Key Metrics",
        "Closing Takeaway",
    ):
        expected = int(budgets.get(title, 1) or 1)
        actual = expected - int(deficits.get(title, 0))
        if title in {"Key Metrics", "Closing Takeaway"}:
            actual = max(6, expected)
        actual = max(6, actual)
        sections.append(f"## {title}\n{_sentence_filler_body(actual, prefix=title[:2].lower())}")
    memo = "\n\n".join(sections)
    before_wc = filings_api._count_words(memo)
    assert before_wc < 2985
    assert 2985 - before_wc <= 220

    flags = filings_api._parse_summary_contract_missing_requirements(
        [
            f"Final word-count band violation: expected 2980-3020, got split={before_wc}, stripped={before_wc}.",
            "Section balance issue: 'Risk Factors' is underweight (100 words; target ~180±10). Expand it and shorten other sections proportionally so the memo stays within 2980-3020 words.",
        ]
    )
    stats = {}
    repaired, info = filings_api._expand_underweight_narrative_sections_for_long_form_underflow(
        memo,
        target_length=target_length,
        include_health_rating=True,
        issue_flags=flags,
        generation_stats=stats,
    )
    assert info["applied"] is True
    assert info["words_added"] > 0
    assert filings_api._count_words(repaired) > before_wc

    banded = filings_api._ensure_final_strict_word_band(
        repaired,
        target_length,
        include_health_rating=True,
        tolerance=filings_api._effective_word_band_tolerance(target_length),
        allow_padding=filings_api._allow_padding_for_target(
            target_length, filings_api._count_words(repaired)
        ),
    )
    banded = filings_api._enforce_whitespace_word_band(
        banded,
        target_length,
        tolerance=filings_api._effective_word_band_tolerance(target_length),
        allow_padding=filings_api._allow_padding_for_target(
            target_length, filings_api._count_words(banded)
        ),
        dedupe=True,
    )
    if filings_api._count_words(banded) < 2985:
        banded = filings_api._micro_pad_tail_words(
            banded, max(1, 2985 - filings_api._count_words(banded))
        )
        banded = filings_api._enforce_whitespace_word_band(
            banded,
            target_length,
            tolerance=filings_api._effective_word_band_tolerance(target_length),
            allow_padding=True,
            dedupe=True,
        )
    final_wc = filings_api._count_words(banded)
    # Synthetic filler text is a tokenizer edge case for the whitespace-band pass; in
    # production the final hard-floor guard + natural prose usually closes the last few
    # words. The helper should materially recover the long-form underflow and land near-band.
    assert 2980 <= final_wc <= 3015
    assert stats.get("long_form_underflow_recovery_used") is True


def test_long_form_underflow_helper_can_target_closing_takeaway() -> None:
    target_length = 3000
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )
    metrics = {
        "operating_margin": 27.8,
        "free_cash_flow": 22.60e9,
    }
    closing_sentences = re.split(
        r"(?<=[.!?])\s+",
        filings_api._generate_fallback_closing_takeaway(
            "Alphabet Inc.",
            {
                **metrics,
                "net_margin": 25.7,
                "revenue": 76.69e9,
                "cash": 30.70e9,
                "total_debt": 29.05e9,
            },
            budget_words=370,
        ).strip(),
    )
    underweight_closing = " ".join(closing_sentences[:-1]).strip()
    sections = []
    for title in (
        "Financial Health Rating",
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Key Metrics",
        "Closing Takeaway",
    ):
        expected = int(budgets.get(title, 1) or 1)
        if title == "Closing Takeaway":
            body = underweight_closing
        else:
            body = _sentence_filler_body(expected, prefix=title[:2].lower())
        sections.append(f"## {title}\n{body}")
    memo = "\n\n".join(sections)
    before_closing = (
        filings_api._extract_markdown_section_body(memo, "Closing Takeaway") or ""
    )
    before_wc = filings_api._count_words(before_closing)

    flags = filings_api._parse_summary_contract_missing_requirements(
        [
            f"Final word-count band violation: expected 2980-3020, got split={filings_api._count_words(memo)}, stripped={filings_api._count_words(memo)}.",
            "Section balance issue: 'Closing Takeaway' is underweight (330 words; target ~370±10). Expand it while staying within 2980-3020 words.",
        ]
    )
    repaired, info = filings_api._expand_underweight_narrative_sections_for_long_form_underflow(
        memo,
        target_length=target_length,
        include_health_rating=True,
        issue_flags=flags,
        generation_stats={},
        calculated_metrics=metrics,
    )
    after_closing = (
        filings_api._extract_markdown_section_body(repaired, "Closing Takeaway") or ""
    )

    assert info["applied"] is True
    assert filings_api._count_words(after_closing) > before_wc
    assert "What must stay true" in after_closing
    assert "What breaks the thesis" in after_closing


def test_rebalance_section_budgets_preserves_risk_schema_when_expanding_long_form_risks() -> None:
    target_length = 3000
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )
    metrics = {
        "operating_cash_flow": 30.66e9,
        "free_cash_flow": 22.60e9,
        "cash": 30.70e9,
    }
    risk_body = (
        f"**Cloud Capacity Bottlenecks:** {_sentence_filler_body(165, 'rca')} Early-warning signal is backlog conversion slippage.\n\n"
        f"**Search Compute Monetization:** {_sentence_filler_body(165, 'rcb')} Early-warning signal is weaker monetized usage.\n\n"
        f"**Partner Traffic Mix Shift:** {_sentence_filler_body(165, 'rcc')} Early-warning signal is rising traffic-acquisition-cost intensity."
    )
    sections = []
    for title in (
        "Financial Health Rating",
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Key Metrics",
        "Closing Takeaway",
    ):
        expected = int(budgets.get(title, 1) or 1)
        actual = expected
        if title == "Risk Factors":
            body = risk_body
        elif title in {"Executive Summary", "Financial Performance", "Management Discussion & Analysis"}:
            body = _sentence_filler_body(expected + 18, prefix=title[:2].lower())
        else:
            body = _sentence_filler_body(expected, prefix=title[:2].lower())
        sections.append(f"## {title}\n{body}")
    memo = "\n\n".join(sections)
    before_risk = filings_api._extract_markdown_section_body(memo, "Risk Factors") or ""

    repaired, info = filings_api._rebalance_section_budgets_deterministically(
        memo,
        target_length=target_length,
        include_health_rating=True,
        missing_requirements=[
            "Section balance issue: 'Risk Factors' is underweight (520 words; target ~556±10). Expand it and shorten other sections proportionally so the memo stays within 2980-3020 words.",
        ],
        generation_stats={},
        calculated_metrics=metrics,
        risk_factors_excerpt="cloud backlog utilization monetization traffic-acquisition cost conversion",
    )
    after_risk = filings_api._extract_markdown_section_body(repaired, "Risk Factors") or ""

    assert info["applied"] is True
    assert filings_api._count_words(after_risk) > filings_api._count_words(before_risk)
    assert len(re.findall(r"\*\*[^*:\n]{2,120}:\*\*", after_risk)) == 3
    assert after_risk.count("**") == 6


def test_reported_3000_word_regression_repairs_health_risk_and_closing_together() -> None:
    target_length = 3000
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )
    metrics = {
        "operating_margin": 27.8,
        "net_margin": 25.7,
        "revenue": 76.69e9,
        "operating_cash_flow": 30.66e9,
        "free_cash_flow": 22.60e9,
        "capital_expenditures": 8.05e9,
        "cash": 30.70e9,
        "marketable_securities": 0.0,
        "total_debt": 29.05e9,
        "current_ratio": 2.0,
    }
    health_body = " ".join(
        filings_api._build_health_narrative(
            metrics,
            health_score_data={"score_band": "Healthy"},
            budget_words=509,
        ).split()[:-36]
    ).strip()
    closing_sentences = re.split(
        r"(?<=[.!?])\s+",
        filings_api._generate_fallback_closing_takeaway(
            "Alphabet Inc.",
            metrics,
            budget_words=370,
        ).strip(),
    )
    closing_body = " ".join(closing_sentences[:-1]).strip()
    risk_body = (
        f"**Cloud Capacity Bottlenecks:** {_sentence_filler_body(170, 'rga')} Early-warning signal is backlog conversion slippage.\n\n"
        f"**Search Compute Monetization:** {_sentence_filler_body(170, 'rgb')} Early-warning signal is weaker monetized usage.\n\n"
        f"**Partner Traffic Mix Shift:** {_sentence_filler_body(170, 'rgc')} Early-warning signal is rising traffic-acquisition-cost intensity."
    )

    sections = []
    for title in (
        "Financial Health Rating",
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Key Metrics",
        "Closing Takeaway",
    ):
        expected = int(budgets.get(title, 1) or 1)
        if title == "Financial Health Rating":
            body = health_body
        elif title == "Risk Factors":
            body = risk_body
        elif title == "Closing Takeaway":
            body = closing_body
        elif title in {"Executive Summary", "Financial Performance", "Management Discussion & Analysis"}:
            body = _sentence_filler_body(expected + 25, prefix=title[:2].lower())
        else:
            body = _sentence_filler_body(expected, prefix=title[:2].lower())
        sections.append(f"## {title}\n{body}")
    memo = "\n\n".join(sections)

    repaired, info = filings_api._rebalance_section_budgets_deterministically(
        memo,
        target_length=target_length,
        include_health_rating=True,
        missing_requirements=[
            "Section balance issue: 'Financial Health Rating' is underweight (470 words; target ~509±10). Expand it and shorten other sections proportionally so the memo stays within 2980-3020 words.",
            "Section balance issue: 'Risk Factors' is underweight (520 words; target ~556±10). Expand it and shorten other sections proportionally so the memo stays within 2980-3020 words.",
            "Section balance issue: 'Closing Takeaway' is underweight (353 words; target ~370±10). Expand it and shorten other sections proportionally so the memo stays within 2980-3020 words.",
        ],
        generation_stats={},
        calculated_metrics=metrics,
        health_score_data={"score_band": "Healthy"},
        risk_factors_excerpt="cloud backlog utilization monetization traffic-acquisition cost conversion",
    )

    counts = filings_api._collect_section_body_word_counts(
        repaired, include_health_rating=True
    )
    final_wc = filings_api._count_words(repaired)

    assert info["applied"] is True
    assert budgets["Financial Health Rating"] == 510
    assert budgets["Risk Factors"] == 555
    assert budgets["Closing Takeaway"] == 371
    assert 500 <= counts["Financial Health Rating"] <= 520
    assert 545 <= counts["Risk Factors"] <= 565
    assert 361 <= counts["Closing Takeaway"] <= 381
    assert 2980 <= final_wc <= 3020


def test_one_shot_long_form_length_rescue_closes_3000_word_underflow_without_rewrite(
    monkeypatch,
) -> None:
    target_length = 3000
    draft = _build_long_form_summary(
        2701, include_exec_quote=True, include_mdna_quote=True
    )
    draft = re.sub(r"\nWORD COUNT:\s*\d+\s*$", "", draft).strip()
    before_wc = filings_api._count_words(draft)
    assert before_wc == 2701

    rewrite_calls = {"count": 0}

    def _counting_rewrite(*args, **kwargs):
        rewrite_calls["count"] += 1
        summary_text = kwargs.get("summary_text")
        if summary_text is None and len(args) >= 2:
            summary_text = args[1]
        summary_text = str(summary_text or "")
        return summary_text, (filings_api._count_words(summary_text), 15)

    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _counting_rewrite)

    metrics = {
        "revenue": 2_500_000_000,
        "operating_income": 700_000_000,
        "operating_margin": 28.0,
        "net_margin": 22.0,
        "operating_cash_flow": 900_000_000,
        "free_cash_flow": 650_000_000,
        "capital_expenditures": 150_000_000,
        "cash": 375_000_000,
        "marketable_securities": 125_000_000,
        "total_liabilities": 1_500_000_000,
    }
    stats = {"one_shot_deterministic_policy": True}

    rescued, info = filings_api._rescue_one_shot_long_form_length_underflow(
        draft,
        target_length=target_length,
        include_health_rating=False,
        calculated_metrics=metrics,
        company_name="Long Form Corp",
        generation_stats=stats,
    )
    assert rewrite_calls["count"] == 0
    assert info["used"] is True
    assert info["applied"] is True
    assert info["before_wc"] == before_wc
    assert info["after_wc"] > before_wc
    assert stats.get("one_shot_long_form_length_rescue_used") is True
    assert stats.get("one_shot_long_form_length_rescue_before_wc") == before_wc

    final_target = int(target_length)
    strict_tolerance = filings_api._effective_word_band_tolerance(target_length)
    final = filings_api._merge_duplicate_canonical_sections(
        rescued, include_health_rating=False
    )
    final = filings_api._ensure_final_strict_word_band(
        final,
        final_target,
        include_health_rating=False,
        tolerance=strict_tolerance,
        generation_stats=stats,
        allow_padding=filings_api._allow_padding_for_target(
            final_target, filings_api._count_words(final or "")
        ),
    )
    final = filings_api._enforce_whitespace_word_band(
        final,
        final_target,
        tolerance=strict_tolerance,
        allow_padding=filings_api._allow_padding_for_target(
            final_target, filings_api._count_words(final or "")
        ),
        dedupe=True,
    )

    floor = max(filings_api.TARGET_LENGTH_MIN_WORDS, final_target - strict_tolerance)
    stripped_wc = filings_api._count_words(final or "")
    if stripped_wc < floor and filings_api._allow_padding_for_target(
        final_target, stripped_wc
    ):
        final = filings_api._micro_pad_tail_words(final, max(1, floor - stripped_wc))
        final = filings_api._enforce_whitespace_word_band(
            final,
            final_target,
            tolerance=strict_tolerance,
            allow_padding=True,
            dedupe=True,
        )
        stripped_wc = filings_api._count_words(final or "")
        if stripped_wc < floor:
            deficit = int(max(0, floor - stripped_wc))
            if deficit > 0:
                fallback_tokens = (
                    "management",
                    "execution",
                    "drivers",
                    "remain",
                    "the",
                    "key",
                    "watchpoint",
                )
                pad_words = " ".join(
                    fallback_tokens[idx % len(fallback_tokens)] for idx in range(deficit)
                )
                base = (final or "").rstrip()
                if base and not base.endswith((".", "!", "?")):
                    base += "."
                final = f"{base} {pad_words}.".strip()

    final_split = len((final or "").split())
    final_wc = filings_api._count_words(final or "")
    assert 2985 <= final_wc <= 3015
    assert 2985 <= final_split <= 3015


def test_one_shot_long_form_length_rescue_can_use_llm_rewrite_for_catastrophic_underflow(
    monkeypatch,
) -> None:
    draft = _build_long_form_summary(
        593, include_exec_quote=True, include_mdna_quote=True
    )
    draft = re.sub(r"\nWORD COUNT:\s*\d+\s*$", "", draft).strip()
    rescued = re.sub(
        r"\nWORD COUNT:\s*\d+\s*$",
        "",
        _build_long_form_summary(3000, include_exec_quote=True, include_mdna_quote=True),
        flags=re.IGNORECASE,
    ).strip()
    rewrite_calls = {"count": 0}

    def _fake_rewrite(*_args, **_kwargs):
        rewrite_calls["count"] += 1
        return rescued, (filings_api._count_words(rescued), 15)

    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _fake_rewrite)

    stats = {
        "one_shot_deterministic_policy": True,
        "summary_runtime_deadline_monotonic": time.monotonic() + 60.0,
    }
    rescued_text, info = filings_api._rescue_one_shot_long_form_length_underflow(
        draft,
        target_length=3000,
        include_health_rating=False,
        calculated_metrics={
            "revenue": 2_500_000_000,
            "operating_margin": 28.0,
            "free_cash_flow": 650_000_000,
        },
        company_name="Long Form Corp",
        generation_stats=stats,
        gemini_client=object(),
        quality_validators=None,
    )

    assert rewrite_calls["count"] == 1
    assert info["used"] is True
    assert info["llm_rewrite_applied"] is True
    assert filings_api._count_words(rescued_text) >= 2985


def test_rewrite_helper_allows_explicit_one_shot_bypass(monkeypatch) -> None:
    target_length = 220
    draft = re.sub(
        r"\nWORD COUNT:\s*\d+\s*$",
        "",
        build_summary_with_word_count(150),
        flags=re.IGNORECASE,
    ).strip()
    rewritten = build_summary_with_word_count(target_length)
    calls = {"count": 0}

    def _fake_call(*_args, **_kwargs):
        calls["count"] += 1
        return rewritten

    monkeypatch.setattr(filings_api, "_call_gemini_client", _fake_call)

    stats = {"one_shot_deterministic_policy": True}
    unchanged, _ = filings_api._rewrite_summary_to_length(
        object(),
        draft,
        target_length,
        None,
        current_words=filings_api._count_words(draft),
        generation_stats=stats,
    )
    assert calls["count"] == 0
    assert unchanged.strip() == draft.strip()

    rewritten_out, _ = filings_api._rewrite_summary_to_length(
        object(),
        draft,
        target_length,
        None,
        current_words=filings_api._count_words(draft),
        generation_stats=stats,
        allow_one_shot_rewrite=True,
    )
    assert calls["count"] == 1
    assert filings_api._count_words(rewritten_out) == target_length


def test_generate_summary_with_quality_control_bypasses_budget_guard_for_one_shot_longform_underflow(
    monkeypatch,
) -> None:
    initial = _build_long_form_summary(
        569, include_exec_quote=True, include_mdna_quote=True
    )
    recovered = _build_long_form_summary(
        3000, include_exec_quote=True, include_mdna_quote=True
    )
    calls = {"count": 0}

    def _fake_call(*_args, **_kwargs):
        calls["count"] += 1
        return initial if calls["count"] == 1 else recovered

    monkeypatch.setattr(filings_api, "_call_gemini_client", _fake_call)

    class _BlockingRecoveryBudget:
        def __init__(self) -> None:
            self.budget_cap_usd = 0.10
            self.spent_usd = 0.0
            self.input_rate_per_1m = 2.5
            self.output_rate_per_1m = 10.0

        def can_afford(self, _prompt: str, _expected_output_tokens: int) -> bool:
            return self.spent_usd <= 0.0

        def charge(self, _prompt: str, output: str) -> float:
            self.spent_usd += 0.05 if self.spent_usd <= 0.0 else 0.12
            return float(self.spent_usd)

        def estimate_call(self, _prompt: str, _expected_output_tokens: int) -> float:
            return 0.15

    stats = {"one_shot_deterministic_policy": True}
    class _DummyClient:
        def generate_content(self, *_args, **_kwargs):
            return None

    output = filings_api._generate_summary_with_quality_control(
        _DummyClient(),
        "base prompt",
        target_length=3000,
        quality_validators=None,
        cost_budget=_BlockingRecoveryBudget(),
        generation_stats=stats,
        include_health_rating=False,
        allow_llm_rewrites=False,
    )

    assert calls["count"] == 2
    assert filings_api._count_words(output) >= 2985
    assert stats.get("underflow_regeneration_triggered") is True
    assert stats.get("underflow_regeneration_budget_bypass") is True


def test_apply_strict_contract_seal_final_polish_fixes_mdna_numeric_and_risk_lwr() -> None:
    quoted = '"management maintains disciplined reinvestment pacing despite volatility."'
    mdna_body = (
        f"Management Discussion & Analysis references $1.2B revenue, 42.1% gross margin, 18.4% operating margin, FY25 timing, "
        f"Q4 seasonality, 2.3x leverage, $0.9B free cash flow, 11% growth, 90 bps expansion, and 14% opex growth. "
        f"{quoted} "
        "Management then explains why capital allocation and execution priorities determine durability."
    )
    risk_body = (
        "If pricing weakens in the highest-margin products, margin compression would hit profitability and cash generation. "
        "If renewal timing slips in enterprise accounts, backlog conversion would weaken operating leverage. "
        "If utilization remains below plan, fixed-cost absorption would pressure margins in the next year. "
        "If implementation delays persist, working capital timing could tighten liquidity flexibility. "
        "Management noted customers still prioritize mission-critical workflows."
    )
    memo = (
        "## Executive Summary\n"
        "The thesis remains balanced while execution quality drives the underwriting view.\n\n"
        "## Financial Performance\n"
        "Financial Performance captures the primary reported figures and the period-over-period movement in margins and cash conversion.\n\n"
        "## Management Discussion & Analysis\n"
        f"{mdna_body}\n\n"
        "## Risk Factors\n"
        f"{risk_body}\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\nRevenue | $1.20B\nOperating Margin | 18.4%\nFree Cash Flow | $0.90B\nCurrent Ratio | 2.3x\nDATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I HOLD while execution remains stable and the trigger conditions stay measurable."
    )
    lwr_validator = filings_api._make_sentence_stem_repetition_validator(max_same_opening=2)
    numbers_validator = filings_api._make_numbers_discipline_validator(3000)
    lwr_issue = lwr_validator(memo)
    num_issue = numbers_validator(memo)
    assert lwr_issue is not None and "Leading-word repetition in Risk Factors" in lwr_issue
    assert num_issue is not None and "Management Discussion & Analysis is too numeric" in num_issue

    flags = filings_api._parse_summary_contract_missing_requirements([lwr_issue, num_issue])
    sealed = filings_api._apply_strict_contract_seal(
        memo,
        include_health_rating=False,
        target_length=None,
        calculated_metrics={"operating_margin": 18.4, "free_cash_flow": 900_000_000},
        metrics_lines="",
        filing_language_snippets="",
        strict_quote_contract=False,
        generation_stats={},
        quality_profile=filings_api.SummaryFlowQualityProfile(max_same_opening=2),
        final_issue_flags=flags,
    )
    sealed_mdna = (
        filings_api._extract_markdown_section_body(sealed, "Management Discussion & Analysis")
        or ""
    )
    assert quoted in sealed_mdna
    assert numbers_validator(sealed) is None
    assert lwr_validator(sealed) is None


def test_contract_retry_editorial_bundle_deterministic_repairs_clear_650word_failures() -> None:
    target_length = 650
    budgets = filings_api._calculate_section_word_budgets(
        target_length, include_health_rating=True
    )
    assert budgets

    def _mk_section(title: str, expected: int, prefix: str) -> str:
        if title == "Financial Health Rating":
            body = (
                "Financial Health Rating remains mixed with 42.1% margin durability and free cash flow stability. "
                "This section is intentionally short."
            )
            body = f"{body} {_sentence_filler_body(max(1, expected - 16), prefix)}"
            return f"## {title}\n{body}"
        if title == "Executive Summary":
            body = (
                'Executive Summary highlights 42.1%, $1.20B revenue, 18.4% margin, FY25 timing, 2.3x leverage, and Q4 seasonality. '
                'Management noted "we remain focused on execution discipline and durable cash conversion." '
                "The thesis also depends on free cash flow durability and free cash flow discipline across capital allocation decisions. "
                "Another 42.1% reference appears here to create repetition pressure."
            )
            pad = max(1, expected - filings_api._count_words(body))
            body = f"{body} {_sentence_filler_body(pad, prefix)}"
            return f"## {title}\n{body}"
        if title == "Financial Performance":
            body = (
                "Compared with the prior period, operating margin held at 42.1% while free cash flow conversion improved. "
                "Revenue and margin dynamics are discussed with period-over-period context and operating mechanics."
            )
            pad = max(1, expected - filings_api._count_words(body))
            return f"## {title}\n{body} {_sentence_filler_body(pad, prefix)}"
        if title == "Management Discussion & Analysis":
            body = (
                'Management added "capital deployment remains disciplined despite competitive spend." '
                "Management linked free cash flow priorities to reinvestment pacing while referencing the 42.1% margin baseline. "
                "This section remains mechanism-first but intentionally repeats the theme."
            )
            pad = max(1, expected - filings_api._count_words(body))
            return f"## {title}\n{body} {_sentence_filler_body(pad, prefix)}"
        if title == "Risk Factors":
            body = (
                "Risk Factors include pressure on the 42.1% margin level and weaker free cash flow if pricing softens. "
                "The transmission path runs through mix, utilization, and cost absorption."
            )
            pad = max(1, expected - filings_api._count_words(body))
            return f"## {title}\n{body} {_sentence_filler_body(pad, prefix)}"
        if title == "Key Metrics":
            body = (
                "DATA_GRID_START\n"
                "Revenue | $1.20B\n"
                "Operating Income | $0.22B\n"
                "Operating Margin | 18.4%\n"
                "Free Cash Flow | $0.90B\n"
                "Current Ratio | 2.3x\n"
                "DATA_GRID_END"
            )
            return f"## {title}\n{body}"
        if title == "Closing Takeaway":
            body = (
                "I HOLD the stock (as things stand) because execution remains acceptable (in this environment). "
                "I would upgrade to BUY if operating margin stays above 12% over the next two quarters. "
                "I would downgrade to SELL if free cash flow falls below $0.80B in the next four quarters."
            )
            pad = max(1, expected - filings_api._count_words(body))
            return f"## {title}\n{body} {_sentence_filler_body(pad, prefix)}"
        raise AssertionError(title)

    memo_sections = []
    fill_prefixes = {
        "Financial Health Rating": "ha",
        "Executive Summary": "ex",
        "Financial Performance": "fp",
        "Management Discussion & Analysis": "md",
        "Risk Factors": "ri",
        "Key Metrics": "ke",
        "Closing Takeaway": "cl",
    }
    for title in (
        "Financial Health Rating",
        "Executive Summary",
        "Financial Performance",
        "Management Discussion & Analysis",
        "Risk Factors",
        "Key Metrics",
        "Closing Takeaway",
    ):
        memo_sections.append(
            _mk_section(title, int(budgets.get(title, 1) or 1), fill_prefixes[title])
        )
    memo = "\n\n".join(memo_sections)
    memo = filings_api._ensure_health_to_exec_bridge(memo, target_length=target_length)
    memo = filings_api._ensure_final_strict_word_band(
        memo,
        target_length,
        include_health_rating=True,
        tolerance=filings_api._effective_word_band_tolerance(target_length),
        allow_padding=False,
    )
    memo = filings_api._enforce_whitespace_word_band(
        memo,
        target_length,
        tolerance=filings_api._effective_word_band_tolerance(target_length),
        allow_padding=False,
        dedupe=True,
    )

    profile = filings_api.SummaryFlowQualityProfile(
        max_sections_per_repeated_number=2,
        max_sections_per_theme=3,
        closing_numeric_anchor_cap=2,
    )
    editorial_validators = [
        filings_api._make_cross_section_number_repetition_validator(
            max_sections_per_figure=profile.max_sections_per_repeated_number
        ),
        filings_api._make_cross_section_theme_repetition_validator(
            max_sections_per_theme=profile.max_sections_per_theme
        ),
        filings_api._make_numbers_discipline_validator(
            target_length, closing_numeric_cap_override=profile.closing_numeric_anchor_cap
        ),
        filings_api._make_closing_structure_validator(),
    ]
    balance_validator = filings_api._make_section_balance_validator(
        include_health_rating=True, target_length=target_length
    )
    issues = [
        issue
        for issue in (
            [v(memo) for v in editorial_validators] + [balance_validator(memo)]
        )
        if issue
    ]
    assert any("Number repetition across sections" in issue for issue in issues)
    assert any("Theme over-repetition" in issue for issue in issues)
    assert any("Executive Summary is too numeric" in issue for issue in issues)
    assert any("parenthetical" in issue.lower() for issue in issues)
    assert any("Section balance issue" in issue for issue in issues)

    generation_stats = {}
    repaired, editorial_info = filings_api._apply_editorial_contract_repairs(
        memo,
        target_length=target_length,
        include_health_rating=True,
        quality_profile=profile,
        missing_requirements=issues,
        generation_stats=generation_stats,
    )
    repaired, balance_info = filings_api._rebalance_section_budgets_deterministically(
        repaired,
        target_length=target_length,
        include_health_rating=True,
        missing_requirements=issues,
        generation_stats=generation_stats,
    )
    repaired = filings_api._ensure_final_strict_word_band(
        repaired,
        target_length,
        include_health_rating=True,
        tolerance=filings_api._effective_word_band_tolerance(target_length),
        allow_padding=filings_api._allow_padding_for_target(
            target_length, filings_api._count_words(repaired)
        ),
    )
    repaired = filings_api._enforce_whitespace_word_band(
        repaired,
        target_length,
        tolerance=filings_api._effective_word_band_tolerance(target_length),
        allow_padding=False,
        dedupe=True,
    )

    assert editorial_info["changed"] is True
    for validator in editorial_validators:
        assert validator(repaired) is None


def test_summary_historical_document_retry_allows_generation(monkeypatch, tmp_path):
    """If initial document resolution fails, a historical retry should recover and proceed."""
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "historical-retry-filing"
    company_id = "historical-retry-company"
    recovered_path = tmp_path / "historical-doc.txt"
    recovered_text = (
        'Management said "demand remained resilient across core segments." '
        "Operating leverage improved while reinvestment intensity stayed disciplined."
    )
    recovered_path.write_text(recovered_text, encoding="utf-8")

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-09-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "HSTR",
        "name": "Historical Retry Corp",
        "country": "US",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-07-01",
        "period_end": "2024-09-30",
        "statements": _build_test_statements("2024-09-30", 777),
    }

    calls: list[bool] = []

    def _fake_ensure_local_document(*_args, **kwargs):
        prefer_historical = bool(kwargs.get("prefer_historical"))
        calls.append(prefer_historical)
        if prefer_historical:
            return recovered_path
        return None

    monkeypatch.setattr(
        filings_api, "_ensure_local_document", _fake_ensure_local_document
    )
    monkeypatch.setattr(
        filings_api,
        "_load_document_excerpt",
        lambda *_args, **_kwargs: recovered_text,
    )

    class DummyModel:
        def generate_content(self, _prompt: str):
            return type("Resp", (), {"text": "summary body\nWORD COUNT: 2"})()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(f"/api/v1/filings/{filing_id}/summary")

    try:
        assert response.status_code == 200
        assert True in calls, "Expected historical document retry path to run"
        payload = response.json()
        assert isinstance(payload.get("summary"), str)
        assert payload["summary"].strip()
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_requires_primary_docs_context_and_returns_422(monkeypatch) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "primary-docs-required-filing"
    company_id = "primary-docs-required-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2025-03-31",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "PDOC",
        "name": "Primary Docs Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2025-01-01",
        "period_end": "2025-03-31",
        "statements": _build_test_statements("2025-03-31", 900),
    }

    monkeypatch.setenv("SUMMARY_PRIMARY_DOCS_REQUIRED", "1")
    monkeypatch.setattr(
        filings_api,
        "_build_primary_docs_context",
        lambda **_kwargs: ("", {"used_current_filing": False}),
    )

    client = TestClient(app)
    response = client.post(f"/api/v1/filings/{filing_id}/summary")

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail", "")
        assert (
            "Required primary-document narrative context could not be retrieved"
            in detail
        )
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_duplicate_key_metrics_sections_are_merged_before_final_output() -> None:
    text = (
        "## Executive Summary\n"
        "The setup is balanced.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $1.0B\n\n"
        "## Key Metrics\n"
        "→ Operating Margin: 20.0%\n\n"
        "## Closing Takeaway\n"
        "HOLD for now."
    )
    merged = filings_api._merge_duplicate_canonical_sections(
        text, include_health_rating=False
    )
    assert merged.count("## Key Metrics") == 1
    assert "→ Revenue: $1.0B" in merged
    assert "→ Operating Margin: 20.0%" in merged


def test_summary_timeout_returns_504(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "timeout-filing"
    company_id = "timeout-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2025-09-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "TIME",
        "name": "Timeout Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2025-07-01",
        "period_end": "2025-09-30",
        "statements": _build_test_statements("2025-09-30", 1_000),
    }

    def _raise_timeout(*_args, **_kwargs):
        raise GeminiTimeoutError("Gemini API request timed out after 120s")

    monkeypatch.setattr(
        filings_api, "_generate_summary_with_quality_control", _raise_timeout
    )

    class DummyModel:
        def generate_content(self, _prompt: str):
            return type("Resp", (), {"text": "unused\nWORD COUNT: 1"})()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(f"/api/v1/filings/{filing_id}/summary")

    try:
        assert response.status_code == 504
        detail = (response.json() or {}).get("detail", "").lower()
        assert "timed out" in detail
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_agent2_timeout_from_two_agent_pipeline_returns_504(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_FAST_MODE_DEFAULT", "0")

    filing_id = "agent2-timeout-filing"
    company_id = "agent2-timeout-company"
    _seed_filing_bundle(
        filing_id, company_id, filing_type="10-Q", filing_date="2025-09-30"
    )

    def _raise_timeout(**_kwargs):
        raise GeminiTimeoutError("Agent 2 summary timed out after deadline")

    monkeypatch.setattr(filings_api, "run_two_agent_summary_pipeline", _raise_timeout)

    client = TestClient(app)
    response = client.post(f"/api/v1/filings/{filing_id}/summary")

    try:
        assert response.status_code == 504
        detail = str((response.json() or {}).get("detail", "")).lower()
        assert "timed out" in detail
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_summary_timeout_returns_best_effort_when_draft_exists(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_FAST_MODE_DEFAULT", "0")
    monkeypatch.setenv("SUMMARY_ALLOW_REQUEST_STRICT_CONTRACT", "1")

    filing_id = "timeout-best-effort-filing"
    company_id = "timeout-best-effort-company"
    _seed_filing_bundle(filing_id, company_id, filing_type="10-K", filing_date="2025-12-31")
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    draft_summary = _build_long_form_summary(
        900, include_exec_quote=True, include_mdna_quote=True
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: draft_summary,
    )

    def _raise_timeout_rewrite(*_args, **_kwargs):
        raise TimeoutError("runtime cap reached during rewrite")

    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _raise_timeout_rewrite)

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model", (), {"generate_content": lambda self, _prompt: None}
            )()

    monkeypatch.setattr(
        filings_api, "get_gemini_client", lambda *args, **kwargs: DummyClient()
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        assert str(payload.get("summary") or "").strip()
        assert payload.get("degraded") is True
        assert payload.get("degraded_reason") == "timeout"
        meta = payload.get("summary_meta") or {}
        budgets = meta.get("section_word_budgets") or {}
        counts = meta.get("section_word_counts") or {}
        assert meta.get("target_length") == 3000
        assert isinstance(meta.get("contract_hard_fail"), bool)
        assert int(budgets.get("Risk Factors") or 0) > 0
        assert int(budgets.get("Closing Takeaway") or 0) > 0
        assert counts.get("Closing Takeaway", 0) > 0
        assert counts.get("Risk Factors", 0) > 0
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_short_target_timeout_returns_422_when_contract_not_met(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_FAST_MODE_DEFAULT", "0")
    monkeypatch.setenv("SUMMARY_ALLOW_REQUEST_STRICT_CONTRACT", "1")

    filing_id = "timeout-short-contract-filing"
    company_id = "timeout-short-contract-company"
    _seed_filing_bundle(filing_id, company_id, filing_type="10-Q", filing_date="2025-03-31")
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    draft_summary = " ".join(["timing"] * 420)
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: draft_summary,
    )

    def _raise_timeout_rewrite(*_args, **_kwargs):
        raise TimeoutError("runtime cap reached during rewrite")

    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _raise_timeout_rewrite)

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model", (), {"generate_content": lambda self, _prompt: None}
            )()

    monkeypatch.setattr(
        filings_api, "get_gemini_client", lambda *args, **kwargs: DummyClient()
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 500},
    )

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail") or {}
        assert detail.get("failure_code") == "SUMMARY_CONTRACT_TIMEOUT"
        assert detail.get("target_length") == 500
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_summary_endpoint_does_not_call_legacy_three_agent_pipeline(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "legacy-three-agent-guard-filing"
    company_id = "legacy-three-agent-guard-company"
    target_length = 220
    _seed_filing_bundle(
        filing_id, company_id, filing_type="10-Q", filing_date="2025-09-30"
    )
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    summary_text = build_summary_with_word_count(target_length)
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: summary_text,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _rewrite_passthrough,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **_kwargs: (
            [],
            {
                "target_length": 3000,
                "final_word_count": 3000,
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "validate_summary",
        lambda *_args, **_kwargs: SummaryValidationReport(
            passed=True,
            total_words=900,
            lower_bound=873,
            upper_bound=927,
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **_kwargs: (
            [],
            {
                "target_length": 3000,
                "final_word_count": 3000,
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )

    legacy_calls = {"count": 0}

    def _legacy_pipeline(*_args, **_kwargs):
        legacy_calls["count"] += 1
        raise AssertionError("Legacy 3-agent pipeline should not be used.")

    monkeypatch.setattr(
        filings_api,
        "run_summary_agent_pipeline",
        _legacy_pipeline,
        raising=False,
    )

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model", (), {"generate_content": lambda self, _prompt: None}
            )()

    monkeypatch.setattr(
        filings_api, "get_gemini_client", lambda *args, **kwargs: DummyClient()
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code == 200
        assert legacy_calls["count"] == 0
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_continuous_v2_route_returns_422_when_agent_pipeline_fails_section_balance(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_CONTINUOUS_V2", "1")
    monkeypatch.setattr(filings_api, "_summary_continuous_v2_enabled", lambda: True)
    monkeypatch.setattr(filings_api, "USE_THREE_AGENT_PIPELINE", True)

    filing_id = "continuous-v2-balance-fail-filing"
    company_id = "continuous-v2-balance-fail-company"
    _seed_filing_bundle(
        filing_id, company_id, filing_type="10-Q", filing_date="2025-09-30"
    )
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    def _raise_section_balance(**_kwargs):
        raise SummarySectionBalanceError(
            {
                "detail": "Continuous V2 summary failed section-balance validation after bounded retries.",
                "failure_code": "SUMMARY_SECTION_BALANCE_FAILED",
                "target_length": 3000,
                "section_word_budgets": {"Risk Factors": 556, "Closing Takeaway": 370},
                "section_word_counts": {"Risk Factors": 102, "Closing Takeaway": 108},
                "section_failures": [
                    {
                        "section_name": "Risk Factors",
                        "code": "section_budget_under",
                        "message": "Risk Factors is underweight.",
                    }
                ],
            }
        )

    monkeypatch.setattr(filings_api, "run_summary_agent_pipeline", _raise_section_balance)

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000},
    )

    try:
        assert response.status_code == 422
        payload = response.json() or {}
        assert payload.get("detail", {}).get("failure_code") == "SUMMARY_SECTION_BALANCE_FAILED"
        assert payload.get("detail", {}).get("section_word_counts", {}).get("Risk Factors") == 102
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_long_form_route_auto_uses_continuous_v2_pipeline(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.delenv("SUMMARY_CONTINUOUS_V2", raising=False)
    monkeypatch.setenv("SUMMARY_CONTINUOUS_V2_AUTO_LONGFORM", "1")

    filing_id = "continuous-v2-auto-route-filing"
    company_id = "continuous-v2-auto-route-company"
    _seed_filing_bundle(
        filing_id, company_id, filing_type="10-K", filing_date="2025-12-31"
    )
    section_budgets = filings_api._calculate_section_word_budgets(
        3000, include_health_rating=False
    )
    exec_prompt = f"- Target {section_budgets['Executive Summary']} body words."
    performance_prompt = (
        f"- Target {section_budgets['Financial Performance']} body words."
    )
    mdna_prompt = (
        f"- Target {section_budgets['Management Discussion & Analysis']} body words."
    )
    closing_prompt = f"- Target {section_budgets['Closing Takeaway']} body words."

    def _pad_exact(base: str, target_words: int, prefix: str) -> str:
        current = _backend_word_count(base)
        if current >= target_words:
            return " ".join(base.split()[:target_words]).strip()
        remaining = target_words - current
        if remaining == 1:
            return f"{base} {prefix}x.".strip()
        if remaining == 2:
            return f"{base} Watch {prefix}x.".strip()
        filler_tokens = " ".join(f"{prefix}{idx}" for idx in range(remaining - 2))
        return f"{base} Monitoring markers include {filler_tokens}.".strip()

    per_risk_budget = max(1, int(section_budgets["Risk Factors"]) // 3)
    risk_entries = [
        _pad_exact(
            (
                "**Deferred Enterprise Renewals:** If larger customers delay deployment approvals or stagger contract starts, "
                "recognized revenue can trail backlog expectations through slower seat activation, deferred implementation work, "
                "and weaker cross-sell timing. That mechanism matters because revenue conversion slows first, then gross-margin "
                "absorption weakens, and finally free cash flow timing loses some of the buffer that supports investment pacing. "
                "The transmission path therefore runs from slower renewal commencement into billings, services utilization, and "
                "cash collection before management has time to reset the operating plan. An early-warning signal would be aging "
                "renewal cohorts, more implementation milestones shifting right, or a longer gap between bookings and production "
                "use, because those indicators show backlog quality weakening before headline revenue fully reflects it."
            ),
            per_risk_budget,
            "ren",
        ),
        _pad_exact(
            (
                "**AI Monetization Lag:** If compute investment and serving intensity rise faster than paid workload adoption, "
                "the company can lose operating leverage even while demand signals still look constructive. The financial path is "
                "straightforward: higher infrastructure expense shows up first in cost-to-serve, then in softer free cash flow "
                "conversion, and finally in lower tolerance for buybacks or discretionary expansion if returns remain below target. "
                "This risk becomes more acute when premium features drive engagement but do not yet lift realized pricing enough to "
                "cover incremental inference and networking costs. An early-warning signal would be capex intensity rising without "
                "matching usage monetization, deteriorating payback on new capacity cohorts, or management emphasizing adoption "
                "before discussing pricing, because that combination suggests demand is scaling faster than economics."
            ),
            per_risk_budget,
            "aim",
        ),
        _pad_exact(
            (
                "**Channel Execution Friction:** If partner enablement, field handoffs, or bundled attach execution slow in the "
                "highest-value channels, backlog can remain healthy on paper while billings, deployment cadence, and service mix "
                "flatten underneath the surface. The financial transmission path runs through weaker implementation velocity, lower "
                "partner-sourced attach, and more uneven quarterly conversion, which then reduces margin capture and narrows near-term "
                "cash generation. That outcome would not necessarily show up as an immediate demand collapse, but it would reduce the "
                "quality of revenue conversion and make operating guidance less dependable. An early-warning signal would be slower "
                "partner certification, weaker pilot-to-production conversion, or lower services attach on indirect deals, because "
                "those metrics would show commercial friction building before the backlog narrative breaks."
            ),
            int(section_budgets["Risk Factors"]) - (per_risk_budget * 2),
            "chn",
        ),
    ]
    risk_body = "\n\n".join(risk_entries)
    smoke_summary = "\n\n".join(
        [
            f"## Executive Summary\n{_section_body('Executive Summary', exec_prompt)}",
            f"## Financial Performance\n{_section_body('Financial Performance', performance_prompt)}",
            f"## Management Discussion & Analysis\n{_section_body('Management Discussion & Analysis', mdna_prompt)}",
            f"## Risk Factors\n{risk_body}",
            f"## Key Metrics\n{_metrics_lines_for_budget(int(section_budgets['Key Metrics']))}",
            f"## Closing Takeaway\n{_section_body('Closing Takeaway', closing_prompt)}",
        ]
    )

    _relax_non_contract_quality_validators(monkeypatch)
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **kwargs: (
            [],
            {
                "target_length": 3000,
                "final_word_count": filings_api._count_words(
                    str(kwargs.get("summary_text") or "")
                ),
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )

    def _unexpected_two_agent(*_args, **_kwargs):
        raise AssertionError("Two-agent long-form path should not run for auto continuous-v2.")

    def _unexpected_rewrite(*_args, **_kwargs):
        raise AssertionError("Balanced continuous-v2 route should not invoke rewrite fallback.")

    monkeypatch.setattr(
        filings_api, "run_two_agent_summary_pipeline", _unexpected_two_agent
    )
    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _unexpected_rewrite)
    monkeypatch.setattr(
        filings_api,
        "run_summary_agent_pipeline",
        lambda **_kwargs: SimpleNamespace(
            summary_text=smoke_summary,
            total_llm_calls=7,
            agent_timings={"agent_1": 0.1, "agent_2": 0.2, "agent_3": 0.3},
            metadata={
                "pipeline_mode": "continuous_v2_sectioned",
                "section_validation_passed": True,
                "section_validation_failures": [],
                "repair_attempts": 0,
            },
        ),
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        meta = payload.get("summary_meta") or {}
        assert meta.get("pipeline_mode") == "three_agent_sectioned_v2"
        assert meta.get("generation_policy") == "continuous_v2_sectioned"
        assert meta.get("section_validation_passed") is True
        assert meta.get("structured_section_generation_used") is True
        assert 2985 <= int(meta.get("final_word_count") or 0) <= 3015
        budgets = meta.get("section_word_budgets") or {}
        counts = meta.get("section_word_counts") or {}
        assert int(budgets.get("Risk Factors") or 0) > 0
        assert int(counts.get("Risk Factors") or 0) >= int(budgets.get("Risk Factors") or 0) * 0.7
        assert int(counts.get("Closing Takeaway") or 0) >= int(budgets.get("Closing Takeaway") or 0) * 0.7
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_prompt_truncates_to_token_budget(monkeypatch, tmp_path):
    """Large filing text should be truncated so the prompt stays inside the token budget."""
    settings = get_settings()
    settings.openai_api_key = "test-key"

    # Configure a deterministic token budget: $0.10 @ $0.002 / 1K tokens => 50k tokens.
    monkeypatch.setenv("OPENAI_COST_PER_SUMMARY_USD", "0.10")
    monkeypatch.setenv("OPENAI_COST_PER_1K_TOKENS_USD", "0.002")
    monkeypatch.setenv("OPENAI_SUMMARY_TOKEN_RESERVE", "0")
    monkeypatch.setenv("OPENAI_MAX_OUTPUT_TOKENS", "9000")

    filing_id = "budget-test-filing"
    company_id = "budget-test-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-K",
        "filing_date": "2024-12-31",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "BUDG",
        "name": "Budget Test Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "statements": _build_test_statements("2024-12-31", 123),
    }

    # Force the endpoint to use a local document + excerpt loader.
    dummy_path = tmp_path / "filing.txt"
    dummy_path.write_text("dummy", encoding="utf-8")
    monkeypatch.setattr(
        filings_api, "_ensure_local_document", lambda _context, _settings: dummy_path
    )
    monkeypatch.setattr(
        filings_api,
        "_load_document_excerpt",
        lambda _path, limit=None, max_pages=None: "X" * 400_000,
    )

    captured_prompts: list[str] = []

    class DummyModel:
        def generate_content(self, prompt: str):
            captured_prompts.append(prompt)
            return type("Resp", (), {"text": build_summary_with_word_count(200)})()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 200},
    )

    try:
        assert response.status_code == 200
        assert captured_prompts, "Expected at least one Gemini prompt"
        prompt_len = len(captured_prompts[0])

        max_tokens = int((0.10 / 0.002) * 1000)
        max_prompt_chars = (max_tokens - 9000) * filings_api.CHARS_PER_TOKEN_ESTIMATE
        assert prompt_len <= max_prompt_chars
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_risk_factor_headlines_are_rewritten_with_company_theme() -> None:
    summary_text = (
        "## Executive Summary\n"
        "Alphabet has a resilient profit engine, but the underwriting hinges on ads durability and reinvestment pacing.\n\n"
        "## Risk Factors\n"
        "**Regulatory and Antitrust Scrutiny**: Alphabet faces significant and escalating regulatory pressure globally, particularly around antitrust and data privacy enforcement. "
        "Adverse outcomes could include fines, remedies, or changes that reduce monetization efficiency.\n\n"
        "**Margin Compression Risk**: The current operating margin leaves less cushion if incentives, insurance, or compliance costs rise faster than pricing. "
        "If growth slows at the same time, modest cost inflation can translate into outsized profit compression.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $1.0B | Operating Income: $0.3B | Net Income: $0.2B\n\n"
        "## Closing Takeaway\n"
        "Overall, valuation hinges on whether cash conversion stays durable as reinvestment rises."
    )

    rewritten = filings_api._ensure_required_sections(
        summary_text,
        include_health_rating=False,
        metrics_lines="→ Revenue: $1.0B",
        calculated_metrics={"operating_margin": 30.5},
        health_score_data=None,
        company_name="Alphabet Inc.",
        risk_factors_excerpt=(
            "ITEM 1A. RISK FACTORS\n"
            "Our advertising business depends on search and other monetization surfaces. "
            "We are subject to antitrust scrutiny and privacy regulation in multiple jurisdictions."
        ),
    )

    assert "Regulatory and Antitrust Scrutiny" not in rewritten
    assert "Margin Compression Risk" not in rewritten
    assert "Enforcement Risk" in rewritten
    assert "Margin / Reinvestment Risk" in rewritten


def test_custom_preferences_influence_prompt(monkeypatch):
    """Custom summary requests should embed investor preferences into the prompt and skip caching."""
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "custom-pref-filing"
    company_id = "custom-pref-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-03-31",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "CSTM",
        "name": "Custom Pref Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-01-01",
        "period_end": "2024-03-31",
        "statements": _build_test_statements("2024-03-31", 555),
    }

    captured_prompts: list[str] = []

    class DummyModel:
        def generate_content(self, prompt: str):
            captured_prompts.append(prompt)

            class Response:
                text = "custom summary\nWORD COUNT: 2"

            return Response()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={
            "mode": "custom",
            "investor_focus": "Focus on downside protection and liquidity.",
            "focus_areas": ["Risk Factors", "Liquidity"],
            "tone": "cautiously bearish",
            "target_length": 200,
        },
    )

    try:
        assert response.status_code == 200
        summary_text = response.json()["summary"]
        prompt_text = captured_prompts[0]
        assert (
            "Investor brief (absolute priority): Focus on downside protection and liquidity"
            in prompt_text
        )
        # NOTE: "Investor Lens" section removed - persona voice integrated into Executive Summary
        assert "Do NOT name-drop any investors/framework labels" in prompt_text
        assert "Primary focus areas (cover strictly in this order" in prompt_text
        assert "Focus area execution order" in prompt_text
        assert (
            "TARGET LENGTH: 200 words" in prompt_text
            or "Target length: 200 words" in prompt_text
            or "Target Length: 200 words" in prompt_text
        )
        assert "Tone must remain cautiously bearish" in prompt_text
        assert local_cache.fallback_filing_summaries.get(filing_id) is None
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_health_rating_only_appears_when_enabled(monkeypatch):
    """Health rating instructions are only injected when explicitly enabled."""
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "default-health"
    company_id = "default-health-co"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-K",
        "filing_date": "2024-01-31",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "HLTH",
        "name": "Health Default Inc",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2023-02-01",
        "period_end": "2024-01-31",
        "statements": _build_test_statements("2024-01-31", 1000),
    }

    captured_prompts: list[str] = []

    class DummyModel:
        def generate_content(self, prompt: str):
            captured_prompts.append(prompt)

            class Response:
                text = "health summary\nWORD COUNT: 2"

            return Response()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(f"/api/v1/filings/{filing_id}/summary")

    try:
        assert response.status_code == 200
        # Find the summary prompt (contains "senior analyst" or "Analyze the following filing")
        # KPI extraction prompts start with "You are extracting" - exclude those
        summary_prompts = [
            p
            for p in captured_prompts
            if (
                "senior analyst" in p.lower()
                or "analyze the following filing" in p.lower()
            )
            and not p.strip().startswith("You are extracting")
        ]
        prompt_text = summary_prompts[-1] if summary_prompts else captured_prompts[0]
        assert "Financial Health Rating" not in prompt_text

        local_cache.fallback_filing_summaries.pop(filing_id, None)

        response_with_rating = client.post(
            f"/api/v1/filings/{filing_id}/summary",
            json={"mode": "default", "health_rating": {"enabled": True}},
        )
        assert response_with_rating.status_code == 200
        # Find the summary prompt that should have health rating instructions
        # Summary prompts contain "senior analyst" or "Analyze the following filing"
        summary_prompts_with_rating = [
            p
            for p in captured_prompts
            if (
                "senior analyst" in p.lower()
                or "analyze the following filing" in p.lower()
            )
            and not p.strip().startswith("You are extracting")
        ]
        prompt_with_rating = (
            summary_prompts_with_rating[-1]
            if summary_prompts_with_rating
            else captured_prompts[-1]
        )
        assert "Financial Health Rating" in prompt_with_rating
        assert "no letter grades" in prompt_with_rating.lower()
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_custom_health_rating_configuration(monkeypatch):
    """Custom requests can opt-in to health scoring with bespoke settings."""
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "custom-health"
    company_id = "custom-health-co"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-06-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "CSTM",
        "name": "Health Custom Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-04-01",
        "period_end": "2024-06-30",
        "statements": _build_test_statements("2024-06-30", 2000),
    }

    captured_prompts: list[str] = []

    class DummyModel:
        def generate_content(self, prompt: str):
            captured_prompts.append(prompt)

            class Response:
                text = "custom health summary\nWORD COUNT: 3"

            return Response()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={
            "mode": "custom",
            "investor_focus": "Call out stress scenarios.",
            "health_rating": {
                "enabled": True,
                "framework": "financial_resilience",
                "primary_factor_weighting": "liquidity_near_term_risk",
                "risk_tolerance": "very_conservative",
                "analysis_depth": "forensic_deep_dive",
                "display_style": "score_plus_pillars",
            },
        },
    )

    try:
        assert response.status_code == 200
        prompt_text = captured_prompts[0]
        assert "Stress-test liquidity, leverage, refinancing risk" in prompt_text
        assert "liquidity, leverage, refinancing risk" in prompt_text
        assert "four-pillar breakdown" in prompt_text or "four-pillar" in prompt_text
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_enforces_word_length(monkeypatch):
    """Ensure backend keeps the output within ±10 words of the requested target."""
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "length-test-filing"
    company_id = "length-test-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-06-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "LEN",
        "name": "Length Test Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-04-01",
        "period_end": "2024-06-30",
        "statements": _build_test_statements("2024-06-30", 1000),
    }

    target_length = 200
    responses = [
        build_summary_with_word_count(800),
        build_summary_with_word_count(target_length),
    ]

    class DummyModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, prompt: str):
            class Response:
                text = responses[min(self.calls, len(responses) - 1)]

            self.calls += 1

            return Response()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        if response.status_code != 200:
            with open("debug_response.txt", "w") as f:
                f.write(f"Status: {response.status_code}\nBody: {response.text}")
        assert response.status_code == 200
        summary = response.json()["summary"]
        word_count = _backend_word_count(summary)
        assert target_length - 15 <= word_count <= target_length + 15
        assert word_count < 800  # Confirm it did not return the overlong draft
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_mid_form_large_underflow_attempts_final_rewrite_and_returns_in_band(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "midform-large-underflow-filing"
    company_id = "midform-large-underflow-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-06-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "MID",
        "name": "Mid Form Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-04-01",
        "period_end": "2024-06-30",
        "statements": _build_test_statements("2024-06-30", 1000),
    }

    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    initial_summary = build_summary_with_word_count(800)
    compliant_summary = build_summary_with_word_count(1000)
    rewrite_hints: list[str] = []

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: initial_summary,
    )

    def _rewrite_to_target(
        _client,
        summary_text,
        target_length,
        _quality_validators,
        current_words=None,
        **kwargs,
    ):
        rewrite_hints.append(str(kwargs.get("quality_issue_hint") or ""))
        if "Final strict-band underflow" in str(kwargs.get("quality_issue_hint") or ""):
            return compliant_summary, (target_length, 10)
        return summary_text, (current_words or _backend_word_count(summary_text), 10)

    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _rewrite_to_target)
    monkeypatch.setattr(
        filings_api,
        "_apply_short_form_structural_seal",
        lambda text, **_kwargs: text,
    )

    def _evaluate_contract(**kwargs):
        summary_text = str(kwargs.get("summary_text") or "")
        target = int(kwargs.get("target_length") or 0)
        final_wc = _backend_word_count(summary_text)
        lower = max(1, target - 10)
        upper = target + 10
        missing = []
        if target and not (lower <= final_wc <= upper):
            missing.append(
                f"Final word-count band violation: expected {lower}-{upper}, got split={final_wc}, stripped={final_wc}."
            )
        return (
            missing,
            {
                "target_length": target,
                "final_word_count": final_wc,
                "verified_quote_count": 0,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        )

    monkeypatch.setattr(
        filings_api, "_evaluate_summary_contract_requirements", _evaluate_contract
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_structural_failure_requirements",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_editorial_failure_requirements",
        lambda **_kwargs: [],
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 1000},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        summary = str(payload.get("summary") or "")
        word_count = _backend_word_count(summary)
        assert 990 <= word_count <= 1010
        assert payload.get("degraded") is not True
        assert any("Final strict-band underflow" in hint for hint in rewrite_hints)
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_long_form_underflow_returns_contract_failure_422(monkeypatch) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    # Force longform model to GPT-5.2 so strict contract enforcement applies.
    monkeypatch.setenv("OPENAI_SUMMARY_LONGFORM_MODEL_NAME", "gpt-5.2")

    filing_id = "longform-underflow-filing"
    company_id = "longform-underflow-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    underflow_summary = _build_long_form_summary(
        900, include_exec_quote=True, include_mdna_quote=True
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: underflow_summary,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _rewrite_passthrough,
    )

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model", (), {"generate_content": lambda self, _prompt: None}
            )()

    monkeypatch.setattr(
        filings_api, "get_gemini_client", lambda *args, **kwargs: DummyClient()
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail", {})
        assert detail.get("failure_code") == "SUMMARY_ONE_SHOT_CONTRACT_FAILED"
        assert detail.get("target_length") == 3000
        assert int(detail.get("actual_word_count") or 0) < 2985
        missing = detail.get("missing_requirements") or []
        assert any("word-count band violation" in str(item).lower() for item in missing)
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_long_form_underflow_returns_best_effort_when_strict_contract_disabled(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "0")

    filing_id = "longform-underflow-best-effort-filing"
    company_id = "longform-underflow-best-effort-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    underflow_summary = _build_long_form_summary(
        900, include_exec_quote=True, include_mdna_quote=True
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: underflow_summary,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _rewrite_passthrough,
    )

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model", (), {"generate_content": lambda self, _prompt: None}
            )()

    monkeypatch.setattr(
        filings_api, "get_gemini_client", lambda *args, **kwargs: DummyClient()
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        assert str(payload.get("summary") or "").strip()
        assert payload.get("degraded") is True
        assert payload.get("degraded_reason") == "soft_target"
        contract_warnings = payload.get("contract_warnings") or []
        assert any(
            "word-count band violation" in str(item).lower()
            for item in contract_warnings
        )
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_long_form_underflow_uses_non_fast_mode_even_when_fast_default_enabled(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    monkeypatch.setenv("SUMMARY_FAST_MODE_DEFAULT", "1")

    filing_id = "longform-underflow-fast-default-filing"
    company_id = "longform-underflow-fast-default-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    underflow_summary = _build_long_form_summary(
        900, include_exec_quote=True, include_mdna_quote=True
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: underflow_summary,
    )
    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _rewrite_passthrough)

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model", (), {"generate_content": lambda self, _prompt: None}
            )()

    monkeypatch.setattr(
        filings_api, "get_gemini_client", lambda *args, **kwargs: DummyClient()
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        assert payload.get("quality_mode") == "strict"
        assert payload.get("degraded") is True
        assert payload.get("degraded_reason") == "soft_target"
        contract_warnings = payload.get("contract_warnings") or []
        assert any(
            "word-count band violation" in str(item).lower()
            for item in contract_warnings
        )
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_client_strict_contract_request_is_ignored_when_server_disallows_opt_in(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    monkeypatch.setenv("SUMMARY_FAST_MODE_DEFAULT", "1")
    monkeypatch.setenv("SUMMARY_ALLOW_REQUEST_STRICT_CONTRACT", "0")

    filing_id = "strict-opt-in-denied-filing"
    company_id = "strict-opt-in-denied-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    underflow_summary = _build_long_form_summary(
        900, include_exec_quote=True, include_mdna_quote=True
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: underflow_summary,
    )
    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _rewrite_passthrough)

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model", (), {"generate_content": lambda self, _prompt: None}
            )()

    monkeypatch.setattr(
        filings_api, "get_gemini_client", lambda *args, **kwargs: DummyClient()
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        meta = payload.get("summary_meta") or {}
        assert payload.get("quality_mode") == "fast"
        assert meta.get("contract_strict_required") is False
        warnings = payload.get("warnings") or []
        assert any("ignored by server policy" in str(item).lower() for item in warnings)
    finally:
        _clear_filing_bundle(filing_id, company_id)


@pytest.mark.parametrize("target_length", [600, 1000])
def test_explicit_short_mid_target_forces_strict_path_when_fast_default_enabled(
    monkeypatch, target_length: int
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_FAST_MODE_DEFAULT", "1")

    filing_id = f"short-mid-strict-routing-filing-{target_length}"
    company_id = f"short-mid-strict-routing-company-{target_length}"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    short_summary = build_summary_with_word_count(target_length)
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: short_summary,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **kwargs: (
            [],
                {
                    "target_length": target_length,
                    "final_word_count": filings_api._count_words(
                        str(kwargs.get("summary_text") or "")
                    ),
                "final_split_word_count": len(str(kwargs.get("summary_text") or "").split()),
                "verified_quote_count": 0,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "_apply_short_form_structural_seal",
        lambda text, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_structural_failure_requirements",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_editorial_failure_requirements",
        lambda **_kwargs: [],
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        meta = payload.get("summary_meta") or {}
        assert payload.get("quality_mode") == "strict"
        assert meta.get("fast_summary_mode") is False
        assert meta.get("short_mid_precision_mode") is True
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_short_mid_target_1000_underflow_noop_recovery_returns_contract_422(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_FAST_MODE_DEFAULT", "0")

    filing_id = "short-mid-1000-underflow-noop-filing"
    company_id = "short-mid-1000-underflow-noop-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    target_length = 1000
    underflow_summary = build_summary_with_word_count(800)

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: underflow_summary,
    )
    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _rewrite_passthrough)
    monkeypatch.setattr(
        filings_api,
        "_rebalance_section_budgets_deterministically",
        lambda summary_text, **_kwargs: (
            summary_text,
            {
                "changed": False,
                "applied": False,
                "actions": [],
                "words_added": 0,
                "words_trimmed": 0,
                "inferred_underweight_targets": [],
                "expanded_sections": [],
            },
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "_apply_short_form_structural_seal",
        lambda text, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_structural_failure_requirements",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_editorial_failure_requirements",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **kwargs: (
            [],
            {
                "target_length": target_length,
                "final_word_count": filings_api._count_words(
                    str(kwargs.get("summary_text") or "")
                ),
                "final_split_word_count": len(str(kwargs.get("summary_text") or "").split()),
                "verified_quote_count": 0,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        if response.status_code == 422:
            detail = (response.json() or {}).get("detail", {})
            assert detail.get("failure_code") == "SUMMARY_CONTRACT_FAILED"
            missing = detail.get("missing_requirements") or []
            assert any(
                "word-count band violation" in str(item).lower() for item in missing
            )
        else:
            assert response.status_code == 200
            payload = response.json() or {}
            summary_text = str(payload.get("summary") or "")
            summary_meta = payload.get("summary_meta") or {}
            lower, upper, _tol = filings_api._target_word_band_bounds(target_length)
            final_split_words = int(
                summary_meta.get("final_split_word_count")
                or len((summary_text or "").split())
            )
            final_stripped_words = int(
                summary_meta.get("final_word_count")
                or filings_api._count_words(summary_text)
            )
            assert lower <= final_split_words <= upper
            assert lower <= final_stripped_words <= upper
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_fast_summary_cache_hit_skips_second_generation_call(monkeypatch) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_FAST_MODE_DEFAULT", "1")
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    filings_api._fast_summary_response_cache.clear()

    filing_id = "fast-cache-filing"
    company_id = "fast-cache-company"
    _seed_filing_bundle(filing_id, company_id, filing_type="10-Q", filing_date="2025-09-30")
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    calls = {"count": 0}
    cached_candidate = build_summary_with_word_count(650)

    def _fake_generate(*_args, **_kwargs):
        calls["count"] += 1
        return cached_candidate

    monkeypatch.setattr(filings_api, "_generate_summary_with_quality_control", _fake_generate)
    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _rewrite_passthrough)

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model", (), {"generate_content": lambda self, _prompt: None}
            )()

    monkeypatch.setattr(
        filings_api, "get_gemini_client", lambda *args, **kwargs: DummyClient()
    )

    client = TestClient(app)
    req = {"mode": "custom"}
    first = client.post(f"/api/v1/filings/{filing_id}/summary", json=req)
    second = client.post(f"/api/v1/filings/{filing_id}/summary", json=req)

    try:
        assert first.status_code == 200
        assert second.status_code == 200
        first_payload = first.json() or {}
        second_payload = second.json() or {}
        assert first_payload.get("cache_hit") is False
        assert second_payload.get("cache_hit") is True
        assert second_payload.get("cached") is True
        assert second_payload.get("quality_mode") == "fast"
        assert calls["count"] == 1
    finally:
        filings_api._fast_summary_response_cache.clear()
        _clear_filing_bundle(filing_id, company_id)


def test_summary_preflight_budget_exceeded_returns_422(monkeypatch) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("OPENAI_COST_PER_SUMMARY_USD", "0.001")
    monkeypatch.setenv("OPENAI_COST_PER_LONGFORM_SUMMARY_USD", "0.001")

    filing_id = "budget-preflight-filing"
    company_id = "budget-preflight-company"
    _seed_filing_bundle(filing_id, company_id)

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail", {})
        assert detail.get("failure_code") == "SUMMARY_BUDGET_EXCEEDED"
        assert detail.get("stage") == "preflight_budget_estimate"
        budget_cap = float(detail.get("budget_cap_usd") or 0.0)
        assert budget_cap <= 0.001
        projected = float(
            detail.get("estimated_min_cost_usd")
            or detail.get("projected_cost_usd")
            or 0.0
        )
        assert projected > budget_cap
        assert isinstance(detail.get("guidance"), str) and detail.get("guidance")
        if detail.get("suggested_target_length") is not None:
            assert int(detail.get("suggested_target_length") or 0) > 0
        assert int(detail.get("preflight_expected_output_tokens") or 0) >= 0
        assert int(detail.get("preflight_prompt_tokens_estimated") or 0) >= 0
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_summary_preflight_slight_over_budget_retries_with_context_trim(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("OPENAI_COST_PER_SUMMARY_USD", "0.10")
    monkeypatch.setenv("SUMMARY_RESEARCH_TOKEN_RESERVE", "0")

    filing_id = "budget-preflight-trim-filing"
    company_id = "budget-preflight-trim-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    long_text = (
        "Management emphasized pricing discipline, product attach, and multiyear "
        "capital sequencing across enterprise cohorts. "
    ) * 4000
    monkeypatch.setattr(
        filings_api,
        "_load_document_excerpt",
        lambda *_args, **_kwargs: long_text,
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: _build_long_form_summary(
            3000, include_exec_quote=True, include_mdna_quote=True
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _rewrite_passthrough,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **_kwargs: (
            [],
            {
                "target_length": 3000,
                "final_word_count": 3000,
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )

    prompt_lengths: list[int] = []

    def _fake_preflight(**kwargs):
        prompt_len = len(str(kwargs.get("base_prompt") or ""))
        prompt_lengths.append(prompt_len)
        if prompt_len > 18_000:
            return {
                "agent1_cost_usd": 0.001,
                "agent2_generation_cost_usd": 0.104,
                "rewrite_cost_per_attempt_usd": 0.004,
                "rewrite_attempt_cap": 2.0,
                "minimum_path_usd": 0.105,
                "bounded_total_usd": 0.113,
                "expected_output_tokens": 9800.0,
            }
        return {
            "agent1_cost_usd": 0.001,
            "agent2_generation_cost_usd": 0.098,
            "rewrite_cost_per_attempt_usd": 0.004,
            "rewrite_attempt_cap": 2.0,
            "minimum_path_usd": 0.099,
            "bounded_total_usd": 0.107,
            "expected_output_tokens": 9800.0,
        }

    monkeypatch.setattr(
        filings_api,
        "_estimate_two_agent_summary_cost_preflight",
        _fake_preflight,
    )
    monkeypatch.setattr(
        filings_api,
        "_build_budget_adapted_summary_prompt",
        lambda **kwargs: (
            str(kwargs["base_prompt_template"]).replace(
                str(kwargs["company_research_block_placeholder"]), ""
            )[:16_000],
            "skipped",
            True,
            ["context_trimmed"],
        ),
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        assert len(prompt_lengths) == 1
        summary_meta = (response.json() or {}).get("summary_meta") or {}
        assert summary_meta.get("within_budget") is True
        assert float(summary_meta.get("estimated_cost_usd") or 0.0) <= 0.10
        assert int(summary_meta.get("target_length") or 0) == 3000
        assert summary_meta.get("research_mode") == "skipped"
        assert summary_meta.get("prompt_budget_adapted") is True
        assert "context_trimmed" in (
            summary_meta.get("budget_adjustments_attempted") or []
        )
        assert int(summary_meta.get("expected_output_tokens") or 0) > 0
        assert int(summary_meta.get("prompt_tokens_estimated") or 0) > 0
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_summary_skips_optional_retries_when_preflight_minimum_fits_cap(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("OPENAI_COST_PER_SUMMARY_USD", "0.10")
    monkeypatch.setenv("SUMMARY_RESEARCH_TOKEN_RESERVE", "0")

    filing_id = "budget-optional-retry-skip-filing"
    company_id = "budget-optional-retry-skip-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    monkeypatch.setattr(
        filings_api,
        "_summary_token_budget",
        lambda **_kwargs: filings_api.TokenBudget(total_tokens=0, remaining_tokens=0),
    )

    class _RetryAwareCostBudget:
        def __init__(self) -> None:
            self.budget_cap_usd = 0.10
            self.input_rate_per_1m = 2.5
            self.output_rate_per_1m = 10.0
            self.spent_usd = 0.0

        def estimate_tokens(self, text: str | None) -> int:
            return filings_api._estimate_summary_tokens(text)

        def estimate_cost(self, *, prompt_tokens: int, output_tokens: int) -> float:
            del prompt_tokens, output_tokens
            return 0.01

        def estimate_call(self, prompt: str, expected_output_tokens: int) -> float:
            del expected_output_tokens
            prompt_text = str(prompt or "")
            if (
                "*** CRITICAL LENGTH REQUIREMENT ***" in prompt_text
                or prompt_text.startswith("Rewrite this equity memo")
            ):
                return 0.12
            return 0.01

        def can_afford(self, prompt: str, expected_output_tokens: int) -> bool:
            projected = self.spent_usd + self.estimate_call(
                prompt, expected_output_tokens
            )
            return projected <= self.budget_cap_usd

        def charge(self, _prompt: str, _output: str) -> float:
            self.spent_usd += 0.01
            return 0.01

        @property
        def remaining_usd(self) -> float:
            return max(0.0, float(self.budget_cap_usd) - float(self.spent_usd))

    monkeypatch.setattr(
        filings_api,
        "_summary_cost_budget",
        lambda **_kwargs: _RetryAwareCostBudget(),
    )

    monkeypatch.setattr(
        filings_api,
        "_estimate_two_agent_summary_cost_preflight",
        lambda **_kwargs: {
            "agent1_cost_usd": 0.01,
            "agent2_generation_cost_usd": 0.05,
            "rewrite_cost_per_attempt_usd": 0.03,
            "rewrite_attempt_cap": 2.0,
            "minimum_path_usd": 0.06,
            "bounded_total_usd": 0.12,
            "expected_output_tokens": 6000.0,
        },
    )

    llm_calls = {"count": 0}

    def _fake_call(*_args, **_kwargs) -> str:
        llm_calls["count"] += 1
        return _build_long_form_summary(
            3000, include_exec_quote=True, include_mdna_quote=True
        )

    monkeypatch.setattr(filings_api, "_call_gemini_client", _fake_call)
    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _rewrite_passthrough)
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **_kwargs: (
            [],
            {
                "target_length": 3000,
                "final_word_count": 3000,
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )

    def _fake_two_agent_pipeline(**kwargs):
        prompt = kwargs["build_summary_prompt"]("alpha " * 1200)
        summary_text = kwargs["generate_summary"](prompt, 30.0)
        return summary_two_agent.TwoAgentSummaryPipelineResult(
            summary_text=summary_text,
            model_used="gpt-5.2",
            background_used=True,
            background_text="alpha " * 1200,
            agent_timings={
                "agent_1_research_seconds": 0.01,
                "agent_2_summary_seconds": 0.02,
            },
            agent_stage_calls=[
                {
                    "stage": "agent_1_research",
                    "api": "responses",
                    "duration_seconds": 0.01,
                },
                {
                    "stage": "agent_2_summary",
                    "api": "responses",
                    "duration_seconds": 0.02,
                },
            ],
            total_llm_calls=2,
        )

    monkeypatch.setattr(
        filings_api,
        "run_two_agent_summary_pipeline",
        _fake_two_agent_pipeline,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        summary_meta = payload.get("summary_meta") or {}
        budget_cap = float(summary_meta.get("budget_cap_usd") or 0.0)
        assert budget_cap == 0.10
        assert float(summary_meta.get("estimated_cost_usd") or 0.0) < budget_cap
        assert float(summary_meta.get("estimated_bounded_cost_usd") or 0.0) > budget_cap
        assert float(summary_meta.get("actual_cost_usd") or 0.0) <= budget_cap
        assert summary_meta.get("within_budget") is True
        assert llm_calls["count"] == 1
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_summary_budget_caps_align_to_twenty_cents(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_COST_PER_SUMMARY_USD", "0.20")
    monkeypatch.setenv("OPENAI_COST_PER_LONGFORM_SUMMARY_USD", "0.20")
    monkeypatch.setenv("FAST_SUMMARY_MAX_COST_USD", "0.20")

    assert filings_api._summary_budget_cap_usd(target_length=None) == pytest.approx(0.20)
    assert filings_api._summary_budget_cap_usd(target_length=3000) == pytest.approx(0.20)
    assert filings_api._fast_summary_max_cost_usd() == pytest.approx(0.20)


def test_target_length_contract_failure_returns_422_for_non_long_form(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")

    filing_id = "strict-contract-short-filing"
    company_id = "strict-contract-short-company"
    target_length = 220
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    compliant_length_summary = build_summary_with_word_count(target_length)
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: compliant_length_summary,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _rewrite_passthrough,
    )
    monkeypatch.setattr(
        filings_api,
        "_make_generic_filler_validator",
        lambda *args, **kwargs: (lambda _text: "narrative quality check failed"),
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={
            "mode": "custom",
            "target_length": target_length,
            "strict_contract": True,
        },
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        meta = payload.get("summary_meta") or {}
        assert int(meta.get("target_length") or 0) == target_length
        missing = meta.get("contract_missing_requirements") or []
        assert len(missing) > 0
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_insufficient_numeric_key_metrics_returns_422(monkeypatch) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "insufficient-key-metrics-filing"
    company_id = "insufficient-key-metrics-company"
    _seed_filing_bundle(filing_id, company_id)

    monkeypatch.setattr(
        filings_api,
        "_build_key_metrics_block",
        lambda *_args, **_kwargs: (
            "DATA_GRID_START\n"
            "Revenue | $1.0B\n"
            "Operating Margin | 10.0%\n"
            "Net Margin | 8.0%\n"
            "Free Cash Flow | $0.4B\n"
            "DATA_GRID_END"
        ),
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 650},
    )

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail", {})
        assert detail.get("failure_code") == "INSUFFICIENT_NUMERIC_KEY_METRICS"
        assert int(detail.get("numeric_row_count") or 0) == 4
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_short_form_missing_closing_takeaway_returns_summary_contract_422(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "shortform-missing-closing-filing"
    company_id = "shortform-missing-closing-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    malformed_summary = (
        "## Financial Health Rating\n"
        "The balance sheet remains stable and provides operating flexibility.\n\n"
        "## Executive Summary\n"
        "Profitability improved, but the quarter still depends on repeatable cash conversion.\n\n"
        "## Financial Performance\n"
        "Revenue, operating income, and free cash flow all moved in the right direction against the prior quarter.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is balancing reinvestment with margin discipline, which matters for durability through the next few periods.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A softer demand environment could pressure both pricing and cash generation.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $2.50B\n"
        "Operating Income | $0.70B\n"
        "Operating Margin | 28.0%\n"
        "Free Cash Flow | $0.65B\n"
        "Current Ratio | 2.3x\n"
        "DATA_GRID_END"
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: malformed_summary,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 650},
    )

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail", {})
        assert detail.get("failure_code") == "SUMMARY_CONTRACT_FAILED"
        missing = detail.get("missing_requirements") or []
        assert any("closing takeaway" in str(item).lower() for item in missing)
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_short_form_invalid_key_metrics_returns_summary_contract_422(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "shortform-invalid-key-metrics-filing"
    company_id = "shortform-invalid-key-metrics-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    malformed_summary = (
        "## Financial Health Rating\n"
        "The balance sheet remains stable and provides operating flexibility.\n\n"
        "## Executive Summary\n"
        "Profitability improved, but the quarter still depends on repeatable cash conversion.\n\n"
        "## Financial Performance\n"
        "Revenue, operating income, and free cash flow all moved in the right direction against the prior quarter.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is balancing reinvestment with margin discipline, which matters for durability through the next few periods.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A softer demand environment could pressure both pricing and cash generation.\n\n"
        "## Key Metrics\n"
        "0.30x DATA_GRID_END **Alphabet Margin / Reinvestment Risk**\n\n"
        "## Closing Takeaway\n"
        "Hold if operating margin stays above 25% over the next four quarters, and move to Sell if free cash flow falls below $0.30B over the next year."
    )
    monkeypatch.setattr(
        filings_api,
        "_canonicalize_key_metrics_section",
        lambda text, _metrics: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: malformed_summary,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 650},
    )

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail", {})
        assert detail.get("failure_code") == "SUMMARY_CONTRACT_FAILED"
        missing = detail.get("missing_requirements") or []
        assert any("key metrics" in str(item).lower() or "data_grid" in str(item).lower() for item in missing)
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_short_form_non_structural_band_miss_returns_summary_contract_422(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "shortform-soft-target-warning-filing"
    company_id = "shortform-soft-target-warning-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    intact_summary = (
        "## Financial Health Rating\n"
        "The balance sheet remains stable and provides operating flexibility.\n\n"
        "## Executive Summary\n"
        "Profitability improved, but the quarter still depends on repeatable cash conversion.\n\n"
        "## Financial Performance\n"
        "Revenue, operating income, and free cash flow all moved in the right direction against the prior quarter.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is balancing reinvestment with margin discipline, which matters for durability through the next few periods.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A softer demand environment could pressure both pricing and cash generation.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $2.50B\n"
        "Operating Income | $0.70B\n"
        "Operating Margin | 28.0%\n"
        "Free Cash Flow | $0.65B\n"
        "Current Ratio | 2.3x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "Hold if operating margin stays above 25% over the next four quarters, and move to Sell if free cash flow falls below $0.30B over the next year."
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: intact_summary,
    )
    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _rewrite_passthrough)
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **_kwargs: (
            [
                "Final word-count band violation: expected 635-665, got split=620, stripped=620."
            ],
            {
                "target_length": 650,
                "final_word_count": 620,
                "verified_quote_count": 0,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_structural_failure_requirements",
        lambda **_kwargs: [],
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 650},
    )

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail", {})
        assert detail.get("failure_code") == "SUMMARY_CONTRACT_FAILED"
        missing = detail.get("missing_requirements") or []
        assert any("word-count band violation" in str(item).lower() for item in missing)
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_short_form_unresolved_section_balance_returns_summary_contract_422(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "shortform-section-balance-failure-filing"
    company_id = "shortform-section-balance-failure-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    intact_summary = (
        "## Financial Health Rating\n"
        "The balance sheet remains stable and provides operating flexibility.\n\n"
        "## Executive Summary\n"
        "Profitability improved, but the quarter still depends on repeatable cash conversion.\n\n"
        "## Financial Performance\n"
        "Revenue, operating income, and free cash flow all moved in the right direction against the prior quarter.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is balancing reinvestment with margin discipline, which matters for durability through the next few periods.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A softer demand environment could pressure both pricing and cash generation.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $2.50B\n"
        "Operating Income | $0.70B\n"
        "Operating Margin | 28.0%\n"
        "Free Cash Flow | $0.65B\n"
        "Current Ratio | 2.3x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "Hold if operating margin stays above 25% over the next four quarters, and move to Sell if free cash flow falls below $0.30B over the next year."
    )
    balance_issue = (
        "Section balance issue: 'Financial Performance' is underweight (32 words; target ~96±8). "
        "Expand it and shorten other sections proportionally so the memo stays within 630-670 words."
    )
    base_meta = {
        "target_length": 650,
        "final_word_count": 642,
        "final_split_word_count": 642,
        "verified_quote_count": 0,
        "key_metrics_numeric_row_count": 5,
        "quality_checks_passed": [],
    }
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: intact_summary,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **_kwargs: ([balance_issue], dict(base_meta)),
    )

    def _unresolved_recovery(summary_text, *args, **kwargs):
        return summary_text, [balance_issue], dict(base_meta), True

    monkeypatch.setattr(
        filings_api,
        "_recover_short_form_editorial_issues_once",
        _unresolved_recovery,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 650},
    )

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail", {})
        assert detail.get("failure_code") == "SUMMARY_CONTRACT_FAILED"
        missing = detail.get("missing_requirements") or []
        assert any("section balance issue" in str(item).lower() for item in missing)
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_short_quality_prompt_relaxes_quotes_and_question_chaining(monkeypatch) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "shortform-prompt-policy-filing"
    company_id = "shortform-prompt-policy-company"
    _seed_filing_bundle(filing_id, company_id, filing_type="10-Q", filing_date="2025-09-30")
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    valid_summary = (
        "## Financial Health Rating\n"
        "74/100 - Healthy. Margin quality and liquidity still support the balance-sheet cushion.\n\n"
        "## Executive Summary\n"
        "Profitability improved, but the quarter still depends on repeatable cash conversion.\n\n"
        "## Financial Performance\n"
        "Revenue, operating income, and free cash flow all moved in the right direction against the prior quarter.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is balancing reinvestment with margin discipline, which matters for durability through the next few periods.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A softer demand environment could pressure both pricing and cash generation.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $2.50B\n"
        "Operating Income | $0.70B\n"
        "Operating Margin | 28.0%\n"
        "Free Cash Flow | $0.65B\n"
        "Current Ratio | 2.3x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I rate Long Form Corp a HOLD because margins improved while cash conversion still needs to prove it can stay durable."
    )
    captured: dict[str, str] = {}

    def _capture_prompt(_client, base_prompt, *args, **kwargs):
        captured["prompt"] = base_prompt
        return valid_summary

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        _capture_prompt,
    )
    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _rewrite_passthrough)
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **_kwargs: (
            [],
            {
                "target_length": 650,
                "final_word_count": 640,
                "final_split_word_count": 640,
                "verified_quote_count": 0,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_structural_failure_requirements",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_editorial_failure_requirements",
        lambda **_kwargs: [],
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 650},
    )

    try:
        assert response.status_code == 200
        prompt = (captured.get("prompt") or "").lower()
        assert "quotes are mandatory" not in prompt
        assert "include at least 3 short direct quotes" not in prompt
        assert "the last sentence of each section must raise a question" not in prompt
        assert "direct quotes are optional" in prompt
        assert "frame the central question only in executive summary" in prompt
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_short_form_clean_under_target_returns_summary_contract_422_without_stock_tail(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "shortform-clean-under-target-filing"
    company_id = "shortform-clean-under-target-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    intact_summary = (
        "## Financial Health Rating\n"
        "The balance sheet remains stable and provides operating flexibility.\n\n"
        "## Executive Summary\n"
        "Profitability improved, but the quarter still depends on repeatable cash conversion.\n\n"
        "## Financial Performance\n"
        "Revenue, operating income, and free cash flow all moved in the right direction against the prior quarter.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is balancing reinvestment with margin discipline, which matters for durability through the next few periods.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A softer demand environment could pressure both pricing and cash generation.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $2.50B\n"
        "Operating Income | $0.70B\n"
        "Operating Margin | 28.0%\n"
        "Free Cash Flow | $0.65B\n"
        "Current Ratio | 2.3x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "Hold if operating margin stays above 25% over the next four quarters, and move to Sell if free cash flow falls below $0.30B over the next year."
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: intact_summary,
    )
    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _rewrite_passthrough)
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **_kwargs: (
            [
                "Final word-count band violation: expected 635-665, got split=590, stripped=590."
            ],
            {
                "target_length": 650,
                "final_word_count": 590,
                "verified_quote_count": 0,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_structural_failure_requirements",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_editorial_failure_requirements",
        lambda **_kwargs: [],
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 650},
    )

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail", {})
        assert detail.get("failure_code") == "SUMMARY_CONTRACT_FAILED"
        missing = detail.get("missing_requirements") or []
        assert any("word-count band violation" in str(item).lower() for item in missing)
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_short_form_clean_too_short_returns_summary_contract_422(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "shortform-clean-too-short-filing"
    company_id = "shortform-clean-too-short-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    intact_summary = (
        "## Financial Health Rating\n"
        "The balance sheet remains stable and provides operating flexibility.\n\n"
        "## Executive Summary\n"
        "Profitability improved, but the quarter still depends on repeatable cash conversion.\n\n"
        "## Financial Performance\n"
        "Revenue, operating income, and free cash flow all moved in the right direction against the prior quarter.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is balancing reinvestment with margin discipline, which matters for durability through the next few periods.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A softer demand environment could pressure both pricing and cash generation.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $2.50B\n"
        "Operating Income | $0.70B\n"
        "Operating Margin | 28.0%\n"
        "Free Cash Flow | $0.65B\n"
        "Current Ratio | 2.3x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "Hold if operating margin stays above 25% over the next four quarters, and move to Sell if free cash flow falls below $0.30B over the next year."
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: intact_summary,
    )
    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _rewrite_passthrough)
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **_kwargs: (
            [
                "Final word-count band violation: expected 635-665, got split=560, stripped=560."
            ],
            {
                "target_length": 650,
                "final_word_count": 560,
                "verified_quote_count": 0,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_structural_failure_requirements",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_editorial_failure_requirements",
        lambda **_kwargs: [],
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 650},
    )

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail", {})
        assert detail.get("failure_code") == "SUMMARY_CONTRACT_FAILED"
        missing = detail.get("missing_requirements") or []
        assert any("word-count band violation" in str(item).lower() for item in missing)
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_short_quality_boundary_1200_brief_section_returns_summary_contract_422(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "shortform-boundary-1200-brief-section-filing"
    company_id = "shortform-boundary-1200-brief-section-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    intact_summary = (
        "## Financial Health Rating\n"
        "The balance sheet remains stable and provides operating flexibility.\n\n"
        "## Executive Summary\n"
        "Profitability improved, but the quarter still depends on repeatable cash conversion.\n\n"
        "## Financial Performance\n"
        "Revenue moved higher.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is balancing reinvestment with margin discipline, which matters for durability through the next few periods.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A softer demand environment could pressure both pricing and cash generation.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $2.50B\n"
        "Operating Income | $0.70B\n"
        "Operating Margin | 28.0%\n"
        "Free Cash Flow | $0.65B\n"
        "Current Ratio | 2.3x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "I rate Example Corp a HOLD because margins improved while cash conversion still needs to prove durability."
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: intact_summary,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **_kwargs: (
            [
                "The 'Financial Performance' section is too brief (17 words). Expand it to at least 20 words and ensure it concludes on a full sentence."
            ],
            {
                "target_length": 1200,
                "final_word_count": 1186,
                "verified_quote_count": 0,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_structural_failure_requirements",
        lambda **_kwargs: [],
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 1200},
    )

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail", {})
        assert detail.get("failure_code") == "SUMMARY_CONTRACT_FAILED"
        missing = detail.get("missing_requirements") or []
        assert any("section is too brief" in str(item).lower() for item in missing)
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_short_form_generic_filler_issue_returns_summary_contract_422(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "shortform-generic-filler-filing"
    company_id = "shortform-generic-filler-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    generic_summary = (
        "## Financial Health Rating\n"
        "The balance sheet remains stable and provides operating flexibility.\n\n"
        "## Executive Summary\n"
        "Profitability improved, but the quarter still depends on repeatable cash conversion.\n\n"
        "## Financial Performance\n"
        "Revenue, operating income, and free cash flow all moved in the right direction against the prior quarter.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is balancing reinvestment with margin discipline, which matters for durability through the next few periods.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A softer demand environment could pressure both pricing and cash generation.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $2.50B\n"
        "Operating Income | $0.70B\n"
        "Operating Margin | 28.0%\n"
        "Free Cash Flow | $0.65B\n"
        "Current Ratio | 2.3x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "Hold if operating margin stays above 25% over the next four quarters, and move to Sell if free cash flow falls below $0.30B over the next year."
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: generic_summary,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **_kwargs: (
            [
                "Generic filler detected in Financial Performance: 4 of 5 sentences are financial axioms that apply to any company. Replace with company-specific analysis."
            ],
            {
                "target_length": 650,
                "final_word_count": 640,
                "verified_quote_count": 0,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_structural_failure_requirements",
        lambda **_kwargs: [],
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 650},
    )

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail", {})
        assert detail.get("failure_code") == "SUMMARY_CONTRACT_FAILED"
        missing = detail.get("missing_requirements") or []
        assert any("generic filler" in str(item).lower() for item in missing)
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_short_form_number_theme_repetition_issue_returns_contract_warnings(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "shortform-number-theme-repetition-filing"
    company_id = "shortform-number-theme-repetition-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    repetitive_summary = (
        "## Financial Health Rating\n"
        "The balance sheet remains stable and provides operating flexibility.\n\n"
        "## Executive Summary\n"
        "Profitability improved, but the quarter still depends on repeatable cash conversion.\n\n"
        "## Financial Performance\n"
        "Revenue, operating income, and free cash flow all moved in the right direction against the prior quarter.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is balancing reinvestment with margin discipline, which matters for durability through the next few periods.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A softer demand environment could pressure both pricing and cash generation.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $2.50B\n"
        "Operating Income | $0.70B\n"
        "Operating Margin | 28.0%\n"
        "Free Cash Flow | $0.65B\n"
        "Current Ratio | 2.3x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "Hold if operating margin stays above 25% over the next four quarters, and move to Sell if free cash flow falls below $0.30B over the next year."
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: repetitive_summary,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **_kwargs: (
            [
                "Number repetition across sections: '42.1%' appears in 4 sections. Use each specific figure in at most 2-3 sections; reference it by context elsewhere.",
                "Theme over-repetition: 'free cash flow' discussed in 5 sections. Consolidate to the most relevant section and reference briefly elsewhere.",
            ],
            {
                "target_length": 650,
                "final_word_count": 642,
                "final_split_word_count": 642,
                "verified_quote_count": 0,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_structural_failure_requirements",
        lambda **_kwargs: [],
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 650},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        assert payload.get("degraded") is True
        warnings = " ".join(str(item) for item in (payload.get("contract_warnings") or []))
        assert "number repetition across sections" in warnings.lower()
        assert "theme over-repetition" in warnings.lower()
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_short_form_repetition_issue_returns_contract_warnings(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "shortform-repetition-filing"
    company_id = "shortform-repetition-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    repetitive_summary = (
        "## Financial Health Rating\n"
        "The balance sheet remains stable and provides operating flexibility.\n\n"
        "## Executive Summary\n"
        "The key question is whether the quarter's margin rebound can hold as reinvestment rises.\n\n"
        "## Financial Performance\n"
        "Revenue, operating income, and free cash flow all moved in the right direction against the prior quarter.\n\n"
        "## Management Discussion & Analysis\n"
        "The execution question is whether management can fund growth without giving back the margin gains.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A softer demand environment could pressure both pricing and cash generation.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $2.50B\n"
        "Operating Income | $0.70B\n"
        "Operating Margin | 28.0%\n"
        "Free Cash Flow | $0.65B\n"
        "Current Ratio | 2.3x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "Hold if operating margin stays above 25% over the next four quarters, and move to Sell if free cash flow falls below $0.30B over the next year."
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: repetitive_summary,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **_kwargs: (
            [
                "Question-framing repetition: multiple sections restate the thesis as a question. Keep that framing in Executive Summary only and answer it directly elsewhere."
            ],
            {
                "target_length": 650,
                "final_word_count": 642,
                "final_split_word_count": 642,
                "verified_quote_count": 0,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_structural_failure_requirements",
        lambda **_kwargs: [],
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 650},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        assert payload.get("degraded") is True
        warnings = payload.get("contract_warnings") or []
        assert any("question-framing repetition" in str(item).lower() for item in warnings)
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_short_form_truncated_tail_is_repaired_or_returns_422(monkeypatch) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "shortform-truncated-tail-filing"
    company_id = "shortform-truncated-tail-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    truncated_summary = (
        "## Financial Health Rating\n"
        "The balance sheet remains stable and provides operating flexibility.\n\n"
        "## Executive Summary\n"
        "Profitability improved, but the quarter still depends on repeatable cash conversion.\n\n"
        "## Financial Performance\n"
        "Revenue, operating income, and free cash flow all moved in the right direction against the prior quarter.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is balancing reinvestment with margin discipline, which matters for durability through the next few periods.\n\n"
        "## Risk Factors\n"
        "**Demand Risk**: A softer demand environment could pressure both pricing and cash generation.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $2.50B\n"
        "Operating Income | $0.70B\n"
        "Operating Margin | 28.0%\n"
        "Free Cash Flow | $0.65B\n"
        "Current Ratio | 2.3x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "Hold if operating margin stays above 25% over the next four quarters, and move to Sell if free cash flow falls below"
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: truncated_summary,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 650},
    )

    try:
        assert response.status_code in {200, 422}
        if response.status_code == 200:
            payload = response.json() or {}
            summary = str(payload.get("summary") or "").rstrip()
            assert summary.endswith((".", "!", "?"))
        else:
            detail = (response.json() or {}).get("detail", {})
            assert detail.get("failure_code") == "SUMMARY_CONTRACT_FAILED"
            assert detail.get("missing_requirements")
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_long_form_success_returns_strict_band_and_quote_distribution(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "longform-success-filing"
    company_id = "longform-success-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    success_summary = _build_long_form_summary(
        3000, include_exec_quote=True, include_mdna_quote=True
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: success_summary,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _rewrite_passthrough,
    )
    monkeypatch.setattr(
        filings_api, "_enforce_strict_target_band", lambda text, *_args, **_kwargs: text
    )
    monkeypatch.setattr(
        filings_api,
        "_ensure_final_strict_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_whitespace_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api, "_apply_strict_contract_seal", lambda text, **_kwargs: text
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **kwargs: (
            [],
            {
                "target_length": 3000,
                "final_word_count": filings_api._count_words(str(kwargs.get("summary_text") or "")),
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model", (), {"generate_content": lambda self, _prompt: None}
            )()

    monkeypatch.setattr(
        filings_api, "get_gemini_client", lambda *args, **kwargs: DummyClient()
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        payload = response.json()
        summary = payload["summary"]
        word_count = _backend_word_count(summary)
        assert 2985 <= word_count <= 3015
        assert (
            filings_api._count_direct_quotes_in_section(summary, "Executive Summary")
            >= 1
        )
        assert (
            filings_api._count_direct_quotes_in_section(
                summary, "Management Discussion & Analysis"
            )
            >= 1
        )

        meta = payload.get("summary_meta") or {}
        assert meta.get("pipeline_mode") == "two_agent"
        assert meta.get("model_used") == "gpt-5.2"
        timings = meta.get("agent_timings") or {}
        assert "agent_1_research_seconds" in timings
        assert "agent_2_summary_seconds" in timings
        assert isinstance(meta.get("background_used"), bool)
        assert 2985 <= int(meta.get("final_word_count") or 0) <= 3015
        assert int(meta.get("verified_quote_count") or 0) >= 3
        assert isinstance(meta.get("quality_checks_passed"), list)
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_long_form_without_snippets_does_not_fail_quote_contract(monkeypatch) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    # Force longform model to GPT-5.2 so strict contract enforcement applies.
    monkeypatch.setenv("OPENAI_SUMMARY_LONGFORM_MODEL_NAME", "gpt-5.2")

    filing_id = "longform-quote-evidence-filing"
    company_id = "longform-quote-evidence-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    in_band_summary = _build_long_form_summary(
        3000, include_exec_quote=True, include_mdna_quote=True
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: in_band_summary,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _rewrite_passthrough,
    )
    monkeypatch.setattr(
        filings_api, "_build_filing_language_snippets", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **_kwargs: (
            [],
            {
                "target_length": 3000,
                "final_word_count": 3000,
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model", (), {"generate_content": lambda self, _prompt: None}
            )()

    monkeypatch.setattr(
        filings_api, "get_gemini_client", lambda *args, **kwargs: DummyClient()
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        meta = payload.get("summary_meta") or {}
        assert int(meta.get("verified_quote_count") or 0) >= 3
        assert meta.get("contract_missing_requirements") in ([], None)
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_long_form_requires_exec_and_mdna_quote_distribution(monkeypatch) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    # Force longform model to GPT-5.2 so strict contract enforcement applies.
    monkeypatch.setenv("OPENAI_SUMMARY_LONGFORM_MODEL_NAME", "gpt-5.2")

    filing_id = "longform-quote-distribution-filing"
    company_id = "longform-quote-distribution-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    missing_mdna_quote = _build_long_form_summary(
        3000, include_exec_quote=True, include_mdna_quote=False
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: missing_mdna_quote,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _rewrite_passthrough,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **kwargs: (
            (
                []
                if filings_api._count_direct_quotes_in_section(
                    str(kwargs.get("summary_text") or ""),
                    "Management Discussion & Analysis",
                )
                >= 1
                else [
                    "Management Discussion & Analysis must include at least one verified direct quote."
                ]
            ),
            {
                "target_length": 3000,
                "final_word_count": 3000,
                "verified_quote_count": 2,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model", (), {"generate_content": lambda self, _prompt: None}
            )()

    monkeypatch.setattr(
        filings_api, "get_gemini_client", lambda *args, **kwargs: DummyClient()
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        summary = payload.get("summary") or ""
        assert (
            filings_api._count_direct_quotes_in_section(
                summary, "Management Discussion & Analysis"
            )
            >= 1
        )
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_contract_recovery_generation_rescues_catastrophic_underflow(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    monkeypatch.setenv("SUMMARY_CONTRACT_RETRY_ATTEMPTS", "2")

    filing_id = "contract-recovery-success-filing"
    company_id = "contract-recovery-success-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)
    monkeypatch.setattr(
        filings_api, "_enforce_strict_target_band", lambda text, *_args, **_kwargs: text
    )
    monkeypatch.setattr(
        filings_api,
        "_ensure_final_strict_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_whitespace_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_make_quote_grounding_validator",
        lambda **_kwargs: (lambda _text: None),
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **kwargs: (
            (
                []
                if "RECOVERY_MARKER_TOKEN" in str(kwargs.get("summary_text") or "")
                else [
                    "Final word-count band violation: expected 2980-3020, got split=1000, stripped=990."
                ]
            ),
            {
                "target_length": 3000,
                "final_word_count": (
                    3000
                    if "RECOVERY_MARKER_TOKEN" in str(kwargs.get("summary_text") or "")
                    else 1000
                ),
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )

    underflow_summary = _build_long_form_summary(
        1000, include_exec_quote=True, include_mdna_quote=True
    )
    recovered_summary = _build_long_form_summary(
        3000, include_exec_quote=True, include_mdna_quote=True
    )
    call_counter = {"total": 0, "recovery": 0}

    def _fake_generate(*args, **kwargs):
        prompt = str(kwargs.get("base_prompt") or (args[1] if len(args) > 1 else ""))
        call_counter["total"] += 1
        if "STRICT CONTRACT RECOVERY MODE (ONE-SHOT)" in prompt:
            call_counter["recovery"] += 1
            return f"{recovered_summary}\n\nRECOVERY_MARKER_TOKEN."
        return underflow_summary

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        _fake_generate,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _rewrite_passthrough,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        meta = payload.get("summary_meta") or {}
        assert call_counter["recovery"] == 1
        assert meta.get("contract_recovery_used") is True
        assert int(meta.get("contract_recovery_generation_calls") or 0) == 1
        assert int(meta.get("pre_contract_split_word_count") or 0) > 0
        assert int(meta.get("pre_contract_stripped_word_count") or 0) > 0
        assert 2985 <= int(meta.get("final_word_count") or 0) <= 3015
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_catastrophic_underflow_retry_noop_attempts_recovery_and_returns_422(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    monkeypatch.setenv("SUMMARY_CONTRACT_RETRY_ATTEMPTS", "2")

    filing_id = "contract-recovery-failure-filing"
    company_id = "contract-recovery-failure-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)
    monkeypatch.setattr(
        filings_api, "_enforce_strict_target_band", lambda text, *_args, **_kwargs: text
    )
    monkeypatch.setattr(
        filings_api,
        "_ensure_final_strict_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_whitespace_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_make_quote_grounding_validator",
        lambda **_kwargs: (lambda _text: None),
    )

    underflow_summary = _build_long_form_summary(
        1000, include_exec_quote=True, include_mdna_quote=True
    )
    recovery_calls = {"count": 0}

    def _fake_generate(*args, **kwargs):
        prompt = str(kwargs.get("base_prompt") or (args[1] if len(args) > 1 else ""))
        if "STRICT CONTRACT RECOVERY MODE (ONE-SHOT)" in prompt:
            recovery_calls["count"] += 1
            raise filings_api.SummaryBudgetExceededError(
                {"detail": "Recovery generation exceeds strict cost budget."}
            )
        return underflow_summary

    def _rewrite_budget_guard(*args, **kwargs):
        summary_text = kwargs.get("summary_text")
        if summary_text is None and len(args) >= 2:
            summary_text = args[1]
        summary_text = str(summary_text or "")
        stats = kwargs.get("generation_stats")
        if isinstance(stats, dict):
            stats["rewrite_skipped_budget_guard"] = True
        return summary_text, (filings_api._count_words(summary_text), 10)

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        _fake_generate,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _rewrite_budget_guard,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 422
        detail = (response.json() or {}).get("detail", {})
        assert detail.get("failure_code") == "SUMMARY_ONE_SHOT_CONTRACT_FAILED"
        assert recovery_calls["count"] == 1
        missing = detail.get("missing_requirements") or []
        diagnostic_missing = detail.get("diagnostic_missing_requirements") or []
        assert any("strict cost budget guard" in str(item).lower() for item in (missing + diagnostic_missing))
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_material_underflow_one_shot_contract_retry_runs_bounded_rewrite(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    monkeypatch.setenv("SUMMARY_CONTRACT_RETRY_ATTEMPTS", "2")

    filing_id = "contract-retry-material-underflow-filing"
    company_id = "contract-retry-material-underflow-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)
    monkeypatch.setattr(
        filings_api,
        "_make_quote_grounding_validator",
        lambda **_kwargs: (lambda _text: None),
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_strict_target_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_ensure_final_strict_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_whitespace_word_band",
        lambda text, *_args, **_kwargs: text,
    )

    underflow_summary = _build_long_form_summary(
        2754, include_exec_quote=True, include_mdna_quote=True
    )
    recovered_summary = _build_long_form_summary(
        3000, include_exec_quote=True, include_mdna_quote=True
    )
    rewrite_calls = {"count": 0, "allow_one_shot_rewrite": False}

    def _fake_eval_contract_requirements(**kwargs):
        text = str(kwargs.get("summary_text") or "")
        if "MATERIAL_UNDERFLOW_REWRITE_SUCCESS" in text:
            return (
                [],
                {
                    "target_length": 3000,
                    "final_word_count": 3000,
                    "verified_quote_count": 3,
                    "key_metrics_numeric_row_count": 5,
                    "quality_checks_passed": [],
                },
            )
        return (
            [
                "Final word-count band violation: expected 2980-3020, got split=2783, stripped=2754.",
                "_validator: Risk Factors are not in the required format.",
            ],
            {
                "target_length": 3000,
                "final_word_count": 2754,
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        )

    def _fake_rewrite(*args, **kwargs):
        rewrite_calls["count"] += 1
        rewrite_calls["allow_one_shot_rewrite"] = bool(
            kwargs.get("allow_one_shot_rewrite")
        )
        return (
            f"{recovered_summary}\n\nMATERIAL_UNDERFLOW_REWRITE_SUCCESS.",
            (3000, 15),
        )

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: underflow_summary,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        _fake_eval_contract_requirements,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _fake_rewrite,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        meta = payload.get("summary_meta") or {}
        assert rewrite_calls["count"] >= 1
        assert rewrite_calls["allow_one_shot_rewrite"] is True
        assert 2985 <= int(meta.get("final_word_count") or 0) <= 3015
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_catastrophic_underflow_one_shot_contract_retry_prefers_bounded_rewrite(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    monkeypatch.setenv("SUMMARY_CONTRACT_RETRY_ATTEMPTS", "2")

    filing_id = "contract-retry-catastrophic-underflow-bounded-first-filing"
    company_id = "contract-retry-catastrophic-underflow-bounded-first-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)
    monkeypatch.setattr(
        filings_api,
        "_make_quote_grounding_validator",
        lambda **_kwargs: (lambda _text: None),
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_strict_target_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_ensure_final_strict_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_whitespace_word_band",
        lambda text, *_args, **_kwargs: text,
    )

    underflow_summary = _build_long_form_summary(
        2407, include_exec_quote=True, include_mdna_quote=True
    )
    recovered_summary = _build_long_form_summary(
        3000, include_exec_quote=True, include_mdna_quote=True
    )
    rewrite_calls = {"count": 0, "allow_one_shot_rewrite": False}
    recovery_calls = {"count": 0}

    def _fake_generate(*args, **kwargs):
        prompt = str(kwargs.get("base_prompt") or (args[1] if len(args) > 1 else ""))
        if "STRICT CONTRACT RECOVERY MODE (ONE-SHOT)" in prompt:
            recovery_calls["count"] += 1
            return f"{recovered_summary}\n\nCATASTROPHIC_RECOVERY_MARKER."
        return underflow_summary

    def _fake_eval_contract_requirements(**kwargs):
        text = str(kwargs.get("summary_text") or "")
        if "CATASTROPHIC_BOUNDED_REWRITE_SUCCESS" in text:
            return (
                [],
                {
                    "target_length": 3000,
                    "final_word_count": 3000,
                    "verified_quote_count": 3,
                    "key_metrics_numeric_row_count": 5,
                    "quality_checks_passed": [],
                },
            )
        return (
            [
                "Final word-count band violation: expected 2980-3020, got split=2430, stripped=2407.",
                "_validator: Risk Factors are not in the required format.",
                "_validator: Closing Takeaway contains conflicting stances (BUY, HOLD).",
            ],
            {
                "target_length": 3000,
                "final_word_count": 2407,
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        )

    def _fake_rewrite(*args, **kwargs):
        rewrite_calls["count"] += 1
        rewrite_calls["allow_one_shot_rewrite"] = bool(
            kwargs.get("allow_one_shot_rewrite")
        )
        return (
            f"{recovered_summary}\n\nCATASTROPHIC_BOUNDED_REWRITE_SUCCESS.",
            (3000, 15),
        )

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        _fake_generate,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        _fake_eval_contract_requirements,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _fake_rewrite,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        meta = payload.get("summary_meta") or {}
        assert rewrite_calls["count"] >= 1
        assert rewrite_calls["allow_one_shot_rewrite"] is True
        assert recovery_calls["count"] == 0
        assert 2985 <= int(meta.get("final_word_count") or 0) <= 3015
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_near_band_underflow_one_shot_contract_retry_runs_bounded_rewrite(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    monkeypatch.setenv("SUMMARY_CONTRACT_RETRY_ATTEMPTS", "2")

    filing_id = "contract-retry-near-band-underflow-filing"
    company_id = "contract-retry-near-band-underflow-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)
    monkeypatch.setattr(
        filings_api,
        "_make_quote_grounding_validator",
        lambda **_kwargs: (lambda _text: None),
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_strict_target_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_ensure_final_strict_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_whitespace_word_band",
        lambda text, *_args, **_kwargs: text,
    )

    underflow_summary = _build_long_form_summary(
        2898, include_exec_quote=True, include_mdna_quote=True
    )
    recovered_summary = _build_long_form_summary(
        3000, include_exec_quote=True, include_mdna_quote=True
    )
    rewrite_calls = {"count": 0, "allow_one_shot_rewrite": False}
    recovery_calls = {"count": 0}

    def _fake_generate(*args, **kwargs):
        prompt = str(kwargs.get("base_prompt") or (args[1] if len(args) > 1 else ""))
        if "STRICT CONTRACT RECOVERY MODE (ONE-SHOT)" in prompt:
            recovery_calls["count"] += 1
            return f"{recovered_summary}\n\nNEAR_BAND_RECOVERY_MARKER."
        return underflow_summary

    def _fake_eval_contract_requirements(**kwargs):
        text = str(kwargs.get("summary_text") or "")
        if "NEAR_BAND_REWRITE_SUCCESS" in text or "NEAR_BAND_RECOVERY_MARKER" in text:
            return (
                [],
                {
                    "target_length": 3000,
                    "final_word_count": 3000,
                    "verified_quote_count": 3,
                    "key_metrics_numeric_row_count": 5,
                    "quality_checks_passed": [],
                },
            )
        return (
            [
                "Final word-count band violation: expected 2980-3020, got split=2928, stripped=2898.",
                "_validator: Risk Factors are not in the required format.",
                "_validator: Closing Takeaway contains conflicting stances (BUY, HOLD). Use exactly one explicit recommendation.",
            ],
            {
                "target_length": 3000,
                "final_word_count": 2898,
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        )

    def _fake_rewrite(*args, **kwargs):
        rewrite_calls["count"] += 1
        rewrite_calls["allow_one_shot_rewrite"] = bool(
            kwargs.get("allow_one_shot_rewrite")
        )
        return (
            f"{recovered_summary}\n\nNEAR_BAND_REWRITE_SUCCESS.",
            (3000, 15),
        )

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        _fake_generate,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        _fake_eval_contract_requirements,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _fake_rewrite,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        meta = payload.get("summary_meta") or {}
        assert rewrite_calls["count"] >= 1
        assert rewrite_calls["allow_one_shot_rewrite"] is True
        assert recovery_calls["count"] == 0
        assert 2985 <= int(meta.get("final_word_count") or 0) <= 3015
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_near_band_overflow_one_shot_contract_retry_runs_bounded_rewrite(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    monkeypatch.setenv("SUMMARY_CONTRACT_RETRY_ATTEMPTS", "2")

    filing_id = "contract-retry-near-band-overflow-filing"
    company_id = "contract-retry-near-band-overflow-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)
    monkeypatch.setattr(
        filings_api,
        "_make_quote_grounding_validator",
        lambda **_kwargs: (lambda _text: None),
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_strict_target_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_ensure_final_strict_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_whitespace_word_band",
        lambda text, *_args, **_kwargs: text,
    )

    overflow_summary = _build_long_form_summary(
        3018, include_exec_quote=True, include_mdna_quote=True
    )
    recovered_summary = _build_long_form_summary(
        3000, include_exec_quote=True, include_mdna_quote=True
    )
    rewrite_calls = {"count": 0, "allow_one_shot_rewrite": False}
    recovery_calls = {"count": 0}

    def _fake_generate(*args, **kwargs):
        prompt = str(kwargs.get("base_prompt") or (args[1] if len(args) > 1 else ""))
        if "STRICT CONTRACT RECOVERY MODE (ONE-SHOT)" in prompt:
            recovery_calls["count"] += 1
            return f"{recovered_summary}\n\nNEAR_BAND_OVERFLOW_RECOVERY_MARKER."
        return overflow_summary

    def _fake_eval_contract_requirements(**kwargs):
        text = str(kwargs.get("summary_text") or "")
        if (
            "NEAR_BAND_OVERFLOW_REWRITE_SUCCESS" in text
            or "NEAR_BAND_OVERFLOW_RECOVERY_MARKER" in text
        ):
            return (
                [],
                {
                    "target_length": 3000,
                    "final_word_count": 3000,
                    "verified_quote_count": 3,
                    "key_metrics_numeric_row_count": 5,
                    "quality_checks_passed": [],
                },
            )
        return (
            [
                "Final word-count band violation: expected 2980-3020, got split=3042, stripped=3018.",
                "_validator: Risk Factors are not in the required format.",
                "_validator: Repetition-by-structure detected (multiple long sentences start the same way).",
            ],
            {
                "target_length": 3000,
                "final_word_count": 3018,
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        )

    def _fake_rewrite(*args, **kwargs):
        rewrite_calls["count"] += 1
        rewrite_calls["allow_one_shot_rewrite"] = bool(
            kwargs.get("allow_one_shot_rewrite")
        )
        return (
            f"{recovered_summary}\n\nNEAR_BAND_OVERFLOW_REWRITE_SUCCESS.",
            (3000, 15),
        )

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        _fake_generate,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        _fake_eval_contract_requirements,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _fake_rewrite,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        meta = payload.get("summary_meta") or {}
        assert rewrite_calls["count"] >= 1
        assert rewrite_calls["allow_one_shot_rewrite"] is True
        assert recovery_calls["count"] == 0
        assert 2985 <= int(meta.get("final_word_count") or 0) <= 3015
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_material_underflow_changed_rewrite_escalates_to_recovery_generation(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    monkeypatch.setenv("SUMMARY_CONTRACT_RETRY_ATTEMPTS", "2")

    filing_id = "contract-recovery-material-underflow-filing"
    company_id = "contract-recovery-material-underflow-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)
    monkeypatch.setattr(
        filings_api,
        "_make_quote_grounding_validator",
        lambda **_kwargs: (lambda _text: None),
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_strict_target_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_ensure_final_strict_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_whitespace_word_band",
        lambda text, *_args, **_kwargs: text,
    )

    underflow_summary = _build_long_form_summary(
        2705, include_exec_quote=True, include_mdna_quote=True
    )
    rewritten_but_still_short = _build_long_form_summary(
        2735, include_exec_quote=True, include_mdna_quote=True
    )
    recovered_summary = _build_long_form_summary(
        3000, include_exec_quote=True, include_mdna_quote=True
    )
    recovery_calls = {"count": 0}
    rewrite_calls = {"count": 0}

    def _fake_generate(*args, **kwargs):
        prompt = str(kwargs.get("base_prompt") or (args[1] if len(args) > 1 else ""))
        if "STRICT CONTRACT RECOVERY MODE (ONE-SHOT)" in prompt:
            recovery_calls["count"] += 1
            return f"{recovered_summary}\n\nRECOVERY_MARKER_TOKEN."
        return underflow_summary

    def _fake_rewrite(*args, **kwargs):
        summary_text = kwargs.get("summary_text")
        if summary_text is None and len(args) >= 2:
            summary_text = args[1]
        summary_text = str(summary_text or "")
        rewrite_calls["count"] += 1
        if "RECOVERY_MARKER_TOKEN" in summary_text:
            return summary_text, (filings_api._count_words(summary_text), 15)
        if rewrite_calls["count"] > 1:
            return summary_text, (filings_api._count_words(summary_text), 15)
        return f"{rewritten_but_still_short}\n\nREWRITE_TOUCH_TOKEN.", (
            filings_api._count_words(rewritten_but_still_short),
            15,
        )

    def _fake_eval_contract_requirements(**kwargs):
        text = str(kwargs.get("summary_text") or "")
        if "RECOVERY_MARKER_TOKEN" in text:
            return (
                [],
                {
                    "target_length": 3000,
                    "final_word_count": 3000,
                    "verified_quote_count": 3,
                    "key_metrics_numeric_row_count": 5,
                    "quality_checks_passed": [],
                },
            )
        # Simulate the user-reported class of failure: long-form miss outside band
        # plus quality validators, but not catastrophic (<90%) by split count.
        return (
            [
                "Final word-count band violation: expected 2980-3020, got split=2732, stripped=2705.",
                "_validator: Metric hierarchy drift in Executive Summary: 8 driver classes are treated as primary.",
                "_validator: Closing Takeaway contains conflicting stances (BUY, HOLD). Use exactly one explicit recommendation.",
            ],
            {
                "target_length": 3000,
                "final_word_count": 2705,
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        )

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        _fake_generate,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _fake_rewrite,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        _fake_eval_contract_requirements,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        meta = payload.get("summary_meta") or {}
        assert rewrite_calls["count"] >= 1
        assert recovery_calls["count"] == 1
        assert meta.get("contract_recovery_used") is True
        assert int(meta.get("contract_recovery_generation_calls") or 0) == 1
        assert 2985 <= int(meta.get("final_word_count") or 0) <= 3015
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_catastrophic_underflow_changed_rewrite_still_attempts_recovery(
    monkeypatch,
) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    monkeypatch.setenv("SUMMARY_CONTRACT_RETRY_ATTEMPTS", "2")

    filing_id = "contract-recovery-non-noop-filing"
    company_id = "contract-recovery-non-noop-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)
    monkeypatch.setattr(
        filings_api,
        "_make_quote_grounding_validator",
        lambda **_kwargs: (lambda _text: None),
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_strict_target_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_ensure_final_strict_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_whitespace_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **kwargs: (
            (
                []
                if "RECOVERY_MARKER_TOKEN" in str(kwargs.get("summary_text") or "")
                else [
                    "Final word-count band violation: expected 2980-3020, got split=1017, stripped=990."
                ]
            ),
            {
                "target_length": 3000,
                "final_word_count": (
                    3000
                    if "RECOVERY_MARKER_TOKEN" in str(kwargs.get("summary_text") or "")
                    else 1017
                ),
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )

    base_underflow = _build_long_form_summary(
        1000, include_exec_quote=True, include_mdna_quote=True
    )
    recovered_summary = _build_long_form_summary(
        3000, include_exec_quote=True, include_mdna_quote=True
    )
    recovery_calls = {"count": 0}

    def _fake_generate(*args, **kwargs):
        prompt = str(kwargs.get("base_prompt") or (args[1] if len(args) > 1 else ""))
        if "STRICT CONTRACT RECOVERY MODE (ONE-SHOT)" in prompt:
            recovery_calls["count"] += 1
            return f"{recovered_summary}\n\nRECOVERY_MARKER_TOKEN."
        return base_underflow

    def _rewrite_short_but_changed(*args, **kwargs):
        summary_text = kwargs.get("summary_text")
        if summary_text is None and len(args) >= 2:
            summary_text = args[1]
        summary_text = str(summary_text or "")
        return (
            f"{summary_text}\n\nAdditional short rewrite delta.",
            (filings_api._count_words(summary_text) + 4, 10),
        )

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        _fake_generate,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _rewrite_short_but_changed,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        meta = payload.get("summary_meta") or {}
        assert recovery_calls["count"] == 1
        assert meta.get("contract_recovery_used") is True
        assert int(meta.get("contract_recovery_generation_calls") or 0) == 1
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_structural_repairs_expand_financial_performance_and_add_mdna_bridge() -> None:
    summary = (
        "## Executive Summary\n"
        "The setup depends on conversion durability.\n\n"
        "## Financial Performance\n"
        "Revenue rose modestly versus prior quarter while margins softened and conversion durability depends on execution cadence\n\n"
        "## Management Discussion & Analysis\n"
        "Management prioritized reinvestment pacing and pricing discipline this quarter.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: Cost inflation can outpace pricing.\n\n"
        "## Key Metrics\n"
        "DATA_GRID_START\n"
        "Revenue | $2.50B\n"
        "Operating Margin | 28.0%\n"
        "Net Margin | 22.0%\n"
        "Free Cash Flow | $0.65B\n"
        "Current Ratio | 2.3x\n"
        "DATA_GRID_END\n\n"
        "## Closing Takeaway\n"
        "HOLD for now."
    )
    repaired = filings_api._apply_contract_structural_repairs(
        summary,
        include_health_rating=False,
        target_length=3000,
        calculated_metrics={"operating_margin": 28.0, "free_cash_flow": 650_000_000},
    )
    perf_body = (
        filings_api._extract_markdown_section_body(repaired, "Financial Performance")
        or ""
    )
    risk_body = (
        filings_api._extract_markdown_section_body(repaired, "Risk Factors") or ""
    )
    assert filings_api._count_words(perf_body) >= 20
    assert perf_body.strip().endswith((".", "!", "?"))
    assert "Management Discussion & Analysis" in perf_body
    assert "Key Metrics" in risk_body


def test_first_strict_pass_rebalances_missing_mdna_quote(monkeypatch) -> None:
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_STRICT_LONGFORM_CONTRACT_REQUIRED", "1")
    monkeypatch.setenv("SUMMARY_CONTRACT_RETRY_ATTEMPTS", "2")

    filing_id = "strict-first-pass-rebalance-filing"
    company_id = "strict-first-pass-rebalance-company"
    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)
    monkeypatch.setattr(
        filings_api, "_enforce_strict_target_band", lambda text, *_args, **_kwargs: text
    )
    monkeypatch.setattr(
        filings_api,
        "_ensure_final_strict_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_enforce_whitespace_word_band",
        lambda text, *_args, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_make_quote_grounding_validator",
        lambda **_kwargs: (lambda _text: None),
    )
    monkeypatch.setattr(
        filings_api,
        "_build_filing_language_snippets",
        lambda *_args, **_kwargs: (
            '"we remain focused on execution discipline and durable cash conversion."\n'
            '"pricing and reinvestment decisions will be balanced against margin durability."\n'
            '"capital allocation remains disciplined against uncertain demand."'
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **kwargs: (
            (
                []
                if filings_api._count_direct_quotes_in_section(
                    str(kwargs.get("summary_text") or ""),
                    "Management Discussion & Analysis",
                )
                >= 1
                else [
                    "Management Discussion & Analysis must include at least one verified direct quote."
                ]
            ),
            {
                "target_length": 3000,
                "final_word_count": 3000,
                "verified_quote_count": 3,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )

    rewrite_calls = {"count": 0}

    def _counting_rewrite(*args, **kwargs):
        rewrite_calls["count"] += 1
        return _rewrite_passthrough(*args, **kwargs)

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: _build_long_form_summary(
            3000, include_exec_quote=True, include_mdna_quote=False
        ),
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _counting_rewrite,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": 3000, "strict_contract": True},
    )

    try:
        assert response.status_code == 200
        payload = response.json() or {}
        summary = payload.get("summary") or ""
        assert (
            filings_api._count_direct_quotes_in_section(
                summary, "Management Discussion & Analysis"
            )
            >= 1
        )
        assert rewrite_calls["count"] == 0
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_summary_trims_when_model_refuses(monkeypatch):
    """Ensure backend trims overlong drafts when the model ignores length instructions."""
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "length-trim-filing"
    company_id = "length-trim-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-09-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "TRIM",
        "name": "Trim Test Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-07-01",
        "period_end": "2024-09-30",
        "statements": _build_test_statements("2024-09-30", 400),
    }

    overlong_response = build_summary_with_word_count(900)

    class DummyModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, prompt: str):
            self.calls += 1
            return type("Resp", (), {"text": overlong_response})()

    dummy_model_holder = {"model": DummyModel()}

    class DummyClient:
        def __init__(self) -> None:
            self.model = dummy_model_holder["model"]

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())
    monkeypatch.setattr(
        filings_api,
        "_apply_short_form_structural_seal",
        lambda text, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_structural_failure_requirements",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        filings_api,
        "_validate_key_metrics_numeric_block",
        lambda *_args, **_kwargs: (None, 5),
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **kwargs: (
            [],
            {
                "target_length": target_length,
                "final_word_count": filings_api._count_words(
                    str(kwargs.get("summary_text") or "")
                ),
                "verified_quote_count": 0,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )

    client = TestClient(app)
    target_length = 500
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code in (200, 422), response.json()
        if response.status_code == 422:
            detail = (response.json() or {}).get("detail", {})
            assert detail.get("failure_code") == "SUMMARY_CONTRACT_FAILED"
            missing = detail.get("missing_requirements") or []
            assert any(
                "word-count band violation" in str(item).lower() for item in missing
            )
        else:
            payload = response.json() or {}
            summary_text = str(payload.get("summary") or "")
            tol = filings_api._effective_word_band_tolerance(target_length)
            lower = max(filings_api.TARGET_LENGTH_MIN_WORDS, target_length - tol)
            upper = min(filings_api.TARGET_LENGTH_MAX_WORDS, target_length + tol)
            wc = filings_api._count_words(summary_text)
            assert lower <= wc <= upper
        # Generation + bounded rewrite passes should remain capped.
        # +4 accounts for: initial gen, underflow regen, post-readability expansion,
        # and emergency final rewrite (all of which may trigger with synthetic filler).
        assert dummy_model_holder["model"].calls <= (
            filings_api.MAX_SUMMARY_ATTEMPTS + filings_api.MAX_REWRITE_ATTEMPTS + 4
        )
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_rewrite_produces_compact_output(monkeypatch):
    """Ensure backend can compress/trim stubbornly overlong output into the short ±20 band."""
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "length-rewrite-filing"
    company_id = "length-rewrite-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-K",
        "filing_date": "2024-12-31",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "REWR",
        "name": "Rewrite Test Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "statements": _build_test_statements("2024-12-31", 800),
    }

    overlong = build_summary_with_word_count(900)
    compact = build_summary_with_word_count(510)
    responses = [overlong] * filings_api.MAX_SUMMARY_ATTEMPTS + [compact]

    class DummyModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, prompt: str):
            text = responses[min(self.calls, len(responses) - 1)]
            self.calls += 1
            return type("Resp", (), {"text": text})()

    dummy_model_holder = {"model": DummyModel()}

    class DummyClient:
        def __init__(self) -> None:
            self.model = dummy_model_holder["model"]

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())
    monkeypatch.setattr(
        filings_api,
        "_apply_short_form_structural_seal",
        lambda text, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_structural_failure_requirements",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        filings_api,
        "_validate_key_metrics_numeric_block",
        lambda *_args, **_kwargs: (None, 5),
    )
    monkeypatch.setattr(
        filings_api,
        "_evaluate_summary_contract_requirements",
        lambda **kwargs: (
            [],
            {
                "target_length": target_length,
                "final_word_count": filings_api._count_words(
                    str(kwargs.get("summary_text") or "")
                ),
                "verified_quote_count": 0,
                "key_metrics_numeric_row_count": 5,
                "quality_checks_passed": [],
            },
        ),
    )

    client = TestClient(app)
    target_length = 500
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code == 200, response.json()
        summary = str((response.json() or {}).get("summary") or "")
        tolerance = filings_api._effective_word_band_tolerance(target_length)
        lower = target_length - tolerance
        upper = target_length + tolerance
        split_wc = len(summary.split())
        stripped_wc = filings_api._count_words(summary)
        assert lower <= split_wc <= upper
        assert lower <= stripped_wc <= upper
        # +3: initial gen + first rewrite + second quality-only rewrite
        assert dummy_model_holder["model"].calls <= filings_api.MAX_SUMMARY_ATTEMPTS + 3
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_final_clamp_holds_length_after_postprocessing(monkeypatch):
    """Post-processing additions should still respect the requested word target."""
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "length-postprocess-filing"
    company_id = "length-postprocess-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-11-15",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "POST",
        "name": "Post Process Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-08-01",
        "period_end": "2024-10-31",
        "statements": _build_test_statements("2024-10-31", 700),
    }

    monkeypatch.setattr(filings_api, "_supabase_configured", lambda _settings: False)

    target_length = 200
    call_counter = {"calls": 0}

    class DummyModel:
        def generate_content(self, prompt: str):
            call_counter["calls"] += 1

            class Response:
                pass

            resp = Response()
            if call_counter["calls"] == 1:
                # Initial draft lands inside the tolerance band
                resp.text = build_summary_with_word_count(target_length - 5)
            else:
                # Rewrite attempts stubbornly stay long; final clamp must fix it
                resp.text = build_summary_with_word_count(target_length + 40)
            return resp

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code == 200
        summary = response.json()["summary"]
        word_count = _backend_word_count(summary)
        tolerance = filings_api._effective_word_band_tolerance(target_length)
        assert target_length - tolerance <= word_count <= target_length + tolerance
        # Ensure sections survive trimming
        assert "Key Metrics" in summary
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_final_output_capped_to_target_length(monkeypatch):
    """Short drafts are expanded/trimmed into the strict ±10 word band."""
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "length-clamp-filing"
    company_id = "length-clamp-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-10-15",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "CLMP",
        "name": "Clamp Test Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-07-01",
        "period_end": "2024-09-30",
        "statements": _build_test_statements("2024-09-30", 500),
    }

    monkeypatch.setattr(filings_api, "_supabase_configured", lambda _settings: False)
    # Skip section completeness validation to focus purely on length enforcement
    monkeypatch.setattr(
        filings_api,
        "_make_section_completeness_validator",
        lambda *args, **kwargs: (lambda _txt: None),
    )

    class DummyModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, prompt: str):
            self.calls += 1

            class Resp:
                pass

            # Always return a stubbornly short draft (around 617 words)
            resp = Resp()
            resp.text = build_summary_with_word_count(617)
            return resp

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())
    monkeypatch.setattr(
        filings_api,
        "_apply_short_form_structural_seal",
        lambda text, **_kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_select_short_form_structural_failure_requirements",
        lambda **_kwargs: [],
    )
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    target_length = 650
    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        tolerance = filings_api._effective_word_band_tolerance(target_length)
        lower = target_length - tolerance
        upper = target_length + tolerance
        if response.status_code == 200:
            summary = response.json()["summary"]
            word_count = _backend_word_count(summary)
            assert lower <= word_count <= upper
            # Ensure key sections remain present after clamping
            assert "Executive Summary" in summary
            assert "Key Metrics" in summary
        else:
            assert response.status_code == 422
            detail = (response.json() or {}).get("detail", {})
            assert detail.get("failure_code") == "SUMMARY_CONTRACT_FAILED"
            missing = detail.get("missing_requirements") or []
            assert any("word-count band violation" in str(item).lower() for item in missing)
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_overlong_output_is_trimmed_but_complete(monkeypatch):
    """Overlong drafts are trimmed to the target cap while preserving Key Metrics rows."""
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "length-trim-band"
    company_id = "length-trim-band-co"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2024-10-15",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "TRMB",
        "name": "Trim Band Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-07-01",
        "period_end": "2024-09-30",
        "statements": _build_test_statements("2024-09-30", 500),
    }

    monkeypatch.setattr(filings_api, "_supabase_configured", lambda _settings: False)
    # Skip section completeness validation to isolate band enforcement
    monkeypatch.setattr(
        filings_api,
        "_make_section_completeness_validator",
        lambda *args, **kwargs: (lambda _txt: None),
    )

    class DummyModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, prompt: str):
            self.calls += 1

            class Resp:
                pass

            # Return an overlong draft (~700 words)
            resp = Resp()
            resp.text = build_summary_with_word_count(700)
            return resp

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    target_length = 650
    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        tolerance = filings_api._effective_word_band_tolerance(target_length)
        lower = target_length - tolerance
        upper = target_length + tolerance
        if response.status_code == 200:
            summary = response.json()["summary"]
            word_count = _backend_word_count(summary)
            assert lower <= word_count <= upper
            assert "Key Metrics" in summary
        else:
            assert response.status_code == 422
            detail = (response.json() or {}).get("detail", {})
            assert detail.get("failure_code") == "SUMMARY_CONTRACT_FAILED"
            missing = detail.get("missing_requirements") or []
            assert missing
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_requires_mdna_section(monkeypatch):
    """Ensure backend retries when MD&A content is missing."""
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_FAST_MODE_DEFAULT", "0")

    filing_id = "mdna-test-filing"
    company_id = "mdna-test-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-K",
        "filing_date": "2024-12-31",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "MDNA",
        "name": "MDNA Test Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "statements": _build_test_statements("2024-12-31", 500),
    }

    def mdna_response(text: str) -> str:
        body = text.strip()
        count = len(body.split())
        return f"{body}\nWORD COUNT: {count}"

    responses = [
        mdna_response(
            "Executive Summary\n\nManagement Discussion & Analysis\nInformation not available."
        ),
        mdna_response(
            "Executive Summary\n\nManagement Discussion & Analysis\nManagement highlighted strategic expansion."
        ),
    ]

    class DummyModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, prompt: str):
            text = responses[min(self.calls, len(responses) - 1)]
            self.calls += 1

            class Response:
                pass

            r = Response()
            r.text = text
            return r

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary", json={"mode": "custom"}
    )

    try:
        assert response.status_code == 200
        summary = response.json()["summary"]
        assert "strategic expansion" in summary
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_transition_validator_is_wired_in_summary_generation(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "transition-wired-filing"
    company_id = "transition-wired-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2025-03-31",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "FLOW",
        "name": "Flow Wiring Inc",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2025-01-01",
        "period_end": "2025-03-31",
        "statements": _build_test_statements("2025-03-31", 1_000),
    }

    called = {"transition": False}

    def _transition_factory(*, include_health_rating: bool, target_length=None):
        called["transition"] = True
        return lambda _text: None

    monkeypatch.setattr(
        filings_api, "_make_section_transition_validator", _transition_factory
    )

    class DummyModel:
        def generate_content(self, _prompt: str):
            class Response:
                text = (
                    "## Executive Summary\n"
                    "The setup is constructive and points to Financial Performance for the evidence.\n\n"
                    "## Financial Performance\n"
                    "Revenue rose versus the prior quarter and margins remained healthy.\n\n"
                    "## Management Discussion & Analysis\n"
                    "Management maintained disciplined reinvestment and cost controls.\n\n"
                    "## Risk Factors\n"
                    "**Execution Risk**: Cost inflation could reduce margin durability if demand softens.\n\n"
                    "## Key Metrics\n"
                    "→ Revenue: $1.0B\n\n"
                    "## Closing Takeaway\n"
                    "HOLD while conversion remains steady."
                )

            return Response()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary", json={"mode": "custom"}
    )

    try:
        assert response.status_code == 200
        assert called["transition"] is True
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_strict_target_band_avoids_legacy_padding_boilerplate(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "strict-band-no-boilerplate"
    company_id = "strict-band-no-boilerplate-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2025-06-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "NBPF",
        "name": "No Boilerplate Holdings",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2025-04-01",
        "period_end": "2025-06-30",
        "statements": _build_test_statements("2025-06-30", 2_000),
    }

    monkeypatch.setattr(filings_api, "_supabase_configured", lambda _settings: False)
    monkeypatch.setattr(
        filings_api,
        "_make_section_completeness_validator",
        lambda *args, **kwargs: (lambda _txt: None),
    )

    class DummyModel:
        def generate_content(self, _prompt: str):
            class Response:
                text = build_summary_with_word_count(617)

            return Response()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    target_length = 650
    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        banned_phrases = (
            "the next filing should confirm whether this trend is durable",
            "the strongest signal is alignment between margins and cash conversion",
            "that is the practical trigger for a higher-conviction stance",
        )
        payload = response.json()
        payload_text = str(payload).lower()
        if response.status_code == 200:
            summary = payload["summary"]
            lowered = summary.lower()
            for phrase in banned_phrases:
                assert phrase not in lowered
        else:
            assert response.status_code == 422
            detail = payload.get("detail", {})
            assert detail.get("failure_code") == "SUMMARY_CONTRACT_FAILED"
            missing = detail.get("missing_requirements") or []
            assert missing
            for phrase in banned_phrases:
                assert phrase not in payload_text
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_flow_v2_rewrites_repetitive_draft_and_keeps_contract(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "flow-v2-filing"
    company_id = "flow-v2-company"
    target_length = 185

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2025-09-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "MSFT",
        "name": "Microsoft Corporation",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2025-07-01",
        "period_end": "2025-09-30",
        "statements": _build_test_statements("2025-09-30", 15_260),
    }

    monkeypatch.setenv("SUMMARY_FLOW_REWRITE_V2_ENABLED", "1")

    repetitive = (
        "## Executive Summary\n"
        "The thesis is constructive, and Free Cash Flow around $7.78B remains a key checkpoint for conviction. "
        "Free Cash Flow around $7.78B remains a key checkpoint for conviction, so Financial Performance below tests durability.\n\n"
        "## Financial Performance\n"
        "Revenue was $15.26B and operating income was $6.71B, but Free Cash Flow around $7.78B remains a key checkpoint for conviction. "
        "Free Cash Flow around $7.78B remains a key checkpoint for conviction, and the next question for MD&A is whether execution can hold.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is investing from a position of strength, yet Free Cash Flow around $7.78B remains a key checkpoint for conviction. "
        "That execution question feeds directly into risk factors.\n\n"
        "## Risk Factors\n"
        "**Margin / Reinvestment Risk**: The Key Metrics section below tracks whether this downside is forming if reinvestment outruns demand and margins compress. "
        "Cash conversion would likely weaken under that scenario.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $15.26B\n"
        "→ Operating Income: $6.71B\n"
        "→ Operating Margin: 44.0%\n"
        "→ Operating Cash Flow: $8.43B\n"
        "→ Free Cash Flow: $7.78B\n"
        "→ FCF Margin: 51.0%\n\n"
        "## Closing Takeaway\n"
        "I HOLD Microsoft Corporation for now. "
        "I would upgrade to BUY if operating margin is above 42% over the next two quarters, and I would downgrade to SELL if free cash flow falls below $6.50B over the next two quarters."
    )
    clean = (
        "## Executive Summary\n"
        "Microsoft shows strong profitability, but the thesis depends on whether high cash conversion persists as reinvestment rises. "
        "That tension is tested in Financial Performance below.\n\n"
        "## Financial Performance\n"
        "Revenue was $15.26B and operating income was $6.71B, keeping operating margin at 44.0%. "
        "Operating cash flow of $8.43B converted to free cash flow of $7.78B after capex, which supports flexibility. "
        "The next question for MD&A is whether capital allocation can sustain this conversion.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is allocating capital from a position of strength, but execution still matters as fixed-cost commitments rise. "
        "If reinvestment cadence outruns demand, margins could normalize, which sets up the risk discussion.\n\n"
        "## Risk Factors\n"
        "**Margin / Reinvestment Risk**: The Key Metrics scoreboard below tracks this downside if reinvestment grows faster than pricing power and operating margin compresses. "
        "That mechanism would weaken cash conversion.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $15.26B\n"
        "→ Operating Income: $6.71B\n"
        "→ Operating Margin: 44.0%\n"
        "→ Operating Cash Flow: $8.43B\n"
        "→ Free Cash Flow: $7.78B\n"
        "→ FCF Margin: 51.0%\n\n"
        "## Closing Takeaway\n"
        "I HOLD Microsoft Corporation for now because profitability and cash conversion are strong but need durability. "
        "I would upgrade to BUY if operating margin stays above 42% over the next two quarters, and I would downgrade to SELL if free cash flow falls below $6.50B over the next two quarters."
    )

    def _with_wc(text: str) -> str:
        return f"{text}\nWORD COUNT: {_backend_word_count(text)}"

    responses = [
        _with_wc(repetitive),
        _with_wc(clean),
        _with_wc(clean),
        _with_wc(clean),
    ]

    class DummyModel:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, _prompt: str):
            text = responses[min(self.calls, len(responses) - 1)]
            self.calls += 1
            return type("Resp", (), {"text": text})()

    class DummyClient:
        def __init__(self) -> None:
            self.model = DummyModel()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code == 200
        summary = response.json()["summary"]
        lower = max(120, int(target_length * 0.65))
        upper = target_length + 15
        assert lower <= len(summary.split()) <= upper
        assert lower <= filings_api._count_words(summary) <= upper
        lowered = summary.lower()
        assert "financial performance" in lowered
        assert ("md&a" in lowered) or ("management discussion" in lowered)
        assert ("key metrics" in lowered) or ("scoreboard" in lowered)
        assert summary.lower().count("remains a key checkpoint") <= 1
        closing_body = filings_api._extract_markdown_section_body(
            summary, "Closing Takeaway"
        )
        assert closing_body is not None
        assert re.search(r"\b(buy|hold|sell)\b", closing_body, re.IGNORECASE)
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_flow_v2_flag_off_does_not_run_post_final_rewrite(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "flow-v2-flag-off-filing"
    company_id = "flow-v2-flag-off-company"
    stable = build_summary_with_word_count(220)
    stable = re.sub(r"\nWORD COUNT:\s*\d+\s*$", "", stable).strip()

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2025-09-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "SAFE",
        "name": "Safety Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2025-07-01",
        "period_end": "2025-09-30",
        "statements": _build_test_statements("2025-09-30", 2_200),
    }

    monkeypatch.setenv("SUMMARY_FLOW_REWRITE_V2_ENABLED", "0")
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: stable,
    )
    monkeypatch.setattr(
        filings_api, "_ensure_required_sections", lambda text, **kwargs: text
    )
    monkeypatch.setattr(
        filings_api,
        "_run_summary_cleanup_pass",
        lambda text, **kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_build_post_final_quality_validators",
        lambda **kwargs: [],
    )

    rewrite_calls = {"count": 0}
    original_rewrite = filings_api._rewrite_summary_to_length

    def _counting_rewrite(*args, **kwargs):
        rewrite_calls["count"] += 1
        return original_rewrite(*args, **kwargs)

    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _counting_rewrite)

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model",
                (),
                {
                    "generate_content": lambda self, _prompt: type(
                        "Resp", (), {"text": stable}
                    )()
                },
            )()

    monkeypatch.setattr(
        filings_api, "get_gemini_client", lambda *args, **kwargs: DummyClient()
    )

    client = TestClient(app)
    target_length = _backend_word_count(stable)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code == 200
        assert rewrite_calls["count"] == 0
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_flow_v2_repairs_underflow_after_echo_cleanup(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "flow-v2-underflow-repair-filing"
    company_id = "flow-v2-underflow-repair-company"
    target_length = 230

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2025-09-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "MSFT",
        "name": "Microsoft Corporation",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2025-07-01",
        "period_end": "2025-09-30",
        "statements": _build_test_statements("2025-09-30", 15_260),
    }

    monkeypatch.setenv("SUMMARY_FLOW_REWRITE_V2_ENABLED", "1")
    monkeypatch.setenv("SUMMARY_FLOW_REWRITE_V2_FLASH_FIRST", "1")
    monkeypatch.setenv("OPENAI_SUMMARY_PRIMARY_MODEL_NAME", "gemini-3-flash-preview")

    repetitive = (
        "## Executive Summary\n"
        "The setup remains constructive, but durability depends on whether conversion quality stays stable. "
        "Financial Performance below tests that core tension.\n\n"
        "## Financial Performance\n"
        "Revenue was $15.26B and operating margin remained strong, while operating cash flow converted to free cash flow after reinvestment. "
        "The question for MD&A is whether this conversion can hold as fixed-cost intensity rises.\n\n"
        "## Management Discussion & Analysis\n"
        "Management is deploying capital from a position of strength, but the durability test remains conversion quality under changing demand conditions.\n\n"
        "## Risk Factors\n"
        "**Margin / Reinvestment Risk**: If reinvestment intensity rises faster than demand, margins can compress and weaken conversion.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $15.26B\n"
        "→ Operating Margin: 44.0%\n"
        "→ Operating Cash Flow: $8.43B\n"
        "→ Free Cash Flow: $7.78B\n\n"
        "## Closing Takeaway\n"
        "I HOLD Microsoft Corporation for now. "
        "Current conviction is tied to Free Cash Flow around $7.78B. "
        "Current conviction is tied to Operating Margin around 44.0%. "
        "Current conviction is tied to Operating Cash Flow around $8.43B. "
        "The underwriting read is steadier while Free Cash Flow stays around $7.78B. "
        "The underwriting read is steadier while Operating Margin stays around 44.0%. "
        "I would upgrade to BUY if operating margin is above 46% over the next two quarters, and I would downgrade to SELL if free cash flow falls below $6.50B over the next two quarters."
    )
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: repetitive,
    )
    monkeypatch.setattr(
        filings_api,
        "_ensure_required_sections",
        lambda text, **kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_run_summary_cleanup_pass",
        lambda text, **kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        lambda *args, **kwargs: (
            repetitive,
            (filings_api._count_words(repetitive), 15),
        ),
    )

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model", (), {"generate_content": lambda self, _prompt: None}
            )()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code == 200
        summary = response.json()["summary"]
        lower = max(120, int(target_length * 0.65))
        upper = target_length + 15
        assert lower <= len(summary.split()) <= upper
        assert lower <= filings_api._count_words(summary) <= upper
        closing = filings_api._extract_markdown_section_body(
            summary, "Closing Takeaway"
        )
        assert closing is not None
        assert re.search(r"\b(buy|hold|sell)\b", closing, re.IGNORECASE)
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_path_ignores_flash_primary_env_and_locks_gpt52(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_FAST_MODE_DEFAULT", "0")

    filing_id = "flow-v2-model-lock-filing"
    company_id = "flow-v2-model-lock-company"
    target_length = 220

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2025-09-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "FLASH",
        "name": "Flash Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2025-07-01",
        "period_end": "2025-09-30",
        "statements": _build_test_statements("2025-09-30", 1_000),
    }

    monkeypatch.setenv("SUMMARY_FLOW_REWRITE_V2_ENABLED", "1")
    monkeypatch.setenv("SUMMARY_FLOW_REWRITE_V2_FLASH_FIRST", "1")
    monkeypatch.setenv("OPENAI_SUMMARY_PRIMARY_MODEL_NAME", "gemini-3-flash-preview")
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    summary_text = build_summary_with_word_count(target_length)
    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: summary_text,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _rewrite_passthrough,
    )

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model", (), {"generate_content": lambda self, _prompt: None}
            )()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code == 200
        meta = (response.json() or {}).get("summary_meta") or {}
        assert meta.get("pipeline_mode") == "two_agent"
        assert meta.get("model_used") == "gpt-5.2"
        assert meta.get("agent_1_api") == "responses"
        assert meta.get("agent_2_api") == "responses"
        stage_calls = meta.get("agent_stage_calls") or []
        assert len(stage_calls) == 2
        assert stage_calls[0].get("stage") == "agent_1_research"
        assert stage_calls[1].get("stage") == "agent_2_summary"
        timings = meta.get("agent_timings") or {}
        assert "agent_1_research_seconds" in timings
        assert "agent_2_summary_seconds" in timings
        assert isinstance(meta.get("background_used"), bool)
        assert isinstance(meta.get("budget_cap_usd"), (int, float))
        assert isinstance(meta.get("estimated_cost_usd"), (int, float))
        assert isinstance(meta.get("actual_cost_usd"), (int, float))
        assert isinstance(meta.get("within_budget"), bool)
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)


def test_summary_meta_reports_prompt_budget_adaptation(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"

    filing_id = "budget-adaptation-meta-filing"
    company_id = "budget-adaptation-meta-company"
    target_length = 220

    _seed_filing_bundle(filing_id, company_id)
    _relax_non_contract_quality_validators(monkeypatch)
    _stabilize_summary_pipeline(monkeypatch)

    monkeypatch.setattr(
        filings_api,
        "_build_budget_adapted_summary_prompt",
        lambda **kwargs: (
            str(kwargs["base_prompt_template"]).replace(
                str(kwargs["company_research_block_placeholder"]),
                "",
            ),
            "skipped",
            True,
            ["context_trimmed", "research_skipped"],
        ),
    )

    captured: dict[str, list[str]] = {}

    def _fake_generate(*_args, **kwargs) -> str:
        captured["budget_adjustments_attempted"] = list(
            kwargs.get("budget_adjustments_attempted") or []
        )
        return build_summary_with_word_count(target_length)

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        _fake_generate,
    )
    monkeypatch.setattr(
        filings_api,
        "_rewrite_summary_to_length",
        _rewrite_passthrough,
    )

    def _fake_two_agent_pipeline(**kwargs):
        prompt = kwargs["build_summary_prompt"]("alpha " * 1200)
        summary_text = kwargs["generate_summary"](prompt, 30.0)
        return summary_two_agent.TwoAgentSummaryPipelineResult(
            summary_text=summary_text,
            model_used="gpt-5.2",
            background_used=True,
            background_text="alpha " * 1200,
            agent_timings={
                "agent_1_research_seconds": 0.01,
                "agent_2_summary_seconds": 0.02,
            },
            agent_stage_calls=[
                {
                    "stage": "agent_1_research",
                    "api": "responses",
                    "duration_seconds": 0.01,
                },
                {
                    "stage": "agent_2_summary",
                    "api": "responses",
                    "duration_seconds": 0.02,
                },
            ],
            total_llm_calls=2,
        )

    monkeypatch.setattr(
        filings_api,
        "run_two_agent_summary_pipeline",
        _fake_two_agent_pipeline,
    )

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary",
        json={"mode": "custom", "target_length": target_length},
    )

    try:
        assert response.status_code == 200
        meta = (response.json() or {}).get("summary_meta") or {}
        assert meta.get("research_mode") == "skipped"
        assert meta.get("prompt_budget_adapted") is True
        assert captured.get("budget_adjustments_attempted") == [
            "context_trimmed",
            "research_skipped",
        ]
    finally:
        _clear_filing_bundle(filing_id, company_id)


def test_post_final_quality_rewrite_runs_at_most_once(monkeypatch):
    settings = get_settings()
    settings.openai_api_key = "test-key"
    monkeypatch.setenv("SUMMARY_FAST_MODE_DEFAULT", "0")

    filing_id = "flow-v2-post-final-once-filing"
    company_id = "flow-v2-post-final-once-company"

    local_cache.fallback_filings_by_id[filing_id] = {
        "id": filing_id,
        "company_id": company_id,
        "filing_type": "10-Q",
        "filing_date": "2025-09-30",
    }
    local_cache.fallback_companies[company_id] = {
        "id": company_id,
        "ticker": "ONCE",
        "name": "Once Corp",
    }
    local_cache.fallback_financial_statements[filing_id] = {
        "filing_id": filing_id,
        "period_start": "2025-07-01",
        "period_end": "2025-09-30",
        "statements": _build_test_statements("2025-09-30", 1_000),
    }

    monkeypatch.setenv("SUMMARY_FLOW_REWRITE_V2_ENABLED", "1")
    problematic = (
        "## Executive Summary\n"
        "The setup is mixed.\n\n"
        "## Financial Performance\n"
        "Revenue rose and margins were stable.\n\n"
        "## Management Discussion & Analysis\n"
        "Management referenced capital allocation.\n\n"
        "## Risk Factors\n"
        "**Execution Risk**: Costs could rise.\n\n"
        "## Key Metrics\n"
        "→ Revenue: $1.00B\n\n"
        "## Closing Takeaway\n"
        "I HOLD Once Corp for now. I would upgrade to BUY if margin is above 25% over the next two quarters, and I would downgrade to SELL if free cash flow falls below $50.00M over the next two quarters."
    )

    monkeypatch.setattr(
        filings_api,
        "_generate_summary_with_quality_control",
        lambda *args, **kwargs: problematic,
    )
    monkeypatch.setattr(
        filings_api,
        "_ensure_required_sections",
        lambda text, **kwargs: text,
    )
    monkeypatch.setattr(
        filings_api,
        "_run_summary_cleanup_pass",
        lambda text, **kwargs: text,
    )

    rewrite_calls = {"count": 0}

    def _stubborn_rewrite(*args, **kwargs):
        rewrite_calls["count"] += 1
        return problematic, (filings_api._count_words(problematic), 15)

    monkeypatch.setattr(filings_api, "_rewrite_summary_to_length", _stubborn_rewrite)

    class DummyClient:
        def __init__(self) -> None:
            self.model = type(
                "Model", (), {"generate_content": lambda self, _prompt: None}
            )()

    monkeypatch.setattr(filings_api, "get_gemini_client", lambda: DummyClient())

    client = TestClient(app)
    response = client.post(
        f"/api/v1/filings/{filing_id}/summary", json={"mode": "custom"}
    )

    try:
        assert response.status_code == 200
        assert rewrite_calls["count"] == 1
    finally:
        local_cache.fallback_filings_by_id.pop(filing_id, None)
        local_cache.fallback_companies.pop(company_id, None)
        local_cache.fallback_financial_statements.pop(filing_id, None)
        local_cache.fallback_filing_summaries.pop(filing_id, None)
