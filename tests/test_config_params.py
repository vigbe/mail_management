"""Config-parameter hardening: bad admin values degrade to defaults (no crash)."""

from odoo.tests.common import TransactionCase


class TestConfigParams(TransactionCase):
    def _set(self, key, value):
        self.env["ir.config_parameter"].sudo().set_param(key, value)

    def test_bad_imap_timeout_falls_back(self):
        self._set("mail_management.imap_timeout", "30s")  # admin typo
        mailbox = self.env["mail.management.mailbox"]
        self.assertEqual(mailbox._get_imap_timeout(), 30)

    def test_zero_timeout_falls_back(self):
        self._set("mail_management.imap_timeout", "0")
        mailbox = self.env["mail.management.mailbox"]
        self.assertEqual(mailbox._get_imap_timeout(), 30)

    def test_bad_cron_batch_falls_back(self):
        self._set("mail_management.cron_batch_size", "twenty")
        mailbox = self.env["mail.management.mailbox"]
        self.assertEqual(mailbox._safe_int("twenty", 20), 20)

    def test_none_falls_back(self):
        mailbox = self.env["mail.management.mailbox"]
        self.assertEqual(mailbox._safe_int(None, 50), 50)

    def test_good_value_passes_through(self):
        mailbox = self.env["mail.management.mailbox"]
        self.assertEqual(mailbox._safe_int("7", 20), 7)
