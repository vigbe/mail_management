"""Strict-TDD RED tests for PR-2a — message list sorting + quick filters.

Contract under test (parent PR-2a scope + tasks.md):

``GET/POST /mail_management/messages`` gains WHITELISTED params:

* ``sort`` in {date, date_desc, from, from_desc, subject, subject_desc}
  (``from`` sorts on the stored ``email_from`` column);
* ``priority`` — 'unread_first' and/or 'flagged_first' (string or list,
  both may combine; the priority groups are applied on top of the chosen
  sort so unread/flagged messages rise to the top);
* ``filter`` in {unread, flagged, attachments} (quick filters).

Anything outside the whitelists falls back to the safe default — raw
input NEVER reaches the ORM ``order``/domain (no SQL interpolation).

The legacy ``order`` parameter (PR-1b-ii: 'date desc' | 'date asc') keeps
working; an explicit valid ``sort`` takes precedence over it.

Fixture: one folder, four messages chosen so every sort key, priority
combination and quick filter yields a DISTINCT expected order:

=====  ========  =====  ========  ========  ============
msg    date      from   subject   seen      flagged att
=====  ========  =====  ========  ========  ============
m1    2020      alice  Beta      False     False   no
m2    2021      bob    Alpha     True      True    yes
m3    2022      carol  Gamma     False     True    no
m4    2023      dave   Delta     True      False   yes
=====  ========  =====  ========  ========  ============
"""

import base64
import io
import os
from contextlib import contextmanager
from unittest import mock

from odoo import fields  # type: ignore
from odoo.modules.module import get_module_path  # type: ignore
from odoo.tests.common import TransactionCase, tagged  # type: ignore


@contextmanager
def mock_request(env):
    """Patch BOTH ``odoo.http.request`` and the controller-module request
    (PR-1c pattern — ``_()`` frame-walking needs the real proxy patched)."""
    fake = type("FakeRequest", (), {"env": env, "session": {}})()
    with (
        mock.patch("odoo.http.request", fake),
        mock.patch("odoo.addons.mail_management.controllers.main.request", fake),
    ):
        yield fake


def _mb_vals(**kw):
    base = {
        "name": "Sort Test",
        "provider_type": "generic",
        "imap_host": "imap.sort.com",
        "imap_user": "me@sort.com",
        "smtp_host": "smtp.sort.com",
        "from_email": "me@sort.com",
        "credential": "sort-pass",
        "state": "active",
    }
    base.update(kw)
    return base


@tagged("standard", "at_install")
class TestListSorting(TransactionCase):
    """Whitelisted sort / priority / filter params on /messages."""

    def setUp(self):
        super().setUp()
        from odoo.addons.mail_management.controllers.main import (  # type: ignore
            MailManagementController,
        )

        self.controller = MailManagementController()
        group_user = self.env.ref("mail_management.group_mail_management_user")
        self.user_a = self.env["res.users"].create(
            {
                "name": "Sort A",
                "login": "sort_a@test",
                "groups_id": [(6, 0, [group_user.id])],
            }
        )
        Mailbox = self.env["mail.management.mailbox"]
        Folder = self.env["mail.management.folder"]
        Msg = self.env["mail.management.message"]

        self.mailbox = Mailbox.with_user(self.user_a).create(_mb_vals())
        self.folder = Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "INBOX",
                "name": "Inbox",
                "role": "inbox",
            }
        )
        rows = [
            # (uid, date, email_from, subject, flag_seen, flag_flagged, att)
            (1, "2020-01-01 08:00:00", "alice@x.com", "Beta", False, False, False),
            (2, "2021-01-01 08:00:00", "bob@x.com", "Alpha", True, True, True),
            (3, "2022-01-01 08:00:00", "carol@x.com", "Gamma", False, True, False),
            (4, "2023-01-01 08:00:00", "dave@x.com", "Delta", True, False, True),
        ]
        self.msgs = []
        for uid, date, mail_from, subject, seen, flagged, att in rows:
            msg = Msg.create(
                {
                    "mailbox_id": self.mailbox.id,
                    "folder_id": self.folder.id,
                    "uid": uid,
                    "message_id": f"<s{uid}@sort.com>",
                    "subject": subject,
                    "email_from": mail_from,
                    "date": fields.Datetime.to_datetime(date),
                    "flag_seen": seen,
                    "flag_flagged": flagged,
                }
            )
            if att:
                ir_att = self.env["ir.attachment"].create(
                    {
                        "name": f"att{uid}.txt",
                        "datas": base64.b64encode(
                            io.BytesIO(f"att{uid}".encode()).getvalue()
                        ).decode(),
                        "res_model": "mail.management.message",
                        "res_id": msg.id,
                        "mimetype": "text/plain",
                    }
                )
                self.env["mail.management.attachment"].create(
                    {
                        "webmail_message_id": msg.id,
                        "name": f"att{uid}.txt",
                        "mimetype": "text/plain",
                        "file_size": 5,
                        "ir_attachment_id": ir_att.id,
                    }
                )
            self.msgs.append(msg)

        # m1, m2, m3, m4 shortcuts.
        self.m1, self.m2, self.m3, self.m4 = self.msgs

    # -- helper ----------------------------------------------------------

    def _ids(self, **kw):
        with mock_request(self.env(user=self.user_a)):
            rows = self.controller.get_messages(folder_id=self.folder.id, **kw)
        return [r["id"] for r in rows]

    def _order(self, *msgs):
        return [m.id for m in msgs]

    # ------------------------------------------------------------------
    # sort whitelist
    # ------------------------------------------------------------------

    def test_sort_date_asc(self):
        self.assertEqual(
            self._ids(sort="date"), self._order(self.m1, self.m2, self.m3, self.m4)
        )

    def test_sort_date_desc(self):
        self.assertEqual(
            self._ids(sort="date_desc"),
            self._order(self.m4, self.m3, self.m2, self.m1),
        )

    def test_sort_from_asc(self):
        self.assertEqual(
            self._ids(sort="from"), self._order(self.m1, self.m2, self.m3, self.m4)
        )

    def test_sort_from_desc(self):
        self.assertEqual(
            self._ids(sort="from_desc"),
            self._order(self.m4, self.m3, self.m2, self.m1),
        )

    def test_sort_subject_asc(self):
        self.assertEqual(
            self._ids(sort="subject"),
            self._order(self.m2, self.m1, self.m4, self.m3),
        )

    def test_sort_subject_desc(self):
        self.assertEqual(
            self._ids(sort="subject_desc"),
            self._order(self.m3, self.m4, self.m1, self.m2),
        )

    # ------------------------------------------------------------------
    # priority (unread_first / flagged_first, combinable)
    # ------------------------------------------------------------------

    def test_priority_unread_first_with_date_desc(self):
        self.assertEqual(
            self._ids(sort="date_desc", priority="unread_first"),
            self._order(self.m3, self.m1, self.m4, self.m2),
        )

    def test_priority_flagged_first_with_date_desc(self):
        self.assertEqual(
            self._ids(sort="date_desc", priority="flagged_first"),
            self._order(self.m3, self.m2, self.m4, self.m1),
        )

    def test_priority_both_combined(self):
        """Both priorities combine: unread group first, flagged top within
        each group, then the primary sort inside the groups."""
        self.assertEqual(
            self._ids(
                sort="date_desc",
                priority=["unread_first", "flagged_first"],
            ),
            self._order(self.m3, self.m1, self.m2, self.m4),
        )

    def test_priority_with_subject_sort(self):
        self.assertEqual(
            self._ids(sort="subject", priority="flagged_first"),
            self._order(self.m2, self.m3, self.m1, self.m4),
        )

    # ------------------------------------------------------------------
    # quick filters
    # ------------------------------------------------------------------

    def test_filter_unread(self):
        """Filters keep the default sort (date desc)."""
        self.assertEqual(self._ids(filter="unread"), self._order(self.m3, self.m1))

    def test_filter_flagged(self):
        self.assertEqual(self._ids(filter="flagged"), self._order(self.m3, self.m2))

    def test_filter_attachments(self):
        self.assertEqual(self._ids(filter="attachments"), self._order(self.m4, self.m2))

    def test_filter_combined_with_sort(self):
        """Filter + sort compose: unread only, oldest first."""
        self.assertEqual(
            self._ids(sort="date", filter="unread"), self._order(self.m1, self.m3)
        )

    def test_filter_combined_with_priority_and_sort(self):
        """The full triple: flagged filter + unread-first + date desc."""
        self.assertEqual(
            self._ids(sort="date_desc", priority="unread_first", filter="flagged"),
            self._order(self.m3, self.m2),
        )

    # ------------------------------------------------------------------
    # TRIANGULATE — pagination + priority order-dependence
    # ------------------------------------------------------------------

    def test_filter_with_limit_and_offset(self):
        """Quick filters compose with pagination (limit/offset apply after
        the filter, in the current sort order)."""
        with mock_request(self.env(user=self.user_a)):
            page1 = self.controller.get_messages(
                folder_id=self.folder.id, filter="attachments", limit=1, offset=0
            )
            page2 = self.controller.get_messages(
                folder_id=self.folder.id, filter="attachments", limit=1, offset=1
            )
        self.assertEqual([r["id"] for r in page1], [self.m4.id])
        self.assertEqual([r["id"] for r in page2], [self.m2.id])

    def test_priority_order_is_caller_defined(self):
        """['flagged_first', 'unread_first'] (reversed) groups flagged
        first, then unread INSIDE each group — the caller's order wins
        (unflagged group: m1 is unread → before m4)."""
        self.assertEqual(
            self._ids(
                sort="date_desc",
                priority=["flagged_first", "unread_first"],
            ),
            self._order(self.m3, self.m2, self.m1, self.m4),
        )

    def test_sort_with_priority_unread_first_asc(self):
        """Priority + ascending primary sort: unread group first, oldest
        first inside the groups."""
        self.assertEqual(
            self._ids(sort="date", priority="unread_first"),
            self._order(self.m1, self.m3, self.m2, self.m4),
        )

    # ------------------------------------------------------------------
    # invalid input → safe default (never interpolated)
    # ------------------------------------------------------------------

    def test_invalid_sort_falls_back_to_date_desc(self):
        default = self._ids(sort=None)
        forged = self._ids(sort="date desc; DROP TABLE messages; --")
        self.assertEqual(forged, default)
        self.assertEqual(forged, self._order(self.m4, self.m3, self.m2, self.m1))

    def test_invalid_filter_ignored(self):
        self.assertEqual(
            self._ids(filter="all' OR true; --"),
            self._order(self.m4, self.m3, self.m2, self.m1),
        )

    def test_invalid_priority_ignored(self):
        self.assertEqual(
            self._ids(sort="date_desc", priority=["admin_first", 42]),
            self._order(self.m4, self.m3, self.m2, self.m1),
        )

    # ------------------------------------------------------------------
    # legacy order parameter keeps working (no PR-1b-ii regression)
    # ------------------------------------------------------------------

    def test_legacy_order_param_still_honored(self):
        self.assertEqual(
            self._ids(order="date asc"), self._order(self.m1, self.m2, self.m3, self.m4)
        )

    def test_sort_takes_precedence_over_legacy_order(self):
        self.assertEqual(
            self._ids(sort="date", order="date desc"),
            self._order(self.m1, self.m2, self.m3, self.m4),
        )

    def test_default_remains_date_desc(self):
        self.assertEqual(self._ids(), self._order(self.m4, self.m3, self.m2, self.m1))

    def test_isolation_other_users_folder_empty(self):
        """The list stays per-user: another user sees nothing in this
        folder (ir.rules)."""
        group_user = self.env.ref("mail_management.group_mail_management_user")
        user_b = self.env["res.users"].create(
            {
                "name": "Sort B",
                "login": "sort_b@test",
                "groups_id": [(6, 0, [group_user.id])],
            }
        )
        with mock_request(self.env(user=user_b)):
            rows = self.controller.get_messages(folder_id=self.folder.id)
        self.assertEqual(rows, [])


# ===========================================================================
# Sorting/filter UI wiring (headless, template/JS source assertions)
# ===========================================================================


def _read(relative):
    path = os.path.join(get_module_path("mail_management"), relative)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@tagged("standard", "at_install")
class TestSortingUiWiring(TransactionCase):
    """PR-2e consolidation: ONE sort dropdown (date/from/subject ×
    asc/desc + Unread-first/Starred-first checkboxes) replaces the three
    clickable headers and the two separate toggles.  The CONTROLLER
    contract (sort/priority whitelists) is untouched."""

    def test_message_list_has_one_sort_menu(self):
        xml = _read("static/src/xml/message_list.xml")
        for needle in (
            "o_webmail_sort_menu_btn",
            "o_webmail_sort_menu",
            "o_webmail_sort_menu_item",
            # The sort key is bound dynamically from sortItems (t-att-),
            # with the six PR-2a whitelisted keys in the JS getter.
            't-att-data-sort-key="item.key"',
            "applySort",
        ):
            self.assertIn(needle, xml)
        js = _read("static/src/components/message_list.js")
        for key in (
            '"date"',
            '"date_desc"',
            '"from"',
            '"from_desc"',
            '"subject"',
            '"subject_desc"',
        ):
            self.assertIn(key, js)

    def test_message_list_sort_menu_priority_checkboxes(self):
        xml = _read("static/src/xml/message_list.xml")
        for needle in (
            'data-priority="unread_first"',
            'data-priority="flagged_first"',
            "togglePriority",
        ):
            self.assertIn(needle, xml)
        # The old clickable headers + standalone toggles are gone.
        self.assertNotIn("o_webmail_sort_header", xml)
        self.assertNotIn("o_webmail_priority_toggle", xml)

    def test_message_list_has_filter_chips(self):
        xml = _read("static/src/xml/message_list.xml")
        for needle in (
            "o_webmail_filter_chip",
            'data-filter="unread"',
            'data-filter="flagged"',
            'data-filter="attachments"',
            "onSetFilter",
        ):
            self.assertIn(needle, xml)

    def test_client_wires_sort_state_and_handlers(self):
        js = _read("static/src/webmail_client.js")
        for needle in (
            "messageSort",
            "messageFilter",
            "messagePriority",
            "setSort",
            "togglePriority",
            "setFilter",
        ):
            self.assertIn(needle, js)
        xml = _read("static/src/xml/webmail_client.xml")
        for needle in (
            "onSetSort.bind",
            "onTogglePriority.bind",
            "onSetFilter.bind",
            'sortBy="state.messageSort"',
            'filter="state.messageFilter"',
        ):
            self.assertIn(needle, xml)
