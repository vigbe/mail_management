"""PR-2j — draggable pane splitters (wiring needles).

OWL 2 contract: NO t-ref on components. The pane element is resolved via DOM
relations (splitter's previousElementSibling) and one root ref on a plain div.
"""

from pathlib import Path

from odoo.tests.common import TransactionCase

_STATIC = Path(__file__).parent.parent / "static" / "src"


def _read(rel):
    return (_STATIC / rel).read_text(encoding="utf-8")


class TestPaneSplitters(TransactionCase):
    def test_template_has_two_splitters(self):
        xml = _read("xml/webmail_client.xml")
        self.assertEqual(xml.count("o_webmail_splitter"), 2)
        self.assertIn("startSplitterDrag('tree', ev)", xml)
        self.assertIn("startSplitterDrag('list', ev)", xml)
        self.assertIn('t-on-mounted="applySavedWidths"', xml)

    def test_no_tref_on_components(self):
        """OWL 2 rejects t-ref on component tags — regression guard for the
        production crash of 2026-09-11."""
        xml = _read("xml/webmail_client.xml")
        self.assertNotIn("<FolderTree t-ref", xml)
        self.assertNotIn("<MessageList t-ref", xml)
        self.assertIn('t-ref="root"', xml)  # ref only on the plain root div

    def test_drag_logic_bounds_and_persistence(self):
        js = _read("webmail_client.js")
        self.assertIn("previousElementSibling", js)
        self.assertIn("{ min: 160, max: 460 }", js)
        self.assertIn("{ min: 280, max: 760 }", js)
        self.assertIn('"o_webmail." + which + "_width"', js)
        self.assertIn("removeEventListener", js)

    def test_splitter_styles(self):
        css = _read("scss/webmail.scss")
        self.assertIn(".o_webmail_splitter", css)
        self.assertIn("col-resize", css)
        self.assertIn("body.o_webmail_dragging", css)
