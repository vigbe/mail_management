"""Tests for the read JSON-RPC controllers (PR-1b-i).

Strict-TDD RED: written before the controllers exist.
Verifies design §F — read endpoints return only the caller's records
(per-user ir.rule isolation via request.env), never leak credential fields.

Controllers are tested by mocking ``odoo.http.request`` and calling the
controller methods directly — no browser / HttpCase needed.
"""

from contextlib import contextmanager
from unittest import mock

from odoo.tests.common import TransactionCase, tagged


@contextmanager
def mock_request(env):
    """Patch ``request`` in the controller module so methods see *env*."""
    fake = type("FakeRequest", (), {"env": env, "session": {}})()
    with mock.patch(
        "odoo.addons.mail_management.controllers.main.request", fake
    ):
        yield fake


def _mb_vals(**kw):
    base = {
        "name": "Ctrl Test",
        "provider_type": "generic",
        "imap_host": "imap.ctrl.com",
        "imap_user": "ctrl@test.com",
        "smtp_host": "smtp.ctrl.com",
        "from_email": "ctrl@test.com",
        "credential": "ctrl-pass",
        "state": "active",
    }
    base.update(kw)
    return base


@tagged("standard", "at_install")
class TestReadControllers(TransactionCase):
    """Read JSON endpoints: per-user isolation, no credential leak."""

    def setUp(self):
        super().setUp()
        from odoo.addons.mail_management.controllers.main import (
            MailManagementController,
        )

        self.controller = MailManagementController()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]

        group_user = self.env.ref("mail_management.group_mail_management_user")
        self.user_a = self.env["res.users"].create(
            {
                "name": "Ctrl A",
                "login": "ctrl_a@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )
        self.user_b = self.env["res.users"].create(
            {
                "name": "Ctrl B",
                "login": "ctrl_b@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )

        # User A owns a mailbox + folder + message.
        self.mailbox_a = self.Mailbox.with_user(self.user_a).create(
            _mb_vals(name="A MB", imap_host="ia.com", imap_user="a@ia.com")
        )
        self.folder_a = self.Folder.create(
            {
                "mailbox_id": self.mailbox_a.id,
                "imap_name": "INBOX",
                "name": "Inbox",
                "role": "inbox",
            }
        )
        self.msg_a = self.Msg.create(
            {
                "mailbox_id": self.mailbox_a.id,
                "folder_id": self.folder_a.id,
                "uid": 1,
                "message_id": "<ca@ia.com>",
                "subject": "Hello A",
                "email_from": "from@x.com",
                "body_html": "<p>Body A</p>",
                "body_text": "Body A",
            }
        )

    # ------------------------------------------------------------------
    # /mail_management/folders
    # ------------------------------------------------------------------
    def test_get_folders_returns_own_only(self):
        """User A sees only their own mailbox/folders."""
        with mock_request(self.env(user=self.user_a)):
            result = self.controller.get_folders()
        mailbox_ids = [m["id"] for m in result]
        self.assertIn(self.mailbox_a.id, mailbox_ids)
        # Verify folder data present.
        mb = next(m for m in result if m["id"] == self.mailbox_a.id)
        self.assertTrue(len(mb["folders"]) >= 1)

    def test_get_folders_user_b_sees_nothing_from_a(self):
        """User B does not see user A's mailbox."""
        with mock_request(self.env(user=self.user_b)):
            result = self.controller.get_folders()
        mailbox_ids = [m["id"] for m in result]
        self.assertNotIn(self.mailbox_a.id, mailbox_ids)

    # ------------------------------------------------------------------
    # /mail_management/messages
    # ------------------------------------------------------------------
    def test_get_messages_returns_own(self):
        """User A sees their messages in the folder."""
        with mock_request(self.env(user=self.user_a)):
            result = self.controller.get_messages(folder_id=self.folder_a.id)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["subject"], "Hello A")

    def test_get_messages_user_b_empty(self):
        """User B sees no messages in user A's folder (ir.rule)."""
        with mock_request(self.env(user=self.user_b)):
            result = self.controller.get_messages(folder_id=self.folder_a.id)
        self.assertEqual(len(result), 0)

    # ------------------------------------------------------------------
    # /mail_management/message_body
    # ------------------------------------------------------------------
    def test_get_message_body_own(self):
        """User A can read their own message body."""
        with mock_request(self.env(user=self.user_a)):
            result = self.controller.get_message_body(message_id=self.msg_a.id)
        self.assertEqual(result["subject"], "Hello A")
        self.assertIn("Body A", result.get("body_html", ""))

    def test_get_message_body_cross_user_empty(self):
        """User B cannot read user A's message body (returns empty/none)."""
        with mock_request(self.env(user=self.user_b)):
            result = self.controller.get_message_body(message_id=self.msg_a.id)
        self.assertEqual(result, {})

    # ------------------------------------------------------------------
    # No credential leak
    # ------------------------------------------------------------------
    def test_no_credential_fields_in_folders(self):
        """The /folders response never contains credential fields."""
        with mock_request(self.env(user=self.user_a)):
            result = self.controller.get_folders()
        serialized = str(result)
        self.assertNotIn("credential", serialized.lower())
        self.assertNotIn("ctrl-pass", serialized)

    def test_no_credential_fields_in_message_body(self):
        """The /message_body response never contains credential fields."""
        with mock_request(self.env(user=self.user_a)):
            result = self.controller.get_message_body(message_id=self.msg_a.id)
        serialized = str(result)
        self.assertNotIn("credential", serialized.lower())
        self.assertNotIn("ctrl-pass", serialized)
