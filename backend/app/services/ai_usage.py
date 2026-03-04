"""Local AI usage logging helpers."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.config import ensure_env_loaded
from app.services.posthog import capture_posthog_event

try:  # pragma: no cover - platform dependent
    import fcntl  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - platform dependent
    fcntl = None  # type: ignore[assignment]

BACKEND_DIR = Path(__file__).resolve().parents[2]
USAGE_DIR = BACKEND_DIR / "data" / "local_cache"
USAGE_DIR.mkdir(parents=True, exist_ok=True)
USAGE_LOG_FILE = USAGE_DIR / "ai_usage.jsonl"
USAGE_LOCK_FILE = USAGE_DIR / "ai_usage.lock"

DEFAULT_TARGET_COST_USD = 0.1

# GPT-5.2 pricing (adjust based on actual pricing)
GPT52_INPUT_RATE_PER_M = 2.50   # $2.50 per 1M input tokens
GPT52_OUTPUT_RATE_PER_M = 10.00  # $10.00 per 1M output tokens
OPENAI_API_BASE = "https://api.openai.com/v1"
POSTHOG_MAX_TEXT_CHARS = 12000


class TokenBudget:
    """Enforces a cost budget for AI API calls.

    Tracks token usage across multiple calls and prevents exceeding
    a target cost (default $0.10 per summary).
    """

    def __init__(
        self,
        target_cost_usd: float = DEFAULT_TARGET_COST_USD,
        input_rate_per_m: Optional[float] = None,
        output_rate_per_m: Optional[float] = None,
    ):
        self.target_cost_usd = float(target_cost_usd)

        if input_rate_per_m is not None:
            self.input_rate_per_m = float(input_rate_per_m)
        else:
            self.input_rate_per_m = _float_env(
                "OPENAI_COST_PER_1M_INPUT_TOKENS",
                GPT52_INPUT_RATE_PER_M,
            )

        if output_rate_per_m is not None:
            self.output_rate_per_m = float(output_rate_per_m)
        else:
            self.output_rate_per_m = _float_env(
                "OPENAI_COST_PER_1M_OUTPUT_TOKENS",
                GPT52_OUTPUT_RATE_PER_M,
            )

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost_usd = 0.0
        self.call_count = 0

    @property
    def remaining_budget(self) -> float:
        return max(0.0, self.target_cost_usd - self.total_cost_usd)

    @property
    def remaining_tokens(self) -> int:
        if self.output_rate_per_m <= 0:
            return 1_000_000
        return int((self.remaining_budget / self.output_rate_per_m) * 1_000_000)

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        input_cost = (input_tokens / 1_000_000) * self.input_rate_per_m
        output_cost = (output_tokens / 1_000_000) * self.output_rate_per_m
        return input_cost + output_cost

    def can_afford(self, prompt: str, expected_output_tokens: int = 500) -> bool:
        input_tokens = _estimate_tokens(prompt)
        estimated_cost = self._estimate_cost(input_tokens, expected_output_tokens)
        return (self.total_cost_usd + estimated_cost) <= self.target_cost_usd

    def charge(self, prompt: str, response: str) -> float:
        input_tokens = _estimate_tokens(prompt)
        output_tokens = _estimate_tokens(response)
        cost = self._estimate_cost(input_tokens, output_tokens)

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost_usd += cost
        self.call_count += 1
        return cost

    def is_exhausted(self) -> bool:
        return self.total_cost_usd >= self.target_cost_usd

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_cost_usd": self.target_cost_usd,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "remaining_budget": round(self.remaining_budget, 6),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "call_count": self.call_count,
            "is_exhausted": self.is_exhausted(),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    input_rate = _float_env(
        "OPENAI_COST_PER_1M_INPUT_TOKENS", GPT52_INPUT_RATE_PER_M
    )
    output_rate = _float_env(
        "OPENAI_COST_PER_1M_OUTPUT_TOKENS", GPT52_OUTPUT_RATE_PER_M
    )
    if input_rate <= 0:
        input_rate = GPT52_INPUT_RATE_PER_M
    if output_rate <= 0:
        output_rate = GPT52_OUTPUT_RATE_PER_M
    target_cost = _float_env("OPENAI_COST_PER_SUMMARY_USD", DEFAULT_TARGET_COST_USD)
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


def _truncate_text(text: Optional[str], *, max_chars: int = POSTHOG_MAX_TEXT_CHARS) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return value[:max_chars]


def _emit_posthog_ai_usage_event(
    event: Dict[str, Any],
    *,
    prompt: str,
    response_text: str,
) -> None:
    """Best-effort mirror of local usage and LLM telemetry into PostHog."""
    distinct_id = (
        str(event.get("user_id") or "").strip()
        or str(event.get("request_id") or "").strip()
        or str(event.get("id") or "").strip()
    )
    if not distinct_id:
        return

    trace_id = str(event.get("request_id") or event.get("id") or "").strip() or str(
        uuid4().hex
    )
    timestamp = str(event.get("timestamp_utc") or "")

    llm_properties: Dict[str, Any] = {
        "$ai_provider": "openai",
        "$ai_model": event.get("model"),
        "$ai_input": [
            {
                "role": "user",
                "content": [{"type": "text", "text": _truncate_text(prompt)}],
            }
        ],
        "$ai_output_choices": [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": _truncate_text(response_text)}],
            }
        ],
        "$ai_http_status": 200,
        "$ai_input_tokens": event.get("prompt_tokens"),
        "$ai_output_tokens": event.get("output_tokens"),
        "$ai_trace_id": trace_id,
        "$ai_base_url": OPENAI_API_BASE,
        "source": "backend",
        "event_id": event.get("id"),
        "request_id": event.get("request_id"),
        "request_type": event.get("request_type"),
        "user_id": event.get("user_id"),
        "filing_id": event.get("filing_id"),
        "company_id": event.get("company_id"),
        "pipeline_mode": event.get("pipeline_mode"),
        "agent_stage": event.get("agent_stage"),
        "call_type": event.get("call_type"),
        "cost_usd": event.get("cost_usd"),
        "target_cost_usd": event.get("target_cost_usd"),
        "cost_basis": event.get("cost_basis"),
    }
    if not str(event.get("user_id") or "").strip():
        llm_properties["$process_person_profile"] = False

    capture_posthog_event(
        event="$ai_generation",
        distinct_id=distinct_id,
        timestamp=timestamp,
        properties=llm_properties,
    )

    capture_posthog_event(
        event="ai_api_usage_recorded",
        distinct_id=distinct_id,
        timestamp=timestamp,
        properties={
            "source": "backend",
            "event_id": event.get("id"),
            "request_id": event.get("request_id"),
            "request_type": event.get("request_type"),
            "user_id": event.get("user_id"),
            "filing_id": event.get("filing_id"),
            "company_id": event.get("company_id"),
            "pipeline_mode": event.get("pipeline_mode"),
            "agent_stage": event.get("agent_stage"),
            "call_type": event.get("call_type"),
            "model": event.get("model"),
            "prompt_tokens": event.get("prompt_tokens"),
            "output_tokens": event.get("output_tokens"),
            "total_tokens": event.get("total_tokens"),
            "prompt_chars": event.get("prompt_chars"),
            "output_chars": event.get("output_chars"),
            "cost_usd": event.get("cost_usd"),
            "target_cost_usd": event.get("target_cost_usd"),
            "cost_basis": event.get("cost_basis"),
        },
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_ai_usage(
    *,
    prompt: str,
    response_text: str,
    usage_metadata: Optional[Dict[str, Any]],
    model: str,
    usage_context: Optional[Dict[str, Any]] = None,
) -> None:
    """Best-effort logging of AI token usage and estimated cost."""
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
        _emit_posthog_ai_usage_event(event, prompt=prompt, response_text=response_text)
    except Exception:
        return


def load_ai_usage_events(days: int = 7) -> List[Dict[str, Any]]:
    """Load recent usage events from the local JSONL log."""
    try:
        days = max(0, int(days))
    except Exception:
        days = 7

    if not USAGE_LOG_FILE.exists():
        return []

    cutoff_ts = None
    if days > 0:
        cutoff_ts = datetime.now(timezone.utc).timestamp() - (days * 86400)

    events: List[Dict[str, Any]] = []
    try:
        with USAGE_LOG_FILE.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = (line or "").strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(event, dict):
                    continue
                if cutoff_ts is not None:
                    ts_raw = event.get("timestamp_utc") or event.get("timestamp") or ""
                    try:
                        dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                        ts = dt.timestamp()
                    except Exception:
                        ts = None
                    if ts is not None and ts < cutoff_ts:
                        continue
                events.append(event)
    except Exception:
        return []

    def _ts(event: Dict[str, Any]) -> float:
        ts_raw = event.get("timestamp_utc") or event.get("timestamp") or ""
        try:
            dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            return float(dt.timestamp())
        except Exception:
            return 0.0

    events.sort(key=_ts, reverse=True)
    return events


def aggregate_ai_usage_by_request_id(
    events: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Aggregate usage events by request_id for admin reporting."""
    grouped: Dict[str, Dict[str, Any]] = {}

    def _to_int(value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    def _to_float(value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    for event in events or []:
        if not isinstance(event, dict):
            continue
        request_id = (
            event.get("request_id")
            or event.get("requestId")
            or event.get("filing_id")
            or event.get("summary_id")
            or event.get("id")
            or "unknown"
        )
        request_id = str(request_id)
        bucket = grouped.get(request_id)
        if bucket is None:
            bucket = {
                "request_id": request_id,
                "call_count": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "models": set(),
                "latest_timestamp_utc": None,
            }
            grouped[request_id] = bucket

        bucket["call_count"] = int(bucket["call_count"]) + 1
        bucket["total_tokens"] = int(bucket["total_tokens"]) + _to_int(event.get("total_tokens"))
        bucket["total_cost_usd"] = float(bucket["total_cost_usd"]) + _to_float(event.get("cost_usd"))
        model = event.get("model")
        if isinstance(model, str) and model.strip():
            bucket["models"].add(model.strip())
        ts = event.get("timestamp_utc") or event.get("timestamp")
        if ts and (bucket.get("latest_timestamp_utc") is None):
            bucket["latest_timestamp_utc"] = ts

    out: List[Dict[str, Any]] = []
    for bucket in grouped.values():
        models = sorted(bucket.pop("models", set()))
        bucket["models"] = models
        bucket["total_cost_usd"] = round(float(bucket.get("total_cost_usd") or 0.0), 6)
        out.append(bucket)

    out.sort(
        key=lambda b: (float(b.get("total_cost_usd") or 0.0), int(b.get("call_count") or 0)),
        reverse=True,
    )
    return out


# ---------------------------------------------------------------------------
# Backward-compat aliases
# ---------------------------------------------------------------------------
record_gemini_usage = record_ai_usage
load_gemini_usage_events = load_ai_usage_events
aggregate_gemini_usage_by_request_id = aggregate_ai_usage_by_request_id
