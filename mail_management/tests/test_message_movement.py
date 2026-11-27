"""Strict-TDD RED tests for PR-2b — message movement (move + delete-to-Trash).

Contract under test (openspec/changes/webmail-complete-management):

* ``ImapClient`` lib seams: ``copy_messages``, ``delete_messages`` (flag
  ``\\Deleted``), ``expunge``, and ``move_messages`` = COPY + ``\\Deleted`` +
  EXPUNGE (portable — no raw IMAP MOVE).
* ``mailbox._move_messages(messages, dest_folder)`` — IMAP-FIRST policy: the
  IMAP move runs first; a failure raises ``UserError`` and NO local record is
  touched.  On success the LOCAL source records are deleted (the destination
  copy is re-synced by the folder sync — the destination folder gets a cheap
  same-connection refresh).  Cross-mailbox destinations are rejected.
* ``mailbox._delete_messages_to_trash(messages)`` — trash semantics: if the
  mailbox has a ``role='trash'`` folder the messages are MOVED there; messages
  already IN Trash are PERMANENTLY deleted (``\\Deleted`` + EXPUNGE + local
  unlink, no recovery).
* Controller ``POST /mail_management/message/{move,delete}``
  (``auth='user'``): per-user isolation via ir.rules; cross-user message ids
  are silently ignored, a cross-user destination folder is rejected.

All transports are MOCKED — no real network calls.
"""

import os
from contextlib import contextmanager
from datetime import timedelta
from unittest import mock

from odoo import fields  # type: ignore
from odoo.exceptions import UserError  # type: ignore
from odoo.modules.module import get_module_path  # type: ignore
from odoo.tests.common import TransactionCase, tagged  # type: ignore

IMAP_PATCH = "odoo.addons.mail_management.models.mail_management.ImapClient"
IMAP_LIB_PATCH = "odoo.addons.mail_management.lib.mail_imap_client.MailBox"


# ---------------------------------------------------------------------------
# Fake IMAP seam (records move/delete/trash calls — the PR-2b IMAP-first path)
# ---------------------------------------------------------------------------


class FakeImapMoveClient:
    """Stands in for ``ImapClient`` at the model seam — records movement ops.

    Also implements the read-side seams the same-connection destination
    refresh uses (``uidvalidity`` / ``search_uids`` / ``fetch_messages``) so
    the cheap refresh path is exercised without any network.
    """

    def __init__(self, fail_ops=None):
        self.calls = []  # (op_name, args...)
        self.fail_ops = set(fail_ops or {})

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _record(self, op, *args):
        if op in self.fail_ops:
            raise OSError(f"imap {op} down")
        self.calls.append((op, *args))

    # --- PR-2b movement seams (model-level tests mock these) -------------
    def move_messages(self, src_imap_name, uids, dest_imap_name):
        self._record("move_messages", src_imap_name, list(uids), dest_imap_name)

    def delete_messages(self, src_imap_name, uids):
        self._record("delete_messages", src_imap_name, list(uids))

    def expunge(self, src_imap_name):
        self._record("expunge", src_imap_name)

    def copy_messages(self, src_imap_name, uids, dest_imap_name):
        self._record("copy_messages", src_imap_name, list(uids), dest_imap_name)

    # --- read side used by the cheap destination refresh ------------------
    def uidvalidity(self, folder_name):
        return "1"

    def search_uids(self, folder_name, since_uid=0):
        self._record("search_uids", folder_name, since_uid)
        return []

    def fetch_messages(self, folder_name, uids, mark_seen=False):
        self._record("fetch_messages", folder_name, list(uids))
        return iter([])


def _mb_vals(**kw):
    base = {
        "name": "Move Test",
        "provider_type": "generic",
        "imap_host": "imap.move.com",
        "imap_user": "me@move.com",
        "smtp_host": "smtp.move.com",
        "from_email": "me@move.com",
        "credential": "app-pass-MOVE",
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
        "message_id": f"<m{uid}@move.com>",
        "subject": f"S {uid}",
        "email_from": f"s{uid}@ext.com",
        "date": fields.Datetime.to_datetime("2026-01-01 09:00:00")
        + timedelta(hours=uid),
        "body_text": f"b{uid}",
    }
    base.update(kw)
    return base


@contextmanager
def _mock_request(env):
    fake = type("FakeRequest", (), {"env": env, "session": {}})()
    with (
        mock.patch("odoo.http.request", fake),
        mock.patch("odoo.addons.mail_management.controllers.main.request", fake),
    ):
        yield fake


# ===========================================================================
# ImapClient movement seams (lib level — imap_tools translation)
# ===========================================================================


@tagged("standard", "at_install")
class TestMoveSeam(TransactionCase):
    """``ImapClient`` movement seams → imap_tools COPY/STORE/EXPUNGE."""

    @contextmanager
    def _client_with_mock_conn(self):
        from odoo.addons.mail_management.lib.mail_imap_client import (  # type: ignore
            ImapClient,
        )

        client = ImapClient("imap.move.com")
        with mock.patch(IMAP_LIB_PATCH) as mock_mailbox:
            client._conn = mock_mailbox.return_value
            yield client, mock_mailbox.return_value

    def test_move_messages_is_copy_store_expunge(self):
        """Portable move: COPY → flag \\Deleted → EXPUNGE (no raw MOVE)."""
        with self._client_with_mock_conn() as (client, conn):
            client.move_messages("INBOX", [4, 2], "Archive")
        conn.folder.set.assert_called_with("INBOX")
        conn.copy.assert_called_once_with([4, 2], "Archive")
        conn.flag.assert_called_once_with([4, 2], {"\\Deleted"}, True)
        conn.expunge.assert_called_once_with()

    def test_copy_messages_selects_source_and_copies(self):
        with self._client_with_mock_conn() as (client, conn):
            client.copy_messages("INBOX", [7], "Keep")
        conn.folder.set.assert_called_with("INBOX")
        conn.copy.assert_called_once_with([7], "Keep")

    def test_delete_messages_flags_deleted(self):
        with self._client_with_mock_conn() as (client, conn):
            client.delete_messages("Trash", [9])
        conn.folder.set.assert_called_with("Trash")
        conn.flag.assert_called_once_with([9], {"\\Deleted"}, True)

    def test_expunge_selects_folder_then_expunges(self):
        with self._client_with_mock_conn() as (client, conn):
            client.expunge("Trash")
        conn.folder.set.assert_called_with("Trash")
        conn.expunge.assert_called_once_with()

    def test_move_messages_empty_uids_noop(self):
        """No uids → nothing selected, nothing copied, flagged or expunged."""
        with self._client_with_mock_conn() as (client, conn):
            client.move_messages("INBOX", [], "Archive")
        conn.folder.set.assert_not_called()
        conn.copy.assert_not_called()
        conn.flag.assert_not_called()
        conn.expunge.assert_not_called()

    def test_delete_messages_empty_uids_noop(self):
        with self._client_with_mock_conn() as (client, conn):
            client.delete_messages("INBOX", [])
        conn.folder.set.assert_not_called()
        conn.flag.assert_not_called()


# ===========================================================================
# mailbox._move_messages (IMAP-FIRST policy)
# ===========================================================================


@tagged("standard", "at_install")
class TestMoveMessagesModel(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]

        self.mailbox = self.Mailbox.create(_mb_vals())
        self.inbox = self.Folder.create(
            _folder_vals(self.mailbox, "INBOX", name="Inbox", role="inbox")
        )
        self.archive = self.Folder.create(_folder_vals(self.mailbox, "Archive"))
        self.trash = self.Folder.create(
            _folder_vals(self.mailbox, "Trash", role="trash")
        )

    def _msg(self, folder, uid, **kw):
        return self.Msg.create(_msg_vals(self.mailbox, folder, uid, **kw))

    def test_move_imap_first_then_local_delete_and_dest_refresh(self):
        msg1 = self._msg(self.inbox, 1)
        msg2 = self._msg(self.inbox, 2)
        fake = FakeImapMoveClient()
        with mock.patch(IMAP_PATCH, fake):
            moved = self.mailbox._move_messages(msg1 | msg2, self.archive)
        self.assertEqual(moved, msg1 | msg2)
        self.assertIn(("move_messages", "INBOX", [1, 2], "Archive"), fake.calls)
        # LOCAL source records are gone after the server move.
        self.assertFalse(msg1.exists())
        self.assertFalse(msg2.exists())
        # Destination refresh was triggered (cheap, same connection).
        self.assertIn(("search_uids", "Archive", 0), fake.calls)
        # No local copy was fabricated for the destination (re-sync owns it).
        self.assertEqual(len(self.archive.message_ids), 0)

    def test_move_multi_message_sorted_uids(self):
        msgs = (
            self._msg(self.inbox, 3)
            | self._msg(self.inbox, 1)
            | self._msg(self.inbox, 2)
        )
        fake = FakeImapMoveClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._move_messages(msgs, self.archive)
        self.assertIn(("move_messages", "INBOX", [1, 2, 3], "Archive"), fake.calls)

    def test_move_multi_folder_grouping(self):
        in1 = self._msg(self.inbox, 1)
        ar1 = self._msg(self.archive, 5)
        fake = FakeImapMoveClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._move_messages(in1 | ar1, self.trash)
        moved = [(c[1], c[3]) for c in fake.calls if c[0] == "move_messages"]
        self.assertEqual(moved, [("INBOX", "Trash"), ("Archive", "Trash")])
        self.assertFalse(in1.exists())
        self.assertFalse(ar1.exists())

    def test_move_cross_mailbox_dest_rejected(self):
        other_mb = self.Mailbox.create(
            _mb_vals(name="Other MB", imap_user="other@move.com")
        )
        foreign_folder = self.Folder.create(_folder_vals(other_mb, "Foreign"))
        msg = self._msg(self.inbox, 1)
        fake = FakeImapMoveClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._move_messages(msg, foreign_folder)
        self.assertEqual([c for c in fake.calls if c[0] == "move_messages"], [])
        self.assertTrue(msg.exists())

    def test_move_imap_failure_no_local_change(self):
        msg = self._msg(self.inbox, 1)
        fake = FakeImapMoveClient(fail_ops={"move_messages"})
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._move_messages(msg, self.archive)
        self.assertTrue(msg.exists())
        self.assertIn(msg, self.inbox.message_ids)

    def test_move_dest_refresh_failure_does_not_fail_move(self):
        """The move already succeeded server-side — a refresh failure is
        best-effort and must NOT roll back the local bookkeeping."""
        msg = self._msg(self.inbox, 1)
        fake = FakeImapMoveClient(fail_ops={"search_uids"})
        with mock.patch(IMAP_PATCH, fake):
            moved = self.mailbox._move_messages(msg, self.archive)
        self.assertEqual(moved, msg)
        self.assertFalse(msg.exists())
        self.assertIn(("move_messages", "INBOX", [1], "Archive"), fake.calls)

    def test_move_empty_recordset_noop(self):
        fake = FakeImapMoveClient()
        with mock.patch(IMAP_PATCH, fake):
            moved = self.mailbox._move_messages(self.Msg, self.archive)
        self.assertFalse(moved)
        self.assertEqual(fake.calls, [])

    def test_move_to_same_folder_noop(self):
        """Moving a message to the folder it is already in must be a NO-OP —
        a COPY-to-self + \\Deleted + EXPUNGE would churn its server UID (and
        some servers mishandle COPY to the selected folder)."""
        msg = self._msg(self.inbox, 1)
        fake = FakeImapMoveClient()
        with mock.patch(IMAP_PATCH, fake):
            moved = self.mailbox._move_messages(msg, self.inbox)
        self.assertFalse(moved)
        self.assertEqual(fake.calls, [])
        self.assertTrue(msg.exists())

    def test_move_mixed_same_and_other_folder(self):
        """Only the messages that actually change folder are moved."""
        stay = self._msg(self.inbox, 1)
        go = self._msg(self.archive, 5)
        fake = FakeImapMoveClient()
        with mock.patch(IMAP_PATCH, fake):
            moved = self.mailbox._move_messages(stay | go, self.inbox)
        self.assertEqual(moved, go)
        self.assertEqual(
            [c for c in fake.calls if c[0] == "move_messages"],
            [("move_messages", "Archive", [5], "INBOX")],
        )
        self.assertTrue(stay.exists())
        self.assertFalse(go.exists())

    def test_move_foreign_mailbox_messages_ignored(self):
        other_mb = self.Mailbox.create(
            _mb_vals(name="Other MB", imap_user="other@move.com")
        )
        other_inbox = self.Folder.create(_folder_vals(other_mb, "INBOX", role="inbox"))
        foreign = self.Msg.create(_msg_vals(other_mb, other_inbox, 9))
        fake = FakeImapMoveClient()
        with mock.patch(IMAP_PATCH, fake):
            moved = self.mailbox._move_messages(foreign, self.archive)
        self.assertFalse(moved)
        self.assertTrue(foreign.exists())
        self.assertEqual(fake.calls, [])


# ===========================================================================
# mailbox._delete_messages_to_trash (trash semantics)
# ===========================================================================


@tagged("standard", "at_install")
class TestDeleteToTrashModel(TransactionCase):
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

    def _msg(self, folder, uid):
        return self.Msg.create(_msg_vals(self.mailbox, folder, uid))

    def test_delete_moves_to_trash_folder(self):
        msg = self._msg(self.inbox, 1)
        fake = FakeImapMoveClient()
        with mock.patch(IMAP_PATCH, fake):
            result = self.mailbox._delete_messages_to_trash(msg)
        self.assertIn(("move_messages", "INBOX", [1], "Trash"), fake.calls)
        self.assertFalse(msg.exists())
        self.assertEqual(len(result["moved_to_trash"]), 1)
        self.assertEqual(len(result["purged"]), 0)
        # No local Trash record is fabricated (dest re-syncs on next refresh).
        self.assertEqual(len(self.trash.message_ids), 0)

    def test_delete_in_trash_is_permanent(self):
        msg = self._msg(self.trash, 1)
        fake = FakeImapMoveClient()
        with mock.patch(IMAP_PATCH, fake):
            result = self.mailbox._delete_messages_to_trash(msg)
        self.assertIn(("delete_messages", "Trash", [1]), fake.calls)
        self.assertIn(("expunge", "Trash"), fake.calls)
        self.assertEqual([c for c in fake.calls if c[0] == "move_messages"], [])
        self.assertFalse(msg.exists())
        self.assertEqual(len(result["purged"]), 1)

    def test_delete_no_trash_folder_is_permanent(self):
        """No role='trash' folder exists → permanent delete (documented
        fallback; nothing to move to)."""
        other_mb = self.Mailbox.create(
            _mb_vals(name="No Trash", imap_user="notrash@move.com")
        )
        inbox = self.Folder.create(_folder_vals(other_mb, "INBOX", role="inbox"))
        msg = self.Msg.create(_msg_vals(other_mb, inbox, 4))
        fake = FakeImapMoveClient()
        with mock.patch(IMAP_PATCH, fake):
            result = other_mb._delete_messages_to_trash(msg)
        self.assertIn(("delete_messages", "INBOX", [4]), fake.calls)
        self.assertIn(("expunge", "INBOX"), fake.calls)
        self.assertFalse(msg.exists())
        self.assertEqual(len(result["purged"]), 1)

    def test_delete_mixed_trash_and_elsewhere(self):
        in_trash = self._msg(self.trash, 7)
        in_inbox = self._msg(self.inbox, 2)
        fake = FakeImapMoveClient()
        with mock.patch(IMAP_PATCH, fake):
            result = self.mailbox._delete_messages_to_trash(in_trash | in_inbox)
        self.assertIn(("move_messages", "INBOX", [2], "Trash"), fake.calls)
        self.assertIn(("delete_messages", "Trash", [7]), fake.calls)
        self.assertFalse(in_trash.exists())
        self.assertFalse(in_inbox.exists())
        self.assertEqual(len(result["moved_to_trash"]), 1)
        self.assertEqual(len(result["purged"]), 1)

    def test_delete_imap_move_failure_no_local_change(self):
        msg = self._msg(self.inbox, 1)
        fake = FakeImapMoveClient(fail_ops={"move_messages"})
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._delete_messages_to_trash(msg)
        self.assertTrue(msg.exists())

    def test_delete_purge_failure_no_local_change(self):
        msg = self._msg(self.trash, 1)
        fake = FakeImapMoveClient(fail_ops={"delete_messages"})
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._delete_messages_to_trash(msg)
        self.assertTrue(msg.exists())


# ===========================================================================
# POST /mail_management/message/{move,delete} (controller)
# ===========================================================================


@tagged("standard", "at_install")
class TestMovementController(TransactionCase):
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
                "name": "Move A",
                "login": "move_a@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )
        self.user_b = self.env["res.users"].create(
            {
                "name": "Move B",
                "login": "move_b@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )

        self.mailbox_a = self.Mailbox.with_user(self.user_a).create(
            _mb_vals(name="A MB", imap_user="a@move.com")
        )
        self.inbox_a = self.Folder.create(
            _folder_vals(self.mailbox_a, "INBOX", role="inbox")
        )
        self.archive_a = self.Folder.create(_folder_vals(self.mailbox_a, "Archive"))
        self.trash_a = self.Folder.create(
            _folder_vals(self.mailbox_a, "Trash", role="trash")
        )
        self.msg1 = self.Msg.create(_msg_vals(self.mailbox_a, self.inbox_a, 1))
        self.msg2 = self.Msg.create(_msg_vals(self.mailbox_a, self.inbox_a, 2))

    def _env(self, user):
        return self.env(user=user)

    # -- route metadata ---------------------------------------------------

    def test_message_move_route_meta(self):
        routing = self.controller.message_move.original_routing
        self.assertEqual(routing["type"], "jsonrpc")
        self.assertEqual(routing["auth"], "user")
        self.assertIn("/mail_management/message/move", routing["routes"])

    def test_message_delete_route_meta(self):
        routing = self.controller.message_delete.original_routing
        self.assertEqual(routing["auth"], "user")
        self.assertIn("/mail_management/message/delete", routing["routes"])

    # -- move -------------------------------------------------------------

    def test_message_move_ok(self):
        fake = FakeImapMoveClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.message_move(
                message_ids=[self.msg1.id, self.msg2.id],
                folder_id=self.archive_a.id,
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["moved"], 2)
        self.assertFalse(self.msg1.exists())
        self.assertFalse(self.msg2.exists())

    def test_message_move_cross_user_ids_ignored(self):
        """User B posting A's message ids into B's OWN folder: the ids do not
        resolve through B's ir.rules → nothing moves, success stays True."""
        mailbox_b = self.Mailbox.with_user(self.user_b).create(
            _mb_vals(name="B MB", imap_user="b@move.com")
        )
        keep_b = self.Folder.create(_folder_vals(mailbox_b, "Keep"))
        fake = FakeImapMoveClient()
        with (
            _mock_request(self._env(self.user_b)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.message_move(
                message_ids=[self.msg1.id], folder_id=keep_b.id
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["moved"], 0)
        self.assertTrue(self.msg1.exists())
        self.assertTrue(self.msg1 in self.inbox_a.message_ids)
        self.assertEqual(fake.calls, [])

    def test_message_move_cross_user_dest_rejected(self):
        """User B moving A's messages into A's folder: the destination is not
        visible through B's ir.rules → UserError (nothing moves)."""
        fake = FakeImapMoveClient()
        with (
            _mock_request(self._env(self.user_b)),
            mock.patch(IMAP_PATCH, fake),
            self.assertRaises(UserError),
        ):
            self.controller.message_move(
                message_ids=[self.msg1.id], folder_id=self.archive_a.id
            )
        self.assertEqual(fake.calls, [])
        self.assertTrue(self.msg1.exists())

    def test_message_move_empty_ids_noop(self):
        fake = FakeImapMoveClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.message_move(
                message_ids=[], folder_id=self.archive_a.id
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["moved"], 0)
        self.assertEqual(fake.calls, [])

    # -- delete -----------------------------------------------------------

    def test_message_delete_routes_to_trash(self):
        fake = FakeImapMoveClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.message_delete(
                message_ids=[self.msg1.id, self.msg2.id]
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["deleted"], 2)
        self.assertEqual(result["moved_to_trash"], 2)
        self.assertEqual(result["purged"], 0)
        self.assertFalse(self.msg1.exists())
        self.assertFalse(self.msg2.exists())

    def test_message_delete_permanent_from_trash(self):
        trashed = self.Msg.create(_msg_vals(self.mailbox_a, self.trash_a, 5))
        fake = FakeImapMoveClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.message_delete(message_ids=[trashed.id])
        self.assertEqual(result["purged"], 1)
        self.assertEqual(result["moved_to_trash"], 0)
        self.assertFalse(trashed.exists())

    def test_message_delete_empties_trash(self):
        """Emptying the Trash in one call: everything in it is purged."""
        t1 = self.Msg.create(_msg_vals(self.mailbox_a, self.trash_a, 1))
        t2 = self.Msg.create(_msg_vals(self.mailbox_a, self.trash_a, 2))
        fake = FakeImapMoveClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.message_delete(message_ids=[t1.id, t2.id])
        self.assertEqual(result["purged"], 2)
        self.assertEqual(result["moved_to_trash"], 0)
        self.assertEqual(result["deleted"], 2)
        self.assertFalse(t1.exists())
        self.assertFalse(t2.exists())
        self.assertIn(("delete_messages", "Trash", [1, 2]), fake.calls)
        self.assertIn(("expunge", "Trash"), fake.calls)

    def test_message_delete_cross_user_ids_ignored(self):
        fake = FakeImapMoveClient()
        with (
            _mock_request(self._env(self.user_b)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.message_delete(message_ids=[self.msg1.id])
        self.assertTrue(result["success"])
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(self.msg1.exists())
        self.assertEqual(fake.calls, [])


# ===========================================================================
# PR-2b UI wiring (headless, template/JS source assertions)
# ===========================================================================


def _read(relative):
    path = os.path.join(get_module_path("mail_management"), relative)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@tagged("standard", "at_install")
class TestMovementUiWiring(TransactionCase):
    """Move dropdown + delete actions in the bulk bar and reading pane."""

    def test_message_list_bulk_bar_has_move_and_delete(self):
        xml = _read("static/src/xml/message_list.xml")
        for needle in (
            "o_webmail_bulk_move",
            "o_webmail_bulk_delete",
            "bulkMove",
            "onDeleteMessages",
        ):
            self.assertIn(needle, xml)

    def test_reading_pane_has_move_and_delete(self):
        xml = _read("static/src/xml/reading_pane.xml")
        for needle in (
            "o_webmail_msg_menu",
            "o_webmail_msg_menu_move",
            "o_webmail_msg_delete",
            "moveTo",
            "deleteCurrent",
        ):
            self.assertIn(needle, xml)
        # PR-2e: the standalone move select is consolidated into the
        # overflow menu (one menu item per destination folder).
        self.assertNotIn("o_webmail_move_select", xml)
        js = _read("static/src/components/reading_pane.js")
        for needle in ("onMoveToFolder", "onDeleteMessage", "isTrash"):
            self.assertIn(needle, js)

    def test_client_binds_movement_handlers_and_calls_endpoints(self):
        js = _read("static/src/webmail_client.js")
        for needle in (
            "moveMessages",
            "deleteMessages",
            "/mail_management/message/move",
            "/mail_management/message/delete",
            "moveTargets",
        ):
            self.assertIn(needle, js)
        xml = _read("static/src/xml/webmail_client.xml")
        for needle in (
            "onMoveToFolder.bind",
            "onDeleteMessages.bind",
            "moveTargets",
        ):
            self.assertIn(needle, xml)
