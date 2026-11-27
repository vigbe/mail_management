"""AI assistant JSON endpoints (PR-3a). Isolation: ir.rules + explicit checks."""

import logging

from odoo import _, http
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)


class MailManagementAssistantController(http.Controller):
    def _mailbox(self, mailbox_id):
        mailbox = request.env["mail.management.mailbox"].browse(mailbox_id).exists()
        if not mailbox:
            raise UserError(_("Mailbox not found."))
        return mailbox

    def _message(self, message_id):
        message = request.env["mail.management.message"].browse(message_id).exists()
        if not message:
            raise UserError(_("Message not found."))
        return message

    @http.route("/mail_management/assistant/chat", type="jsonrpc", auth="user")
    def assistant_chat(self, mailbox_id, text, **kw):
        if not (text or "").strip():
            return {"reply": ""}
        mailbox = self._mailbox(mailbox_id)
        session = request.env["mail.management.chat.session"]._get_or_create(mailbox)
        assistant = request.env["mail.management.assistant"]
        reply = assistant.chat(mailbox, session, text.strip())
        return {
            "reply": reply,
            "history": [
                {"role": m.role, "content": m.content} for m in session.message_ids
            ],
        }

    @http.route("/mail_management/assistant/history", type="jsonrpc", auth="user")
    def assistant_history(self, mailbox_id, **kw):
        mailbox = self._mailbox(mailbox_id)
        session = request.env["mail.management.chat.session"].search(
            [
                ("user_id", "=", request.env.uid),
                ("mailbox_id", "=", mailbox.id),
            ],
            limit=1,
        )
        return {
            "history": [
                {"role": m.role, "content": m.content} for m in session.message_ids
            ]
            if session
            else []
        }

    @http.route("/mail_management/assistant/summarize", type="jsonrpc", auth="user")
    def assistant_summarize(self, message_id, **kw):
        message = self._message(message_id)
        result = request.env["mail.management.assistant"].summarize(message)
        return result

    @http.route("/mail_management/assistant/draft", type="jsonrpc", auth="user")
    def assistant_draft(self, message_id, instructions="", **kw):
        message = self._message(message_id)
        return request.env["mail.management.assistant"].draft(
            message, instructions or ""
        )

    @http.route("/mail_management/assistant/triage", type="jsonrpc", auth="user")
    def assistant_triage(self, mailbox_id, folder_id=None, **kw):
        mailbox = self._mailbox(mailbox_id)
        folder = None
        if folder_id:
            folder = request.env["mail.management.folder"].browse(folder_id).exists()
        return request.env["mail.management.assistant"].triage(mailbox, folder)

    @http.route(
        "/mail_management/assistant/search",
        type="jsonrpc",
        auth="user",
    )
    def assistant_search(self, mailbox_id, query, **kw):
        mailbox = self._mailbox(mailbox_id)
        domain = [
            ("mailbox_id", "=", mailbox.id),
            "|",
            "|",
            "|",
            ("subject", "ilike", query),
            ("email_from", "ilike", query),
            ("email_to", "ilike", query),
            ("body_text", "ilike", query),
        ]
        results = request.env["mail.management.message"].search(
            domain, limit=20, order="date desc"
        )
        summary = request.env["mail.management.assistant"].search_summary(
            mailbox, query, results
        )
        return {
            "summary": summary,
            "results": [
                {
                    "id": m.id,
                    "subject": m.subject,
                    "email_from": m.email_from,
                    "date": str(m.date or ""),
                }
                for m in results
            ],
        }
