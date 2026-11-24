"""Strict-TDD RED tests for PR-2c — HTML signatures (model, isolation,
resolution, config UI, compose wiring).

Covers the PR-2c signature contract (openspec/changes/webmail-complete-
management/tasks.md):

* ``mail.management.signature`` CRUD: user_id (default current, required),
  optional mailbox_id (empty = all the user's mailboxes), name, html_body
  SANITIZED ON WRITE via ``tools.html_sanitize`` (never trust input),
  is_default with AT MOST ONE default per (user, mailbox) scope —
  auto-unset of the previous default in create AND write;
* per-user isolation through group-scoped ir.rules (user=own, manager=all,
  mirroring the mailbox pattern) + ir.model.access rows for the user group;
* ``mailbox._get_default_signature()`` resolution order: mailbox-specific
  default > user-global default > empty recordset;
* ``/mail_management/signatures`` controller: own signatures for a mailbox
  (mailbox-specific + global) with the resolved default id;
* signature config views (list + form + action + menu) under the Mail
  Management root;
* ComposeDialog rich-text wiring (Wysiwyg embed + signature picker).
"""

import ast
import os

from odoo.addons.mail_management.tests.test_compose_send import (  # type: ignore
    _mock_request,
)
from odoo.exceptions import AccessError  # type: ignore
from odoo.modules.module import get_module_path  # type: ignore
from odoo.tests.common import TransactionCase, tagged  # type: ignore


def _module_file(*parts):
    path = get_module_path("mail_management")
    return os.path.join(path, *parts)


def _users(env):
    """Two plain webmail users (A: owner, B: stranger) + A's manager."""
    group_user = env.ref("mail_management.group_mail_management_user")
    group_manager = env.ref("mail_management.group_mail_management_manager")
    user_a = env["res.users"].create(
        {
            "name": "Sig A",
            "login": "sig_a@test",
            "groups_id": [(6, 0, [group_user.id])],
        }
    )
    user_b = env["res.users"].create(
        {
            "name": "Sig B",
            "login": "sig_b@test",
            "groups_id": [(6, 0, [group_user.id])],
        }
    )
    manager = env["res.users"].create(
        {
            "name": "Sig Manager",
            "login": "sig_manager@test",
            "groups_id": [(6, 0, [group_manager.id])],
        }
    )
    return user_a, user_b, manager


# ===========================================================================
# Model: fields, sanitize-on-write, single-default enforcement
# ===========================================================================


@tagged("standard", "at_install")
class TestSignatureModel(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user_a, self.user_b, self.manager = _users(self.env)
        self.env_a = self.env(user=self.user_a)
        self.Signature = self.env["mail.management.signature"]
        self.Mailbox = self.env["mail.management.mailbox"]
        self.mailbox = self.Mailbox.create(
            {
                "name": "Sig Mailbox",
                "user_id": self.user_a.id,
                "provider_type": "generic",
                "imap_host": "imap.sig.com",
                "imap_user": "a@sig.com",
                "smtp_host": "smtp.sig.com",
                "from_email": "a@sig.com",
                "credential": "sig-pass",
                "state": "active",
            }
        )

    def test_create_signature_basic_fields(self):
        sig = self.Signature.create(
            {
                "name": "Professional",
                "user_id": self.user_a.id,
                "mailbox_id": self.mailbox.id,
                "html_body": "<p>Cheers,<br/>Alice</p>",
                "is_default": True,
            }
        )
        self.assertEqual(sig.name, "Professional")
        self.assertEqual(sig.user_id, self.user_a)
        self.assertEqual(sig.mailbox_id, self.mailbox)
        self.assertIn("Cheers", sig.html_body or "")
        self.assertTrue(sig.is_default)

    def test_default_user_is_current_user(self):
        sig = self.env_a["mail.management.signature"].create(
            {"name": "Mine", "html_body": "<p>x</p>"}
        )
        self.assertEqual(sig.user_id, self.user_a)

    def test_mailbox_is_optional(self):
        sig = self.Signature.create(
            {"name": "Global", "user_id": self.user_a.id, "html_body": "<p>g</p>"}
        )
        self.assertFalse(sig.mailbox_id)

    # -- sanitize-on-write (never trust input) ------------------------------

    def test_sanitize_on_create_strips_script(self):
        sig = self.Signature.create(
            {
                "name": "Evil",
                "user_id": self.user_a.id,
                "html_body": "<script>alert(1)</script><p>hello</p>",
            }
        )
        self.assertNotIn("<script>", sig.html_body or "")
        self.assertNotIn("alert(1)", sig.html_body or "")
        self.assertIn("hello", sig.html_body or "")

    def test_sanitize_on_write_strips_script(self):
        sig = self.Signature.create(
            {"name": "Clean", "user_id": self.user_a.id, "html_body": "<p>ok</p>"}
        )
        sig.write(
            {"html_body": "<p>fine</p><script>steal()</script><p onmouseover='x'>t</p>"}
        )
        self.assertNotIn("<script>", sig.html_body or "")
        self.assertNotIn("onmouseover", sig.html_body or "")
        self.assertIn("fine", sig.html_body or "")

    def test_sanitize_keeps_img_strips_onerror(self):
        sig = self.Signature.create(
            {
                "name": "Logo",
                "user_id": self.user_a.id,
                "html_body": '<p><img src="https://x/l.png" onerror="alert(1)"/></p>',
            }
        )
        self.assertIn("<img", sig.html_body or "")
        self.assertNotIn("onerror", sig.html_body or "")

    # -- one default per (user, mailbox) -------------------------------------

    def test_second_default_same_scope_unsets_first(self):
        first = self.Signature.create(
            {
                "name": "First",
                "user_id": self.user_a.id,
                "mailbox_id": self.mailbox.id,
                "is_default": True,
                "html_body": "<p>1</p>",
            }
        )
        second = self.Signature.create(
            {
                "name": "Second",
                "user_id": self.user_a.id,
                "mailbox_id": self.mailbox.id,
                "is_default": True,
                "html_body": "<p>2</p>",
            }
        )
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)

    def test_write_default_unsets_others(self):
        first = self.Signature.create(
            {
                "name": "First",
                "user_id": self.user_a.id,
                "mailbox_id": self.mailbox.id,
                "is_default": True,
                "html_body": "<p>1</p>",
            }
        )
        other = self.Signature.create(
            {
                "name": "Other",
                "user_id": self.user_a.id,
                "mailbox_id": self.mailbox.id,
                "html_body": "<p>2</p>",
            }
        )
        other.write({"is_default": True})
        self.assertFalse(first.is_default)
        self.assertTrue(other.is_default)

    def test_defaults_coexist_across_mailboxes(self):
        mb2 = self.Mailbox.create(
            {
                "name": "Second Mailbox",
                "user_id": self.user_a.id,
                "provider_type": "generic",
                "imap_host": "imap2.sig.com",
                "imap_user": "a2@sig.com",
                "smtp_host": "smtp2.sig.com",
                "from_email": "a2@sig.com",
                "credential": "sig-pass",
                "state": "active",
            }
        )
        one = self.Signature.create(
            {
                "name": "MB1",
                "user_id": self.user_a.id,
                "mailbox_id": self.mailbox.id,
                "is_default": True,
                "html_body": "<p>1</p>",
            }
        )
        two = self.Signature.create(
            {
                "name": "MB2",
                "user_id": self.user_a.id,
                "mailbox_id": mb2.id,
                "is_default": True,
                "html_body": "<p>2</p>",
            }
        )
        self.assertTrue(one.is_default)
        self.assertTrue(two.is_default)

    def test_mailbox_default_and_global_default_coexist(self):
        """Different scopes: (user, mailbox) and (user, no mailbox)."""
        specific = self.Signature.create(
            {
                "name": "Specific",
                "user_id": self.user_a.id,
                "mailbox_id": self.mailbox.id,
                "is_default": True,
                "html_body": "<p>s</p>",
            }
        )
        glob = self.Signature.create(
            {
                "name": "Global",
                "user_id": self.user_a.id,
                "mailbox_id": False,
                "is_default": True,
                "html_body": "<p>g</p>",
            }
        )
        self.assertTrue(specific.is_default)
        self.assertTrue(glob.is_default)

    def test_second_global_default_unsets_first(self):
        first = self.Signature.create(
            {
                "name": "G1",
                "user_id": self.user_a.id,
                "mailbox_id": False,
                "is_default": True,
                "html_body": "<p>1</p>",
            }
        )
        second = self.Signature.create(
            {
                "name": "G2",
                "user_id": self.user_a.id,
                "mailbox_id": False,
                "is_default": True,
                "html_body": "<p>2</p>",
            }
        )
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)

    def test_other_users_default_not_touched(self):
        """The single-default enforcement is scoped per USER too."""
        mine = self.Signature.create(
            {
                "name": "Mine",
                "user_id": self.user_a.id,
                "mailbox_id": False,
                "is_default": True,
                "html_body": "<p>a</p>",
            }
        )
        theirs = self.Signature.create(
            {
                "name": "Theirs",
                "user_id": self.user_b.id,
                "mailbox_id": False,
                "is_default": True,
                "html_body": "<p>b</p>",
            }
        )
        self.assertTrue(mine.is_default)
        self.assertTrue(theirs.is_default)


# ===========================================================================
# Isolation: ir.rules + ir.model.access
# ===========================================================================


@tagged("standard", "at_install")
class TestSignatureIsolation(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user_a, self.user_b, self.manager = _users(self.env)
        self.env_a = self.env(user=self.user_a)
        self.env_b = self.env(user=self.user_b)
        self.env_manager = self.env(user=self.manager)
        self.Signature = self.env["mail.management.signature"]
        self.sig_a = self.Signature.create(
            {
                "name": "A sig",
                "user_id": self.user_a.id,
                "html_body": "<p>a</p>",
            }
        )

    def test_user_sees_only_own_signatures(self):
        sig_b = self.Signature.create(
            {"name": "B sig", "user_id": self.user_b.id, "html_body": "<p>b</p>"}
        )
        visible_a = self.env_a["mail.management.signature"].search([])
        self.assertEqual(visible_a.ids, [self.sig_a.id])
        visible_b = self.env_b["mail.management.signature"].search([])
        self.assertEqual(visible_b.ids, [sig_b.id])

    def test_user_cannot_read_other_users_signature(self):
        stranger_copy = self.env_b["mail.management.signature"].browse(self.sig_a.id)
        with self.assertRaises(AccessError):
            stranger_copy.read()

    def test_user_group_has_full_acl_on_signatures(self):
        """The user group can create/write/unlink its OWN signatures."""
        model_access = self.env["ir.model.access"].search(
            [
                ("model_id.model", "=", "mail.management.signature"),
                (
                    "group_id",
                    "=",
                    self.env.ref("mail_management.group_mail_management_user").id,
                ),
            ]
        )
        self.assertTrue(model_access)
        self.assertTrue(
            all(
                access.perm_read
                and access.perm_write
                and access.perm_create
                and access.perm_unlink
                for access in model_access
            )
        )

    def test_user_can_manage_own_signature(self):
        own = self.env_a["mail.management.signature"].create(
            {"name": "Managed", "html_body": "<p>m</p>"}
        )
        own.write({"name": "Renamed"})
        self.assertEqual(own.name, "Renamed")
        own.unlink()
        self.assertFalse(
            self.env_a["mail.management.signature"].search([("name", "=", "Renamed")])
        )

    def test_manager_rule_sees_all(self):
        self.Signature.create(
            {"name": "B sig", "user_id": self.user_b.id, "html_body": "<p>b</p>"}
        )
        visible = self.env_manager["mail.management.signature"].search([])
        self.assertIn(self.sig_a.id, visible.ids)
        self.assertEqual(len(visible), 2)

    def test_isolation_rules_exist(self):
        user_rule = self.env.ref("mail_management.rule_signature_user_own")
        self.assertEqual(
            user_rule.domain_force,
            "[('user_id', '=', user.id)]",
        )
        manager_rule = self.env.ref("mail_management.rule_signature_manager_all")
        self.assertEqual(manager_rule.domain_force, "[(1, '=', 1)]")


# ===========================================================================
# Resolution: mailbox._get_default_signature()
# ===========================================================================


@tagged("standard", "at_install")
class TestSignatureResolution(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user_a, self.user_b, self.manager = _users(self.env)
        self.Signature = self.env["mail.management.signature"]
        self.Mailbox = self.env["mail.management.mailbox"]
        self.mailbox = self.Mailbox.create(
            {
                "name": "Resolve MB",
                "user_id": self.user_a.id,
                "provider_type": "generic",
                "imap_host": "imap.res.com",
                "imap_user": "a@res.com",
                "smtp_host": "smtp.res.com",
                "from_email": "a@res.com",
                "credential": "res-pass",
                "state": "active",
            }
        )

    def test_mailbox_specific_default_wins_over_global(self):
        self.Signature.create(
            {
                "name": "Global def",
                "user_id": self.user_a.id,
                "mailbox_id": False,
                "is_default": True,
                "html_body": "<p>g</p>",
            }
        )
        specific = self.Signature.create(
            {
                "name": "Specific def",
                "user_id": self.user_a.id,
                "mailbox_id": self.mailbox.id,
                "is_default": True,
                "html_body": "<p>s</p>",
            }
        )
        self.assertEqual(self.mailbox._get_default_signature(), specific)

    def test_global_default_used_when_no_specific(self):
        glob = self.Signature.create(
            {
                "name": "Global def",
                "user_id": self.user_a.id,
                "mailbox_id": False,
                "is_default": True,
                "html_body": "<p>g</p>",
            }
        )
        self.assertEqual(self.mailbox._get_default_signature(), glob)

    def test_no_default_returns_empty_recordset(self):
        self.Signature.create(
            {
                "name": "Not default",
                "user_id": self.user_a.id,
                "mailbox_id": self.mailbox.id,
                "is_default": False,
                "html_body": "<p>n</p>",
            }
        )
        resolved = self.mailbox._get_default_signature()
        self.assertFalse(resolved)

    def test_non_default_signature_not_resolved(self):
        """A non-default signature (even mailbox-specific) is never picked."""
        self.Signature.create(
            {
                "name": "Dormant",
                "user_id": self.user_a.id,
                "mailbox_id": self.mailbox.id,
                "is_default": False,
                "html_body": "<p>d</p>",
            }
        )
        self.assertFalse(self.mailbox._get_default_signature())

    def test_specific_of_other_mailbox_not_resolved(self):
        other_mb = self.Mailbox.create(
            {
                "name": "Other MB",
                "user_id": self.user_a.id,
                "provider_type": "generic",
                "imap_host": "imap2.res.com",
                "imap_user": "a2@res.com",
                "smtp_host": "smtp2.res.com",
                "from_email": "a2@res.com",
                "credential": "res-pass",
                "state": "active",
            }
        )
        other_sig = self.Signature.create(
            {
                "name": "Other MB def",
                "user_id": self.user_a.id,
                "mailbox_id": other_mb.id,
                "is_default": True,
                "html_body": "<p>o</p>",
            }
        )
        resolved = self.mailbox._get_default_signature()
        self.assertNotEqual(resolved, other_sig)
        self.assertFalse(resolved)

    def test_resolution_uses_mailbox_owner(self):
        """The default resolved for a mailbox belongs to ITS owner, whatever
        the current user is (manager/cron context included)."""
        sig_b = self.Signature.create(
            {
                "name": "B default",
                "user_id": self.user_b.id,
                "mailbox_id": False,
                "is_default": True,
                "html_body": "<p>b</p>",
            }
        )
        mailbox_b = self.Mailbox.create(
            {
                "name": "B Mailbox",
                "user_id": self.user_b.id,
                "provider_type": "generic",
                "imap_host": "imap.b.com",
                "imap_user": "b@b.com",
                "smtp_host": "smtp.b.com",
                "from_email": "b@b.com",
                "credential": "b-pass",
                "state": "active",
            }
        )
        # Called through the ADMIN environment — must still resolve user B's
        # signature, not admin's (explicit owner scoping inside the method).
        self.assertEqual(mailbox_b._get_default_signature(), sig_b)


# ===========================================================================
# /mail_management/signatures controller
# ===========================================================================


@tagged("standard", "at_install")
class TestSignatureController(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user_a, self.user_b, self.manager = _users(self.env)
        self.env_a = self.env(user=self.user_a)
        self.env_b = self.env(user=self.user_b)
        self.Mailbox = self.env["mail.management.mailbox"]
        self.mailbox = self.Mailbox.create(
            {
                "name": "Ctrl MB",
                "user_id": self.user_a.id,
                "provider_type": "generic",
                "imap_host": "imap.ctrl.com",
                "imap_user": "a@ctrl.com",
                "smtp_host": "smtp.ctrl.com",
                "from_email": "a@ctrl.com",
                "credential": "ctrl-pass",
                "state": "active",
            }
        )
        self.specific = self.env["mail.management.signature"].create(
            {
                "name": "Specific",
                "user_id": self.user_a.id,
                "mailbox_id": self.mailbox.id,
                "is_default": True,
                "html_body": "<p>specific body</p>",
            }
        )
        self.glob = self.env["mail.management.signature"].create(
            {
                "name": "Global",
                "user_id": self.user_a.id,
                "mailbox_id": False,
                "html_body": "<p>global body</p>",
            }
        )
        from odoo.addons.mail_management.controllers.main import (  # type: ignore
            MailManagementController,
        )

        self.controller = MailManagementController()

    def test_returns_own_signatures_and_default(self):
        with _mock_request(self.env_a):
            result = self.controller.get_signatures(mailbox_id=self.mailbox.id)
        ids = [sig["id"] for sig in result["signatures"]]
        self.assertIn(self.specific.id, ids)
        self.assertIn(self.glob.id, ids)
        self.assertEqual(result["default_id"], self.specific.id)
        by_id = {sig["id"]: sig for sig in result["signatures"]}
        self.assertEqual(by_id[self.glob.id]["html_body"], "<p>global body</p>")
        self.assertEqual(by_id[self.specific.id]["mailbox_id"], self.mailbox.id)

    def test_cross_user_gets_nothing(self):
        with _mock_request(self.env_b):
            result = self.controller.get_signatures(mailbox_id=self.mailbox.id)
        self.assertEqual(result["signatures"], [])
        self.assertFalse(result["default_id"])

    def test_signature_of_other_mailbox_excluded(self):
        """A mailbox-specific signature of ANOTHER mailbox is not offered."""
        mb2 = self.Mailbox.create(
            {
                "name": "Ctrl MB 2",
                "user_id": self.user_a.id,
                "provider_type": "generic",
                "imap_host": "imap2.ctrl.com",
                "imap_user": "a2@ctrl.com",
                "smtp_host": "smtp2.ctrl.com",
                "from_email": "a2@ctrl.com",
                "credential": "ctrl-pass",
                "state": "active",
            }
        )
        other = self.env["mail.management.signature"].create(
            {
                "name": "Other MB",
                "user_id": self.user_a.id,
                "mailbox_id": mb2.id,
                "is_default": True,
                "html_body": "<p>other</p>",
            }
        )
        with _mock_request(self.env_a):
            result = self.controller.get_signatures(mailbox_id=self.mailbox.id)
        self.assertNotIn(other.id, [sig["id"] for sig in result["signatures"]])

    def test_forged_mailbox_still_returns_own_globals(self):
        """A cross-user mailbox id does not resolve (ir.rules) — the user
        still gets their own global signatures (used by the picker)."""
        with _mock_request(self.env_a):
            result = self.controller.get_signatures(mailbox_id=999999)
        ids = [sig["id"] for sig in result["signatures"]]
        self.assertEqual(ids, [self.glob.id])
        self.assertFalse(result["default_id"])

    def test_no_mailbox_returns_globals_with_default(self):
        self.glob.write({"is_default": True})
        with _mock_request(self.env_a):
            result = self.controller.get_signatures()
        ids = [sig["id"] for sig in result["signatures"]]
        self.assertEqual(ids, [self.glob.id, self.specific.id])
        self.assertEqual(result["default_id"], self.glob.id)

    def test_payload_html_is_sanitized(self):
        """Whatever is stored comes back clean (script never reaches the
        client through this route)."""
        self.env["mail.management.signature"].create(
            {
                "name": "Dirty",
                "user_id": self.user_a.id,
                "html_body": "<script>alert(1)</script><p>clean</p>",
            }
        )
        with _mock_request(self.env_a):
            result = self.controller.get_signatures()
        for sig in result["signatures"]:
            self.assertNotIn("<script>", sig["html_body"])
            self.assertNotIn("onerror", sig["html_body"])


# ===========================================================================
# Config UI: views, action, menu
# ===========================================================================


@tagged("standard", "at_install")
class TestSignatureConfigUi(TransactionCase):
    def test_signature_views_and_action_exist(self):
        action = self.env.ref("mail_management.action_mail_management_signature")
        self.assertEqual(action.type, "ir.actions.act_window")
        self.assertEqual(action.res_model, "mail.management.signature")
        self.assertIn("tree", (action.view_mode or "").split(","))
        self.assertIn("form", (action.view_mode or "").split(","))

        list_view = self.env.ref("mail_management.view_signature_list")
        self.assertEqual(list_view.type, "tree")
        form_view = self.env.ref("mail_management.view_signature_form")
        self.assertEqual(form_view.type, "form")
        self.assertIn("html_body", form_view.arch_db)
        self.assertIn('widget="html"', form_view.arch_db)

    def test_signature_menu_under_root(self):
        menu = self.env.ref("mail_management.menu_signatures")
        root = self.env.ref("mail_management.menu_mail_management_root")
        self.assertEqual(menu.parent_id.id, root.id)
        self.assertEqual(
            menu.action.id,
            self.env.ref("mail_management.action_mail_management_signature").id,
        )


# ===========================================================================
# Compose rich-text wiring (headless source assertions)
# ===========================================================================


@tagged("standard", "at_install")
class TestComposeRichTextWiring(TransactionCase):
    def test_manifest_version_bumped(self):
        with open(_module_file("__manifest__.py"), encoding="utf-8") as fh:
            manifest = ast.literal_eval(fh.read())
        self.assertEqual(manifest["version"], "17.0.17.0.0")

    def test_manifest_depends_on_html_editor(self):
        with open(_module_file("__manifest__.py"), encoding="utf-8") as fh:
            manifest = ast.literal_eval(fh.read())
        self.assertIn("web_editor", manifest["depends"])

    def test_manifest_data_includes_signature_views(self):
        with open(_module_file("__manifest__.py"), encoding="utf-8") as fh:
            manifest = ast.literal_eval(fh.read())
        # Module-relative data path (the established convention in this
        # manifest — full module prefix is only used for assets).
        self.assertIn(
            "views/mail_management_signature_views.xml",
            manifest["data"],
        )

    def test_compose_dialog_embeds_wysiwyg(self):
        with open(
            _module_file("static/src/components/compose_dialog.js"), encoding="utf-8"
        ) as fh:
            js = fh.read()
        # The sanctioned Odoo 19 embed (verified in-container: this is the
        # pattern mail's Composer and website_profile's ProfileDialog use).
        self.assertIn("@web_editor/js/wysiwyg/wysiwyg", js)
        self.assertIn("Wysiwyg", js)
        self.assertIn("wysiwygOptions", js)
        self.assertIn("startWysiwyg", js)

    def test_compose_template_has_editor_and_signature_picker(self):
        with open(
            _module_file("static/src/xml/compose_dialog.xml"), encoding="utf-8"
        ) as fh:
            xml = fh.read()
        self.assertIn("<Wysiwyg", xml)
        self.assertIn("wysiwygOptions", xml)
        self.assertIn("o_webmail_compose_signature", xml)
        # The plain-text body textarea is gone (rich editor replaces it).
        self.assertNotIn('id="o_webmail_compose_body"', xml)

    def test_compose_dialog_sends_body_html(self):
        with open(
            _module_file("static/src/components/compose_dialog.js"), encoding="utf-8"
        ) as fh:
            js = fh.read()
        self.assertIn("body_html", js)
        self.assertIn("/mail_management/signatures", js)

    def test_compose_dialog_signature_replacement_wired(self):
        """Switching signature replaces the previously inserted block."""
        with open(
            _module_file("static/src/components/compose_dialog.js"), encoding="utf-8"
        ) as fh:
            js = fh.read()
        self.assertIn("onSignatureChange", js)
        self.assertIn("data-o-mail-signature", js)

    def test_default_signature_inserted_on_open(self):
        """The dialog builds its initial body with the default signature
        (loaded before mount through /mail_management/signatures)."""
        with open(
            _module_file("static/src/components/compose_dialog.js"), encoding="utf-8"
        ) as fh:
            js = fh.read()
        self.assertIn("_loadSignatures", js)
        self.assertIn("_initialBodyHtml", js)
        self.assertIn("--&nbsp;", js)

    def test_message_list_has_compose_new_button(self):
        with open(
            _module_file("static/src/xml/message_list.xml"), encoding="utf-8"
        ) as fh:
            xml = fh.read()
        self.assertIn("o_webmail_compose_new", xml)

    def test_webmail_client_supports_new_mode(self):
        with open(_module_file("static/src/webmail_client.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn('"new"', js)


# ===========================================================================
# TRIANGULATE (PR-2c): rich signature content surviving the sanitize contract
# ===========================================================================


@tagged("standard", "at_install")
class TestSignatureRichContent(TransactionCase):
    """Signatures with links and images: safe markup kept, unsafe stripped."""

    def setUp(self):
        super().setUp()
        self.Signature = self.env["mail.management.signature"]

    def _create(self, html):
        return self.Signature.create(
            {"name": "Rich", "user_id": self.env.user.id, "html_body": html}
        )

    def test_link_and_image_survive_sanitize(self):
        sig = self._create(
            '<p>Alice — <a href="https://example.com">site</a> '
            '<img src="https://example.com/logo.png" alt="logo"/></p>'
        )
        body = sig.html_body or ""
        self.assertIn("<a", body)
        self.assertIn("https://example.com", body)
        self.assertIn("<img", body)

    def test_javascript_link_never_stored(self):
        sig = self._create('<p><a href="javascript:alert(1)">click</a></p>')
        body = sig.html_body or ""
        self.assertNotIn("javascript:", body)

    def test_inline_styles_survive(self):
        """Standard signature styling (font/color) is kept."""
        sig = self._create('<p style="color: #888; font-size: small;">Alice Smith</p>')
        self.assertIn("Alice Smith", sig.html_body or "")

    def test_signature_html_flows_through_send_unsanitized_parts_dropped(self):
        """TRIANGULATE: a rich default signature resolved for a mailbox is
        clean at resolution time (stored sanitized) — links/images intact."""
        from unittest import mock

        from odoo.addons.mail_management.tests.test_compose_send import (  # type: ignore
            IMAP_PATCH,
            SMTP_PATCH,
            FakeImapClient,
            FakeSmtpClient,
        )

        mailbox = self.env["mail.management.mailbox"].create(
            {
                "name": "Rich MB",
                "provider_type": "generic",
                "imap_host": "imap.rich.com",
                "imap_user": "a@rich.com",
                "smtp_host": "smtp.rich.com",
                "from_email": "a@rich.com",
                "credential": "rich-pass",
                "state": "active",
            }
        )
        sig = self._create(
            '<p><a href="https://example.com">Alice</a> '
            '<img src="https://example.com/l.png"/></p>'
        )
        sig.write({"is_default": True, "mailbox_id": mailbox.id})
        resolved = mailbox._get_default_signature()
        self.assertIn("<a", resolved.html_body or "")
        self.assertIn("<img", resolved.html_body or "")
        # And the resolved signature, composed into a body, sends clean.
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            mailbox._send_message(
                to="x@y.com",
                subject="hi",
                body_html=f"<p>Reply text</p>{resolved.html_body}",
            )
        html_part = next(
            p for p in smtp.sent[0].walk() if p.get_content_type() == "text/html"
        )
        content = html_part.get_content()
        self.assertIn("<a", content)
        self.assertIn("<img", content)
        self.assertNotIn("<script>", content)
