"""Strict-TDD RED tests for PR-2g — system-folder mapping + visibility.

Contract under test (parent PR-2g scope A + B):

Folder visibility:

* ``mail.management.folder.visible`` Boolean default True;
* ``/mail_management/folders`` OMITS hidden folders (tree payload);
* the SAME endpoint with ``include_hidden=True`` returns EVERY folder of the
  caller's mailboxes with its ``visible`` flag (the settings payload);
* ``POST /mail_management/folder/set_visible`` writes the flag (hide/restore);
* forged cross-user ids do not resolve (ir.rules) → UserError.

System-folder mapping (SnappyMail parity — the user OVERRIDES guessed roles):

* ``POST /mail_management/folder/set_role {folder_id, role}``;
* ``role`` ∈ {'', 'other', 'sent', 'drafts', 'trash', 'junk', 'archive',
  'all'} — anything else raises UserError;
* 'inbox' is FIXED: assigning it AND changing a folder whose role IS 'inbox'
  are both refused;
* per-mailbox uniqueness for the mappable system roles: assigning a role held
  by another folder of the SAME mailbox SWAPS (the previous holder is demoted
  to 'other' — one dropdown action, SnappyMail-style);
* 'other' stays free-form (many folders may be 'other');
* per-user isolation through the existing folder ir.rules.

Role overrides are HONORED by the sync + APPEND + trash-semantics paths:

* a user-remapped Sent folder receives the sent-copy APPEND (+ local mirror);
* a user-remapped Trash folder receives the delete-to-trash move.

UI wiring (headless needles): Folders settings tab with per-mailbox role
dropdowns + hidden-toggle restore, tree per-folder "Hide folder" menu, the
unread badge gated on ``show_unread_badge``.
"""

import ast
import os
from unittest import mock

from odoo.addons.mail_management.tests.test_compose_send import (  # type: ignore
    FakeImapClient,
    FakeSmtpClient,
    SendTestMixin,
    _mock_request,
)
from odoo.addons.mail_management.tests.test_message_movement import (  # type: ignore
    FakeImapMoveClient,
)
from odoo.exceptions import UserError  # type: ignore
from odoo.modules.module import get_module_path  # type: ignore
from odoo.tests.common import TransactionCase, tagged  # type: ignore

SMTP_PATCH = "odoo.addons.mail_management.models.mail_management.SmtpClient"
IMAP_PATCH = "odoo.addons.mail_management.models.mail_management.ImapClient"


def _module_file(*parts):
    path = get_module_path("mail_management")
    return os.path.join(path, *parts)


def _read(relative):
    with open(_module_file(relative), encoding="utf-8") as fh:
        return fh.read()


def _users(env):
    """Two plain webmail users (A: owner, B: stranger)."""
    group_user = env.ref("mail_management.group_mail_management_user")
    user_a = env["res.users"].create(
        {
            "name": "Map A",
            "login": "map_a@test",
            "groups_id": [(6, 0, [group_user.id])],
        }
    )
    user_b = env["res.users"].create(
        {
            "name": "Map B",
            "login": "map_b@test",
            "groups_id": [(6, 0, [group_user.id])],
        }
    )
    return user_a, user_b


def _controller():
    from odoo.addons.mail_management.controllers.main import (  # type: ignore
        MailManagementController,
    )

    return MailManagementController()


def _mailbox_vals(**kw):
    base = {
        "name": "Map Test",
        "provider_type": "generic",
        "imap_host": "imap.map.com",
        "imap_user": "me@map.com",
        "smtp_host": "smtp.map.com",
        "from_email": "me@map.com",
        "credential": "app-pass-MAP1",
        "state": "active",
    }
    base.update(kw)
    return base


# ===========================================================================
# Folder visibility: model flag + payload filtering + set_visible endpoint
# ===========================================================================


@tagged("standard", "at_install")
class TestFolderVisible(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user_a, self.user_b = _users(self.env)
        self.env_a = self.env(user=self.user_a)
        self.env_b = self.env(user=self.user_b)
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.mailbox = self.Mailbox.with_user(self.user_a).create(
            _mailbox_vals(name="A MB")
        )
        self.inbox = self.Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "INBOX",
                "name": "Inbox",
                "role": "inbox",
            }
        )
        self.archive2 = self.Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "Old Archive",
                "name": "Old Archive",
                "role": "other",
            }
        )
        self.controller = _controller()

    def test_visible_defaults_true(self):
        self.assertTrue(self.Folder._fields["visible"].default)
        self.assertTrue(self.inbox.visible)
        self.assertTrue(self.archive2.visible)

    def test_tree_payload_omits_hidden_folders(self):
        self.archive2.visible = False
        with _mock_request(self.env_a):
            result = self.controller.get_folders()
        folders = result[0]["folders"]
        self.assertEqual([f["imap_name"] for f in folders], ["INBOX"])

    def test_settings_payload_includes_hidden(self):
        self.archive2.visible = False
        with _mock_request(self.env_a):
            result = self.controller.get_folders(include_hidden=True)
        folders = {f["imap_name"]: f for f in result[0]["folders"]}
        self.assertIn("Old Archive", folders)
        self.assertFalse(folders["Old Archive"]["visible"])
        self.assertTrue(folders["INBOX"]["visible"])

    def test_set_visible_endpoint_writes(self):
        with _mock_request(self.env_a):
            hidden = self.controller.folder_set_visible(
                folder_id=self.archive2.id, visible=False
            )
            restored = self.controller.folder_set_visible(
                folder_id=self.archive2.id, visible=True
            )
        self.assertTrue(hidden["success"])
        self.assertTrue(restored["success"])
        self.assertTrue(self.archive2.visible)

    def test_set_visible_cross_user_rejected(self):
        with _mock_request(self.env_b), self.assertRaises(UserError):
            self.controller.folder_set_visible(
                folder_id=self.archive2.id, visible=False
            )
        self.assertTrue(self.archive2.visible)

    def test_set_visible_routes_require_auth(self):
        src = _read("controllers/main.py")
        for route in (
            "/mail_management/folder/set_visible",
            "/mail_management/folder/set_role",
        ):
            idx = src.index(f'"{route}"')
            self.assertIn('auth="user"', src[idx : idx + 200])


# ===========================================================================
# set_role: validation, inbox fixed, swap-uniqueness, isolation
# ===========================================================================


@tagged("standard", "at_install")
class TestSetFolderRole(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user_a, self.user_b = _users(self.env)
        self.env_a = self.env(user=self.user_a)
        self.env_b = self.env(user=self.user_b)
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.mailbox = self.Mailbox.with_user(self.user_a).create(
            _mailbox_vals(name="Role MB")
        )
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
        self.sent2 = self.Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "Sent Messages",
                "name": "Sent Messages",
                "role": "other",
            }
        )
        self.controller = _controller()

    def _set_role(self, env, folder, role):
        with _mock_request(env):
            return self.controller.folder_set_role(folder_id=folder.id, role=role)

    def test_assign_role(self):
        result = self._set_role(self.env_a, self.sent2, "sent")
        self.assertTrue(result["success"])
        self.assertEqual(self.sent2.role, "sent")

    def test_assign_held_role_swaps_previous_holder(self):
        """Per-mailbox uniqueness: the previous holder is demoted to 'other'
        (one dropdown action — the SnappyMail mapping behavior)."""
        result = self._set_role(self.env_a, self.sent2, "sent")
        self.assertEqual(self.sent2.role, "sent")
        self.assertEqual(self.sent.role, "other")
        self.assertEqual(result["demoted_folder_id"], self.sent.id)

    def test_same_folder_reassign_is_noop(self):
        self._set_role(self.env_a, self.sent, "sent")
        self.assertEqual(self.sent.role, "sent")

    def test_clear_role_to_other(self):
        result = self._set_role(self.env_a, self.sent, "")
        self.assertTrue(result["success"])
        self.assertEqual(self.sent.role, "other")

    def test_invalid_role_rejected(self):
        with self.assertRaises(UserError):
            self._set_role(self.env_a, self.sent2, "bogus")

    def test_role_key_case_sensitive(self):
        with self.assertRaises(UserError):
            self._set_role(self.env_a, self.sent2, "SENT")

    def test_inbox_role_cannot_be_assigned(self):
        with self.assertRaises(UserError):
            self._set_role(self.env_a, self.sent2, "inbox")
        self.assertEqual(self.sent2.role, "other")

    def test_inbox_folder_role_cannot_change(self):
        with self.assertRaises(UserError):
            self._set_role(self.env_a, self.inbox, "archive")
        self.assertEqual(self.inbox.role, "inbox")

    def test_inbox_folder_cannot_be_cleared(self):
        with self.assertRaises(UserError):
            self._set_role(self.env_a, self.inbox, "")
        self.assertEqual(self.inbox.role, "inbox")

    def test_many_other_folders_allowed(self):
        """'other' is free-form — clearing never conflicts."""
        self._set_role(self.env_a, self.sent2, "other")
        self._set_role(self.env_a, self.sent, "")
        self.assertEqual(self.sent2.role, "other")
        self.assertEqual(self.sent.role, "other")

    def test_cross_user_rejected(self):
        with self.assertRaises(UserError):
            self._set_role(self.env_b, self.sent2, "trash")
        self.assertEqual(self.sent2.role, "other")

    def test_per_mailbox_uniqueness_scoped(self):
        """A second mailbox keeps its OWN role mapping (uniqueness is per
        mailbox, never global)."""
        mb2 = self.Mailbox.with_user(self.user_a).create(
            _mailbox_vals(name="Role MB 2", imap_user="other@map.com")
        )
        sent_b = self.Folder.create(
            {
                "mailbox_id": mb2.id,
                "imap_name": "Sent",
                "name": "Sent",
                "role": "sent",
            }
        )
        self._set_role(self.env_a, self.sent2, "sent")
        self.assertEqual(sent_b.role, "sent")  # untouched
        self.assertEqual(self.sent.role, "other")

    def test_every_mappable_role_assignable(self):
        folder = self.sent2
        for role in ("drafts", "trash", "junk", "archive", "all"):
            self._set_role(self.env_a, folder, role)
            self.assertEqual(folder.role, role)

    def test_role_labels_payload_for_settings(self):
        """The Folders tab dropdown labels come translated from the server
        (no duplicate JS msgids for the role names)."""
        with _mock_request(self.env_a):
            result = self.controller.get_folders(include_hidden=True)
        labels = result[0]["role_labels"]
        self.assertEqual(labels["sent"], "Sent")
        self.assertEqual(labels["other"], "Other")
        self.assertIn("trash", labels)


# ===========================================================================
# Remapped roles are HONORED by the send/APPEND + trash paths
# ===========================================================================


@tagged("standard", "at_install")
class TestRemappedRolesHonored(SendTestMixin, TransactionCase):
    """The role fields are the single source of truth — a user-remapped Sent
    or Trash folder is what the APPEND and delete-to-trash lookups use."""

    def setUp(self):
        super().setUp()
        self.setUpSend()
        self.Folder = self.env["mail.management.folder"]

    def test_remapped_sent_folder_receives_append(self):
        """Remap 'sent' to a user-chosen folder → APPEND + local mirror land
        THERE (the guess no longer wins)."""
        sent2 = self.Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "Sent Messages",
                "name": "Sent Messages",
                "role": "other",
            }
        )
        demoted = sent2._set_system_role("sent")
        self.assertEqual(demoted, self.sent)  # the guessed holder demoted
        imap = FakeImapClient()
        with (
            mock.patch(SMTP_PATCH, FakeSmtpClient()),
            mock.patch(IMAP_PATCH, imap),
        ):
            result = self._send_reply()
        self.assertEqual(imap.appends[0][1], "Sent Messages")
        self.assertEqual(result["message"].folder_id, sent2)
        self.assertEqual(self.sent.message_count, 0)

    def test_remapped_trash_folder_receives_deletes(self):
        """Remap 'trash' → delete-to-trash moves to the NEW trash folder."""
        trash2 = self.Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "Papelera",
                "name": "Papelera",
                "role": "other",
            }
        )
        trash2._set_system_role("trash")
        fake = FakeImapMoveClient()
        with mock.patch(IMAP_PATCH, fake):
            self.mailbox._delete_messages_to_trash(self.original)
        self.assertIn(("move_messages", "INBOX", [7], "Papelera"), fake.calls)


# ===========================================================================
# UI wiring (headless needles): Folders tab, hide menu, badge gating
# ===========================================================================


@tagged("standard", "at_install")
class TestFolderMappingUiWiring(TransactionCase):
    def test_settings_dialog_has_folders_tab(self):
        js = _read("static/src/components/settings_dialog.js")
        self.assertIn('id: "folders"', js)
        xml = _read("static/src/xml/settings_dialog.xml")
        self.assertIn('data-pane="folders"', xml)
        for needle in (
            "o_webmail_settings_folder_row",
            "o_webmail_settings_folder_role",
            "o_webmail_settings_folder_visible",
        ):
            self.assertIn(needle, xml)

    def test_settings_dialog_role_dropdown_uses_server_labels(self):
        js = _read("static/src/components/settings_dialog.js")
        self.assertIn("role_labels", js)
        self.assertIn("onSetFolderRole", js)
        self.assertIn("onToggleFolderVisible", js)

    def test_folder_tree_has_hide_menu_item(self):
        xml = _read("static/src/xml/folder_tree.xml")
        for needle in ("o_webmail_folder_menu_hide", "hideFolder"):
            self.assertIn(needle, xml)
        js = _read("static/src/components/folder_tree.js")
        self.assertIn("onHideFolder", js)

    def test_tree_menu_wired_through_client(self):
        xml = _read("static/src/xml/webmail_client.xml")
        self.assertIn("onHideFolder.bind", xml)
        js = _read("static/src/webmail_client.js")
        for needle in (
            '"/mail_management/folder/set_visible"',
            '"/mail_management/folder/set_role"',
            "toggleFolderVisible",
            "setFolderRole",
        ):
            self.assertIn(needle, js)

    def test_client_loads_all_folders_for_settings_tab(self):
        js = _read("static/src/webmail_client.js")
        self.assertIn("include_hidden: true", js)

    def test_unread_badge_gated_by_setting(self):
        xml = _read("static/src/xml/folder_tree.xml")
        self.assertIn("props.showUnreadBadge", xml)
        js = _read("static/src/components/folder_tree.js")
        self.assertIn("showUnreadBadge", js)
        xml_client = _read("static/src/xml/webmail_client.xml")
        self.assertIn("showUnreadBadge=", xml_client)


# ===========================================================================
# es_CL: the NEW Python-side term of PR-2g must translate
# ===========================================================================


@tagged("standard", "at_install")
class TestFolderMappingTermTranslatedEsCl(TransactionCase):
    def test_invalid_folder_role_translated_es_cl(self):
        self.env["res.lang"]._activate_lang("es_CL")
        env = self.env["mail.management.folder"].with_context(lang="es_CL").env
        self.assertEqual(env._("Invalid folder role."), "Rol de carpeta no válido.")


# ===========================================================================
# TRIANGULATE (PR-2g) — the full controller chain honors remaps; swaps
# round-trip; hidden folders keep their mapping surface
# ===========================================================================


@tagged("standard", "at_install")
class TestRemappedRolesTriangulate(SendTestMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        self.setUpSend()
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]
        self.trash = self.Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "Trash",
                "name": "Trash",
                "role": "trash",
            }
        )
        self.controller = _controller()

    def test_controller_delete_routes_to_remapped_trash(self):
        """TRIANGULATE: remap trash THROUGH THE CONTROLLER, then run the
        CONTROLLER delete — the full chain lands in the NEW trash."""
        new_trash = self.Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "Papelera",
                "name": "Papelera",
                "role": "other",
            }
        )
        with _mock_request(self.env):
            self.controller.folder_set_role(folder_id=new_trash.id, role="trash")
            fake = FakeImapMoveClient()
            with mock.patch(IMAP_PATCH, fake):
                self.controller.message_delete(message_ids=[self.original.id])
        self.assertEqual(self.trash.role, "other")  # guessed holder demoted
        self.assertIn(("move_messages", "INBOX", [7], "Papelera"), fake.calls)

    def test_swap_back_and_forth(self):
        """TRIANGULATE: mapping is reversible — demote the swapped holder,
        then map the role back to the original folder."""
        sent2 = self.Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "Sent Messages",
                "name": "Sent Messages",
                "role": "other",
            }
        )
        with _mock_request(self.env):
            self.controller.folder_set_role(folder_id=sent2.id, role="sent")
            self.controller.folder_set_role(folder_id=self.sent.id, role="sent")
        self.assertEqual(self.sent.role, "sent")
        self.assertEqual(sent2.role, "other")

    def test_hidden_folder_keeps_role_surface(self):
        """TRIANGULATE: a HIDDEN folder is still mappable and restorable
        through the settings payload (visibility and roles compose)."""
        with _mock_request(self.env):
            self.controller.folder_set_visible(folder_id=self.trash.id, visible=False)
            payload = self.controller.get_folders(include_hidden=True)
            self.controller.folder_set_role(folder_id=self.trash.id, role="archive")
            restored = self.controller.folder_set_visible(
                folder_id=self.trash.id, visible=True
            )
        self.assertTrue(restored["success"])
        self.trash.invalidate_recordset(["role"])
        self.assertEqual(self.trash.role, "archive")
        folders = {f["imap_name"]: f for f in payload[0]["folders"]}
        self.assertTrue(folders["Trash"]["visible"] is False)


# ===========================================================================
# Version tracker (PR-2g)
# ===========================================================================


@tagged("standard", "at_install")
class TestVersionTracker(TransactionCase):
    def test_manifest_version_bumped(self):
        path = _module_file("__manifest__.py")
        with open(path, encoding="utf-8") as fh:
            source = "\n".join(
                ln for ln in fh.read().splitlines() if not ln.lstrip().startswith("#")
            )
        manifest = ast.literal_eval(source)
        self.assertEqual(manifest["version"], "18.0.17.0.0")
