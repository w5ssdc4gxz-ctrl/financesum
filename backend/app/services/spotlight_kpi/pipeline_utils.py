from __future__ import annotations

import json
from typing import Any, Dict, List


def compact_json(data: Any, *, max_chars: int) -> str:
    try:
        dumped = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except Exception:  # noqa: BLE001
        dumped = str(data)
    if max_chars > 0 and len(dumped) > max_chars:
        return dumped[:max_chars].rstrip() + "…"
    return dumped


def safe_json_retry_prompt(schema_name: str, *, bad_output: str) -> str:
    bad = (bad_output or "")[:1800]
    return (
        f"Your previous output was invalid JSON for {schema_name}.\n"
        "Return STRICT JSON ONLY (no code fences, no commentary).\n\n"
        f"PREVIOUS OUTPUT (truncated):\n{bad}"
    )


def thinking_config(level: str) -> Dict[str, Any]:
    lvl = str(level or "").strip().lower()
    if lvl not in ("minimal", "low", "medium", "high"):
        lvl = "low"
    return {"thinkingConfig": {"thinkingLevel": lvl}}


def make_text_slices(text: str, *, max_chars: int, max_slices: int = 2) -> List[str]:
    s = (text or "").strip()
    if not s:
        return []
    if len(s) <= max_chars:
        return [s]

    head = s[:max_chars].rstrip()
    mid_start = max(0, (len(s) // 2) - (max_chars // 2))
    mid = s[mid_start : mid_start + max_chars].rstrip()
    tail = s[-max_chars:].lstrip()

    # Prefer mid/tail for KPI tables in long filings; head is often boilerplate/ToC.
    slices: List[str] = []
    for sl in (mid, tail, head):
        if sl and sl not in slices:
            slices.append(sl)
    return slices[: max(1, int(max_slices))]

