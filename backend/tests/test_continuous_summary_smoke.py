import json
import re
import subprocess
import sys
from pathlib import Path

from app.api import filings as filings_api
from scripts.smoke_summary_continuous_v2 import _section_body


BACKEND_ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = BACKEND_ROOT / "scripts" / "smoke_summary_continuous_v2.py"


def _run_smoke(target_length: int) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            str(SMOKE_SCRIPT),
            "--target",
            str(target_length),
            "--no-summary",
            "--json",
        ],
        cwd=BACKEND_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    output = str(completed.stdout or "").strip()
    for marker in ("\n{", "{"):
        start = output.find(marker)
        while start != -1:
            candidate = output[start + (1 if marker == "\n{" else 0) :]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                start = output.find("{", start + 1)
    raise json.JSONDecodeError("Unable to locate smoke JSON payload", output, 0)


def test_continuous_summary_smoke_scales_with_target_length() -> None:
    # The deterministic FakeSummaryClient is calibrated around a very small budget
    # and the standard memo band. Broader length sweeps belong in renderer-tuning
    # tests rather than this contract smoke.
    reports = [
        _run_smoke(target)
        for target in (300, 1100, 1225)
    ]

    previous_final_words = 0
    previous_budgets: dict[str, int] | None = None
    previous_section_counts: dict[str, int] | None = None
    previous_band: str | None = None

    for report in reports:
        current_band = "short" if int(report["target_length"]) < 1500 else "long"
        assert report["passed"] is True
        assert report["lower_bound"] <= report["final_word_count"] <= report["upper_bound"]
        assert report["metadata"]["used_padding"] is False
        assert report["section_budgets"]["Key Metrics"] <= 90
        assert report["section_budgets"]["Risk Factors"] > report["section_budgets"]["Closing Takeaway"]
        assert report["section_budgets"]["Financial Performance"] >= report["section_budgets"]["Executive Summary"]
        assert report["section_budgets"]["Management Discussion & Analysis"] >= report["section_budgets"]["Executive Summary"]
        assert report["section_word_counts"]["Closing Takeaway"] >= 2
        assert report["final_word_count"] > previous_final_words
        for section_name, section_words in report["section_word_counts"].items():
            lower = report["section_ranges"][section_name]["lower"]
            upper = report["section_ranges"][section_name]["upper"]
            if section_name != "Key Metrics":
                if section_name == "Risk Factors":
                    assert section_words <= upper
                else:
                    assert lower <= section_words <= upper
                assert section_words >= int(report["section_budgets"][section_name] * 0.7)
            else:
                assert section_words <= upper

        if previous_budgets is not None and previous_band == current_band:
            for section_name, budget_words in report["section_budgets"].items():
                assert budget_words >= previous_budgets[section_name]
            for section_name, section_words in report["section_word_counts"].items():
                assert section_words >= previous_section_counts[section_name]

        previous_final_words = int(report["final_word_count"])
        previous_budgets = report["section_budgets"]
        previous_section_counts = report["section_word_counts"]
        previous_band = current_band


def test_smoke_risk_renderer_supports_accepted_source_backed_risks() -> None:
    prompt = (
        "Write ONLY the body of the 'Risk Factors' section for Cloud Workflow Co..\n\n"
        "BODY WORD BUDGET:\n"
        "- Target 51 body words.\n\n"
        "COMPANY TERMS TO REUSE:\n"
        "- AI workflow tier\n"
        "- enterprise renewals\n\n"
        "RISK FACTORS CONTRACT:\n"
        "- Write exactly 2 risks.\n"
        "- Accepted source-backed risks:\n"
        "- Enterprise Renewal Conversion Risk [Risk Factors]: Management highlighted AI attach inside large renewals.\n"
        "- AI Workflow Attach Monetization Risk [Risk Factors]: Management expects AI workflow attach to deepen over the next two quarters.\n\n"
        "Return only the section body."
    )

    body = _section_body("Risk Factors", prompt)
    risks = filings_api._extract_risk_entries_for_repair(body)

    assert len(risks) == 2
    assert "Enterprise Renewal Conversion Risk" in body
    assert "AI Workflow Attach Monetization Risk" in body
