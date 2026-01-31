from __future__ import annotations

import pytest

from app.config import get_settings
from app.services.spotlight_kpi import service as spotlight_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_percent_normalization_scales_decimal_percent_values():
    kpi = {
        "name": "Net Revenue Retention (NRR)",
        "unit": "%",
        "value": 0.42,
        "prior_value": 0.4,
        "history": [
            {"period_label": "Q1 2025", "value": 0.41},
            {"period_label": "Q2 2025", "value": 0.42},
        ],
    }
    spotlight_service._normalize_spotlight_kpi_percent(kpi)
    assert kpi["value"] == pytest.approx(42.0)
    assert kpi["prior_value"] == pytest.approx(40.0)
    assert kpi["history"][0]["value"] == pytest.approx(41.0)


def test_segment_sanitizer_rejects_garbage_labels():
    segments = [
        {"label": "Total", "value": 100},
        {"label": "", "value": 50},
        {"label": "Other", "value": 25},
    ]
    assert spotlight_service._sanitize_segments(segments, company_name="Example Corp") is None


@pytest.mark.anyio
async def test_service_regex_fallback_extracts_kpi_when_no_gemini(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("SPOTLIGHT_CACHE_TTL_SECONDS", "0")
    get_settings.cache_clear()

    doc_path = tmp_path / "filing.html"
    doc_path.write_text(
        "MANAGEMENT DISCUSSION & ANALYSIS\nWe ended the quarter with 250 million MAUs.\n",
        encoding="utf-8",
    )

    payload = await spotlight_service.build_spotlight_payload_for_filing(
        "svc-regex-filing",
        filing={
            "id": "svc-regex-filing",
            "filing_type": "10-Q",
            "filing_date": "2026-01-01",
            "period_end": "2025-12-31",
        },
        company={"id": "svc-company", "name": "Example Corp"},
        local_document_path=doc_path,
        settings=get_settings(),
        context_source="fallback",
        debug=False,
    )

    kpi = payload.get("company_kpi")
    assert isinstance(kpi, dict)
    assert str(kpi.get("name") or "").startswith("Monthly Active Users")
    assert float(kpi.get("value")) == 250_000_000.0
    assert kpi.get("company_specific") is True
    assert kpi.get("source_filing_id") == "svc-regex-filing"
    assert isinstance(kpi.get("period_label"), str) and kpi.get("period_label")


@pytest.mark.anyio
async def test_service_no_kpi_when_only_generic_financials(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()

    doc_path = tmp_path / "filing.txt"
    doc_path.write_text(
        "Revenue was $1.2B. Net income was $0.1B.\n",
        encoding="utf-8",
    )

    payload = await spotlight_service.build_spotlight_payload_for_filing(
        "svc-no-kpi",
        filing={"id": "svc-no-kpi", "filing_type": "10-Q", "filing_date": "2026-01-01"},
        company={"id": "svc-company", "name": "Example Corp"},
        local_document_path=doc_path,
        settings=get_settings(),
        context_source="fallback",
        debug=False,
    )

    assert payload.get("company_kpi") is None
    assert payload.get("status") == "no_kpi"
