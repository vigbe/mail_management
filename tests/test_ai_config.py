"""Provider-agnostic AI configuration: wizard ↔ system parameters ↔ assistant.

One test per rule of the feature.  The AI configuration wizard owns the four
``mail_management.ai.*`` parameters (API key stored encrypted), ``custom``
enforces model + base URL before anything is written, the assistant factory
builds the configured provider from those parameters only (the legacy
``ia_prop`` / ``real_estate`` values are never read), the assistant call sites
still go through the factory, and the new terms are translated to es_CL.
"""

from pathlib import Path
from unittest.mock import Mock, patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

from ..lib.ai_provider import (
    AIProviderError,
    AnthropicProvider,
    CustomProvider,
    DeepSeekProvider,
    GoogleProvider,
    OpenAIProvider,
    make_client,
    provider_selection,
)
from ..lib.mail_crypto import FernetCrypto

PARAM_PROVIDER = "mail_management.ai.provider"
PARAM_KEY = "mail_management.ai.api_key_encrypted"
PARAM_MODEL = "mail_management.ai.model"
PARAM_BASE_URL = "mail_management.ai.base_url"
NEW_PARAMS = (PARAM_PROVIDER, PARAM_KEY, PARAM_MODEL, PARAM_BASE_URL)

#: Read by the previous Gemini-only implementation; must be ignored from now on.
LEGACY_PARAMS = (
    "ia_prop.gemini_api_key",
    "real_estate.ai_gemini_model",
    "mail_management.ai_gemini_key_encrypted",
)

PLAIN_KEY = "sk-test-0123456789"
CUSTOM_URL = "https://llm.example.test/v1"
FACTORY = "odoo.addons.mail_management.models.mail_management_assistant.make_client"
MODULE_ROOT = Path(__file__).resolve().parents[1]


class TestAiConfig(TransactionCase):
    def setUp(self):
        super().setUp()
        self.icp = self.env["ir.config_parameter"].sudo()
        self.Wizard = self.env["mail.management.ai.config.wizard"]
        self.Assistant = self.env["mail.management.assistant"]
        for key in (*NEW_PARAMS, *LEGACY_PARAMS):
            self.icp.set_str(key, False)

    # -- helpers -------------------------------------------------------------

    def _master_key(self):
        return FernetCrypto.get_master_key(self.icp.get_param, self.icp.set_param)

    def _stored_key(self):
        """Decrypt whatever the wizard stored (fails loudly if it is clear)."""
        return FernetCrypto.decrypt(self.icp.get_str(PARAM_KEY), key=self._master_key())

    def _configure(
        self,
        provider: str,
        api_key: str = PLAIN_KEY,
        model: str | bool = False,
        base_url: str | bool = False,
    ):
        """Write the four parameters directly (encrypted key)."""
        self.icp.set_str(PARAM_PROVIDER, provider)
        self.icp.set_str(
            PARAM_KEY, FernetCrypto.encrypt(api_key, key=self._master_key())
        )
        self.icp.set_str(PARAM_MODEL, model)
        self.icp.set_str(PARAM_BASE_URL, base_url)

    def _assert_params_untouched(self):
        for key in NEW_PARAMS:
            self.assertFalse(self.icp.get_str(key), key)

    def _mailbox_with_message(self):
        mailbox = self.env["mail.management.mailbox"].create(
            {
                "name": "AI Test Mailbox",
                "provider_type": "generic",
                "imap_host": "imap.ai.test",
                "imap_user": "ai@test.example",
                "smtp_host": "smtp.ai.test",
                "from_email": "ai@test.example",
            }
        )
        folder = self.env["mail.management.folder"].create(
            {
                "mailbox_id": mailbox.id,
                "imap_name": "INBOX",
                "name": "Inbox",
                "role": "inbox",
            }
        )
        message = self.env["mail.management.message"].create(
            {
                "mailbox_id": mailbox.id,
                "folder_id": folder.id,
                "uid": 1,
                "message_id": "<m1@ai.test>",
                "subject": "Presupuesto",
                "email_from": "alice@example.com",
                "body_text": "¿Me pasas el presupuesto?",
            }
        )
        return mailbox, message

    # -- wizard: persistence ------------------------------------------------

    def test_save_writes_the_four_parameters_with_an_encrypted_key(self):
        self.Wizard.create(
            {
                "provider": "custom",
                "api_key": PLAIN_KEY,
                "model": "llama-x",
                "base_url": CUSTOM_URL,
            }
        ).action_save()

        self.assertEqual(self.icp.get_str(PARAM_PROVIDER), "custom")
        self.assertEqual(self.icp.get_str(PARAM_MODEL), "llama-x")
        self.assertEqual(self.icp.get_str(PARAM_BASE_URL), CUSTOM_URL)

        token = self.icp.get_str(PARAM_KEY)
        self.assertTrue(token)
        self.assertNotEqual(token, PLAIN_KEY)
        self.assertEqual(self._stored_key(), PLAIN_KEY)
        # The clear key is in no parameter at all, and the legacy one is untouched.
        leaked = self.icp.search([]).filtered(
            lambda p, secret=PLAIN_KEY: secret in (p.value or "")
        )
        self.assertFalse(leaked)
        self.assertFalse(self.icp.get_str("mail_management.ai_gemini_key_encrypted"))

    def test_save_without_typing_the_key_keeps_the_current_key(self):
        self.Wizard.create({"provider": "google", "api_key": PLAIN_KEY}).action_save()
        self.Wizard.create({"provider": "google", "model": "gemini-ultra"}).action_save()

        self.assertEqual(self._stored_key(), PLAIN_KEY)
        self.assertEqual(self.icp.get_str(PARAM_MODEL), "gemini-ultra")

    def test_save_without_any_key_raises_and_writes_nothing(self):
        wizard = self.Wizard.create({"provider": "google"})  # no key typed, none stored

        with self.assertRaises(UserError):
            wizard.action_save()

        self._assert_params_untouched()

    # -- wizard: status, selection, validation ------------------------------

    def test_key_is_set_and_current_model_reflect_the_stored_configuration(self):
        wizard = self.Wizard.create({})
        self.assertFalse(wizard.key_is_set)
        self.assertFalse(wizard.current_model)

        self.Wizard.create({"provider": "google", "api_key": PLAIN_KEY}).action_save()
        wizard = self.Wizard.create({})
        self.assertTrue(wizard.key_is_set)
        self.assertEqual(wizard.current_model, "gemini-2.0-flash")  # provider default

        self.Wizard.create({"provider": "openai", "model": "gpt-x"}).action_save()
        wizard = self.Wizard.create({})
        self.assertTrue(wizard.key_is_set)
        self.assertEqual(wizard.current_model, "gpt-x")

        # The legacy encrypted key is not a configured key anymore.
        self.icp.set_str(PARAM_KEY, False)
        self.icp.set_str(
            "mail_management.ai_gemini_key_encrypted",
            FernetCrypto.encrypt("legacy-encrypted-key", key=self._master_key()),
        )
        self.assertFalse(self.Wizard.create({}).key_is_set)

    def test_provider_selection_is_the_registry(self):
        selection = self.Wizard.fields_get(["provider"])["provider"]["selection"]

        self.assertEqual(selection, provider_selection())
        self.assertEqual(
            [key for key, _label in selection],
            ["google", "openai", "anthropic", "deepseek", "custom"],
        )

    def test_custom_without_base_url_or_model_raises_and_writes_nothing(self):
        cases = (
            ({}, "base URL"),
            ({"base_url": CUSTOM_URL}, "model"),
        )
        for extra, missing in cases:
            with self.subTest(missing=missing):
                wizard = self.Wizard.create(
                    {"provider": "custom", "api_key": PLAIN_KEY, **extra}
                )
                with self.assertRaises(UserError) as caught:
                    wizard.action_save()
                self.assertIn(missing.lower(), str(caught.exception).lower())
                self._assert_params_untouched()

    def test_saving_a_stock_provider_drops_the_base_url(self):
        """A stale custom endpoint must never be applied to a stock provider."""
        self.Wizard.create(
            {
                "provider": "custom",
                "api_key": PLAIN_KEY,
                "model": "llama-x",
                "base_url": CUSTOM_URL,
            }
        ).action_save()

        self.Wizard.create({"provider": "deepseek", "model": ""}).action_save()

        self.assertEqual(self.icp.get_str(PARAM_PROVIDER), "deepseek")
        self.assertFalse(self.icp.get_str(PARAM_BASE_URL))
        self.assertFalse(self.icp.get_str(PARAM_MODEL))  # empty = provider default
        self.assertEqual(self._stored_key(), PLAIN_KEY)

    def test_wizard_loads_the_stored_configuration_and_the_provider_default_model(self):
        wizard = self.Wizard.create({})  # nothing stored yet
        self.assertEqual(wizard.provider, "google")
        self.assertFalse(wizard.model)
        self.assertEqual(wizard.default_model, "gemini-2.0-flash")

        wizard.provider = "openai"
        self.assertEqual(wizard.default_model, "gpt-4o-mini")
        wizard.provider = "custom"
        self.assertFalse(wizard.default_model)  # custom has no default

        self.Wizard.create(
            {
                "provider": "custom",
                "api_key": PLAIN_KEY,
                "model": "llama-x",
                "base_url": CUSTOM_URL,
            }
        ).action_save()
        wizard = self.Wizard.create({})
        self.assertEqual(wizard.provider, "custom")
        self.assertEqual(wizard.model, "llama-x")
        self.assertEqual(wizard.base_url, CUSTOM_URL)
        self.assertFalse(wizard.api_key)  # the key is never echoed back

    # -- assistant factory --------------------------------------------------

    def test_factory_builds_the_configured_provider(self):
        cases = {
            "google": (GoogleProvider, False, False, "gemini-2.0-flash"),
            "openai": (OpenAIProvider, "gpt-x", False, "gpt-x"),
            "anthropic": (AnthropicProvider, False, False, "claude-3-5-haiku-latest"),
            "deepseek": (DeepSeekProvider, False, False, "deepseek-chat"),
            "custom": (CustomProvider, "llama-x", CUSTOM_URL, "llama-x"),
        }
        for provider, (cls, model, base_url, expected_model) in cases.items():
            with self.subTest(provider):
                self._configure(provider, model=model, base_url=base_url)
                client = self.Assistant._get_ai_client()
                self.assertIsInstance(client, cls)
                self.assertEqual(client.api_key, PLAIN_KEY)  # decrypted
                self.assertEqual(client.model, expected_model)
                if provider == "custom":
                    self.assertEqual(client.base_url, CUSTOM_URL)

    def test_factory_raises_a_user_error_naming_the_wizard_when_nothing_is_configured(
        self,
    ):
        with self.assertRaises(UserError) as caught:
            self.Assistant._get_ai_client()
        message = str(caught.exception)
        self.assertIn("Configurar IA", message)
        for name in NEW_PARAMS:
            self.assertIn(name, message)

        # A provider without a key, or a key without a provider, is as incomplete.
        self.icp.set_str(PARAM_PROVIDER, "openai")
        with self.assertRaises(UserError):
            self.Assistant._get_ai_client()
        self._configure("google")
        self.icp.set_str(PARAM_PROVIDER, False)
        with self.assertRaises(UserError):
            self.Assistant._get_ai_client()

    def test_factory_maps_the_provider_error_to_a_user_error_with_the_same_message(
        self,
    ):
        self._configure("grok")  # not in the registry

        with self.assertRaises(AIProviderError) as raw:
            make_client("grok", api_key=PLAIN_KEY)
        with self.assertRaises(UserError) as mapped:
            self.Assistant._get_ai_client()

        self.assertEqual(str(mapped.exception), str(raw.exception))

    def test_factory_raises_a_user_error_when_the_stored_key_cannot_be_decrypted(self):
        self._configure("google")
        self.icp.set_str(PARAM_KEY, "not-a-fernet-token")

        with self.assertRaises(UserError) as caught:
            self.Assistant._get_ai_client()
        self.assertIn("Configurar IA", str(caught.exception))

    def test_assistant_never_reads_the_legacy_parameters(self):
        self.icp.set_str("ia_prop.gemini_api_key", "legacy-plain-key")
        self.icp.set_str("real_estate.ai_gemini_model", "legacy-model")
        self.icp.set_str(
            "mail_management.ai_gemini_key_encrypted",
            FernetCrypto.encrypt("legacy-encrypted-key", key=self._master_key()),
        )

        # The legacy values alone do not configure the assistant.
        with self.assertRaises(UserError):
            self.Assistant._get_ai_client()

        # With the module parameters set, only they are used.
        self._configure("openai", api_key="new-key", model="new-model")
        client = self.Assistant._get_ai_client()
        self.assertIsInstance(client, OpenAIProvider)
        self.assertEqual(client.api_key, "new-key")
        self.assertEqual(client.model, "new-model")

    def test_no_python_source_mentions_the_legacy_parameters(self):
        sources = sorted(
            list((MODULE_ROOT / "models").glob("*.py"))
            + list((MODULE_ROOT / "controllers").glob("*.py"))
        )
        self.assertTrue(sources)
        for path in sources:
            text = path.read_text(encoding="utf-8")
            for legacy in LEGACY_PARAMS:
                with self.subTest(source=path.name, legacy=legacy):
                    self.assertNotIn(legacy, text)

    # -- call sites (signatures unchanged, only the factory changed) -------

    def test_chat_and_summarize_route_through_the_factory(self):
        mailbox, message = self._mailbox_with_message()
        self._configure("openai", model="gpt-x")
        client = Mock()
        client.chat.return_value = "Hola <b>qué</b> tal"
        client.ask.return_value = "- Resumen <b>clave</b>"

        with patch(FACTORY, return_value=client) as make_client:
            session = self.env["mail.management.chat.session"]._get_or_create(mailbox)
            reply = self.Assistant.chat(mailbox, session, "hola")

            make_client.assert_called_once_with(
                "openai", PLAIN_KEY, model="gpt-x", base_url=None
            )
            client.chat.assert_called_once()
            system, messages = client.chat.call_args.args
            self.assertIn("AI Test Mailbox", system)
            self.assertEqual(messages[-1]["role"], "user")
            self.assertIn("hola", messages[-1]["text"])
            self.assertEqual(reply, "Hola **qué** tal")
            self.assertEqual(session.message_ids[-1].role, "assistant")
            self.assertEqual(session.message_ids[-1].content, reply)

            make_client.reset_mock()
            result = self.Assistant.summarize(message)

            make_client.assert_called_once_with(
                "openai", PLAIN_KEY, model="gpt-x", base_url=None
            )
            client.ask.assert_called_once()
            self.assertIn(
                "[1] de alice@example.com - Presupuesto", client.ask.call_args.args[1]
            )
            self.assertEqual(result["summary"], "- Resumen **clave**")
            self.assertEqual(result["message_ids"], [message.id])


# ===========================================================================
# es_CL: the NEW terms of the provider-agnostic AI configuration must translate
# ===========================================================================


class TestAiConfigTermsTranslatedEsCl(TransactionCase):
    def setUp(self):
        super().setUp()
        # Field labels are model terms: unlike code strings (read from the PO on
        # disk at run time) they must be loaded into the database for the lang.
        self.env["res.lang"]._activate_lang("es_CL")
        self.env["ir.module.module"].search(
            [("name", "=", "mail_management")]
        )._update_translations(["es_CL"])
        for key in NEW_PARAMS:
            self.env["ir.config_parameter"].sudo().set_str(key, False)

    def test_missing_configuration_error_translated_es_cl(self):
        assistant = self.env["mail.management.assistant"].with_context(lang="es_CL")

        with self.assertRaises(UserError) as caught:
            assistant._get_ai_client()

        message = str(caught.exception)
        self.assertIn(
            "El asistente de IA no está configurado "
            f"(falta: {PARAM_PROVIDER}, {PARAM_KEY}).",
            message,
        )
        self.assertIn("Ajustes ▸ Gestión de correo ▸ Configurar IA", message)
        self.assertNotIn("Settings", message)
        for name in NEW_PARAMS:
            self.assertIn(name, message)

    def test_wizard_field_labels_translated_es_cl(self):
        labels = self.env["mail.management.ai.config.wizard"].with_context(
            lang="es_CL"
        ).fields_get(list(self.env["mail.management.ai.config.wizard"]._fields))

        self.assertEqual(labels["provider"]["string"], "Proveedor")
        self.assertEqual(labels["api_key"]["string"], "Clave de API")
        self.assertEqual(labels["model"]["string"], "Modelo")
        self.assertEqual(labels["default_model"]["string"], "Modelo predeterminado")
        self.assertEqual(labels["base_url"]["string"], "URL base")
        self.assertEqual(labels["current_model"]["string"], "Modelo actual")
        # Every provider of the selection is still offered.
        self.assertEqual(
            [key for key, _label in labels["provider"]["selection"]],
            ["google", "openai", "anthropic", "deepseek", "custom"],
        )
