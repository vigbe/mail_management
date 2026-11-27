# Proposal — `webmail-complete-management`

Goal: close the gap between the current webmail read/compose experience and a
complete webmail manager (SnappyMail-parity feature set), driven by direct user
feedback after real usage of the 3-pane client.

Source research: SnappyMail feature set (sort by date/size/subject, message
flags/colors, identities, folder management, multi-mailbox) + standard webmail
management actions (mark read/unread, star, move/copy, delete-to-trash, bulk
selection, search).

## User requests (2026-09-09, verbatim intent)

1. Full sorting/filtering of the message list (not only "most recent").
2. Create folders from the left tree.
3. Support more mailboxes with fuller management.
4. Complete management: forward, mark, star, signatures, formatting.

## Feature matrix

| Feature | Before | This change |
|---|---|---|
| Sort (date/from/subject, unread-first/flagged-first) | date asc/desc only | PR-2a |
| Quick filters (all/unread/flagged/attachments) | none | PR-2a |
| Star/flag toggle (\Flagged IMAP mirror) | none | PR-2a |
| Mark read/unread ( Seen mirror) | implicit on read | PR-2a |
| Bulk selection + bulk mark/star | none | PR-2a |
| Folder create/rename/delete (IMAP CREATE/RENAME/DELETE) | discovery only | PR-2b |
| Move message to folder + delete-to-Trash | none | PR-2b |
| HTML signatures per mailbox (auto-insert, picker) | none | PR-2c |
| Rich-text compose (Odoo html_editor, sanitized) | plain text | PR-2c |
| Local message search + mailbox mgmt polish | none | PR-2d |
| es_CL translations for all new strings | partial | PR-2d |

Out of scope: PGP, Sieve server filters, contacts, calendar, threading-grouping.

## Delivery

Chained PRs, each strict-TDD (mocked IMAP/SMTP), each deployed to local_main_rs
after independent gatekeeper verification. Baseline: 216 tests GREEN @ 19.0.5.0.0.
