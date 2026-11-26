"""Tests for per-user ir.rule isolation on mail.management.mailbox.

Strict-TDD RED: written before the full model implementation.
Verifies design B.3 — two group-scoped ir.rules (NOT a global own-only rule).
"""

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged("standard", "at_install")
class TestMailboxIsolation(TransactionCase):
    """User A cannot see User B's mailboxes; manager sees all."""

    def setUp(self):
        super().setUp()
        self.Mailbox = self.env["mail.management.mailbox"]
        group_user = self.env.ref("mail_management.group_mail_management_user")
        group_manager = self.env.ref("mail_management.group_mail_management_manager")

        self.user_a = self.env["res.users"].create(
            {
                "name": "User A",
                "login": "webmail_iso_a@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )
        self.user_b = self.env["res.users"].create(
            {
                "name": "User B",
                "login": "webmail_iso_b@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )
        self.manager = self.env["res.users"].create(
            {
                "name": "Manager",
                "login": "webmail_iso_mgr@test",
                "group_ids": [(6, 0, [group_manager.id])],
            }
        )

        # User A creates a mailbox.
        self.mailbox_a = self.Mailbox.with_user(self.user_a).create(
            {
                "name": "A's Mailbox",
                "provider_type": "generic",
                "imap_host": "imap.a.com",
                "imap_user": "a@a.com",
                "smtp_host": "smtp.a.com",
                "from_email": "a@a.com",
            }
        )

    def _mailbox_vals(self, **kw):
        base = {
            "name": kw.pop("name", "Test"),
            "provider_type": "generic",
            "imap_host": kw.pop("imap_host", "imap.test.com"),
            "imap_user": kw.pop("imap_user", "test@test.com"),
            "smtp_host": kw.pop("smtp_host", "smtp.test.com"),
            "from_email": kw.pop("from_email", "test@test.com"),
        }
        base.update(kw)
        return base

    # ------------------------------------------------------------------
    # User sees own
    # ------------------------------------------------------------------
    def test_user_a_sees_own_mailbox(self):
        """User A can see their own mailbox via search."""
        mailboxes = self.Mailbox.with_user(self.user_a).search([])
        self.assertIn(self.mailbox_a, mailboxes)

    # ------------------------------------------------------------------
    # Cross-user isolation
    # ------------------------------------------------------------------
    def test_user_b_does_not_see_user_a_mailbox(self):
        """User B's search does not include User A's mailbox."""
        mailboxes = self.Mailbox.with_user(self.user_b).search([])
        self.assertNotIn(self.mailbox_a, mailboxes)

    def test_user_b_cannot_read_user_a_mailbox(self):
        """User B cannot read User A's mailbox by id (AccessError)."""
        with self.assertRaises(AccessError):
            self.Mailbox.with_user(self.user_b).browse(self.mailbox_a.id).read(["name"])

    def test_user_b_can_create_own_mailbox(self):
        """User B can create and see their own mailbox."""
        mb_b = self.Mailbox.with_user(self.user_b).create(
            self._mailbox_vals(
                name="B's Mailbox",
                imap_host="imap.b.com",
                imap_user="b@b.com",
                smtp_host="smtp.b.com",
                from_email="b@b.com",
            )
        )
        mailboxes = self.Mailbox.with_user(self.user_b).search([])
        self.assertIn(mb_b, mailboxes)

    # ------------------------------------------------------------------
    # Manager sees all (the critical two-rule correctness point)
    # ------------------------------------------------------------------
    def test_manager_sees_all_mailboxes(self):
        """A manager (in both groups) sees all users' mailboxes."""
        mb_b = self.Mailbox.with_user(self.user_b).create(
            self._mailbox_vals(
                name="B's Mailbox",
                imap_host="imap.b.com",
                imap_user="b@b.com",
                smtp_host="smtp.b.com",
                from_email="b@b.com",
            )
        )
        mailboxes = self.Mailbox.with_user(self.manager).search([])
        self.assertIn(self.mailbox_a, mailboxes)
        self.assertIn(mb_b, mailboxes)

    def test_manager_is_not_restricted_to_own(self):
        """The manager rule [(1,'=',1)] is not ANDed with own-only."""
        # The manager has no own mailboxes but should still see all.
        mailboxes = self.Mailbox.with_user(self.manager).search([])
        self.assertIn(self.mailbox_a, mailboxes)
