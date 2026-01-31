"""Tests for the 2-pass KPI extraction pipeline."""

import json
from typing import Any, Dict, Optional

import pytest

from app.services.spotlight_kpi.kpi_pipeline_2pass import (
    extract_kpi_2pass,
    extract_kpi_2pass_from_text,
    Pipeline2PassConfig,
    _fuzzy_quote_in_context,
    _coerce_number,
    _extract_number_from_excerpt,
)
from app.services.spotlight_kpi.regex_fallback import (
    extract_kpis_with_regex,
    extract_single_best_kpi_with_regex,
)


class FakeGeminiClient:
    """Fake Gemini client for testing the 2-pass pipeline."""

    def __init__(
        self,
        *,
        pass1_candidates: Optional[list] = None,
        pass2_status: str = "approved",
        pass2_selected: Optional[Dict[str, Any]] = None,
        should_fail_upload: bool = False,
        should_fail_pass1: bool = False,
        should_fail_pass2: bool = False,
    ):
        self.pass1_candidates = pass1_candidates
        self.pass2_status = pass2_status
        self.pass2_selected = pass2_selected
        self.should_fail_upload = should_fail_upload
        self.should_fail_pass1 = should_fail_pass1
        self.should_fail_pass2 = should_fail_pass2
        self.calls = []

    def upload_file_bytes(
        self,
        *,
        data: bytes,
        mime_type: str,
        display_name: str,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        self.calls.append("upload_file_bytes")
        if self.should_fail_upload:
            raise Exception("Upload failed")
        return {"uri": "gs://fake/file.pdf", "mimeType": mime_type}

    def stream_generate_content_with_file_uri(
        self,
        *,
        file_uri: str,
        file_mime_type: str,
        prompt: str,
        stage_name: str,
        expected_tokens: int,
        generation_config_override: Dict[str, Any],
        timeout_seconds: float,
    ) -> str:
        self.calls.append(f"stream_generate_content_with_file_uri:{stage_name}")
        if self.should_fail_pass1:
            raise Exception("Pass 1 failed")
        
        # Return Pass 1 response
        candidates = self.pass1_candidates or [
            {
                "name": "Monthly Active Users (MAUs)",
                "value": 250000000,
                "unit": "users",
                "excerpt": "We ended the quarter with 250 million Monthly Active Users (MAUs).",
                "why_company_specific": "Core user engagement metric",
            }
        ]
        return json.dumps({"candidates": candidates})

    def stream_generate_content(
        self,
        prompt: str,
        stage_name: str = "Generating",
        expected_tokens: int = 4000,
        generation_config_override: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[float] = None,
        retry: bool = True,
    ) -> str:
        self.calls.append(f"stream_generate_content:{stage_name}")
        if self.should_fail_pass2:
            raise Exception("Pass 2 failed")
        
        # Return Pass 2 response
        selected = self.pass2_selected or {
            "name": "Monthly Active Users (MAUs)",
            "value": 250000000,
            "unit": "users",
            "excerpt": "We ended the quarter with 250 million Monthly Active Users (MAUs).",
            "what_it_measures": "User engagement and platform scale",
            "why_selected": "Most representative metric for this company",
            "scores": {"uniqueness": 5, "representativeness": 5, "verifiability": 5},
        }
        return json.dumps({
            "selected_kpi": selected,
            "verification": {
                "status": self.pass2_status,
                "reason": "Verified",
                "confidence": 0.95,
            },
        })


class TestFuzzyQuoteMatching:
    """Tests for fuzzy quote matching."""

    def test_exact_match(self):
        quote = "We ended the quarter with 250 million MAUs."
        context = "Our results were strong. We ended the quarter with 250 million MAUs. Growth continues."
        assert _fuzzy_quote_in_context(quote, context) is True

    def test_whitespace_normalized(self):
        quote = "We   ended  the quarter   with 250 million MAUs"
        context = "We ended the quarter with 250 million MAUs."
        assert _fuzzy_quote_in_context(quote, context) is True

    def test_case_insensitive(self):
        quote = "WE ENDED THE QUARTER WITH 250 MILLION MAUS"
        context = "we ended the quarter with 250 million maus."
        assert _fuzzy_quote_in_context(quote, context) is True

    def test_word_matching_threshold(self):
        quote = "We ended the quarter with approximately 250 million MAUs and growing"
        context = "We ended quarter with 250 million MAUs growing rapidly."
        # Should match because 70%+ of words appear
        assert _fuzzy_quote_in_context(quote, context, threshold=0.6) is True

    def test_no_match(self):
        quote = "Completely different text about something else"
        context = "We ended the quarter with 250 million MAUs."
        assert _fuzzy_quote_in_context(quote, context) is False

    def test_short_quote_direct_match(self):
        quote = "250 million MAUs"
        context = "We reported 250 million MAUs this quarter."
        assert _fuzzy_quote_in_context(quote, context) is True


class TestNumberParsing:
    """Tests for number parsing and extraction."""

    def test_coerce_number_integer(self):
        assert _coerce_number(123456) == 123456.0

    def test_coerce_number_float(self):
        assert _coerce_number(123.456) == 123.456

    def test_coerce_number_string_simple(self):
        assert _coerce_number("123456") == 123456.0

    def test_coerce_number_string_with_commas(self):
        assert _coerce_number("1,234,567") == 1234567.0

    def test_coerce_number_billions(self):
        assert _coerce_number("2.5B") == 2_500_000_000.0
        assert _coerce_number("2.5 billion") == 2_500_000_000.0

    def test_coerce_number_millions(self):
        assert _coerce_number("250M") == 250_000_000.0
        assert _coerce_number("250 million") == 250_000_000.0

    def test_coerce_number_thousands(self):
        assert _coerce_number("500K") == 500_000.0
        assert _coerce_number("500 thousand") == 500_000.0

    def test_coerce_number_with_dollar_sign(self):
        assert _coerce_number("$1,234.56") == 1234.56

    def test_coerce_number_negative_parens(self):
        assert _coerce_number("(1,234)") == -1234.0

    def test_extract_number_from_excerpt_simple(self):
        excerpt = "We reported 250 million MAUs."
        assert _extract_number_from_excerpt(excerpt) == 250_000_000.0

    def test_extract_number_from_excerpt_with_commas(self):
        excerpt = "Revenue was $1,234,567,890."
        assert _extract_number_from_excerpt(excerpt) == 1234567890.0

    def test_extract_number_from_excerpt_with_b_suffix(self):
        excerpt = "GMV reached $5.2B in the quarter."
        assert _extract_number_from_excerpt(excerpt) == 5_200_000_000.0

    def test_extract_number_skips_years(self):
        excerpt = "In 2024, we reported 250 million MAUs."
        # Should pick 250 million, not 2024
        assert _extract_number_from_excerpt(excerpt) == 250_000_000.0

    def test_extract_number_prefers_larger(self):
        excerpt = "We have 3 product lines with 250 million users."
        # Should pick 250 million over 3
        assert _extract_number_from_excerpt(excerpt) == 250_000_000.0


class TestRegexFallback:
    """Tests for regex-based KPI extraction."""

    def test_extracts_mau(self):
        text = "We ended the quarter with 250 million Monthly Active Users (MAUs)."
        candidates = extract_kpis_with_regex(text, "Example Corp")
        assert len(candidates) >= 1
        assert candidates[0]["name"] == "Monthly Active Users (MAUs)"
        assert candidates[0]["value"] == 250_000_000.0

    def test_extracts_gmv(self):
        text = "Gross Merchandise Volume (GMV) reached $15.2 billion in Q4."
        candidates = extract_kpis_with_regex(text, "Example Corp")
        assert len(candidates) >= 1
        assert any(c["name"] == "Gross Merchandise Volume (GMV)" for c in candidates)

    def test_extracts_arr(self):
        text = "Annual Recurring Revenue (ARR) of $2.5 billion."
        candidates = extract_kpis_with_regex(text, "Example Corp")
        assert len(candidates) >= 1
        assert any(c["name"] == "Annual Recurring Revenue (ARR)" for c in candidates)

    def test_extracts_vehicles_delivered(self):
        text = "Tesla delivered 435,000 vehicles in Q4 2024."
        candidates = extract_kpis_with_regex(text, "Tesla")
        assert len(candidates) >= 1
        # Should find some delivery-related metric

    def test_extracts_subscribers(self):
        text = "Netflix added 10 million paid subscribers, reaching 250 million total."
        candidates = extract_kpis_with_regex(text, "Netflix")
        assert len(candidates) >= 1
        assert any("Subscriber" in c["name"] for c in candidates)

    def test_no_duplicates(self):
        text = "MAUs of 100 million. Monthly Active Users reached 100 million."
        candidates = extract_kpis_with_regex(text, "Example Corp")
        mau_count = sum(1 for c in candidates if "MAU" in c["name"] or "Monthly Active" in c["name"])
        assert mau_count == 1  # Should dedupe

    def test_single_best(self):
        text = "We have 250 million MAUs and $5B in GMV."
        kpi = extract_single_best_kpi_with_regex(text, "Example Corp")
        assert kpi is not None
        # MAU should be highest priority
        assert kpi["name"] == "Monthly Active Users (MAUs)"


class TestPipeline2Pass:
    """Tests for the 2-pass pipeline."""

    def test_successful_extraction(self):
        client = FakeGeminiClient()
        config = Pipeline2PassConfig(total_timeout_seconds=60)
        
        kpi, debug = extract_kpi_2pass(
            client,
            file_bytes=b"%PDF-fake",
            company_name="Example Corp",
            mime_type="application/pdf",
            config=config,
        )
        
        assert kpi is not None
        assert kpi["name"] == "Monthly Active Users (MAUs)"
        assert kpi["value"] == 250_000_000.0
        assert debug["mode"] == "kpi_pipeline_2pass"
        assert "upload_file_bytes" in client.calls
        assert any("Pass 1" in c for c in client.calls)
        assert any("Pass 2" in c for c in client.calls)

    def test_fallback_to_pass1_when_pass2_fails(self):
        client = FakeGeminiClient(should_fail_pass2=True)
        config = Pipeline2PassConfig(total_timeout_seconds=60)
        
        kpi, debug = extract_kpi_2pass(
            client,
            file_bytes=b"%PDF-fake",
            company_name="Example Corp",
            mime_type="application/pdf",
            config=config,
        )
        
        assert kpi is not None
        assert debug.get("fallback_to_pass1") is True

    def test_fallback_to_pass1_when_verification_rejected(self):
        client = FakeGeminiClient(pass2_status="rejected")
        config = Pipeline2PassConfig(total_timeout_seconds=60)
        
        kpi, debug = extract_kpi_2pass(
            client,
            file_bytes=b"%PDF-fake",
            company_name="Example Corp",
            mime_type="application/pdf",
            config=config,
        )
        
        assert kpi is not None
        assert debug.get("verification_status") == "rejected"
        assert debug.get("fallback_to_pass1") is True

    def test_no_gemini_client(self):
        config = Pipeline2PassConfig()
        
        kpi, debug = extract_kpi_2pass(
            None,
            file_bytes=b"%PDF-fake",
            company_name="Example Corp",
            mime_type="application/pdf",
            config=config,
        )
        
        assert kpi is None
        assert debug["reason"] == "no_gemini_client"

    def test_no_file_bytes(self):
        client = FakeGeminiClient()
        config = Pipeline2PassConfig()
        
        kpi, debug = extract_kpi_2pass(
            client,
            file_bytes=b"",
            company_name="Example Corp",
            mime_type="application/pdf",
            config=config,
        )
        
        assert kpi is None
        assert debug["reason"] == "no_file_bytes"

    def test_regex_fallback_on_upload_failure(self):
        client = FakeGeminiClient(should_fail_upload=True)
        config = Pipeline2PassConfig(enable_regex_fallback=True)
        context_text = "We have 250 million Monthly Active Users (MAUs)."
        
        kpi, debug = extract_kpi_2pass(
            client,
            file_bytes=b"%PDF-fake",
            company_name="Example Corp",
            mime_type="application/pdf",
            context_text=context_text,
            config=config,
        )
        
        assert kpi is not None
        assert debug.get("regex_fallback_attempted") is True
        assert debug.get("regex_fallback_success") is True

    def test_regex_fallback_disabled(self):
        client = FakeGeminiClient(should_fail_upload=True)
        config = Pipeline2PassConfig(enable_regex_fallback=False)
        
        kpi, debug = extract_kpi_2pass(
            client,
            file_bytes=b"%PDF-fake",
            company_name="Example Corp",
            mime_type="application/pdf",
            config=config,
        )
        
        assert kpi is None
        assert debug["reason"] == "upload_failed"


class TestPipeline2PassText:
    """Tests for the text-only 2-pass pipeline."""

    def test_successful_text_extraction(self):
        client = FakeGeminiClient()
        config = Pipeline2PassConfig(total_timeout_seconds=60)
        context_text = "We ended the quarter with 250 million Monthly Active Users (MAUs)."
        
        kpi, debug = extract_kpi_2pass_from_text(
            client,
            context_text=context_text,
            company_name="Example Corp",
            config=config,
        )
        
        assert kpi is not None
        assert debug["mode"] == "kpi_pipeline_2pass_text"

    def test_no_context_text(self):
        client = FakeGeminiClient()
        config = Pipeline2PassConfig()
        
        kpi, debug = extract_kpi_2pass_from_text(
            client,
            context_text="",
            company_name="Example Corp",
            config=config,
        )
        
        assert kpi is None
        assert debug["reason"] == "no_context_text"

    def test_regex_fallback_on_ai_failure(self):
        client = FakeGeminiClient(should_fail_pass1=True)
        config = Pipeline2PassConfig(enable_regex_fallback=True)
        context_text = "We have 250 million Monthly Active Users (MAUs) this quarter."
        
        kpi, debug = extract_kpi_2pass_from_text(
            client,
            context_text=context_text,
            company_name="Example Corp",
            config=config,
        )
        
        assert kpi is not None
        assert debug.get("regex_fallback_attempted") is True


class TestIndustrySpecificKPIs:
    """Tests for industry-specific KPI extraction via regex."""

    def test_tech_mau_dau(self):
        text = "Facebook reports 2.9 billion MAUs and 1.9 billion DAUs."
        candidates = extract_kpis_with_regex(text, "Meta Platforms")
        names = [c["name"] for c in candidates]
        assert "Monthly Active Users (MAUs)" in names
        assert "Daily Active Users (DAUs)" in names

    def test_ecommerce_gmv(self):
        text = "Shopify merchants generated $61 billion in Gross Merchandise Volume."
        candidates = extract_kpis_with_regex(text, "Shopify")
        assert any(c["name"] == "Gross Merchandise Volume (GMV)" for c in candidates)
        gmv = next(c for c in candidates if "GMV" in c["name"])
        assert gmv["value"] == 61_000_000_000.0

    def test_saas_arr_nrr(self):
        text = "Our ARR reached $1.5 billion with Net Revenue Retention of 120%."
        candidates = extract_kpis_with_regex(text, "Snowflake")
        names = [c["name"] for c in candidates]
        assert "Annual Recurring Revenue (ARR)" in names
        assert "Net Revenue Retention (NRR)" in names
        nrr = next(c for c in candidates if "NRR" in c["name"])
        assert nrr["value"] == 120.0

    def test_fintech_tpv(self):
        text = "Total Payment Volume reached $376 billion in the quarter."
        candidates = extract_kpis_with_regex(text, "PayPal")
        assert any(c["name"] == "Total Payment Volume (TPV)" for c in candidates)

    def test_manufacturing_vehicles(self):
        text = "We delivered 484,507 vehicles in Q4."
        candidates = extract_kpis_with_regex(text, "Tesla")
        # Should find vehicles delivered
        assert len(candidates) >= 1

    def test_retail_stores(self):
        text = "We operated 9,265 stores at quarter end."
        candidates = extract_kpis_with_regex(text, "Starbucks")
        assert any("Store" in c["name"] for c in candidates)

    def test_streaming_subscribers(self):
        text = "Global paid subscribers reached 260.8 million."
        candidates = extract_kpis_with_regex(text, "Netflix")
        assert any("Subscriber" in c["name"] for c in candidates)

    def test_asset_management_aum(self):
        text = "Assets Under Management totaled $9.1 trillion."
        candidates = extract_kpis_with_regex(text, "BlackRock")
        assert any("AUM" in c["name"] for c in candidates)
        aum = next(c for c in candidates if "AUM" in c["name"])
        assert aum["value"] == 9.1e12
