"""Strict-TDD RED tests for PR-1b-ii controller extensions.

The read-only webmail UI needs two small extensions to the PR-1b-i read
endpoints:

* ``/mail_management/messages``: an optional whitelisted ``order`` parameter
  so the message list can sort by date ascending/descending.
* ``/mail_management/message_body``: ``content_id``/``inline`` on the
  attachment payload so the reading pane can resolve ``cid:`` inline images
  from ``mail.management.attachment.content_id``.

Written BEFORE the controller change (RED).
"""

import base64
import io
from contextlib import contextmanager
from unittest import mock

from odoo import fields  # type: ignore
from odoo.tests.common import TransactionCase, tagged  # type: ignore


@contextmanager
def mock_request(env):
    """Patch ``request`` in the controller module so methods see *env*."""
    fake = type("FakeRequest", (), {"env": env, "session": {}})()
    with mock.patch("odoo.addons.mail_management.controllers.main.request", fake):
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
class TestReadControllersUiExtensions(TransactionCase):
    """PR-1b-ii read endpoint extensions (order + attachment metadata)."""

    def setUp(self):
        super().setUp()
        from odoo.addons.mail_management.controllers.main import (  # type: ignore
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
                "login": "ctrl_a_ui@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )

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
        # Two messages with explicit dates so the sort order is meaningful.
        self.msg_old = self.Msg.create(
            {
                "mailbox_id": self.mailbox_a.id,
                "folder_id": self.folder_a.id,
                "uid": 2,
                "message_id": "<old@ia.com>",
                "subject": "Older message",
                "email_from": "from@x.com",
                "date": fields.Datetime.to_datetime("2020-01-01 08:00:00"),
                "body_html": "<p>Older body</p>",
                "body_text": "Older body",
            }
        )
        self.msg_new = self.Msg.create(
            {
                "mailbox_id": self.mailbox_a.id,
                "folder_id": self.folder_a.id,
                "uid": 3,
                "message_id": "<new@ia.com>",
                "subject": "Newer message",
                "email_from": "from@x.com",
                "date": fields.Datetime.to_datetime("2026-01-01 08:00:00"),
                "body_html": "<p>Newer body</p>",
                "body_text": "Newer body",
            }
        )

    # ------------------------------------------------------------------
    # /mail_management/messages — order parameter
    # ------------------------------------------------------------------
    def test_get_messages_order_ascending(self):
        """``order='date asc'`` returns the oldest message first."""
        with mock_request(self.env(user=self.user_a)):
            result = self.controller.get_messages(
                folder_id=self.folder_a.id, order="date asc"
            )
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], self.msg_old.id)
        self.assertEqual(result[1]["id"], self.msg_new.id)

    def test_get_messages_order_descending(self):
        """``order='date desc'`` returns the newest message first."""
        with mock_request(self.env(user=self.user_a)):
            result = self.controller.get_messages(
                folder_id=self.folder_a.id, order="date desc"
            )
        self.assertEqual(result[0]["id"], self.msg_new.id)

    def test_get_messages_unknown_order_falls_back_to_default(self):
        """A non-whitelisted ``order`` is ignored (default sort kept)."""
        with mock_request(self.env(user=self.user_a)):
            forged = self.controller.get_messages(
                folder_id=self.folder_a.id, order="subject; DROP TABLE users"
            )
            default = self.controller.get_messages(folder_id=self.folder_a.id)
        self.assertEqual([m["id"] for m in forged], [m["id"] for m in default])

    # ------------------------------------------------------------------
    # /mail_management/message_body — attachment metadata for CID images
    # ------------------------------------------------------------------
    def test_get_message_body_includes_content_id(self):
        """Inline attachment payload carries ``content_id`` + ``inline``."""
        ir_att = self.env["ir.attachment"].create(
            {
                "name": "inline.png",
                "datas": base64.b64encode(io.BytesIO(b"png").getvalue()).decode(),
                "res_model": "mail.management.message",
                "res_id": self.msg_new.id,
                "mimetype": "image/png",
            }
        )
        self.env["mail.management.attachment"].create(
            {
                "webmail_message_id": self.msg_new.id,
                "name": "inline.png",
                "mimetype": "image/png",
                "file_size": 3,
                "ir_attachment_id": ir_att.id,
                "content_id": "img001@ia.com",
                "inline": True,
            }
        )
        with mock_request(self.env(user=self.user_a)):
            result = self.controller.get_message_body(message_id=self.msg_new.id)
        self.assertEqual(len(result["attachments"]), 1)
        att = result["attachments"][0]
        self.assertEqual(att["content_id"], "img001@ia.com")
        self.assertTrue(att["inline"])
