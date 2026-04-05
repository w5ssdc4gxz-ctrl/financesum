"""Backward-compat shim — re-exports from ai_usage.py.

All existing ``from app.services.gemini_usage import …`` statements
will continue to work unchanged.
"""
from app.services.ai_usage import (  # noqa: F401
    TokenBudget,
    record_ai_usage as record_gemini_usage,
    load_ai_usage_events as load_gemini_usage_events,
    aggregate_ai_usage_by_request_id as aggregate_gemini_usage_by_request_id,
    DEFAULT_TARGET_COST_USD,
    GPT52_INPUT_RATE_PER_M as GEMINI_3_FLASH_INPUT_RATE_PER_M,
    GPT52_OUTPUT_RATE_PER_M as GEMINI_3_FLASH_OUTPUT_RATE_PER_M,
)
