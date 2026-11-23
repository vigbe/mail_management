# Tasks — `webmail-complete-management`

Baseline: 216 tests GREEN @ 19.0.5.0.0. Harness: docker one-shot (see
openspec/config.yaml). Flag policy: update local flags first, mirror to IMAP
best-effort (log on failure) — consistent with the sync engine's resilience.

## PR-2a — Message list power (sort/filter/flags/bulk)

- [x] RED tests/test_message_flags.py: set_flags endpoint (auth, cross-user
      isolation, local flag_seen/flag_flagged update, IMAP mirror via mocked
      ImapClient, unread_count recompute, invalid flag rejected).
- [x] RED tests/test_list_sorting.py: controller sort whitelist
      (date/date desc/email_from/subject + unread_first/flagged_first
      priority), quick filters (unread/flagged/attachments), invalid keys
      rejected safely.
- [x] GREEN: mailbox._set_message_flags(ids, flags) store+mirror; controller
      /mail_management/message/set_flags + extended /messages params.
- [x] GREEN UI: sortable headers + priority toggles, quick-filter chips,
      star per row + reading pane, mark-unread action, checkboxes + bulk bar
      (mark read/unread, star/unstar).
- [x] Version 19.0.5.0.0 -> 19.0.6.0.0; suite GREEN (216+new); ruff clean.

## PR-2b — Folders & movement

- [x] ImapClient: create_folder / rename_folder / delete_folder /
      move_messages (COPY+STORE \Deleted+EXPUNGE or MOVE) seams, mocked.
- [x] Tree UI: add-folder button, per-folder menu (rename/delete), message
      "move to folder" + delete-to-Trash semantics.
- [x] Tests: folder CRUD mirror, move idempotency, trash semantics, isolation.

## PR-2c — Compose pro (signatures + rich text)

- [x] mail.management.signature model (name, html_body sanitized, per mailbox
      optional, default flag) + config views.
- [x] ComposeDialog: Odoo html_editor for body; signature auto-insert +
      picker; multipart/alternative send (plain fallback), sanitize-out.
- [x] Tests: signature CRUD/isolation, html send sanitized, plain fallback.

## PR-2d — Search & polish

- [x] Local search over synced messages (subject/from/to/body_text) via
      controller param + search box; mailbox add/refresh inside webmail;
      es_CL PO extension (odoo-python grammar!) for all new strings.

## PR-2e — UX polish (simplified buttons + image policy + settings)

User request (2026-09-09): simplify the buttons; allow images to be SEEN;
SnappyMail-style settings (signatures, block/allow images, signature position).

- [x] RED tests/test_webmail_settings.py: settings model (singleton
      auto-create, defaults, unique user, isolation), GET /settings +
      POST /settings/save (round-trip, invalid position, cross-user),
      compose signature-position prefill wiring, UI needles (gear, sort
      menu, overflow, images bar, settings dialog) + es_CL term + version
      tracker 19.0.10.0.0.
- [x] GREEN: mail.management.settings model + rules + ACL; controller
      endpoints; SettingsDialog (remote images toggle, signature position,
      Manage signatures → existing action xmlid); per-message Show-images
      bar + allow_remote_images policy in prepareBody; compose above/below
      placement; button consolidation (sort menu, reading overflow, tree
      header, icon-only bulk bar).
- [x] TRIANGULATE + REFACTOR: settings apply without reload, per-message
      reveal isolation, above-position with image signature; suite GREEN
      (502 baseline preserved) + real-Chrome tour; i18n POT/PO grammar +
      polib merge-check; version 19.0.9.0.0 -> 19.0.10.0.0.

## PR-2g — Settings overhaul part 2 (FINAL: folders mapping/visibility + compose + general/theme)

User spec (2026-09-09, SnappyMail settings screen): system-folder mapping
(which folder is Sent/Drafts/Junk/Trash), hide folders; compose: default
editor (plain/html), place replies in the folder of the message being
replied to, read/delivery confirmation requests, browser spellchecker;
general: unread badge + light/dark theme.

- [x] RED tests/test_folder_mapping.py: `visible` flag + tree-payload
      filtering vs `include_hidden` settings payload, set_visible endpoint
      + cross-user rejection, set_role validation/swap-uniqueness/inbox-
      refused/case-sensitivity/per-mailbox scoping, remapped Sent honored by
      APPEND + remapped Trash honored by delete-to-trash, Folders-tab wiring
      needles + es_CL term.
- [x] RED tests/test_compose_settings.py: 7 new settings-field defaults +
      selection values, controller payload/save round-trips + invalid
      values rejected, `reply_save_folder='same'` (INBOX APPEND + \Answered
      + local mirror; forward/plain compose stay on Sent), receipt headers
      present/absent/from-email on the MOCKED SMTP seam, plain-editor +
      spellcheck + theme/badge source wiring, es_CL term.
- [x] GREEN: folder `visible` field + `_set_system_role` (inbox fixed,
      swap-on-conflict) + `_settings_role_labels`; controller
      /folder/set_visible + /folder/set_role + `get_folders(include_hidden)`;
      7 settings fields + payload/save whitelists; `_send_message` consumes
      the OWNER's settings (receipts headers + 'same' save folder);
      SettingsDialog Folders tab + General extensions; ComposeDialog plain
      textarea mode (spellcheck attr, signature text rendition, single-part
      text send); FolderTree hide menu + badge gating; SCSS palette on CSS
      custom properties + `.o_webmail_dark` root class; i18n POT/PO +25
      entries; version 19.0.11.0.0 → 19.0.12.0.0.
- [x] TRIANGULATE: controller-level remap-then-delete routes to the NEW
      trash; swap back-and-forth; hidden folder keeps its mapping surface;
      receipts + same-folder TOGETHER; theme/badge persist for a fresh
      controller; plain-editor server path stays single-part. Suite GREEN
      745 canonical (647 baseline preserved); polib merge-check OK (131
      entries); ruff: zero new findings in the new code.

  ## PR-2h — Folder management in Settings + soft-delete with Trash retention

    User request (2026-09-09): in Folders (settings) be able to RENAME, CREATE
    and DELETE folders — delete confirms first; contents go to TRASH retained
    for 30 days (Gmail-style), then auto-purged.

  - [x] RED tests/test_folder_soft_delete.py: soft-delete semantics
          (empty → direct delete; with messages → COPY-to-trash+EXPUNGE in
          batches, local trash copies WITH trashed_date; no-trash-mapped and
          system-folder refusals), trashed_date field, retention clamps
          (0→1, 400→365),_cron_purge_trash (aged purged/newer kept/boundary
          at exactly N days/owner settings/no-row default 30/skip
          unmapped/only-active/failure isolation), trash-sync stamping
          (pre-existing + newly synced + non-trash untouched), controller
          summary + isolation + settings round-trip, UI wiring needles,
          es_CL term, version tracker; PR-2b hard-delete tests UPDATED to the
          new contract (force removed) — 819 counted in RED, 12 failures +
          35 errors ALL in the new classes, 745 baseline untouched.
  - [x] GREEN: message.trashed_date; mailbox._delete_folder soft semantics
          +_mirror_trashed_messages (stamped temp-uid copies, shared
          ir.attachment pointers) + _uid_batches (move_batch_size);
          _sync_folder trash stamping; settings.trash_retention_days
          (default 30, clamp 1..365); _cron_purge_trash +_purge_aged_trash
          (owner settings, strict <, unstamped never purged); controller
          /folder/delete summary (moved_count, folder_removed — force GONE);
          settings payload/save; Folders-tab Create/Rename/Delete actions
          (system roles disabled + tooltip), Trash retention input, tree
          kebab soft-delete confirm; purge cron data record; i18n +7/-1
          entries (dead PR-2b confirm msgid removed); 19.0.12.0.0 →
          19.0.13.0.0 (manifest + 8 trackers + openspec config).
  - [x] TRIANGULATE + REFACTOR: current-view delete (tree payload loses
          the folder; client reload needles), retention change moves the
          purge boundary, clamped-to-1 boundary, mixed-trash purge
          selectivity, resurrect-and-delete-again, 130-message batching
          (50/50/30); refactor: _get_trash_folder() dedup (3 call sites);
          suite GREEN **820 tests, 0 failures**; polib merge-check OK
          (137 entries); ruff clean; AST class-structure verified.

  ## PR-2f — Settings overhaul part 1 (message list + message view)

User spec (2026-09-09, SnappyMail settings screen): message-list settings
(messages per page, default sort, show checkboxes) + message-view settings
(mark as read after N seconds, collapse quoted text + max quote levels,
remove background/text colors, prefer HTML over plain text, simple
attachment list, show next message on move/delete, per-sender image
WHITELIST).

- [x] RED tests/test_view_settings.py: model field defaults, save clamps
      (page 5→10/999→200, levels 0→1/99→10, mark-read floor -1),
      default_sort validation against the EXISTING controller sort
      whitelist (incl. priority suffix), image whitelist parse/normalize/
      match (case/whitespace/invalid lines ignored), mark-read semantics
      constants, isolation unchanged, controller round-trips, /messages
      page-size-from-settings, UI wiring needles + es_CL term; PLUS
      tests/test_message_policy.mjs (in-container node execution of the
      pure DOM-free policy helpers: strip styles ON/OFF, prefer_html off,
      whitelist allows, quote collapse at/beyond max, next-at-list-end,
      timer 0/-1/5, combined policies).
- [x] GREEN: 11 new settings fields + helpers (_image_sender_allowed,
      _is_valid_default_sort/_parse_sort_tuple/_get_sort_tuple,
      _normalize_settings_vals on create/write); /settings/save + GET
      extended; /messages limit=None → per-user page size;
      static/src/utils/message_policy.js (pure, import-free);
      WebmailClient pipeline (choose→strip→collapse→prepareBody),
      default sort on folder open + session override, deferred mark-read
      timer with cancellation, next-on-move/delete, whitelist-aware image
      policy; MessageList checkbox/bulk-bar gating; ReadingPane compact
      attachments; SettingsDialog converted to TABBED layout; tour +8
      steps; version 19.0.10.0.0 → 19.0.11.0.0.
- [x] TRIANGULATE + REFACTOR: combined save (strip+prefer_html+whitelist),
      sort suffix edges (garbage suffix rejected, tuple keeps the good
      part), mark-read 0/5/-1 untouched, saved default sort drives the real
      listing, session-override needles, whitelist dedupe; refactor: named
      MARK_READ_MAX_SECONDS constant; suite GREEN 647 canonical + 648
      fresh-install real-Chrome tour (TOUR SUCCEEDED through the tabbed
      settings dialog and the checkbox-off list); polib merge-check OK;
      i18n +25 entries (POT mirrored, es_CL translated).
