"""Strict-TDD RED tests for PR-1c — compose: reply/reply-all/forward + send.

Covers the send contract (design §D.1):
  * threading headers (In-Reply-To / References from the original message);
  * forward = fresh Message-ID, no In-Reply-To, originals MIME-attached;
  * SMTP send via the mailbox credential (SmtpClient seam, mocked);
  * Sent-folder APPEND (role='sent' folder, fallback sent_folder_name);
  * APPEND failure after SMTP success is SURFACED, not swallowed;
  * \\Answered mirrored locally + via IMAP STORE on reply only;
  * attachments persisted via ir.attachment/filestore and linked to the
    local Sent message record;
  * /mail_management/send controller: owner-scoped, redacted errors;
  * compose UI wiring (buttons + dialog + assets + manifest bump).

All transports are MOCKED — no real network calls.
"""

import ast
import base64
import email
import os
from contextlib import contextmanager
from typing import Any
from unittest import mock

from odoo import fields  # type: ignore
from odoo.exceptions import UserError  # type: ignore
from odoo.modules.module import get_module_path  # type: ignore
from odoo.tests.common import TransactionCase, tagged  # type: ignore

SMTP_PATCH = "odoo.addons.mail_management.models.mail_management.SmtpClient"
IMAP_PATCH = "odoo.addons.mail_management.models.mail_management.ImapClient"
IMAP_LIB_PATCH = "odoo.addons.mail_management.lib.mail_imap_client.MailBox"


# ---------------------------------------------------------------------------
# Fake transports (context-manager compatible with the model seam calls)
# ---------------------------------------------------------------------------


class FakeSmtpClient:
    """Stands in for ``SmtpClient`` — records the EmailMessage sent."""

    def __init__(self, fail=None):
        self.sent = []
        self.fail = fail

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send_message(self, message):
        if self.fail:
            raise self.fail
        self.sent.append(message)


class FakeImapClient:
    """Stands in for ``ImapClient`` — records append()/store() calls."""

    def __init__(self, append_fail=None, store_fail=None):
        self.appends = []  # (message_bytes, folder, flag_set, date)
        self.stores = []  # (uids, flag_set, value)
        self.append_fail = append_fail
        self.store_fail = store_fail

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def append(self, message_bytes, folder="INBOX", flag_set=None, date=None):
        if self.append_fail:
            raise self.append_fail
        self.appends.append((message_bytes, folder, flag_set or [], date))

    def store(self, uids, flag_set, value):
        if self.store_fail:
            raise self.store_fail
        self.stores.append((uids, flag_set, value))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mailbox_vals(**kw):
    base = {
        "name": "Send Test",
        "provider_type": "generic",
        "imap_host": "imap.send.com",
        "imap_user": "me@send.com",
        "smtp_host": "smtp.send.com",
        "from_email": "me@send.com",
        "credential": "app-pass-XYZ123",
        "state": "active",
    }
    base.update(kw)
    return base


class SendTestMixin:
    """Shared setUp: an active mailbox with INBOX + Sent folders and an
    original (already fetched) message to reply/forward."""

    #: provided by the TransactionCase the mixin is combined with.
    env: Any

    def setUpSend(self):
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]
        self.IrAttachment = self.env["ir.attachment"]
        self.WAttach = self.env["mail.management.attachment"]

        self.mailbox = self.Mailbox.create(_mailbox_vals())
        self.inbox = self.Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "INBOX",
                "name": "Inbox",
                "role": "inbox",
            }
        )
        self.sent = self.Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "Sent",
                "name": "Sent",
                "role": "sent",
            }
        )
        self.original = self.Msg.create(
            {
                "mailbox_id": self.mailbox.id,
                "folder_id": self.inbox.id,
                "uid": 7,
                "message_id": "<orig@ext.com>",
                "subject": "Original subject",
                "email_from": "customer@ext.com",
                "email_to": "me@send.com",
                "email_cc": "other@ext.com",
                "date": fields.Datetime.to_datetime("2026-01-05 09:00:00"),
                "body_text": "Original body line 1\nOriginal body line 2",
                "in_reply_to": "<parent@ext.com>",
                "references": "<root@ext.com> <parent@ext.com>",
            }
        )

    # -- assertion helpers -------------------------------------------------

    def _send_reply(self, smtp=None, imap=None, **kw):
        """Call ``_send_message`` for a reply with default valid args."""
        vals = {
            "to": "customer@ext.com",
            "cc": "",
            "subject": "Re: Original subject",
            "body_text": "A reply body",
            "reply_to_message": self.original,
        }
        vals.update(kw)
        return self.mailbox._send_message(**vals)

    @staticmethod
    def _parse(raw):
        return email.message_from_bytes(raw)


# ===========================================================================
# Subject prefixes per mode
# ===========================================================================


@tagged("standard", "at_install")
class TestComposeSubject(SendTestMixin, TransactionCase):
    """``_compose_subject`` normalizes Re:/Fwd: prefixes per mode."""

    def setUp(self):
        super().setUp()
        self.setUpSend()

    def test_reply_adds_re_prefix(self):
        self.assertEqual(self.mailbox._compose_subject("Hello", "reply"), "Re: Hello")

    def test_reply_all_adds_re_prefix(self):
        self.assertEqual(
            self.mailbox._compose_subject("Hello", "reply_all"), "Re: Hello"
        )

    def test_reply_does_not_double_prefix(self):
        self.assertEqual(
            self.mailbox._compose_subject("RE: Hello", "reply"), "RE: Hello"
        )

    def test_forward_adds_fwd_prefix(self):
        self.assertEqual(
            self.mailbox._compose_subject("Hello", "forward"), "Fwd: Hello"
        )

    def test_forward_does_not_double_prefix(self):
        self.assertEqual(
            self.mailbox._compose_subject("FWD: Hello", "forward"), "FWD: Hello"
        )

    def test_plain_mode_keeps_subject(self):
        self.assertEqual(self.mailbox._compose_subject("Anything", "plain"), "Anything")


# ===========================================================================
# Threading headers + From/body
# ===========================================================================


@tagged("standard", "at_install")
class TestSendHeadersAndThreading(SendTestMixin, TransactionCase):
    """Reply threading headers; forward gets a fresh Message-ID."""

    def setUp(self):
        super().setUp()
        self.setUpSend()

    def test_reply_sets_in_reply_to_and_references(self):
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self._send_reply(smtp=smtp)
        msg = smtp.sent[0]
        self.assertEqual(msg["In-Reply-To"], "<orig@ext.com>")
        refs = (msg["References"] or "").split()
        self.assertIn("<orig@ext.com>", refs)

    def test_reply_references_include_original_chain(self):
        """References = original references + original message-id (order)."""
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self._send_reply(smtp=smtp)
        msg = smtp.sent[0]
        self.assertEqual(
            msg["References"],
            "<root@ext.com> <parent@ext.com> <orig@ext.com>",
        )

    def test_forward_has_no_in_reply_to_and_fresh_message_id(self):
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self._send_reply(
                smtp=smtp,
                to="friend@other.com",
                subject="Fwd: Original subject",
                reply_to_message=self.original,
                forward=True,
            )
        msg = smtp.sent[0]
        self.assertIsNone(msg["In-Reply-To"])
        self.assertFalse((msg["References"] or "").strip())
        self.assertTrue(msg["Message-ID"])
        self.assertNotEqual(msg["Message-ID"], "<orig@ext.com>")

    def test_reply_message_id_is_fresh(self):
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self._send_reply(smtp=smtp)
        msg = smtp.sent[0]
        self.assertTrue(msg["Message-ID"])
        self.assertNotEqual(msg["Message-ID"], "<orig@ext.com>")

    def test_from_header_is_mailbox_from_email(self):
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self._send_reply(smtp=smtp)
        self.assertEqual(smtp.sent[0]["From"], "me@send.com")

    def test_to_and_cc_headers_set(self):
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self._send_reply(smtp=smtp, cc="cc1@x.com, cc2@x.com")
        msg = smtp.sent[0]
        self.assertEqual(msg["To"], "customer@ext.com")
        self.assertEqual(msg["Cc"], "cc1@x.com, cc2@x.com")

    def test_plain_text_body_sent(self):
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self._send_reply(smtp=smtp, body_text="Hello\nWorld")
        msg = smtp.sent[0]
        self.assertEqual(msg.get_content_type(), "text/plain")
        self.assertIn("Hello", msg.get_content())


# ===========================================================================
# Sent-folder APPEND + \Answered mirroring + local sent record
# ===========================================================================


@tagged("standard", "at_install")
class TestSentAppendAndAnswered(SendTestMixin, TransactionCase):
    """APPEND target selection, failure surfacing, \\Answered mirror."""

    def setUp(self):
        super().setUp()
        self.setUpSend()

    def test_append_uses_role_sent_folder(self):
        imap = FakeImapClient()
        with (
            mock.patch(SMTP_PATCH, FakeSmtpClient()),
            mock.patch(IMAP_PATCH, imap),
        ):
            self._send_reply()
        self.assertEqual(len(imap.appends), 1)
        _bytes, folder, flag_set, _date = imap.appends[0]
        self.assertEqual(folder, "Sent")
        self.assertIn("\\Seen", flag_set)
        # APPENDed copy == sent message (same Message-ID).
        parsed = self._parse(_bytes)
        self.assertTrue(parsed["Message-ID"])

    def test_append_falls_back_to_sent_folder_name_when_no_role(self):
        # No role='sent' folder; only a folder named by sent_folder_name.
        self.sent.role = "other"
        imap = FakeImapClient()
        with (
            mock.patch(SMTP_PATCH, FakeSmtpClient()),
            mock.patch(IMAP_PATCH, imap),
        ):
            self._send_reply()
        _bytes, folder, _flags, _date = imap.appends[0]
        self.assertEqual(folder, "Sent")

    def test_append_failure_surfaced_not_swallowed(self):
        """SMTP OK + APPEND fails → send succeeds, warning surfaced."""
        smtp = FakeSmtpClient()
        imap = FakeImapClient(append_fail=Exception("APPEND failed"))
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, imap),
        ):
            result = self._send_reply(smtp=smtp)
        # The mail was sent — never claim the send failed.
        self.assertEqual(len(smtp.sent), 1)
        self.assertTrue(result["append_warning"])
        # The local Sent mirror still exists (the send happened).
        self.assertTrue(result["message"])

    def test_reply_mirrors_answered_flag_and_store(self):
        imap = FakeImapClient()
        with (
            mock.patch(SMTP_PATCH, FakeSmtpClient()),
            mock.patch(IMAP_PATCH, imap),
        ):
            self._send_reply()
        self.assertEqual(len(imap.stores), 1)
        uids, flag_set, value = imap.stores[0]
        self.assertEqual(uids, [7])
        self.assertIn("\\Answered", flag_set)
        self.assertTrue(value)
        self.assertTrue(self.original.flag_answered)

    def test_forward_does_not_set_answered(self):
        imap = FakeImapClient()
        with (
            mock.patch(SMTP_PATCH, FakeSmtpClient()),
            mock.patch(IMAP_PATCH, imap),
        ):
            self._send_reply(
                to="friend@other.com",
                subject="Fwd: Original subject",
                forward=True,
            )
        self.assertEqual(imap.stores, [])
        self.assertFalse(self.original.flag_answered)

    def test_local_sent_record_created(self):
        """A local mirror lands in the Sent folder with the outgoing id."""
        smtp = FakeSmtpClient()
        imap = FakeImapClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, imap),
        ):
            result = self._send_reply(smtp=smtp)
        sent_rec = result["message"]
        self.assertEqual(sent_rec.folder_id.role, "sent")
        self.assertEqual(sent_rec.message_id, smtp.sent[0]["Message-ID"])
        self.assertTrue(sent_rec.flag_seen)
        self.assertEqual(sent_rec.email_to, "customer@ext.com")
        self.assertEqual(sent_rec.in_reply_to, "<orig@ext.com>")

    def test_reply_without_original_references_chain(self):
        """TRIANGULATE: no original references → References is just the
        original message-id (no leading/junk whitespace)."""
        self.original.references = ""
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self._send_reply(smtp=smtp)
        msg = smtp.sent[0]
        self.assertEqual(msg["References"], "<orig@ext.com>")

    def test_consecutive_sends_unique_local_uids(self):
        """TRIANGULATE: two sends before any sync → the local Sent mirrors
        get distinct temporary uids (UNIQUE(folder_id, uid) must hold)."""
        with (
            mock.patch(SMTP_PATCH, FakeSmtpClient()),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            first = self._send_reply(body_text="first")
            second = self._send_reply(body_text="second")
        self.assertNotEqual(first["message"].uid, second["message"].uid)
        self.assertEqual(self.sent.message_count, 2)  # the 2 sent mirrors

    def test_store_failure_does_not_break_the_send(self):
        """TRIANGULATE: IMAP STORE \\Answered failure after a successful send
        is logged + local mirror still set — the send itself never fails."""
        smtp = FakeSmtpClient()
        imap = FakeImapClient(store_fail=Exception("STORE failed"))
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, imap),
        ):
            result = self._send_reply(smtp=smtp)
        self.assertEqual(len(smtp.sent), 1)
        self.assertFalse(result["append_warning"])  # APPEND itself succeeded
        # Local mirror is still set (best-effort cosmetic flag).
        self.assertTrue(self.original.flag_answered)

    def test_smtp_failure_raises_usererror_no_partial_state(self):
        smtp = FakeSmtpClient(fail=Exception("SMTP auth failed with app-pass-XYZ123"))
        imap = FakeImapClient()
        msg_before = self.Msg.search_count([])
        att_before = self.IrAttachment.search_count([])
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, imap),
            self.assertRaises(UserError),
        ):
            self._send_reply(smtp=smtp)
        # No partial state: nothing mirrored, nothing persisted.
        self.assertFalse(self.original.flag_answered)
        self.assertEqual(imap.appends, [])
        self.assertEqual(imap.stores, [])
        self.assertEqual(self.Msg.search_count([]), msg_before)
        self.assertEqual(self.IrAttachment.search_count([]), att_before)

    def test_error_never_contains_credential(self):
        smtp = FakeSmtpClient(fail=Exception("login failed: app-pass-XYZ123"))
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
            self.assertRaises(UserError) as ctx,
        ):
            self._send_reply(smtp=smtp)
        self.assertNotIn("app-pass-XYZ123", str(ctx.exception))

    def test_empty_to_rejected(self):
        with (
            mock.patch(SMTP_PATCH, FakeSmtpClient()),
            mock.patch(IMAP_PATCH, FakeImapClient()),
            self.assertRaises(UserError),
        ):
            self._send_reply(to="   ")


# ===========================================================================
# Attachments on send
# ===========================================================================


@tagged("standard", "at_install")
class TestSendAttachments(SendTestMixin, TransactionCase):
    """Uploads + forward-originals are MIME-attached, persisted, linked."""

    def setUp(self):
        super().setUp()
        self.setUpSend()

    def test_upload_attachments_mime_attached(self):
        smtp = FakeSmtpClient()
        content = b"file-content-123"
        attachments = [
            {
                "name": "notes.txt",
                "content": content,
                "mimetype": "text/plain",
            }
        ]
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            result = self._send_reply(smtp=smtp, attachments=attachments)
        msg = smtp.sent[0]
        parts = list(msg.iter_attachments())
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].get_filename(), "notes.txt")
        # text/* parts decode to str via get_content(); binary parts stay bytes
        self.assertEqual(parts[0].get_content(), content.decode())
        # Persisted + linked to the local Sent message.
        self.assertEqual(len(result["message"].attachment_ids), 1)
        linked = result["message"].attachment_ids[0]
        self.assertEqual(linked.name, "notes.txt")
        self.assertEqual(linked.ir_attachment_id.datas, base64.b64encode(content))

    def test_attachments_stored_via_ir_attachment_filestore(self):
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            result = self._send_reply(
                smtp=smtp,
                attachments=[
                    {
                        "name": "a.bin",
                        "content": b"\x00\x01",
                        "mimetype": "application/octet-stream",
                    }
                ],
            )
        sent_rec = result["message"]
        ir_att = sent_rec.attachment_ids[0].ir_attachment_id
        self.assertEqual(ir_att.res_model, "mail.management.message")
        self.assertEqual(ir_att.res_id, sent_rec.id)
        self.assertEqual(ir_att.name, "a.bin")
        # bytes round-trip from the stored payload
        self.assertEqual(base64.b64decode(ir_att.datas), b"\x00\x01")

    def test_forward_includes_original_attachments(self):
        # Original message carries an attachment (as synced).
        ir_att = self.IrAttachment.sudo().create(
            {
                "name": "report.pdf",
                "datas": base64.b64encode(b"%PDF-fake").decode(),
                "res_model": "mail.management.message",
                "res_id": self.original.id,
                "mimetype": "application/pdf",
            }
        )
        self.WAttach.create(
            {
                "webmail_message_id": self.original.id,
                "name": "report.pdf",
                "mimetype": "application/pdf",
                "file_size": 9,
                "ir_attachment_id": ir_att.id,
            }
        )
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            result = self._send_reply(
                smtp=smtp,
                to="friend@other.com",
                subject="Fwd: Original subject",
                forward=True,
            )
        msg = smtp.sent[0]
        names = [p.get_filename() for p in msg.iter_attachments()]
        self.assertEqual(names, ["report.pdf"])
        self.assertEqual(list(msg.iter_attachments())[0].get_content(), b"%PDF-fake")
        # The sent local record has its own linked copy.
        self.assertEqual(
            result["message"].attachment_ids.mapped("name"), ["report.pdf"]
        )

    def test_sent_copy_appended_contains_attachments(self):
        smtp = FakeSmtpClient()
        imap = FakeImapClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, imap),
        ):
            self._send_reply(
                smtp=smtp,
                attachments=[
                    {"name": "z.txt", "content": b"zzz", "mimetype": "text/plain"}
                ],
            )
        appended = self._parse(imap.appends[0][0])
        names = [p.get_filename() for p in appended.walk() if p.get_filename()]
        self.assertIn("z.txt", names)


# ===========================================================================
# ImapClient.append seam (imap_tools kwarg compatibility)
# ===========================================================================


@tagged("standard", "at_install")
class TestImapAppendSeam(TransactionCase):
    """``ImapClient.append`` must call imap_tools ``MailBox.append(dt=...)``."""

    def test_append_passes_dt_kwarg(self):
        import datetime

        from odoo.addons.mail_management.lib.mail_imap_client import (  # type: ignore
            ImapClient,
        )

        with mock.patch(IMAP_LIB_PATCH) as MockMailBox:
            conn = MockMailBox.return_value
            client = ImapClient("h", 993, True, "u", "p")
            client._conn = conn
            dt = datetime.datetime(2026, 1, 5, 9, 0, 0)
            client.append(b"RAW", folder="Sent", flag_set=["\\Seen"], date=dt)
        conn.append.assert_called_once_with(
            b"RAW", folder="Sent", flag_set=["\\Seen"], dt=dt
        )


# ===========================================================================
# /mail_management/send controller
# ===========================================================================


def _controller():
    from odoo.addons.mail_management.controllers.main import (  # type: ignore
        MailManagementController,
    )

    return MailManagementController()


@contextmanager
def _mock_request(env):
    """Patch BOTH ``odoo.http.request`` (the real proxy — read dynamically by
    ``Controller.env`` and by Odoo's ``_()`` translator frame-walking) and the
    ``request`` name imported in the controller module, with the same fake.
    Without the real-proxy patch, ``_()`` falls into its last-resort path and
    dereferences ``Controller.env`` (None between requests) → crash."""
    fake = type("FakeRequest", (), {"env": env, "session": {}})()
    with (
        mock.patch("odoo.http.request", fake),
        mock.patch("odoo.addons.mail_management.controllers.main.request", fake),
    ):
        yield fake


@tagged("standard", "at_install")
class TestSendController(SendTestMixin, TransactionCase):
    """``/mail_management/send``: owner-scoped, redacted, attachment-aware."""

    def setUp(self):
        super().setUp()
        self.setUpSend()
        self.controller = _controller()
        group_user = self.env.ref("mail_management.group_mail_management_user")
        self.user_a = self.env["res.users"].create(
            {
                "name": "Send A",
                "login": "send_a@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )
        self.user_b = self.env["res.users"].create(
            {
                "name": "Send B",
                "login": "send_b@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )
        # Give the mailbox to user A.
        self.mailbox.user_id = self.user_a
        # (message.user_id is a stored related field — it follows)

    def _env(self, user):
        return self.env(user=user)

    @contextmanager
    def _happy_mock(self, smtp=None, imap=None):
        """Patch both transport seams for the duration of the block."""
        with (
            mock.patch(SMTP_PATCH, smtp or FakeSmtpClient()),
            mock.patch(IMAP_PATCH, imap or FakeImapClient()),
        ):
            yield

    def test_send_reply_via_controller(self):
        smtp = FakeSmtpClient()
        imap = FakeImapClient()
        with (
            _mock_request(self._env(self.user_a)),
            self._happy_mock(smtp, imap),
        ):
            result = self.controller.send(
                mailbox_id=self.mailbox.id,
                to="customer@ext.com",
                subject="Re: Original subject",
                body_text="Reply from controller",
                mode="reply",
                reply_to_message_id=self.original.id,
            )
        self.assertTrue(result["success"])
        self.assertEqual(smtp.sent[0]["In-Reply-To"], "<orig@ext.com>")
        self.assertTrue(self.original.flag_answered)

    def test_controller_subject_prefix_per_mode(self):
        smtp = FakeSmtpClient()
        imap = FakeImapClient()
        with (
            _mock_request(self._env(self.user_a)),
            self._happy_mock(smtp, imap),
        ):
            self.controller.send(
                mailbox_id=self.mailbox.id,
                to="customer@ext.com",
                subject="Original subject",
                body_text="x",
                mode="reply",
                reply_to_message_id=self.original.id,
            )
        self.assertEqual(smtp.sent[0]["Subject"], "Re: Original subject")

        smtp2 = FakeSmtpClient()
        with _mock_request(self._env(self.user_a)), self._happy_mock(smtp2):
            self.controller.send(
                mailbox_id=self.mailbox.id,
                to="friend@other.com",
                subject="Original subject",
                body_text="x",
                mode="forward",
                reply_to_message_id=self.original.id,
            )
        self.assertEqual(smtp2.sent[0]["Subject"], "Fwd: Original subject")

    def test_cross_user_mailbox_isolation(self):
        """User B cannot send from user A's mailbox (ir.rule + guard)."""
        smtp = FakeSmtpClient()
        with (
            _mock_request(self._env(self.user_b)),
            self._happy_mock(smtp),
            self.assertRaises(UserError),
        ):
            self.controller.send(
                mailbox_id=self.mailbox.id,
                to="victim@ext.com",
                subject="Re: x",
                body_text="x",
                mode="reply",
                reply_to_message_id=self.original.id,
            )
        self.assertEqual(smtp.sent, [])

    def test_cross_user_reply_message_rejected(self):
        """Replying to another user's message id is rejected, not silently
        de-threaded."""
        # User B owns their own mailbox…
        mb_b = self.Mailbox.create(_mailbox_vals(name="B", imap_user="b@send.com"))
        self.Folder.create(
            {
                "mailbox_id": mb_b.id,
                "imap_name": "Sent",
                "name": "Sent",
                "role": "sent",
            }
        )
        smtp = FakeSmtpClient()
        with (
            _mock_request(self._env(self.user_b)),
            self._happy_mock(smtp),
            self.assertRaises(UserError),
        ):
            self.controller.send(
                mailbox_id=mb_b.id,
                to="customer@ext.com",
                subject="Re: Original subject",
                body_text="x",
                mode="reply",
                reply_to_message_id=self.original.id,  # A's message
            )
        self.assertEqual(smtp.sent, [])

    def test_controller_attachments_base64(self):
        smtp = FakeSmtpClient()
        imap = FakeImapClient()
        payload = [
            {
                "name": "up.txt",
                "datas": base64.b64encode(b"upload-data").decode(),
                "mimetype": "text/plain",
            }
        ]
        with (
            _mock_request(self._env(self.user_a)),
            self._happy_mock(smtp, imap),
        ):
            result = self.controller.send(
                mailbox_id=self.mailbox.id,
                to="customer@ext.com",
                subject="Re: Original subject",
                body_text="x",
                mode="reply",
                reply_to_message_id=self.original.id,
                attachments=payload,
            )
        self.assertTrue(result["success"])
        names = [p.get_filename() for p in smtp.sent[0].iter_attachments()]
        self.assertEqual(names, ["up.txt"])

    def test_smtp_failure_redacted_via_controller(self):
        smtp = FakeSmtpClient(fail=Exception("auth failed app-pass-XYZ123"))
        with (
            _mock_request(self._env(self.user_a)),
            self._happy_mock(smtp),
            self.assertRaises(UserError) as ctx,
        ):
            self.controller.send(
                mailbox_id=self.mailbox.id,
                to="customer@ext.com",
                subject="Re: x",
                body_text="x",
                mode="reply",
                reply_to_message_id=self.original.id,
            )
        self.assertNotIn("app-pass-XYZ123", str(ctx.exception))


# ===========================================================================
# Compose UI wiring (headless — no browser)
# ===========================================================================


def _module_file(*parts):
    path = get_module_path("mail_management")
    return os.path.join(path, *parts)


@tagged("standard", "at_install")
class TestComposeUiWiring(TransactionCase):
    """Reading-pane buttons + ComposeDialog component/assets wiring."""

    def test_manifest_version_bumped_5(self):
        with open(_module_file("__manifest__.py"), encoding="utf-8") as fh:
            manifest = ast.literal_eval(fh.read())
        self.assertEqual(manifest["version"], "19.0.17.0.0")

    def test_reading_pane_has_compose_buttons(self):
        with open(
            _module_file("static/src/xml/reading_pane.xml"), encoding="utf-8"
        ) as fh:
            xml = fh.read()
        for mode in ("reply", "reply_all", "forward"):
            self.assertIn(f'data-mode="{mode}"', xml)
        self.assertIn("o_webmail_compose_btn", xml)
        self.assertIn("compose(", xml)

    def test_reading_pane_js_accepts_oncompose_prop(self):
        with open(
            _module_file("static/src/components/reading_pane.js"),
            encoding="utf-8",
        ) as fh:
            js = fh.read()
        self.assertIn("onCompose", js)

    def test_compose_dialog_component_exists(self):
        self.assertTrue(
            os.path.exists(_module_file("static/src/components/compose_dialog.js"))
        )
        self.assertTrue(
            os.path.exists(_module_file("static/src/xml/compose_dialog.xml"))
        )
        with open(
            _module_file("static/src/components/compose_dialog.js"),
            encoding="utf-8",
        ) as fh:
            js = fh.read()
        self.assertIn("/mail_management/send", js)

    def test_compose_dialog_in_manifest_assets(self):
        with open(_module_file("__manifest__.py"), encoding="utf-8") as fh:
            manifest = ast.literal_eval(fh.read())
        backend = manifest["assets"]["web.assets_backend"]
        self.assertIn(
            "mail_management/static/src/components/compose_dialog.js", backend
        )
        self.assertIn("mail_management/static/src/xml/compose_dialog.xml", backend)

    def test_webmail_client_wires_compose(self):
        with open(_module_file("static/src/webmail_client.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("ComposeDialog", js)
        self.assertIn('useService("dialog")', js)
