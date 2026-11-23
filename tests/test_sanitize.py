"""Tests for HTML sanitization on message store (PR-1b-i).

Strict-TDD RED: written before the sync sanitization logic exists.
Verifies design §A.3 (body_html Html sanitize=True) and §C.3
(html_sanitize before store).

The ``fields.Html(sanitize=True)`` field + explicit ``html_sanitize``
call in ``_parse_raw_message`` together guarantee that stored bodies
never contain ``<script>`` or event-handler attributes.
"""

from unittest import mock

from odoo.tests.common import TransactionCase, tagged

IMAP_PATCH = "odoo.addons.mail_management.models.mail_management.ImapClient"


def _make_raw(*, body_html="", body_text="", message_id="<s@t.com>", subject="S"):
    """Build a minimal RFC822 message for sanitization tests."""
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "s@t.com"
    msg["To"] = "r@t.com"
    msg["Message-ID"] = message_id
    if body_text and body_html:
        msg.set_content(body_text)
        msg.add_alternative(body_html, subtype="html")
    elif body_html:
        msg.set_content(body_html, subtype="html")
    elif body_text:
        msg.set_content(body_text)
    else:
        msg.set_content("")
    return msg.as_bytes()


@tagged("standard", "at_install")
class TestHtmlSanitize(TransactionCase):
    """html_sanitize strips dangerous content; sync stores sanitized HTML."""

    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        self.Folder = self.env["mail.management.folder"]
        self.Msg = self.env["mail.management.message"]
        self.mailbox = self.Mailbox.create(
            {
                "name": "Sanitize Test",
                "provider_type": "generic",
                "imap_host": "imap.s.com",
                "imap_user": "s@s.com",
                "smtp_host": "smtp.s.com",
                "from_email": "s@s.com",
                "credential": "san-pass",
                "state": "active",
            }
        )
        self.folder = self.Folder.create(
            {"mailbox_id": self.mailbox.id, "imap_name": "INBOX", "role": "inbox"}
        )

    def _sync_raw(self, raw):
        """Patch ImapClient to return one message and sync."""
        from odoo.addons.mail_management.tests.test_imap_sync import FakeImapClient

        config = {"INBOX": {"uidvalidity": "1", "messages": [(1, raw, set())]}}
        with mock.patch(IMAP_PATCH, return_value=FakeImapClient(config)):
            self.mailbox._sync_mailbox()

    # ------------------------------------------------------------------
    # Direct html_sanitize behaviour
    # ------------------------------------------------------------------
    def test_html_sanitize_strips_script(self):
        """odoo.tools.mail.html_sanitize removes <script> tags."""
        from odoo.tools.mail import html_sanitize

        cleaned = html_sanitize("<script>alert(1)</script><p>hello</p>")
        self.assertNotIn("<script>", cleaned)
        self.assertIn("hello", cleaned)

    def test_html_sanitize_strips_event_handlers(self):
        """html_sanitize removes onerror/onmouseover event handlers."""
        from odoo.tools.mail import html_sanitize

        cleaned = html_sanitize('<p onmouseover="alert(1)">hi</p>')
        self.assertNotIn("onmouseover", cleaned)
        self.assertIn("hi", cleaned)

    def test_html_sanitize_strips_img_onerror(self):
        """html_sanitize removes <img onerror> but keeps the img tag."""
        from odoo.tools.mail import html_sanitize

        cleaned = html_sanitize('<img src="x" onerror="alert(1)">')
        self.assertNotIn("onerror", cleaned)
        self.assertIn("<img", cleaned)

    # ------------------------------------------------------------------
    # multipart/alternative → HTML part stored
    # ------------------------------------------------------------------
    def test_multipart_alternative_stores_html_part(self):
        """multipart/alternative stores the HTML part as body_html."""
        raw = _make_raw(
            body_text="Plain version",
            body_html="<p>HTML version</p>",
        )
        self._sync_raw(raw)
        msg = self.Msg.search([("folder_id", "=", self.folder.id)])
        self.assertIn("HTML version", msg.body_html or "")
        self.assertIn("Plain version", msg.body_text or "")

    # ------------------------------------------------------------------
    # Sanitized BEFORE persist (via sync)
    # ------------------------------------------------------------------
    def test_sync_stores_sanitized_script_and_img_onerror(self):
        """Body with <script> and <img onerror> is sanitized when stored."""
        raw = _make_raw(
            body_html=(
                "<script>alert(1)</script>"
                '<p>safe text</p>'
                '<img src="x" onerror="alert(1)">'
            ),
            body_text="fallback",
        )
        self._sync_raw(raw)
        msg = self.Msg.search([("folder_id", "=", self.folder.id)])
        self.assertNotIn("<script>", msg.body_html or "")
        self.assertNotIn("onerror", msg.body_html or "")
        self.assertIn("safe text", msg.body_html or "")
