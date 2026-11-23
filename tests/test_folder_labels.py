"""Folder display labels: friendly, localized, prefix-cleaned (user feedback 2026-09).

The raw IMAP names (e.g. ``INBOX.Archive``) must not be shown as-is; each
folder exposes a ``display_name`` driven by its role, falling back to a
cleaned name (leading ``INBOX.`` prefix stripped).
"""

from odoo.tests.common import TransactionCase  # type: ignore


class TestFolderLabels(TransactionCase):
    def _mailbox(self):
        return self.env["mail.management.mailbox"].create(
            {
                "name": "Labels Mailbox",
                "imap_host": "imap.example.com",
                "smtp_host": "smtp.example.com",
                "imap_user": "user@example.com",
                "from_email": "user@example.com",
                "credential": "secret",
            }
        )

    def _folder(self, imap_name, role):
        return self.env["mail.management.folder"].create(
            {
                "mailbox_id": self._mailbox().id,
                "imap_name": imap_name,
                "role": role,
            }
        )

    def test_display_name_uses_role_label(self):
        folder = self._folder("INBOX", "inbox")
        self.assertEqual(folder.display_name, "Inbox")

    def test_display_name_sent(self):
        folder = self._folder("INBOX.Sent", "sent")
        self.assertEqual(folder.display_name, "Sent")

    def test_display_name_drafts(self):
        folder = self._folder("INBOX.Draft", "drafts")
        self.assertEqual(folder.display_name, "Drafts")

    def test_display_name_trash(self):
        folder = self._folder("INBOX.Trash", "trash")
        self.assertEqual(folder.display_name, "Trash")

    def test_display_name_junk_role_exists(self):
        """Spam/Junk folders get their own role and a friendly label."""
        folder = self._folder("INBOX.Spam", "junk")
        self.assertEqual(folder.display_name, "Junk")

    def test_display_name_other_strips_inbox_prefix(self):
        folder = self._folder("INBOX.MyCustom", "other")
        self.assertEqual(folder.display_name, "MyCustom")

    def test_display_name_other_without_prefix_unchanged(self):
        folder = self._folder("Projects", "other")
        self.assertEqual(folder.display_name, "Projects")

    def test_guess_folder_role_junk_spam(self):
        Mailbox = self.env["mail.management.mailbox"]
        self.assertEqual(Mailbox._guess_folder_role("INBOX.Spam"), "junk")
        self.assertEqual(Mailbox._guess_folder_role("Junk"), "junk")
        self.assertEqual(Mailbox._guess_folder_role("Junk E-mail"), "junk")

    def test_display_name_translated_es_cl(self):
        """The es_CL PO must translate the role label (regression guard
        for the Odoo-19 PO grammar: entries need '#. module:' refs)."""
        # Self-sufficient: activate es_CL (loads the PO) instead of
        # relying on the language already being active in the test DB —
        # fresh databases (drop/recreate of local_test) don't have it.
        self.env["res.lang"]._activate_lang("es_CL")
        folder = self._folder("INBOX", "inbox").with_context(lang="es_CL")
        self.assertEqual(folder.display_name, "Bandeja de entrada")
