"""Thin filestore-pointer model — ``mail.management.attachment``.

Design reference: openspec/changes/webmail-and-ai-assistant/design.md §A.4
The binary payload lives in ``ir.attachment`` (Odoo filestore); this model
is a thin pointer that carries metadata + CID for inline images.
"""

from odoo import fields, models


class MailManagementAttachment(models.Model):
    _name = "mail.management.attachment"
    _description = "Mail Attachment"
    _order = "name"

    # ------------------------------------------------------------------
    # Fields (design A.4)
    # ------------------------------------------------------------------
    webmail_message_id = fields.Many2one(
        "mail.management.message",
        required=True,
        ondelete="cascade",
        index=True,
    )
    user_id = fields.Many2one(
        "res.users",
        related="webmail_message_id.user_id",
        store=True,
        index=True,
    )
    name = fields.Char(required=True)
    mimetype = fields.Char()
    file_size = fields.Integer()
    content_id = fields.Char(
        help="Content-ID for inline (multipart/related) images."
    )
    ir_attachment_id = fields.Many2one(
        "ir.attachment",
        ondelete="restrict",
        required=True,
    )
    inline = fields.Boolean(default=False)

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------
    def init(self):
        # Odoo <=18: plain SQL index (models.Index is 19+).
        self.env.cr.execute(
            "CREATE INDEX IF NOT EXISTS mail_management_attachment_msg_cid_idx "
            "ON mail_management_attachment (webmail_message_id, content_id)"
        )
