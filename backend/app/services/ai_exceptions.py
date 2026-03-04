"""Custom exceptions for AI API client (OpenAI GPT-5.2).

Backward-compatible aliases for GeminiClientError, etc. are exported so
that existing callers (filings.py, spotlight_kpi/*) keep working.
"""
from typing import Optional


class AIClientError(Exception):
    """Base exception for all AI client errors."""
    pass


class AIRateLimitError(AIClientError):
    """Raised when API rate limit is exceeded (HTTP 429)."""

    def __init__(self, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after


class AIAPIError(AIClientError):
    """Raised for API errors (4xx/5xx excluding 429)."""

    def __init__(self, message: str, status_code: int, response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class AITimeoutError(AIClientError):
    """Raised when API request times out."""
    pass


# ---------------------------------------------------------------------------
# Backward-compat aliases (so existing `from gemini_exceptions import ...` works)
# ---------------------------------------------------------------------------
GeminiClientError = AIClientError
GeminiRateLimitError = AIRateLimitError
GeminiAPIError = AIAPIError
GeminiTimeoutError = AITimeoutError
