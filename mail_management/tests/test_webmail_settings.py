"""Strict-TDD RED tests for PR-2e — per-user webmail settings + image
policy + button consolidation.

Contract under test (parent PR-2e scope + tasks.md):

Model ``mail.management.settings``:

* per-user SINGLETON: ``user_id`` required + unique; every user gets at most
  one row (SQL constraint);
* ``allow_remote_images`` Boolean default False;
* ``signature_position`` Selection below|above, default 'below';
* isolation through the established group-scoped ir.rules (user = own,
  manager = all) + ir.model.access rows.

Controller:

* ``GET /mail_management/settings`` — returns the CURRENT user's settings,
  AUTO-CREATING the singleton when missing (idempotent: never two rows);
* ``POST /mail_management/settings/save`` — writes the current user's
  singleton only; ``signature_position`` outside below|above → UserError;
* cross-user isolation: another user's save never touches the first user's
  row (even for a manager — the endpoints scope to ``env.user``).

Compose prefill: the ``signaturePosition`` setting is consumed by
ComposeDialog — 'below' (default) appends the signature UNDER the quoted
block, 'above' inserts it BEFORE the quote (source-wiring assertions, the
established PR-2a/2d pattern; the real browser tour covers the panel).

Image policy (client-side, defense-in-depth layer only — stored bodies stay
server-sanitized): ``prepareBody(html, attachments, {allowRemoteImages})``
keeps remote ``http(s)`` images when allowed, blocks them (current
behavior) when not; CID inline images resolve regardless; a per-message
"Images blocked — Show images" bar reveals them for THAT message only.

UI consolidation needles: gear settings button in the tree header, ONE
sort dropdown in the message list (replacing 3 clickable headers + 2
toggles), reading-pane overflow menu, icon-only bulk bar.
"""

import ast
import os

import psycopg2
from odoo.addons.mail_management.tests.test_compose_send import (  # type: ignore
    _mock_request,
)
from odoo.exceptions import AccessError, UserError  # type: ignore
from odoo.modules.module import get_module_path  # type: ignore
from odoo.tests.common import TransactionCase, tagged  # type: ignore


def _module_file(*parts):
    path = get_module_path("mail_management")
    return os.path.join(path, *parts)


def _read(relative):
    with open(_module_file(relative), encoding="utf-8") as fh:
        return fh.read()


def _users(env):
    """Two plain webmail users (A: owner, B: stranger) + A's manager."""
    group_user = env.ref("mail_management.group_mail_management_user")
    group_manager = env.ref("mail_management.group_mail_management_manager")
    user_a = env["res.users"].create(
        {
            "name": "Settings A",
            "login": "settings_a@test",
            "groups_id": [(6, 0, [group_user.id])],
        }
    )
    user_b = env["res.users"].create(
        {
            "name": "Settings B",
            "login": "settings_b@test",
            "groups_id": [(6, 0, [group_user.id])],
        }
    )
    manager = env["res.users"].create(
        {
            "name": "Settings Manager",
            "login": "settings_manager@test",
            "groups_id": [(6, 0, [group_manager.id])],
        }
    )
    return user_a, user_b, manager


def _controller():
    from odoo.addons.mail_management.controllers.main import (  # type: ignore
        MailManagementController,
    )

    return MailManagementController()


# ===========================================================================
# Model: fields, defaults, singleton constraint
# ===========================================================================


@tagged("standard", "at_install")
class TestSettingsModel(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user_a, self.user_b, self.manager = _users(self.env)
        self.Settings = self.env["mail.management.settings"]

    def test_create_defaults(self):
        settings = self.Settings.create({"user_id": self.user_a.id})
        self.assertFalse(settings.allow_remote_images)
        self.assertEqual(settings.signature_position, "below")

    def test_user_id_default_is_current_user(self):
        settings = self.Settings.with_user(self.user_a).create({})
        self.assertEqual(settings.user_id, self.user_a)

    def test_user_id_required(self):
        # Explicit False bypasses the env.user default → NOT NULL violation.
        with self.assertRaises(psycopg2.IntegrityError):
            self.Settings.create({"user_id": False})

    def test_one_row_per_user(self):
        """The settings row is a per-user SINGLETON (SQL unique constraint)."""
        self.Settings.create({"user_id": self.user_a.id})
        with self.assertRaises(psycopg2.IntegrityError):
            self.Settings.create({"user_id": self.user_a.id})

    def test_two_users_coexist(self):
        one = self.Settings.create({"user_id": self.user_a.id})
        two = self.Settings.create({"user_id": self.user_b.id})
        self.assertTrue(one.id and two.id and one.id != two.id)

    def test_selection_values(self):
        field = self.Settings._fields["signature_position"]
        self.assertEqual(
            [value for value, _label in field.selection], ["below", "above"]
        )

    def test_get_or_create_for_is_idempotent(self):
        """The model helper never creates a second row for the same user."""
        first = self.Settings._get_or_create_for(self.user_a)
        second = self.Settings._get_or_create_for(self.user_a)
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.Settings.search_count([]), 1)


# ===========================================================================
# Isolation (ir.rules + ACLs — the established pattern)
# ===========================================================================


@tagged("standard", "at_install")
class TestSettingsIsolation(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user_a, self.user_b, self.manager = _users(self.env)
        self.env_a = self.env(user=self.user_a)
        self.env_b = self.env(user=self.user_b)
        self.env_manager = self.env(user=self.manager)
        self.Settings = self.env["mail.management.settings"]
        self.settings_a = self.Settings.create(
            {"user_id": self.user_a.id, "allow_remote_images": True}
        )

    def test_user_sees_only_own_settings(self):
        visible_a = self.env_a["mail.management.settings"].search([])
        self.assertEqual(visible_a.ids, [self.settings_a.id])

    def test_user_cannot_read_others_settings(self):
        stranger = self.env_b["mail.management.settings"].browse(self.settings_a.id)
        with self.assertRaises(AccessError):
            stranger.read()

    def test_user_cannot_write_others_settings(self):
        stranger = self.env_b["mail.management.settings"].browse(self.settings_a.id)
        with self.assertRaises(AccessError):
            stranger.write({"allow_remote_images": False})

    def test_manager_sees_all(self):
        """The manager rule sees every user's settings (support use case)."""
        self.Settings.create({"user_id": self.user_b.id})
        visible_manager = self.env_manager["mail.management.settings"].search([])
        self.assertIn(self.settings_a.id, visible_manager.ids)
        self.assertEqual(len(visible_manager), 2)

    def test_user_group_has_acl(self):
        access = self.env["ir.model.access"].search(
            [
                ("model_id.model", "=", "mail.management.settings"),
                (
                    "group_id",
                    "=",
                    self.env.ref("mail_management.group_mail_management_user").id,
                ),
            ]
        )
        self.assertTrue(access)
        self.assertTrue(all(a.perm_read and a.perm_write for a in access))


# ===========================================================================
# Controller: GET auto-create + POST save
# ===========================================================================


@tagged("standard", "at_install")
class TestSettingsController(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user_a, self.user_b, self.manager = _users(self.env)
        self.env_a = self.env(user=self.user_a)
        self.env_b = self.env(user=self.user_b)
        self.env_manager = self.env(user=self.manager)
        self.controller = _controller()

    def test_get_auto_creates_singleton(self):
        with _mock_request(self.env_a):
            payload = self.controller.get_settings()
        self.assertEqual(self.env["mail.management.settings"].search_count([]), 1)
        self.assertFalse(payload["allow_remote_images"])
        self.assertEqual(payload["signature_position"], "below")

    def test_get_is_idempotent(self):
        with _mock_request(self.env_a):
            self.controller.get_settings()
            self.controller.get_settings()
        self.assertEqual(self.env["mail.management.settings"].search_count([]), 1)

    def test_save_round_trip(self):
        with _mock_request(self.env_a):
            result = self.controller.save_settings(
                allow_remote_images=True, signature_position="above"
            )
            payload = self.controller.get_settings()
        self.assertTrue(result["success"])
        self.assertTrue(payload["allow_remote_images"])
        self.assertEqual(payload["signature_position"], "above")

    def test_save_partial_keeps_other_value(self):
        with _mock_request(self.env_a):
            self.controller.save_settings(allow_remote_images=True)
            result = self.controller.save_settings(signature_position="above")
        self.assertTrue(result["allow_remote_images"])
        self.assertEqual(result["signature_position"], "above")

    def test_save_invalid_position_rejected(self):
        with _mock_request(self.env_a), self.assertRaises(UserError):
            self.controller.save_settings(signature_position="middle")

    def test_save_invalid_position_variants(self):
        # None is NOT in the list: it means "not provided" (partial save
        # keeps the current position — see test_save_partial_keeps_other_value).
        for bad in ("", "top", "Below"):
            # A single combined context per subtest (ruff SIM117): the
            # subTest context wraps the request+assertRaises pair.
            with (
                self.subTest(bad=bad),
                _mock_request(self.env_a),
                self.assertRaises(UserError),
            ):
                self.controller.save_settings(signature_position=bad)

    def test_save_scopes_to_current_user(self):
        """B's save never touches A's row — every user owns their settings."""
        with _mock_request(self.env_a):
            self.controller.get_settings()  # A's singleton (defaults)
        with _mock_request(self.env_b):
            self.controller.save_settings(
                allow_remote_images=True, signature_position="above"
            )
        settings_a = self.env["mail.management.settings"].search(
            [("user_id", "=", self.user_a.id)]
        )
        self.assertFalse(settings_a.allow_remote_images)
        self.assertEqual(settings_a.signature_position, "below")

    def test_manager_endpoints_still_scoped_to_own(self):
        """A manager calling the endpoints gets their OWN singleton, never a
        random other user's row (the search domain pins env.user)."""
        with _mock_request(self.env_a):
            self.controller.save_settings(allow_remote_images=True)
        with _mock_request(self.env_manager):
            payload = self.controller.get_settings()
            self.controller.save_settings(signature_position="above")
        self.assertFalse(payload["allow_remote_images"])  # fresh own defaults
        settings_a = self.env["mail.management.settings"].search(
            [("user_id", "=", self.user_a.id)]
        )
        self.assertTrue(settings_a.allow_remote_images)  # untouched
        self.assertEqual(settings_a.signature_position, "below")

    def test_routes_require_authentication(self):
        """Both endpoints are auth='user' (source-level route contract)."""
        src = _read("controllers/main.py")
        for route in (
            "/mail_management/settings",
            "/mail_management/settings/save",
        ):
            idx = src.index(f'"{route}"')
            # The @http.route decorator wrapping this def carries auth
            # (auth follows the route path inside the decorator).
            block = src[idx : idx + 200]
            self.assertIn('auth="user"', block)

    def test_settings_model_registered_in_manifest_data(self):
        """The settings security (rules + ACL) loads with the module."""
        manifest = _read_manifest()
        self.assertIn("security/security_groups.xml", manifest["data"])
        src = _read("security/security_groups.xml")
        self.assertIn("model_mail_management_settings", src)
        acl = _read("security/ir.model.access.csv")
        self.assertIn("model_mail_management_settings", acl)


def _read_manifest():
    path = _module_file("__manifest__.py")
    with open(path, encoding="utf-8") as fh:
        source = "\n".join(
            ln for ln in fh.read().splitlines() if not ln.lstrip().startswith("#")
        )
    return ast.literal_eval(source)


# ===========================================================================
# Compose prefill: signature position consumed above/below
# ===========================================================================


@tagged("standard", "at_install")
class TestComposeSignaturePositionWiring(TransactionCase):
    """'above' inserts the signature BEFORE the quoted block; 'below' (the
    default) keeps it UNDER the quote.  The prefill helper + the replacement
    helper both consume the position (source assertions — the established
    pattern; the settings panel itself is covered by the browser tour)."""

    def test_initial_body_consumes_signature_position(self):
        js = _read("static/src/components/compose_dialog.js")
        self.assertIn("signaturePosition", js)
        # The prefill helper branches on the position.
        self.assertRegex(js, r"signaturePosition\s*===?\s*[\"']above[\"']")
        # 'above' = signature FIRST, then the quoted block (pure helper).
        self.assertIn("composeBodyHtml(", js)

    def test_compose_body_helper_orders_parts(self):
        """Pure part-ordering helper: above → signature then quote; below →
        quote then signature (single point of truth for placement)."""
        js = _read("static/src/components/compose_dialog.js")
        self.assertIn("export function composeBodyHtml", js)
        # The above branch returns signature + quote; below returns quote +
        # signature (the helper is the only place these orders appear).
        self.assertIn("signatureHtml + quoteHtml", js)
        self.assertIn("quoteHtml + signatureHtml", js)

    def test_replace_signature_respects_position(self):
        """Switching the signature re-inserts at the configured position."""
        js = _read("static/src/components/compose_dialog.js")
        replace = js[js.index("_replaceSignature") :]
        self.assertIn("insertBefore", replace)

    def test_client_passes_position_to_compose(self):
        js = _read("static/src/webmail_client.js")
        self.assertIn("signaturePosition", js)
        # The prop comes from the loaded settings.
        self.assertRegex(
            js, r"signaturePosition:\s*this\.state\.settings\.signaturePosition"
        )


# ===========================================================================
# Image policy: prepareBody allow option + per-message reveal
# ===========================================================================


@tagged("standard", "at_install")
class TestImagePolicyWiring(TransactionCase):
    def test_prepare_body_accepts_allow_remote_images_option(self):
        js = _read("static/src/utils/prepare_body.js")
        self.assertIn("allowRemoteImages", js)
        # Allowed remote images keep their src; blocked ones are stripped.
        self.assertIn("o_webmail_img_blocked", js)  # blocking branch stays

    def test_cid_resolution_unchanged(self):
        js = _read("static/src/utils/prepare_body.js")
        self.assertIn("cid:", js)
        self.assertIn("/mail_management/attachment/", js)

    def test_client_tracks_revealed_messages(self):
        """Per-message reveal state — revealing one message never leaks to
        another (the state is a list of message ids)."""
        js = _read("static/src/webmail_client.js")
        for needle in (
            "revealedImageIds",
            "showImages",
            "hasRemoteImages",
        ):
            self.assertIn(needle, js)

    def test_client_reprepares_body_without_reload(self):
        """Saving settings (or revealing) re-prepares the OPEN message body
        in place — no reload, no re-fetch of the list."""
        js = _read("static/src/webmail_client.js")
        for needle in (
            "_reprepareOpenBody",
            "prepareBody(",
            "saveSettings",
        ):
            self.assertIn(needle, js)

    def test_reading_pane_has_images_blocked_bar(self):
        xml = _read("static/src/xml/reading_pane.xml")
        for needle in (
            "o_webmail_images_blocked_bar",
            "o_webmail_show_images_btn",
            "imagesBlocked",
        ):
            self.assertIn(needle, xml)

    def test_reading_pane_bar_label_translatable(self):
        js = _read("static/src/components/reading_pane.js")
        for needle in ('_t("Images blocked")', '_t("Show images")'):
            self.assertIn(needle, js)


# ===========================================================================
# UI consolidation needles (gear / sort menu / overflow / bulk bar)
# ===========================================================================


@tagged("standard", "at_install")
class TestUiConsolidationWiring(TransactionCase):
    """One Sort dropdown replaces the 3 clickable headers + 2 toggles; the
    reading pane groups actions left (reply buttons) / right (star +
    overflow); the tree header is [⚙ settings][+ folder][⋯ mailbox menu];
    the bulk bar buttons are icon-only with tooltips.  The CONTROLLER sort
    contract is untouched (test_list_sorting keeps passing)."""

    def test_folder_tree_header_has_settings_button(self):
        xml = _read("static/src/xml/folder_tree.xml")
        for needle in (
            "o_webmail_settings_btn",
            "fa-cog",
            "openSettings",
        ):
            self.assertIn(needle, xml)
        # The gear delegates through the component to the parent prop.
        client_xml = _read("static/src/xml/webmail_client.xml")
        self.assertIn("onOpenSettings.bind", client_xml)
        js = _read("static/src/components/folder_tree.js")
        self.assertIn("onOpenSettings", js)

    def test_folder_tree_standalone_refresh_button_removed(self):
        """The standalone Refresh button is consolidated into the ⋯ menu
        (Refresh all) — [⚙][+][⋯] is the whole header."""
        xml = _read("static/src/xml/folder_tree.xml")
        self.assertNotIn("o_webmail_refresh_btn", xml)

    def test_message_list_has_one_sort_menu(self):
        xml = _read("static/src/xml/message_list.xml")
        for needle in (
            "o_webmail_sort_menu_btn",
            "o_webmail_sort_menu",
            "o_webmail_sort_menu_item",
            "applySort",
            "togglePriority",
            'data-priority="unread_first"',
            'data-priority="flagged_first"',
        ):
            self.assertIn(needle, xml)

    def test_message_list_clickable_headers_removed(self):
        xml = _read("static/src/xml/message_list.xml")
        for gone in (
            "o_webmail_sort_header",
            "o_webmail_priority_toggle",
        ):
            self.assertNotIn(gone, xml)

    def test_sort_menu_labels_translatable(self):
        js = _read("static/src/components/message_list.js")
        for needle in (
            '_t("Sort")',
            '_t("Date (newest first)")',
            '_t("Date (oldest first)")',
            '_t("Unread first")',
            '_t("Starred first")',
        ):
            self.assertIn(needle, js)

    def test_client_wires_absolute_sort(self):
        js = _read("static/src/webmail_client.js")
        self.assertIn("setSort(", js)
        xml = _read("static/src/xml/webmail_client.xml")
        self.assertIn("onSetSort.bind", xml)

    def test_reading_pane_has_overflow_menu(self):
        xml = _read("static/src/xml/reading_pane.xml")
        for needle in (
            "o_webmail_msg_menu_btn",
            "o_webmail_msg_menu",
            "o_webmail_mark_unread",
            "o_webmail_msg_menu_move",
            "o_webmail_msg_delete",
            "o_webmail_msg_menu_show_images",
        ):
            self.assertIn(needle, xml)
        # The standalone move select + delete button are gone (menu now).
        self.assertNotIn("o_webmail_move_select", xml)

    def test_bulk_bar_icon_only_with_tooltips(self):
        xml = _read("static/src/xml/message_list.xml")
        for cls in (
            "o_webmail_bulk_read",
            "o_webmail_bulk_unread",
            "o_webmail_bulk_star",
            "o_webmail_bulk_unstar",
            "o_webmail_bulk_delete",
        ):
            self.assertIn(cls, xml)
            # Icon-only: the button carries a title tooltip and no visible
            # text span anymore.
            btn = xml[xml.index(cls) : xml.index(cls) + 400]
            self.assertIn("t-att-title", btn)

    def test_settings_dialog_component(self):
        xml = _read("static/src/xml/settings_dialog.xml")
        for needle in (
            "o_webmail_settings_dialog",
            "o_webmail_settings_remote_images",
            "o_webmail_settings_signature_position",
            "o_webmail_settings_signatures",
            "o_webmail_settings_save",
            't-set-slot="footer"',
        ):
            self.assertIn(needle, xml)
        js = _read("static/src/components/settings_dialog.js")
        for needle in (
            '_t("Settings")',
            '_t("Below the quote")',
            '_t("Above the quote")',
            "onSave",
            "onOpenSignatures",
        ):
            self.assertIn(needle, js)

    def test_settings_dialog_assets_declared(self):
        backend = _read_manifest()["assets"]["web.assets_backend"]
        for asset in (
            "mail_management/static/src/components/settings_dialog.js",
            "mail_management/static/src/xml/settings_dialog.xml",
        ):
            self.assertIn(asset, backend)

    def test_manage_signatures_opens_existing_action(self):
        js = _read("static/src/webmail_client.js")
        self.assertIn('"mail_management.action_mail_management_signature"', js)

    def test_settings_loaded_on_start_and_saved(self):
        js = _read("static/src/webmail_client.js")
        for needle in (
            '"/mail_management/settings"',
            '"/mail_management/settings/save"',
            "loadSettings",
            "openSettings",
        ):
            self.assertIn(needle, js)

    def test_webmail_ui_extended_needles(self):
        """The PR-2e needles extend test_webmail_ui's manifest asset guard."""
        backend = _read_manifest()["assets"]["web.assets_backend"]
        self.assertIn("mail_management/static/src/utils/prepare_body.js", backend)

    def test_manifest_version_bumped(self):
        self.assertEqual(_read_manifest()["version"], "18.0.17.0.0")


# ===========================================================================
# TRIANGULATE (PR-2e) — persistence edges, no-op saves, reveal composition,
# above-position replacement with an image signature
# ===========================================================================


@tagged("standard", "at_install")
class TestSettingsTriangulate(TransactionCase):
    """Edge cases beyond the happy path: idempotent saves, no-op saves,
    value flip-flops, fresh controller instances, and the source-level
    composition of the per-message reveal with the global setting."""

    def setUp(self):
        super().setUp()
        self.user_a, self.user_b, self.manager = _users(self.env)
        self.env_a = self.env(user=self.user_a)
        self.controller = _controller()

    def test_save_same_values_is_idempotent(self):
        with _mock_request(self.env_a):
            self.controller.save_settings(
                allow_remote_images=True, signature_position="above"
            )
            result = self.controller.save_settings(
                allow_remote_images=True, signature_position="above"
            )
        self.assertTrue(result["allow_remote_images"])
        self.assertEqual(self.env["mail.management.settings"].search_count([]), 1)

    def test_save_noop_keeps_values(self):
        """Both keys omitted → a no-op save returning the current values."""
        with _mock_request(self.env_a):
            self.controller.save_settings(
                allow_remote_images=True, signature_position="above"
            )
            result = self.controller.save_settings()
        self.assertTrue(result["allow_remote_images"])
        self.assertEqual(result["signature_position"], "above")

    def test_position_flips_back(self):
        with _mock_request(self.env_a):
            self.controller.save_settings(signature_position="above")
            self.controller.save_settings(signature_position="below")
            payload = self.controller.get_settings()
        self.assertEqual(payload["signature_position"], "below")

    def test_explicit_false_save(self):
        with _mock_request(self.env_a):
            self.controller.save_settings(allow_remote_images=True)
            result = self.controller.save_settings(allow_remote_images=False)
        self.assertFalse(result["allow_remote_images"])

    def test_fresh_controller_sees_saved_state(self):
        """The settings persist in the DB — a brand-new controller instance
        (next RPC) reads back what was saved."""
        with _mock_request(self.env_a):
            self.controller.save_settings(
                allow_remote_images=True, signature_position="above"
            )
        fresh = _controller()
        with _mock_request(self.env_a):
            payload = fresh.get_settings()
        self.assertTrue(payload["allow_remote_images"])
        self.assertEqual(payload["signature_position"], "above")

    def test_third_user_fully_independent(self):
        user_c = self.env["res.users"].create(
            {
                "name": "Settings C",
                "login": "settings_c@test",
                "groups_id": [
                    (
                        6,
                        0,
                        [self.env.ref("mail_management.group_mail_management_user").id],
                    )
                ],
            }
        )
        env_c = self.env(user=user_c)
        with _mock_request(self.env_a):
            self.controller.save_settings(signature_position="above")
        with _mock_request(env_c):
            payload = self.controller.get_settings()
        self.assertEqual(payload["signature_position"], "below")  # own default

    # -- source-level: reveal composes with the setting, never leaks ----------

    def test_reveal_or_setting_composition(self):
        """The effective allow-check is setting OR revealed-for-THIS-message:
        a revealed message keeps rendering images even when the setting is
        OFF, and other messages stay blocked."""
        js = _read("static/src/webmail_client.js")
        self.assertRegex(
            js,
            r"allowRemoteImages\s*\|\|\s*\n?\s*this\.state\.revealedImageIds\.includes\(messageId\)",
        )

    def test_reveal_only_adds_the_open_message_id(self):
        """showImages appends exactly the OPEN message id (spread + push) —
        never touches (nor clears) other revealed ids."""
        js = _read("static/src/webmail_client.js")
        start = js.index("showImages() {")
        end = js.index("_reprepareOpenBody() {")
        show = js[start:end]
        self.assertIn("...this.state.revealedImageIds", show)
        self.assertIn("message.id", show)
        self.assertNotIn("revealedImageIds = []", show)

    def test_reprepare_uses_effective_allow_per_message(self):
        """The body preparation asks the PER-MESSAGE allow-check, not the
        bare setting (so a revealed message survives a settings save
        unchanged) — both openMessage and _reprepareOpenBody go through the
        shared _buildBodyMarkup."""
        js = _read("static/src/webmail_client.js")
        start = js.index("_buildBodyMarkup(rawBodyHtml, attachments, messageId) {")
        # PR-2f widened the helper (strip-styles + quote-collapse run
        # before prepareBody) — the window covers the whole def now.
        build = js[start : start + 800]
        self.assertIn("_remoteImagesAllowedFor(messageId)", build)
        # Both call sites route through the shared helper (single source of
        # truth for the display policy).
        self.assertEqual(js.count("this._buildBodyMarkup("), 2)

    def test_above_replacement_targets_blockquote_only(self):
        """Switching a signature in 'above' mode inserts before the QUOTE;
        'below' keeps appending at the end — both branches present."""
        js = _read("static/src/components/compose_dialog.js")
        replace = js[
            js.index("_replaceSignature") : js.index(
                "// ------------------------------------------------------------------",
                js.index("_replaceSignature"),
            )
        ]
        self.assertIn("insertBefore(parsed, quote)", replace)
        self.assertIn("editable.appendChild(parsed)", replace)

    def test_image_signature_allowed_in_above_mode(self):
        """A signature containing <img> sources is placed the same way (the
        pure helper only orders strings — image content flows through the
        same path; the server sanitizes signature html on write)."""
        js = _read("static/src/components/compose_dialog.js")
        helper = js[
            js.index("export function composeBodyHtml") : js.index("export class")
        ]
        self.assertIn("signatureHtml + quoteHtml", helper)
        self.assertIn("quoteHtml + signatureHtml", helper)


# ===========================================================================
# es_CL: the NEW Python-side term of PR-2e must translate
# ===========================================================================


@tagged("standard", "at_install")
class TestSettingsTermTranslatedEsCl(TransactionCase):
    def test_invalid_signature_position_translated_es_cl(self):
        self.env["res.lang"]._activate_lang("es_CL")
        env = self.env["mail.management.settings"].with_context(lang="es_CL").env
        self.assertEqual(
            env._("Invalid signature position."),
            "Posición de firma no válida.",
        )
