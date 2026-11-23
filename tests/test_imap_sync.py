"""Tests for the IMAP sync data layer (PR-1b-i).

Strict-TDD RED: written before the folder/message/attachment models
and sync methods exist.  All IMAP operations are MOCKED — no real network.

Covers design sections:
  A.2 (folder model), A.3 (message model + HIGH-RISK A.3.1 UIDVALIDITY),
  A.4 (attachment model), C (sync design), H (test plan).
"""

import email.utils
from email.message import EmailMessage
from unittest import mock

import psycopg2
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged

IMAP_PATCH = "odoo.addons.mail_management.models.mail_management.ImapClient"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_raw_message(
    *,
    subject="Test Subject",
    from_addr="sender@test.com",
    to_addr="recip@test.com",
    body_html="",
    body_text="",
    message_id="",
    date_str=None,
    in_reply_to="",
    cc="",
    references="",
    attachments=None,
):
    """Build a raw RFC822 message as bytes (for mocking IMAP fetch output)."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    if message_id:
        msg["Message-ID"] = message_id
    msg["Date"] = date_str or email.utils.formatdate(localtime=False)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if cc:
        msg["Cc"] = cc
    if references:
        msg["References"] = references

    if body_text and body_html:
        msg.set_content(body_text)
        msg.add_alternative(body_html, subtype="html")
    elif body_html:
        msg.set_content(body_html, subtype="html")
    elif body_text:
        msg.set_content(body_text)
    else:
        msg.set_content("")

    if attachments:
        for att in attachments:
            msg.add_attachment(
                att["content"],
                maintype=att.get("maintype", "application"),
                subtype=att.get("subtype", "octet-stream"),
                filename=att["name"],
            )
    return msg.as_bytes()


class FakeImapClient:
    """Mockable IMAP client for sync tests — no real network."""

    def __init__(self, folders=None):
        # folders: dict of folder_name -> {'uidvalidity': str, 'messages': [(uid, raw, flags), ...]}
        self._folders = folders or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_folders(self):
        return list(self._folders.keys())

    def uidvalidity(self, folder_name):
        return str(self._folders[folder_name]["uidvalidity"])

    def search_uids(self, folder_name, since_uid=0):
        msgs = self._folders.get(folder_name, {}).get("messages", [])
        return sorted(uid for uid, _, _ in msgs if uid > since_uid)

    def fetch_messages(self, folder_name, uids):
        msgs = self._folders.get(folder_name, {}).get("messages", [])
        for uid, raw, flags in msgs:
            if uid in uids:
                yield uid, raw, flags


def _mailbox_vals(**kw):
    base = {
        "name": "Sync Test",
        "provider_type": "generic",
        "imap_host": "imap.sync.com",
        "imap_user": "sync@test.com",
        "smtp_host": "smtp.sync.com",
        "from_email": "sync@test.com",
        "credential": "sync-pass-123",
        "state": "active",
    }
    base.update(kw)
    return base


# ===========================================================================
# Folder model
# ===========================================================================


@tagged("standard", "at_install")
class TestFolderModel(TransactionCase):
    """mail.management.folder: fields, defaults, UNIQUE constraint."""

    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.mailbox = self.Mailbox.create(_mailbox_vals())

    def test_folder_create(self):
        """A folder can be created with required fields."""
        folder = self.Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "INBOX",
                "name": "Inbox",
                "role": "inbox",
            }
        )
        self.assertTrue(folder.id)
        self.assertEqual(folder.imap_name, "INBOX")
        self.assertEqual(folder.role, "inbox")

    def test_folder_user_id_stored_from_mailbox(self):
        """user_id is stored from the mailbox relation (enables ir.rule)."""
        folder = self.Folder.create(
            {"mailbox_id": self.mailbox.id, "imap_name": "INBOX"}
        )
        self.assertEqual(folder.user_id, self.mailbox.user_id)

    def test_folder_defaults(self):
        """last_uid defaults to 0; role defaults to 'other'."""
        folder = self.Folder.create(
            {"mailbox_id": self.mailbox.id, "imap_name": "INBOX"}
        )
        self.assertEqual(folder.last_uid, 0)
        self.assertEqual(folder.role, "other")

    def test_folder_unique_constraint(self):
        """UNIQUE(mailbox_id, imap_name) rejects duplicates."""
        self.Folder.create(
            {"mailbox_id": self.mailbox.id, "imap_name": "INBOX"}
        )
        with self.assertRaises(psycopg2.IntegrityError):
            self.Folder.create(
                {"mailbox_id": self.mailbox.id, "imap_name": "INBOX"}
            )

    def test_folder_unique_allows_different_name(self):
        """Same mailbox but different imap_name is allowed."""
        self.Folder.create(
            {"mailbox_id": self.mailbox.id, "imap_name": "INBOX"}
        )
        f2 = self.Folder.create(
            {"mailbox_id": self.mailbox.id, "imap_name": "Sent"}
        )
        self.assertTrue(f2.id)

    def test_folder_counts_compute(self):
        """message_count and unread_count compute correctly."""
        folder = self.Folder.create(
            {"mailbox_id": self.mailbox.id, "imap_name": "INBOX"}
        )
        Msg = self.env["mail.management.message"]
        Msg.create(
            {
                "mailbox_id": self.mailbox.id,
                "folder_id": folder.id,
                "uid": 1,
                "flag_seen": True,
            }
        )
        Msg.create(
            {
                "mailbox_id": self.mailbox.id,
                "folder_id": folder.id,
                "uid": 2,
                "flag_seen": False,
            }
        )
        self.assertEqual(folder.message_count, 2)
        self.assertEqual(folder.unread_count, 1)


# ===========================================================================
# Message model
# ===========================================================================


@tagged("standard", "at_install")
class TestMessageModel(TransactionCase):
    """mail.management.message: fields, UNIQUE(folder_id, uid), sanitization."""

    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]
        self.mailbox = self.Mailbox.create(_mailbox_vals())
        self.folder = self.Folder.create(
            {"mailbox_id": self.mailbox.id, "imap_name": "INBOX", "role": "inbox"}
        )

    def test_message_create(self):
        """A message can be created with all fields."""
        msg = self.Msg.create(
            {
                "mailbox_id": self.mailbox.id,
                "folder_id": self.folder.id,
                "uid": 42,
                "message_id": "<abc@test.com>",
                "subject": "Hello",
                "email_from": "from@test.com",
                "email_to": "to@test.com",
                "body_html": "<p>Body</p>",
                "body_text": "Body",
                "flag_seen": True,
                "flag_flagged": True,
            }
        )
        self.assertTrue(msg.id)
        self.assertEqual(msg.uid, 42)
        self.assertTrue(msg.flag_seen)
        self.assertTrue(msg.flag_flagged)

    def test_message_user_id_stored(self):
        """user_id is stored from the mailbox relation."""
        msg = self.Msg.create(
            {
                "mailbox_id": self.mailbox.id,
                "folder_id": self.folder.id,
                "uid": 1,
            }
        )
        self.assertEqual(msg.user_id, self.mailbox.user_id)

    def test_message_unique_folder_uid(self):
        """UNIQUE(folder_id, uid) rejects duplicates."""
        self.Msg.create(
            {"mailbox_id": self.mailbox.id, "folder_id": self.folder.id, "uid": 10}
        )
        with self.assertRaises(psycopg2.IntegrityError):
            self.Msg.create(
                {
                    "mailbox_id": self.mailbox.id,
                    "folder_id": self.folder.id,
                    "uid": 10,
                }
            )

    def test_message_unique_allows_different_folder(self):
        """Same uid in a different folder is allowed (UIDs are per-folder)."""
        f2 = self.Folder.create(
            {"mailbox_id": self.mailbox.id, "imap_name": "Sent", "role": "sent"}
        )
        self.Msg.create(
            {"mailbox_id": self.mailbox.id, "folder_id": self.folder.id, "uid": 5}
        )
        msg2 = self.Msg.create(
            {"mailbox_id": self.mailbox.id, "folder_id": f2.id, "uid": 5}
        )
        self.assertTrue(msg2.id)

    def test_message_html_sanitized_on_store(self):
        """body_html is sanitized when stored (fields.Html sanitize=True)."""
        msg = self.Msg.create(
            {
                "mailbox_id": self.mailbox.id,
                "folder_id": self.folder.id,
                "uid": 1,
                "body_html": "<script>alert(1)</script><p>hello</p>",
            }
        )
        self.assertNotIn("<script>", msg.body_html or "")
        self.assertIn("hello", msg.body_html or "")


# ===========================================================================
# Attachment model
# ===========================================================================


@tagged("standard", "at_install")
class TestAttachmentModel(TransactionCase):
    """mail.management.attachment: ir.attachment link, ondelete='restrict'."""

    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]
        self.Attachment = self.env["mail.management.attachment"]
        self.mailbox = self.Mailbox.create(_mailbox_vals())
        self.folder = self.Folder.create(
            {"mailbox_id": self.mailbox.id, "imap_name": "INBOX"}
        )
        self.message = self.Msg.create(
            {"mailbox_id": self.mailbox.id, "folder_id": self.folder.id, "uid": 1}
        )

    def test_attachment_links_ir_attachment(self):
        """Attachment links to ir.attachment filestore (no blob column)."""
        import base64

        ir_att = self.env["ir.attachment"].create(
            {
                "name": "doc.pdf",
                "datas": base64.b64encode(b"PDFDATA").decode(),
                "res_model": "mail.management.message",
                "res_id": self.message.id,
                "mimetype": "application/pdf",
            }
        )
        att = self.Attachment.create(
            {
                "webmail_message_id": self.message.id,
                "name": "doc.pdf",
                "ir_attachment_id": ir_att.id,
                "mimetype": "application/pdf",
                "file_size": 7,
            }
        )
        self.assertTrue(att.id)
        self.assertEqual(att.ir_attachment_id, ir_att)
        self.assertTrue(self.message.has_attachment)

    def test_attachment_ondelete_restrict(self):
        """Deleting a linked ir.attachment is restricted (FK RESTRICT)."""
        import base64

        ir_att = self.env["ir.attachment"].create(
            {
                "name": "doc.txt",
                "datas": base64.b64encode(b"TXT").decode(),
                "res_model": "mail.management.message",
                "res_id": self.message.id,
            }
        )
        self.Attachment.create(
            {
                "webmail_message_id": self.message.id,
                "name": "doc.txt",
                "ir_attachment_id": ir_att.id,
            }
        )
        with self.assertRaises(psycopg2.IntegrityError):
            ir_att.unlink()


# ===========================================================================
# Sync: idempotency, incremental, UIDVALIDITY (HIGH-RISK)
# ===========================================================================


@tagged("standard", "at_install")
class TestSyncCore(TransactionCase):
    """_sync_mailbox: incremental fetch, idempotency, sanitization."""

    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]
        self.mailbox = self.Mailbox.create(_mailbox_vals())
        self.folder = self.Folder.create(
            {"mailbox_id": self.mailbox.id, "imap_name": "INBOX", "role": "inbox"}
        )

    def _sync_with(self, folders_config):
        """Patch ImapClient and call _sync_mailbox."""
        with mock.patch(IMAP_PATCH, return_value=FakeImapClient(folders_config)):
            self.mailbox._sync_mailbox()

    # ------------------------------------------------------------------
    # Incremental fetch + last_uid advance
    # ------------------------------------------------------------------
    def test_sync_creates_messages(self):
        """_sync_mailbox creates messages from fetched data."""
        raw = make_raw_message(
            subject="Hello",
            message_id="<m1@test.com>",
            body_html="<p>Hi</p>",
            body_text="Hi",
        )
        self._sync_with(
            {"INBOX": {"uidvalidity": "123", "messages": [(1, raw, {"\\Seen"})]}}
        )
        msgs = self.Msg.search([("folder_id", "=", self.folder.id)])
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs.uid, 1)
        self.assertEqual(msgs.subject, "Hello")
        self.assertTrue(msgs.flag_seen)
        self.assertIn("Hi", msgs.body_html)

    def test_sync_advances_last_uid(self):
        """last_uid advances to the highest fetched uid."""
        raw = make_raw_message(subject="A", message_id="<a@t.com>")
        self._sync_with(
            {"INBOX": {"uidvalidity": "1", "messages": [(5, raw, set())]}}
        )
        self.assertEqual(self.folder.last_uid, 5)

    def test_sync_incremental_fetch(self):
        """Only uids > last_uid are fetched on subsequent syncs."""
        raw1 = make_raw_message(subject="First", message_id="<f@t.com>")
        raw2 = make_raw_message(subject="Second", message_id="<s@t.com>")
        config = {
            "INBOX": {
                "uidvalidity": "1",
                "messages": [
                    (1, raw1, set()),
                    (2, raw2, set()),
                ],
            }
        }
        # First sync fetches both.
        self._sync_with(config)
        self.assertEqual(self.folder.last_uid, 2)
        self.assertEqual(self.Msg.search_count([("folder_id", "=", self.folder.id)]), 2)

        # Second sync with only uid > 2 (nothing new).
        self._sync_with(config)
        self.assertEqual(self.Msg.search_count([("folder_id", "=", self.folder.id)]), 2)

    def test_sync_new_messages_incremental(self):
        """New messages with higher uids are fetched incrementally."""
        raw1 = make_raw_message(subject="First", message_id="<f@t.com>")
        config = {"INBOX": {"uidvalidity": "1", "messages": [(1, raw1, set())]}}
        self._sync_with(config)
        self.assertEqual(self.folder.last_uid, 1)

        # Server now has uid 3 too.
        raw3 = make_raw_message(subject="Third", message_id="<t@t.com>")
        config2 = {
            "INBOX": {
                "uidvalidity": "1",
                "messages": [(1, raw1, set()), (3, raw3, set())],
            }
        }
        self._sync_with(config2)
        self.assertEqual(self.folder.last_uid, 3)
        self.assertEqual(self.Msg.search_count([("folder_id", "=", self.folder.id)]), 2)

    # ------------------------------------------------------------------
    # Idempotency (#1 HIGH-RISK)
    # ------------------------------------------------------------------
    def test_sync_idempotent_no_duplicates(self):
        """Re-syncing the same messages creates no duplicates."""
        raw = make_raw_message(
            subject="Dup test", message_id="<dup@t.com>", body_text="Hello"
        )
        config = {"INBOX": {"uidvalidity": "1", "messages": [(1, raw, set())]}}

        self._sync_with(config)
        self._sync_with(config)

        count = self.Msg.search_count([("folder_id", "=", self.folder.id)])
        self.assertEqual(count, 1)

    def test_sync_updates_existing_message(self):
        """Upsert updates an existing message's flags, not duplicates."""
        raw = make_raw_message(
            subject="Flag test", message_id="<flag@t.com>"
        )
        # Create via direct upsert (simulates first fetch).
        self.mailbox._upsert_message(self.folder, 1, raw, set())
        msg = self.Msg.search([("folder_id", "=", self.folder.id)])
        self.assertFalse(msg.flag_flagged)

        # Re-upsert the same (folder, uid) with \Flagged.
        self.mailbox._upsert_message(self.folder, 1, raw, {"\\Flagged"})
        msgs = self.Msg.search([("folder_id", "=", self.folder.id)])
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs.flag_flagged)

    # ------------------------------------------------------------------
    # Sanitization on sync
    # ------------------------------------------------------------------
    def test_sync_sanitizes_html_body(self):
        """HTML body with <script> is sanitized when stored via sync."""
        raw = make_raw_message(
            subject="XSS",
            message_id="<xss@t.com>",
            body_html="<script>alert(1)</script><p>safe</p>",
            body_text="safe",
        )
        self._sync_with(
            {"INBOX": {"uidvalidity": "1", "messages": [(1, raw, set())]}}
        )
        msg = self.Msg.search([("folder_id", "=", self.folder.id)])
        self.assertNotIn("<script>", msg.body_html or "")
        self.assertIn("safe", msg.body_html or "")

    def test_sync_stores_attachments(self):
        """Attachments are stored via ir.attachment filestore."""
        raw = make_raw_message(
            subject="With attach",
            message_id="<att@t.com>",
            body_text="See attached",
            attachments=[{"name": "doc.txt", "content": b"FILEDATA"}],
        )
        self._sync_with(
            {"INBOX": {"uidvalidity": "1", "messages": [(1, raw, set())]}}
        )
        msg = self.Msg.search([("folder_id", "=", self.folder.id)])
        self.assertTrue(msg.has_attachment)
        self.assertEqual(len(msg.attachment_ids), 1)
        self.assertEqual(msg.attachment_ids.name, "doc.txt")
        self.assertTrue(msg.attachment_ids.ir_attachment_id)

    # ------------------------------------------------------------------
    # TRIANGULATE: HIGH-RISK sync edge cases
    # ------------------------------------------------------------------
    def test_sync_partial_resume(self):
        """A large folder is fetched in batches across multiple sync ticks."""
        raws = [
            (
                i,
                make_raw_message(subject=f"Msg {i}", message_id=f"<m{i}@t.com>"),
                set(),
            )
            for i in range(1, 31)
        ]
        config = {"INBOX": {"uidvalidity": "1", "messages": raws}}
        self.env["ir.config_parameter"].sudo().set_param(
            "mail_management.fetch_batch_size", "10"
        )
        # First tick: 10 messages, last_uid = 10.
        self._sync_with(config)
        self.assertEqual(self.folder.last_uid, 10)
        self.assertEqual(
            self.Msg.search_count([("folder_id", "=", self.folder.id)]), 10
        )
        # Second tick: next 10, last_uid = 20.
        self._sync_with(config)
        self.assertEqual(self.folder.last_uid, 20)
        self.assertEqual(
            self.Msg.search_count([("folder_id", "=", self.folder.id)]), 20
        )
        # Third tick: last 10, last_uid = 30.
        self._sync_with(config)
        self.assertEqual(self.folder.last_uid, 30)
        self.assertEqual(
            self.Msg.search_count([("folder_id", "=", self.folder.id)]), 30
        )
        # Fourth tick: nothing new.
        self._sync_with(config)
        self.assertEqual(self.folder.last_uid, 30)
        self.assertEqual(
            self.Msg.search_count([("folder_id", "=", self.folder.id)]), 30
        )

    def test_sync_empty_folder(self):
        """Syncing a folder with zero messages does not crash."""
        self._sync_with({"INBOX": {"uidvalidity": "1", "messages": []}})
        self.assertEqual(self.folder.last_uid, 0)
        self.assertEqual(
            self.Msg.search_count([("folder_id", "=", self.folder.id)]), 0
        )

    def test_sync_html_with_script_and_img_onerror(self):
        """Body with <script> and <img onerror> is sanitized when stored."""
        raw = make_raw_message(
            subject="XSS tri",
            message_id="<xss3@t.com>",
            body_html=(
                "<script>alert(1)</script>"
                '<p>safe content</p>'
                '<img src="x" onerror="alert(1)">'
            ),
            body_text="fallback text",
        )
        self._sync_with(
            {"INBOX": {"uidvalidity": "1", "messages": [(1, raw, set())]}}
        )
        msg = self.Msg.search([("folder_id", "=", self.folder.id)])
        self.assertNotIn("<script>", msg.body_html or "")
        self.assertNotIn("onerror", msg.body_html or "")
        self.assertIn("safe content", msg.body_html or "")


# ===========================================================================
# UIDVALIDITY recovery (HIGH-RISK A.3.1)
# ===========================================================================


@tagged("standard", "at_install")
class TestUidvalidityRecovery(TransactionCase):
    """UIDVALIDITY change: reset last_uid, re-fetch, message_id upsert."""

    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]
        self.mailbox = self.Mailbox.create(_mailbox_vals())
        self.folder = self.Folder.create(
            {"mailbox_id": self.mailbox.id, "imap_name": "INBOX", "role": "inbox"}
        )

    def _sync_with(self, folders_config):
        with mock.patch(IMAP_PATCH, return_value=FakeImapClient(folders_config)):
            self.mailbox._sync_mailbox()

    def test_uidvalidity_recovery_no_crash(self):
        """UIDVALIDITY change resets last_uid and re-fetches without crashing."""
        raw = make_raw_message(subject="Survivor", message_id="<surv@t.com>")
        config1 = {"INBOX": {"uidvalidity": "100", "messages": [(1, raw, set())]}}
        self._sync_with(config1)
        self.assertEqual(self.folder.last_uid, 1)

        # UIDVALIDITY changes; same message reappears under new uid.
        config2 = {"INBOX": {"uidvalidity": "999", "messages": [(50, raw, set())]}}
        self._sync_with(config2)

        # last_uid reset then advanced; no crash, no orphan dupes.
        self.assertEqual(self.folder.uidvalidity, "999")
        # The message is re-linked by message_id (1 record, not 2).
        msgs = self.Msg.search([("folder_id", "=", self.folder.id)])
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs.uid, 50)

    def test_uidvalidity_preserves_flagged(self):
        """User-set \\Flagged is preserved across UIDVALIDITY change."""
        raw = make_raw_message(subject="Flagged", message_id="<flg@t.com>")
        config1 = {
            "INBOX": {"uidvalidity": "100", "messages": [(1, raw, {"\\Flagged"})]}
        }
        self._sync_with(config1)
        msg = self.Msg.search([("folder_id", "=", self.folder.id)])
        self.assertTrue(msg.flag_flagged)

        # UIDVALIDITY changes; message reappears WITHOUT \Flagged flag.
        config2 = {"INBOX": {"uidvalidity": "999", "messages": [(77, raw, set())]}}
        self._sync_with(config2)
        msgs = self.Msg.search([("folder_id", "=", self.folder.id)])
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs.flag_flagged, "Flag should be preserved")


# ===========================================================================
# Cron batching
# ===========================================================================


@tagged("standard", "at_install")
class TestCronBatching(TransactionCase):
    """_cron_sync_mailboxes(limit=N) processes at most N mailboxes."""

    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]

    def test_cron_batch_limit(self):
        """Only `limit` mailboxes are processed per cron tick."""
        for i in range(5):
            self.Mailbox.create(_mailbox_vals(name=f"MB{i}", imap_user=f"u{i}@t.com"))

        call_count = {"n": 0}

        def counting_sync(self_mb):
            call_count["n"] += 1

        with mock.patch.object(type(self.Mailbox), "_sync_mailbox", counting_sync):
            self.Mailbox._cron_sync_mailboxes(limit=2)
        self.assertEqual(call_count["n"], 2)

    def test_cron_one_failure_does_not_abort_batch(self):
        """A failing mailbox does not prevent others from syncing."""
        self.Mailbox.create(_mailbox_vals(name="OK", imap_user="ok@t.com"))
        self.Mailbox.create(_mailbox_vals(name="BAD", imap_user="bad@t.com"))
        syncs = []

        def selective_sync(self_mb):
            syncs.append(self_mb.name)
            if self_mb.name == "BAD":
                raise RuntimeError("boom")

        with mock.patch.object(type(self.Mailbox), "_sync_mailbox", selective_sync):
            self.Mailbox._cron_sync_mailboxes(limit=10)
        # Both mailboxes were attempted.
        self.assertIn("OK", syncs)
        self.assertIn("BAD", syncs)


# ===========================================================================
# Per-user isolation extended (folder/message/attachment)
# ===========================================================================


@tagged("standard", "at_install")
class TestIsolationExtended(TransactionCase):
    """User A's folders/messages/attachments are invisible to user B."""

    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]
        group_user = self.env.ref("mail_management.group_mail_management_user")
        group_manager = self.env.ref("mail_management.group_mail_management_manager")

        self.user_a = self.env["res.users"].create(
            {
                "name": "Sync User A",
                "login": "sync_iso_a@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )
        self.user_b = self.env["res.users"].create(
            {
                "name": "Sync User B",
                "login": "sync_iso_b@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )
        self.manager = self.env["res.users"].create(
            {
                "name": "Sync Manager",
                "login": "sync_iso_mgr@test",
                "group_ids": [(6, 0, [group_manager.id])],
            }
        )

        self.mailbox_a = self.Mailbox.with_user(self.user_a).create(
            _mailbox_vals(name="A MB", imap_host="ia.com", imap_user="a@ia.com")
        )
        self.folder_a = self.Folder.create(
            {
                "mailbox_id": self.mailbox_a.id,
                "imap_name": "INBOX",
                "role": "inbox",
            }
        )
        self.msg_a = self.Msg.create(
            {
                "mailbox_id": self.mailbox_a.id,
                "folder_id": self.folder_a.id,
                "uid": 1,
                "message_id": "<a@ia.com>",
            }
        )

    def test_user_b_cannot_see_folder_a(self):
        """User B does not see user A's folder."""
        folders = self.Folder.with_user(self.user_b).search([])
        self.assertNotIn(self.folder_a, folders)

    def test_user_b_cannot_see_message_a(self):
        """User B does not see user A's message."""
        msgs = self.Msg.with_user(self.user_b).search([])
        self.assertNotIn(self.msg_a, msgs)

    def test_user_b_cannot_read_message_a(self):
        """User B cannot read user A's message by id."""
        with self.assertRaises(AccessError):
            self.Msg.with_user(self.user_b).browse(self.msg_a.id).read(["subject"])

    def test_manager_sees_all_folders(self):
        """Manager sees all users' folders."""
        folders = self.Folder.with_user(self.manager).search([])
        self.assertIn(self.folder_a, folders)

    def test_manager_sees_all_messages(self):
        """Manager sees all users' messages."""
        msgs = self.Msg.with_user(self.manager).search([])
        self.assertIn(self.msg_a, msgs)
