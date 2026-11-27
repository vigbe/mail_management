"""Provider-agnostic AI clients for the mail assistant.

Every provider implements the same tiny interface::

    client.chat(system, messages) -> str
    client.ask(system, user_text) -> str      # one-shot helper

with ``messages`` a list of ``{"role": "user" | "model", "text": str}`` dicts
(the historical Gemini shape, kept so call sites and stored transcripts do not
change).  Clients are pure Python on top of ``requests`` — no vendor SDK — so
every supported Odoo version (16.0–20.0) can run them unchanged.

Selection happens through :data:`PROVIDERS` and :func:`make_client`; the
module's Settings wizard stores the chosen key in ``mail_management.ai.provider``.
"""

import time

import requests

#: HTTP statuses worth retrying (transient by contract on all providers).
TRANSIENT_HTTP_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
#: Base backoff between attempts, multiplied by the attempt number.
RETRY_BACKOFF_SECONDS = 1.5

#: Internal role used for the assistant's turns in ``messages``.
MODEL_ROLE = "model"


class AIProviderError(Exception):
    """Raised for non-transient AI failures (message is user-safe)."""


class BaseAIProvider:
    """Retry-once-framework shared by every provider.

    Subclasses set the class attributes and implement the four hooks
    :meth:`url`, :meth:`headers`, :meth:`payload` and :meth:`extract_text`.
    """

    #: registry key (``mail_management.ai.provider``)
    name = ""
    #: human label for errors and the wizard
    label = "AI provider"
    default_model = ""
    default_base_url = ""
    #: the OpenAI-compatible "custom" provider has no usable defaults
    requires_base_url = False
    requires_model = False

    def __init__(self, api_key, model=None, base_url=None, timeout=30, max_retries=2):
        if not api_key:
            raise AIProviderError(f"{self.label}: API key is missing")
        self.api_key = api_key
        if self.requires_base_url and not base_url:
            raise AIProviderError(f"{self.label}: a base URL is required")
        if self.requires_model and not model:
            raise AIProviderError(f"{self.label}: a model is required")
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.model = model or self.default_model
        self.timeout = timeout
        self.max_retries = max_retries

    # -- public interface -------------------------------------------------

    def chat(self, system, messages):
        """``messages``: list of ``{"role": "user"|"model", "text": str}``.

        :returns: the assistant text.
        :raises AIProviderError: on definitive failure or unexpected response.
        """
        data = self._post(self.url(), self.headers(), self.payload(system, messages))
        try:
            return self.extract_text(data)
        except (AttributeError, IndexError, KeyError, TypeError) as exc:
            raise AIProviderError(f"Unexpected {self.label} response shape") from exc

    def ask(self, system, user_text):
        """One-shot convenience: single user turn -> text."""
        return self.chat(system, [{"role": "user", "text": user_text}])

    # -- provider hooks ---------------------------------------------------

    def url(self):
        raise NotImplementedError

    def headers(self):
        raise NotImplementedError

    def payload(self, system, messages):
        raise NotImplementedError

    def extract_text(self, data):
        raise NotImplementedError

    @staticmethod
    def _turns(messages, model_role):
        """Yield ``(role, text)`` with the internal model role mapped."""
        for message in messages:
            role = model_role if message["role"] == MODEL_ROLE else message["role"]
            yield role, message["text"]

    # -- transport with retries -------------------------------------------

    def _post(self, url, headers, payload):
        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    url, json=payload, headers=headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = str(exc)
            else:
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise AIProviderError(
                            f"Unexpected {self.label} response shape"
                        ) from exc
                if response.status_code not in TRANSIENT_HTTP_CODES:
                    raise AIProviderError(
                        f"{self.label} API error {response.status_code}: {response.text[:300]}"
                    )
                last_error = f"HTTP {response.status_code}"
            if attempt < self.max_retries:
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise AIProviderError(f"{self.label} unavailable: {last_error}")


class GoogleProvider(BaseAIProvider):
    """Google Gemini ``generateContent`` REST API."""

    name = "google"
    label = "Google Gemini"
    default_model = "gemini-2.0-flash"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"

    def url(self):
        return f"{self.base_url}/models/{self.model}:generateContent"

    def headers(self):
        return {"x-goog-api-key": self.api_key}

    def payload(self, system, messages):
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": role, "parts": [{"text": text}]}
                for role, text in self._turns(messages, MODEL_ROLE)
            ],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
        }

    def extract_text(self, data):
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts)


class OpenAIProvider(BaseAIProvider):
    """OpenAI ``chat/completions`` — also the base for OpenAI-compatible APIs."""

    name = "openai"
    label = "OpenAI"
    default_model = "gpt-4o-mini"
    default_base_url = "https://api.openai.com/v1"

    def url(self):
        return f"{self.base_url}/chat/completions"

    def headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def payload(self, system, messages):
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": system}]
            + [
                {"role": role, "content": text}
                for role, text in self._turns(messages, "assistant")
            ],
            "temperature": 0.4,
            "max_tokens": 2048,
        }

    def extract_text(self, data):
        text = data["choices"][0]["message"]["content"]
        if not isinstance(text, str):
            raise TypeError("content is not text")
        return text


class AnthropicProvider(BaseAIProvider):
    """Anthropic Messages API."""

    name = "anthropic"
    label = "Anthropic Claude"
    default_model = "claude-3-5-haiku-latest"
    default_base_url = "https://api.anthropic.com/v1"

    def url(self):
        return f"{self.base_url}/messages"

    def headers(self):
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

    def payload(self, system, messages):
        return {
            "model": self.model,
            "max_tokens": 2048,
            "system": system,
            "messages": [
                {"role": role, "content": text}
                for role, text in self._turns(messages, "assistant")
            ],
        }

    def extract_text(self, data):
        return "".join(
            block["text"] for block in data["content"] if block.get("type") == "text"
        )


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek (OpenAI-compatible endpoint)."""

    name = "deepseek"
    label = "DeepSeek"
    default_model = "deepseek-chat"
    default_base_url = "https://api.deepseek.com/v1"


class CustomProvider(OpenAIProvider):
    """Any OpenAI-compatible endpoint: model and base URL are mandatory."""

    name = "custom"
    label = "Custom (OpenAI-compatible)"
    default_model = ""
    default_base_url = ""
    requires_base_url = True
    requires_model = True


#: registry key -> provider class, in wizard display order.
PROVIDERS = {
    cls.name: cls
    for cls in (
        GoogleProvider,
        OpenAIProvider,
        AnthropicProvider,
        DeepSeekProvider,
        CustomProvider,
    )
}


def provider_selection():
    """``[(key, label), ...]`` ready for a Selection field."""
    return [(key, cls.label) for key, cls in PROVIDERS.items()]


def get_provider_class(name):
    """Resolve a registry key (case/space tolerant); raise if unknown."""
    key = (name or "").strip().lower()
    if key not in PROVIDERS:
        raise AIProviderError(
            f"Unknown AI provider {name!r}. Choose one of: {', '.join(PROVIDERS)}"
        )
    return PROVIDERS[key]


def make_client(provider, api_key, model=None, base_url=None, **kwargs):
    """Instantiate the provider registered under *provider*."""
    return get_provider_class(provider)(
        api_key=api_key, model=model, base_url=base_url, **kwargs
    )
