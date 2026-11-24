"""Per-user webmail settings — ``mail.management.settings`` (PR-2e + PR-2f).

A SINGLETON row per user (SQL-unique ``user_id``) holding the SnappyMail-style
webmail preferences:

* ``allow_remote_images`` — whether remote ``http(s)`` images render directly
  in the reading pane (default: BLOCKED — tracking-pixel protection).  This is
  a CLIENT-side display policy on top of the stored, server-sanitized bodies;
  it never weakens sanitization.
* ``signature_position`` — where the compose dialog inserts the signature
  relative to the quoted text ('below' = traditional bottom, default).

PR-2f (settings overhaul part 1 — message list + message view):

* ``messages_per_page`` — page size for /messages (clamped 10..200 on save);
* ``default_sort`` — sort applied on folder open, validated against the
  EXISTING controller sort whitelist keys (optionally with one priority
  suffix, e.g. ``date_desc+unread_first``); invalid values are IGNORED on
  model write and REJECTED (UserError) by the settings-save endpoint;
* ``show_checkboxes`` — hide the checkbox column + bulk bar when False;
* ``mark_read_after_seconds`` — 0 = mark instantly on open (default), N > 0 =
  deferred timer (cancelled on navigation), -1 = NEVER auto-mark;
* ``collapse_quoted_text`` / ``max_quote_levels`` — collapsible quote
  blockquotes (levels beyond the max hide behind "Show more quote");
* ``strip_message_styles`` — remove background/text colors on read;
* ``prefer_html`` — render the plain-text body instead when False;
* ``simple_attachment_list`` — compact attachment list instead of cards;
* ``show_next_on_move_delete`` — auto-open the next message after a
  move/delete (previous when the removed one was last);
* ``image_whitelist`` — one sender email per line (normalized on save);
  remote images FROM those senders render even when globally blocked.

Isolation follows the established pattern (group-scoped ir.rules: user = own,
manager = all).  The controller endpoints always pin the domain to
``env.user`` so even a manager only reads/writes their own row through the
webmail.
"""

import re

from odoo import api, fields, models  # type: ignore

SIGNATURE_POSITIONS = [("below", "Below the quote"), ("above", "Above the quote")]

#: PR-2g compose settings — the compose body editor (the 'plain' mode renders
#: a plain textarea instead of the WYSIWYG and sends single-part text).
COMPOSE_EDITORS = [("html", "HTML editor"), ("plain", "Plain text")]

#: PR-2g — where a sent REPLY is saved: the Sent folder (default) or the
#: folder of the message being replied to (SnappyMail parity).  Forwards and
#: fresh composes always use the Sent folder.
REPLY_SAVE_FOLDERS = [
    ("sent", "Sent folder"),
    ("same", "Folder of the message being replied to"),
]

#: PR-2g — the webmail palette (CSS custom properties in webmail.scss; the
#: 'dark' root class only overrides the variables).
WEBMAIL_THEMES = [
    ("light", "Light"),
    ("dark", "Dark"),
    ("night", "Night (OLED)"),
    ("sepia", "Sepia"),
    ("ocean", "Ocean"),
    ("forest", "Forest"),
]

#: PR-2f mark-read semantics: 0 = mark as read instantly on open, N > 0 =
#: defer N seconds (cancelled when the user navigates away), -1 = NEVER
#: auto-mark as read.
MARK_READ_INSTANT = 0
MARK_READ_NEVER = -1
#: Generous upper bound for the deferred mark-read timer (10 days).
MARK_READ_MAX_SECONDS = 10**6

#: Save-time clamps for the numeric preferences.
MESSAGES_PER_PAGE_MIN = 10
MESSAGES_PER_PAGE_MAX = 200
MAX_QUOTE_LEVELS_MIN = 1
MAX_QUOTE_LEVELS_MAX = 10

#: PR-2h — Trash retention window (days) for the folder soft-delete flow:
#: deleted-folder contents (and any message landing in Trash) are purged
#: after this many days by the daily cron.
TRASH_RETENTION_DEFAULT_DAYS = 30
TRASH_RETENTION_MIN_DAYS = 1
TRASH_RETENTION_MAX_DAYS = 365

#: One bare email address per whitelist line (case is normalized on save).
_EMAIL_LINE_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
#: ``Name <addr@example.com>`` display form (email_from values).
_BRACKET_ADDR_RE = re.compile(r"<([^<>\s]+@[^<>\s]+)>")
#: Bare address anywhere in the string (fallback for email_from).
_BARE_ADDR_RE = re.compile(r"[^\s<>]+@[^\s<>]+\.[^\s<>]+")


class MailManagementSettings(models.Model):
    _name = "mail.management.settings"
    _description = "Webmail Settings"

    user_id = fields.Many2one(
        "res.users",
        string="User",
        required=True,
        index=True,
        default=lambda self: self.env.user,
    )
    allow_remote_images = fields.Boolean(
        string="Allow remote images",
        default=False,
        help="Show remote images in messages directly instead of blocking "
        "them. Remote images can be used to track when you read a message.",
    )
    signature_position = fields.Selection(
        SIGNATURE_POSITIONS,
        string="Signature position",
        default="below",
        required=True,
        help="Where the signature is inserted when composing: below the "
        "quoted message (traditional) or above it.",
    )
    messages_per_page = fields.Integer(
        string="Messages per page",
        default=50,
        help="How many messages the message list fetches per page "
        "(clamped to 10-200 on save).",
    )
    default_sort = fields.Char(
        string="Default sort",
        default="date_desc",
        help="Sort applied when a folder is opened: a whitelisted sort key "
        "(date, date_desc, from, from_desc, subject, subject_desc), "
        "optionally with a priority suffix (+unread_first / +flagged_first). "
        "The user can still change the sort for the session.",
    )
    show_checkboxes = fields.Boolean(
        string="Show checkboxes",
        default=True,
        help="Show the selection checkboxes and the bulk-actions bar in the "
        "message list.",
    )
    mark_read_after_seconds = fields.Integer(
        string="Mark as read after (seconds)",
        default=MARK_READ_INSTANT,
        help="When an opened message is marked as read: 0 = immediately "
        "(default); a positive number = after that many seconds (cancelled "
        "if you navigate away); -1 = never mark as read automatically.",
    )
    collapse_quoted_text = fields.Boolean(
        string="Collapse quoted text",
        default=True,
        help="Collapse quoted message text behind a toggle when reading.",
    )
    max_quote_levels = fields.Integer(
        string="Max quote levels",
        default=3,
        help="How many nested quote levels stay directly visible when "
        "quoting is collapsed; deeper levels hide behind a 'Show more "
        "quote' toggle (clamped to 1-10 on save).",
    )
    strip_message_styles = fields.Boolean(
        string="Remove message colors",
        default=False,
        help="Remove background and text colors (and other inline styles) "
        "from message bodies when reading.",
    )
    prefer_html = fields.Boolean(
        string="Prefer HTML",
        default=True,
        help="Render the HTML body when available; when disabled the "
        "plain-text body is shown instead.",
    )
    simple_attachment_list = fields.Boolean(
        string="Simple attachment list",
        default=False,
        help="Show attachments as a compact list instead of preview cards.",
    )
    show_next_on_move_delete = fields.Boolean(
        string="Show next on move/delete",
        default=True,
        help="After moving or deleting a message, automatically open the "
        "next message in the list (the previous one when the removed "
        "message was the last).",
    )
    image_whitelist = fields.Text(
        string="Trusted image senders",
        default="",
        help="One sender email address per line: remote images in messages "
        "FROM these senders are always shown, even when remote images are "
        "blocked globally.",
    )
    compose_editor = fields.Selection(
        COMPOSE_EDITORS,
        string="Default editor",
        default="html",
        required=True,
        help="Editor used when composing: the rich HTML editor or a plain "
        "text area (plain sends single-part text messages).",
    )
    reply_save_folder = fields.Selection(
        REPLY_SAVE_FOLDERS,
        string="Save replies in",
        default="sent",
        required=True,
        help="Place replies in the folder of the message being replied to "
        "instead of the Sent folder (forwards always use Sent).",
    )
    request_read_receipt = fields.Boolean(
        string="Request read confirmation",
        default=False,
        help="Ask for a read receipt (Disposition-Notification-To header) "
        "on every message you send.",
    )
    request_delivery_receipt = fields.Boolean(
        string="Request delivery confirmation",
        default=False,
        help="Ask for a delivery receipt (Return-Receipt-To header) on "
        "every message you send.",
    )
    enable_spellcheck = fields.Boolean(
        string="Use browser spellchecker",
        default=True,
        help="Enable the browser spellchecker in the compose editor.",
    )
    show_unread_badge = fields.Boolean(
        string="Show unread count badge",
        default=True,
        help="Show the unread message count badge next to folders.",
    )
    theme = fields.Selection(
        WEBMAIL_THEMES,
        string="Theme",
        default="light",
        required=True,
        help="Webmail color theme.",
    )
    trash_retention_days = fields.Integer(
        string="Trash retention (days)",
        default=TRASH_RETENTION_DEFAULT_DAYS,
        help="How long messages stay in the Trash folder before the daily "
        "purge deletes them permanently (clamped to 1-365 on save).",
    )

    _sql_constraints = [
        (
            "settings_user_uniq",
            "unique(user_id)",
            "Only one settings row is allowed per user.",
        ),
    ]

    @api.model
    def _get_or_create_for(self, user):
        """Return ``user``'s settings row, creating the singleton on first use.

        Idempotent by construction (unique constraint + search-first); used by
        the /settings controller endpoints which always pass the CURRENT user.
        """
        settings = self.search([("user_id", "=", user.id)], limit=1)
        if not settings:
            settings = self.create({"user_id": user.id})
        return settings

    # ------------------------------------------------------------------
    # PR-2f — default sort validation (against the EXISTING controller
    # whitelists — the single source of truth for sort/priority keys)
    # ------------------------------------------------------------------

    @api.model
    def _controller_whitelists(self):
        """The EXISTING controller sort + priority whitelists (the single
        source of truth for list ordering).  Imported lazily inside the method
        because models load before controllers at registry build time."""
        from odoo.addons.mail_management.controllers.main import (  # type: ignore[attr-defined]
            _MESSAGE_PRIORITY_WHITELIST,
            _MESSAGE_SORT_WHITELIST,
        )

        return _MESSAGE_SORT_WHITELIST, _MESSAGE_PRIORITY_WHITELIST

    @api.model
    def _default_sort_keys(self):
        """Every valid default_sort token: the controller SORT whitelist keys
        UNION the PRIORITY whitelist keys (the suffix tokens)."""
        sort_whitelist, priority_whitelist = self._controller_whitelists()
        return set(sort_whitelist) | set(priority_whitelist)

    @api.model
    def _is_valid_default_sort(self, value):
        """True for ``<sort_key>`` or ``<sort_key>+<priority_key>`` where the
        sort key comes from the controller sort whitelist and the (single,
        optional) suffix from the priority whitelist."""
        if not isinstance(value, str) or not value:
            return False
        parts = value.split("+")
        if len(parts) > 2:
            return False
        sort_whitelist, priority_whitelist = self._controller_whitelists()
        if parts[0] not in sort_whitelist:
            return False
        if len(parts) == 2:
            return parts[1] in priority_whitelist
        return True

    @api.model
    def _parse_sort_tuple(self, value):
        """Parse ANY stored string into a validated ``(sort, priority list)``
        tuple; unknown tokens fall back to the safe default (never invalid)."""
        if not value:
            return ("date_desc", [])
        parts = str(value).split("+")
        sort_whitelist, priority_whitelist = self._controller_whitelists()
        sort = parts[0] if parts[0] in sort_whitelist else "date_desc"
        priority = [p for p in parts[1:] if p in priority_whitelist]
        return (sort, priority)

    def _get_sort_tuple(self):
        """This row's validated default sort as ``(sort_key, [priority])``."""
        self.ensure_one()
        return self._parse_sort_tuple(self.default_sort)

    # ------------------------------------------------------------------
    # PR-2f — save-time normalization (clamps + whitelist parsing)
    # ------------------------------------------------------------------

    @api.model
    def _clamp_int(self, value, low, high):
        """Clamp *value* into [low, high]; bad input degrades to *low*.

        Guards user-supplied settings values: a typo must degrade to the
        conservative bound, never raise (same policy as ``_safe_int``).
        """
        try:
            return min(high, max(low, int(value)))
        except (TypeError, ValueError):
            return low

    @api.model
    def _normalize_image_whitelist(self, text):
        """One lowercase trimmed email per line; blank and invalid lines are
        ignored; duplicates collapse (first occurrence wins, order kept)."""
        lines = []
        for raw in (text or "").splitlines():
            line = raw.strip().lower()
            if line and _EMAIL_LINE_RE.match(line) and line not in lines:
                lines.append(line)
        return "\n".join(lines)

    @api.model
    def _extract_email_address(self, value):
        """``'Name <a@b.c>'`` → ``'a@b.c'``; a bare address passes through;
        anything without an address yields ``''``."""
        raw = str(value or "").strip()
        if not raw:
            return ""
        bracket = _BRACKET_ADDR_RE.search(raw)
        if bracket:
            return bracket.group(1)
        bare = _BARE_ADDR_RE.search(raw)
        return bare.group(0) if bare else ""

    def _whitelist_addresses(self):
        self.ensure_one()
        return {
            line.strip()
            for line in (self.image_whitelist or "").splitlines()
            if line.strip()
        }

    def _image_sender_allowed(self, email):
        """Exact, case-insensitive sender match against the whitelist (the
        address inside ``Name <addr>`` forms is what counts)."""
        address = self._extract_email_address(email or "").lower()
        return bool(address) and address in self._whitelist_addresses()

    @api.model
    def _normalize_settings_vals(self, vals):
        """Apply the save-time contract to a vals dict (clamps for the numeric
        fields, whitelist normalization, IGNORED invalid default_sort)."""
        vals = dict(vals)
        if vals.get("messages_per_page") is not None:
            vals["messages_per_page"] = self._clamp_int(
                vals["messages_per_page"],
                MESSAGES_PER_PAGE_MIN,
                MESSAGES_PER_PAGE_MAX,
            )
        if vals.get("max_quote_levels") is not None:
            vals["max_quote_levels"] = self._clamp_int(
                vals["max_quote_levels"],
                MAX_QUOTE_LEVELS_MIN,
                MAX_QUOTE_LEVELS_MAX,
            )
        if vals.get("trash_retention_days") is not None:
            # PR-2h: the retention window is always 1..365 days.
            vals["trash_retention_days"] = self._clamp_int(
                vals["trash_retention_days"],
                TRASH_RETENTION_MIN_DAYS,
                TRASH_RETENTION_MAX_DAYS,
            )
        if vals.get("mark_read_after_seconds") is not None:
            # Below the NEVER sentinel (-1) there is nothing — clamp up.
            vals["mark_read_after_seconds"] = self._clamp_int(
                vals["mark_read_after_seconds"],
                MARK_READ_NEVER,
                MARK_READ_MAX_SECONDS,
            )
        if vals.get("default_sort") is not None and not self._is_valid_default_sort(
            vals["default_sort"]
        ):
            # Model-level policy: never STORE an invalid sort (ignored).
            vals.pop("default_sort")
        if "image_whitelist" in vals:
            vals["image_whitelist"] = self._normalize_image_whitelist(
                vals["image_whitelist"]
            )
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create(
            [self._normalize_settings_vals(vals) for vals in vals_list]
        )

    def write(self, vals):
        return super().write(self._normalize_settings_vals(vals))
