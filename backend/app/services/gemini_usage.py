"""Local Gemini usage logging helpers."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from app.config import ensure_env_loaded

try:  # pragma: no cover - platform dependent
    import fcntl  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - platform dependent
    fcntl = None  # type: ignore[assignment]

BACKEND_DIR = Path(__file__).resolve().parents[2]
USAGE_DIR = BACKEND_DIR / "data" / "local_cache"
USAGE_DIR.mkdir(parents=True, exist_ok=True)
USAGE_LOG_FILE = USAGE_DIR / "gemini_usage.jsonl"
USAGE_LOCK_FILE = USAGE_DIR / "gemini_usage.lock"

DEFAULT_TARGET_COST_USD = 0.1


@contextmanager
def _exclusive_lock(path: Path):
    if fcntl is None:
        yield
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError:
            yield
            return

        try:
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _load_pricing_config() -> Tuple[float, float, float]:
    ensure_env_loaded()
    input_rate = _float_env("GEMINI_COST_PER_1M_INPUT_TOKENS", 0.0)
    output_rate = _float_env("GEMINI_COST_PER_1M_OUTPUT_TOKENS", 0.0)
    target_cost = _float_env("GEMINI_COST_PER_SUMMARY_USD", DEFAULT_TARGET_COST_USD)
    return input_rate, output_rate, target_cost


def _estimate_tokens(text: Optional[str]) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / 4))


def _sanitize_context(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not context:
        return {}
    sanitized: Dict[str, Any] = {}
    for key, value in context.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            sanitized[key] = value
        else:
            sanitized[key] = str(value)
    return sanitized


def _append_json_line(event: Dict[str, Any]) -> None:
    payload = json.dumps(event, ensure_ascii=True)
    with _exclusive_lock(USAGE_LOCK_FILE):
        with USAGE_LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")


def record_gemini_usage(
    *,
    prompt: str,
    response_text: str,
    usage_metadata: Optional[Dict[str, Any]],
    model: str,
    usage_context: Optional[Dict[str, Any]] = None,
) -> None:
    """Best-effort logging of Gemini token usage and estimated cost."""
    try:
        prompt_tokens = None
        output_tokens = None
        total_tokens = None

        if usage_metadata:
            prompt_tokens = usage_metadata.get("prompt_token_count") or usage_metadata.get("promptTokenCount")
            output_tokens = usage_metadata.get("candidates_token_count") or usage_metadata.get("candidatesTokenCount")
            total_tokens = usage_metadata.get("total_token_count") or usage_metadata.get("totalTokenCount")

        if prompt_tokens is None:
            prompt_tokens = _estimate_tokens(prompt)
        if output_tokens is None:
            output_tokens = _estimate_tokens(response_text)
        if total_tokens is None:
            total_tokens = int(prompt_tokens) + int(output_tokens)

        input_rate, output_rate, target_cost = _load_pricing_config()
        cost_usd = None
        cost_basis = None
        if input_rate > 0 or output_rate > 0:
            cost_usd = (float(prompt_tokens) / 1_000_000) * input_rate + (
                float(output_tokens) / 1_000_000
            ) * output_rate
            cost_basis = "per_million_token_rate"
        elif target_cost:
            cost_usd = float(target_cost)
            cost_basis = "flat_target"

        event: Dict[str, Any] = {
            "id": uuid4().hex,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "prompt_chars": len(prompt or ""),
            "output_chars": len(response_text or ""),
            "prompt_tokens": int(prompt_tokens),
            "output_tokens": int(output_tokens),
            "total_tokens": int(total_tokens),
            "cost_usd": round(cost_usd, 6) if cost_usd is not None else None,
            "cost_basis": cost_basis,
            "target_cost_usd": float(target_cost),
        }
        event.update(_sanitize_context(usage_context))
        _append_json_line(event)
    except Exception:
        # Best-effort logging only; never raise from usage tracking.
        return
