"""Strict-TDD RED tests for the read-only webmail UI wiring (PR-1b-ii).

Covers the *backend-visible* part of the UI:

* ``ir.actions.act_window`` + list/form/search views for
  ``mail.management.mailbox``;
* the ``ir.actions.client`` record (tag ``mail_management.webmail``);
* menus (root / Webmail / My Mailboxes);
* manifest assets bundle + version bump;
* the OWL templates never use ``t-raw`` (XSS guard — design §G.1);
* ``action_test_connection`` returns a ``display_notification`` action so the
  form button shows feedback, while keeping ``success``/``message`` keys for
  programmatic callers (PR-1a contract).

Written BEFORE the views/action/assets exist (RED).
"""

import ast
import os
from unittest import mock

from odoo.modules.module import get_module_path  # type: ignore
from odoo.tests.common import TransactionCase, tagged  # type: ignore


@tagged("standard", "at_install")
class TestWebmailUiWiring(TransactionCase):
    """UI records, assets and view wiring for the read-only webmail."""

    # ------------------------------------------------------------------
    # Mailbox configuration views / action
    # ------------------------------------------------------------------
    def test_mailbox_action_and_views_exist(self):
        action = self.env.ref("mail_management.action_mail_management_mailbox")
        self.assertEqual(action.type, "ir.actions.act_window")
        self.assertEqual(action.res_model, "mail.management.mailbox")
        self.assertIn("list", (action.view_mode or "").split(","))
        self.assertIn("form", (action.view_mode or "").split(","))

        list_view = self.env.ref("mail_management.view_mailbox_list")
        self.assertEqual(list_view.type, "list")
        form_view = self.env.ref("mail_management.view_mailbox_form")
        self.assertEqual(form_view.type, "form")

    def test_webmail_client_action_record(self):
        """The OWL client action is declared with the right tag."""
        action = self.env.ref("mail_management.action_webmail_client")
        self.assertEqual(action.type, "ir.actions.client")
        self.assertEqual(action.tag, "mail_management.webmail")

    def test_menus_exist(self):
        root = self.env.ref("mail_management.menu_mail_management_root")
        self.assertTrue(root.name)

        webmail_menu = self.env.ref("mail_management.menu_webmail")
        self.assertEqual(
            webmail_menu.action.id,
            self.env.ref("mail_management.action_webmail_client").id,
        )
        self.assertEqual(webmail_menu.parent_id.id, root.id)

        mailbox_menu = self.env.ref("mail_management.menu_mailboxes")
        self.assertEqual(
            mailbox_menu.action.id,
            self.env.ref("mail_management.action_mail_management_mailbox").id,
        )
        self.assertEqual(mailbox_menu.parent_id.id, root.id)

    # ------------------------------------------------------------------
    # Manifest: version bump + asset bundles
    # ------------------------------------------------------------------
    @staticmethod
    def _read_manifest():
        path = get_module_path("mail_management")
        with open(os.path.join(path, "__manifest__.py"), encoding="utf-8") as fh:
            return ast.literal_eval(fh.read())

    @staticmethod
    def _read_module_file(relative):
        """Read a module source file (UI wiring needles)."""
        path = get_module_path("mail_management")
        with open(os.path.join(path, relative), encoding="utf-8") as fh:
            return fh.read()

    def test_manifest_version_bumped(self):
        self.assertEqual(self._read_manifest()["version"], "18.0.17.0.0")

    def test_manifest_assets_declared(self):
        assets = self._read_manifest()["assets"]
        backend = assets.get("web.assets_backend", [])
        for needed in (
            "mail_management/static/src/webmail_client.js",
            "mail_management/static/src/components/folder_tree.js",
            "mail_management/static/src/components/message_list.js",
            "mail_management/static/src/components/reading_pane.js",
            "mail_management/static/src/xml/webmail_client.xml",
            "mail_management/static/src/scss/webmail.scss",
        ):
            self.assertIn(needed, backend)
        tests = assets.get("web.assets_tests", [])
        self.assertIn("mail_management/static/tests/tours/webmail_tour.js", tests)

    # ------------------------------------------------------------------
    # XSS guard: t-out only, never t-raw (design §G.1, R7)
    # ------------------------------------------------------------------
    def test_owl_templates_never_use_t_raw(self):
        module_path = get_module_path("mail_management")
        static_dirs = ("static/src", "static/tests")
        offenders = []
        for static_dir in static_dirs:
            base = os.path.join(module_path, static_dir)
            for root, _dirs, files in os.walk(base):
                for fname in files:
                    if not fname.endswith((".xml", ".js")):
                        continue
                    full = os.path.join(root, fname)
                    with open(full, encoding="utf-8") as fh:
                        content = fh.read()
                        if "t-raw=" in content or "t-raw =" in content:
                            offenders.append(os.path.relpath(full, module_path))
            self.assertEqual(offenders, [])

    # ------------------------------------------------------------------
    # PR-2e: settings panel + consolidated buttons (gear / sort menu /
    # overflow menu) — wiring needles beyond test_webmail_settings.py.
    # ------------------------------------------------------------------

    def test_folder_tree_settings_button_wired(self):
        """The gear ⚙ opens the settings panel (SnappyMail-style)."""
        xml = self._read_module_file("static/src/xml/folder_tree.xml")
        for needle in (
            "o_webmail_settings_btn",
            "openSettings",
        ):
            self.assertIn(needle, xml)

    def test_message_list_sort_menu_wired(self):
        """ONE Sort dropdown replaces the clickable headers + toggles."""
        xml = self._read_module_file("static/src/xml/message_list.xml")
        for needle in (
            "o_webmail_sort_menu_btn",
            "o_webmail_sort_menu",
        ):
            self.assertIn(needle, xml)
        self.assertNotIn("o_webmail_sort_header", xml)

    def test_reading_pane_overflow_menu_wired(self):
        """Right-side overflow ⋯ groups the secondary message actions."""
        xml = self._read_module_file("static/src/xml/reading_pane.xml")
        for needle in (
            "o_webmail_msg_menu_btn",
            "o_webmail_msg_menu",
        ):
            self.assertIn(needle, xml)

        # ------------------------------------------------------------------
        # Test Connection button feedback (form button UX)
        # ------------------------------------------------------------------

    def test_action_test_connection_returns_notification_action(self):
        """The button returns a display_notification action (UI feedback)
        while keeping the PR-1a ``success``/``message`` contract."""
        mailbox = self.env["mail.management.mailbox"].create(
            {
                "name": "UI Notify MB",
                "provider_type": "generic",
                "imap_host": "imap.ui.com",
                "imap_user": "ui@ui.com",
                "smtp_host": "smtp.ui.com",
                "from_email": "ui@ui.com",
                "credential": "ui-pass",
                "state": "draft",
            }
        )
        with (
            mock.patch(
                "odoo.addons.mail_management.models.mail_management.ImapClient",
                return_value=_FakeContextManager(),
            ),
            mock.patch(
                "odoo.addons.mail_management.models.mail_management.SmtpClient",
                return_value=_FakeContextManager(),
            ),
        ):
            result = mailbox.action_test_connection()

        self.assertEqual(result.get("type"), "ir.actions.client")
        self.assertEqual(result.get("tag"), "display_notification")
        self.assertTrue(result["params"]["message"])
        # PR-1a programmatic contract kept.
        self.assertTrue(result["success"])
        self.assertIn("success", result)
        self.assertIn("message", result)


class _FakeContextManager:
    """Minimal ``ImapClient``/``SmtpClient`` mock: success on enter/exit."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
