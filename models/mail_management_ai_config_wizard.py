"""Admin wizard: AI provider, API key (encrypted), model and optional base URL.

The wizard is the only writer of the four module parameters the assistant
factory reads (``models/mail_management_assistant.py``)::

    mail_management.ai.provider           registry key (google, openai, ...)
    mail_management.ai.api_key_encrypted  Fernet token (lib/mail_crypto.py)
    mail_management.ai.model              empty = provider default
    mail_management.ai.base_url           custom provider only
"""

from odoo import _, api, fields, models
from odoo.addons.mail_management.lib.ai_provider import PROVIDERS, provider_selection
from odoo.addons.mail_management.lib.mail_crypto import FernetCrypto
from odoo.addons.mail_management.models.mail_management_assistant import (
    PARAM_API_KEY,
    PARAM_BASE_URL,
    PARAM_MODEL,
    PARAM_PROVIDER,
)
from odoo.exceptions import UserError

#: Registry key of the "bring your own OpenAI-compatible endpoint" provider.
CUSTOM_PROVIDER = "custom"
#: First entry of the registry, offered when nothing is stored yet.
DEFAULT_PROVIDER = next(iter(PROVIDERS))


class MailManagementAiConfigWizard(models.TransientModel):
    _name = "mail.management.ai.config.wizard"
    _description = "AI Configuration"

    provider = fields.Selection(
        selection=provider_selection(),
        string="Provider",
        required=True,
        default=lambda self: self._default_provider(),
        help="Service that hosts the model. Custom points the assistant at any "
        "OpenAI-compatible endpoint.",
    )
    api_key = fields.Char(
        string="API key",
        password=True,
        help="Secret key issued by the provider. It is stored encrypted "
        "(Fernet). Leave it empty to keep the current key.",
    )
    model = fields.Char(
        string="Model",
        default=lambda self: self._default_param(PARAM_MODEL),
        help="Leave empty to use the provider's default model.",
    )
    default_model = fields.Char(
        string="Default model",
        compute="_compute_default_model",
        help="Model the provider uses when the model field is empty.",
    )
    base_url = fields.Char(
        string="Base URL",
        default=lambda self: self._default_param(PARAM_BASE_URL),
        help="Custom provider only: root of the OpenAI-compatible endpoint, "
        "e.g. https://llm.example.com/v1",
    )
    key_is_set = fields.Boolean(string="Key configurada", compute="_compute_status")
    current_model = fields.Char(string="Current model", compute="_compute_status")

    # ------------------------------------------------------------------
    # Defaults: the wizard opens on the stored configuration
    # ------------------------------------------------------------------
    @api.model
    def _default_param(self, key):
        return self.env["ir.config_parameter"].sudo().get_str(key) or False

    @api.model
    def _default_provider(self):
        stored = self._default_param(PARAM_PROVIDER)
        return stored if stored in PROVIDERS else DEFAULT_PROVIDER

    @api.depends("provider")
    def _compute_default_model(self):
        for wiz in self:
            provider_class = PROVIDERS.get(wiz.provider)
            wiz.default_model = provider_class.default_model if provider_class else ""

    def _compute_status(self):
        icp = self.env["ir.config_parameter"].sudo()
        for wiz in self:
            wiz.key_is_set = bool(icp.get_str(PARAM_API_KEY))
            wiz.current_model = self._effective_model(
                icp.get_str(PARAM_PROVIDER), icp.get_str(PARAM_MODEL)
            )

    @api.model
    def _effective_model(self, provider, model):
        """Stored model, else the provider default (nothing when unknown)."""
        if model:
            return model
        provider_class = PROVIDERS.get(provider or "")
        return provider_class.default_model if provider_class else ""

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    def action_save(self):
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        # Validate everything before writing a single parameter.
        self._check_before_save(key_is_set=bool(icp.get_str(PARAM_API_KEY)))

        if self.api_key:
            key = FernetCrypto.get_master_key(icp.get_param, icp.set_param)
            icp.set_str(
                PARAM_API_KEY, FernetCrypto.encrypt(self.api_key.strip(), key=key)
            )
        icp.set_str(PARAM_PROVIDER, self.provider)
        icp.set_str(PARAM_MODEL, self._param_value(self.model))
        # The base URL belongs to the custom provider only: a stale custom
        # endpoint must never be applied to a stock provider.
        icp.set_str(
            PARAM_BASE_URL,
            self._param_value(self.base_url)
            if self.provider == CUSTOM_PROVIDER
            else False,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Configurar IA",
                "message": "IA configurada ✔ (la key quedó cifrada)",
                "type": "success",
            },
        }

    def _check_before_save(self, key_is_set):
        """Raise before anything is written when the form cannot configure AI."""
        if self.provider == CUSTOM_PROVIDER:
            label = PROVIDERS[CUSTOM_PROVIDER].label
            if not self._param_value(self.base_url):
                raise UserError(
                    _(
                        "%(provider)s needs a base URL "
                        "(the root of the OpenAI-compatible endpoint)."
                    )
                    % {"provider": label}
                )
            if not self._param_value(self.model):
                raise UserError(_("%(provider)s needs a model.") % {"provider": label})
        if not key_is_set and not self._param_value(self.api_key):
            raise UserError(_("Enter the API key: none is stored yet."))

    @staticmethod
    def _param_value(text):
        """Trimmed text, or ``False`` so ``set_param`` removes the parameter."""
        return (text or "").strip() or False
