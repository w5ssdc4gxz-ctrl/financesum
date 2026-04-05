"""Backward-compat shim — re-exports from ai_exceptions.py.

All existing ``from app.services.gemini_exceptions import …`` statements
will continue to work unchanged.
"""
from app.services.ai_exceptions import (  # noqa: F401
    AIClientError as GeminiClientError,
    AIRateLimitError as GeminiRateLimitError,
    AIAPIError as GeminiAPIError,
    AITimeoutError as GeminiTimeoutError,
)
