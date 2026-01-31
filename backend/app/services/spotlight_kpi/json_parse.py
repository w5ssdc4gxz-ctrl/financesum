from __future__ import annotations

import json
import re
import ast
from typing import Any, Dict, Optional


def _strip_code_fences(text: str) -> str:
    if not text:
        return ""
    stripped = text.strip()
    # ```json ... ```
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _find_json_object(text: str) -> Optional[str]:
    """Best-effort extraction of the first top-level JSON object.

    Gemini sometimes returns:
    - A valid JSON object plus additional text, and/or
    - Multiple brace-delimited blocks (e.g., "note: {...}") after the JSON.

    We prefer the first *balanced* JSON object starting at the first '{' to
    avoid accidentally spanning across unrelated trailing braces.
    """
    if not text:
        return None
    t = text.strip()

    def _balanced_from(start: int) -> Optional[str]:
        if start < 0 or start >= len(t) or t[start] != "{":
            return None
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    continue
                if ch == '"':
                    in_str = False
                continue

            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return t[start : i + 1]
        return None

    # Fast path: starts with an object.
    if t.startswith("{"):
        obj = _balanced_from(0)
        if obj:
            return obj

    # General path: find the first balanced object anywhere in the output.
    first = t.find("{")
    if first == -1:
        return None
    obj = _balanced_from(first)
    if obj:
        return obj

    # Fallback: previous greedy behavior (best-effort).
    end = t.rfind("}")
    if end == -1 or end <= first:
        return None
    return t[first : end + 1]


def _repair_common_json_issues(blob: str) -> str:
    """Best-effort repairs for common model JSON mistakes."""
    if not blob:
        return blob

    # Replace smart quotes.
    blob = (
        blob.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )

    # Remove trailing commas before object/array close.
    blob = re.sub(r",(\s*[}\]])", r"\1", blob)

    # Some models emit JSON with leading BOM-like whitespace/control chars.
    blob = blob.strip("\ufeff \t\r\n")
    return blob


def parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    cleaned = _strip_code_fences(text or "")
    blob = _find_json_object(cleaned)
    if not blob:
        return None

    def _parse_blob(candidate: str) -> Optional[Any]:
        try:
            return json.loads(candidate)
        except Exception:  # noqa: BLE001
            try:
                repaired = _repair_common_json_issues(candidate)
                return json.loads(repaired)
            except Exception:  # noqa: BLE001
                try:
                    py_blob = _repair_common_json_issues(candidate)
                    py_blob = re.sub(r"\bnull\b", "None", py_blob)
                    py_blob = re.sub(r"\btrue\b", "True", py_blob, flags=re.IGNORECASE)
                    py_blob = re.sub(r"\bfalse\b", "False", py_blob, flags=re.IGNORECASE)
                    return ast.literal_eval(py_blob)
                except Exception:  # noqa: BLE001
                    return None

    data = _parse_blob(blob)
    if data is None:
        # If the first balanced object wasn't parseable, try a few later brace blocks
        # (models sometimes prepend small objects like { "error": ... } before the real payload).
        cleaned_strip = cleaned.strip()
        tries = 0
        for m in re.finditer(r"\{", cleaned_strip):
            if tries >= 8:
                break
            cand = cleaned_strip[m.start() :]
            candidate_obj = _find_json_object(cand)
            if not candidate_obj:
                continue
            tries += 1
            data = _parse_blob(candidate_obj)
            if data is not None:
                break
        if data is None:
            return None
    if isinstance(data, dict):
        return data
    return None
