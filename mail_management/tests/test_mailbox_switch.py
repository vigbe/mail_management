"""PR-2j — click a mailbox label in the tree to switch mailbox."""

from pathlib import Path

from odoo.tests.common import TransactionCase

_STATIC = Path(__file__).parent.parent / "static" / "src"


def _read(rel):
    return (_STATIC / rel).read_text(encoding="utf-8")


class TestMailboxSwitch(TransactionCase):
    def test_label_is_clickable(self):
        xml = _read("xml/folder_tree.xml")
        self.assertIn("o_webmail_mailbox_label", xml)
        self.assertIn("selectMailbox(mailbox.id)", xml)
        self.assertIn('role="button"', xml)

    def test_prop_and_binding(self):
        self.assertIn("onSelectMailbox: t.function()", _read("components/folder_tree.js"))
        client_xml = _read("xml/webmail_client.xml")
        self.assertIn('onSelectMailbox.bind="this.selectMailbox"', client_xml)

    def test_client_selects_inbox_first(self):
        js = _read("webmail_client.js")
        self.assertIn("async selectMailbox(mailboxId)", js)
        self.assertIn('f.role === "inbox"', js)
        self.assertIn("mailbox.folders[0]", js)
        self.assertIn("await this.selectFolder(mailboxId, target.id)", js)

    def test_active_mailbox_highlighted(self):
        xml = _read("xml/folder_tree.xml")
        self.assertIn("props.selectedMailboxId", xml)
