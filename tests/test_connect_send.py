"""Tests for the IMAP/SMTP client seam and action_test_connection.

Strict-TDD RED: written before the lib/ clients and action_test_connection
exist.  All transports are MOCKED — no real network calls.
"""

from email.message import EmailMessage
from unittest import mock

from odoo.tests.common import TransactionCase, tagged


@tagged("standard", "at_install")
class TestImapClient(TransactionCase):
    """ImapClient context-manager seam (mocked imap_tools)."""

    def test_connect_and_close(self):
        """ImapClient connects, logs in, and closes cleanly."""
        from odoo.addons.mail_management.lib.mail_imap_client import ImapClient

        with mock.patch(
            "odoo.addons.mail_management.lib.mail_imap_client.MailBox"
        ) as MockMailBox:
            mock_conn = MockMailBox.return_value
            mock_conn.login.return_value = mock_conn

            with ImapClient(
                "imap.example.com", 993, True, "user@test.com", "secret"
            ) as client:
                self.assertIsNotNone(client)

            MockMailBox.assert_called_once()
            mock_conn.login.assert_called_once_with("user@test.com", "secret")
            mock_conn.logout.assert_called_once()

    def test_connect_unencrypted(self):
        """Uses MailBoxUnencrypted when use_ssl is False."""
        from odoo.addons.mail_management.lib.mail_imap_client import ImapClient

        with (
            mock.patch(
                "odoo.addons.mail_management.lib.mail_imap_client.MailBoxUnencrypted"
            ) as MockUnenc,
            mock.patch(
                "odoo.addons.mail_management.lib.mail_imap_client.MailBox"
            ) as MockEnc,
        ):
            mock_conn = MockUnenc.return_value
            mock_conn.login.return_value = mock_conn

            with ImapClient("imap.example.com", 143, False, "user@test.com", "secret"):
                pass

            MockUnenc.assert_called_once()
            MockEnc.assert_not_called()


@tagged("standard", "at_install")
class TestSmtpClient(TransactionCase):
    """SmtpClient seam (mocked smtplib)."""

    def test_connect_starttls_and_auth(self):
        """SmtpClient connects, STARTTLS, AUTH, and can send."""
        from odoo.addons.mail_management.lib.mail_smtp_client import SmtpClient

        with mock.patch(
            "odoo.addons.mail_management.lib.mail_smtp_client.smtplib.SMTP"
        ) as MockSMTP:
            mock_conn = MockSMTP.return_value

            client = SmtpClient(
                "smtp.example.com", 587, True, "user@test.com", "secret"
            )
            client.connect()

            MockSMTP.assert_called_once()
            mock_conn.starttls.assert_called_once()
            mock_conn.login.assert_called_once_with("user@test.com", "secret")

            msg = EmailMessage()
            msg["From"] = "user@test.com"
            msg["To"] = "dest@test.com"
            msg.set_content("Hello")
            client.send_message(msg)
            mock_conn.send_message.assert_called_once_with(msg)

            client.close()
            mock_conn.quit.assert_called_once()


@tagged("standard", "at_install")
class TestActionTestConnection(TransactionCase):
    """mailbox.action_test_connection with mocked transports."""

    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.mailbox = self.Mailbox.create(
            {
                "name": "Conn Test",
                "provider_type": "generic",
                "imap_host": "imap.example.com",
                "imap_user": "test@example.com",
                "smtp_host": "smtp.example.com",
                "from_email": "test@example.com",
                "credential": "app-password-xyz",
            }
        )

    @staticmethod
    def _mock_cm(*, enter_side_effect=None):
        obj = mock.MagicMock()
        if enter_side_effect is not None:
            obj.__enter__.side_effect = enter_side_effect
        else:
            obj.__enter__.return_value = obj
        obj.__exit__.return_value = False
        return obj

    def test_success(self):
        """Both transports succeed → success=True."""
        with (
            mock.patch(
                "odoo.addons.mail_management.models.mail_management.ImapClient",
                return_value=self._mock_cm(),
            ),
            mock.patch(
                "odoo.addons.mail_management.models.mail_management.SmtpClient",
                return_value=self._mock_cm(),
            ),
        ):
            result = self.mailbox.action_test_connection()
        self.assertTrue(result["success"])
        self.assertNotIn("app-password-xyz", result.get("message", ""))

    def test_imap_failure_redacted(self):
        """IMAP failure → success=False, message redacted."""
        with (
            mock.patch(
                "odoo.addons.mail_management.models.mail_management.ImapClient",
                return_value=self._mock_cm(
                    enter_side_effect=Exception("AUTH failed: app-password-xyz")
                ),
            ),
            mock.patch(
                "odoo.addons.mail_management.models.mail_management.SmtpClient",
                return_value=self._mock_cm(),
            ),
        ):
            result = self.mailbox.action_test_connection()
        self.assertFalse(result["success"])
        self.assertNotIn("app-password-xyz", result["message"])

    def test_credential_never_in_result(self):
        """The plaintext credential never appears in any result string."""
        for scenario in ("success", "failure"):
            with self.subTest(scenario=scenario):
                if scenario == "success":
                    imap_cm = self._mock_cm()
                else:
                    imap_cm = self._mock_cm(
                        enter_side_effect=Exception("connection refused")
                    )
                with (
                    mock.patch(
                        "odoo.addons.mail_management.models.mail_management.ImapClient",
                        return_value=imap_cm,
                    ),
                    mock.patch(
                        "odoo.addons.mail_management.models.mail_management.SmtpClient",
                        return_value=self._mock_cm(),
                    ),
                ):
                    result = self.mailbox.action_test_connection()
                self.assertNotIn("app-password-xyz", str(result))
