"""PR-2j — connection-test error wizard.

When the test fails with an *actionable* provider policy error (Google /
Microsoft app-password), the result opens as a modal wizard with clear
step-by-step instructions instead of a transient toast (user request
2026-09): the toast auto-dismisses and users lose the instructions.
"""

from odoo import fields, models


class MailManagementConnectionWizard(models.TransientModel):
    _name = "mail.management.connection.wizard"
    _description = "Connection Test — Action Required"

    mailbox_id = fields.Many2one(
        "mail.management.mailbox", readonly=True, ondelete="cascade"
    )
    title = fields.Char(readonly=True)
    message = fields.Html(readonly=True, sanitize=True)
    link_url = fields.Char(readonly=True)
    link_label = fields.Char(readonly=True)
    raw_error = fields.Text(readonly=True)

    def action_open_link(self):
        """Open the provider's help page in a new browser tab."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": self.link_url,
            "target": "new",
        }

    @staticmethod
    def build_message(guidance):
        """Render the guidance dict as wizard HTML.

        Returns a PLAIN string — the ``message`` field is
        ``fields.Html(sanitize=True)`` and sanitizes on write, so no
        ``Markup`` wrapping is needed (or safe-looking) here.
        """
        steps = "".join(f"<li>{step}</li>" for step in guidance["steps"])
        return f"<p>{guidance['intro']}</p><ol>{steps}</ol>"
