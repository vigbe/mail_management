"""Mailbox configuration model — ``mail.management.mailbox``.

Replaces the placeholder ``mail.management`` scaffold (PR-1a-2).
Per-user IMAP/SMTP mailbox configuration with Fernet encrypt-at-rest
credentials, two group-scoped ir.rule isolation, and the IMAP sync
backbone (PR-1b-i).

Design reference: openspec/changes/webmail-and-ai-assistant/design.md
  §A.1 (field-level), §B.2 (credential pattern), §B.3 (ir.rule isolation),
  §B.5 (redaction), §C (sync design), §C/D (client seam design).
"""

import base64
import email
import email.utils
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage

from odoo import _, api, fields, models  # type: ignore
from odoo.addons.mail_management.lib import (  # type: ignore
    FernetCrypto,
    ImapClient,
    SmtpClient,
)
from odoo.addons.mail_management.models.mail_management_settings import (  # type: ignore
    TRASH_RETENTION_DEFAULT_DAYS,
)
from odoo.exceptions import UserError  # type: ignore
from odoo.tools import html2plaintext  # type: ignore
from odoo.tools.mail import html_sanitize  # type: ignore

_logger = logging.getLogger(__name__)

#: Constant mask returned by the credential compute (never decrypts).
_CREDENTIAL_MASK = "**********"


class MailManagementMailbox(models.Model):
    _name = "mail.management.mailbox"
    _description = "Mailbox Configuration"
    _inherit = ["mail.thread"]  # noqa: RUF012
    _order = "name"
    _check_company_auto = True

    # ------------------------------------------------------------------
    # Fields (design A.1)
    # ------------------------------------------------------------------
    name = fields.Char(required=True, tracking=True)
    user_id = fields.Many2one(
        "res.users",
        string="Owner",
        required=True,
        default=lambda self: self.env.user,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    provider_type = fields.Selection(
        [
            ("generic", "Otro (configuración manual)"),
            ("gmail", "Gmail"),
            ("oauth_ms365", "Outlook / Hotmail / Live"),
        ],
        required=True,
        default="generic",
    )

    # IMAP
    imap_host = fields.Char(required=True)
    imap_port = fields.Integer(default=993)
    imap_use_ssl = fields.Boolean(default=True)
    imap_user = fields.Char(required=True)

    # SMTP
    smtp_host = fields.Char(required=True)
    smtp_port = fields.Integer(default=587)
    smtp_use_tls = fields.Boolean(default=True)
    from_email = fields.Char(required=True)

    # Credential fields (design B.2 — transient compute+inverse,
    # ciphertext-only at rest)
    credential = fields.Char(
        compute="_compute_credential",
        inverse="_inverse_credential",
        password=True,
        help="Type the IMAP/SMTP app-password here. "
        "It is encrypted at rest and never displayed again.",
    )
    credential_encrypted = fields.Char(
        password=True,
        copy=False,
        groups="mail_management.group_mail_management_manager",
        index=True,
    )
    # Reserved for later OAuth PRs (design E); nullable, not used in PR-1a-2.
    oauth_refresh_token_encrypted = fields.Char(
        password=True,
        copy=False,
        groups="mail_management.group_mail_management_manager",
    )

    # Sync state
    sent_folder_name = fields.Char(default="Sent")
    last_sync = fields.Datetime()

    # Relations (PR-1b-i)
    folder_ids = fields.One2many("mail.management.folder", "mailbox_id")
    synced_message_ids = fields.One2many("mail.management.message", "mailbox_id")
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("configured", "Configured"),
            ("active", "Active"),
            ("error", "Error"),
        ],
        default="draft",
        tracking=True,
    )
    sync_error = fields.Text()
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # Constraints & indexes (Odoo 19: models.Constraint / models.Index)
    # ------------------------------------------------------------------
    _mailbox_uniq = models.Constraint(
        "UNIQUE(user_id, imap_host, imap_user)",
        "A mailbox for this (user, IMAP host, IMAP user) already exists.",
    )
    _mailbox_user_company_idx = models.Index("(user_id, company_id)")

    # ------------------------------------------------------------------
    # Field-parameter whitelist (password=True escape hatch — Odoo 19)
    # ------------------------------------------------------------------
    @api.model
    def _valid_field_parameter(self, field, name):
        """Allow ``password=True`` on our credential fields."""
        if name == "password":
            return True
        return super()._valid_field_parameter(field, name)

    # ------------------------------------------------------------------
    # Credential transient compute + inverse (design B.2)
    # ------------------------------------------------------------------
    @api.depends("credential_encrypted")
    def _compute_credential(self):
        """Return a constant mask — **never** decrypt the ciphertext."""
        for rec in self:
            rec.credential = (
                _CREDENTIAL_MASK if rec.sudo().credential_encrypted else False
            )

    def _inverse_credential(self):
        """Encrypt plaintext credential to ``credential_encrypted``.

        Uses ``sudo()`` so a non-manager owner can still set the
        credential; only the resulting ciphertext is persisted.
        The display value is explicitly set because the ``sudo()`` write
        does not trigger the compute recompute in the caller's environment.
        """
        for rec in self:
            plain = rec.credential
            if plain and plain != _CREDENTIAL_MASK:
                rec.sudo().credential_encrypted = rec._encrypt_credential(plain)
            # Always (re)set the display value (sudo write doesn't propagate).
            rec.credential = (
                _CREDENTIAL_MASK if rec.sudo().credential_encrypted else False
            )

    def _encrypt_credential(self, plaintext):
        """Encrypt *plaintext* using the resolved Fernet master key."""
        icp = self.env["ir.config_parameter"].sudo()
        key = FernetCrypto.get_master_key(icp.get_str, icp.set_str)
        return FernetCrypto.encrypt(plaintext, key=key)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault("user_id", self.env.user.id)
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Delete guard (design A.1 — @api.ondelete, NOT unlink override)
    # ------------------------------------------------------------------
    @api.ondelete(at_uninstall=False)
    def _unlink_if_active(self):
        if any(rec.state == "active" for rec in self):
            raise UserError(
                _("Cannot delete a mailbox in 'Active' state. Set it to Draft first.")
            )

    # ------------------------------------------------------------------
    # Credential plaintext accessor (design B.2, B.5)
    # ------------------------------------------------------------------
    @api.private
    def _get_credential_plaintext(self):
        """Return the decrypted credential.

        .. warning::

            The returned value is a **plaintext secret**.  It must only be
            held in a local variable and never logged, serialised, or
            stored.
        """
        self.ensure_one()
        token = self.sudo().credential_encrypted
        if not token:
            raise UserError(_("No credential is set for this mailbox."))
        icp = self.env["ir.config_parameter"].sudo()
        key = FernetCrypto.get_master_key(icp.get_str, icp.set_str)
        return FernetCrypto.decrypt(token, key=key)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _safe_int(self, value, default):
        """Coerce *value* to int, falling back to *default* on bad input.

        Guards admin-editable system parameters: a typo like ``30s`` in
        ``mail_management.*`` must degrade to the default instead of
        crashing the cron or the connection test.
        """
        try:
            result = int(value)
        except (TypeError, ValueError):
            result = default
        return result if result > 0 else default

    def _get_imap_timeout(self):
        """Return the configured IMAP socket timeout (default 30 s)."""
        return self._safe_int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_str("mail_management.imap_timeout", 30),
            30,
        )

    # ------------------------------------------------------------------
    # Connection test (design C/D, B.5)
    # ------------------------------------------------------------------
    @api.onchange("provider_type")
    def _onchange_provider_type(self):
        """Preset the server parameters for well-known providers so
        non-technical users only type email + password."""
        presets = {
            "gmail": {
                "imap_host": "imap.gmail.com",
                "imap_port": 993,
                "imap_use_ssl": True,
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
                "smtp_use_tls": True,
            },
            "oauth_ms365": {
                "imap_host": "outlook.office365.com",
                "imap_port": 993,
                "imap_use_ssl": True,
                "smtp_host": "smtp-mail.outlook.com",
                "smtp_port": 587,
                "smtp_use_tls": True,
            },
        }
        preset = presets.get(self.provider_type)
        if preset:
            for field_name, value in preset.items():
                setattr(self, field_name, value)

    @api.onchange("from_email")
    def _onchange_from_email(self):
        """One visible email field: it seeds the IMAP user and, when the
        name is empty, the mailbox label too."""
        if self.from_email:
            if not self.imap_user:
                self.imap_user = self.from_email
            if not self.name:
                self.name = self.from_email

    @api.model
    def _connection_error_guidance(self, message):
        """Structured guidance for ACTIONABLE provider policy errors.

        Returns ``{intro, steps, link_url, link_label, title}`` for
        errors the USER can fix (app-password flows), else ``None``.
        The wizard renders steps; the plain hint derives from it.
        """
        text = message or ""
        if "Application-specific password required" in text:
            if "smtp-mail.outlook.com" in text or "office365" in text:
                return {
                    "title": "Conectá tu Outlook con una contraseña de aplicación",
                    "intro": "Por seguridad, Microsoft no acepta tu "
                    "contraseña habitual en aplicaciones externas. "
                    "Necesitás generar una contraseña de aplicación.",
                    "steps": [
                        "Entrá a account.live.com/passwords con tu cuenta "
                        "Microsoft (Outlook, Hotmail o Live).",
                        "Creá una nueva contraseña de aplicación (por "
                        "ejemplo «Odoo») y copiala.",
                        "Pegala en «Contraseña de correo» y apretá «Probar conexión».",
                    ],
                    "link_url": "https://account.live.com/passwords",
                    "link_label": "Abrir contraseñas de aplicación de Microsoft",
                }
            return {
                "title": "Conectá tu Gmail con una contraseña de aplicación",
                "intro": "Por seguridad, Google no acepta tu contraseña "
                "habitual en aplicaciones externas como este correo "
                "web. Necesitás una contraseña exclusiva de 16 "
                "caracteres — generarla lleva 2 minutos.",
                "steps": [
                    "Verificá que la «Verificación en 2 pasos» esté activada "
                    "en tu cuenta de Google (myaccount.google.com → "
                    "Seguridad). Sin ella, Google no ofrece contraseñas de "
                    "aplicación.",
                    "Entrá a myaccount.google.com/apppasswords, escribí un "
                    "nombre (por ejemplo «Odoo») y tocá «Crear».",
                    "Copiá los 16 caracteres que aparecen — solo se muestran "
                    "una vez; si los perdés, generás otra.",
                    "Volvé al formulario, pegala en «Contraseña de correo» y "
                    "apretá «Probar conexión».",
                ],
                "link_url": "https://myaccount.google.com/apppasswords",
                "link_label": "Abrir contraseñas de aplicación de Google",
            }
        return None

    @api.model
    def _connection_error_hint(self, message):
        """Plain-text hint (toast fallback) derived from the guidance."""
        text = message or ""
        guidance = self._connection_error_guidance(text)
        if guidance:
            numbered = " ".join(
                f"{index}) {step}"
                for index, step in enumerate(guidance["steps"], start=1)
            )
            return guidance["intro"] + " " + numbered
        if "INVALID CREDENTIALS" in text or "authentication failed" in text.lower():
            return _(
                "El usuario o la contraseña son incorrectos. Revisá que el correo "
                "esté completo y que la contraseña sea la correcta (en "
                "Gmail/Outlook: la de aplicación, no la habitual)."
            )
        if "Name or service not known" in text or "Errno -2" in text:
            server = "SMTP" if "SMTP" in text else "IMAP"
            return (
                _(
                    "El nombre del servidor %s no existe o está mal escrito "
                    "(error de DNS). Andá a «Servidores (avanzado) ▸ Salida — "
                    "SMTP» y verificá que esté exacto y sin espacios: Gmail es "
                    "smtp.gmail.com y Outlook es smtp-mail.outlook.com (sin "
                    "«https://» y sin mayúsculas)."
                )
                % server
            )
        return ""

    def action_test_connection(self):
        """Test IMAP + SMTP connectivity with the stored credential.

        :returns: ``{'success': bool, 'message': str}`` — the message is
                  **redacted**; the plaintext credential never appears.
        """
        self.ensure_one()
        plain = self._get_credential_plaintext()
        errors = []

        discovered = 0
        # --- IMAP -----------------------------------------------------
        try:
            with ImapClient(
                self.imap_host,
                self.imap_port,
                self.imap_use_ssl,
                self.imap_user,
                plain,
                timeout=self._get_imap_timeout(),
            ) as imap:
                # connect + login succeeded
                # PR-2j UX: auto-discover folders on the FIRST successful
                # connection (best-effort — a discovery glitch never fails the test).
                if not self.folder_ids:
                    try:
                        discovered = len(
                            self._create_missing_folders(imap.list_folders())
                        )
                    except Exception:  # noqa: BLE001
                        _logger.warning(
                            "auto folder discovery failed for mailbox %s",
                            self.id,
                            exc_info=True,
                        )
        except Exception as exc:  # noqa: BLE001
            errors.append("IMAP: " + FernetCrypto.redact(str(exc), plain))

        # --- SMTP -----------------------------------------------------
        try:
            with SmtpClient(
                self.smtp_host,
                self.smtp_port,
                self.smtp_use_tls,
                self.imap_user,
                plain,
            ):
                pass
        except Exception as exc:  # noqa: BLE001
            errors.append("SMTP: " + FernetCrypto.redact(str(exc), plain))

        if errors:
            message = "; ".join(errors)
            # PR-2j UX: ACTIONABLE provider errors open a MODAL WIZARD with
            # step-by-step instructions (a toast auto-dismisses and users
            # lose the steps — user request 2026-09).
            guidance = self._connection_error_guidance(message)
            if guidance:
                wizard_model = self.env["mail.management.connection.wizard"]
                wizard = wizard_model.create(
                    {
                        "mailbox_id": self.id,
                        "title": guidance["title"],
                        "message": wizard_model.build_message(guidance),
                        "link_url": guidance["link_url"],
                        "link_label": guidance["link_label"],
                        "raw_error": message,
                    }
                )
                return {
                    "type": "ir.actions.act_window",
                    "name": _("Connection Test"),
                    "res_model": "mail.management.connection.wizard",
                    "view_mode": "form",
                    "res_id": wizard.id,
                    "target": "new",
                }
            hint = self._connection_error_hint(message)
            if hint:
                message = hint + "\n\n" + message
        else:
            message = _("Connection successful.")
            if self.state == "draft" and self.folder_ids:
                self.state = "active"
                message += _(" Mailbox activated for sync.")

            if discovered:
                message = (
                    _("Connection successful. %s folders discovered.") % discovered
                )
        return {
            # ir.actions.client display_notification → the form button
            # shows the result to the user (PR-1b-ii UI wiring).
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Connection Test"),
                "message": message,
                "type": "success" if not errors else "warning",
                "sticky": False,
            },
            # Programmatic contract kept (PR-1a tests / callers).
            "success": not errors,
            "message": message,
        }

    # ------------------------------------------------------------------
    # Send / reply / forward (design §D.1 — PR-1c)
    # ------------------------------------------------------------------

    @property
    def _sent_append_failed_warning(self):
        """Surfaced (never swallowed) when the Sent-copy APPEND fails after a
        successful SMTP send — must NOT read as if the send failed."""
        return _("Message sent, but could not be saved to the Sent folder.")

    @property
    def _no_sent_folder_warning(self):
        return _("Message sent, but no Sent folder is configured for this mailbox.")

    @staticmethod
    def _compose_subject(subject, mode):
        """Normalize the subject prefix for *mode* (no double prefixes).

        ``reply``/``reply_all`` → ``Re: …``; ``forward`` → ``Fwd: …``;
        ``plain`` keeps the subject untouched.  An already-prefixed subject
        (any casing) is left as-is so users never see ``Re: Re: …``.
        """
        subject = (subject or "").strip()
        if not subject:
            return subject
        if mode in ("reply", "reply_all"):
            return subject if subject.lower().startswith("re:") else f"Re: {subject}"
        if mode == "forward":
            return (
                subject
                if subject.lower().startswith(("fwd:", "fw:"))
                else f"Fwd: {subject}"
            )
        return subject

    def _get_sent_folder(self):
        """Return the local Sent folder record (role='sent' first, then a
        folder matching ``sent_folder_name`` case-insensitively) or empty."""
        self.ensure_one()
        sent = self.folder_ids.filtered(lambda f: f.role == "sent")[:1]
        if not sent:
            wanted = (self.sent_folder_name or "Sent").strip().lower()
            sent = self.folder_ids.filtered(
                lambda f: (f.imap_name or "").strip().lower() == wanted
            )[:1]
        return sent

    def _get_default_signature(self):
        """Resolve the default HTML signature for THIS mailbox (PR-2c).

        Order: mailbox-specific default first, then any user-global default
        (``mailbox_id`` empty), else an empty recordset.  The search is
        ``sudo()`` with an EXPLICIT ``user_id`` filter on the mailbox owner —
        deterministic whatever the current user is (manager/cron contexts
        included), while staying equivalent to the per-user ir.rule contract.
        """
        self.ensure_one()
        signature_model = self.env["mail.management.signature"].sudo()
        specific = signature_model.search(
            [
                ("user_id", "=", self.user_id.id),
                ("mailbox_id", "=", self.id),
                ("is_default", "=", True),
            ],
            limit=1,
        )
        if specific:
            return specific
        return signature_model.search(
            [
                ("user_id", "=", self.user_id.id),
                ("mailbox_id", "=", False),
                ("is_default", "=", True),
            ],
            limit=1,
        )

    @staticmethod
    def _html_body_to_plain(html):
        """Generate the text/plain fallback for an HTML body (PR-2c).

        Uses Odoo's ``html2plaintext`` (block-level tags → newlines, inline
        formatting reduced to markdown-ish plain text — no tags survive)."""
        return html2plaintext(html or "")

    def _send_message(
        self,
        *,
        to,
        cc="",
        bcc="",
        subject="",
        body_text="",
        body_html="",
        attachments=None,
        reply_to_message=None,
        forward=False,
    ):
        """Build and send an RFC822 message via SMTP (design D.1).

        Steps (in order — SMTP success gates every local side effect, so a
        failure never leaves partial state):

        1. build an :class:`email.message.EmailMessage` (From/To/Cc/Bcc/
           Subject, plain-text body, MIME attachments);
        2. reply threading: ``In-Reply-To`` = original ``message_id`` and
           ``References`` = original references chain + message id;
           forward: fresh ``Message-ID``, no threading headers;
        3. SMTP send through the mailbox credential (redacted UserError);
        4. IMAP APPEND a copy to the Sent folder — an APPEND failure is
           SURFACED as a warning (the mail was sent; never claim otherwise);
        5. reply only: mirror ``\\Answered`` (IMAP STORE + local flag);
        6. create the local Sent mirror record + persist attachments via
           ``ir.attachment``/filestore, linked to it.

        :param body_text: plain-text body.  When ``body_html`` is given it
            is used as the text/plain part of the multipart/alternative
            message (the plain fallback); when empty, the fallback is
            generated from the sanitized HTML.
        :param body_html: rich-text body (PR-2c).  When non-empty the
            outgoing message becomes ``multipart/alternative`` with the
            SANITIZED HTML (``tools.html_sanitize`` — ``<script>`` and
            event handlers never leave) as the ``text/html`` part;
            plain-only bodies keep the single-part ``text/plain`` path.
        :param attachments: list of ``{'name', 'content'(bytes),
            'mimetype'}`` dicts (new uploads and/or forward copies).
        :returns: ``{'message': local sent record (or empty recordset),
            'append_warning': str or False}``
        """
        self.ensure_one()
        if not (to or "").strip():
            raise UserError(_("At least one recipient (To) is required."))

        mode = "forward" if forward else ("reply" if reply_to_message else "plain")
        subject = self._compose_subject(subject, mode)
        attachments = list(attachments or [])
        if forward and reply_to_message:
            attachments = attachments + self._forward_attachment_payloads(
                reply_to_message
            )

        # --- 1/2. build the outgoing message --------------------------------
        msg = EmailMessage()
        msg["From"] = self.from_email
        msg["To"] = to
        if (cc or "").strip():
            msg["Cc"] = cc
        if (bcc or "").strip():
            msg["Bcc"] = bcc  # smtplib strips Bcc from the wire copy
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=False)
        msg["Message-ID"] = email.utils.make_msgid()
        # --- PR-2g: compose settings of the mailbox OWNER (receipts +
        # reply save folder).  sudo() with an explicit user_id filter —
        # the sender may be a manager/cron acting on another user's
        # mailbox, and the send policy follows the OWNER's preferences.
        owner_settings = (
            self.env["mail.management.settings"]
            .sudo()
            .search([("user_id", "=", self.user_id.id)], limit=1)
        )
        if owner_settings.request_read_receipt:
            msg["Disposition-Notification-To"] = self.from_email
        if owner_settings.request_delivery_receipt:
            msg["Return-Receipt-To"] = self.from_email
        # PR-2g 'same': a sent REPLY is saved next to the ORIGINAL message
        # (APPEND + local mirror + \Answered there) instead of the Sent
        # folder; forwards and fresh composes always use Sent.
        reply_folder = reply_to_message.folder_id if reply_to_message else False
        if reply_folder and not forward and owner_settings.reply_save_folder == "same":
            save_folder = reply_folder
        else:
            save_folder = self._get_sent_folder()
        references = ""
        if reply_to_message and not forward:
            msg["In-Reply-To"] = reply_to_message.message_id or ""
            references = " ".join(
                part
                for part in (
                    (reply_to_message.references or "").split(),
                    (reply_to_message.message_id or "").split(),
                )
                for part in part
            )
            if references:
                msg["References"] = references
        # --- body: multipart/alternative when HTML is present (PR-2c) -----
        safe_html = ""
        if body_html and str(body_html).strip():
            # SANITIZE-OUT: never trust the client — the html part is
            # sanitized BEFORE it leaves this method (and the sent copy
            # is sanitized again on store by the fields.Html field).
            safe_html = html_sanitize(body_html)
        # The plain part: the explicit body_text when given, else the
        # generated fallback — one value shared by the wire message AND
        # the local Sent mirror (they can never diverge).
        if safe_html:
            plain_part = (body_text or "").strip() or self._html_body_to_plain(
                safe_html
            )
            msg.set_content(plain_part)
            msg.add_alternative(safe_html, subtype="html")
        else:
            plain_part = body_text or ""
            msg.set_content(plain_part)
        for att in attachments:
            maintype, _sep, subtype = (
                att.get("mimetype") or "application/octet-stream"
            ).partition("/")
            msg.add_attachment(
                att["content"],
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=att["name"],
            )

        # --- 3. SMTP send (failure → redacted UserError, nothing else) ------
        plain = self._get_credential_plaintext()
        try:
            with SmtpClient(
                self.smtp_host,
                self.smtp_port,
                self.smtp_use_tls,
                self.imap_user,
                plain,
            ) as smtp:
                smtp.send_message(msg)
        except Exception as exc:  # noqa: BLE001
            raise UserError(
                _("Failed to send via SMTP: %s") % FernetCrypto.redact(str(exc), plain)
            ) from exc

        # --- 4/5. IMAP APPEND + \Answered mirror (warnings, never errors) ----
        append_warning = False
        if save_folder:
            try:
                with ImapClient(
                    self.imap_host,
                    self.imap_port,
                    self.imap_use_ssl,
                    self.imap_user,
                    plain,
                    timeout=self._get_imap_timeout(),
                ) as imap:
                    try:
                        imap.append(
                            msg.as_bytes(),
                            folder=save_folder.imap_name,
                            flag_set=["\\Seen"],
                            date=datetime.now(timezone.utc),
                        )
                    except Exception as exc:  # noqa: BLE001
                        # SMTP already succeeded: surface, do NOT swallow and
                        # never claim the send failed (spec: Sent APPEND).
                        append_warning = self._sent_append_failed_warning
                        _logger.warning(
                            "Sent-copy APPEND failed for mailbox %s folder %s: %s",
                            self.id,
                            save_folder.imap_name,
                            FernetCrypto.redact(str(exc), plain),
                        )
                    if reply_to_message and not forward:
                        try:
                            if reply_to_message.uid:
                                imap.store(
                                    [reply_to_message.uid],
                                    ["\\Answered"],
                                    True,
                                )
                        except Exception as exc:  # noqa: BLE001
                            _logger.warning(
                                "\\Answered STORE failed for message %s: %s",
                                reply_to_message.id,
                                FernetCrypto.redact(str(exc), plain),
                            )
            except Exception as exc:  # noqa: BLE001
                append_warning = self._sent_append_failed_warning
                _logger.warning(
                    "IMAP connection for sent-copy/answered failed (mailbox %s): %s",
                    self.id,
                    FernetCrypto.redact(str(exc), plain),
                )
        else:
            append_warning = self._no_sent_folder_warning
            _logger.warning("No Sent folder found for mailbox %s", self.id)

        if reply_to_message and not forward:
            reply_to_message.flag_answered = True

        # --- 6. local Sent mirror + attachment persistence ------------------
        sent_record = self.env["mail.management.message"]
        if save_folder:
            sent_record = self.env["mail.management.message"].create(
                {
                    "mailbox_id": self.id,
                    "folder_id": save_folder.id,
                    # Temporary uid far above the server watermark; corrected
                    # by the message_id dedup on the next Sent-folder sync.
                    "uid": max(save_folder.message_ids.mapped("uid"), default=0)
                    + 1_000_000,
                    "message_id": msg["Message-ID"],
                    "subject": subject,
                    "email_from": self.from_email,
                    "email_to": to,
                    "email_cc": cc or "",
                    "date": fields.Datetime.now(),
                    "body_text": plain_part,
                    "body_html": safe_html or False,
                    "in_reply_to": (reply_to_message.message_id or "")
                    if (reply_to_message and not forward)
                    else "",
                    "references": references if not forward else "",
                    "flag_seen": True,
                }
            )
            if attachments:
                self._store_attachments(
                    sent_record,
                    [
                        {
                            "name": att["name"],
                            "content": att["content"],
                            "mimetype": att.get("mimetype", ""),
                        }
                        for att in attachments
                    ],
                )

        return {"message": sent_record, "append_warning": append_warning}

    @staticmethod
    def _forward_attachment_payloads(original_message):
        """Load a forwarded message's attachments from the filestore as
        MIME-ready payload dicts (bytes + mimetype + name)."""
        payloads = []
        for att in original_message.attachment_ids:
            content = bytes(getattr(att.ir_attachment_id.raw, "content", b"") or b"")
            payloads.append(
                {
                    "name": att.name,
                    "content": content,
                    "mimetype": att.mimetype or "application/octet-stream",
                }
            )
        return payloads

    # ------------------------------------------------------------------
    # IMAP sync backbone (design §C — PR-1b-i)
    # ------------------------------------------------------------------
    @api.model
    def _cron_sync_mailboxes(self, limit=None):
        """Batched cron entry — sync up to *limit* (or ``cron_batch_size``)
        active mailboxes per tick.

        Worker-safe: bounded batch, per-mailbox try/except + savepoint so one
        failure never aborts the batch (design §C.2).
        """
        batch = self._safe_int(
            limit
            or self.env["ir.config_parameter"]
            .sudo()
            .get_str("mail_management.cron_batch_size", 20),
            20,
        )
        mailboxes = self.search(
            [("state", "=", "active"), ("active", "=", True)], limit=batch
        )
        for mb in mailboxes:
            try:
                with self.env.cr.savepoint():
                    mb._sync_mailbox()
            except Exception as exc:
                _logger.exception(
                    "sync failed for mailbox %s (user %s)", mb.id, mb.user_id.id
                )
                redacted = FernetCrypto.redact(str(exc))[:1000]
                try:
                    with self.env.cr.savepoint():
                        mb.sudo().write({"sync_error": redacted})
                except Exception:  # noqa: BLE001
                    _logger.error("could not persist sync_error for mailbox %s", mb.id)

    def _sync_mailbox(self):
        """Connect to the IMAP server and sync all known folders.

        Incremental: only fetches UIDs ``> folder.last_uid``.
        UIDVALIDITY-aware: detects epoch changes, resets ``last_uid`` and
        re-fetches (design §A.3.1, §C.3).
        """
        self.ensure_one()
        plain = self._get_credential_plaintext()
        fetch_batch = self._safe_int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_str("mail_management.fetch_batch_size", 50),
            50,
        )
        now = fields.Datetime.now()

        with ImapClient(
            self.imap_host,
            self.imap_port,
            self.imap_use_ssl,
            self.imap_user,
            plain,
            timeout=self._get_imap_timeout(),
        ) as imap:
            for folder in self.folder_ids:
                self._sync_folder(imap, folder, fetch_batch=fetch_batch, now=now)

        self.last_sync = now
        self.sync_error = False

    def _sync_folder(self, imap, folder, fetch_batch=50, now=None):
        """Sync ONE folder over an open IMAP session.

        Incremental (UIDs ``> folder.last_uid``) and UIDVALIDITY-aware
        (epoch change → ``last_uid`` reset).  Extracted from
        ``_sync_mailbox`` so the PR-2b post-move destination refresh can
        reuse the exact same logic on the same connection.

        PR-2b note: ``now`` defaults to ``fields.Datetime.now()`` for
        single-folder refreshes; ``_sync_mailbox`` passes one timestamp for
        all folders (unchanged behavior).
        """
        server_uidval = imap.uidvalidity(folder.imap_name)
        if folder.uidvalidity and str(folder.uidvalidity) != str(server_uidval):
            # UIDVALIDITY changed — reset epoch (design A.3.1).
            folder.last_uid = 0
        folder.uidvalidity = str(server_uidval)

        new_uids = imap.search_uids(folder.imap_name, since_uid=folder.last_uid)
        if new_uids:
            batch_uids = new_uids[:fetch_batch]
            for uid, raw, flags in imap.fetch_messages(folder.imap_name, batch_uids):
                self._upsert_message(folder, uid, raw, flags)
            folder.last_uid = max(batch_uids)
        folder.last_sync = now or fields.Datetime.now()
        # PR-2h approximation: anything observed in a Trash folder WITHOUT a
        # trashed_date is stamped NOW — deletions made from other clients
        # have no local anchor, and pre-existing Trash content gets stamped
        # on the first observed sync (the retention clock starts there).
        if folder.role == "trash":
            folder.message_ids.filtered(lambda m: not m.trashed_date).write(
                {"trashed_date": now or fields.Datetime.now()}
            )

    # ------------------------------------------------------------------
    # Message upsert + parsing (design §A.3.1 HIGH-RISK, §C.3)
    # ------------------------------------------------------------------
    def _upsert_message(self, folder, uid, raw_bytes, flags):
        """Idempotently insert or update a fetched message.

        Dedup strategy (design A.3.1):
          1. Match by ``message_id`` within the mailbox (UIDVALIDITY recovery).
          2. Fall back to ``(folder_id, uid)`` for normal re-sync.
          3. If neither matches, create new — the ``UNIQUE(folder_id, uid)``
             constraint handles the rare concurrent-insert case.

        User-set ``\\Flagged`` is preserved across UIDVALIDITY re-fetch.
        """
        msg_model = self.env["mail.management.message"]
        parsed = self._parse_raw_message(raw_bytes)

        vals = {
            "mailbox_id": self.id,
            "folder_id": folder.id,
            "uid": uid,
            "message_id": parsed["message_id"],
            "subject": parsed["subject"],
            "email_from": parsed["email_from"],
            "email_to": parsed["email_to"],
            "email_cc": parsed["email_cc"],
            "date": parsed["date"],
            "body_html": parsed["body_html"],
            "body_text": parsed["body_text"],
            "in_reply_to": parsed["in_reply_to"],
            "references": parsed["references"],
            "flag_seen": "\\Seen" in flags,
            "flag_answered": "\\Answered" in flags,
            "flag_flagged": "\\Flagged" in flags,
        }

        # 1. Match by message_id within mailbox (handles UIDVALIDITY change).
        existing = msg_model
        if vals["message_id"]:
            existing = msg_model.search(
                [
                    ("mailbox_id", "=", self.id),
                    ("message_id", "=", vals["message_id"]),
                ],
                limit=1,
            )

        # 2. Fall back to (folder_id, uid) for normal re-sync.
        if not existing:
            existing = msg_model.search(
                [("folder_id", "=", folder.id), ("uid", "=", uid)], limit=1
            )

        if existing:
            # Preserve user-set \Flagged if the server no longer reports it.
            if existing.flag_flagged and "\\Flagged" not in flags:
                vals["flag_flagged"] = True
            existing.write(vals)
            return existing

        # 3. Create new message + attachments.
        message = msg_model.create(vals)
        if parsed["attachments"]:
            self._store_attachments(message, parsed["attachments"])
        return message

    def _store_attachments(self, message, attachments):
        """Create ``ir.attachment`` + ``mail.management.attachment`` for each
        parsed MIME part.

        The binary payload lives in ``ir.attachment`` (filestore), never in a
        DB blob column (design A.4).
        """
        attach_model = self.env["mail.management.attachment"]
        ir_attach_model = self.env["ir.attachment"]
        for att in attachments:
            ir_att = ir_attach_model.sudo().create(
                {
                    "name": att["name"],
                    "raw": att["content"],
                    "res_model": "mail.management.message",
                    "res_id": message.id,
                    "mimetype": att.get("mimetype", ""),
                }
            )
            attach_model.create(
                {
                    "webmail_message_id": message.id,
                    "name": att["name"],
                    "mimetype": att.get("mimetype", ""),
                    "file_size": len(att["content"]),
                    "ir_attachment_id": ir_att.id,
                    "content_id": att.get("content_id", ""),
                    "inline": bool(att.get("content_id")),
                }
            )

    @staticmethod
    def _parse_raw_message(raw_bytes):
        """Parse a raw RFC822 message into a vals dict.

        Sanitizes HTML via ``html_sanitize`` **before** returning the vals
        so the stored ``body_html`` is safe even before the ``fields.Html``
        field does its own sanitization (defense in depth, design §C.3, §A.3).
        """
        msg = email.message_from_bytes(raw_bytes)

        def _decode_hdr(value):
            if not value:
                return ""
            try:
                return str(make_header(decode_header(value)))
            except Exception:  # noqa: BLE001
                return str(value)

        message_id = (msg.get("Message-ID") or "").strip()
        subject = _decode_hdr(msg.get("Subject"))
        email_from = _decode_hdr(msg.get("From"))
        email_to = _decode_hdr(msg.get("To"))
        email_cc = _decode_hdr(msg.get("Cc"))
        in_reply_to = (msg.get("In-Reply-To") or "").strip()
        references = msg.get("References") or ""

        # Date — convert to naive UTC for Odoo's fields.Datetime.
        date_dt = False
        date_str = msg.get("Date")
        if date_str:
            try:
                date_dt = email.utils.parsedate_to_datetime(date_str)
                if date_dt and date_dt.tzinfo:
                    date_dt = date_dt.astimezone(timezone.utc).replace(tzinfo=None)
            except (ValueError, TypeError):
                date_dt = False

        # Body parts + attachments.
        body_html = ""
        body_text = ""
        attachments = []

        def _decode_payload(part):
            payload = part.get_payload(decode=True)
            if not payload:
                return ""
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")

        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disposition = str(part.get_content_disposition() or "")

                if disposition == "attachment" or part.get_filename():
                    payload = part.get_payload(decode=True)
                    if payload:
                        attachments.append(
                            {
                                "name": part.get_filename() or "unnamed",
                                "content": payload,
                                "mimetype": ctype,
                                "content_id": (part.get("Content-ID") or "").strip(
                                    "<>"
                                ),
                            }
                        )
                elif ctype == "text/html":
                    body_html = _decode_payload(part)
                elif ctype == "text/plain":
                    body_text = _decode_payload(part)
        else:
            ctype = msg.get_content_type()
            if ctype == "text/html":
                body_html = _decode_payload(msg)
            else:
                body_text = _decode_payload(msg)

        # Sanitize HTML before persist (defense in depth).
        if body_html:
            body_html = html_sanitize(body_html)

        return {
            "message_id": message_id,
            "subject": subject,
            "email_from": email_from,
            "email_to": email_to,
            "email_cc": email_cc,
            "date": date_dt,
            "body_html": body_html,
            "body_text": body_text,
            "in_reply_to": in_reply_to,
            "references": references,
            "attachments": attachments,
        }

    # ------------------------------------------------------------------
    # Message flags (PR-2a — local-first, best-effort IMAP mirror)
    # ------------------------------------------------------------------

    #: The only flags the webmail may set through ``_set_message_flags``.
    _settable_flags = ("seen", "flagged")

    def _set_message_flags(self, message_ids, flags):
        """Update ``\\Seen``/``\\Flagged``: LOCAL FIRST, mirror best-effort.

        POLICY (openspec/changes/webmail-complete-management/tasks.md):

        1. write the LOCAL ``flag_seen``/``flag_flagged`` fields first so
           the UI and the stored ``folder.unread_count`` recompute
           instantly (the sync engine stays consistent on its own
           ``\\Flagged``-preservation path);
        2. mirror to IMAP best-effort through the ``ImapClient.set_flags``
           seam, grouped per folder — a mirror failure (including a
           missing credential) is LOGGED with the mailbox id and NEVER
           raised to the caller.

        :param message_ids: ids of THIS mailbox's messages; ids belonging
            to another mailbox are ignored (callers group by mailbox).
        :param flags: ``{'seen': bool}`` and/or ``{'flagged': bool}``;
            any other key raises ``UserError``.
        :returns: the recordset actually updated.
        """
        self.ensure_one()
        clean = {}
        for key, value in (flags or {}).items():
            if key not in self._settable_flags:
                raise UserError(_("Invalid flag: %s.") % key)
            clean[key] = bool(value)
        ids = set(message_ids or [])
        messages = self.synced_message_ids.filtered(lambda m: m.id in ids)
        if not messages or not clean:
            return messages

        vals = {}
        if "seen" in clean:
            vals["flag_seen"] = clean["seen"]
        if "flagged" in clean:
            vals["flag_flagged"] = clean["flagged"]
        # 1. LOCAL FIRST — instant UI + unread_count (stored compute
        #    depends on message_ids.flag_seen).
        messages.write(vals)

        # 2. Best-effort IMAP mirror — one set_flags call per folder.
        try:
            plain = self._get_credential_plaintext()
            with ImapClient(
                self.imap_host,
                self.imap_port,
                self.imap_use_ssl,
                self.imap_user,
                plain,
                timeout=self._get_imap_timeout(),
            ) as imap:
                for folder in messages.mapped("folder_id"):
                    uids = sorted(
                        messages.filtered(lambda m, f=folder: m.folder_id == f).mapped(
                            "uid"
                        )
                    )
                    imap.set_flags(
                        folder.imap_name,
                        uids,
                        seen=clean.get("seen"),
                        flagged=clean.get("flagged"),
                    )
        except Exception as exc:  # noqa: BLE001 — POLICY: never raise
            _logger.warning(
                "Flag mirror failed for mailbox %s (flags stay local): %s",
                self.id,
                FernetCrypto.redact(str(exc)),
            )
        return messages

        # ------------------------------------------------------------------
        # Folder discovery (design §C.3)
        # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Folder CRUD + movement (PR-2b — IMAP-FIRST policy)
    #
    # POLICY (deliberately DIFFERENT from the flags best-effort mirror
    # above): for folder CRUD and message movement the IMAP server is the
    # source of truth.  Every method performs the IMAP operation FIRST
    # and only then runs the local bookkeeping; an IMAP failure raises
    # ``UserError`` and leaves the LOCAL data untouched — the local
    # mirror must never diverge from the server shape.  (Flags are
    # user-level annotations and degrade gracefully; a folder that exists
    # locally but not on the server would break every later sync.)
    # ------------------------------------------------------------------

    #: Longest folder name accepted by ``_create_folder``/``_rename_folder``.
    _folder_name_max_length = 255

    #: Hierarchy separators rejected in v1 names — creating nested paths
    #: would require knowing the server delimiter, so v1 accepts simple
    #: child names only (user feedback: create folders from the tree).
    _folder_name_separators = ("/", ".")

    #: PR-2h — mapped system-folder roles that REFUSE deletion (the user must
    #: unmap the role in Settings ▸ Folders first — protects Sent/Trash/etc.
    #: from accidental nukes).  'archive'/'all'/'other' stay deletable.
    _protected_folder_roles = ("inbox", "sent", "drafts", "trash", "junk")

    @staticmethod
    def _uids_by_folder(messages):
        """Group *messages* per source folder → ``[(folder, sorted uids)]``.

        Shared by the PR-2b move/purge flows (one IMAP call per folder,
        deterministic uid order).
        """
        return [
            (
                folder,
                sorted(
                    messages.filtered(lambda m, f=folder: m.folder_id == f).mapped(
                        "uid"
                    )
                ),
            )
            for folder in messages.mapped("folder_id")
        ]

    def _get_trash_folder(self):
        """The mailbox's mapped Trash folder (``role='trash'``) or empty
        (PR-2h — shared by the soft-delete, delete-to-trash and purge flows)."""
        self.ensure_one()
        return self.folder_ids.filtered(lambda f: f.role == "trash")[:1]

    def _uid_batches(self, uids):
        """Yield *uids* in worker-safe batches (PR-2h).

        Batch size: ``mail_management.move_batch_size`` (default 100) — a
        huge folder soft-delete must never fire one giant IMAP command.
        """
        size = self._safe_int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_str("mail_management.move_batch_size", 100),
            100,
        )
        uids = list(uids)
        for start in range(0, len(uids), size):
            yield uids[start : start + size]

    @contextmanager
    def _imap_session(self):
        """Open an IMAP session with the stored credential.

        Shared by the PR-2b IMAP-first operations; the credential
        ``UserError`` is raised BEFORE any IMAP call when the mailbox has
        none (no local change is ever made).
        """
        self.ensure_one()
        plain = self._get_credential_plaintext()
        with ImapClient(
            self.imap_host,
            self.imap_port,
            self.imap_use_ssl,
            self.imap_user,
            plain,
            timeout=self._get_imap_timeout(),
        ) as imap:
            yield imap

    @classmethod
    def _sanitize_folder_name(cls, name):
        """Validate/clean a folder name for IMAP CREATE/RENAME (v1).

        Strips whitespace; rejects empty, too-long (> 255 chars) and
        hierarchy-separator names ("/" and "." — simple child names only).
        Raises ``UserError`` on invalid input, returns the clean name
        otherwise.
        """
        clean = (name or "").strip()
        if not clean:
            raise UserError(_("The folder name cannot be empty."))
        if len(clean) > cls._folder_name_max_length:
            raise UserError(
                _("The folder name is too long (max %s characters).")
                % cls._folder_name_max_length
            )
        for sep in cls._folder_name_separators:
            if sep in clean:
                raise UserError(
                    _(
                        "The folder name cannot contain '%s'. Use a simple "
                        "folder name (sub-folders are not supported yet)."
                    )
                    % sep
                )
        return clean

    def _create_folder(self, name):
        """Create a folder on the server (IMAP CREATE), then locally.

        IMAP-first: any IMAP failure raises ``UserError`` BEFORE any local
        record is created.  A local duplicate is rejected upfront (cheap
        local pre-check that avoids a doomed server round-trip).
        """
        self.ensure_one()
        clean = self._sanitize_folder_name(name)
        folder_model = self.env["mail.management.folder"]
        if clean in set(self.folder_ids.mapped("imap_name")):
            raise UserError(
                _("A folder named '%s' already exists in this mailbox.") % clean
            )
        try:
            with self._imap_session() as imap:
                imap.create_folder(clean)
        except UserError:
            raise
        except Exception as exc:  # noqa: BLE001 — IMAP-first: no local change
            raise UserError(
                _("Could not create the folder on the IMAP server: %s")
                % FernetCrypto.redact(str(exc))
            ) from exc
        return folder_model.create(
            {
                "mailbox_id": self.id,
                "imap_name": clean,
                "name": clean,
                "role": "other",
            }
        )

    def _rename_folder(self, folder, new_name):
        """Rename a folder on the server (IMAP RENAME), then locally.

        Renaming to the CURRENT name is an idempotent no-op (some servers
        answer NO to ``RENAME x x`` — never ask them).
        """
        self.ensure_one()
        if folder.mailbox_id != self:
            raise UserError(_("The folder does not belong to this mailbox."))
        clean = self._sanitize_folder_name(new_name)
        if clean == folder.imap_name:
            return folder
        dupes = self.folder_ids.filtered(
            lambda f, n=clean: f.imap_name == n and f != folder
        )
        if dupes:
            raise UserError(
                _("A folder named '%s' already exists in this mailbox.") % clean
            )
        try:
            with self._imap_session() as imap:
                imap.rename_folder(folder.imap_name, clean)
        except UserError:
            raise
        except Exception as exc:  # noqa: BLE001 — IMAP-first: no local change
            raise UserError(
                _("Could not rename the folder on the IMAP server: %s")
                % FernetCrypto.redact(str(exc))
            ) from exc
        folder.write({"imap_name": clean, "name": clean})
        return folder

    def _delete_folder(self, folder):
        """Delete a folder with GMail-style SOFT-DELETE semantics (PR-2h).

        Replaces the PR-2b hard delete (``force`` is gone — the client
        confirms, the server relocates):

        * mapped system folders (:attr:`_protected_folder_roles`) REFUSE
          deletion (``UserError``) — unmap the role in Settings ▸ Folders
          first;
        * a folder with NO messages → IMAP DELETE + local unlink (the
          PR-2b direct path);
        * a folder WITH messages → SOFT delete: every message moves to the
          mailbox's ``role='trash'`` folder through the PR-2b move
          machinery in batches (COPY + ``\\Deleted`` + EXPUNGE on the
          source), LOCAL Trash copies are created WITH
          ``trashed_date=now`` (the retention anchor the daily purge cron
          reads), then the now-empty source folder is IMAP-DELETEd and
          removed locally (cascading its local records);
        * NO mapped Trash folder → ``UserError`` telling the user to map
          one first (ties into the PR-2g role mapping).

        IMAP-first as ever: any server failure raises ``UserError`` BEFORE
        any local change.

        :returns: ``{'moved_count': int}`` — messages relocated to Trash.
        """
        self.ensure_one()
        if folder.mailbox_id != self:
            raise UserError(_("The folder does not belong to this mailbox."))
        if folder.role in self._protected_folder_roles:
            raise UserError(
                _(
                    "The folder '%s' is a mapped system folder. Remove its "
                    "role in Settings ▸ Folders before deleting it."
                )
                % folder.imap_name
            )
        messages = folder.message_ids
        if not messages:
            try:
                with self._imap_session() as imap:
                    imap.delete_folder(folder.imap_name)
            except UserError:
                raise
            except Exception as exc:  # noqa: BLE001 — IMAP-first: no local change
                raise UserError(
                    _("Could not delete the folder on the IMAP server: %s")
                    % FernetCrypto.redact(str(exc))
                ) from exc
            folder.unlink()
            return {"moved_count": 0}
        trash = self._get_trash_folder()
        if not trash:
            raise UserError(
                _(
                    "No Trash folder is mapped for this mailbox. "
                    "Map one in Settings ▸ Folders first."
                )
            )
        now = fields.Datetime.now()
        try:
            with self._imap_session() as imap:
                for batch in self._uid_batches(sorted(messages.mapped("uid"))):
                    imap.move_messages(folder.imap_name, batch, trash.imap_name)
                # The source is empty server-side now — remove it too.
                imap.delete_folder(folder.imap_name)
        except UserError:
            raise
        except Exception as exc:  # noqa: BLE001 — IMAP-first: no local change
            raise UserError(
                _("Could not delete the folder on the IMAP server: %s")
                % FernetCrypto.redact(str(exc))
            ) from exc
        # Local bookkeeping: stamped Trash copies FIRST (they read the
        # source records), then the folder unlink cascades the sources.
        self._mirror_trashed_messages(messages, trash, now)
        folder.unlink()
        return {"moved_count": len(messages)}

    def _mirror_trashed_messages(self, messages, trash_folder, now):
        """Create the LOCAL Trash copies of soft-deleted messages (PR-2h).

        Each copy carries ``trashed_date=now`` — the retention anchor the
        daily purge cron reads.  Temporary uids above the Trash watermark
        keep the ``UNIQUE(folder_id, uid)`` constraint happy; the
        message_id dedup on the next Trash sync re-binds them to the real
        server uids (the same trick as the local Sent mirror in
        ``_send_message``).  Attachment POINTER rows are duplicated —
        they share the ``ir.attachment`` (no filestore copy).
        """
        msg_model = self.env["mail.management.message"]
        att_model = self.env["mail.management.attachment"]
        base = max(trash_folder.message_ids.mapped("uid"), default=0) + 1_000_000
        for index, message in enumerate(messages):
            copy = msg_model.create(
                {
                    "mailbox_id": message.mailbox_id.id,
                    "folder_id": trash_folder.id,
                    "uid": base + index,
                    "message_id": message.message_id or "",
                    "subject": message.subject,
                    "email_from": message.email_from,
                    "email_to": message.email_to,
                    "email_cc": message.email_cc,
                    "date": message.date,
                    "body_html": message.body_html or False,
                    "body_text": message.body_text or False,
                    "in_reply_to": message.in_reply_to or "",
                    "references": message.references or "",
                    "flag_seen": message.flag_seen,
                    "flag_answered": message.flag_answered,
                    "flag_flagged": message.flag_flagged,
                    "trashed_date": now,
                }
            )
            for att in message.attachment_ids:
                att_model.create(
                    {
                        "webmail_message_id": copy.id,
                        "name": att.name,
                        "mimetype": att.mimetype or "",
                        "file_size": att.file_size,
                        "ir_attachment_id": att.ir_attachment_id.id,
                        "content_id": att.content_id or "",
                        "inline": att.inline,
                    }
                )
        return True

    def _move_messages(self, messages, dest_folder):
        """Move messages to another folder of THIS mailbox (IMAP-first).

        The server move runs first (portable COPY + ``\\Deleted`` +
        EXPUNGE through the ``ImapClient.move_messages`` seam, grouped per
        source folder); only after success are the LOCAL source records
        deleted.  Destination copies are NOT fabricated locally — the
        destination folder gets a cheap same-connection refresh here and
        is fully re-synced by the regular folder sync.  An IMAP failure
        raises ``UserError`` and nothing local changes.
        """
        self.ensure_one()
        if dest_folder.mailbox_id != self:
            raise UserError(
                _("Messages cannot be moved to a folder of another mailbox.")
            )
        own = messages.filtered(lambda m: m.mailbox_id == self)
        # Moving to the folder a message is already in is a NO-OP: a
        # COPY-to-self + ``\\Deleted`` + EXPUNGE would churn its server
        # UID (and some servers mishandle COPY to the selected folder).
        own = own.filtered(lambda m, d=dest_folder: m.folder_id != d)
        if not own:
            return own
        try:
            with self._imap_session() as imap:
                for folder, uids in self._uids_by_folder(own):
                    if uids:
                        imap.move_messages(
                            folder.imap_name, uids, dest_folder.imap_name
                        )
                # Cheap destination refresh on the SAME connection: the
                # moved copies become visible without a full sync.  Best
                # effort only — the move already succeeded server-side.
                try:
                    self._sync_folder(imap, dest_folder)
                except Exception:  # noqa: BLE001 — move already succeeded
                    _logger.warning(
                        "Post-move refresh of folder %s failed (ignored)",
                        dest_folder.id,
                        exc_info=True,
                    )
        except UserError:
            raise
        except Exception as exc:  # noqa: BLE001 — IMAP-first: no local change
            raise UserError(
                _("Could not move the messages on the IMAP server: %s")
                % FernetCrypto.redact(str(exc))
            ) from exc
        own.unlink()
        return own

    def _purge_messages(self, messages):
        """Permanently delete messages: ``\\Deleted`` + EXPUNGE, then
        local unlink.  IMAP-first: a failure raises ``UserError`` and
        nothing is removed locally.  (EXPUNGE is folder-wide by IMAP
        design — normally only this client's marks are pending.)
        """
        self.ensure_one()
        own = messages.filtered(lambda m: m.mailbox_id == self)
        if not own:
            return own
        try:
            with self._imap_session() as imap:
                for folder, uids in self._uids_by_folder(own):
                    if uids:
                        imap.delete_messages(folder.imap_name, uids)
                        imap.expunge(folder.imap_name)
        except UserError:
            raise
        except Exception as exc:  # noqa: BLE001 — IMAP-first: no local change
            raise UserError(
                _("Could not delete the messages on the IMAP server: %s")
                % FernetCrypto.redact(str(exc))
            ) from exc
        own.unlink()
        return own

    def _delete_messages_to_trash(self, messages):
        """Delete with Trash semantics (PR-2b).

        * messages OUTSIDE Trash → MOVED to the mailbox's ``role='trash'``
          folder when one exists (recoverable until the Trash empties);
        * messages ALREADY in Trash — and, as a documented fallback,
          anything deleted when the mailbox has NO Trash folder — are
          PERMANENTLY deleted (``\\Deleted`` + EXPUNGE + local unlink,
          no recovery).

        :returns: ``{'moved_to_trash': recordset, 'purged': recordset}``
        """
        self.ensure_one()
        own = messages.filtered(lambda m: m.mailbox_id == self)
        empty = self.env["mail.management.message"]
        if not own:
            return {"moved_to_trash": empty, "purged": empty}
        trash = self._get_trash_folder()
        in_trash = own.filtered(lambda m: m.folder_id.role == "trash")
        elsewhere = own - in_trash
        moved = purged = empty
        if elsewhere and trash:
            moved = self._move_messages(elsewhere, trash)
        if in_trash or (elsewhere and not trash):
            purged = self._purge_messages(
                in_trash | (elsewhere if not trash else empty)
            )
        return {"moved_to_trash": moved, "purged": purged}

    # ------------------------------------------------------------------
    # PR-2h — Trash retention purge (daily cron)
    # ------------------------------------------------------------------

    @api.model
    def _cron_purge_trash(self, limit=None):
        """Daily cron entry — purge aged Trash messages (PR-2h).

        Worker-safe like the sync cron: bounded mailbox batch
        (``cron_batch_size``), per-mailbox try/except + savepoint so one
        failure never aborts the run (failures are logged, never raised).
        """
        batch = self._safe_int(
            limit
            or self.env["ir.config_parameter"]
            .sudo()
            .get_str("mail_management.cron_batch_size", 20),
            20,
        )
        mailboxes = self.search(
            [("state", "=", "active"), ("active", "=", True)], limit=batch
        )
        for mb in mailboxes:
            try:
                with self.env.cr.savepoint():
                    purged = mb._purge_aged_trash()
                    if purged:
                        _logger.info(
                            "trash purge: %s message(s) removed from mailbox %s",
                            purged,
                            mb.id,
                        )
            except Exception:  # noqa: BLE001 — best-effort per mailbox
                _logger.exception(
                    "trash purge failed for mailbox %s (user %s)",
                    mb.id,
                    mb.user_id.id,
                )

    def _purge_aged_trash(self):
        """Permanently delete THIS mailbox's aged Trash messages (PR-2h).

        The retention window comes from the mailbox OWNER's settings
        (``trash_retention_days``, default 30).  Only messages WITH a
        ``trashed_date`` strictly older than ``now - retention`` are purged
        (an unstamped message has no known age — the next trash sync stamps
        it, then retention applies).  IMAP-first through ``_purge_messages``:
        ``\\Deleted`` + EXPUNGE + local unlink; a server failure raises
        ``UserError`` (caught by the cron wrapper) and nothing local changes.

        :returns: the number of purged messages (0 when there is nothing
                  to do or no Trash folder is mapped).
        """
        self.ensure_one()
        trash = self._get_trash_folder()
        if not trash:
            return 0
        settings = (
            self.env["mail.management.settings"]
            .sudo()
            .search([("user_id", "=", self.user_id.id)], limit=1)
        )
        retention = settings.trash_retention_days or TRASH_RETENTION_DEFAULT_DAYS
        cutoff = fields.Datetime.now() - timedelta(days=retention)
        aged = trash.message_ids.filtered(
            lambda m, c=cutoff: m.trashed_date and m.trashed_date < c
        )
        if not aged:
            return 0
        self._purge_messages(aged)
        return len(aged)

    def _create_missing_folders(self, server_folders):
        """Create local folder records for names not yet known (PR-2j).

        Shared by ``action_discover_folders`` and the connection-test
        auto-discovery — one creation path, no drift.
        """
        self.ensure_one()
        existing = set(self.folder_ids.mapped("imap_name"))
        folder_model = self.env["mail.management.folder"]
        new_folders = folder_model
        for name in server_folders:
            if name in existing:
                continue
            new_folders |= folder_model.create(
                {
                    "mailbox_id": self.id,
                    "imap_name": name,
                    "name": name,
                    "role": self._guess_folder_role(name),
                }
            )
        return new_folders

    def action_discover_folders(self):
        """Discover server folders and create ``mail.management.folder``
        records with the right SPECIAL-USE role (design §C.3).

        Tolerates folder-name casing (``INBOX`` / ``Inbox``) by matching
        role by name heuristics.
        """
        self.ensure_one()
        plain = self._get_credential_plaintext()
        with ImapClient(
            self.imap_host,
            self.imap_port,
            self.imap_use_ssl,
            self.imap_user,
            plain,
            timeout=self._get_imap_timeout(),
        ) as imap:
            server_folders = imap.list_folders()
        return bool(self._create_missing_folders(server_folders))

    @staticmethod
    def _guess_folder_role(name):
        """Guess the SPECIAL-USE role from a folder name (casing-tolerant)."""
        lower = (name or "").lower()
        if lower == "inbox":
            return "inbox"
        if lower in ("sent", "sent items") or lower.endswith(".sent"):
            return "sent"
        if "draft" in lower:
            return "drafts"
        if "trash" in lower or "deleted" in lower:
            return "trash"
        if "archive" in lower:
            return "archive"
        if "spam" in lower or "junk" in lower:
            return "junk"
        if lower in ("all", "all mail", "all mail items"):
            return "all"
        return "other"
