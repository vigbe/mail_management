"""PR-2j — per-mailbox collapse chevron + active highlight + per-mailbox config."""

from pathlib import Path

from odoo.tests.common import TransactionCase

_STATIC = Path(__file__).parent.parent / "static" / "src"


def _read(rel):
    return (_STATIC / rel).read_text(encoding="utf-8")


class TestTreeCollapse(TransactionCase):
    def test_label_has_chevron_and_toggle(self):
        xml = _read("xml/folder_tree.xml")
        self.assertIn("o_webmail_mailbox_chevron", xml)
        self.assertIn("toggleCollapse(mailbox.id)", xml)
        self.assertIn("'keyboard_arrow_right'", xml)
        self.assertIn("'keyboard_arrow_down'", xml)

    def test_folders_hidden_when_collapsed(self):
        xml = _read("xml/folder_tree.xml")
        self.assertIn('<t t-if="!this.isCollapsed(mailbox.id)">', xml)

    def test_label_click_expands_and_selects(self):
        js = _read("components/folder_tree.js")
        self.assertIn("selectMailbox(mailboxId)", js)
        self.assertIn("this.collapsed[mailboxId] = false", js)

    def test_active_mailbox_highlighted(self):
        xml = _read("xml/folder_tree.xml")
        self.assertIn("o_webmail_mailbox_active", xml)
        css = _read("scss/webmail.scss")
        self.assertIn("border-left: 3px solid var(--wmail-accent)", css)

    def test_per_mailbox_config(self):
        xml = _read("xml/folder_tree.xml")
        self.assertIn("onConfigureMailbox(mailbox.id)", xml)
        js = _read("webmail_client.js")
        self.assertIn("configureMailbox(mailboxId)", js)
        self.assertIn('views: [[false, "form"]]', js)
