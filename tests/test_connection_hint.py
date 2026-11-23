"""PR-2j — friendly Spanish hint on provider auth-policy errors."""

from odoo.tests.common import TransactionCase


class TestConnectionHint(TransactionCase):
    def setUp(self):
        super().setUp()
        self.mailbox = self.env["mail.management.mailbox"].new(
            {
                "name": "G",
                "imap_host": "imap.gmail.com",
                "smtp_host": "smtp.gmail.com",
                "imap_user": "u@gmail.com",
            }
        )

    def test_google_app_password_hint(self):
        msg = (
            'IMAP: Response status "OK" expected, but "NO" received. '
            "Data: [b'[ALERT] Application-specific password required: "
            "https://support.google.com/accounts/answer/185833 (Failure)']"
        )
        hint = self.mailbox._connection_error_hint(msg)
        self.assertIn("myaccount.google.com/apppasswords", hint)
        self.assertIn("Verificación en 2 pasos", hint)

    def test_outlook_app_password_hint(self):
        msg = (
            "SMTP: (535, b'5.7.139 Authentication unsuccessful, "
            "Application-specific password required. smtp-mail.outlook.com')"
        )
        hint = self.mailbox._connection_error_hint(msg)
        self.assertIn("account.live.com/passwords", hint)

    def test_plain_bad_credentials_hint(self):
        hint = self.mailbox._connection_error_hint(
            "IMAP: INVALID CREDENTIALS - authentication failed"
        )
        self.assertIn("incorrectos", hint)

    def test_no_hint_for_generic_errors(self):
        self.assertEqual(self.mailbox._connection_error_hint("IMAP: timeout"), "")


class TestConnectionWizard(TransactionCase):
    """PR-2j: actionable provider errors open a MODAL wizard with steps."""

    def setUp(self):
        super().setUp()
        self.mailbox = self.env["mail.management.mailbox"].create(
            {
                "name": "G",
                "imap_host": "imap.gmail.com",
                "smtp_host": "smtp.gmail.com",
                "imap_user": "u@gmail.com",
                "from_email": "u@gmail.com",
                "credential": "app-secret",
            }
        )

    def _google_alert(self):
        return Exception(
            'Response status "OK" expected, but "NO" received. '
            "Data: [b'[ALERT] Application-specific password required: "
            "https://support.google.com/accounts/answer/185833 (Failure)']"
        )

    def test_guidance_structure_google(self):
        g = self.mailbox._connection_error_guidance(
            "x Application-specific password required"
        )
        self.assertEqual(g["link_url"], "https://myaccount.google.com/apppasswords")
        self.assertGreaterEqual(len(g["steps"]), 3)

    def test_action_returns_wizard_modal_on_google_error(self):
        from unittest import mock

        with mock.patch(
            "odoo.addons.mail_management.models.mail_management.ImapClient",
            side_effect=self._google_alert(),
        ):
            result = self.mailbox.action_test_connection()
        self.assertEqual(result["type"], "ir.actions.act_window")
        self.assertEqual(result["res_model"], "mail.management.connection.wizard")
        self.assertEqual(result["target"], "new")
        wizard = self.env["mail.management.connection.wizard"].browse(result["res_id"])
        self.assertIn("myaccount.google.com/apppasswords", wizard.link_url)
        self.assertIn("Verificación en 2 pasos", wizard.message)
        self.assertIn("Application-specific", wizard.raw_error)

    def test_generic_error_keeps_toast(self):
        from unittest import mock

        with (
            mock.patch(
                "odoo.addons.mail_management.models.mail_management.ImapClient",
                side_effect=Exception("timeout"),
            ),
            mock.patch(
                "odoo.addons.mail_management.models.mail_management.SmtpClient",
                side_effect=Exception("timeout"),
            ),
        ):
            result = self.mailbox.action_test_connection()
        self.assertEqual(result["type"], "ir.actions.client")
        self.assertFalse(result["success"])

    def test_dns_error_hint(self):
        """Errno -2 (hostname not resolvable) gets an actionable hint."""
        hint = self.mailbox._connection_error_hint(
            "SMTP: [Errno -2] Name or service not known"
        )
        self.assertIn("no existe", hint)
        self.assertIn("smtp.gmail.com", hint)

    def test_auto_activates_on_success(self):
        """A successful connection with folders auto-activates the mailbox."""
        from unittest import mock

        # un buzón draft con carpetas descubierto y conecta -> active
        self.mailbox.state = "draft"
        folder = self.env["mail.management.folder"].create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "INBOX",
                "role": "inbox",
            }
        )
        with (
            mock.patch(
                "odoo.addons.mail_management.models.mail_management.ImapClient"
            ) as fake_imap,
            mock.patch(
                "odoo.addons.mail_management.models.mail_management.SmtpClient"
            ) as fake_smtp,
        ):
            fake_imap.return_value.__enter__ = lambda s: mock.MagicMock(
                list_folders=lambda: ["INBOX"]
            )
            fake_imap.return_value.__exit__ = lambda s, *a: False
            fake_smtp.return_value.__enter__ = lambda s: None
            fake_smtp.return_value.__exit__ = lambda s, *a: False
            result = self.mailbox.action_test_connection()
        self.assertTrue(result.get("success", True))
        self.assertEqual(self.mailbox.state, "active")
        folder.unlink()
