"""Backward-compat shim — re-exports from openai_client.py.

All existing ``from app.services.gemini_client import …`` statements
will continue to work unchanged.  The ``GeminiClient`` name is an alias
for ``OpenAIClient``, and ``get_gemini_client`` delegates to
``get_openai_client``.
"""
from app.services.openai_client import (  # noqa: F401
    OpenAIClient as GeminiClient,
    get_openai_client as get_gemini_client,
    generate_growth_assessment,
    PERSONA_DEFAULT_LENGTHS,
)
