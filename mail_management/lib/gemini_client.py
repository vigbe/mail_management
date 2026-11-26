"""Backward-compatible shim: the Gemini client is now the ``google`` entry of
:mod:`mail_management.lib.ai_provider`.  New code uses ``make_client``; this
module keeps ``GeminiClient`` / ``GeminiError`` importable for existing callers.
"""

from .ai_provider import AIProviderError, GoogleProvider

#: Kept for callers catching the historical name.
GeminiError = AIProviderError


class GeminiClient(GoogleProvider):
    """Deprecated alias of :class:`mail_management.lib.ai_provider.GoogleProvider`."""
