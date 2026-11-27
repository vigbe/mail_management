"""Strict-TDD RED tests for PR-2b — folder CRUD (create/rename/delete).

Contract under test (openspec/changes/webmail-complete-management):

* ``ImapClient`` lib seams over imap_tools 1.15: ``create_folder(name)``,
  ``rename_folder(old, new)``, ``delete_folder(name)`` — thin, mockable
  (tests patch ``MailBox`` at lib level like every seam).
* ``mailbox._create_folder(name)`` / ``_rename_folder(folder, new_name)`` /
  ``_delete_folder(folder, force=False)`` — **IMAP-FIRST policy**: the IMAP
  operation is the source of truth; it runs BEFORE any local bookkeeping and a
  failure surfaces as ``UserError`` with NO local change.  (This deliberately
  differs from the PR-2a flags best-effort policy — folder CRUD mutates the
  server shape, so the local mirror must never diverge.)
* Name validation: strip; reject empty / too long (>255) / parent-path
  separators (``/`` or ``.``) — v1 supports simple child names only.
* ``_delete_folder`` guard: refuse when the folder has local messages unless
  ``force=True`` (UserError with the count); success cascades messages.
* Controller ``POST /mail_management/folder/{create,rename,delete}``
  (``auth='user'``): per-user isolation via ir.rules; cross-user ids rejected.

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
# Fake IMAP seam (records folder CRUD calls — the PR-2b IMAP-first path)
# ---------------------------------------------------------------------------


class FakeImapFolderClient:
    """Stands in for ``ImapClient`` at the model seam — records folder ops."""

    def __init__(self, fail_ops=None):
        self.calls = []  # (op_name, args...)
        self.fail_ops = set(fail_ops or [])

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

    # --- PR-2b folder CRUD seams ---------------------------------------
    def create_folder(self, name):
        self._record("create_folder", name)

    def rename_folder(self, old_name, new_name):
        self._record("rename_folder", old_name, new_name)

    def delete_folder(self, name):
        self._record("delete_folder", name)

    # --- PR-2h soft-delete seam (folder contents → Trash) ---------------
    def move_messages(self, src_imap_name, uids, dest_imap_name):
        self._record("move_messages", src_imap_name, list(uids), dest_imap_name)


def _mb_vals(**kw):
    base = {
        "name": "Folder Test",
        "provider_type": "generic",
        "imap_host": "imap.folders.com",
        "imap_user": "me@folders.com",
        "smtp_host": "smtp.folders.com",
        "from_email": "me@folders.com",
        "credential": "app-pass-FOLDERS",
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


@contextmanager
def _mock_request(env):
    """Patch BOTH ``odoo.http.request`` and the controller-module ``request``
    import (established PR-1c/PR-2a pattern)."""
    fake = type("FakeRequest", (), {"env": env, "session": {}})()
    with (
        mock.patch("odoo.http.request", fake),
        mock.patch("odoo.addons.mail_management.controllers.main.request", fake),
    ):
        yield fake


# ===========================================================================
# ImapClient folder seams (lib level — imap_tools translation)
# ===========================================================================


@tagged("standard", "at_install")
class TestFolderSeam(TransactionCase):
    """``ImapClient`` folder CRUD → imap_tools ``conn.folder.*`` calls."""

    @contextmanager
    def _client_with_mock_conn(self):
        from odoo.addons.mail_management.lib.mail_imap_client import (  # type: ignore
            ImapClient,
        )

        client = ImapClient("imap.folders.com")
        with mock.patch(IMAP_LIB_PATCH) as mock_mailbox:
            client._conn = mock_mailbox.return_value
            yield client, mock_mailbox.return_value

    def test_create_folder_calls_folder_create(self):
        with self._client_with_mock_conn() as (client, conn):
            client.create_folder("Projects")
        conn.folder.create.assert_called_once_with("Projects")

    def test_rename_folder_calls_folder_rename(self):
        with self._client_with_mock_conn() as (client, conn):
            client.rename_folder("Old", "New")
        conn.folder.rename.assert_called_once_with("Old", "New")

    def test_delete_folder_calls_folder_delete(self):
        with self._client_with_mock_conn() as (client, conn):
            client.delete_folder("Projects")
        conn.folder.delete.assert_called_once_with("Projects")


# ===========================================================================
# mailbox._create_folder / _rename_folder / _delete_folder (IMAP-FIRST)
# ===========================================================================


@tagged("standard", "at_install")
class TestFolderCrudModel(TransactionCase):
    """IMAP first, local mirror second; IMAP failure → UserError, no local."""

    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]

        self.mailbox = self.Mailbox.create(_mb_vals())
        self.inbox = self.Folder.create(
            _folder_vals(self.mailbox, "INBOX", name="Inbox", role="inbox")
        )
        self.existing = self.Folder.create(_folder_vals(self.mailbox, "Archive"))

    def _msg(self, folder, uid):
        return self.Msg.create(
            {
                "mailbox_id": self.mailbox.id,
                "folder_id": folder.id,
                "uid": uid,
                "message_id": f"<m{uid}@folders.com>",
                "subject": f"S {uid}",
                "email_from": f"s{uid}@ext.com",
                "date": fields.Datetime.to_datetime("2026-01-01 09:00:00"),
                "body_text": "b",
            }
        )

    # -- create -----------------------------------------------------------

    def test_create_folder_imap_then_local(self):
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake):
            folder = self.mailbox._create_folder("Projects")
        self.assertEqual(fake.calls, [("create_folder", "Projects")])
        self.assertTrue(folder)
        self.assertEqual(folder.imap_name, "Projects")
        self.assertEqual(folder.name, "Projects")
        self.assertEqual(folder.role, "other")
        self.assertIn(folder, self.mailbox.folder_ids)

    def test_create_folder_strips_name(self):
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake):
            folder = self.mailbox._create_folder("  Projects  ")
        self.assertEqual(fake.calls, [("create_folder", "Projects")])
        self.assertEqual(folder.imap_name, "Projects")

    def test_create_folder_empty_name_rejected(self):
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._create_folder("   ")
        self.assertEqual(fake.calls, [])

    def test_create_folder_too_long_rejected(self):
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._create_folder("x" * 256)
        self.assertEqual(fake.calls, [])

    def test_create_folder_path_separator_rejected(self):
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._create_folder("Parent/Child")
        self.assertEqual(fake.calls, [])

    def test_create_folder_dot_separator_rejected(self):
        """'.' is a common IMAP hierarchy separator — simple child names only."""
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._create_folder("Parent.Child")
        self.assertEqual(fake.calls, [])

    def test_create_folder_duplicate_name_rejected_before_imap(self):
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._create_folder("Archive")
        self.assertEqual(fake.calls, [])

    def test_create_folder_second_mailbox_isolated(self):
        """Creating on mailbox B never touches mailbox A's folders."""
        other_mb = self.Mailbox.create(
            _mb_vals(name="Other MB", imap_user="other@folders.com")
        )
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake):
            folder = other_mb._create_folder("Projects")
        self.assertEqual(fake.calls, [("create_folder", "Projects")])
        self.assertEqual(folder.mailbox_id, other_mb)
        self.assertNotIn(folder, self.mailbox.folder_ids)
        self.assertNotIn("Projects", self.mailbox.folder_ids.mapped("imap_name"))

    def test_create_folder_imap_failure_no_local_change(self):
        fake = FakeImapFolderClient(fail_ops={"create_folder"})
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._create_folder("Projects")
        self.assertNotIn("Projects", self.mailbox.folder_ids.mapped("imap_name"))

    # -- rename -----------------------------------------------------------

    def test_rename_folder_imap_then_local(self):
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._rename_folder(self.existing, "Renamed")
        self.assertEqual(fake.calls, [("rename_folder", "Archive", "Renamed")])
        self.assertEqual(self.existing.imap_name, "Renamed")
        self.assertEqual(self.existing.name, "Renamed")

    def test_rename_folder_to_same_name_noop(self):
        """Idempotent: same name → no IMAP call, no error, nothing changes."""
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._rename_folder(self.existing, "Archive")
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.existing.imap_name, "Archive")

    def test_rename_folder_strips_name(self):
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._rename_folder(self.existing, "  Renamed  ")
        self.assertEqual(fake.calls, [("rename_folder", "Archive", "Renamed")])

    def test_rename_folder_invalid_name_rejected(self):
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._rename_folder(self.existing, "a/b")
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.existing.imap_name, "Archive")

    def test_rename_folder_duplicate_name_rejected(self):
        other = self.Folder.create(_folder_vals(self.mailbox, "Second"))
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._rename_folder(other, "Archive")
        self.assertEqual(fake.calls, [])
        self.assertEqual(other.imap_name, "Second")

    def test_rename_folder_imap_failure_no_local_change(self):
        fake = FakeImapFolderClient(fail_ops={"rename_folder"})
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._rename_folder(self.existing, "Renamed")
        self.assertEqual(self.existing.imap_name, "Archive")

    def test_rename_folder_of_other_mailbox_rejected(self):
        other_mb = self.Mailbox.create(
            _mb_vals(name="Other MB", imap_user="other@folders.com")
        )
        foreign = self.Folder.create(_folder_vals(other_mb, "Foreign"))
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._rename_folder(foreign, "Hacked")
        self.assertEqual(fake.calls, [])
        self.assertEqual(foreign.imap_name, "Foreign")

    # -- delete (PR-2h: soft semantics) --------------------------------

    def test_delete_folder_empty_imap_then_local(self):
        """Empty folder → the PR-2b direct path: IMAP DELETE + local
        unlink (PR-2h kept it)."""
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake):
            result = self.mailbox._delete_folder(self.existing)
        self.assertEqual(fake.calls, [("delete_folder", "Archive")])
        self.assertFalse(self.existing.exists())
        self.assertEqual(result["moved_count"], 0)

    def test_delete_folder_with_messages_soft_deletes(self):
        """PR-2h contract change: a folder WITH messages is soft-deleted —
        contents MOVE to the mapped Trash folder (stamped copies), then
        the folder is removed server-side and locally."""
        self.Folder.create(_folder_vals(self.mailbox, "Trash", role="trash"))
        self._msg(self.existing, 1)
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake):
            result = self.mailbox._delete_folder(self.existing)
        self.assertIn(("move_messages", "Archive", [1], "Trash"), fake.calls)
        self.assertIn(("delete_folder", "Archive"), fake.calls)
        self.assertFalse(self.existing.exists())
        self.assertEqual(result["moved_count"], 1)
        trash = self.mailbox.folder_ids.filtered(lambda f: f.role == "trash")
        self.assertEqual(len(trash.message_ids), 1)
        self.assertTrue(trash.message_ids.trashed_date)

    def test_delete_folder_refuses_with_messages(self):
        """PR-2h: no mapped Trash folder → UserError (map one first),
        nothing happens."""
        self._msg(self.existing, 1)
        self._msg(self.existing, 2)
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._delete_folder(self.existing)
        self.assertEqual(fake.calls, [])
        self.assertTrue(self.existing.exists())

    def test_delete_folder_imap_failure_no_local_change(self):
        self._msg(self.existing, 1)
        self.Folder.create(_folder_vals(self.mailbox, "Trash", role="trash"))
        fake = FakeImapFolderClient(fail_ops={"delete_folder"})
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._delete_folder(self.existing)
        self.assertTrue(self.existing.exists())
        self.assertEqual(len(self.existing.message_ids), 1)

    def test_delete_folder_of_other_mailbox_rejected(self):
        other_mb = self.Mailbox.create(
            _mb_vals(name="Other MB", imap_user="other@folders.com")
        )
        foreign = self.Folder.create(_folder_vals(other_mb, "Foreign"))
        fake = FakeImapFolderClient()
        with mock.patch(IMAP_PATCH, fake), self.assertRaises(UserError):
            self.mailbox._delete_folder(foreign)
        self.assertEqual(fake.calls, [])
        self.assertTrue(foreign.exists())


# ===========================================================================
# POST /mail_management/folder/{create,rename,delete} (controller)
# ===========================================================================


@tagged("standard", "at_install")
class TestFolderCrudController(TransactionCase):
    def setUp(self):
        super().setUp()
        from odoo.addons.mail_management.controllers.main import (  # type: ignore
            MailManagementController,
        )

        self.controller = MailManagementController()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]

        group_user = self.env.ref("mail_management.group_mail_management_user")
        self.user_a = self.env["res.users"].create(
            {
                "name": "Folder A",
                "login": "folder_a@test",
                "groups_id": [(6, 0, [group_user.id])],
            }
        )
        self.user_b = self.env["res.users"].create(
            {
                "name": "Folder B",
                "login": "folder_b@test",
                "groups_id": [(6, 0, [group_user.id])],
            }
        )

        self.mailbox_a = self.Mailbox.with_user(self.user_a).create(
            _mb_vals(name="A MB", imap_user="a@folders.com")
        )
        self.folder_a = self.Folder.create(_folder_vals(self.mailbox_a, "Projects"))

    def _env(self, user):
        return self.env(user=user)

    # -- route metadata ---------------------------------------------------

    def test_folder_create_route_meta(self):
        routing = self.controller.folder_create.original_routing
        self.assertEqual(routing["type"], "json")
        self.assertEqual(routing["auth"], "user")
        self.assertIn("/mail_management/folder/create", routing["routes"])
        self.assertIn("POST", routing["methods"])

    def test_folder_rename_route_meta(self):
        routing = self.controller.folder_rename.original_routing
        self.assertEqual(routing["auth"], "user")
        self.assertIn("/mail_management/folder/rename", routing["routes"])

    def test_folder_delete_route_meta(self):
        routing = self.controller.folder_delete.original_routing
        self.assertEqual(routing["auth"], "user")
        self.assertIn("/mail_management/folder/delete", routing["routes"])

    # -- create -----------------------------------------------------------

    def test_folder_create_ok(self):
        fake = FakeImapFolderClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.folder_create(
                mailbox_id=self.mailbox_a.id, name=" NewFolder "
            )
        self.assertTrue(result["success"])
        folder = self.Folder.search(
            [("mailbox_id", "=", self.mailbox_a.id), ("imap_name", "=", "NewFolder")]
        )
        self.assertEqual(len(folder), 1)
        self.assertEqual(result["folder_id"], folder.id)

    def test_folder_create_cross_user_rejected(self):
        """User B posting user A's mailbox id: not found through ir.rules."""
        fake = FakeImapFolderClient()
        with (
            _mock_request(self._env(self.user_b)),
            mock.patch(IMAP_PATCH, fake),
            self.assertRaises(UserError),
        ):
            self.controller.folder_create(mailbox_id=self.mailbox_a.id, name="X")
        self.assertEqual(fake.calls, [])

    def test_folder_create_invalid_name_via_controller(self):
        fake = FakeImapFolderClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
            self.assertRaises(UserError),
        ):
            self.controller.folder_create(mailbox_id=self.mailbox_a.id, name="a/b")
        self.assertEqual(fake.calls, [])

    # -- rename -----------------------------------------------------------

    def test_folder_rename_ok(self):
        fake = FakeImapFolderClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.folder_rename(
                folder_id=self.folder_a.id, name="Renamed"
            )
        self.assertTrue(result["success"])
        self.assertEqual(self.folder_a.imap_name, "Renamed")

    def test_folder_rename_cross_user_rejected(self):
        fake = FakeImapFolderClient()
        with (
            _mock_request(self._env(self.user_b)),
            mock.patch(IMAP_PATCH, fake),
            self.assertRaises(UserError),
        ):
            self.controller.folder_rename(folder_id=self.folder_a.id, name="X")
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.folder_a.imap_name, "Projects")

    # -- delete -----------------------------------------------------------

    def test_folder_delete_ok(self):
        fake = FakeImapFolderClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.folder_delete(folder_id=self.folder_a.id)
        self.assertTrue(result["success"])
        self.assertFalse(self.folder_a.exists())

    def test_folder_delete_soft_summary(self):
        """PR-2h: the endpoint returns the relocation summary — the
        confirmation is a client concern, ``force`` is gone."""
        self.Folder.create(_folder_vals(self.mailbox_a, "Trash", role="trash"))
        self.env["mail.management.message"].create(
            {
                "mailbox_id": self.mailbox_a.id,
                "folder_id": self.folder_a.id,
                "uid": 1,
                "message_id": "<x@folders.com>",
                "subject": "s",
                "email_from": "e@x.com",
                "date": fields.Datetime.now(),
                "body_text": "b",
            }
        )
        fake = FakeImapFolderClient()
        with (
            _mock_request(self._env(self.user_a)),
            mock.patch(IMAP_PATCH, fake),
        ):
            result = self.controller.folder_delete(folder_id=self.folder_a.id)
        self.assertTrue(result["success"])
        self.assertEqual(result["moved_count"], 1)
        self.assertTrue(result["folder_removed"])
        self.assertFalse(self.folder_a.exists())

    def test_folder_delete_cross_user_rejected(self):
        fake = FakeImapFolderClient()
        with (
            _mock_request(self._env(self.user_b)),
            mock.patch(IMAP_PATCH, fake),
            self.assertRaises(UserError),
        ):
            self.controller.folder_delete(folder_id=self.folder_a.id)
        self.assertEqual(fake.calls, [])
        self.assertTrue(self.folder_a.exists())


# ===========================================================================
# PR-2b UI wiring (headless, template/JS source assertions)
# ===========================================================================


def _read(relative):
    path = os.path.join(get_module_path("mail_management"), relative)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@tagged("standard", "at_install")
class TestFolderUiWiring(TransactionCase):
    """Tree UI: add-folder button, per-folder menu, create/rename dialog."""

    def test_folder_tree_has_add_button_and_folder_menu(self):
        xml = _read("static/src/xml/folder_tree.xml")
        for needle in (
            "o_webmail_add_folder_btn",
            "o_webmail_folder_menu_btn",
            "o_webmail_folder_menu_rename",
            "o_webmail_folder_menu_delete",
            "onCreateFolder",
            "renameFolder",
            "deleteFolder",
        ):
            self.assertIn(needle, xml)

    def test_folder_dialog_component_exists(self):
        js = _read("static/src/components/folder_dialog.js")
        for needle in ("create", "rename", "onConfirm", "canConfirm"):
            self.assertIn(needle, js)
        xml = _read("static/src/xml/folder_dialog.xml")
        for needle in (
            "o_webmail_folder_dialog",
            "o_webmail_folder_dialog_name",
            "o_webmail_folder_dialog_mailbox",
        ):
            self.assertIn(needle, xml)

    def test_client_binds_folder_handlers_and_calls_endpoints(self):
        js = _read("static/src/webmail_client.js")
        for needle in (
            "createFolder",
            "renameFolder",
            "deleteFolder",
            "/mail_management/folder/create",
            "/mail_management/folder/rename",
            "/mail_management/folder/delete",
            "FolderDialog",
        ):
            self.assertIn(needle, js)
        xml = _read("static/src/xml/webmail_client.xml")
        for needle in (
            "onCreateFolder.bind",
            "onRenameFolder.bind",
            "onDeleteFolder.bind",
        ):
            self.assertIn(needle, xml)

    def test_manifest_version_bumped_to_pr2b(self):
        import ast

        manifest = ast.literal_eval(_read("__manifest__.py"))
        self.assertEqual(manifest["version"], "16.0.17.0.0")
