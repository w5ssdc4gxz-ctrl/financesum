import json
import subprocess
import sys
from pathlib import Path


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
    return json.loads(completed.stdout)


def test_continuous_summary_smoke_scales_with_target_length() -> None:
    reports = [_run_smoke(target) for target in (700, 1400, 2600)]

    previous_final_words = 0
    previous_budgets: dict[str, int] | None = None
    previous_section_counts: dict[str, int] | None = None

    for report in reports:
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
                assert lower <= section_words <= upper
                assert section_words >= int(report["section_budgets"][section_name] * 0.7)
            else:
                assert section_words <= upper

        if previous_budgets is not None:
            for section_name, budget_words in report["section_budgets"].items():
                assert budget_words >= previous_budgets[section_name]
            for section_name, section_words in report["section_word_counts"].items():
                assert section_words >= previous_section_counts[section_name]

        previous_final_words = int(report["final_word_count"])
        previous_budgets = report["section_budgets"]
        previous_section_counts = report["section_word_counts"]
