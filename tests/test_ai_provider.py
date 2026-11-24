"""Provider-agnostic AI clients: registry, request building, parsing, retries.

One test per rule of the provider layer, all through the public interface
(``make_client`` + ``chat``/``ask``).  ``requests.post`` is patched at the module
boundary so nothing leaves the test container and no network is needed.
"""

from unittest.mock import Mock, patch

import requests
from odoo.tests.common import BaseCase

from ..lib import ai_provider as _ai_provider_module
from ..lib.ai_provider import (
    PROVIDERS,
    AIProviderError,
    AnthropicProvider,
    CustomProvider,
    DeepSeekProvider,
    GoogleProvider,
    OpenAIProvider,
    make_client,
)

_POST = _ai_provider_module.__name__ + ".requests.post"
_SLEEP = _ai_provider_module.__name__ + ".time.sleep"

GOOGLE_OK = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
OPENAI_OK = {"choices": [{"message": {"content": "ok"}}]}
ANTHROPIC_OK = {"content": [{"type": "text", "text": "ok"}]}


def _response(status=200, json_data=None, text=""):
    response = Mock()
    response.status_code = status
    response.json.return_value = json_data if json_data is not None else {}
    response.text = text or str(json_data)
    return response


class _CaptureMixin:
    """Run one ``chat`` against a mocked HTTP layer and return the call args."""

    MESSAGES = [
        {"role": "user", "text": "hi"},
        {"role": "model", "text": "hello"},
        {"role": "user", "text": "bye"},
    ]

    def _capture(self, provider, ok_body, **kwargs):
        with patch(_POST, return_value=_response(200, ok_body)) as post:
            make_client(provider, api_key="secret", **kwargs).chat(
                "be brief", self.MESSAGES
            )
        return post.call_args


class TestAIProviderRegistry(BaseCase):
    def test_registry_lists_the_five_configurable_providers(self):
        self.assertEqual(
            set(PROVIDERS), {"google", "openai", "anthropic", "deepseek", "custom"}
        )

    def test_unknown_provider_raises_a_provider_error(self):
        with self.assertRaises(AIProviderError):
            make_client("grok", api_key="k")

    def test_each_provider_has_its_own_defaults(self):
        cases = {
            "google": (
                GoogleProvider,
                "gemini-2.0-flash",
                "https://generativelanguage.googleapis.com/v1beta",
            ),
            "openai": (OpenAIProvider, "gpt-4o-mini", "https://api.openai.com/v1"),
            "anthropic": (
                AnthropicProvider,
                "claude-3-5-haiku-latest",
                "https://api.anthropic.com/v1",
            ),
            "deepseek": (DeepSeekProvider, "deepseek-chat", "https://api.deepseek.com/v1"),
        }
        for name, (cls, model, base_url) in cases.items():
            with self.subTest(name):
                client = make_client(name, api_key="k")
                self.assertIsInstance(client, cls)
                self.assertEqual(client.model, model)
                self.assertEqual(client.base_url, base_url)

    def test_explicit_model_and_base_url_win_over_defaults(self):
        client = make_client(
            "openai", api_key="k", model="gpt-x", base_url="https://example.test/v1"
        )
        self.assertEqual(client.model, "gpt-x")
        self.assertEqual(client.base_url, "https://example.test/v1")

    def test_custom_provider_requires_model_and_base_url(self):
        with self.assertRaises(AIProviderError):
            make_client("custom", api_key="k")
        client = make_client(
            "custom", api_key="k", model="m", base_url="https://llm.example.test/v1"
        )
        self.assertIsInstance(client, CustomProvider)


class TestRequestBuilding(_CaptureMixin, BaseCase):
    """What goes over the wire per provider: URL, auth header, payload."""

    def test_google_uses_generate_content_and_keeps_model_role(self):
        args, kwargs = self._capture("google", GOOGLE_OK)
        self.assertEqual(
            args[0],
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.0-flash:generateContent",
        )
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "secret")
        self.assertNotIn("secret", args[0])  # the key never travels in the URL
        payload = kwargs["json"]
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], "be brief")
        self.assertEqual(
            [part["role"] for part in payload["contents"]], ["user", "model", "user"]
        )

    def test_openai_sends_bearer_token_and_assistant_role(self):
        args, kwargs = self._capture("openai", OPENAI_OK)
        self.assertEqual(args[0], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret")
        body = kwargs["json"]
        self.assertEqual(body["model"], "gpt-4o-mini")
        self.assertEqual(
            [message["role"] for message in body["messages"]],
            ["system", "user", "assistant", "user"],
        )
        self.assertEqual(body["messages"][0]["content"], "be brief")

    def test_anthropic_sends_api_key_version_header_and_system_field(self):
        args, kwargs = self._capture("anthropic", ANTHROPIC_OK)
        self.assertEqual(args[0], "https://api.anthropic.com/v1/messages")
        self.assertEqual(kwargs["headers"]["x-api-key"], "secret")
        self.assertIn("anthropic-version", kwargs["headers"])
        body = kwargs["json"]
        self.assertEqual(body["system"], "be brief")
        self.assertEqual(
            [message["role"] for message in body["messages"]],
            ["user", "assistant", "user"],
        )
        self.assertGreater(body["max_tokens"], 0)

    def test_deepseek_and_custom_are_openai_compatible(self):
        cases = {
            "deepseek": ({}, "https://api.deepseek.com/v1"),
            "custom": (
                {"model": "m", "base_url": "https://llm.example.test/v1"},
                "https://llm.example.test/v1",
            ),
        }
        for name, (kwargs, base_url) in cases.items():
            with self.subTest(name):
                args, call_kwargs = self._capture(name, OPENAI_OK, **kwargs)
                self.assertEqual(args[0], base_url + "/chat/completions")
                self.assertEqual(call_kwargs["headers"]["Authorization"], "Bearer secret")
                expected_model = "m" if name == "custom" else "deepseek-chat"
                self.assertEqual(call_kwargs["json"]["model"], expected_model)


class TestResponseParsing(BaseCase):
    def _chat(self, provider, json_data):
        with patch(_POST, return_value=_response(200, json_data)):
            return make_client(provider, api_key="k").ask("sys", "hi")

    def test_google_joins_all_text_parts(self):
        data = {
            "candidates": [{"content": {"parts": [{"text": "Hello"}, {"text": " world"}]}}]
        }
        self.assertEqual(self._chat("google", data), "Hello world")

    def test_openai_reads_first_choice_message(self):
        self.assertEqual(
            self._chat("openai", {"choices": [{"message": {"content": "hello"}}]}),
            "hello",
        )

    def test_anthropic_concatenates_text_blocks(self):
        data = {"content": [{"type": "text", "text": "Hello"}, {"type": "text", "text": " world"}]}
        self.assertEqual(self._chat("anthropic", data), "Hello world")

    def test_unexpected_shape_raises_provider_error(self):
        for name in ("google", "openai", "anthropic", "deepseek"):
            with self.subTest(name), self.assertRaises(AIProviderError):
                self._chat(name, {"unexpected": True})


class TestRetriesAndErrors(BaseCase):
    def test_transient_status_is_retried_then_succeeds(self):
        replies = [_response(503), _response(429), _response(200, OPENAI_OK)]
        with patch(_SLEEP) as sleep, patch(_POST, side_effect=replies) as post:
            self.assertEqual(make_client("openai", api_key="k").ask("s", "hi"), "ok")
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_retry_budget_exhausted_raises(self):
        with (
            patch(_SLEEP),
            patch(_POST, return_value=_response(503)) as post,
            self.assertRaises(AIProviderError),
        ):
            make_client("openai", api_key="k", max_retries=2).ask("s", "hi")
        self.assertEqual(post.call_count, 3)

    def test_connection_errors_are_retried(self):
        replies = [requests.ConnectionError("boom"), _response(200, OPENAI_OK)]
        with patch(_SLEEP), patch(_POST, side_effect=replies) as post:
            self.assertEqual(make_client("openai", api_key="k").ask("s", "hi"), "ok")
        self.assertEqual(post.call_count, 2)

    def test_non_transient_status_fails_fast_with_status_in_message(self):
        with (
            patch(_SLEEP) as sleep,
            patch(_POST, return_value=_response(401, text="bad key")) as post,
            self.assertRaises(AIProviderError) as caught,
        ):
            make_client("openai", api_key="k").ask("s", "hi")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(sleep.call_count, 0)
        self.assertIn("401", str(caught.exception))

    def test_ask_is_a_single_user_turn(self):
        with patch(_POST, return_value=_response(200, OPENAI_OK)) as post:
            make_client("openai", api_key="k").ask("s", "hi")
        self.assertEqual(
            post.call_args.kwargs["json"]["messages"],
            [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}],
        )


class TestBackwardCompatibleShim(BaseCase):
    def test_gemini_client_is_the_google_provider(self):
        from ..lib import GeminiClient, GeminiError

        self.assertIs(GeminiError, AIProviderError)
        self.assertTrue(issubclass(GeminiClient, GoogleProvider))
