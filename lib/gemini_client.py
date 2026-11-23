"""Thin Gemini REST client for the mail assistant (PR-3a).

Model-free like the sibling contract: plain ``requests`` (already an Odoo
dependency), retry on transient errors, no Odoo imports — fully mockable.
"""

import logging
import time

import requests

_logger = logging.getLogger(__name__)

_RETRYABLE = frozenset({408, 425, 429, 500, 502, 503, 504})
_DEFAULT_MODEL = "gemini-2.0-flash"
_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


class GeminiError(Exception):
    """Raised for non-transient API failures (message is user-safe)."""


class GeminiClient:
    """Minimal chat-completions client for Google Gemini."""

    def __init__(self, api_key, model=None, timeout=30, max_retries=2):
        self.api_key = api_key
        self.model = model or _DEFAULT_MODEL
        self.timeout = timeout
        self.max_retries = max_retries

    def chat(self, system, messages):
        """``messages``: list of ``{"role": "user"|"model", "text": str}``.

        :returns: the assistant text.
        :raises GeminiError: on definitive failure.
        """
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": m["role"], "parts": [{"text": m["text"]}]} for m in messages
            ],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
        }
        url = _ENDPOINT.format(model=self.model)
        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    url,
                    json=payload,
                    params={"key": self.api_key},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
            else:
                if response.status_code == 200:
                    data = response.json()
                    try:
                        return data["candidates"][0]["content"]["parts"][0]["text"]
                    except (KeyError, IndexError, TypeError) as exc:
                        raise GeminiError("Unexpected Gemini response shape") from exc
                if response.status_code not in _RETRYABLE:
                    raise GeminiError(
                        "Gemini API error %s: %s"
                        % (response.status_code, response.text[:300])
                    )
                last_error = f"HTTP {response.status_code}"
            if attempt < self.max_retries:
                time.sleep(1.5 * (attempt + 1))
        raise GeminiError(f"Gemini unavailable: {last_error}")

    def ask(self, system, user_text):
        """One-shot convenience: single user turn -> text."""
        return self.chat(system, [{"role": "user", "text": user_text}])
