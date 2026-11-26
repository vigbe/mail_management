"""HTTP controllers for the webmail read endpoints (PR-1b-i) + send (PR-1c).

Design reference: openspec/changes/webmail-and-ai-assistant/design.md §F
All endpoints use ``auth='user'``; per-user isolation is enforced by the
ir.rules (domain on stored ``user_id``), so operating through
``request.env`` is sufficient.  Controllers never return ``credential*``
fields.
"""

import base64
import logging

from odoo import _, http  # type: ignore
from odoo.addons.mail_management.models.mail_management_settings import (  # type: ignore
    TRASH_RETENTION_DEFAULT_DAYS,
)
from odoo.exceptions import UserError  # type: ignore
from odoo.http import request  # type: ignore
from odoo.tools import plaintext2html  # type: ignore

_logger = logging.getLogger(__name__)


def _as_bool(value):
    """Strict boolean parse for request params.

    ``bool("false")`` is ``True`` (string truthiness) — never trust a raw
    typecast on user input. Accepts real bools, "true"/"false" (any case),
    and 1/0; anything else degrades to ``False``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "1"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("false", "0", ""):
        return False
    return False


#: Fields never exposed in any controller response.
_SENSITIVE_FIELDS = {
    "credential",
    "credential_encrypted",
    "oauth_refresh_token_encrypted",
}

#: Whitelisted sort orders for the message list (injection-safe).
_MESSAGE_ORDER_WHITELIST = ("date desc", "date asc")

#: PR-2a whitelisted ``sort`` keys → ORM order clauses (injection-safe:
#: the client sends a KEY, never a raw ORDER BY).
_MESSAGE_SORT_WHITELIST = {
    "date": "date asc",
    "date_desc": "date desc",
    "from": "email_from asc",
    "from_desc": "email_from desc",
    "subject": "subject asc",
    "subject_desc": "subject desc",
}

#: PR-2a priority toggles → ORM order clauses.  The priority groups are
#: prepended to the primary sort so unread/flagged messages rise to the
#: top while the chosen sort orders each group.
_MESSAGE_PRIORITY_WHITELIST = {
    "unread_first": "flag_seen asc",
    "flagged_first": "flag_flagged desc",
}

#: PR-2a quick filters → search domains.
_MESSAGE_FILTER_WHITELIST = {
    "unread": [("flag_seen", "=", False)],
    "flagged": [("flag_flagged", "=", True)],
    "attachments": [("has_attachment", "=", True)],
}


def _build_message_order(sort, order, priority):
    """Assemble the ORM ``order`` from WHITELISTS only (PR-2a).

    Precedence: a valid ``sort`` key wins; else the legacy PR-1b-ii
    ``order`` (if whitelisted); else the ``date desc`` default.  Priority
    clauses are prepended, a deterministic ``id desc`` tiebreak is
    appended.  Raw input is never interpolated into the clause string.
    """
    if sort in _MESSAGE_SORT_WHITELIST:
        primary = _MESSAGE_SORT_WHITELIST[sort]
    elif sort is None and order in _MESSAGE_ORDER_WHITELIST:
        primary = order
    else:
        primary = "date desc"
    if isinstance(priority, str):
        priority = [priority]
    parts = [
        _MESSAGE_PRIORITY_WHITELIST[key]
        for key in (priority or [])
        if key in _MESSAGE_PRIORITY_WHITELIST
    ]
    parts.append(primary)
    parts.append("id desc")
    return ", ".join(parts)


def _escape_like(value):
    """Escape LIKE wildcards (``%``, ``_`` and ``\\``) so PR-2d search input
    is matched LITERALLY (an unescaped ``100%`` would also match ``1004``).
    Postgres' default LIKE escape character is the backslash.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_domain(env, folder_id, query):
    """Mailbox-wide search domain for a sanitized query (PR-2d).

    ``folder_id`` only resolves the CURRENT mailbox (through ``request.env``
    so the per-user ir.rules apply — a forged id resolves nothing); the
    search then spans ALL folders of that mailbox across ``subject``,
    ``email_from``, ``email_to`` and ``body_text`` (ilike → case-insensitive,
    wildcards escaped).  ``None`` when the query is blank (folder view).
    """
    query = (query or "").strip()
    if not query:
        return None
    folder = env["mail.management.folder"].search([("id", "=", folder_id)], limit=1)
    if not folder:
        # Unresolvable folder (cross-user or deleted): no results, never a
        # cross-user leak.
        return [(0, "=", 1)]
    pattern = f"%{_escape_like(query)}%"
    return [
        ("mailbox_id", "=", folder.mailbox_id.id),
        "|",
        "|",
        "|",
        ("subject", "ilike", pattern),
        ("email_from", "ilike", pattern),
        ("email_to", "ilike", pattern),
        ("body_text", "ilike", pattern),
    ]


def _settings_payload(settings):
    """PR-2f + PR-2g: the settings fields every endpoint returns
    (``image_whitelist`` parsed to a list for the client)."""
    return {
        "allow_remote_images": settings.allow_remote_images,
        "signature_position": settings.signature_position or "below",
        "messages_per_page": settings.messages_per_page,
        "default_sort": settings.default_sort or "date_desc",
        "show_checkboxes": settings.show_checkboxes,
        "mark_read_after_seconds": settings.mark_read_after_seconds,
        "collapse_quoted_text": settings.collapse_quoted_text,
        "max_quote_levels": settings.max_quote_levels,
        "strip_message_styles": settings.strip_message_styles,
        "prefer_html": settings.prefer_html,
        "simple_attachment_list": settings.simple_attachment_list,
        "show_next_on_move_delete": settings.show_next_on_move_delete,
        "image_whitelist": [
            line.strip()
            for line in (settings.image_whitelist or "").splitlines()
            if line.strip()
        ],
        "compose_editor": settings.compose_editor or "html",
        "reply_save_folder": settings.reply_save_folder or "sent",
        "request_read_receipt": settings.request_read_receipt,
        "request_delivery_receipt": settings.request_delivery_receipt,
        "enable_spellcheck": settings.enable_spellcheck,
        "show_unread_badge": settings.show_unread_badge,
        "theme": settings.theme or "light",
        "trash_retention_days": settings.trash_retention_days
        or TRASH_RETENTION_DEFAULT_DAYS,
    }


class MailManagementController(http.Controller):
    """JSON-RPC + http endpoints for the OWL webmail client."""

    # ------------------------------------------------------------------
    # /mail_management/folders — list mailboxes + folders for current user
    # ------------------------------------------------------------------
    @http.route(
        "/mail_management/folders", type="jsonrpc", auth="user", methods=["POST"]
    )
    def get_folders(self, include_hidden=False):
        """List mailboxes + folders of the current user.

        PR-2g: folders with ``visible=False`` are OMITTED (the folder-tree
        payload); ``include_hidden=True`` returns EVERY folder together
        with its ``visible`` flag and the translated role labels — the
        Folders settings tab payload (mapping + hide/restore UI).
        """
        env = request.env
        include_hidden = _as_bool(include_hidden)
        role_labels = None
        if include_hidden:
            role_labels = env["mail.management.folder"]._settings_role_labels()
        mailboxes = env["mail.management.mailbox"].search([])
        result = []
        for mb in mailboxes:
            folders = []
            for folder in mb.folder_ids:
                if not folder.visible and not include_hidden:
                    continue
                folders.append(
                    {
                        "id": folder.id,
                        "name": folder.name or folder.imap_name,
                        "label": folder.display_name,
                        "imap_name": folder.imap_name,
                        "role": folder.role,
                        "visible": folder.visible,
                        "message_count": folder.message_count,
                        "unread_count": folder.unread_count,
                    }
                )
            entry = {"id": mb.id, "name": mb.name, "folders": folders}
            if include_hidden:
                entry["role_labels"] = role_labels
            result.append(entry)
        return result

    # ------------------------------------------------------------------
    # /mail_management/messages — paginated message list for a folder
    # ------------------------------------------------------------------
    @http.route(
        "/mail_management/messages", type="jsonrpc", auth="user", methods=["POST"]
    )
    def get_messages(
        self,
        folder_id,
        limit=None,
        offset=0,
        order="date desc",
        sort=None,
        priority=None,
        filter=None,
        search=None,
    ):
        """Paginated message list for a folder (PR-2a sorting/filtering).

        All list parameters are WHITELIST-LOOKED-UP, never interpolated:

        * ``order`` — legacy PR-1b-ii ('date desc' | 'date asc');
        * ``sort`` — date | date_desc | from | from_desc | subject |
          subject_desc (wins over ``order`` when valid);
        * ``priority`` — 'unread_first' and/or 'flagged_first' (string or
          list); the groups are applied on top of the primary sort;
        * ``filter`` — unread | flagged | attachments (quick filters);
        * ``search`` — PR-2d local search: ilike across subject,
          email_from, email_to and body_text of the CURRENT MAILBOX (all
          its folders; wildcards in the input are literal).  Blank →
          ignored (plain folder view);
        * ``limit`` — PR-2f: when the request passes NO explicit limit the
          caller's ``messages_per_page`` setting applies (50 without a
          settings row).

        Anything outside a whitelist falls back to the safe default.
        """
        env = request.env
        if limit is None:
            # PR-2f: page size comes from the caller's per-user settings
            # (search-only: the list is a hot path, never auto-creates).
            settings = env["mail.management.settings"].search(
                [("user_id", "=", env.user.id)], limit=1
            )
            limit = settings.messages_per_page or 50
        domain = _search_domain(env, folder_id, search)
        if domain is None:
            domain = [("folder_id", "=", folder_id)]
        if filter in _MESSAGE_FILTER_WHITELIST:
            domain += _MESSAGE_FILTER_WHITELIST[filter]
        messages = env["mail.management.message"].search(
            domain,
            limit=limit,
            offset=offset,
            order=_build_message_order(sort, order, priority),
        )
        return [
            {
                "id": m.id,
                "uid": m.uid,
                "subject": m.subject or request.env._("(no subject)"),
                "email_from": m.email_from,
                "date": m.date.isoformat() if m.date else None,
                "flag_seen": m.flag_seen,
                "flag_flagged": m.flag_flagged,
                "has_attachment": m.has_attachment,
            }
            for m in messages
        ]

    # ------------------------------------------------------------------
    # /mail_management/message_body — single message body + metadata
    # ------------------------------------------------------------------
    @http.route(
        "/mail_management/message_body", type="jsonrpc", auth="user", methods=["POST"]
    )
    def get_message_body(self, message_id):
        env = request.env
        message = env["mail.management.message"].search(
            [("id", "=", message_id)], limit=1
        )
        if not message:
            return {}
        return {
            "id": message.id,
            "subject": message.subject or "",
            "email_from": message.email_from or "",
            "email_to": message.email_to or "",
            "email_cc": message.email_cc or "",
            "date": message.date.isoformat() if message.date else None,
            "flag_seen": message.flag_seen,
            "flag_flagged": message.flag_flagged,
            "body_html": message.body_html or plaintext2html(message.body_text or ""),
            "body_text": message.body_text or "",
            "in_reply_to": message.in_reply_to or "",
            "references": message.references or "",
            "attachments": [
                {
                    "id": a.id,
                    "name": a.name,
                    "file_size": a.file_size,
                    "mimetype": a.mimetype or "",
                    "content_id": a.content_id or "",
                    "inline": bool(a.inline),
                }
                for a in message.attachment_ids
            ],
        }

    # ------------------------------------------------------------------
    # /mail_management/refresh — on-demand sync (idempotent, worker-safe)
    # ------------------------------------------------------------------
    @http.route(
        "/mail_management/refresh", type="jsonrpc", auth="user", methods=["POST"]
    )
    def refresh(self, mailbox_id=None):
        env = request.env
        if mailbox_id:
            mailbox = env["mail.management.mailbox"].search(
                [("id", "=", mailbox_id)], limit=1
            )
            if mailbox:
                mailbox._sync_mailbox()
        else:
            env["mail.management.mailbox"]._cron_sync_mailboxes()
        return {"success": True}

    # ------------------------------------------------------------------
    # /mail_management/set_flags — mark seen/flagged
    # ------------------------------------------------------------------
    @http.route(
        "/mail_management/set_flags", type="jsonrpc", auth="user", methods=["POST"]
    )
    def set_flags(self, message_id, **flags):
        env = request.env
        message = env["mail.management.message"].search(
            [("id", "=", message_id)], limit=1
        )
        if not message:
            return {"success": False, "error": "not_found"}
        vals = {}
        if "seen" in flags:
            vals["flag_seen"] = bool(flags["seen"])
        if "flagged" in flags:
            vals["flag_flagged"] = bool(flags["flagged"])
        if vals:
            message.write(vals)
        return {"success": True}

    # ------------------------------------------------------------------
    # /mail_management/message/set_flags — bulk seen/flagged (PR-2a)
    # ------------------------------------------------------------------
    @http.route(
        "/mail_management/message/set_flags",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def message_set_flags(self, message_ids, flags=None):
        """Bulk ``\\Seen``/``\\Flagged`` update (star/unstar, read/unread).

        ``message_ids`` is a list; ids the caller may not see are filtered
        out by the per-user ir.rules (silently ignored — a forged id can
        never flip another user's flags).  ``flags`` is
        ``{'seen': bool}`` and/or ``{'flagged': bool}``; anything else
        raises ``UserError`` (model-side whitelist).

        The update is LOCAL-FIRST with a best-effort IMAP mirror
        (``mailbox._set_message_flags`` POLICY), so the endpoint never
        fails because of the IMAP server.
        """
        env = request.env
        ids = list(message_ids or [])
        messages = env["mail.management.message"].search([("id", "in", ids)])
        updated = env["mail.management.message"]
        for mailbox in messages.mapped("mailbox_id"):
            own = messages.filtered(lambda m, mb=mailbox: m.mailbox_id == mb)
            updated |= mailbox._set_message_flags(own.ids, flags or {})
        return {"success": True, "updated": len(updated)}

    # ------------------------------------------------------------------
    # /mail_management/folder/{create,rename,delete} (PR-2b, IMAP-first)
    #
    # Folder CRUD mirrors the server shape: the model methods run the IMAP
    # operation FIRST and raise ``UserError`` on failure (no local change).
    # Per-user isolation: every search goes through ``request.env`` so the
    # ir.rules apply — a forged cross-user id simply does not resolve.
    # ------------------------------------------------------------------
    @http.route(
        "/mail_management/folder/create",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def folder_create(self, mailbox_id, name):
        """Create a folder in a mailbox (IMAP CREATE → local mirror)."""
        env = request.env
        mailbox = env["mail.management.mailbox"].search(
            [("id", "=", mailbox_id)], limit=1
        )
        if not mailbox:
            raise UserError(request.env._("Mailbox not found."))
        folder = mailbox._create_folder(name)
        return {"success": True, "folder_id": folder.id}

    @http.route(
        "/mail_management/folder/rename",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def folder_rename(self, folder_id, name):
        """Rename a folder (IMAP RENAME → local imap_name/name update)."""
        env = request.env
        folder = env["mail.management.folder"].search([("id", "=", folder_id)], limit=1)
        if not folder:
            raise UserError(request.env._("Folder not found."))
        folder.mailbox_id._rename_folder(folder, name)
        return {"success": True}

    @http.route(
        "/mail_management/folder/delete",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def folder_delete(self, folder_id):
        """Delete a folder with GMail-style SOFT-DELETE semantics (PR-2h).

        The confirmation is a CLIENT concern; the server relocates and
        reports a summary: ``moved_count`` messages went to the Trash
        folder (retained per the owner's ``trash_retention_days``
        setting, then auto-purged by the daily cron) and the folder was
        removed.  Mapped system folders are refused (unmap first), and
        so is a folder with messages when no Trash folder is mapped.
        """
        env = request.env
        folder = env["mail.management.folder"].search([("id", "=", folder_id)], limit=1)
        if not folder:
            raise UserError(request.env._("Folder not found."))
        result = folder.mailbox_id._delete_folder(folder)
        return {
            "success": True,
            "moved_count": result["moved_count"],
            "folder_removed": True,
        }

    # ------------------------------------------------------------------
    # /mail_management/folder/{set_visible,set_role} (PR-2g — SnappyMail
    # folder visibility + system-folder mapping).  Local-write settings:
    # no IMAP round-trip (the role/visibility are OUR metadata on top of
    # the server folders; the sync engine keeps consuming them).
    # ------------------------------------------------------------------
    @http.route(
        "/mail_management/folder/set_visible",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def folder_set_visible(self, folder_id, visible=True):
        """Hide a folder from / restore it to the folder tree.

        Hidden folders keep syncing — only the tree payload omits them
        (Settings ▸ Folders lists them and restores them).
        """
        env = request.env
        folder = env["mail.management.folder"].search([("id", "=", folder_id)], limit=1)
        if not folder:
            raise UserError(request.env._("Folder not found."))
        folder.write({"visible": bool(visible)})
        return {"success": True, "visible": folder.visible}

    @http.route(
        "/mail_management/folder/set_role",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def folder_set_role(self, folder_id, role=""):
        """Override a folder's system role (PR-2g mapping API).

        ``role`` ∈ {'', 'sent', 'drafts', 'trash', 'junk', 'archive',
        'all'}; '' clears to 'other'.  Validation, the fixed Inbox rule
        and per-mailbox swap-uniqueness live on the model
        (``_set_system_role``); a forged cross-user id does not resolve
        (ir.rules) → UserError.
        """
        env = request.env
        folder = env["mail.management.folder"].search([("id", "=", folder_id)], limit=1)
        if not folder:
            raise UserError(request.env._("Folder not found."))
        demoted = folder._set_system_role(role)
        return {
            "success": True,
            "role": folder.role,
            "demoted_folder_id": demoted.id or False,
            "demoted_folder_name": demoted.name or "",
        }

    # ------------------------------------------------------------------
    # /mail_management/message/{move,delete} (PR-2b, IMAP-first + trash)
    # ------------------------------------------------------------------
    @http.route(
        "/mail_management/message/move",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def message_move(self, message_ids, folder_id):
        """Move messages to another folder of the SAME mailbox.

        Cross-user ids do not resolve (ir.rules) and are silently ignored;
        messages from OTHER mailboxes cannot move into the destination and
        are ignored as well (the destination's mailbox owns the move).
        """
        env = request.env
        dest = env["mail.management.folder"].search([("id", "=", folder_id)], limit=1)
        if not dest:
            raise UserError(request.env._("Folder not found."))
        messages = env["mail.management.message"].search(
            [("id", "in", list(message_ids or []))]
        )
        own = messages.filtered(lambda m, d=dest: m.mailbox_id == d.mailbox_id)
        moved = dest.mailbox_id._move_messages(own, dest)
        return {"success": True, "moved": len(moved)}

    @http.route(
        "/mail_management/message/delete",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def message_delete(self, message_ids):
        """Delete messages with server-side Trash semantics.

        Outside Trash → moved to the mailbox's Trash folder; already in
        Trash (or no Trash folder) → permanently deleted.  Cross-user ids
        do not resolve (ir.rules) and are ignored.
        """
        env = request.env
        messages = env["mail.management.message"].search(
            [("id", "in", list(message_ids or []))]
        )
        moved = purged = env["mail.management.message"]
        for mailbox in messages.mapped("mailbox_id"):
            own = messages.filtered(lambda m, mb=mailbox: m.mailbox_id == mb)
            result = mailbox._delete_messages_to_trash(own)
            moved |= result["moved_to_trash"]
            purged |= result["purged"]
        return {
            "success": True,
            "deleted": len(moved) + len(purged),
            "moved_to_trash": len(moved),
            "purged": len(purged),
        }

    # ------------------------------------------------------------------
    # /mail_management/signatures — compose signature picker (PR-2c)
    # ------------------------------------------------------------------
    @http.route(
        "/mail_management/signatures",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def get_signatures(self, mailbox_id=None):
        """Signatures available to the CURRENT USER for a mailbox.

        Returns the user's mailbox-specific signatures for that mailbox
        plus their user-global ones (ir.rules keep other users' records
        invisible — a forged mailbox id simply does not resolve and the
        user still gets their own globals), with the resolved default id
        for the compose picker.
        """
        env = request.env
        signature_model = env["mail.management.signature"]
        # With a mailbox: its signatures + the user's globals.  Without
        # one: every signature the user owns (ir.rules scope to own).
        domain = [("mailbox_id", "in", [False, mailbox_id])] if mailbox_id else []
        signatures = signature_model.search(domain, order="mailbox_id desc, name")
        mailbox = (
            env["mail.management.mailbox"].search([("id", "=", mailbox_id)], limit=1)
            if mailbox_id
            else env["mail.management.mailbox"]
        )
        default = (
            mailbox._get_default_signature()
            if mailbox
            else signature_model.search(
                [
                    ("mailbox_id", "=", False),
                    ("is_default", "=", True),
                ],
                limit=1,
            )
        )
        return {
            "signatures": [
                {
                    "id": sig.id,
                    "name": sig.name,
                    # Stored sanitized-on-write; str() drops the Markup.
                    "html_body": str(sig.html_body or ""),
                    "is_default": sig.is_default,
                    "mailbox_id": sig.mailbox_id.id or False,
                    "mailbox_name": sig.mailbox_id.name or "",
                }
                for sig in signatures
            ],
            "default_id": default.id or False,
        }

    # ------------------------------------------------------------------
    # /mail_management/settings[.save] — per-user webmail settings
    # (PR-2e + PR-2f)
    #
    # GET auto-creates the caller's singleton; both endpoints pin the
    # domain to ``env.user`` so even a manager (ir.rule: see all) only
    # ever reads/writes their OWN row through the webmail.
    # ------------------------------------------------------------------
    @http.route(
        "/mail_management/settings",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def get_settings(self):
        """Current user's webmail settings (auto-creating the singleton)."""
        env = request.env
        settings = env["mail.management.settings"]._get_or_create_for(env.user)
        return _settings_payload(settings)

    @http.route(
        "/mail_management/settings/save",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def save_settings(
        self,
        allow_remote_images=None,
        signature_position=None,
        messages_per_page=None,
        default_sort=None,
        show_checkboxes=None,
        mark_read_after_seconds=None,
        collapse_quoted_text=None,
        max_quote_levels=None,
        strip_message_styles=None,
        prefer_html=None,
        simple_attachment_list=None,
        show_next_on_move_delete=None,
        image_whitelist=None,
        compose_editor=None,
        reply_save_folder=None,
        request_read_receipt=None,
        request_delivery_receipt=None,
        enable_spellcheck=None,
        show_unread_badge=None,
        theme=None,
        trash_retention_days=None,
    ):
        """Write the current user's settings (partial updates allowed).

        ``signature_position`` is whitelisted to below|above and
        ``default_sort`` to the controller sort whitelist keys (with an
        optional priority suffix); anything else raises ``UserError``
        (never stored, never interpolated).  The numeric fields are
        CLAMPED by the model's save-time normalization, and
        ``image_whitelist`` is normalized (one lowercase trimmed email
        per line, invalid/blank lines ignored).
        """
        env = request.env
        if signature_position is not None and signature_position not in (
            "below",
            "above",
        ):
            raise UserError(request.env._("Invalid signature position."))
        settings_model = env["mail.management.settings"]
        if default_sort is not None and not settings_model._is_valid_default_sort(
            default_sort
        ):
            raise UserError(request.env._("Invalid default sort."))
        # PR-2g selection whitelists (never stored, never interpolated).
        if compose_editor is not None and compose_editor not in ("html", "plain"):
            raise UserError(request.env._("Invalid compose editor."))
        if reply_save_folder is not None and reply_save_folder not in (
            "sent",
            "same",
        ):
            raise UserError(request.env._("Invalid reply save folder."))
        if theme is not None and theme not in (
            "light",
            "dark",
            "night",
            "sepia",
            "ocean",
            "forest",
        ):
            raise UserError(request.env._("Invalid theme."))
        settings = settings_model._get_or_create_for(env.user)
        vals = {}
        if allow_remote_images is not None:
            vals["allow_remote_images"] = _as_bool(allow_remote_images)
        if signature_position is not None:
            vals["signature_position"] = signature_position
        if messages_per_page is not None:
            vals["messages_per_page"] = messages_per_page
        if default_sort is not None:
            vals["default_sort"] = default_sort
        if compose_editor is not None:
            vals["compose_editor"] = compose_editor
        if reply_save_folder is not None:
            vals["reply_save_folder"] = reply_save_folder
        if theme is not None:
            vals["theme"] = theme
        for key, value in (
            ("show_checkboxes", show_checkboxes),
            ("collapse_quoted_text", collapse_quoted_text),
            ("strip_message_styles", strip_message_styles),
            ("prefer_html", prefer_html),
            ("simple_attachment_list", simple_attachment_list),
            ("show_next_on_move_delete", show_next_on_move_delete),
            ("request_read_receipt", request_read_receipt),
            ("request_delivery_receipt", request_delivery_receipt),
            ("enable_spellcheck", enable_spellcheck),
            ("show_unread_badge", show_unread_badge),
        ):
            if value is not None:
                vals[key] = _as_bool(value)
        if mark_read_after_seconds is not None:
            vals["mark_read_after_seconds"] = mark_read_after_seconds
        if max_quote_levels is not None:
            vals["max_quote_levels"] = max_quote_levels
        if trash_retention_days is not None:
            # PR-2h: clamped 1..365 by the model normalization.
            vals["trash_retention_days"] = trash_retention_days
        if image_whitelist is not None:
            vals["image_whitelist"] = image_whitelist
        if vals:
            settings.write(vals)
        return {"success": True, **_settings_payload(settings)}

    # ------------------------------------------------------------------
    # /mail_management/send — compose: reply / reply-all / forward (PR-1c)
    # ------------------------------------------------------------------
    @http.route("/mail_management/send", type="jsonrpc", auth="user", methods=["POST"])
    def send(
        self,
        mailbox_id,
        to,
        cc="",
        bcc="",
        subject="",
        body_text="",
        body_html="",
        mode="reply",
        reply_to_message_id=None,
        attachments=None,
    ):
        r"""Send a reply / reply-all / forward (design §D.1, §F).

        Per-user scoping: the mailbox and (when given) the original message
        are searched through ``request.env`` so the ir.rules apply; a forged
        cross-user id simply does not resolve → UserError.  ``mode`` drives
        the Re:/Fwd: subject normalization; threading and the IMAP
        ``\Answered`` mirroring are derived on the model side from
        ``reply_to_message`` + ``forward``.

        :param attachments: ``[{'name', 'datas' (base64), 'mimetype'}]``
            uploads — persisted to ir.attachment/filestore only after the
            SMTP send succeeded (no partial state on failure).
        :param body_html: rich-text body from the compose editor (PR-2c).
            SANITIZED server-side before it leaves — never trusted.
        """
        env = request.env
        mailbox = env["mail.management.mailbox"].search(
            [("id", "=", mailbox_id)], limit=1
        )
        if not mailbox:
            raise UserError(request.env._("Mailbox not found."))
        # Second line of defense (design §F) on top of the ir.rule.
        if mailbox.user_id != env.user:
            raise UserError(request.env._("Mailbox not found."))

        original = env["mail.management.message"]
        if reply_to_message_id:
            original = env["mail.management.message"].search(
                [("id", "=", reply_to_message_id)], limit=1
            )
            if not original:
                raise UserError(request.env._("Original message not found."))
            if original.user_id != env.user:
                raise UserError(request.env._("Original message not found."))

        payloads = []
        for att in attachments or []:
            payloads.append(
                {
                    "name": att.get("name") or "attachment",
                    "content": base64.b64decode(att.get("datas") or ""),
                    "mimetype": att.get("mimetype") or "application/octet-stream",
                }
            )

        result = mailbox._send_message(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            attachments=payloads,
            reply_to_message=original or None,
            forward=(mode == "forward"),
        )
        return {
            "success": True,
            "message_id": result["message"].id or False,
            "warning": result["append_warning"] or False,
        }

    # ------------------------------------------------------------------
    # /mail_management/attachment/<id> — download from filestore
    # ------------------------------------------------------------------
    @http.route("/mail_management/attachment/<int:attach_id>", type="http", auth="user")
    def download_attachment(self, attach_id):
        env = request.env
        attach = env["mail.management.attachment"].search(
            [("id", "=", attach_id)], limit=1
        )
        if not attach:
            return request.not_found()
        ir_attach = attach.ir_attachment_id
        content = base64.b64decode(ir_attach.datas)
        headers = [
            (
                "Content-Type",
                attach.mimetype or "application/octet-stream",
            ),
            (
                "Content-Disposition",
                f'attachment; filename="{attach.name}"',
            ),
        ]
        return request.make_response(content, headers)
