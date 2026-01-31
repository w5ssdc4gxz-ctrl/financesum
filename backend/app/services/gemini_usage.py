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

# Gemini 3 Flash pricing (estimated, adjust based on actual pricing)
# These are used for token budget enforcement
GEMINI_3_FLASH_INPUT_RATE_PER_M = 0.04  # $0.04 per 1M input tokens
GEMINI_3_FLASH_OUTPUT_RATE_PER_M = 0.15  # $0.15 per 1M output tokens


class TokenBudget:
    """Enforces a cost budget for Gemini API calls.

    This class tracks token usage across multiple calls and prevents
    exceeding a target cost (default $0.10 per summary).

    Usage:
        budget = TokenBudget(target_cost_usd=0.10)

        # Before each call:
        if budget.can_afford(prompt, expected_output_tokens=500):
            response = gemini_client.generate(prompt)
            budget.charge(prompt, response)
        else:
            # Budget exhausted - abort or use fallback
            pass
    """

    def __init__(
        self,
        target_cost_usd: float = DEFAULT_TARGET_COST_USD,
        input_rate_per_m: Optional[float] = None,
        output_rate_per_m: Optional[float] = None,
    ):
        self.target_cost_usd = float(target_cost_usd)

        # Load rates from env or use defaults
        if input_rate_per_m is not None:
            self.input_rate_per_m = float(input_rate_per_m)
        else:
            self.input_rate_per_m = _float_env(
                "GEMINI_COST_PER_1M_INPUT_TOKENS", GEMINI_3_FLASH_INPUT_RATE_PER_M
            )

        if output_rate_per_m is not None:
            self.output_rate_per_m = float(output_rate_per_m)
        else:
            self.output_rate_per_m = _float_env(
                "GEMINI_COST_PER_1M_OUTPUT_TOKENS", GEMINI_3_FLASH_OUTPUT_RATE_PER_M
            )

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost_usd = 0.0
        self.call_count = 0

    @property
    def remaining_budget(self) -> float:
        """Remaining budget in USD."""
        return max(0.0, self.target_cost_usd - self.total_cost_usd)

    @property
    def remaining_tokens(self) -> int:
        """Approximate remaining tokens (assuming output-heavy usage)."""
        if self.output_rate_per_m <= 0:
            return 1_000_000  # No limit if rate is 0
        return int((self.remaining_budget / self.output_rate_per_m) * 1_000_000)

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost for given token counts."""
        input_cost = (input_tokens / 1_000_000) * self.input_rate_per_m
        output_cost = (output_tokens / 1_000_000) * self.output_rate_per_m
        return input_cost + output_cost

    def can_afford(self, prompt: str, expected_output_tokens: int = 500) -> bool:
        """Check if we can afford another call within budget."""
        input_tokens = _estimate_tokens(prompt)
        estimated_cost = self._estimate_cost(input_tokens, expected_output_tokens)
        return (self.total_cost_usd + estimated_cost) <= self.target_cost_usd

    def charge(self, prompt: str, response: str) -> float:
        """Record a completed call and return the cost charged."""
        input_tokens = _estimate_tokens(prompt)
        output_tokens = _estimate_tokens(response)
        cost = self._estimate_cost(input_tokens, output_tokens)

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost_usd += cost
        self.call_count += 1

        return cost

    def is_exhausted(self) -> bool:
        """Check if budget is exhausted."""
        return self.total_cost_usd >= self.target_cost_usd

    def to_dict(self) -> Dict[str, Any]:
        """Return budget state as a dictionary."""
        return {
            "target_cost_usd": self.target_cost_usd,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "remaining_budget": round(self.remaining_budget, 6),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "call_count": self.call_count,
            "is_exhausted": self.is_exhausted(),
        }


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
            prompt_tokens = usage_metadata.get(
                "prompt_token_count"
            ) or usage_metadata.get("promptTokenCount")
            output_tokens = usage_metadata.get(
                "candidates_token_count"
            ) or usage_metadata.get("candidatesTokenCount")
            total_tokens = usage_metadata.get(
                "total_token_count"
            ) or usage_metadata.get("totalTokenCount")

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
