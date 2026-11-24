"""Strict-TDD RED tests for PR-2c — multipart/alternative HTML send.

Covers the PR-2c send contract (openspec/changes/webmail-complete-
management/tasks.md):

* ``mailbox._send_message(body_html=...)``: when the body is/contains HTML,
  the outgoing MIME message is ``multipart/alternative`` with a text/plain
  part (generated fallback: tags stripped sensibly) AND a text/html part
  (SANITIZED via ``tools.html_sanitize`` before it leaves — ``<script>`` and
  event handlers never reach the wire);
* plain-only bodies keep the single-part ``text/plain`` path unchanged;
* attachments + HTML compose ``multipart/mixed`` wrapping the alternative;
* the local Sent mirror stores ``body_html`` (sanitized) and ``body_text``
  (the plain fallback);
* ``/mail_management/send`` accepts and forwards a ``body_html`` parameter.

All transports are MOCKED (the SMTP seam records the EmailMessage) — no real
network calls.
"""

from unittest import mock

from odoo.addons.mail_management.tests.test_compose_send import (  # type: ignore
    IMAP_PATCH,
    SMTP_PATCH,
    FakeImapClient,
    FakeSmtpClient,
    SendTestMixin,
    _mock_request,
)
from odoo.tests.common import TransactionCase, tagged  # type: ignore


def _parts_by_type(message):
    """Map content-type -> list of parts for an EmailMessage."""
    result = {}
    for part in message.walk():
        if part.is_multipart():
            continue
        result.setdefault(part.get_content_type(), []).append(part)
    return result


# ===========================================================================
# Model-level: _send_message(body_html=...)
# ===========================================================================


@tagged("standard", "at_install")
class TestHtmlSend(SendTestMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        self.setUpSend()

    def _send_html(self, smtp=None, imap=None, **kw):
        """Call ``_send_message`` with an HTML body and default valid args."""
        vals = {
            "to": "customer@ext.com",
            "subject": "Re: Original subject",
            "body_text": "",
            "body_html": "<p>Hello <b>world</b></p>",
            "reply_to_message": self.original,
        }
        vals.update(kw)
        return self.mailbox._send_message(**vals)

    def _send_and_get(self, **kw):
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self._send_html(smtp=smtp, **kw)
        return smtp.sent[0]

    # -- structure -----------------------------------------------------------

    def test_html_body_builds_multipart_alternative(self):
        msg = self._send_and_get()
        self.assertEqual(msg.get_content_type(), "multipart/alternative")

    def test_both_parts_present(self):
        msg = self._send_and_get()
        parts = _parts_by_type(msg)
        self.assertIn("text/plain", parts)
        self.assertIn("text/html", parts)

    def test_html_part_carries_the_body(self):
        msg = self._send_and_get(body_html="<p>RICH BODY MARKER</p>")
        html_part = _parts_by_type(msg)["text/html"][0]
        content = html_part.get_content()
        self.assertIn("RICH BODY MARKER", content)
        self.assertIn("<p>", content)

    # -- sanitize-out (never trust the client) --------------------------------

    def test_script_never_leaves(self):
        msg = self._send_and_get(body_html="<p>ok</p><script>alert('evil')</script>")
        html_part = _parts_by_type(msg)["text/html"][0]
        content = html_part.get_content()
        self.assertNotIn("<script>", content)
        self.assertNotIn("alert", content)
        self.assertIn("ok", content)

    def test_event_handler_never_leaves(self):
        msg = self._send_and_get(body_html='<p onmouseover="steal()">hover</p>')
        html_part = _parts_by_type(msg)["text/html"][0]
        self.assertNotIn("onmouseover", html_part.get_content())
        self.assertIn("hover", html_part.get_content())

    def test_img_kept_but_onerror_stripped(self):
        msg = self._send_and_get(
            body_html='<p><img src="https://x/l.png" onerror="alert(1)"/></p>'
        )
        content = _parts_by_type(msg)["text/html"][0].get_content()
        self.assertIn("<img", content)
        self.assertNotIn("onerror", content)

    # -- plain fallback -------------------------------------------------------

    def test_plain_fallback_non_empty_and_tag_free(self):
        msg = self._send_and_get(body_html="<p>Hello <b>world</b></p>")
        plain = _parts_by_type(msg)["text/plain"][0].get_content()
        self.assertTrue(plain.strip())
        self.assertNotIn("<", plain)
        self.assertNotIn(">", plain)
        self.assertIn("Hello", plain)
        self.assertIn("world", plain)

    def test_explicit_body_text_used_as_plain_part(self):
        msg = self._send_and_get(
            body_html="<p>rich</p>", body_text="Custom plain version"
        )
        plain = _parts_by_type(msg)["text/plain"][0].get_content()
        self.assertIn("Custom plain version", plain)

    # -- plain-only path unchanged ---------------------------------------------

    def test_plain_only_path_single_part(self):
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self._send_html(smtp=smtp, body_html="", body_text="Plain only body")
        msg = smtp.sent[0]
        self.assertEqual(msg.get_content_type(), "text/plain")
        self.assertIn("Plain only body", msg.get_content())

    # -- sent copy storage ------------------------------------------------------

    def test_sent_copy_body_fields_populated(self):
        smtp = FakeSmtpClient()
        imap = FakeImapClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, imap),
        ):
            result = self._send_html(smtp=smtp, imap=imap)
        sent_rec = result["message"]
        sent_msg = smtp.sent[0]
        self.assertIn("Hello", sent_rec.body_html or "")
        self.assertIn("<p>", sent_rec.body_html or "")
        # body_text mirrors the plain part that went to the wire.
        plain_wire = _parts_by_type(sent_msg)["text/plain"][0].get_content()
        self.assertEqual((sent_rec.body_text or "").strip(), plain_wire.strip())

    def test_sent_copy_body_html_sanitized(self):
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            result = self._send_html(
                smtp=smtp,
                body_html="<p>fine</p><script>x()</script>",
            )
        self.assertNotIn("<script>", result["message"].body_html or "")
        self.assertNotIn("x()", result["message"].body_html or "")
        self.assertIn("fine", result["message"].body_html or "")

    def test_plain_only_sent_copy_has_no_body_html(self):
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            result = self._send_html(smtp=smtp, body_html="", body_text="plain body")
        self.assertFalse(result["message"].body_html)
        self.assertEqual(result["message"].body_text, "plain body")

    # -- attachments + html -------------------------------------------------------

    def test_html_with_attachments_is_multipart_mixed_with_alternative(self):
        smtp = FakeSmtpClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self._send_html(
                smtp=smtp,
                attachments=[
                    {
                        "name": "notes.txt",
                        "content": b"data",
                        "mimetype": "text/plain",
                    }
                ],
            )
        msg = smtp.sent[0]
        self.assertEqual(msg.get_content_type(), "multipart/mixed")
        parts = _parts_by_type(msg)
        self.assertIn("text/plain", parts)
        self.assertIn("text/html", parts)
        self.assertEqual(
            [p.get_filename() for p in msg.iter_attachments()],
            ["notes.txt"],
        )
        # The two body parts sit together inside the alternative subpart.
        alt = next(
            p for p in msg.walk() if p.get_content_type() == "multipart/alternative"
        )
        self.assertEqual(
            sorted(p.get_content_type() for p in alt.iter_parts()),
            ["text/html", "text/plain"],
        )

    def test_appended_sent_copy_is_alternative(self):
        """The IMAP APPENDed Sent copy carries both parts too."""
        smtp = FakeSmtpClient()
        imap = FakeImapClient()
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, imap),
        ):
            self._send_html(smtp=smtp, imap=imap)
        import email as email_lib

        appended = email_lib.message_from_bytes(imap.appends[0][0])
        types = sorted(
            p.get_content_type() for p in appended.walk() if not p.is_multipart()
        )
        self.assertEqual(types, ["text/html", "text/plain"])

    # -- reply threading preserved on the html path --------------------------------

    def test_reply_headers_kept_on_html_send(self):
        msg = self._send_and_get()
        self.assertEqual(msg["In-Reply-To"], "<orig@ext.com>")
        self.assertIn("<orig@ext.com>", (msg["References"] or "").split())


# ===========================================================================
# /mail_management/send controller — body_html parameter
# ===========================================================================


@tagged("standard", "at_install")
class TestHtmlSendController(SendTestMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        self.setUpSend()
        from odoo.addons.mail_management.controllers.main import (  # type: ignore
            MailManagementController,
        )

        self.controller = MailManagementController()
        group_user = self.env.ref("mail_management.group_mail_management_user")
        self.user_a = self.env["res.users"].create(
            {
                "name": "Html A",
                "login": "html_a@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )
        self.mailbox.user_id = self.user_a

    def test_controller_accepts_body_html(self):
        smtp = FakeSmtpClient()
        with (
            _mock_request(self.env(user=self.user_a)),
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            result = self.controller.send(
                mailbox_id=self.mailbox.id,
                to="customer@ext.com",
                subject="Re: Original subject",
                body_text="",
                body_html="<p>Controller <em>rich</em> body</p>",
                mode="reply",
                reply_to_message_id=self.original.id,
            )
        self.assertTrue(result["success"])
        msg = smtp.sent[0]
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        parts = _parts_by_type(msg)
        self.assertIn("text/html", parts)
        self.assertIn("Controller", parts["text/html"][0].get_content())

    def test_controller_sanitizes_body_html(self):
        smtp = FakeSmtpClient()
        with (
            _mock_request(self.env(user=self.user_a)),
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self.controller.send(
                mailbox_id=self.mailbox.id,
                to="customer@ext.com",
                subject="Re: x",
                body_html="<p>fine</p><script>alert(1)</script>",
                mode="reply",
                reply_to_message_id=self.original.id,
            )
        content = _parts_by_type(smtp.sent[0])["text/html"][0].get_content()
        self.assertNotIn("<script>", content)
        self.assertIn("fine", content)


# ===========================================================================
# TRIANGULATE (PR-2c): quote + signature combined, forward + attachments
# ===========================================================================


@tagged("standard", "at_install")
class TestHtmlSendTriangulate(SendTestMixin, TransactionCase):
    """Compose-shaped bodies: quote + signature, forward + attachments."""

    def setUp(self):
        super().setUp()
        self.setUpSend()

    def _send_body(self, body_html, smtp=None, **kw):
        smtp = smtp or FakeSmtpClient()
        vals = {
            "to": "customer@ext.com",
            "subject": "Re: Original subject",
            "body_html": body_html,
            "reply_to_message": self.original,
        }
        vals.update(kw)
        with (
            mock.patch(SMTP_PATCH, smtp),
            mock.patch(IMAP_PATCH, FakeImapClient()),
        ):
            self.mailbox._send_message(**vals)
        return smtp.sent[0]

    @staticmethod
    def _html_part(msg):
        return next(
            p for p in msg.walk() if p.get_content_type() == "text/html"
        ).get_content()

    @staticmethod
    def _plain_part(msg):
        return next(
            p for p in msg.walk() if p.get_content_type() == "text/plain"
        ).get_content()

    def test_reply_quote_plus_signature_combined(self):
        """The compose-shaped body (blockquote + '-- ' signature) sends with
        BOTH blocks in the html part and both texts in the plain fallback."""
        body = (
            "<p>Here is my answer.</p>"
            "<blockquote><p>&gt; Original body line 1</p></blockquote>"
            '<div class="o_webmail_signature" data-o-mail-signature="1">'
            "<p>--&nbsp;</p><p>Alice Smith — ACME</p></div>"
        )
        msg = self._send_body(body)
        html = self._html_part(msg)
        self.assertIn("Here is my answer.", html)
        self.assertIn("blockquote", html)
        self.assertIn("Alice Smith — ACME", html)
        plain = self._plain_part(msg)
        self.assertIn("Here is my answer.", plain)
        self.assertIn("Original body line 1", plain)
        self.assertIn("Alice Smith — ACME", plain)
        # The standard '-- ' separator survives into the plain fallback
        # (n&nbsp; becomes a plain space / newline boundary).
        self.assertIn("--", plain)
        self.assertNotIn("<", plain)

    def test_forward_keeps_attachments_and_signature(self):
        """Forward: original attachments ride along AND the signature stays
        in the html alternative."""

        ir_att = self.IrAttachment.sudo().create(
            {
                "name": "contract.pdf",
                "raw": b"%PDF-fwd",
                "res_model": "mail.management.message",
                "res_id": self.original.id,
                "mimetype": "application/pdf",
            }
        )
        self.WAttach.create(
            {
                "webmail_message_id": self.original.id,
                "name": "contract.pdf",
                "mimetype": "application/pdf",
                "file_size": 8,
                "ir_attachment_id": ir_att.id,
            }
        )
        msg = self._send_body(
            "<p>Take a look.</p>"
            '<div class="o_webmail_signature" data-o-mail-signature="1">'
            "<p>--&nbsp;</p><p>Alice</p></div>",
            to="friend@other.com",
            forward=True,
        )
        self.assertEqual(msg.get_content_type(), "multipart/mixed")
        self.assertIn(
            "contract.pdf", [p.get_filename() for p in msg.iter_attachments()]
        )
        html = self._html_part(msg)
        self.assertIn("Take a look.", html)
        self.assertIn("Alice", html)
        self.assertIn("--", self._plain_part(msg))

    def test_signature_block_sanitized_in_transit(self):
        """A malicious signature block injected into the compose body is
        stripped by the sanitize-out gate — signature or not."""
        msg = self._send_body(
            '<p>ok</p><div data-o-mail-signature="1">'
            "<p>--&nbsp;</p><p>sig</p>"
            '<script>document.location="https://evil"</script></div>'
        )
        html = self._html_part(msg)
        self.assertNotIn("<script>", html)
        self.assertNotIn("evil", html)
        self.assertIn("sig", html)

    def test_empty_body_html_falls_back_to_plain_path(self):
        """Whitespace-only body_html must NOT produce an empty alternative."""
        msg = self._send_body("   ", body_text="Fallback only")
        self.assertEqual(msg.get_content_type(), "text/plain")
        self.assertIn("Fallback only", msg.get_content())
