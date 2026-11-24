"""Strict-TDD RED tests for PR-2f — settings overhaul part 1: message list +
message view settings (SnappyMail parity).

Contract under test (parent PR-2f scope + tasks.md):

Model ``mail.management.settings`` (EXTENDED, singleton + isolation unchanged):

* ``messages_per_page`` Integer default 50, CLAMPED to 10..200 on save;
* ``default_sort`` Char default 'date_desc', validated against the EXISTING
  controller sort whitelist keys INCLUDING the priority suffix keys
  (``date_desc+unread_first`` style) — invalid values are IGNORED on model
  write and REJECTED (UserError) by the controller save;
* ``show_checkboxes`` Boolean default True;
* ``mark_read_after_seconds`` Integer default 0 (0 = mark instantly on open,
  N = deferred timer, -1 = NEVER auto-mark; documented in the help text);
* ``collapse_quoted_text`` Boolean default True;
* ``max_quote_levels`` Integer default 3, CLAMPED to 1..10 on save;
* ``strip_message_styles`` Boolean default False;
* ``prefer_html`` Boolean default True;
* ``simple_attachment_list`` Boolean default False;
* ``show_next_on_move_delete`` Boolean default True;
* ``image_whitelist`` Text — one sender email per line, NORMALIZED on save
  (lowercase, trimmed, blank + invalid lines ignored, duplicates collapsed);
* ``_image_sender_allowed(email)`` — exact match, case-insensitive (accepts
  ``Name <addr>`` display forms);
* ``_get_sort_tuple()`` — validated (sort key, priority list) default sort.

Controller:

* ``POST /mail_management/settings/save`` accepts every new field with the
  same validation/clamps; ``GET /mail_management/settings`` returns them
  (``image_whitelist`` parsed to a list);
* ``POST /mail_management/messages`` honors ``messages_per_page`` from the
  caller's settings WHEN the request passes no explicit ``limit``.

Client policy: the prepare_body pipeline gains DOM-free PURE helpers
(``static/src/utils/message_policy.js`` — node-runnable, zero imports) for
strip-styles, prefer-html, quote collapsing, whitelist matching, next-message
selection and the mark-read delay semantics; ``prepareBody``/the client
pipeline consume them; the SettingsDialog becomes TABBED (General / Message
list / Message view / Accounts & signatures).
"""

import ast
import os

import psycopg2
from odoo.addons.mail_management.tests.test_compose_send import (  # type: ignore
    _mock_request,
)
from odoo.exceptions import UserError  # type: ignore
from odoo.modules.module import get_module_path  # type: ignore
from odoo.tests.common import TransactionCase, tagged  # type: ignore


def _module_file(*parts):
    path = get_module_path("mail_management")
    return os.path.join(path, *parts)


def _read(relative):
    with open(_module_file(relative), encoding="utf-8") as fh:
        return fh.read()


def _read_manifest():
    path = _module_file("__manifest__.py")
    with open(path, encoding="utf-8") as fh:
        source = "\n".join(
            ln for ln in fh.read().splitlines() if not ln.lstrip().startswith("#")
        )
    return ast.literal_eval(source)


def _users(env):
    """Two plain webmail users (A: owner, B: stranger)."""
    group_user = env.ref("mail_management.group_mail_management_user")
    user_a = env["res.users"].create(
        {
            "name": "View Settings A",
            "login": "view_settings_a@test",
            "group_ids": [(6, 0, [group_user.id])],
        }
    )
    user_b = env["res.users"].create(
        {
            "name": "View Settings B",
            "login": "view_settings_b@test",
            "group_ids": [(6, 0, [group_user.id])],
        }
    )
    return user_a, user_b


def _controller():
    from odoo.addons.mail_management.controllers.main import (  # type: ignore
        MailManagementController,
    )

    return MailManagementController()


def _seed_folder(env, user, count=4):
    """One mailbox/folder with ``count`` messages for the limit tests."""
    mailbox = (
        env["mail.management.mailbox"]
        .with_user(user)
        .create(
            {
                "name": "Page Test",
                "provider_type": "generic",
                "imap_host": "imap.page.com",
                "imap_user": "me@page.com",
                "smtp_host": "smtp.page.com",
                "from_email": "me@page.com",
                "credential": "page-pass",
                "state": "active",
            }
        )
    )
    folder = env["mail.management.folder"].create(
        {
            "mailbox_id": mailbox.id,
            "imap_name": "INBOX",
            "name": "Inbox",
            "role": "inbox",
        }
    )
    from odoo import fields  # type: ignore

    for uid in range(1, count + 1):
        env["mail.management.message"].create(
            {
                "mailbox_id": mailbox.id,
                "folder_id": folder.id,
                "uid": uid,
                "message_id": f"<p{uid}@page.com>",
                "subject": f"Msg {uid}",
                "email_from": f"from{uid}@page.com",
                "date": fields.Datetime.to_datetime(f"2024-01-01 08:{uid % 60:02d}:00"),
                "flag_seen": True,
            }
        )
    return folder


# ===========================================================================
# Model: field defaults, clamps, normalization helpers
# ===========================================================================


@tagged("standard", "at_install")
class TestViewSettingsModel(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user_a, self.user_b = _users(self.env)
        self.Settings = self.env["mail.management.settings"]
        self.settings = self.Settings.create({"user_id": self.user_a.id})

    def test_field_defaults(self):
        self.assertEqual(self.settings.messages_per_page, 50)
        self.assertEqual(self.settings.default_sort, "date_desc")
        self.assertTrue(self.settings.show_checkboxes)
        self.assertEqual(self.settings.mark_read_after_seconds, 0)
        self.assertTrue(self.settings.collapse_quoted_text)
        self.assertEqual(self.settings.max_quote_levels, 3)
        self.assertFalse(self.settings.strip_message_styles)
        self.assertTrue(self.settings.prefer_html)
        self.assertFalse(self.settings.simple_attachment_list)
        self.assertTrue(self.settings.show_next_on_move_delete)
        self.assertEqual(self.settings.image_whitelist, "")

    def test_messages_per_page_clamped_low(self):
        self.settings.write({"messages_per_page": 5})
        self.assertEqual(self.settings.messages_per_page, 10)

    def test_messages_per_page_clamped_high(self):
        self.settings.write({"messages_per_page": 999})
        self.assertEqual(self.settings.messages_per_page, 200)

    def test_messages_per_page_valid_untouched(self):
        self.settings.write({"messages_per_page": 25})
        self.assertEqual(self.settings.messages_per_page, 25)

    def test_max_quote_levels_clamped_low(self):
        self.settings.write({"max_quote_levels": 0})
        self.assertEqual(self.settings.max_quote_levels, 1)

    def test_max_quote_levels_clamped_high(self):
        self.settings.write({"max_quote_levels": 99})
        self.assertEqual(self.settings.max_quote_levels, 10)

    def test_mark_read_seconds_floor(self):
        """Below -1 (the NEVER sentinel) clamps to -1, never lower."""
        self.settings.write({"mark_read_after_seconds": -7})
        self.assertEqual(self.settings.mark_read_after_seconds, -1)

    def test_mark_read_semantics_constants(self):
        """The three semantics are named constants (0 = instant, -1 = never)."""
        from odoo.addons.mail_management.models.mail_management_settings import (  # type: ignore[attr-defined]
            MARK_READ_INSTANT,
            MARK_READ_NEVER,
        )

        self.assertEqual(MARK_READ_INSTANT, 0)
        self.assertEqual(MARK_READ_NEVER, -1)
        # The field default stays in sync with the instant constant (Odoo
        # wraps plain defaults in a callable — resolve it).
        default = self.Settings._fields["mark_read_after_seconds"].default
        if callable(default):
            default = default(self.Settings)
        self.assertEqual(default, MARK_READ_INSTANT)

    def test_mark_read_help_documents_semantics(self):
        help_text = self.Settings._fields["mark_read_after_seconds"].help or ""
        self.assertIn("0", help_text)
        self.assertIn("-1", help_text)

    def test_default_sort_invalid_ignored_on_write(self):
        """Model writes IGNORE an invalid default_sort (never stored)."""
        self.settings.write({"default_sort": "date; drop table messages"})
        self.assertEqual(self.settings.default_sort, "date_desc")

    def test_default_sort_valid_keys_accepted(self):
        for value in ("from_desc", "subject", "date_desc+unread_first"):
            with self.subTest(value=value):
                self.settings.write({"default_sort": value})
                self.assertEqual(self.settings.default_sort, value)

    def test_sort_whitelist_keys_match_controller(self):
        """The valid-key set covers the EXISTING controller sort whitelist
        keys INCLUDING the priority suffix keys."""
        keys = self.Settings._default_sort_keys()
        for key in (
            "date",
            "date_desc",
            "from",
            "from_desc",
            "subject",
            "subject_desc",
            "unread_first",
            "flagged_first",
        ):
            self.assertIn(key, keys)

    def test_is_valid_default_sort(self):
        self.assertTrue(self.Settings._is_valid_default_sort("date_desc"))
        self.assertTrue(self.Settings._is_valid_default_sort("date_desc+flagged_first"))
        self.assertFalse(self.Settings._is_valid_default_sort("date_desc+nope"))
        self.assertFalse(self.Settings._is_valid_default_sort("evil; drop"))
        self.assertFalse(self.Settings._is_valid_default_sort("a+b+c"))

    def test_parse_sort_tuple(self):
        """Pure parser: sort key + optional single priority suffix."""
        self.assertEqual(
            self.Settings._parse_sort_tuple("from+flagged_first"),
            ("from", ["flagged_first"]),
        )
        self.assertEqual(
            self.Settings._parse_sort_tuple("subject_desc"), ("subject_desc", [])
        )
        # Garbage stored by hand (or legacy) falls back to the safe default.
        self.assertEqual(
            self.Settings._parse_sort_tuple("garbage here"),
            ("date_desc", []),
        )
        self.assertEqual(self.Settings._parse_sort_tuple(""), ("date_desc", []))

    def test_get_sort_tuple_on_record(self):
        self.settings.write({"default_sort": "date+unread_first"})
        self.assertEqual(self.settings._get_sort_tuple(), ("date", ["unread_first"]))

    def test_image_whitelist_normalized_on_write(self):
        """Lowercase + trimmed; blank and INVALID lines ignored; duplicates
        collapsed; order preserved."""
        self.settings.write(
            {
                "image_whitelist": (
                    "  Alice@X.com \n"
                    "\n"
                    "bob@example.com\n"
                    "not an email\n"
                    "  CAROL@Y.CL  \n"
                    "ALICE@X.COM\n"
                )
            }
        )
        self.assertEqual(
            self.settings.image_whitelist,
            "alice@x.com\nbob@example.com\ncarol@y.cl",
        )

    def test_image_whitelist_blank_write(self):
        self.settings.write({"image_whitelist": " \n \n"})
        self.assertEqual(self.settings.image_whitelist, "")

    def test_image_sender_allowed_exact_case_insensitive(self):
        self.settings.write({"image_whitelist": "alice@x.com"})
        self.assertTrue(self.settings._image_sender_allowed("Alice@X.COM"))
        self.assertTrue(self.settings._image_sender_allowed("alice@x.com"))
        # Exact match only — never a substring/domain match.
        self.assertFalse(self.settings._image_sender_allowed("mallory@evil.com"))
        self.assertFalse(self.settings._image_sender_allowed("x.com"))
        self.assertFalse(self.settings._image_sender_allowed(""))

    def test_image_sender_allowed_display_name_form(self):
        """``email_from`` may be 'Name <addr>' — the address is what counts."""
        self.settings.write({"image_whitelist": "alice@x.com"})
        self.assertTrue(self.settings._image_sender_allowed("Bob Sender <Alice@X.com>"))
        self.assertFalse(self.settings._image_sender_allowed("Bob <mallory@evil.com>"))

    def test_create_normalizes_too(self):
        settings = self.Settings.create(
            {
                "user_id": self.user_b.id,
                "messages_per_page": 999,
                "max_quote_levels": 0,
                "image_whitelist": "A@B.com\njunk",
            }
        )
        self.assertEqual(settings.messages_per_page, 200)
        self.assertEqual(settings.max_quote_levels, 1)
        self.assertEqual(settings.image_whitelist, "a@b.com")

    def test_singleton_constraint_unchanged(self):
        with self.assertRaises(psycopg2.IntegrityError):
            self.Settings.create({"user_id": self.user_a.id})


# ===========================================================================
# Controller: save clamps/validation + GET payload + /messages page size
# ===========================================================================


@tagged("standard", "at_install")
class TestViewSettingsController(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user_a, self.user_b = _users(self.env)
        self.env_a = self.env(user=self.user_a)
        self.env_b = self.env(user=self.user_b)
        self.controller = _controller()

    def test_get_returns_new_defaults(self):
        with _mock_request(self.env_a):
            payload = self.controller.get_settings()
        self.assertEqual(payload["messages_per_page"], 50)
        self.assertEqual(payload["default_sort"], "date_desc")
        self.assertTrue(payload["show_checkboxes"])
        self.assertEqual(payload["mark_read_after_seconds"], 0)
        self.assertTrue(payload["collapse_quoted_text"])
        self.assertEqual(payload["max_quote_levels"], 3)
        self.assertFalse(payload["strip_message_styles"])
        self.assertTrue(payload["prefer_html"])
        self.assertFalse(payload["simple_attachment_list"])
        self.assertTrue(payload["show_next_on_move_delete"])
        self.assertEqual(payload["image_whitelist"], [])

    def test_save_round_trip_new_fields(self):
        with _mock_request(self.env_a):
            result = self.controller.save_settings(
                messages_per_page=25,
                default_sort="from_desc",
                show_checkboxes=False,
                mark_read_after_seconds=5,
                collapse_quoted_text=False,
                max_quote_levels=5,
                strip_message_styles=True,
                prefer_html=False,
                simple_attachment_list=True,
                show_next_on_move_delete=False,
                image_whitelist="Friend@Example.com",
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["messages_per_page"], 25)
        self.assertEqual(result["default_sort"], "from_desc")
        self.assertFalse(result["show_checkboxes"])
        self.assertEqual(result["mark_read_after_seconds"], 5)
        self.assertFalse(result["collapse_quoted_text"])
        self.assertEqual(result["max_quote_levels"], 5)
        self.assertTrue(result["strip_message_styles"])
        self.assertFalse(result["prefer_html"])
        self.assertTrue(result["simple_attachment_list"])
        self.assertFalse(result["show_next_on_move_delete"])
        self.assertEqual(result["image_whitelist"], ["friend@example.com"])

    def test_save_clamps_page_size(self):
        with _mock_request(self.env_a):
            result = self.controller.save_settings(messages_per_page=5)
            self.assertEqual(result["messages_per_page"], 10)
            result = self.controller.save_settings(messages_per_page=999)
            self.assertEqual(result["messages_per_page"], 200)

    def test_save_clamps_quote_levels(self):
        with _mock_request(self.env_a):
            result = self.controller.save_settings(max_quote_levels=0)
            self.assertEqual(result["max_quote_levels"], 1)

    def test_save_mark_read_floor(self):
        with _mock_request(self.env_a):
            result = self.controller.save_settings(mark_read_after_seconds=-9)
        self.assertEqual(result["mark_read_after_seconds"], -1)

    def test_save_invalid_default_sort_rejected(self):
        with _mock_request(self.env_a), self.assertRaises(UserError):
            self.controller.save_settings(default_sort="date; drop")

    def test_save_default_sort_priority_suffix_accepted(self):
        with _mock_request(self.env_a):
            result = self.controller.save_settings(
                default_sort="date_desc+flagged_first"
            )
            payload = self.controller.get_settings()
        self.assertEqual(result["default_sort"], "date_desc+flagged_first")
        self.assertEqual(payload["default_sort"], "date_desc+flagged_first")

    def test_save_image_whitelist_normalized_and_parsed(self):
        with _mock_request(self.env_a):
            self.controller.save_settings(
                image_whitelist="  A@B.cl \nnot-an-email\nC@D.io\na@b.cl"
            )
            payload = self.controller.get_settings()
        self.assertEqual(payload["image_whitelist"], ["a@b.cl", "c@d.io"])

    def test_save_partial_keeps_others(self):
        with _mock_request(self.env_a):
            self.controller.save_settings(messages_per_page=30, prefer_html=False)
            result = self.controller.save_settings(show_checkboxes=False)
        self.assertEqual(result["messages_per_page"], 30)
        self.assertFalse(result["prefer_html"])

    def test_messages_limit_from_settings(self):
        """//messages with NO explicit limit uses the caller's page size."""
        folder = _seed_folder(self.env, self.user_a, count=12)
        with _mock_request(self.env_a):
            self.controller.save_settings(messages_per_page=10)
            rows = self.controller.get_messages(folder_id=folder.id)
        self.assertEqual(len(rows), 10)

    def test_messages_explicit_limit_wins(self):
        folder = _seed_folder(self.env, self.user_a, count=12)
        with _mock_request(self.env_a):
            self.controller.save_settings(messages_per_page=10)
            rows = self.controller.get_messages(folder_id=folder.id, limit=3)
        self.assertEqual(len(rows), 3)

    def test_messages_no_settings_row_falls_back(self):
        """No settings row yet → the historical default of 50 applies."""
        folder = _seed_folder(self.env, self.user_a, count=12)
        with _mock_request(self.env_a):
            rows = self.controller.get_messages(folder_id=folder.id)
        self.assertEqual(len(rows), 12)  # fewer than 50 exist

    def test_messages_other_users_settings_isolated(self):
        """B's page size never limits A's list (per-user settings)."""
        folder = _seed_folder(self.env, self.user_a, count=12)
        with _mock_request(self.env_b):
            self.controller.save_settings(messages_per_page=10)
        with _mock_request(self.env_a):
            rows = self.controller.get_messages(folder_id=folder.id)
        self.assertEqual(len(rows), 12)

    def test_save_scoped_to_current_user_unchanged(self):
        """B's save never touches A's row (isolation pattern preserved)."""
        with _mock_request(self.env_a):
            self.controller.save_settings(messages_per_page=20, prefer_html=False)
        with _mock_request(self.env_b):
            self.controller.save_settings(messages_per_page=100, prefer_html=True)
        settings_a = self.env["mail.management.settings"].search(
            [("user_id", "=", self.user_a.id)]
        )
        self.assertEqual(settings_a.messages_per_page, 20)
        self.assertFalse(settings_a.prefer_html)


# ===========================================================================
# Client wiring: pure policy module + pipeline + settings dialog tabs
# (source assertions — the established pattern; the pure helpers themselves
# are executed for real by tests/test_message_policy.mjs under in-container
# node, reported separately)
# ===========================================================================


@tagged("standard", "at_install")
class TestMessagePolicyWiring(TransactionCase):
    def test_pure_policy_module_exists_and_dom_free(self):
        """Every new display-policy helper is a PURE, DOM-free function in a
        dedicated module (zero imports → node-runnable, no webclient)."""
        js = _read("static/src/utils/message_policy.js")
        self.assertNotIn("DOMParser", js)
        self.assertNotIn("import ", js)
        for name in (
            "stripMessageStyles",
            "collapseQuotes",
            "plainTextToHtml",
            "chooseBodyHtml",
            "extractEmailAddress",
            "senderInWhitelist",
            "nextMessageId",
            "markReadDelayMs",
        ):
            self.assertIn(f"export function {name}", js)

    def test_client_consumes_policy_module(self):
        client = _read("static/src/webmail_client.js")
        self.assertIn("@mail_management/utils/message_policy", client)
        for name in (
            "chooseBodyHtml",
            "stripMessageStyles",
            "collapseQuotes",
            "senderInWhitelist",
            "nextMessageId",
            "markReadDelayMs",
        ):
            self.assertIn(name, client)

    def test_client_body_pipeline_order(self):
        """The reading pipeline is: choose source (prefer_html) → strip
        styles → collapse quotes → prepareBody (images + cid)."""
        js = _read("static/src/webmail_client.js")
        for name in (
            "chooseBodyHtml(",
            "stripMessageStyles(",
            "collapseQuotes(",
            "prepareBody(",
        ):
            self.assertIn(name, js)
        def_start = js.index("_buildBodyMarkup(rawBodyHtml, attachments, messageId) {")
        build = js[def_start : def_start + 900]
        self.assertIn("stripMessageStyles(", build)
        self.assertIn("collapseQuotes(", build)
        self.assertIn("prepareBody(", build)
        self.assertLess(build.index("stripMessageStyles("), build.index("prepareBody("))
        self.assertLess(build.index("collapseQuotes("), build.index("prepareBody("))
        # The source choice happens BEFORE the pipeline — both on open and
        # on live re-prepare (a prefer_html toggle re-chooses in place).
        open_msg = js[js.index("async openMessage(") : js.index("_decrementUnread")]
        self.assertIn("chooseBodyHtml(", open_msg)
        self.assertIn("sourceHtml", open_msg)
        reprepare = js[
            js.index("_reprepareOpenBody() {") : js.index("_reprepareOpenBody() {")
            + 500
        ]
        self.assertIn("chooseBodyHtml(", reprepare)

    def test_client_remote_allow_includes_whitelist(self):
        """Effective remote-image allow = setting OR per-message reveal OR
        sender whitelisted (checked on the OPEN message sender)."""
        js = _read("static/src/webmail_client.js")
        start = js.index("_remoteImagesAllowedFor(")
        check = js[start : start + 500]
        self.assertIn("allowRemoteImages", check)
        self.assertIn("revealedImageIds", check)
        self.assertIn("senderInWhitelist", check)
        self.assertIn("email_from", check)

    def test_client_page_size_from_settings(self):
        """The list RPC no longer hardcodes limit=50 — the caller's page
        size (settings-driven server default) applies."""
        js = _read("static/src/webmail_client.js")
        self.assertNotIn("limit: 50", js)
        self.assertIn("messagesPerPage", js)

    def test_client_default_sort_applied_on_folder_open(self):
        js = _read("static/src/webmail_client.js")
        for needle in (
            "defaultSort",
            "sortTouched",
            "_applyDefaultSort",
        ):
            self.assertIn(needle, js)
        # The folder-open path re-applies the default until the user changes
        # the sort for the session.
        select = js[js.index("async selectFolder(") : js.index("_parseDefaultSort(")]
        self.assertIn("_applyDefaultSort", select)

    def test_client_mark_read_timer_wiring(self):
        js = _read("static/src/webmail_client.js")
        for needle in (
            "markReadDelayMs",
            "_scheduleMarkRead",
            "_cancelMarkReadTimer",
            "setTimeout",
            "clearTimeout",
        ):
            self.assertIn(needle, js)
        # Navigating away cancels a pending deferred mark-read.
        open_msg = js[js.index("async openMessage(") : js.index("_decrementUnread")]
        self.assertIn("_cancelMarkReadTimer", open_msg)

    def test_client_next_on_move_delete_wiring(self):
        js = _read("static/src/webmail_client.js")
        for needle in (
            "showNextOnMoveDelete",
            "nextMessageId",
            "_selectAfterRemoval",
        ):
            self.assertIn(needle, js)

    def test_message_list_checkboxes_hidden_when_disabled(self):
        js = _read("static/src/components/message_list.js")
        self.assertIn("showCheckboxes", js)
        xml = _read("static/src/xml/message_list.xml")
        # The select-all checkbox, the per-row checkbox AND the bulk bar all
        # honor the setting.
        self.assertEqual(xml.count("showCheckboxes"), 3)
        self.assertNotIn('t-if="selectedCount"', xml)  # now gated by the prop

    def test_reading_pane_simple_attachment_list(self):
        js = _read("static/src/components/reading_pane.js")
        self.assertIn("simpleAttachmentList", js)
        xml = _read("static/src/xml/reading_pane.xml")
        self.assertIn("o_webmail_attachments_simple", xml)

    def test_settings_dialog_tabbed(self):
        xml = _read("static/src/xml/settings_dialog.xml")
        # The tab strip renders from the `tabs` getter (single source of
        # truth) — data-tab is the dynamic binding; each PANE carries a
        # literal data-pane anchor for tests/tours.
        for needle in (
            "o_webmail_settings_tab",
            't-att-data-tab="tab.id"',
            'data-pane="general"',
            'data-pane="message_list"',
            'data-pane="message_view"',
            'data-pane="accounts"',
        ):
            self.assertIn(needle, xml)

    def test_settings_dialog_new_controls(self):
        xml = _read("static/src/xml/settings_dialog.xml")
        for needle in (
            "o_webmail_settings_messages_per_page",
            "o_webmail_settings_default_sort",
            "o_webmail_settings_priority",
            "o_webmail_settings_show_checkboxes",
            "o_webmail_settings_mark_read",
            "o_webmail_settings_collapse_quotes",
            "o_webmail_settings_max_quote_levels",
            "o_webmail_settings_strip_styles",
            "o_webmail_settings_prefer_html",
            "o_webmail_settings_simple_attachments",
            "o_webmail_settings_next_on_delete",
            "o_webmail_settings_image_whitelist",
        ):
            self.assertIn(needle, xml)

    def test_settings_dialog_translatable_labels(self):
        js = _read("static/src/components/settings_dialog.js")
        for needle in (
            '_t("General")',
            '_t("Message list")',
            '_t("Message view")',
            '_t("Accounts & signatures")',
            '_t("Messages per page")',
            '_t("Default sort")',
            '_t("Show checkboxes")',
            '_t("Collapse quoted text")',
            '_t("Prefer HTML over plain text")',
        ):
            self.assertIn(needle, js)

    def test_quote_labels_translatable(self):
        """The collapsible-quote summary labels go through _t (they are
        injected into markup by the pure helper's parameters)."""
        js = _read("static/src/webmail_client.js")
        self.assertIn('_t("Show quoted text")', js)
        self.assertIn('_t("Show more quote")', js)

    def test_manifest_assets_declared(self):
        backend = _read_manifest()["assets"]["web.assets_backend"]
        self.assertIn("mail_management/static/src/utils/message_policy.js", backend)

    def test_manifest_version_bumped(self):
        self.assertEqual(_read_manifest()["version"], "20.0.17.0.0")


# ===========================================================================
# TRIANGULATE (PR-2f) — combined saves, suffix edges, per-user isolation of
# the page-size fallback, and the JS-behavior edges that the node harness
# covers for real (tests/test_message_policy.mjs): timer 0/-1/5, next-at-end,
# strip+prefer_html+whitelist combined.
# ===========================================================================


@tagged("standard", "at_install")
class TestViewSettingsTriangulate(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user_a, self.user_b = _users(self.env)
        self.env_a = self.env(user=self.user_a)
        self.controller = _controller()

    def test_combined_save_strip_prefer_html_whitelist(self):
        """All three display policies save together (the node harness then
        proves the client composition for real)."""
        with _mock_request(self.env_a):
            result = self.controller.save_settings(
                strip_message_styles=True,
                prefer_html=False,
                image_whitelist="LETTERS@Example.com",
            )
        self.assertTrue(result["strip_message_styles"])
        self.assertFalse(result["prefer_html"])
        # Case-normalized to the bare address on save.
        self.assertEqual(result["image_whitelist"], ["letters@example.com"])

    def test_sort_suffix_edges(self):
        """The optional priority suffix: both keys valid, garbage suffix
        rejected by the endpoint, and the tuple parse keeps the good part."""
        with _mock_request(self.env_a), self.assertRaises(UserError):
            self.controller.save_settings(default_sort="date_desc+nope")
        with _mock_request(self.env_a):
            self.controller.save_settings(default_sort="date+flagged_first")
        settings = self.env["mail.management.settings"].search(
            [("user_id", "=", self.user_a.id)]
        )
        self.assertEqual(settings._get_sort_tuple(), ("date", ["flagged_first"]))
        # A hand-mangled stored value still parses safely.
        self.assertEqual(settings._parse_sort_tuple("from+garbage"), ("from", []))

    def test_mark_read_valid_values_untouched(self):
        """0 / 5 / -1 are all VALID — only values below -1 clamp (node side
        proves the delay semantics 0/-1/5 for real)."""
        for value in (0, 5, -1):
            with self.subTest(value=value), _mock_request(self.env_a):
                result = self.controller.save_settings(mark_read_after_seconds=value)
            self.assertEqual(result["mark_read_after_seconds"], value)

    def test_saved_default_sort_drives_listing(self):
        """The saved default (sort + priority) is exactly what the client
        sends on folder open — verified against the listing for real."""
        folder = _seed_folder(self.env, self.user_a, count=3)
        # Make m2 the only UNREAD so unread_first must lift it to the top.
        messages = self.env["mail.management.message"].search(
            [("folder_id", "=", folder.id)]
        )
        for m in messages:
            m.flag_seen = m.uid != 2
        with _mock_request(self.env_a):
            self.controller.save_settings(default_sort="date+unread_first")
            settings = self.env["mail.management.settings"].search(
                [("user_id", "=", self.user_a.id)]
            )
            sort, priority = settings._get_sort_tuple()
            rows = self.controller.get_messages(
                folder_id=folder.id, sort=sort, priority=priority
            )
        self.assertEqual(rows[0]["uid"], 2)

    def test_sort_touch_marks_session_override(self):
        """Both user sort actions mark the session override so the saved
        default stops re-applying on folder open."""
        js = _read("static/src/webmail_client.js")
        for method in ("async setSort(", "async togglePriority("):
            body = js[js.index(method) : js.index(method) + 220]
            self.assertIn("this.sortTouched = true", body)

    def test_whitelist_save_dedupes_and_keeps_order(self):
        with _mock_request(self.env_a):
            self.controller.save_settings(
                image_whitelist="b@x.io\nA@X.io\na@x.io\nB@X.IO"
            )
            payload = self.controller.get_settings()
        self.assertEqual(payload["image_whitelist"], ["b@x.io", "a@x.io"])


# ===========================================================================
# es_CL: the NEW Python-side term of PR-2f must translate
# ===========================================================================


@tagged("standard", "at_install")
class TestViewSettingsTermTranslatedEsCl(TransactionCase):
    def test_invalid_default_sort_translated_es_cl(self):
        self.env["res.lang"]._activate_lang("es_CL")
        env = self.env["mail.management.settings"].with_context(lang="es_CL").env
        self.assertEqual(
            env._("Invalid default sort."),
            "Orden de clasificación predeterminada no válida.",
        )
