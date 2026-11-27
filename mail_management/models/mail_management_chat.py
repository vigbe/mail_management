"""Per-mailbox chat sessions for the AI assistant (PR-3a).

One session per (user, mailbox): switching mailbox switches the chat; the
history is PERSISTED so nothing is ever forgotten when switching back.
"""

from odoo import fields, models


class MailManagementChatSession(models.Model):
    _name = "mail.management.chat.session"
    _description = "Mailbox AI Chat Session"
    _order = "id desc"

    user_id = fields.Many2one(
        "res.users", required=True, index=True, default=lambda self: self.env.user
    )
    mailbox_id = fields.Many2one(
        "mail.management.mailbox", required=True, index=True, ondelete="cascade"
    )
    message_ids = fields.One2many("mail.management.chat.message", "session_id")

    def _get_or_create(self, mailbox):
        mailbox.ensure_one()
        session = self.search(
            [
                ("user_id", "=", self.env.user.id),
                ("mailbox_id", "=", mailbox.id),
            ],
            limit=1,
        )
        return session or self.create(
            {"user_id": self.env.user.id, "mailbox_id": mailbox.id}
        )


class MailManagementChatMessage(models.Model):
    _name = "mail.management.chat.message"
    _description = "Mailbox AI Chat Message"
    _order = "id asc"

    session_id = fields.Many2one(
        "mail.management.chat.session",
        required=True,
        index=True,
        ondelete="cascade",
    )
    role = fields.Selection(
        [("user", "Usuario"), ("assistant", "Asistente")], required=True
    )
    content = fields.Text()
    created = fields.Datetime(default=lambda self: fields.Datetime.now())
