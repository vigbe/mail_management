"""PR-2j — signature template generator."""

from odoo.tests.common import TransactionCase

# 1x1 PNG transparente
_TINY_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class TestSignatureTemplates(TransactionCase):
    def _signature(self, **kw):
        vals = {"name": "Firma", "template_key": "classic"}
        vals.update(kw)
        return self.env["mail.management.signature"].create(vals)

    def test_generate_classic(self):
        sig = self._signature(
            contact_name="Ana Pérez",
            contact_title="Gerente",
            contact_email="ana@x.cl",
            contact_phone="+56 9 1234 5678",
        )
        sig.action_generate_signature()
        body = sig.html_body
        for expected in ("Ana Pérez", "Gerente", "ana@x.cl", "+56 9 1234 5678"):
            self.assertIn(expected, body)
        self.assertIn("<table", body)  # email-safe

    def test_generate_all_templates(self):
        for key in ("modern", "minimal", "banner", "elegant", "corporate", "compact"):
            sig = self._signature(
                template_key=key,
                contact_name="Ana",
                contact_title="Jefa",
                contact_email="a@x.cl",
                contact_phone="9",
            )
            sig.action_generate_signature()
            self.assertIn("Ana", sig.html_body, key)

    def test_logo_embedded_as_data_uri(self):
        sig = self._signature(contact_name="Ana", logo=_TINY_PNG)
        sig.action_generate_signature()
        self.assertIn("data:image/png;base64,", sig.html_body)

    def test_escaping_of_user_input(self):
        sig = self._signature(
            contact_name="<script>alert(1)</script>",
            contact_title="<b>Cargo</b>",
        )
        sig.action_generate_signature()
        self.assertNotIn("<script>", sig.html_body)
        self.assertIn("&lt;script&gt;", sig.html_body)

    def test_defaults_from_user(self):
        sig = self._signature()  # sin datos de contacto
        sig.action_generate_signature()
        self.assertIn(self.env.user.name, sig.html_body)

    def test_none_template_warns_and_keeps_body(self):
        sig = self._signature(template_key="none", html_body="<p>manual</p>")
        result = sig.action_generate_signature()
        self.assertEqual(result["params"]["type"], "warning")
        self.assertIn("manual", sig.html_body)


class TestSignatureViewUx(TransactionCase):
    def test_view_responsive_grid_and_modal(self):
        from pathlib import Path

        xml = (
            Path(__file__).parent.parent
            / "views"
            / "mail_management_signature_views.xml"
        ).read_text(encoding="utf-8")
        # grilla 3 -> 2 -> 1
        self.assertIn("col-lg-4", xml)
        self.assertIn("col-md-6", xml)
        self.assertIn("col-12", xml)
        # administrar/crear firmas abre en MODAL
        self.assertIn('<field name="target">new</field>', xml)
