"""Strict-TDD RED tests for PR-2g — compose settings + general/theme.

Contract under test (parent PR-2g scope C + D):

``mail.management.settings`` gains:

* ``compose_editor`` Selection html|plain default 'html';
* ``reply_save_folder`` Selection sent|same default 'sent';
* ``request_read_receipt`` Boolean default False;
* ``request_delivery_receipt`` Boolean default False;
* ``enable_spellcheck`` Boolean default True;
* ``show_unread_badge`` Boolean default True;
* ``theme`` Selection light|dark default 'light'.

Controller: both settings endpoints carry the new keys; invalid selection
values raise UserError (never stored); booleans round-trip.

``reply_save_folder='same'`` (SnappyMail "place replies in the folder of the
message being replied to"): a REPLY APPENDs to the ORIGINAL message's folder
(+ \\Answered there, local mirror there) instead of Sent; forwards and the
default keep using Sent.

Receipts: ``request_read_receipt`` → ``Disposition-Notification-To: <from>``;
``request_delivery_receipt`` → ``Return-Receipt-To: <from>`` on the outgoing
message (asserted on the MOCKED SMTP seam); absent by default.

Plain editor: ComposeDialog renders a plain ``<textarea>`` (spellcheck attr
per ``enable_spellcheck``) instead of the Wysiwyg, builds the body as TEXT
(signature inserted as a TAGS-STRIPPED rendition) and sends ``body_text``
only (single-part), never ``body_html``.

Theme: the webmail root applies ``o_webmail_dark`` from the settings; the
SCSS defines the palette on CSS custom properties with a dark override.
"""

import os
from unittest import mock

from odoo.addons.mail_management.tests.test_compose_send import (  # type: ignore
    FakeImapClient,
    FakeSmtpClient,
    SendTestMixin,
    _mock_request,
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
    group_user = env.ref("mail_management.group_mail_management_user")
    user_a = env["res.users"].create(
        {
            "name": "Compose A",
            "login": "compose_a@test",
            "group_ids": [(6, 0, [group_user.id])],
        }
    )
    return user_a


def _controller():
    from odoo.addons.mail_management.controllers.main import (  # type: ignore
        MailManagementController,
    )

    return MailManagementController()


# ===========================================================================
# Model: new field defaults + selection values
# ===========================================================================


@tagged("standard", "at_install")
class TestComposeSettingsModel(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user = _users(self.env)
        self.Settings = self.env["mail.management.settings"]
        self.settings = self.Settings.create({"user_id": self.user.id})

    def test_defaults(self):
        self.assertEqual(self.settings.compose_editor, "html")
        self.assertEqual(self.settings.reply_save_folder, "sent")
        self.assertFalse(self.settings.request_read_receipt)
        self.assertFalse(self.settings.request_delivery_receipt)
        self.assertTrue(self.settings.enable_spellcheck)
        self.assertTrue(self.settings.show_unread_badge)
        self.assertEqual(self.settings.theme, "light")

    def test_editor_selection_values(self):
        field = self.Settings._fields["compose_editor"]
        self.assertEqual(
            [value for value, _label in field.selection], ["html", "plain"]
        )

    def test_reply_save_selection_values(self):
        field = self.Settings._fields["reply_save_folder"]
        self.assertEqual([value for value, _label in field.selection], ["sent", "same"])

    def test_theme_selection_values(self):
        field = self.Settings._fields["theme"]
        self.assertEqual(
            [value for value, _label in field.selection],
            ["light", "dark", "night", "sepia", "ocean", "forest"],
        )


# ===========================================================================
# Controller: payload + save round-trips + validation
# ===========================================================================


@tagged("standard", "at_install")
class TestComposeSettingsController(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user = _users(self.env)
        self.env_a = self.env(user=self.user)
        self.controller = _controller()

    def test_get_payload_carries_new_keys(self):
        with _mock_request(self.env_a):
            payload = self.controller.get_settings()
        self.assertEqual(payload["compose_editor"], "html")
        self.assertEqual(payload["reply_save_folder"], "sent")
        self.assertFalse(payload["request_read_receipt"])
        self.assertFalse(payload["request_delivery_receipt"])
        self.assertTrue(payload["enable_spellcheck"])
        self.assertTrue(payload["show_unread_badge"])
        self.assertEqual(payload["theme"], "light")

    def test_save_round_trip(self):
        with _mock_request(self.env_a):
            result = self.controller.save_settings(
                compose_editor="plain",
                reply_save_folder="same",
                request_read_receipt=True,
                request_delivery_receipt=True,
                enable_spellcheck=False,
                show_unread_badge=False,
                theme="dark",
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["compose_editor"], "plain")
        self.assertEqual(result["reply_save_folder"], "same")
        self.assertTrue(result["request_read_receipt"])
        self.assertTrue(result["request_delivery_receipt"])
        self.assertFalse(result["enable_spellcheck"])
        self.assertFalse(result["show_unread_badge"])
        self.assertEqual(result["theme"], "dark")

    def test_partial_save_keeps_others(self):
        with _mock_request(self.env_a):
            self.controller.save_settings(compose_editor="plain")
            result = self.controller.save_settings(theme="dark")
        self.assertEqual(result["compose_editor"], "plain")
        self.assertEqual(result["theme"], "dark")

    def test_invalid_editor_rejected(self):
        with _mock_request(self.env_a), self.assertRaises(UserError):
            self.controller.save_settings(compose_editor="rich")

    def test_invalid_reply_save_rejected(self):
        with _mock_request(self.env_a), self.assertRaises(UserError):
            self.controller.save_settings(reply_save_folder="drafts")

    def test_invalid_theme_rejected(self):
        with _mock_request(self.env_a), self.assertRaises(UserError):
            self.controller.save_settings(theme="blue")

    def test_booleans_round_trip_off(self):
        with _mock_request(self.env_a):
            self.controller.save_settings(enable_spellcheck=True)
            result = self.controller.save_settings(enable_spellcheck=False)
        self.assertFalse(result["enable_spellcheck"])


# ===========================================================================
# reply_save_folder='same': replies save next to the original message
# ===========================================================================


@tagged("standard", "at_install")
class TestReplySaveFolderSame(SendTestMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        self.setUpSend()
        self.Settings = self.env["mail.management.settings"]
        # The mailbox owner's settings (the send path reads the OWNER's row).
        self.settings = self.Settings.create({"user_id": self.mailbox.user_id.id})

    def _send(self, smtp, imap, **kw):
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, imap),
        ):
            return self._send_reply(smtp=smtp, **kw)

    def test_same_appends_to_original_folder(self):
        self.settings.reply_save_folder = "same"
        imap = FakeImapClient()
        result = self._send(FakeSmtpClient(), imap)
        self.assertEqual(imap.appends[0][1], "INBOX")
        self.assertEqual(result["message"].folder_id, self.inbox)

    def test_same_sets_answered_on_original(self):
        self.settings.reply_save_folder = "same"
        imap = FakeImapClient()
        self._send(FakeSmtpClient(), imap)
        self.assertTrue(self.original.flag_answered)
        self.assertEqual(len(imap.stores), 1)
        self.assertIn("\\Answered", imap.stores[0][1])

    def test_same_local_mirror_next_to_original(self):
        """The local sent mirror lands in the ORIGINAL folder with a temp uid
        above its watermark (same contract as the Sent path)."""
        self.settings.reply_save_folder = "same"
        imap = FakeImapClient()
        result = self._send(FakeSmtpClient(), imap)
        mirror = result["message"]
        self.assertEqual(mirror.folder_id, self.inbox)
        self.assertGreater(mirror.uid, self.original.uid)
        self.assertEqual(self.sent.message_count, 0)  # nothing went to Sent

    def test_default_still_uses_sent(self):
        imap = FakeImapClient()
        result = self._send(FakeSmtpClient(), imap)
        self.assertEqual(imap.appends[0][1], "Sent")
        self.assertEqual(result["message"].folder_id, self.sent)

    def test_same_forward_still_uses_sent(self):
        """Forwards ALWAYS use the Sent folder (only replies follow the
        original message)."""
        self.settings.reply_save_folder = "same"
        imap = FakeImapClient()
        with (
            mock.patch(SMTP_PATCH, FakeSmtpClient()),
            mock.patch(IMAP_PATCH, imap),
        ):
            self._send_reply(
                smtp=FakeSmtpClient(),
                to="friend@other.com",
                subject="Fwd: Original subject",
                forward=True,
            )
        self.assertEqual(imap.appends[0][1], "Sent")

    def test_plain_new_message_uses_sent(self):
        """'same' only applies to replies — a fresh compose (no original) is
        not redirected."""
        self.settings.reply_save_folder = "same"
        imap = FakeImapClient()
        with (
            mock.patch(SMTP_PATCH, FakeSmtpClient()),
            mock.patch(IMAP_PATCH, imap),
        ):
            self.mailbox._send_message(
                to="someone@ext.com", subject="Hi", body_text="Hello"
            )
        self.assertEqual(imap.appends[0][1], "Sent")


# ===========================================================================
# Receipt headers (mocked SMTP seam)
# ===========================================================================


@tagged("standard", "at_install")
class TestReceiptHeaders(SendTestMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        self.setUpSend()
        self.Settings = self.env["mail.management.settings"]
        self.settings = self.Settings.create({"user_id": self.mailbox.user_id.id})

    def _send(self):
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self._send_reply(smtp=smtp)
        return smtp.sent[0]

    def test_read_receipt_header_present(self):
        self.settings.request_read_receipt = True
        msg = self._send()
        self.assertEqual(msg["Disposition-Notification-To"], "me@send.com")

    def test_delivery_receipt_header_present(self):
        self.settings.request_delivery_receipt = True
        msg = self._send()
        self.assertEqual(msg["Return-Receipt-To"], "me@send.com")

    def test_receipts_absent_by_default(self):
        msg = self._send()
        self.assertIsNone(msg["Disposition-Notification-To"])
        self.assertIsNone(msg["Return-Receipt-To"])

    def test_receipt_value_is_from_email_not_user(self):
        """The header points at the mailbox From address (the sender identity
        the recipient would answer), never the Odoo login."""
        self.mailbox.from_email = "identity@send.com"
        self.settings.request_read_receipt = True
        msg = self._send()
        self.assertEqual(msg["Disposition-Notification-To"], "identity@send.com")


# ===========================================================================
# Plain editor + spellcheck + signature text rendition (source wiring)
# ===========================================================================


@tagged("standard", "at_install")
class TestPlainEditorWiring(TransactionCase):
    def test_compose_dialog_knows_editor_and_spellcheck(self):
        js = _read("static/src/components/compose_dialog.js")
        for needle in (
            "composeEditor",
            "enableSpellcheck",
            "spellcheckAttr",
            "state.bodyText",
        ):
            self.assertIn(needle, js)

    def test_plain_textarea_rendered_with_spellcheck_attr(self):
        xml = _read("static/src/xml/compose_dialog.xml")
        self.assertIn("o_webmail_compose_plain", xml)
        self.assertIn("t-att-spellcheck", xml)

    def test_signature_text_rendition_helper(self):
        """The plain editor inserts the signature with tags STRIPPED (a text
        rendition — no HTML leaks into the plain body)."""
        js = _read("static/src/components/compose_dialog.js")
        self.assertIn("htmlSignatureToText", js)

    def test_plain_mode_sends_text_only(self):
        """Plain mode: the textarea is the body; body_html stays EMPTY (the
        server keeps the single-part text path — body_text carries it)."""
        js = _read("static/src/components/compose_dialog.js")
        self.assertIn("const bodyHtml = this.isPlainEditor", js)
        self.assertIn('? ""', js)
        self.assertIn("body_html: bodyHtml,", js)
        self.assertIn("body_text: bodyText,", js)

    def test_client_passes_editor_settings_to_compose(self):
        js = _read("static/src/webmail_client.js")
        self.assertIn("composeEditor:", js)
        self.assertIn("enableSpellcheck:", js)
        self.assertIn("payload.compose_editor", js)

    def test_spellcheck_setting_plumbed(self):
        js = _read("static/src/webmail_client.js")
        self.assertIn("enableSpellcheck: payload.enable_spellcheck", js)


# ===========================================================================
# General: unread badge + theme wiring (source)
# ===========================================================================


@tagged("standard", "at_install")
class TestGeneralSettingsWiring(TransactionCase):
    def test_settings_dialog_general_tab_extended(self):
        xml = _read("static/src/xml/settings_dialog.xml")
        for needle in (
            "o_webmail_settings_compose_editor",
            "o_webmail_settings_reply_save",
            "o_webmail_settings_read_receipt",
            "o_webmail_settings_delivery_receipt",
            "o_webmail_settings_spellcheck",
            "o_webmail_settings_unread_badge",
            "o_webmail_settings_theme",
        ):
            self.assertIn(needle, xml)

    def test_theme_dark_class_applied_from_settings(self):
        """PR-2i: the ROOT template binds the themeClass getter; the JS
        maps all six theme keys to their scss root classes and whitelists
        the payload value."""
        xml = _read("static/src/xml/webmail_client.xml")
        self.assertIn('t-att-class="themeClass"', xml)
        js = _read("static/src/webmail_client.js")
        self.assertIn("get themeClass()", js)
        for key in ("dark", "night", "sepia", "ocean", "forest"):
            cls = "o_webmail_dark" if key == "dark" else f"o_webmail_theme_{key}"
            self.assertIn(cls, js)

    def test_scss_dark_theme_on_custom_properties(self):
        scss = _read("static/src/scss/webmail.scss")
        self.assertIn(".o_webmail_dark", scss)
        self.assertGreaterEqual(scss.count("--wmail-"), 8)

    def test_scss_panels_use_custom_properties(self):
        """The light palette ALSO runs through the custom properties (the
        dark class only overrides variables, never re-declares rules)."""
        scss = _read("static/src/scss/webmail.scss")
        self.assertIn("var(--wmail-panel-bg)", scss)


# ===========================================================================
# TRIANGULATE (PR-2g) — receipts + same-folder TOGETHER; fresh-controller
# persistence of the new general settings
# ===========================================================================


@tagged("standard", "at_install")
class TestComposeSettingsTriangulate(SendTestMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        self.setUpSend()
        self.Settings = self.env["mail.management.settings"]
        self.settings = self.Settings.create({"user_id": self.mailbox.user_id.id})

    def test_receipts_and_same_folder_together(self):
        """TRIANGULATE: both receipt headers AND the same-folder reply save
        compose on one reply — INBOX APPEND + both headers at once."""
        self.settings.reply_save_folder = "same"
        self.settings.request_read_receipt = True
        self.settings.request_delivery_receipt = True
        imap = FakeImapClient()
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, imap),
        ):
            result = self._send_reply(smtp=smtp)
        msg = smtp.sent[0]
        self.assertEqual(msg["Disposition-Notification-To"], "me@send.com")
        self.assertEqual(msg["Return-Receipt-To"], "me@send.com")
        self.assertEqual(imap.appends[0][1], "INBOX")
        self.assertEqual(result["message"].folder_id, self.inbox)

    def test_theme_and_badge_persist_for_fresh_controller(self):
        """TRIANGULATE: theme + unread badge persist in the DB — a brand-new
        controller instance (next RPC) reads them back."""
        user = _users(self.env)
        env_a = self.env(user=user)
        with _mock_request(env_a):
            _controller().save_settings(theme="dark", show_unread_badge=False)
            payload = _controller().get_settings()
        self.assertEqual(payload["theme"], "dark")
        self.assertFalse(payload["show_unread_badge"])

    def test_plain_editor_setting_reaches_send_path(self):
        """TRIANGULATE: the compose_editor setting alone changes NOTHING on
        the server (the plain rendition is client-side) — a body_text-only
        send stays single-part whatever the editor."""
        self.settings.compose_editor = "plain"
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self._send_reply(smtp=smtp, body_text="Plain words")
        msg = smtp.sent[0]
        self.assertEqual(msg.get_content_type(), "text/plain")
        self.assertFalse(msg.is_multipart())
        self.assertIn("Plain words", msg.get_content())


# ===========================================================================
# es_CL: a NEW Python-side term of PR-2g must translate
# ===========================================================================


@tagged("standard", "at_install")
class TestComposeSettingsTermTranslatedEsCl(TransactionCase):
    def test_invalid_theme_translated_es_cl(self):
        self.env["res.lang"]._activate_lang("es_CL")
        env = self.env["mail.management.settings"].with_context(lang="es_CL").env
        self.assertEqual(env._("Invalid theme."), "Tema no válido.")
