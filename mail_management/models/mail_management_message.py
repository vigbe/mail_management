"""Webmail message model — ``mail.management.message``.

Design reference: openspec/changes/webmail-and-ai-assistant/design.md §A.3
Includes the HIGH-RISK ``UNIQUE(folder_id, uid)`` constraint (§A.3.1).
"""

from odoo import api, fields, models  # type: ignore


class MailManagementMessage(models.Model):
    _name = "mail.management.message"
    _description = "Mail Message"
    _order = "date desc"

    # ------------------------------------------------------------------
    # Relations
    # ------------------------------------------------------------------
    mailbox_id = fields.Many2one(
        "mail.management.mailbox",
        required=True,
        ondelete="cascade",
        index=True,
    )
    folder_id = fields.Many2one(
        "mail.management.folder",
        required=True,
        ondelete="cascade",
        index=True,
    )
    user_id = fields.Many2one(
        "res.users",
        related="mailbox_id.user_id",
        store=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # IMAP identifiers
    # ------------------------------------------------------------------
    uid = fields.Integer(required=True, index=True)
    message_id = fields.Char(
        index=True, help="RFC822 Message-ID header (soft-dedup key)."
    )

    # ------------------------------------------------------------------
    # Headers
    # ------------------------------------------------------------------
    subject = fields.Char()
    email_from = fields.Char()
    email_to = fields.Text()
    email_cc = fields.Text()
    date = fields.Datetime()
    in_reply_to = fields.Char()
    references = fields.Text()

    # ------------------------------------------------------------------
    # Body (sanitized at sync time via html_sanitize + fields.Html sanitize)
    # ------------------------------------------------------------------
    body_html = fields.Html(sanitize=True)
    body_text = fields.Text()

    # ------------------------------------------------------------------
    # Flags
    # ------------------------------------------------------------------
    flag_seen = fields.Boolean(default=False)
    flag_answered = fields.Boolean(default=False)
    flag_flagged = fields.Boolean(default=False)
    flag_deleted = fields.Boolean(default=False)

    # ------------------------------------------------------------------
    # PR-2h — soft-delete retention anchor
    # ------------------------------------------------------------------
    trashed_date = fields.Datetime(
        index=True,
        help="When this message entered the Trash folder (the retention "
        "anchor the daily purge cron reads).  Approximation: messages "
        "already in Trash when first synced are stamped at that first "
        "observed sync; deletions made from other clients are stamped on "
        "the next Trash sync.",
    )

    # ------------------------------------------------------------------
    # Computed / relations
    # ------------------------------------------------------------------
    has_attachment = fields.Boolean(compute="_compute_has_attachment", store=True)
    parent_id = fields.Many2one("mail.management.message", compute="_compute_parent")
    attachment_ids = fields.One2many("mail.management.attachment", "webmail_message_id")

    # ------------------------------------------------------------------
    # Constraints & indexes (HIGH-RISK A.3.1)
    # ------------------------------------------------------------------
    _sql_constraints = [
        (
            "msg_folder_uid_uniq",
            "unique(folder_id, uid)",
            "A message with this (folder, UID) already exists.",
        ),
    ]

    def init(self):
        # Odoo <=18: plain SQL indexes (models.Index is 19+).
        self.env.cr.execute(
            "CREATE INDEX IF NOT EXISTS mail_management_message_mailbox_msgid_idx "
            "ON mail_management_message (mailbox_id, message_id)"
        )
        self.env.cr.execute(
            "CREATE INDEX IF NOT EXISTS mail_management_message_folder_date_idx "
            "ON mail_management_message (folder_id, date DESC)"
        )

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------
    @api.depends("attachment_ids")
    def _compute_has_attachment(self):
        for rec in self:
            rec.has_attachment = bool(rec.attachment_ids)

    @api.depends("in_reply_to")
    def _compute_parent(self):
        """Lightweight threading: link by In-Reply-To -> message_id."""
        for rec in self:
            if rec.in_reply_to:
                parent = self.search(
                    [
                        ("mailbox_id", "=", rec.mailbox_id.id),
                        ("message_id", "=", rec.in_reply_to),
                    ],
                    limit=1,
                )
                rec.parent_id = parent or False
            else:
                rec.parent_id = False
