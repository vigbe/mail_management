"""Admin wizard: configure the Gemini API key (encrypted) + model (PR-3a)."""

from odoo import fields, models
from odoo.addons.mail_management.lib.mail_crypto import FernetCrypto


class MailManagementAiConfigWizard(models.TransientModel):
    _name = "mail.management.ai.config.wizard"
    _description = "AI Configuration"

    api_key = fields.Char(
        string="API key de Gemini",
        password=True,
        help="Obtenela gratis en aistudio.google.com → Get API key. "
        "Se guarda CIFRADA (Fernet).",
    )
    model = fields.Char(
        string="Modelo",
        default="gemini-2.0-flash",
        help="Ej: gemini-2.0-flash (rápido y económico).",
    )
    key_is_set = fields.Boolean(string="Key configurada", compute="_compute_status")
    current_model = fields.Char(compute="_compute_status")

    def _compute_status(self):
        icp = self.env["ir.config_parameter"].sudo()
        for wiz in self:
            wiz.key_is_set = bool(
                icp.get_param("mail_management.ai_gemini_key_encrypted")
            )
            wiz.current_model = (
                icp.get_param("mail_management.ai_model") or "gemini-2.0-flash"
            )

    def action_save(self):
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        if self.api_key:
            key = FernetCrypto.get_master_key(icp.get_param, icp.set_param)
            icp.set_param(
                "mail_management.ai_gemini_key_encrypted",
                FernetCrypto.encrypt(self.api_key, key=key),
            )
        if self.model:
            icp.set_param("mail_management.ai_model", self.model.strip())
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Configurar IA",
                "message": "IA configurada ✔ (la key quedó cifrada)",
                "type": "success",
            },
        }
