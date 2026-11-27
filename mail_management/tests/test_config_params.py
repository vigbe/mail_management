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



class TestInstallAdminDefault(TransactionCase):
    """S9: on install, the DEFAULT admin login (base.user_admin) gets the
    mail_management manager rights so the module is usable from the start.
    Only that user: nobody else — other admins included — is granted
    anything automatically."""

    def test_default_admin_has_manager_group(self):
        admin = self.env.ref("base.user_admin")
        self.assertTrue(
            admin.has_group("mail_management.group_mail_management_manager"),
            "base.user_admin must be a mail_management manager after install",
        )

    def test_no_other_user_gets_it_automatically(self):
        other = self.env["res.users"].create(
            {"name": "Other Admin", "login": "other_admin_mm"}
        )
        self.assertFalse(
            other.has_group("mail_management.group_mail_management_manager")
        )

