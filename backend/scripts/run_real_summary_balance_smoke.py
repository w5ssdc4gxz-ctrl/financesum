#!/usr/bin/env python3
"""Run a real Continuous V2 balance smoke against the backend summary route."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.summary_post_processor import validate_summary
from app.services.word_surgery import count_words
from fastapi.testclient import TestClient
from app.api.auth import get_current_user
from app.main import app


def _extract_section_word_counts(summary_text: str, section_budgets: dict[str, int]) -> dict[str, int]:
    import re

    counts: dict[str, int] = {}
    for section_name in section_budgets:
        match = re.search(
            rf"##\s+{re.escape(section_name)}\s*\n(.*?)(?=\n##\s+|\Z)",
            summary_text or "",
            re.DOTALL,
        )
        counts[section_name] = count_words((match.group(1) if match else "").strip())
    return counts


def _post_json(url: str, payload: dict[str, Any], auth_token: str | None) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            **(
                {"Authorization": f"Bearer {auth_token}"}
                if auth_token
                else {}
            ),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            status = int(getattr(response, "status", 200) or 200)
            body = json.loads(response.read().decode("utf-8"))
            return status, body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"detail": raw}
        return int(exc.code), body


def _post_json_in_process(filing_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    original = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = lambda: type("User", (), {"id": "smoke-user"})()
    try:
        client = TestClient(app)
        response = client.post(f"/api/v1/filings/{filing_id}/summary", json=payload)
        return int(response.status_code), dict(response.json() or {})
    finally:
        if original is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = original


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--filing-id", required=True)
    parser.add_argument("--target", required=True, type=int)
    parser.add_argument("--base-url")
    parser.add_argument("--auth-token")
    parser.add_argument("--in-process", action="store_true")
    args = parser.parse_args()

    payload = {"mode": "custom", "target_length": int(args.target)}
    if args.in_process or not args.base_url:
        status_code, body = _post_json_in_process(args.filing_id, payload)
    else:
        url = f"{args.base_url.rstrip('/')}/api/v1/filings/{args.filing_id}/summary"
        status_code, body = _post_json(url, payload, args.auth_token)

    if status_code != 200:
        print(
            json.dumps(
                {
                    "passed": False,
                    "status_code": status_code,
                    "detail": body.get("detail"),
                },
                indent=2,
            )
        )
        return 1

    summary_text = str(body.get("summary") or "")
    summary_meta = dict(body.get("summary_meta") or {})
    section_budgets = dict(summary_meta.get("section_word_budgets") or {})
    section_word_counts = _extract_section_word_counts(summary_text, section_budgets)
    validation = validate_summary(
        summary_text,
        target_words=int(args.target),
        section_budgets=section_budgets,
        include_health_rating="Financial Health Rating" in section_budgets,
        risk_factors_excerpt="",
    )

    report = {
        "passed": bool(validation.passed),
        "status_code": status_code,
        "target_length": int(args.target),
        "total_word_count": int(count_words(summary_text)),
        "total_band": {
            "lower": int(validation.lower_bound),
            "upper": int(validation.upper_bound),
        },
        "section_budgets": section_budgets,
        "section_ranges": dict(summary_meta.get("section_word_ranges") or {}),
        "section_word_counts": section_word_counts,
        "violations": list(validation.global_failures)
        + [failure.message for failure in validation.section_failures],
        "summary_meta": {
            "pipeline_mode": summary_meta.get("pipeline_mode"),
            "section_validation_passed": summary_meta.get("section_validation_passed"),
            "repair_attempts": summary_meta.get("repair_attempts"),
        },
    }
    print(json.dumps(report, indent=2))
    return 0 if validation.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
