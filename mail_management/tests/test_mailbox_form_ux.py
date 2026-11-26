"""PR-2j — friendly mailbox form: provider presets + one-email UX."""

from pathlib import Path

from odoo.tests.common import TransactionCase


class TestMailboxFormUx(TransactionCase):
    def _wizard_like(self, provider, email):
        rec = self.env["mail.management.mailbox"].new(
            {"provider_type": provider, "from_email": email}
        )
        rec._onchange_provider_type()
        rec._onchange_from_email()
        return rec

    def test_gmail_preset_fills_servers(self):
        rec = self._wizard_like("gmail", "someone@gmail.com")
        self.assertEqual(rec.imap_host, "imap.gmail.com")
        self.assertEqual(rec.imap_port, 993)
        self.assertTrue(rec.imap_use_ssl)
        self.assertEqual(rec.smtp_host, "smtp.gmail.com")
        self.assertEqual(rec.smtp_port, 587)
        self.assertTrue(rec.smtp_use_tls)

    def test_outlook_preset_fills_servers(self):
        rec = self._wizard_like("oauth_ms365", "someone@hotmail.com")
        self.assertEqual(rec.imap_host, "outlook.office365.com")
        self.assertEqual(rec.smtp_host, "smtp-mail.outlook.com")
        self.assertEqual(rec.smtp_port, 587)

    def test_generic_keeps_manual(self):
        rec = self._wizard_like("generic", "a@b.cl")
        self.assertFalse(rec.imap_host)

    def test_email_seeds_user_and_name(self):
        rec = self._wizard_like("gmail", "someone@gmail.com")
        self.assertEqual(rec.imap_user, "someone@gmail.com")
        self.assertEqual(rec.name, "someone@gmail.com")

    def test_existing_values_not_overwritten(self):
        rec = self.env["mail.management.mailbox"].new(
            {
                "provider_type": "gmail",
                "from_email": "x@gmail.com",
                "imap_user": "custom@other.cl",
                "name": "Mi correo",
            }
        )
        rec._onchange_from_email()
        self.assertEqual(rec.imap_user, "custom@other.cl")
        self.assertEqual(rec.name, "Mi correo")

    def test_view_has_friendly_spanish_layout(self):
        xml = (
            Path(__file__).parent.parent / "views" / "mail_management_mailbox_views.xml"
        ).read_text(encoding="utf-8")
        self.assertIn('string="Contraseña de correo"', xml)
        self.assertIn('string="Correo electrónico"', xml)
        self.assertIn('string="Servidores (avanzado)"', xml)
        self.assertIn('string="Probar conexión"', xml)
        page = xml.split('name="servers"')[1].split("</page>")[0]
        self.assertIn("imap_host", page)
        self.assertIn("smtp_host", page)
