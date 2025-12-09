"""Custom exceptions for Gemini API client."""
from typing import Optional


class GeminiClientError(Exception):
    """Base exception for all Gemini client errors."""
    pass


class GeminiRateLimitError(GeminiClientError):
    """Raised when Gemini API rate limit is exceeded (HTTP 429)."""

    def __init__(self, message: str, retry_after: Optional[int] = None):
        """
        Initialize rate limit error.

        Args:
            message: Error message
            retry_after: Seconds to wait before retry (from Retry-After header)
        """
        super().__init__(message)
        self.retry_after = retry_after


class GeminiAPIError(GeminiClientError):
    """Raised for Gemini API errors (4xx/5xx excluding 429)."""

    def __init__(self, message: str, status_code: int, response_body: Optional[str] = None):
        """
        Initialize API error.

        Args:
            message: Error message
            status_code: HTTP status code
            response_body: Response body from API
        """
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class GeminiTimeoutError(GeminiClientError):
    """Raised when Gemini API request times out."""
    pass
