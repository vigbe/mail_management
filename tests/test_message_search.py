"""Strict-TDD RED tests for PR-2d — local message search + webmail mailbox
management + es_CL coverage.

Contract under test (parent PR-2d scope + tasks.md):

``POST /mail_management/messages`` gains a sanitized ``search`` param:

* ilike across ``subject``, ``email_from``, ``email_to`` and ``body_text``
  (case-insensitive);
* ``%`` and ``_`` in the INPUT are treated literally (LIKE-wildcard escape);
* when a search is active the scope becomes the CURRENT MAILBOX across ALL
  its folders (the ``folder_id`` param resolves the mailbox);
* the search composes with the existing ``sort``/``priority``/``filter``
  whitelists (PR-2a);
* a blank/whitespace ``search`` is IGNORED (plain folder view);
* cross-user isolation stays enforced by the per-user ir.rules.

UI wiring (headless source assertions, PR-2a pattern): the search bar
(input + apply button), the "Searching: 'x' — clear" indicator, and the
folder-tree "Add mailbox" / "Refresh all" management menu.

es_CL regression: one NEW Python-side term of this change must translate
(mirrors tests/test_folder_labels.py::test_display_name_translated_es_cl).
"""

import ast
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
        "name": "Search Test",
        "provider_type": "generic",
        "imap_host": "imap.search.com",
        "imap_user": "me@search.com",
        "smtp_host": "smtp.search.com",
        "from_email": "me@search.com",
        "credential": "search-pass",
        "state": "active",
    }
    base.update(kw)
    return base


@tagged("standard", "at_install")
class TestMessageSearch(TransactionCase):
    """Sanitized ``search`` param on /mail_management/messages."""

    def setUp(self):
        super().setUp()
        from odoo.addons.mail_management.controllers.main import (  # type: ignore
            MailManagementController,
        )

        self.controller = MailManagementController()
        group_user = self.env.ref("mail_management.group_mail_management_user")
        self.user_a = self.env["res.users"].create(
            {
                "name": "Search A",
                "login": "search_a@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )
        Mailbox = self.env["mail.management.mailbox"]
        Folder = self.env["mail.management.folder"]
        Msg = self.env["mail.management.message"]

        # Two folders in the SAME mailbox: searches must span both.
        self.mailbox = Mailbox.with_user(self.user_a).create(_mb_vals())
        self.inbox = Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "INBOX",
                "name": "Inbox",
                "role": "inbox",
            }
        )
        self.archive = Folder.create(
            {
                "mailbox_id": self.mailbox.id,
                "imap_name": "Archive",
                "name": "Archive",
                "role": "archive",
            }
        )
        # A SECOND mailbox of the same user: search must stay scoped to the
        # CURRENT mailbox (never leak across mailboxes).
        self.mailbox2 = Mailbox.with_user(self.user_a).create(
            _mb_vals(name="Second", imap_user="me2@search.com")
        )
        self.other_folder = Folder.create(
            {
                "mailbox_id": self.mailbox2.id,
                "imap_name": "INBOX",
                "name": "Inbox",
                "role": "inbox",
            }
        )

        rows = [
            # (folder, uid, date, from, to, subject, body, seen, flagged)
            (
                self.inbox,
                1,
                "2020-01-01 08:00:00",
                "alice@corp.com",
                "me@corp.com",
                "Quarterly Report",
                "The numbers are looking good this quarter.",
                False,
                False,
            ),
            (
                self.archive,
                1,
                "2021-01-01 08:00:00",
                "bob@corp.com",
                "team@corp.com",
                "Garden Party",
                "Bring your own chair to the park.",
                True,
                False,
            ),
            (
                self.inbox,
                2,
                "2022-01-01 08:00:00",
                "carol@corp.com",
                "billing@corp.com",
                "Invoice 100% paid",
                "Payment was confirmed today.",
                True,
                False,
            ),
            (
                self.inbox,
                3,
                "2023-01-01 08:00:00",
                "dave@corp.com",
                "billing@corp.com",
                "Invoice 1004 late",
                "Still awaiting payment.",
                False,
                False,
            ),
            (
                self.inbox,
                4,
                "2024-01-01 08:00:00",
                "erin@corp.com",
                "dev@corp.com",
                "snake_case notation",
                "We prefer snake_case here.",
                True,
                False,
            ),
            (
                self.inbox,
                5,
                "2025-01-01 08:00:00",
                "frank@corp.com",
                "dev@corp.com",
                "snakeXcase notation",
                "We actually prefer camelCase.",
                True,
                False,
            ),
            # Same subject as m1 but in the SECOND mailbox.
            (
                self.other_folder,
                1,
                "2026-01-01 08:00:00",
                "zoe@other.com",
                "me@search.com",
                "Quarterly Report",
                "Other mailbox content.",
                False,
                False,
            ),
        ]
        self.msgs = []
        for folder, uid, date, mail_from, mail_to, subject, body, seen, flagged in rows:
            self.msgs.append(
                Msg.create(
                    {
                        "mailbox_id": folder.mailbox_id.id,
                        "folder_id": folder.id,
                        "uid": uid,
                        "message_id": f"<q{uid}-{folder.imap_name}@search.com>",
                        "subject": subject,
                        "email_from": mail_from,
                        "email_to": mail_to,
                        "body_text": body,
                        "date": fields.Datetime.to_datetime(date),
                        "flag_seen": seen,
                        "flag_flagged": flagged,
                    }
                )
            )
        (self.m1, self.m2, self.m3, self.m4, self.m5, self.m6, self.m7) = self.msgs

    # -- helpers -----------------------------------------------------------

    def _ids(self, folder=None, **kw):
        folder = folder or self.inbox
        with mock_request(self.env(user=self.user_a)):
            rows = self.controller.get_messages(folder_id=folder.id, **kw)
        return [r["id"] for r in rows]

    def _order(self, *msgs):
        return [m.id for m in msgs]

    # ------------------------------------------------------------------
    # matches across the four searched fields
    # ------------------------------------------------------------------

    def test_search_matches_subject(self):
        self.assertEqual(self._ids(search="quarterly report"), self._order(self.m1))

    def test_search_matches_from(self):
        self.assertEqual(self._ids(search="alice@corp.com"), self._order(self.m1))

    def test_search_matches_to(self):
        self.assertEqual(self._ids(search="team@corp.com"), self._order(self.m2))

    def test_search_matches_body_text(self):
        self.assertEqual(self._ids(search="chair"), self._order(self.m2))

    def test_search_case_insensitive(self):
        """ilike: any-field, any-case matching."""
        self.assertEqual(self._ids(search="GARDEN PARTY"), self._order(self.m2))
        self.assertEqual(self._ids(search="quarterly"), self._order(self.m1))

    # ------------------------------------------------------------------
    # LIKE wildcards in the INPUT are literal
    # ------------------------------------------------------------------

    def test_search_percent_treated_literally(self):
        """'100%' matches only the literal 'Invoice 100% paid', never
        'Invoice 1004 late' (an unescaped % would match both)."""
        self.assertEqual(self._ids(search="100%"), self._order(self.m3))

    def test_search_underscore_treated_literally(self):
        """'snake_case' must not match 'snakeXcase' (an unescaped _ would)."""
        self.assertEqual(self._ids(search="snake_case"), self._order(self.m5))

    # ------------------------------------------------------------------
    # scope: current mailbox, all its folders
    # ------------------------------------------------------------------

    def test_search_spans_all_folders_of_current_mailbox(self):
        """Browsing INBOX, the search finds the ARCHIVE message too (the
        results replace the folder list — mailbox-wide scope)."""
        self.assertEqual(self._ids(search="party"), self._order(self.m2))

    def test_search_never_leaks_other_mailbox(self):
        """The same subject exists in the user's SECOND mailbox — searching
        while browsing mailbox 1 must NOT return it."""
        self.assertEqual(self._ids(search="quarterly"), self._order(self.m1))

    # ------------------------------------------------------------------
    # empty results / blank input
    # ------------------------------------------------------------------

    def test_search_empty_result(self):
        self.assertEqual(self._ids(search="zzz-no-such-thing"), [])

    def test_search_blank_ignored(self):
        """'' and '   ' behave exactly like no search param (folder view)."""
        expected = self._order(self.m6, self.m5, self.m4, self.m3, self.m1)
        self.assertEqual(self._ids(search=""), expected)
        self.assertEqual(self._ids(search="   "), expected)
        self.assertEqual(self._ids(), expected)

    # ------------------------------------------------------------------
    # composition with the PR-2a whitelists
    # ------------------------------------------------------------------

    def test_search_combined_with_filter(self):
        """'invoice' matches m3 (seen) + m4 (unread); filter=unread keeps m4."""
        self.assertEqual(
            self._ids(search="invoice", filter="unread"), self._order(self.m4)
        )

    def test_search_combined_with_sort(self):
        """Search results keep honoring the sort whitelist (subject asc)."""
        self.assertEqual(
            self._ids(search="invoice", sort="subject"),
            self._order(self.m3, self.m4),
        )
        self.assertEqual(
            self._ids(search="invoice", sort="subject_desc"),
            self._order(self.m4, self.m3),
        )

    def test_search_combined_with_priority(self):
        """unread_first rises the unread invoice to the top."""
        self.assertEqual(
            self._ids(search="invoice", priority="unread_first"),
            self._order(self.m4, self.m3),
        )

        # ------------------------------------------------------------------

    # TRIANGULATE — phrase semantics, accents, long bodies, trim, pages
    # ------------------------------------------------------------------

    def test_search_multi_word_phrase(self):
        """A multi-word query is a PHRASE (single ilike), not word soup."""
        self.assertEqual(self._ids(search="quarterly report"), self._order(self.m1))
        # 'report quarterly' (reversed) must NOT match 'Quarterly Report'.
        self.assertEqual(self._ids(search="report quarterly"), [])

    def test_search_unicode_accents(self):
        msg = self.env["mail.management.message"].create(
            {
                "mailbox_id": self.mailbox.id,
                "folder_id": self.inbox.id,
                "uid": 99,
                "message_id": "<accent@search.com>",
                "subject": "Informe de diseño",
                "email_from": "diseno@corp.com",
                "email_to": "me@corp.com",
                "body_text": "Reunión sobre el diseño de la campaña",
                "date": fields.Datetime.to_datetime("2020-05-05 08:00:00"),
            }
        )
        self.assertEqual(self._ids(search="diseño"), [msg.id])
        self.assertEqual(self._ids(search="reunión"), [msg.id])

    def test_search_long_body_middle_match(self):
        filler = "lorem ipsum " * 900  # ~10k chars
        needle = "needle-in-the-middle-of-a-very-long-body"
        msg = self.env["mail.management.message"].create(
            {
                "mailbox_id": self.mailbox.id,
                "folder_id": self.archive.id,
                "uid": 98,
                "message_id": "<long@search.com>",
                "subject": "Unrelated subject",
                "email_from": "long@corp.com",
                "email_to": "me@corp.com",
                "body_text": f"{filler} {needle} {filler}",
                "date": fields.Datetime.to_datetime("2020-06-06 08:00:00"),
            }
        )
        self.assertEqual(self._ids(search=needle), [msg.id])

    def test_search_input_trimmed_before_use(self):
        """Surrounding whitespace never blocks a match."""
        self.assertEqual(self._ids(search="  garden  "), self._order(self.m2))

    def test_search_composes_with_limit_offset(self):
        """Pagination applies to the search result set, in sort order."""
        with mock_request(self.env(user=self.user_a)):
            page1 = self.controller.get_messages(
                folder_id=self.inbox.id, search="notation", limit=1, offset=0
            )
            page2 = self.controller.get_messages(
                folder_id=self.inbox.id, search="notation", limit=1, offset=1
            )
        self.assertEqual([r["id"] for r in page1], [self.m6.id])
        self.assertEqual([r["id"] for r in page2], [self.m5.id])

    # ------------------------------------------------------------------
    # isolation
    # ------------------------------------------------------------------

    def test_search_cross_user_isolation(self):
        """User B never sees user A's messages, whatever they search for;
        searching in A's folder id resolves nothing for B (ir.rules)."""
        group_user = self.env.ref("mail_management.group_mail_management_user")
        user_b = self.env["res.users"].create(
            {
                "name": "Search B",
                "login": "search_b@test",
                "group_ids": [(6, 0, [group_user.id])],
            }
        )
        mailbox_b = (
            self.env["mail.management.mailbox"]
            .with_user(user_b)
            .create(_mb_vals(name="B Mailbox", imap_user="b@search.com"))
        )
        folder_b = self.env["mail.management.folder"].create(
            {
                "mailbox_id": mailbox_b.id,
                "imap_name": "INBOX",
                "name": "Inbox",
                "role": "inbox",
            }
        )
        msg_b = self.env["mail.management.message"].create(
            {
                "mailbox_id": mailbox_b.id,
                "folder_id": folder_b.id,
                "uid": 1,
                "message_id": "<b1@search.com>",
                "subject": "Quarterly Report",
                "email_from": "b-friend@corp.com",
                "email_to": "b@search.com",
                "body_text": "b body",
                "date": fields.Datetime.to_datetime("2020-06-06 08:00:00"),
            }
        )
        # B searching their own INBOX sees only their own message.
        with mock_request(self.env(user=user_b)):
            rows = self.controller.get_messages(
                folder_id=folder_b.id, search="quarterly report"
            )
        self.assertEqual([r["id"] for r in rows], [msg_b.id])
        # B searching inside A's folder id resolves NOTHING (ir.rules).
        with mock_request(self.env(user=user_b)):
            rows = self.controller.get_messages(
                folder_id=self.inbox.id, search="quarterly report"
            )
        self.assertEqual(rows, [])


# ===========================================================================
# UI wiring (headless, template/JS source assertions — PR-2a pattern)
# ===========================================================================


def _read(relative):
    path = os.path.join(get_module_path("mail_management"), relative)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@tagged("standard", "at_install")
class TestSearchUiWiring(TransactionCase):
    """Search bar + indicator + folder-tree mailbox management menu."""

    def test_message_list_has_search_bar(self):
        xml = _read("static/src/xml/message_list.xml")
        for needle in (
            "o_webmail_search_input",
            "o_webmail_search_btn",
            "onSearch",
        ):
            self.assertIn(needle, xml)

    def test_message_list_has_search_indicator_with_clear(self):
        xml = _read("static/src/xml/message_list.xml")
        for needle in (
            "o_webmail_search_indicator",
            "o_webmail_search_clear",
            "clearSearch",
            "searchIndicatorLabel",
        ):
            self.assertIn(needle, xml)
        # The indicator label itself is translated in the JS (getter).
        js = _read("static/src/components/message_list.js")
        self.assertIn("Searching:", js)

    def test_message_list_js_wires_search_handlers(self):
        js = _read("static/src/components/message_list.js")
        for needle in (
            "searchQuery",
            "onSearchInput",
            "applySearchNow",
            "clearSearch",
            "_t(",
        ):
            self.assertIn(needle, js)

    def test_client_wires_search_state_and_rpc(self):
        js = _read("static/src/webmail_client.js")
        for needle in (
            "searchQuery",
            "applySearch",
            "clearSearch",
        ):
            self.assertIn(needle, js)
        # The RPC actually carries the search param.
        self.assertRegex(js, r"params\.search\s*=\s*")
        xml = _read("static/src/xml/webmail_client.xml")
        for needle in (
            "onSearch.bind",
            'searchQuery="state.searchQuery"',
        ):
            self.assertIn(needle, xml)

    def test_folder_tree_has_mailbox_management_menu(self):
        xml = _read("static/src/xml/folder_tree.xml")
        for needle in (
            "o_webmail_manage_btn",
            "o_webmail_add_mailbox_item",
            "o_webmail_refresh_all_item",
            "toggleManageMenu",
        ):
            self.assertIn(needle, xml)
        # The menu items delegate through component methods to the props.
        js = _read("static/src/components/folder_tree.js")
        for needle in (
            "onAddMailbox",
            "onRefreshAll",
            "addMailboxLabel",
            "refreshAllLabel",
        ):
            self.assertIn(needle, js)

    def test_client_opens_mailbox_form_and_refreshes_all(self):
        js = _read("static/src/webmail_client.js")
        for needle in (
            "openAddMailbox",
            "doAction",
            "mail.management.mailbox",
            "refreshAll",
            '"/mail_management/refresh"',
        ):
            self.assertIn(needle, js)
        xml = _read("static/src/xml/webmail_client.xml")
        for needle in (
            "onAddMailbox.bind",
            "onRefreshAll.bind",
        ):
            self.assertIn(needle, xml)


# ===========================================================================
# es_CL regression — a NEW Python-side term of this change must translate
# ===========================================================================


@tagged("standard", "at_install")
class TestNewTermTranslatedEsCl(TransactionCase):
    """PR-2d extends the es_CL PO to every NEW string of this change.  The
    Odoo-19 PO grammar (module + flavor markers) is guarded by this test:
    a missing/malformed entry leaves the term English (and a malformed PO
    crashes the registry at load — the canonical -u run is the guard)."""

    def test_folder_name_empty_translated_es_cl(self):
        """PR-2b UserError term, translated via the env-bound translator
        (mirrors test_folder_labels.py::test_display_name_translated_es_cl)."""
        self.env["res.lang"]._activate_lang("es_CL")
        env = self.env["mail.management.folder"].with_context(lang="es_CL").env
        self.assertEqual(
            env._("The folder name cannot be empty."),
            "El nombre de la carpeta no puede estar vacío.",
        )

    def test_po_entries_have_odoo_grammar(self):
        """Every es_CL entry carries BOTH required comment lines (module +
        flavor marker) — the empirically learned importer grammar."""
        po = _read("i18n/es_CL.po")
        entries = po.split("\n\n")
        checked = 0
        for entry in entries:
            if "msgid " not in entry or 'msgid ""' in entry:
                continue
            self.assertIn("#. module: mail_management", entry)
            # Model-translation entries (menu/action labels) use '#: model:'
            # occurrences and legitimately carry NO code flavor marker.
            if "#: model:" not in entry:
                self.assertTrue(
                    "#. odoo-python" in entry or "#. odoo-javascript" in entry,
                    f"entry lacks a flavor marker:\n{entry}",
                )
            checked += 1
        self.assertGreater(checked, 7)  # the 7 pre-existing + the new ones

    def test_pot_mirrors_po_entries(self):
        """The POT has the SAME msgids (polib merge replaces comments with
        the POT's — a missing POT marker would corrupt the DB on upgrade)."""
        po = _read("i18n/es_CL.po")
        pot = _read("i18n/mail_management.pot")
        po_ids = [
            line.split('"')[1]
            for line in po.splitlines()
            if line.startswith("msgid ") and line != 'msgid ""'
        ]
        pot_ids = [
            line.split('"')[1]
            for line in pot.splitlines()
            if line.startswith("msgid ") and line != 'msgid ""'
        ]
        self.assertEqual(sorted(po_ids), sorted(pot_ids))
        for msgid in po_ids:
            block = self._entry_block(pot, msgid)
            self.assertIn("#. module: mail_management", block)
            if "#: model:" not in block:
                self.assertTrue(
                    "#. odoo-python" in block or "#. odoo-javascript" in block,
                    f"POT entry lacks a flavor marker: {msgid}",
                )

    @staticmethod
    def _entry_block(pot, msgid):
        lines = pot.splitlines()
        idx = lines.index(f'msgid "{msgid}"')
        # Walk back over the contiguous comment lines (#. / #:) directly
        # above the msgid — that block is what polib merge keeps/replaces.
        start = idx
        while start > 0 and lines[start - 1].startswith("#"):
            start -= 1
        end = idx + 2  # msgid + msgstr
        return "\n".join(lines[start:end])

    def test_manifest_version_bumped(self):
        path = os.path.join(get_module_path("mail_management"), "__manifest__.py")
        with open(path, encoding="utf-8") as fh:
            # Comment lines (the noqa note) are not valid JSON — strip them.
            source = "\n".join(
                ln for ln in fh.read().splitlines() if not ln.lstrip().startswith("#")
            )
        manifest = ast.literal_eval(source)
        self.assertEqual(manifest["version"], "19.0.17.0.0")
