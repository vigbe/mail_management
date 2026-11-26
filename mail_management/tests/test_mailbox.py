"""Tests for the mail.management.mailbox model (PR-1a-2).

Strict-TDD RED: written BEFORE the full implementation exists.
Covers CRUD, defaults, UNIQUE constraint, credential encryption-at-rest,
ondelete guard, and action_test_connection with mocked transports.
"""

from unittest import mock

import psycopg2
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("standard", "at_install")
class TestMailboxModel(TransactionCase):
    """Mailbox model: fields, defaults, constraints, credential pattern."""

    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.user = self.env.user
        self.mailbox_vals = {
            "name": "Test Mailbox",
            "user_id": self.user.id,
            "provider_type": "generic",
            "imap_host": "imap.example.com",
            "imap_user": "test@example.com",
            "smtp_host": "smtp.example.com",
            "from_email": "test@example.com",
        }

    # ------------------------------------------------------------------
    # CRUD & defaults
    # ------------------------------------------------------------------
    def test_create_mailbox(self):
        """A mailbox can be created with all required fields."""
        mb = self.Mailbox.create(self.mailbox_vals)
        self.assertTrue(mb.id)
        self.assertEqual(mb.name, "Test Mailbox")
        self.assertEqual(mb.provider_type, "generic")
        self.assertEqual(mb.imap_host, "imap.example.com")
        self.assertEqual(mb.imap_user, "test@example.com")
        self.assertEqual(mb.smtp_host, "smtp.example.com")
        self.assertEqual(mb.from_email, "test@example.com")

    def test_defaults_imap_smtp(self):
        """IMAP/SMTP port and SSL/TLS defaults are correct."""
        mb = self.Mailbox.create(self.mailbox_vals)
        self.assertEqual(mb.imap_port, 993)
        self.assertTrue(mb.imap_use_ssl)
        self.assertEqual(mb.smtp_port, 587)
        self.assertTrue(mb.smtp_use_tls)
        self.assertEqual(mb.state, "draft")
        self.assertTrue(mb.active)

    def test_user_id_defaults_to_current_user(self):
        """user_id defaults to the current user when not explicitly set."""
        vals = dict(self.mailbox_vals)
        vals.pop("user_id")
        mb = self.Mailbox.create(vals)
        self.assertEqual(mb.user_id, self.env.user)

    def test_provider_type_defaults_to_generic(self):
        """provider_type defaults to 'generic'."""
        vals = dict(self.mailbox_vals)
        vals.pop("provider_type")
        mb = self.Mailbox.create(vals)
        self.assertEqual(mb.provider_type, "generic")

    def test_company_id_defaults_to_current_company(self):
        """company_id defaults to the current company."""
        mb = self.Mailbox.create(self.mailbox_vals)
        self.assertEqual(mb.company_id, self.env.company)

    # ------------------------------------------------------------------
    # UNIQUE(user_id, imap_host, imap_user) constraint
    # ------------------------------------------------------------------
    def test_unique_constraint_rejects_duplicate(self):
        """Duplicate (user_id, imap_host, imap_user) is rejected."""
        self.Mailbox.create(self.mailbox_vals)
        with self.assertRaises(psycopg2.IntegrityError):
            self.Mailbox.create(self.mailbox_vals)

    def test_unique_constraint_allows_different_host(self):
        """Same user+imap_user but different host is allowed."""
        self.Mailbox.create(self.mailbox_vals)
        vals = dict(self.mailbox_vals)
        vals["imap_host"] = "imap.other.com"
        mb = self.Mailbox.create(vals)
        self.assertTrue(mb.id)

    def test_unique_constraint_allows_different_user(self):
        """Same host+imap_user but different user is allowed."""
        other_user = self.env["res.users"].create(
            {"name": "Other User", "login": "other_uniq@test"}
        )
        self.Mailbox.create(self.mailbox_vals)
        vals = dict(self.mailbox_vals)
        vals["user_id"] = other_user.id
        mb = self.Mailbox.create(vals)
        self.assertTrue(mb.id)

    # ------------------------------------------------------------------
    # Credential encryption-at-rest (CRITICAL — design B.2)
    # ------------------------------------------------------------------
    def test_credential_never_persists_plaintext(self):
        """The raw DB column must contain Fernet ciphertext, never plaintext."""
        mb = self.Mailbox.create(dict(self.mailbox_vals, credential="mySecret123"))
        # Flush so the value is actually in the DB.
        self.env["mail.management.mailbox"].flush_model(["credential_encrypted"])
        self.env.cr.execute(
            "SELECT credential_encrypted FROM mail_management_mailbox WHERE id = %s",
            (mb.id,),
        )
        raw = self.env.cr.fetchone()[0]
        self.assertIsNotNone(raw, "credential_encrypted column is NULL")
        self.assertNotIn("mySecret123", raw)
        # Fernet tokens are base64 and start with 'gAAAAA'.
        self.assertTrue(raw.startswith("gAAAAA"))

    def test_credential_read_returns_mask(self):
        """Reading credential returns a constant mask, never plaintext."""
        mb = self.Mailbox.create(dict(self.mailbox_vals, credential="mySecret123"))
        self.assertEqual(mb.credential, "**********")

    def test_credential_mask_when_not_set(self):
        """When no credential is set, reading returns False."""
        mb = self.Mailbox.create(self.mailbox_vals)
        self.assertFalse(mb.credential)

    def test_credential_decrypt_roundtrip(self):
        """_get_credential_plaintext returns the original plaintext."""
        mb = self.Mailbox.create(dict(self.mailbox_vals, credential="mySecret123"))
        plain = mb._get_credential_plaintext()
        self.assertEqual(plain, "mySecret123")

    def test_credential_encrypted_is_copy_false(self):
        """Duplicating a mailbox does NOT carry the secret."""
        mb = self.Mailbox.create(dict(self.mailbox_vals, credential="mySecret123"))
        mb2 = mb.copy(
            default={
                "imap_host": "imap.copy.com",
                "imap_user": "copy@test.com",
            }
        )
        self.assertFalse(mb2.credential_encrypted)

    def test_write_credential_encrypts(self):
        """Writing credential via write also encrypts at rest."""
        mb = self.Mailbox.create(self.mailbox_vals)
        mb.credential = "newPass456"
        self.env["mail.management.mailbox"].flush_model(["credential_encrypted"])
        self.env.cr.execute(
            "SELECT credential_encrypted FROM mail_management_mailbox WHERE id = %s",
            (mb.id,),
        )
        raw = self.env.cr.fetchone()[0]
        self.assertIsNotNone(raw)
        self.assertNotIn("newPass456", raw)
        # The decrypted value is correct.
        self.assertEqual(mb._get_credential_plaintext(), "newPass456")

    def test_get_credential_raises_when_not_set(self):
        """_get_credential_plaintext raises UserError when no credential set."""
        mb = self.Mailbox.create(self.mailbox_vals)
        with self.assertRaises(UserError):
            mb._get_credential_plaintext()

    def test_credential_update_overwrites_previous(self):
        """Updating the credential re-encrypts with the new value."""
        mb = self.Mailbox.create(dict(self.mailbox_vals, credential="old-pass"))
        self.assertEqual(mb._get_credential_plaintext(), "old-pass")
        mb.credential = "new-pass"
        self.assertEqual(mb._get_credential_plaintext(), "new-pass")

    # ------------------------------------------------------------------
    # ondelete guard
    # ------------------------------------------------------------------
    def test_unlink_blocked_when_active(self):
        """Cannot delete a mailbox in 'active' state."""
        mb = self.Mailbox.create(self.mailbox_vals)
        mb.state = "active"
        with self.assertRaises(UserError):
            mb.unlink()

    def test_unlink_allowed_when_draft(self):
        """Can delete a mailbox in 'draft' state."""
        mb = self.Mailbox.create(self.mailbox_vals)
        mb.unlink()
        self.assertFalse(mb.exists())

    # ------------------------------------------------------------------
    # action_test_connection (mocked transports)
    # ------------------------------------------------------------------
    def test_action_test_connection_success(self):
        """Returns success when mocked IMAP+SMTP connect."""
        mb = self.Mailbox.create(dict(self.mailbox_vals, credential="conn-pass-1"))
        mock_imap = self._mock_context_manager()
        mock_smtp = self._mock_context_manager()
        with (
            mock.patch(
                "odoo.addons.mail_management.models.mail_management.ImapClient",
                return_value=mock_imap,
            ),
            mock.patch(
                "odoo.addons.mail_management.models.mail_management.SmtpClient",
                return_value=mock_smtp,
            ),
        ):
            result = mb.action_test_connection()
        self.assertTrue(result["success"])
        self.assertNotIn("conn-pass-1", result.get("message", ""))

    def test_action_test_connection_failure_redacted(self):
        """Returns a redacted failure message when IMAP login fails."""
        mb = self.Mailbox.create(dict(self.mailbox_vals, credential="conn-pass-2"))
        mock_imap = self._mock_context_manager(
            enter_side_effect=Exception("Authentication failed for conn-pass-2")
        )
        mock_smtp = self._mock_context_manager()
        with (
            mock.patch(
                "odoo.addons.mail_management.models.mail_management.ImapClient",
                return_value=mock_imap,
            ),
            mock.patch(
                "odoo.addons.mail_management.models.mail_management.SmtpClient",
                return_value=mock_smtp,
            ),
        ):
            result = mb.action_test_connection()
        self.assertFalse(result["success"])
        # The plaintext must NEVER appear in the failure message.
        self.assertNotIn("conn-pass-2", result["message"])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _mock_context_manager(*, enter_side_effect=None):
        """Create a MagicMock usable as a ``with`` context manager."""
        obj = mock.MagicMock()
        if enter_side_effect is not None:
            obj.__enter__.side_effect = enter_side_effect
        else:
            obj.__enter__.return_value = obj
        obj.__exit__.return_value = False
        return obj
