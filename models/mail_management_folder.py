"""IMAP folder + sync-state model — ``mail.management.folder``.

Design reference: openspec/changes/webmail-and-ai-assistant/design.md §A.2
Holds the per-folder UIDVALIDITY watermark and the incremental-fetch
``last_uid`` used by ``_sync_mailbox`` (§C.3).

PR-2g adds the SnappyMail-parity folder PREFERENCES:

* ``visible`` — hidden folders keep syncing but leave the folder tree
  (restorable from Settings ▸ Folders);
* ``_set_system_role`` — the user-facing system-folder MAPPING API (one
  folder per mailbox and role, 'inbox' fixed, swap-on-conflict);
* ``_settings_role_labels`` — translated role names for the mapping
  dropdown (the server owns the terms; the JS never duplicates them).
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MailManagementFolder(models.Model):
    _name = "mail.management.folder"
    _description = "Mail Folder"
    _order = "name"

    # ------------------------------------------------------------------
    # Fields (design A.2)
    # ------------------------------------------------------------------
    mailbox_id = fields.Many2one(
        "mail.management.mailbox",
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
    name = fields.Char()
    imap_name = fields.Char(required=True)
    role = fields.Selection(
        [
            ("inbox", "Inbox"),
            ("sent", "Sent"),
            ("drafts", "Drafts"),
            ("trash", "Trash"),
            ("all", "All"),
            ("archive", "Archive"),
            ("junk", "Junk"),
            ("other", "Other"),
        ],
        default="other",
    )
    uidvalidity = fields.Char(
        help="Current UIDVALIDITY for this folder (Char to safely "
        "hold large integer values).",
    )
    last_uid = fields.Integer(
        default=0,
        help="Highest UID fetched in the current UIDVALIDITY epoch.",
    )
    last_sync = fields.Datetime()
    visible = fields.Boolean(
        string="Visible in the folder tree",
        default=True,
        help="Hidden folders stay synced but do not appear in the folder "
        "tree (they can be restored from Settings ▸ Folders).",
    )
    message_ids = fields.One2many("mail.management.message", "folder_id")
    message_count = fields.Integer(compute="_compute_counts", store=True)
    unread_count = fields.Integer(compute="_compute_counts", store=True)
    display_name = fields.Char(compute="_compute_display_name")

    # Friendly label per role (source terms; translated at compute time so
    # the user's language applies — _() must NOT run at import time).
    # Anything else falls back to a cleaned raw name so users never see
    # "INBOX.Archive"-style names.
    _ROLE_LABELS = {
        "inbox": "Inbox",
        "sent": "Sent",
        "drafts": "Drafts",
        "trash": "Trash",
        "junk": "Junk",
        "archive": "Archive",
        "all": "All",
    }

    #: PR-2g — system-folder roles a USER may assign through the settings
    #: API.  'inbox' is deliberately absent: the Inbox role is fixed (one
    #: inbox per mailbox, auto-detected at discovery, never remapped).
    MAPPABLE_ROLES = ("sent", "drafts", "trash", "junk", "archive", "all", "other")

    # ------------------------------------------------------------------
    # Constraints & indexes
    # ------------------------------------------------------------------
    _folder_uniq = models.Constraint(
        "UNIQUE(mailbox_id, imap_name)",
        "A folder with this IMAP name already exists for this mailbox.",
    )
    _folder_mailbox_idx = models.Index("(mailbox_id, role)")

    # ------------------------------------------------------------------
    # Computed counts (design A.2) + display labels (user feedback 2026-09)
    # ------------------------------------------------------------------
    @api.depends("message_ids", "message_ids.flag_seen")
    def _compute_counts(self):
        for rec in self:
            msgs = rec.message_ids
            rec.message_count = len(msgs)
            rec.unread_count = len(msgs.filtered(lambda m: not m.flag_seen))

    @api.depends("role", "name", "imap_name")
    def _compute_display_name(self):
        for rec in self:
            label = self._ROLE_LABELS.get(rec.role)
            if label:
                # env-bound translator: resolves the term against the
                # CURRENT environment language (reads i18n/*.po lazily).
                # The module-level _() has no env context and stays English.
                rec.display_name = self.env._(label)
                continue
            raw = rec.name or rec.imap_name or ""
            if raw.upper().startswith("INBOX."):
                raw = raw[len("INBOX.") :]
            rec.display_name = raw

    # ------------------------------------------------------------------
    # PR-2g — system-folder mapping + visibility (SnappyMail parity)
    # ------------------------------------------------------------------

    def _set_system_role(self, role):
        """Override the guessed system role of THIS folder (PR-2g).

        Contract (user-facing mapping API — called by the controller with
        a record already scoped by the per-user ir.rules):

        * ``role`` is normalized; ``''`` clears to ``'other'``;
        * ``'inbox'`` is FIXED: assigning it, changing a folder whose role
          IS ``'inbox'`` (or clearing it) raises ``UserError``;
        * anything outside :attr:`MAPPABLE_ROLES` raises ``UserError``;
        * per-mailbox uniqueness: assigning a system role currently held
          by ANOTHER folder of the same mailbox SWAPS — the previous
          holder is demoted to ``'other'`` (one dropdown action, exactly
          like SnappyMail's per-role mapping);
        * ``'other'`` stays free-form (many folders may be ``'other'``).

        :returns: the demoted previous holder (empty recordset when none)
        """
        self.ensure_one()
        clean = (role or "").strip() or "other"
        if clean == "inbox":
            raise UserError(_("The Inbox role cannot be assigned manually."))
        if self.role == "inbox":
            raise UserError(_("The Inbox role cannot be changed."))
        if clean not in self.MAPPABLE_ROLES:
            raise UserError(_("Invalid folder role."))
        demoted = self.mailbox_id.folder_ids.filtered(
            lambda f, r=clean: f.id != self.id and f.role == r and r != "other"
        )[:1]
        if demoted:
            demoted.write({"role": "other"})
        self.write({"role": clean})
        return demoted

    @api.model
    def _settings_role_labels(self):
        """Translated label per role for the settings mapping dropdown —
        the SERVER owns the role names so the webmail JS never declares
        duplicate msgids for them."""
        labels = {role: self.env._(label) for role, label in self._ROLE_LABELS.items()}
        labels["other"] = self.env._("Other")
        return labels
