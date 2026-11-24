"""Strict-TDD RED tests for PR-2a — message flags (star/flag + read/unread).

Contract under test (openspec/changes/webmail-complete-management):

* ``ImapClient.set_flags(folder_imap_name, uids, seen, flagged)`` — new
  mockable seam over imap_tools ``client.flag(uid_list, {flags}, value)``;
* ``mailbox._set_message_flags(message_ids, flags)`` — flags limited to
  ``{'seen': bool, 'flagged': bool}``; POLICY (tasks.md): local
  ``flag_seen``/``flag_flagged`` are written FIRST so the UI and the stored
  ``unread_count`` react instantly, then the IMAP mirror runs best-effort
  through the seam — a mirror failure is logged with the mailbox id and
  NEVER raised to the caller; ``unread_count`` recomputes afterwards;
* ``POST /mail_management/message/set_flags`` (``auth='user'``, jsonrpc) —
  accepts ``message_ids`` + ``flags``; per-user isolation via the ir.rules:
  cross-user ids are silently ignored; invalid flag keys → UserError.

All transports are MOCKED — no real network calls.
"""

import os
from contextlib import contextmanager
from unittest import mock

from odoo import fields  # type: ignore
from odoo.exceptions import UserError  # type: ignore
from odoo.modules.module import get_module_path  # type: ignore
from odoo.tests.common import TransactionCase, tagged  # type: ignore

IMAP_PATCH = "odoo.addons.mail_management.models.mail_management.ImapClient"
IMAP_LIB_PATCH = "odoo.addons.mail_management.lib.mail_imap_client.MailBox"


# ---------------------------------------------------------------------------
# Fake IMAP seam (records set_flags calls — the PR-2a mirror path)
# ---------------------------------------------------------------------------


class FakeImapFlagClient:
    """Stands in for ``ImapClient`` at the model seam — records set_flags."""

    def __init__(self, fail=None):
        # (folder_imap_name, uids, seen, flagged)
        self.calls = []
        self.fail = fail

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def set_flags(self, folder_imap_name, uids, seen=None, flagged=None):
        if self.fail:
            raise self.fail
        self.calls.append((folder_imap_name, list(uids), seen, flagged))


def _mb_vals(**kw):
    base = {
        "name": "Flags Test",
        "provider_type": "generic",
        "imap_host": "imap.flags.com",
        "imap_user": "me@flags.com",
        "smtp_host": "smtp.flags.com",
        "from_email": "me@flags.com",
        "credential": "app-pass-FLAGS",
        "state": "active",
    }
    base.update(kw)
    return base


def _msg_vals(mailbox, folder, uid, **kw):
    base = {
        "mailbox_id": mailbox.id,
        "folder_id": folder.id,
        "uid": uid,
        "message_id": f"<msg{uid}@flags.com>",
        "subject": f"Subject {uid}",
        "email_from": f"sender{uid}@ext.com",
        "date": fields.Datetime.to_datetime(f"2026-01-0{uid} 09:00:00"),
        "body_text": f"Body {uid}",
    }
    base.update(kw)
    return base


@contextmanager
def _mock_request(env):
    """Patch BOTH ``odoo.http.request`` (real proxy — read dynamically by
    ``Controller.env`` and by ``_()`` frame-walking) and the ``request``
    name imported in the controller module (PR-1c pattern)."""
    fake = type("FakeRequest", (), {"env": env, "session": {}})()
    with (
        mock.patch("odoo.http.request", fake),
        mock.patch("odoo.addons.mail_management.controllers.main.request", fake),
    ):
        yield fake


# ===========================================================================
# ImapClient.set_flags seam (lib level)
# ===========================================================================


@tagged("standard", "at_install")
class TestSetFlagsSeam(TransactionCase):
    """``ImapClient.set_flags`` → imap_tools ``conn.flag`` translation."""

    @contextmanager
    def _client_with_mock_conn(self):
        """Real ``ImapClient`` whose connection is a mocked MailBox."""
        from odoo.addons.mail_management.lib.mail_imap_client import (  # type: ignore
            ImapClient,
        )

        client = ImapClient("imap.flags.com")
        with mock.patch(IMAP_LIB_PATCH) as mock_mailbox:
            client._conn = mock_mailbox.return_value
            yield client, mock_mailbox.return_value

    def test_seen_true_single_flag_call(self):
        with self._client_with_mock_conn() as (client, conn):
            client.set_flags("INBOX", [3, 1], seen=True)
        conn.folder.set.assert_called_once_with("INBOX")
        conn.flag.assert_called_once_with([3, 1], {"\\Seen"}, True)

    def test_flagged_true_uses_flagged_flag(self):
        with self._client_with_mock_conn() as (client, conn):
            client.set_flags("Archive", [7], flagged=True)
        conn.flag.assert_called_once_with([7], {"\\Flagged"}, True)

    def test_mixed_values_two_calls(self):
        """seen=True + flagged=False needs two STOREs (different values)."""
        with self._client_with_mock_conn() as (client, conn):
            client.set_flags("INBOX", [1, 2], seen=True, flagged=False)
        self.assertEqual(conn.flag.call_count, 2)
        self.assertEqual(
            {frozenset(c.args[1]) for c in conn.flag.call_args_list},
            {frozenset({"\\Seen"}), frozenset({"\\Flagged"})},
        )
        values = sorted(c.args[2] for c in conn.flag.call_args_list)
        self.assertEqual(values, [False, True])
        conn.folder.set.assert_called_once_with("INBOX")

    def test_none_values_leave_flags_untouched(self):
        """All-None = no-op: no STORE, no folder select."""
        with self._client_with_mock_conn() as (client, conn):
            client.set_flags("INBOX", [1], seen=None, flagged=None)
        conn.flag.assert_not_called()
        conn.folder.set.assert_not_called()

    def test_empty_uids_noop(self):
        with self._client_with_mock_conn() as (client, conn):
            client.set_flags("INBOX", [], seen=True)
        conn.flag.assert_not_called()
        conn.folder.set.assert_not_called()


# ===========================================================================
# mailbox._set_message_flags (model level — the flag POLICY)
# ===========================================================================


@tagged("standard", "at_install")
class TestSetMessageFlagsModel(TransactionCase):
    """Local-first flag update + best-effort IMAP mirror (never raises)."""

    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]

        self.mailbox = self.Mailbox.create(_mb_vals())
        self.inbox = self.Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "INBOX",
                "name": "Inbox",
                "role": "inbox",
            }
        )
        self.archive = self.Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "Archive",
                "name": "Archive",
                "role": "archive",
            }
        )
        self.msg1 = self.Msg.create(_msg_vals(self.mailbox, self.inbox, 1))
        self.msg2 = self.Msg.create(_msg_vals(self.mailbox, self.inbox, 2))
        self.msg3 = self.Msg.create(
            _msg_vals(self.mailbox, self.archive, 5, flag_flagged=True)
        )

    def test_local_update_first_then_mirror(self):
        """Local flags are written even before looking at the mirror seam."""
        fake = FakeImapFlagClient()
        with mock.patch(IMAP_PATCH, fake):
            result = self.mailbox._set_message_flags(
                [self.msg1.id, self.msg2.id], {"seen": True}
            )
        self.assertEqual(result, self.msg1 | self.msg2)
        self.assertTrue(self.msg1.flag_seen)
        self.assertTrue(self.msg2.flag_seen)
        self.assertEqual(fake.calls, [("INBOX", [1, 2], True, None)])

    def test_flagged_toggle_off(self):
        fake = FakeImapFlagClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._set_message_flags([self.msg3.id], {"flagged": False})
        self.assertFalse(self.msg3.flag_flagged)
        self.assertEqual(fake.calls, [("Archive", [5], None, False)])

    def test_multi_folder_grouping(self):
        """Messages spanning folders produce one mirror call per folder."""
        fake = FakeImapFlagClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._set_message_flags(
                [self.msg1.id, self.msg2.id, self.msg3.id], {"seen": True}
            )
        by_folder = {c[0]: c for c in fake.calls}
        self.assertEqual(set(by_folder), {"INBOX", "Archive"})
        self.assertEqual(by_folder["INBOX"][1], [1, 2])
        self.assertEqual(by_folder["Archive"][1], [5])

    def test_invalid_flag_key_rejected(self):
        """Only {'seen', 'flagged'} are accepted — anything else: UserError,
        no local write, no mirror call."""
        fake = FakeImapFlagClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._set_message_flags([self.msg1.id], {"deleted": True})
        self.assertFalse(self.msg1.flag_deleted)
        self.assertFalse(self.msg1.flag_seen)
        self.assertEqual(fake.calls, [])

    def test_mirror_failure_never_raises(self):
        """POLICY: a mirror exception is logged, not raised; the LOCAL flags
        (already written) stay."""
        fake = FakeImapFlagClient(fail=OSError("imap down"))
        with mock.patch(IMAP_PATCH, fake):
            result = self.mailbox._set_message_flags(
                [self.msg1.id], {"seen": True, "flagged": True}
            )
        self.assertEqual(result, self.msg1)
        self.assertTrue(self.msg1.flag_seen)
        self.assertTrue(self.msg1.flag_flagged)

    def test_missing_credential_still_updates_local(self):
        """A mailbox without a credential (draft) mirrors nothing but the
        local update must still succeed (the credential UserError is part
        of the best-effort mirror, not of the local write)."""
        draft = self.Mailbox.create(
            _mb_vals(name="Draft MB", imap_user="draft@flags.com")
        )
        # Clear the ciphertext at rest — assigning False to the masked
        # compute field would be a no-op (the inverse skips empty values).
        draft.sudo().credential_encrypted = False
        draft.state = "draft"
        folder = self.Folder.create(
            {
                "mailbox_id": draft.id,
                "imap_name": "INBOX",
                "name": "Inbox",
                "role": "inbox",
            }
        )
        msg = self.Msg.create(_msg_vals(draft, folder, 1))
        fake = FakeImapFlagClient()
        with mock.patch(IMAP_PATCH, fake):
            result = draft._set_message_flags([msg.id], {"seen": True})
        self.assertEqual(result, msg)
        self.assertTrue(msg.flag_seen)
        self.assertEqual(fake.calls, [])

    def test_unread_count_recompute_follows(self):
        """The stored ``folder.unread_count`` recomputes after the write."""
        self.assertEqual(self.inbox.unread_count, 2)
        self.mailbox._set_message_flags([self.msg1.id, self.msg2.id], {"seen": True})
        self.assertEqual(self.inbox.unread_count, 0)
        self.assertEqual(self.inbox.message_count, 2)
        self.mailbox._set_message_flags([self.msg1.id], {"seen": False})
        self.assertEqual(self.inbox.unread_count, 1)

    def test_foreign_mailbox_ids_ignored(self):
        """The method only touches THIS mailbox's messages."""
        other_mb = self.Mailbox.create(
            _mb_vals(name="Other", imap_user="other@flags.com")
        )
        other_folder = self.Folder.create(
            {
                "mailbox_id": other_mb.id,
                "imap_name": "INBOX",
                "name": "Inbox",
                "role": "inbox",
            }
        )
        foreign = self.Msg.create(_msg_vals(other_mb, other_folder, 9))
        fake = FakeImapFlagClient()
        with mock.patch(IMAP_PATCH, fake):
            result = self.mailbox._set_message_flags(
                [foreign.id, self.msg1.id], {"seen": True}
            )
        self.assertEqual(result, self.msg1)
        self.assertFalse(foreign.flag_seen)
        self.assertEqual(fake.calls, [("INBOX", [1], True, None)])


# ===========================================================================
# POST /mail_management/message/set_flags (controller)
# ===========================================================================


@tagged("standard", "at_install")
class TestMessageSetFlagsController(TransactionCase):
    """Bulk flag endpoint: auth, isolation, local update, mirror, counts."""

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
                "name": "Flags A",
                "login": "flags_a@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )
        self.user_b = self.env["res.users"].create(
            {
                "name": "Flags B",
                "login": "flags_b@test",
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
        self.msg1 = self.Msg.create(_msg_vals(self.mailbox_a, self.folder_a, 1))
        self.msg2 = self.Msg.create(_msg_vals(self.mailbox_a, self.folder_a, 2))
        self.msg3 = self.Msg.create(
            _msg_vals(self.mailbox_a, self.folder_a, 3, flag_flagged=True)
        )

    def _env(self, user):
        return self.env(user=user)

    def test_route_meta_auth_user_jsonrpc(self):
        routing = self.controller.message_set_flags.original_routing
        self.assertEqual(routing["type"], "jsonrpc")
        self.assertEqual(routing["auth"], "user")
        self.assertIn("/mail_management/message/set_flags", routing["routes"])
        self.assertIn("POST", routing["methods"])

    def test_bulk_mark_seen_via_controller(self):
        fake = FakeImapFlagClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.message_set_flags(
                message_ids=[self.msg1.id, self.msg2.id, self.msg3.id],
                flags={"seen": True},
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["updated"], 3)
        self.assertTrue(self.msg1.flag_seen)
        self.assertTrue(self.msg3.flag_seen)
        self.assertEqual(fake.calls, [("INBOX", [1, 2, 3], True, None)])
        self.assertEqual(self.folder_a.unread_count, 0)

    def test_bulk_star_via_controller(self):
        fake = FakeImapFlagClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.message_set_flags(
                message_ids=[self.msg1.id, self.msg2.id],
                flags={"flagged": True},
            )
        self.assertEqual(result["updated"], 2)
        self.assertTrue(self.msg1.flag_flagged)
        self.assertTrue(self.msg2.flag_flagged)
        self.assertEqual(fake.calls, [("INBOX", [1, 2], None, True)])

    def test_mixed_flags_one_call(self):
        fake = FakeImapFlagClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            self.controller.message_set_flags(
                message_ids=[self.msg1.id], flags={"seen": False, "flagged": True}
            )
        self.assertFalse(self.msg1.flag_seen)
        self.assertTrue(self.msg1.flag_flagged)
        self.assertEqual(fake.calls, [("INBOX", [1], False, True)])

    def test_cross_user_ids_ignored(self):
        """User B posting user A's message ids: silently ignored (ir.rules),
        A's records untouched, no mirror call, success stays True."""
        fake = FakeImapFlagClient()
        with (
            _mock_request(self._env(self.user_b)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.message_set_flags(
                message_ids=[self.msg1.id, self.msg2.id], flags={"seen": True}
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["updated"], 0)
        self.assertFalse(self.msg1.flag_seen)
        self.assertEqual(fake.calls, [])

    def test_invalid_flag_key_rejected_via_controller(self):
        fake = FakeImapFlagClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
            self.assertRaises(UserError),
        ):
            self.controller.message_set_flags(
                message_ids=[self.msg1.id], flags={"importance": 5}
            )
        self.assertFalse(self.msg1.flag_seen)
        self.assertEqual(fake.calls, [])

    def test_empty_ids_noop_success(self):
        with _mock_request(self._env(self.user_a)):
            result = self.controller.message_set_flags(
                message_ids=[], flags={"seen": True}
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["updated"], 0)

    def test_unread_count_recompute_via_controller(self):
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, FakeImapFlagClient()),
        ):
            self.controller.message_set_flags(
                message_ids=[self.msg1.id, self.msg2.id], flags={"seen": True}
            )
        self.assertEqual(self.folder_a.unread_count, 1)

    # ------------------------------------------------------------------
    # TRIANGULATE — multi-message bulk, mixed flags, cross-folder bulk,
    # star round-trip through the endpoint
    # ------------------------------------------------------------------

    def test_bulk_star_then_unstar_round_trip(self):
        """A star followed by an unstar leaves no residue (toggle semantics)."""
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, FakeImapFlagClient()),
        ):
            self.controller.message_set_flags(
                message_ids=[self.msg1.id, self.msg2.id], flags={"flagged": True}
            )
            self.controller.message_set_flags(
                message_ids=[self.msg2.id], flags={"flagged": False}
            )
        self.assertTrue(self.msg1.flag_flagged)
        self.assertFalse(self.msg2.flag_flagged)

    def test_bulk_mixed_flags_on_many_messages(self):
        """Mixed {'seen', 'flagged'} over a multi-message bulk in one call."""
        fake = FakeImapFlagClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.message_set_flags(
                message_ids=[self.msg1.id, self.msg2.id, self.msg3.id],
                flags={"seen": True, "flagged": False},
            )
        self.assertEqual(result["updated"], 3)
        for msg in (self.msg1, self.msg2, self.msg3):
            self.assertTrue(msg.flag_seen)
            self.assertFalse(msg.flag_flagged)
        # One mirror call for the folder, carrying BOTH flag values.
        self.assertEqual(fake.calls, [("INBOX", [1, 2, 3], True, False)])

    def test_bulk_across_two_folders_groups_mirror_calls(self):
        archive = self.Folder.create(
            {
                "mailbox_id": self.mailbox_a.id,
                "imap_name": "Archive",
                "name": "Archive",
                "role": "archive",
            }
        )
        archived = self.Msg.create(_msg_vals(self.mailbox_a, archive, 8))
        fake = FakeImapFlagClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            self.controller.message_set_flags(
                message_ids=[self.msg1.id, archived.id], flags={"seen": True}
            )
        by_folder = {c[0]: c for c in fake.calls}
        self.assertEqual(set(by_folder), {"INBOX", "Archive"})
        self.assertEqual(by_folder["Archive"][1], [8])


# ===========================================================================
# PR-2a UI wiring (headless, template/JS source assertions)
# ===========================================================================


def _read(relative):
    path = os.path.join(get_module_path("mail_management"), relative)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@tagged("standard", "at_install")
class TestFlagsUiWiring(TransactionCase):
    """Star toggles + mark-unread + bulk selection UI is present and bound."""

    def test_message_list_has_star_and_selection_markup(self):
        xml = _read("static/src/xml/message_list.xml")
        for needle in (
            "o_webmail_star_toggle",
            "o_webmail_msg_checkbox",
            "o_webmail_select_all",
            "o_webmail_bulk_bar",
            "o_webmail_bulk_read",
            "o_webmail_bulk_unread",
            "o_webmail_bulk_star",
            "o_webmail_bulk_unstar",
            "onToggleStar",
            "onToggleSelect",
            "onToggleSelectAll",
            "onBulkSetFlags",
        ):
            self.assertIn(needle, xml)

    def test_reading_pane_has_star_and_mark_unread(self):
        xml = _read("static/src/xml/reading_pane.xml")
        for needle in (
            "o_webmail_reading_star",
            "o_webmail_mark_unread",
            "toggleStar",
            "markUnread",
        ):
            self.assertIn(needle, xml)
        js = _read("static/src/components/reading_pane.js")
        self.assertIn("onToggleStar", js)
        self.assertIn("onMarkUnread", js)

    def test_client_binds_flag_handlers_and_calls_bulk_endpoint(self):
        js = _read("static/src/webmail_client.js")
        for needle in (
            "toggleStar",
            "bulkSetFlags",
            "toggleSelectAll",
            "markUnread",
            "/mail_management/message/set_flags",
            "selectedIds",
        ):
            self.assertIn(needle, js)
        xml = _read("static/src/xml/webmail_client.xml")
        for needle in (
            "onToggleStar.bind",
            "onToggleSelect.bind",
            "onToggleSelectAll.bind",
            "onBulkSetFlags.bind",
            "onMarkUnread.bind",
        ):
            self.assertIn(needle, xml)

    def test_message_body_payload_carries_flag_flagged(self):
        """The reading pane star reflects the server flag: /message_body
        must expose ``flag_flagged``."""
        src = _read("controllers/main.py")
        self.assertIn('"flag_flagged": message.flag_flagged', src)

    def test_manifest_version_bumped_to_pr2a(self):
        import ast

        manifest = ast.literal_eval(_read("__manifest__.py"))
        self.assertEqual(manifest["version"], "20.0.17.0.0")
