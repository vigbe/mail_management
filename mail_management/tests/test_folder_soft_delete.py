"""Strict-TDD RED tests for PR-2h — folder management in Settings + soft-delete.

User request (2026-09-09): in Folders (settings) rename/create/delete folders,
with a delete confirmation; deleted-folder contents go to TRASH retained for
30 days (Gmail-style) then auto-purged.

Contract under test (openspec/changes/webmail-complete-management PR-2h):

* ``mail.management.message.trashed_date`` (Datetime, indexed) — the LOCAL
  retention anchor for messages sitting in a ``role='trash'`` folder.
* ``mailbox._delete_folder(folder)`` — SOFT-DELETE semantics (replaces the
  PR-2b hard delete):
  - mapped system folders (inbox/sent/drafts/trash/junk) REFUSE deletion
    (UserError — unmap the role first);
  - a folder with NO messages → IMAP DELETE + local unlink (as before);
  - a folder WITH messages → move every message to the mailbox's
    ``role='trash'`` folder through the PR-2b move machinery in batches
    (COPY + ``\\Deleted`` + EXPUNGE on the source), create LOCAL Trash
    copies stamped ``trashed_date=now``, then IMAP DELETE the now-empty
    source folder + remove the local source folder (cascading its local
    records);
  - NO mapped Trash folder → UserError telling the user to map one first.
  IMAP-first as ever: any server failure raises ``UserError`` BEFORE any
  local change.
* Trash-sync approximation: syncing a ``role='trash'`` folder stamps any
  message landing there WITHOUT a ``trashed_date`` with the sync time.
* ``mail.management.settings.trash_retention_days`` (Integer, default 30,
  clamped 1..365 on save).
* ``mailbox._cron_purge_trash()`` — daily best-effort purge: for each active
  mailbox's Trash folder, hard-delete (``\\Deleted`` + EXPUNGE + local
  unlink) the messages with ``trashed_date < now - retention`` (OWNER's
  setting); worker-safe per-mailbox savepoints; skips mailboxes without a
  Trash mapping.
* Controller ``POST /mail_management/folder/delete`` — soft semantics, no
  ``force``; returns ``{'moved_count': N, 'folder_removed': True}``.
* Settings ▸ Folders tab gains Create/Rename/Delete actions + a Trash
  retention input; the tree kebab confirm text switches to the soft-delete
  message.

All transports are MOCKED — no real network calls.
"""

import ast
import inspect
import os
from contextlib import contextmanager
from datetime import timedelta
from unittest import mock

from odoo import fields  # type: ignore
from odoo.exceptions import UserError  # type: ignore
from odoo.modules.module import get_module_path  # type: ignore
from odoo.tests.common import TransactionCase, tagged  # type: ignore

IMAP_PATCH = "odoo.addons.mail_management.models.mail_management.ImapClient"

#: A tiny valid RFC822 message reused by the trash-sync stamping tests.
RAW_MESSAGE = (
    b"From: Alice <alice@ext.com>\r\n"
    b"To: bob@softdel.com\r\n"
    b"Subject: Hello trash\r\n"
    b"Message-ID: <hello-trash-1@ext.com>\r\n"
    b"Date: Wed, 01 Jan 2026 09:00:00 +0000\r\n"
    b"\r\n"
    b"Body text\r\n"
)


# ---------------------------------------------------------------------------
# Fake IMAP seam (folder soft-delete + purge + sync read side)
# ---------------------------------------------------------------------------


class FakeImapSoftClient:
    """Stands in for ``ImapClient`` at the model seam — records every op."""

    def __init__(self, fail_ops=None, fail_folder=None):
        self.calls = []  # (op_name, args...)
        self.fail_ops = set(fail_ops or [])
        self.fail_folder = fail_folder  # folder-scoped failure (isolation test)

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _record(self, op, *args):
        if op in self.fail_ops and (
            self.fail_folder is None or self.fail_folder in args
        ):
            raise OSError(f"imap {op} down")
        self.calls.append((op, *args))

    # --- movement / purge seams ------------------------------------------
    def move_messages(self, src_imap_name, uids, dest_imap_name):
        self._record("move_messages", src_imap_name, list(uids), dest_imap_name)

    def delete_messages(self, src_imap_name, uids):
        self._record("delete_messages", src_imap_name, list(uids))

    def expunge(self, src_imap_name):
        self._record("expunge", src_imap_name)

    # --- folder CRUD seams -------------------------------------------------
    def delete_folder(self, name):
        self._record("delete_folder", name)

    # --- read side used by _sync_folder (trash stamping) -------------------
    def uidvalidity(self, folder_name):
        return "7"

    def search_uids(self, folder_name, since_uid=0):
        return []

    def fetch_messages(self, folder_name, uids, mark_seen=False):
        return iter([])


class FakeImapSyncClient(FakeImapSoftClient):
    """Fake that reports ONE new uid in the synced folder (stamping test)."""

    def __init__(self, new_uid=0):
        super().__init__()
        self.new_uid = new_uid

    def search_uids(self, folder_name, since_uid=0):
        return [self.new_uid] if self.new_uid > since_uid else []

    def fetch_messages(self, folder_name, uids, mark_seen=False):
        return iter([(uid, RAW_MESSAGE, ["\\Seen"]) for uid in uids])


def _mb_vals(**kw):
    base = {
        "name": "SoftDel Test",
        "provider_type": "generic",
        "imap_host": "imap.softdel.com",
        "imap_user": "me@softdel.com",
        "smtp_host": "smtp.softdel.com",
        "from_email": "me@softdel.com",
        "credential": "app-pass-SOFTDEL",
        "state": "active",
    }
    base.update(kw)
    return base


def _folder_vals(mailbox, imap_name, **kw):
    base = {
        "mailbox_id": mailbox.id,
        "imap_name": imap_name,
        "name": imap_name,
        "role": "other",
    }
    base.update(kw)
    return base


def _msg_vals(mailbox, folder, uid, **kw):
    base = {
        "mailbox_id": mailbox.id,
        "folder_id": folder.id,
        "uid": uid,
        "message_id": f"<m{uid}@softdel.com>",
        "subject": f"S {uid}",
        "email_from": f"s{uid}@ext.com",
        "date": fields.Datetime.to_datetime("2026-01-01 09:00:00"),
        "body_text": f"b{uid}",
    }
    base.update(kw)
    return base


@contextmanager
def _mock_request(env):
    """Patch BOTH ``odoo.http.request`` and the controller-module import."""
    fake = type("FakeRequest", (), {"env": env, "session": {}})()
    with (
        mock.patch("odoo.http.request", fake),
        mock.patch("odoo.addons.mail_management.controllers.main.request", fake),
    ):
        yield fake


# ===========================================================================
# mailbox._delete_folder — SOFT-DELETE semantics
# ===========================================================================


@tagged("standard", "at_install")
class TestFolderSoftDeleteModel(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]

        self.mailbox = self.Mailbox.create(_mb_vals())
        self.inbox = self.Folder.create(
            _folder_vals(self.mailbox, "INBOX", name="Inbox", role="inbox")
        )
        self.trash = self.Folder.create(
            _folder_vals(self.mailbox, "Trash", role="trash")
        )
        self.projects = self.Folder.create(_folder_vals(self.mailbox, "Projects"))

    def _msg(self, folder, uid, **kw):
        return self.Msg.create(_msg_vals(self.mailbox, folder, uid, **kw))

    # -- empty folder: direct delete (unchanged PR-2b path) ------------------

    def test_empty_folder_direct_delete(self):
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            result = self.mailbox._delete_folder(self.projects)
        self.assertEqual(fake.calls, [("delete_folder", "Projects")])
        self.assertFalse(self.projects.exists())
        self.assertEqual(result["moved_count"], 0)

    # -- folder with messages: SOFT delete -----------------------------------

    def test_folder_with_messages_moves_to_trash(self):
        m1 = self._msg(self.projects, 1)
        m2 = self._msg(self.projects, 2)
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            result = self.mailbox._delete_folder(self.projects)
        # Server: COPY-to-trash + \Deleted + EXPUNGE, then DELETE the folder.
        self.assertIn(("move_messages", "Projects", [1, 2], "Trash"), fake.calls)
        self.assertIn(("delete_folder", "Projects"), fake.calls)
        # Local: source folder (and its local records) gone.
        self.assertFalse(self.projects.exists())
        self.assertFalse(m1.exists())
        self.assertFalse(m2.exists())
        # Local Trash copies exist WITH the retention stamp.
        copies = self.trash.message_ids
        self.assertEqual(len(copies), 2)
        self.assertTrue(all(c.trashed_date for c in copies))
        self.assertEqual(set(copies.mapped("subject")), {"S 1", "S 2"})
        self.assertEqual(
            set(copies.mapped("message_id")), {"<m1@softdel.com>", "<m2@softdel.com>"}
        )
        self.assertEqual(result["moved_count"], 2)

    def test_trash_copies_get_distinct_temp_uids(self):
        """Temporary uids keep UNIQUE(folder_id, uid) happy until the next
        Trash sync re-binds the copies to the real server uids."""
        for uid in range(1, 4):
            self._msg(self.projects, uid)
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._delete_folder(self.projects)
        uids = self.trash.message_ids.mapped("uid")
        self.assertEqual(len(uids), len(set(uids)))
        # First temp uid is exactly the watermark +1_000_000 (the same
        # convention as the local Sent mirror in _send_message).
        self.assertTrue(all(u >= 1_000_000 for u in uids))

    def test_trash_copies_carry_flags_and_bodies(self):
        self._msg(self.projects, 1, flag_seen=True, flag_flagged=True)
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._delete_folder(self.projects)
        copy = self.trash.message_ids
        self.assertEqual(len(copy), 1)
        self.assertTrue(copy.flag_seen)
        self.assertTrue(copy.flag_flagged)
        self.assertEqual(copy.body_text, "b1")

    def test_trash_copies_share_attachment_pointers(self):
        msg = self._msg(self.projects, 1)
        ir_att = (
            self.env["ir.attachment"]
            .sudo()
            .create({"name": "f.txt", "raw": b"hello", "mimetype": "text/plain"})
        )
        self.env["mail.management.attachment"].create(
            {
                "webmail_message_id": msg.id,
                "name": "f.txt",
                "mimetype": "text/plain",
                "file_size": 5,
                "ir_attachment_id": ir_att.id,
            }
        )
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._delete_folder(self.projects)
        copy = self.trash.message_ids
        self.assertEqual(len(copy.attachment_ids), 1)
        # The pointer is duplicated, the filestore record SHARED (no copy).
        self.assertEqual(copy.attachment_ids.ir_attachment_id.id, ir_att.id)
        self.assertTrue(ir_att.exists())

    def test_soft_delete_existing_trash_content_keeps_its_stamp(self):
        """Soft-deleting into a Trash folder that already holds stamped
        messages never rewrites the existing retention anchors."""
        keeper = self._msg(self.trash, 90, trashed_date="2026-01-01 00:00:00")
        self._msg(self.projects, 1)
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._delete_folder(self.projects)
        self.assertEqual(
            keeper.trashed_date, fields.Datetime.to_datetime("2026-01-01 00:00:00")
        )
        new_copy = self.trash.message_ids.filtered(lambda m: m.uid > 1_000_000)
        self.assertEqual(len(new_copy), 1)
        self.assertTrue(new_copy.trashed_date)

    # -- guards ---------------------------------------------------------------

    def test_no_trash_folder_refused(self):
        """Messages to relocate but no role='trash' folder mapped → UserError,
        nothing happens (ties into the PR-2g role mapping)."""
        bare = self.Mailbox.create(
            _mb_vals(name="No Trash", imap_user="bare@softdel.com")
        )
        folder = self.Folder.create(_folder_vals(bare, "Projects"))
        msg = self.Msg.create(_msg_vals(bare, folder, 1))
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            bare._delete_folder(folder)
        self.assertEqual(fake.calls, [])
        self.assertTrue(folder.exists())
        self.assertTrue(msg.exists())

    def test_system_folders_refuse_deletion(self):
        """Mapped system folders must be UNMAPPED before deletion (protects
        Sent/Trash from accidental nukes)."""
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            for role, name in (
                ("sent", "Sent"),
                ("drafts", "Drafts"),
                ("junk", "Junk"),
                ("archive", None),  # archive/all stay DELETABLE (boundary)
            ):
                folder = (
                    self.inbox
                    if role == "inbox"
                    else self.Folder.create(
                        _folder_vals(self.mailbox, name or "X", role=role)
                    )
                )
                if role == "archive":
                    # archive is not a protected role — delete proceeds (soft,
                    # folder is empty → direct path).
                    self.mailbox._delete_folder(folder)
                    continue
                with self.assertRaises(UserError):
                    self.mailbox._delete_folder(folder)
        self.assertIn(("delete_folder", "X"), fake.calls)  # archive went through
        self.assertTrue(self.inbox.exists())
        self.assertTrue(self.trash.exists())  # the Trash role is protected too

    def test_system_folder_inbox_refused(self):
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._delete_folder(self.inbox)
        self.assertEqual(fake.calls, [])
        self.assertTrue(self.inbox.exists())

    def test_system_folder_trash_refused(self):
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._delete_folder(self.trash)
        self.assertEqual(fake.calls, [])
        self.assertTrue(self.trash.exists())

    def test_delete_folder_of_other_mailbox_rejected(self):
        other_mb = self.Mailbox.create(
            _mb_vals(name="Other MB", imap_user="other@softdel.com")
        )
        foreign = self.Folder.create(_folder_vals(other_mb, "Foreign"))
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._delete_folder(foreign)
        self.assertEqual(fake.calls, [])
        self.assertTrue(foreign.exists())

    # -- IMAP-first failure isolation -----------------------------------------

    def test_move_failure_no_local_change(self):
        msg = self._msg(self.projects, 1)
        fake = FakeImapSoftClient(fail_ops={"move_messages"})
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._delete_folder(self.projects)
        self.assertTrue(self.projects.exists())
        self.assertTrue(msg.exists())
        self.assertEqual(len(self.trash.message_ids), 0)

    def test_folder_delete_failure_no_local_change(self):
        """The moves succeeded server-side but DELETE refused → UserError,
        the LOCAL mirror stays untouched (IMAP-first policy)."""
        msg = self._msg(self.projects, 1)
        fake = FakeImapSoftClient(fail_ops={"delete_folder"})
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._delete_folder(self.projects)
        self.assertTrue(self.projects.exists())
        self.assertTrue(msg.exists())
        self.assertEqual(len(self.trash.message_ids), 0)

    def test_empty_folder_delete_failure_no_local_change(self):
        fake = FakeImapSoftClient(fail_ops={"delete_folder"})
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._delete_folder(self.projects)
        self.assertTrue(self.projects.exists())

    # -- TRIANGULATE: batching --------------------------------------------------

    def test_many_messages_batched(self):
        for uid in range(1, 131):
            self._msg(self.projects, uid)
        self.env["ir.config_parameter"].sudo().set_str(
            "mail_management.move_batch_size", 50
        )
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._delete_folder(self.projects)
        moves = [c for c in fake.calls if c[0] == "move_messages"]
        self.assertEqual(len(moves), 3)
        self.assertEqual([len(m[2]) for m in moves], [50, 50, 30])
        self.assertEqual(len(self.trash.message_ids), 130)


# ===========================================================================
# mail.management.message.trashed_date field
# ===========================================================================


@tagged("standard", "at_install")
class TestTrashedDateField(TransactionCase):
    def test_trashed_date_field_exists(self):
        field = self.env["mail.management.message"]._fields["trashed_date"]
        self.assertEqual(field.type, "datetime")
        self.assertFalse(field.required)
        self.assertTrue(field.index)


# ===========================================================================
# Trash-sync stamping approximation
# ===========================================================================


@tagged("standard", "at_install")
class TestTrashSyncStamping(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]

        self.mailbox = self.Mailbox.create(_mb_vals())
        self.inbox = self.Folder.create(
            _folder_vals(self.mailbox, "INBOX", name="Inbox", role="inbox")
        )
        self.trash = self.Folder.create(
            _folder_vals(self.mailbox, "Trash", role="trash")
        )
        self.projects = self.Folder.create(_folder_vals(self.mailbox, "Projects"))

    def _msg(self, folder, uid, **kw):
        return self.Msg.create(_msg_vals(self.mailbox, folder, uid, **kw))

    def test_sync_trash_folder_stamps_unstamped(self):
        """Pre-existing Trash content (no local anchor) is stamped on the
        first observed sync — the documented approximation."""
        msg = self._msg(self.trash, 1)
        self.assertFalse(msg.trashed_date)
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._sync_folder(fake, self.trash)
        self.assertTrue(msg.trashed_date)

    def test_sync_trash_folder_keeps_existing_stamp(self):
        msg = self._msg(self.trash, 1, trashed_date="2026-01-01 00:00:00")
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._sync_folder(fake, self.trash)
        self.assertEqual(
            msg.trashed_date, fields.Datetime.to_datetime("2026-01-01 00:00:00")
        )

    def test_sync_non_trash_folder_does_not_stamp(self):
        msg = self._msg(self.projects, 1)
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._sync_folder(fake, self.projects)
        self.assertFalse(msg.trashed_date)

    def test_newly_synced_trash_message_gets_stamped(self):
        """A message landing in Trash during a sync (e.g. deleted from
        another client) is stamped immediately."""
        self.trash.last_uid = 0
        fake = FakeImapSyncClient(new_uid=5)
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._sync_folder(fake, self.trash)
        msg = self.Msg.search([("folder_id", "=", self.trash.id), ("uid", "=", 5)])
        self.assertEqual(len(msg), 1)
        self.assertTrue(msg.trashed_date)


# ===========================================================================
# settings.trash_retention_days (default + clamps)
# ===========================================================================


@tagged("standard", "at_install")
class TestTrashRetentionSettings(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Settings = self.env["mail.management.settings"]

    def test_default_30(self):
        settings = self.Settings.create({"user_id": self.env.user.id})
        self.assertEqual(settings.trash_retention_days, 30)

    def test_clamp_zero_to_one_on_create(self):
        settings = self.Settings.create(
            {"user_id": self.env.user.id, "trash_retention_days": 0}
        )
        self.assertEqual(settings.trash_retention_days, 1)

    def test_clamp_400_to_365_on_create(self):
        settings = self.Settings.create(
            {"user_id": self.env.user.id, "trash_retention_days": 400}
        )
        self.assertEqual(settings.trash_retention_days, 365)

    def test_clamp_on_write(self):
        settings = self.Settings.create({"user_id": self.env.user.id})
        settings.write({"trash_retention_days": 0})
        self.assertEqual(settings.trash_retention_days, 1)
        settings.write({"trash_retention_days": 400})
        self.assertEqual(settings.trash_retention_days, 365)

    def test_in_range_value_untouched(self):
        settings = self.Settings.create(
            {"user_id": self.env.user.id, "trash_retention_days": 7}
        )
        self.assertEqual(settings.trash_retention_days, 7)


# ===========================================================================
# mailbox._cron_purge_trash + _purge_aged_trash (retention purge)
# ===========================================================================


@tagged("standard", "at_install")
class TestCronPurgeTrash(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]
        self.Settings = self.env["mail.management.settings"]

        self.mailbox = self.Mailbox.create(_mb_vals())
        self.trash = self.Folder.create(
            _folder_vals(self.mailbox, "Trash", role="trash")
        )
        self.settings = self.Settings.create(
            {"user_id": self.mailbox.user_id.id, "trash_retention_days": 30}
        )

    def _trash_msg(self, uid, trashed_date):
        return self.Msg.create(
            _msg_vals(
                self.mailbox,
                self.trash,
                uid,
                message_id=f"<t{uid}@softdel.com>",
                trashed_date=trashed_date,
            )
        )

    def test_older_than_retention_purged(self):
        old = self._trash_msg(1, fields.Datetime.now() - timedelta(days=31))
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            purged = self.mailbox._purge_aged_trash()
        self.assertEqual(purged, 1)
        self.assertIn(("delete_messages", "Trash", [1]), fake.calls)
        self.assertIn(("expunge", "Trash"), fake.calls)
        self.assertFalse(old.exists())

    def test_newer_than_retention_kept(self):
        fresh = self._trash_msg(1, fields.Datetime.now() - timedelta(days=29))
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            purged = self.mailbox._purge_aged_trash()
        self.assertEqual(purged, 0)
        self.assertTrue(fresh.exists())
        self.assertEqual(fake.calls, [])

    def test_unstamped_messages_never_purged(self):
        """No trashed_date → no KNOWN age → left alone (the next trash sync
        stamps it, then retention applies)."""
        unstamped = self.Msg.create(_msg_vals(self.mailbox, self.trash, 1))
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            purged = self.mailbox._purge_aged_trash()
        self.assertEqual(purged, 0)
        self.assertTrue(unstamped.exists())

    def test_boundary_exactly_n_days_kept(self):
        """Exactly N days old is NOT yet past the window (strict <).  The
        clock is frozen so the boundary is deterministic."""
        fixed = fields.Datetime.to_datetime("2026-06-30 12:00:00")
        at_boundary = self._trash_msg(1, fixed - timedelta(days=30))
        past_boundary = self._trash_msg(2, fixed - timedelta(days=30, seconds=1))
        fake = FakeImapSoftClient()
        with (
            mock.patch(IMAP_PATCH, fake),
            mock.patch.object(fields.Datetime, "now", return_value=fixed),
        ):
            purged = self.mailbox._purge_aged_trash()
        self.assertEqual(purged, 1)
        self.assertTrue(at_boundary.exists())
        self.assertFalse(past_boundary.exists())

    def test_owner_settings_drive_retention(self):
        """The retention window comes from the mailbox OWNER's settings."""
        self.settings.write({"trash_retention_days": 5})
        aged = self._trash_msg(1, fields.Datetime.now() - timedelta(days=6))
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            purged = self.mailbox._purge_aged_trash()
        self.assertEqual(purged, 1)
        self.assertFalse(aged.exists())

    def test_no_settings_row_defaults_to_30(self):
        self.settings.unlink()
        kept = self._trash_msg(1, fields.Datetime.now() - timedelta(days=29))
        gone = self._trash_msg(2, fields.Datetime.now() - timedelta(days=31))
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            purged = self.mailbox._purge_aged_trash()
        self.assertEqual(purged, 1)
        self.assertTrue(kept.exists())
        self.assertFalse(gone.exists())

    def test_mailbox_without_trash_mapping_skipped(self):
        self.Mailbox.create(_mb_vals(name="No Trash", imap_user="bare@softdel.com"))
        old = self._trash_msg(1, fields.Datetime.now() - timedelta(days=40))
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            self.Mailbox._cron_purge_trash(limit=10)
        # The mapped mailbox was purged; the bare one raised nothing.
        self.assertFalse(old.exists())

    def test_cron_only_touches_active_mailboxes(self):
        self.mailbox.state = "draft"
        old = self._trash_msg(1, fields.Datetime.now() - timedelta(days=40))
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            self.Mailbox._cron_purge_trash(limit=10)
        self.assertTrue(old.exists())
        self.assertEqual(fake.calls, [])

    def test_cron_isolates_mailbox_failures(self):
        """One mailbox's IMAP failure never aborts the batch (best-effort
        with logging, like the sync cron)."""
        other_mb = self.Mailbox.create(
            _mb_vals(name="Other MB", imap_user="other@softdel.com")
        )
        other_trash = self.Folder.create(_folder_vals(other_mb, "Basura", role="trash"))
        doomed = self._trash_msg(1, fields.Datetime.now() - timedelta(days=40))
        survivor = self.Msg.create(
            _msg_vals(
                other_mb,
                other_trash,
                1,
                message_id="<surv@softdel.com>",
                trashed_date=fields.Datetime.now() - timedelta(days=40),
            )
        )
        # IMAP fails ONLY for the first mailbox's Trash folder.
        fake = FakeImapSoftClient(fail_ops={"delete_messages"}, fail_folder="Trash")
        with mock.patch(IMAP_PATCH, fake):
            self.Mailbox._cron_purge_trash(limit=10)  # must NOT raise
        self.assertTrue(doomed.exists())  # its purge failed (logged)
        self.assertFalse(survivor.exists())  # the other mailbox went through

    def test_purge_imap_failure_leaves_local_records(self):
        old = self._trash_msg(1, fields.Datetime.now() - timedelta(days=40))
        fake = FakeImapSoftClient(fail_ops={"delete_messages"})
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._purge_aged_trash()
        self.assertTrue(old.exists())

    def test_cron_xml_record_loaded(self):
        cron = self.env.ref(
            "mail_management.cron_purge_trash", raise_if_not_found=False
        )
        self.assertTrue(cron, "the daily purge cron record must load with the module")
        self.assertEqual(cron.interval_number, 1)
        self.assertEqual(cron.interval_type, "days")
        self.assertIn("_cron_purge_trash", cron.code)


# ===========================================================================
# POST /mail_management/folder/delete — soft semantics + isolation
# ===========================================================================


@tagged("standard", "at_install")
class TestFolderSoftDeleteController(TransactionCase):
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
                "name": "SoftDel A",
                "login": "softdel_a@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )
        self.user_b = self.env["res.users"].create(
            {
                "name": "SoftDel B",
                "login": "softdel_b@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )

        self.mailbox_a = self.Mailbox.with_user(self.user_a).create(
            _mb_vals(name="A MB", imap_user="a@softdel.com")
        )
        self.trash_a = self.Folder.create(
            _folder_vals(self.mailbox_a, "Trash", role="trash")
        )
        self.projects_a = self.Folder.create(_folder_vals(self.mailbox_a, "Projects"))

    def _env(self, user):
        return self.env(user=user)

    def _msg(self, folder, uid):
        return self.Msg.create(_msg_vals(self.mailbox_a, folder, uid))

    def test_folder_delete_signature_has_no_force(self):
        """PR-2h contract change: confirmation is client-side; the server
        endpoint no longer takes a ``force`` flag."""
        params = inspect.signature(self.controller.folder_delete).parameters
        self.assertNotIn("force", params)

    def test_folder_delete_empty_folder(self):
        fake = FakeImapSoftClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.folder_delete(folder_id=self.projects_a.id)
        self.assertTrue(result["success"])
        self.assertEqual(result["moved_count"], 0)
        self.assertTrue(result["folder_removed"])
        self.assertFalse(self.projects_a.exists())

    def test_folder_delete_with_messages_summary(self):
        self._msg(self.projects_a, 1)
        self._msg(self.projects_a, 2)
        fake = FakeImapSoftClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.folder_delete(folder_id=self.projects_a.id)
        self.assertTrue(result["success"])
        self.assertEqual(result["moved_count"], 2)
        self.assertFalse(self.projects_a.exists())
        copies = self.trash_a.message_ids
        self.assertEqual(len(copies), 2)
        self.assertTrue(all(c.trashed_date for c in copies))

    def test_folder_delete_system_folder_refused(self):
        fake = FakeImapSoftClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
            self.assertRaises(UserError),
        ):
            self.controller.folder_delete(folder_id=self.trash_a.id)
        self.assertEqual(fake.calls, [])
        self.assertTrue(self.trash_a.exists())

    def test_folder_delete_no_trash_mapped_refused(self):
        bare = self.Mailbox.with_user(self.user_a).create(
            _mb_vals(name="Bare", imap_user="bare2@softdel.com")
        )
        folder = self.Folder.create(_folder_vals(bare, "Stuck"))
        self._msg(folder, 1)
        fake = FakeImapSoftClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
            self.assertRaises(UserError),
        ):
            self.controller.folder_delete(folder_id=folder.id)
        self.assertEqual(fake.calls, [])
        self.assertTrue(folder.exists())

    def test_folder_delete_cross_user_rejected(self):
        self._msg(self.projects_a, 1)
        fake = FakeImapSoftClient()
        with (
            _mock_request(self._env(self.user_b)),
            mock.patch(IMAP_PATCH, fake),
            self.assertRaises(UserError),
        ):
            self.controller.folder_delete(folder_id=self.projects_a.id)
        self.assertEqual(fake.calls, [])
        self.assertTrue(self.projects_a.exists())

    # -- settings round-trip (trash retention) --------------------------------

    def test_settings_payload_has_trash_retention(self):
        with _mock_request(self._env(self.user_a)):
            payload = self.controller.get_settings()
        self.assertEqual(payload["trash_retention_days"], 30)

    def test_settings_save_clamps_trash_retention(self):
        with _mock_request(self._env(self.user_a)):
            result = self.controller.save_settings(trash_retention_days=0)
            self.assertEqual(result["trash_retention_days"], 1)
            result = self.controller.save_settings(trash_retention_days=400)
            self.assertEqual(result["trash_retention_days"], 365)
            result = self.controller.save_settings(trash_retention_days=14)
            self.assertEqual(result["trash_retention_days"], 14)


# ===========================================================================
# TRIANGULATE (PR-2h) — current-view delete; retention change moves the
# purge boundary
# ===========================================================================


@tagged("standard", "at_install")
class TestSoftDeleteTriangulate(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]
        self.Settings = self.env["mail.management.settings"]

        self.mailbox = self.Mailbox.create(_mb_vals())
        self.trash = self.Folder.create(
            _folder_vals(self.mailbox, "Trash", role="trash")
        )
        self.projects = self.Folder.create(_folder_vals(self.mailbox, "Projects"))
        self.settings = self.Settings.create(
            {"user_id": self.mailbox.user_id.id, "trash_retention_days": 30}
        )

    def test_deleted_folder_leaves_the_tree_payload(self):
        """Deleting the folder that IS the current view: the tree payload no
        longer contains it (the client's _keepOrAutoSelect then re-selects)."""
        from odoo.addons.mail_management.controllers.main import (  # type: ignore
            MailManagementController,
        )

        self.Msg.create(_msg_vals(self.mailbox, self.projects, 1))
        controller = MailManagementController()
        env = self.env(user=self.mailbox.user_id)
        fake = FakeImapSoftClient()
        with (
            _mock_request(env),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = controller.folder_delete(folder_id=self.projects.id)
            payload = controller.get_folders()
        self.assertEqual(result["moved_count"], 1)
        folder_ids = [f["id"] for mb in payload for f in mb["folders"]]
        self.assertNotIn(self.projects.id, folder_ids)
        self.assertIn(self.trash.id, folder_ids)

    def test_retention_change_moves_the_purge_boundary(self):
        old = self.Msg.create(
            _msg_vals(
                self.mailbox,
                self.trash,
                1,
                trashed_date=fields.Datetime.now() - timedelta(days=20),
            )
        )
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            self.assertEqual(self.mailbox._purge_aged_trash(), 0)  # 30d window
        self.settings.write({"trash_retention_days": 14})  # tighter window
        with mock.patch(IMAP_PATCH, fake):
            self.assertEqual(self.mailbox._purge_aged_trash(), 1)
        self.assertFalse(old.exists())

    def test_folder_resurrect_and_delete_again(self):
        """Deleting a re-created folder of the same name works and lands in
        the same Trash (the local copies from the first delete stay)."""
        self.Msg.create(_msg_vals(self.mailbox, self.projects, 1))
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._delete_folder(self.projects)
        self.assertEqual(len(self.trash.message_ids), 1)
        reborn = self.Folder.create(_folder_vals(self.mailbox, "Projects"))
        self.Msg.create(
            _msg_vals(
                self.mailbox,
                reborn,
                1,
                message_id="<again@softdel.com>",
            )
        )
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._delete_folder(reborn)
        self.assertEqual(len(self.trash.message_ids), 2)
        self.assertIn(("delete_folder", "Projects"), fake.calls)

    def test_purge_selects_only_aged_within_trash(self):
        """Mixed Trash: only the aged messages are purged — ONE grouped
        IMAP delete for them, the fresh one never leaves."""
        aged1 = self.Msg.create(
            _msg_vals(
                self.mailbox,
                self.trash,
                1,
                message_id="<a1@softdel.com>",
                trashed_date=fields.Datetime.now() - timedelta(days=40),
            )
        )
        aged2 = self.Msg.create(
            _msg_vals(
                self.mailbox,
                self.trash,
                2,
                message_id="<a2@softdel.com>",
                trashed_date=fields.Datetime.now() - timedelta(days=35),
            )
        )
        fresh = self.Msg.create(
            _msg_vals(
                self.mailbox,
                self.trash,
                3,
                message_id="<f3@softdel.com>",
                trashed_date=fields.Datetime.now() - timedelta(days=2),
            )
        )
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            purged = self.mailbox._purge_aged_trash()
        self.assertEqual(purged, 2)
        self.assertEqual(
            [c for c in fake.calls if c[0] == "delete_messages"],
            [("delete_messages", "Trash", [1, 2])],
        )
        self.assertFalse(aged1.exists())
        self.assertFalse(aged2.exists())
        self.assertTrue(fresh.exists())

    def test_retention_clamped_to_one_day_moves_boundary(self):
        """Retention 0 clamps to 1: a 2-day-old trashed message is already
        past the window, a half-day-old one is not."""
        self.settings.write({"trash_retention_days": 0})
        self.assertEqual(self.settings.trash_retention_days, 1)  # clamped
        gone = self.Msg.create(
            _msg_vals(
                self.mailbox,
                self.trash,
                1,
                message_id="<g@softdel.com>",
                trashed_date=fields.Datetime.now() - timedelta(days=2),
            )
        )
        kept = self.Msg.create(
            _msg_vals(
                self.mailbox,
                self.trash,
                2,
                message_id="<k@softdel.com>",
                trashed_date=fields.Datetime.now() - timedelta(hours=12),
            )
        )
        fake = FakeImapSoftClient()
        with mock.patch(IMAP_PATCH, fake):
            purged = self.mailbox._purge_aged_trash()
        self.assertEqual(purged, 1)
        self.assertFalse(gone.exists())
        self.assertTrue(kept.exists())


# ===========================================================================
# es_CL: the NEW Python-side term of PR-2h must translate
# ===========================================================================


@tagged("standard", "at_install")
class TestSoftDeleteTermTranslatedEsCl(TransactionCase):
    def test_no_trash_folder_term_translated_es_cl(self):
        self.env["res.lang"]._activate_lang("es_CL")
        env = self.env["mail.management.mailbox"].with_context(lang="es_CL").env
        self.assertEqual(
            env._(
                "No Trash folder is mapped for this mailbox. "
                "Map one in Settings ▸ Folders first."
            ),
            "No hay ninguna carpeta de Papelera asignada a este buzón. "
            "Asigne una primero en Configuración ▸ Carpetas.",
        )


# ===========================================================================
# PR-2h UI wiring (headless, template/JS source assertions) + i18n + version
# ===========================================================================


def _read(relative):
    path = os.path.join(get_module_path("mail_management"), relative)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@tagged("standard", "at_install")
class TestSoftDeleteUiWiring(TransactionCase):
    """Folders tab actions + retention input + tree confirm text + cron data."""

    def test_settings_folders_tab_actions(self):
        xml = _read("static/src/xml/settings_dialog.xml")
        for needle in (
            "o_webmail_settings_create_folder",  # per-mailbox Create button
            "o_webmail_settings_folder_rename",  # per-folder Rename action
            "o_webmail_settings_folder_delete",  # per-folder Delete action
            "o_webmail_settings_trash_retention",  # retention input
            "isSystemFolder",  # disabled delete on system roles
            "systemFolderDeleteTooltip",
        ):
            self.assertIn(needle, xml)

    def test_settings_dialog_component_wiring(self):
        js = _read("static/src/components/settings_dialog.js")
        for needle in (
            "isSystemFolder",
            "trashRetentionDays",
            "onCreateFolder",
            "onRenameFolder",
            "onDeleteFolder",
        ):
            self.assertIn(needle, js)
        # The retention draft rides the normal Save payload.
        self.assertIn("trashRetentionDays: this.state.trashRetentionDays", js)

    def test_client_soft_delete_confirm_reads_retention(self):
        js = _read("static/src/webmail_client.js")
        for needle in (
            "trashRetentionDays",
            "trash_retention_days",
            "settingsCreateFolder",
            "settingsRenameFolder",
            "settingsDeleteFolder",
            "automatically deleted after %s days",
            "moved_count",
        ):
            self.assertIn(needle, js)

    def test_client_delete_folder_reloads_current_view(self):
        """Deleting the folder that IS the current view re-selects: the
        delete flow reloads folders AND messages."""
        js = _read("static/src/webmail_client.js")
        start = js.index("async deleteFolder(")
        end = js.index("_captureNextAfterRemoval")
        body = js[start:end]
        self.assertIn("loadFolders", body)
        self.assertIn("loadMessages", body)

    def test_tree_kebab_keeps_rename_delete(self):
        xml = _read("static/src/xml/folder_tree.xml")
        for needle in (
            "o_webmail_folder_menu_rename",
            "o_webmail_folder_menu_delete",
        ):
            self.assertIn(needle, xml)

    def test_cron_data_has_purge_record(self):
        xml = _read("data/mail_management_cron.xml")
        for needle in ("cron_purge_trash", "_cron_purge_trash", "days"):
            self.assertIn(needle, xml)

    def test_i18n_new_entries_mirrored(self):
        pot = _read("i18n/mail_management.pot")
        po = _read("i18n/es_CL.po")
        new_terms = (
            "Trash retention (days)",
            "The folder '%s' is a mapped system folder",
            "No Trash folder is mapped for this mailbox.",
            "automatically deleted after %s days",
            "Mapped system folders cannot be deleted",
        )
        for term in new_terms:
            self.assertIn(term, pot)
            self.assertIn(term, po)
        # The dead PR-2b hard-delete confirm msgid is GONE from both files.
        dead = "Delete it anyway? They will also be removed from Odoo."
        self.assertNotIn(dead, pot)
        self.assertNotIn(dead, po)

    def test_manifest_version_bumped_to_pr2h(self):
        manifest = ast.literal_eval(_read("__manifest__.py"))
        self.assertEqual(manifest["version"], "20.0.17.0.0")
