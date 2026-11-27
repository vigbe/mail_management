"""HTML signature model — ``mail.management.signature`` (PR-2c).

Per-user (optionally per-mailbox) HTML signatures for the webmail compose
dialog.  The ``html_body`` content is SANITIZED ON WRITE via
``tools.html_sanitize`` (defense in depth: the ``fields.Html`` field
sanitizes again on store — the same double-guard pattern as
``mail.management.message.body_html`` at sync time).

One default per (user, mailbox) scope is guaranteed by AUTO-UNSETTING the
previous default in ``create`` and ``write`` (a mailbox-specific default and
a user-global default live in different scopes and coexist).

PR-2j: template-based signature GENERATOR — the user picks a layout, fills
name/title/email/phone/logo and one button renders email-safe HTML.
"""

import base64
import html as html_mod

from odoo import api, fields, models  # type: ignore
from odoo.tools.mail import html_sanitize  # type: ignore


class MailManagementSignature(models.Model):
    _name = "mail.management.signature"
    _description = "Mail Signature"
    _order = "name"

    name = fields.Char(required=True)
    user_id = fields.Many2one(
        "res.users",
        string="Owner",
        required=True,
        default=lambda self: self.env.user,
        index=True,
    )
    mailbox_id = fields.Many2one(
        "mail.management.mailbox",
        string="Mailbox",
        ondelete="cascade",
        help="Leave empty to apply this signature to all your mailboxes.",
    )
    html_body = fields.Html(
        string="Signature",
        sanitize=True,
        help="HTML content. Sanitized on write: script tags and event "
        "handlers are stripped before anything is stored or sent.",
    )
    is_default = fields.Boolean(
        string="Default",
        help="At most one default is kept per (user, mailbox) scope — "
        "setting a new default automatically unsets the previous one.",
    )

    # ------------------------------------------------------------------
    # PR-2j — signature template generator (user request 2026-09)
    # ------------------------------------------------------------------
    template_key = fields.Selection(
        [
            ("none", "Manual (sin plantilla)"),
            ("classic", "Clásica — logo a la izquierda"),
            ("modern", "Moderna — borde de color e íconos"),
            ("minimal", "Minimalista — una línea"),
            ("banner", "Con banner — nombre sobre franja"),
            ("elegant", "Elegante — serif con línea dorada"),
            ("corporate", "Corporativa — logo arriba centrado"),
            ("compact", "Compacta — chip oscuro minimal"),
        ],
        default="none",
        help="Elegí una plantilla y completá tus datos: el botón "
        "«Generar firma» arma el HTML por vos (después podés editarlo).",
    )
    contact_name = fields.Char(string="Nombre y apellido")
    contact_title = fields.Char(string="Cargo o título")
    contact_email = fields.Char(string="Correo")
    contact_phone = fields.Char(string="Teléfono")
    logo = fields.Binary(
        string="Logo",
        attachment=True,
        help="Imagen chica (PNG/JPG) — se incrusta en la firma.",
    )
    logo_filename = fields.Char()

    # ------------------------------------------------------------------
    # CRUD — sanitize-on-write + single-default enforcement
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault("user_id", self.env.user.id)
            if vals.get("html_body"):
                # Never trust input: sanitize BEFORE the field/ORM sees it.
                vals["html_body"] = html_sanitize(vals["html_body"])
        records = super().create(vals_list)
        records.filtered("is_default")._enforce_single_default()
        return records

    def write(self, vals):
        if vals.get("html_body"):
            vals["html_body"] = html_sanitize(vals["html_body"])
        result = super().write(vals)
        if vals.get("is_default"):
            self.filtered("is_default")._enforce_single_default()
        return result

    def _enforce_single_default(self):
        """Keep at most ONE default per (user, mailbox) scope.

        Scope key: ``(user_id, mailbox_id)`` — with ``mailbox_id = False``
        for user-global signatures (a global default and a mailbox-specific
        default are different scopes and may coexist).  The PREVIOUS default
        in the same scope is auto-unset; other users are never touched.
        """
        for signature in self:
            others = self.search(
                [
                    ("user_id", "=", signature.user_id.id),
                    ("mailbox_id", "=", signature.mailbox_id.id or False),
                    ("is_default", "=", True),
                    ("id", "!=", signature.id),
                ]
            )
            if others:
                others.write({"is_default": False})

    # ------------------------------------------------------------------
    # PR-2j — template rendering (email-safe: tables + inline styles)
    # ------------------------------------------------------------------
    def action_generate_signature(self):
        """Render ``html_body`` from the chosen template + contact data."""
        self.ensure_one()
        if self.template_key == "none":
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Generar firma",
                    "message": "Elegí primero una plantilla.",
                    "type": "warning",
                },
            }
        self.html_body = self._render_signature_html()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Generar firma",
                "message": "Firma generada — podés ajustarla abajo.",
                "type": "success",
            },
        }

    def _signature_context(self):
        """Escaped contact values with sensible user-based defaults."""
        self.ensure_one()
        return {
            "name": html_mod.escape(self.contact_name or self.user_id.name or ""),
            "title": html_mod.escape(self.contact_title or ""),
            "email": html_mod.escape(
                self.contact_email or self.user_id.email or self.user_id.login or ""
            ),
            "phone": html_mod.escape(self.contact_phone or ""),
            "logo": self._logo_html(),
        }

    def _logo_html(self):
        self.ensure_one()
        data = self.logo
        if not data:
            return ""
        raw = base64.b64decode(data)
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            mime = "image/png"
        elif raw[:3] == b"\xff\xd8\xff":
            mime = "image/jpeg"
        elif raw[:4] == b"GIF8":
            mime = "image/gif"
        else:
            mime = "image/png"
        src = "data:" + mime + ";base64," + data.decode()
        return (
            '<img src="' + src + '" alt="logo" '
            'style="max-width:96px; max-height:96px; '
            'border-radius:4px;" />'
        )

    def _render_signature_html(self):
        self.ensure_one()
        ctx = self._signature_context()
        key = self.template_key
        if key == "classic":
            logo_cell = (
                '<td style="padding-right:12px; '
                'border-right:3px solid #0d6efd;">' + ctx["logo"] + "</td>"
                if ctx["logo"]
                else ""
            )
            pad = ' style="padding-left:12px;"' if ctx["logo"] else ""
            return (
                '<table cellpadding="0" cellspacing="0" '
                'style="font-family:Arial,sans-serif;font-size:12px;'
                'color:#333;border-collapse:collapse;">'
                "<tr>" + logo_cell + "<td" + pad + ">"
                '<div style="font-size:14px;font-weight:bold;'
                'color:#1f2328;">' + ctx["name"] + "</div>"
                '<div style="color:#6c757d;margin-bottom:4px;">'
                + ctx["title"]
                + "</div>"
                '<div style="color:#0d6efd;">' + ctx["email"] + "</div>"
                "<div>" + ctx["phone"] + "</div>"
                "</td></tr></table>"
            )
        if key == "modern":
            return (
                '<table cellpadding="0" cellspacing="0" '
                'style="font-family:Arial,sans-serif;font-size:12px;'
                'border-collapse:collapse;">'
                '<tr><td style="border-left:4px solid #4f8cff;'
                'padding:6px 14px;">'
                '<div style="font-size:15px;font-weight:bold;'
                'color:#0f1115;">'
                + ctx["name"]
                + ' <span style="font-weight:normal;color:#9aa3b2;">'
                "· " + ctx["title"] + "</span></div>"
                '<div style="color:#4f8cff;margin-top:3px;">'
                "✉ " + ctx["email"] + " &nbsp;·&nbsp; ✆ " + ctx["phone"] + "</div>"
                '<div style="margin-top:6px;">' + ctx["logo"] + "</div>"
                "</td></tr></table>"
            )
        if key == "minimal":
            logo = " " + ctx["logo"] if ctx["logo"] else ""
            return (
                '<div style="font-family:Arial,sans-serif;font-size:12px;'
                'color:#333;"><b>' + ctx["name"] + "</b>"
                ' <span style="color:#6c757d;">— ' + ctx["title"] + "</span>"
                ' &nbsp;·&nbsp; <span style="color:#0d6efd;">'
                + ctx["email"]
                + "</span>"
                + " &nbsp;·&nbsp; "
                + ctx["phone"]
                + logo
                + "</div>"
            )
        if key == "elegant":
            return (
                '<table cellpadding="0" cellspacing="0" '
                'style="font-family:Georgia,serif;font-size:12px;'
                'color:#3b3226;border-collapse:collapse;">'
                "<tr><td>"
                '<div style="font-size:15px;color:#3b3226;'
                'letter-spacing:1px;">'
                + ctx["name"]
                + "</div>"
                + '<div style="border-top:1px solid #b8985a;width:180px;'
                'margin:4px 0;"></div>'
                + '<div style="font-style:italic;color:#7a6c55;">'
                + ctx["title"]
                + "</div>"
                + '<div style="margin-top:4px;color:#3b3226;">'
                + ctx["email"]
                + " &nbsp;·&nbsp; "
                + ctx["phone"]
                + "</div>"
                + '<div style="margin-top:6px;">'
                + ctx["logo"]
                + "</div>"
                + "</td></tr></table>"
            )
        if key == "corporate":
            return (
                '<table cellpadding="0" cellspacing="0" '
                'style="font-family:Arial,sans-serif;font-size:12px;'
                'border-collapse:collapse;text-align:center;">'
                "<tr><td>"
                + ctx["logo"]
                + "</td></tr>"
                + '<tr><td style="padding-top:6px;font-size:14px;'
                'font-weight:bold;color:#1f2328;">'
                + ctx["name"]
                + "</td></tr>"
                + '<tr><td style="color:#6c757d;">'
                + ctx["title"]
                + "</td></tr>"
                + '<tr><td style="border-top:1px solid #dee2e6;'
                'margin-top:6px;padding-top:4px;color:#0d6efd;">'
                + ctx["email"]
                + " &nbsp;·&nbsp; "
                + ctx["phone"]
                + "</td></tr>"
                + "</table>"
            )
        if key == "compact":
            return (
                '<table cellpadding="0" cellspacing="0" '
                'style="font-family:Arial,sans-serif;border-collapse:'
                'collapse;border-radius:8px;overflow:hidden;">'
                '<tr><td style="background:#0f1115;padding:8px 14px;">'
                '<span style="color:#ffffff;font-size:13px;'
                'font-weight:bold;">'
                + ctx["name"]
                + "</span>"
                + '<span style="color:#9aa3b2;font-size:11px;'
                'padding-left:8px;">'
                + ctx["title"]
                + "</span>"
                + '<div style="color:#6ea8ff;font-size:11px;'
                'padding-top:3px;">'
                + ctx["email"]
                + " &nbsp;·&nbsp; "
                + ctx["phone"]
                + "</div>"
                + "</td></tr></table>"
            )
        # banner
        return (
            '<table cellpadding="0" cellspacing="0" '
            'style="font-family:Arial,sans-serif;border-collapse:collapse;'
            'border-radius:6px;overflow:hidden;">'
            '<tr><td style="background:#0d6efd;padding:10px 16px;">'
            '<span style="color:#ffffff;font-size:16px;font-weight:bold;">'
            + ctx["name"]
            + "</span>"
            + '<span style="color:#cfe2ff;font-size:12px;'
            'padding-left:10px;">'
            + ctx["title"]
            + "</span></td></tr>"
            + '<tr><td style="background:#f1f3f5;padding:8px 16px;'
            'font-size:12px;color:#333;">'
            + ctx["logo"]
            + '<div style="margin-top:4px;">'
            '<span style="color:#0d6efd;">'
            + ctx["email"]
            + "</span>"
            + " &nbsp;·&nbsp; "
            + ctx["phone"]
            + "</div></td></tr></table>"
        )
