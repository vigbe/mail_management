# Apply Progress — `webmail-complete-management`

> **PR-2h (folder management in Settings tab + soft-delete with Trash
> retention — Gmail-style) — COMPLETE.** Strict TDD mode active. All evidence
> from real docker harness runs. Baseline before this PR: **745 tests GREEN
> @ 19.0.12.0.0** (safety-net run re-verified at the start: `mail_management:
> 745 tests 26.71s`, EXIT=0). Final: **820 tests, 0 failures** @
> **19.0.13.0.0** (`odoo.tests.stats: mail_management: 820 tests 27.41s`,
> EXIT=0). The 745 baseline tests stayed green throughout EXCEPT the
> deliberately UPDATED PR-2b hard-delete tests (the task's expected contract
> change — see "Contract change" below; every intermediate failure was
> confined to the new PR-2h classes plus those reworked tests).

---

## TDD Cycle Evidence (PR-2h)

| Task | Phase | Test file | Result | Docker evidence |
|------|-------|-----------|--------|-----------------|
| Safety net (before any edit) | BASELINE | whole suite | 745 tests, 0 failures | `odoo.tests.stats: mail_management: 745 tests 26.71s`, EXIT=0 |
| RED batch (56 new test methods across 9 classes: soft-delete model semantics, trashed_date field, trash-sync stamping, retention clamps, purge cron + boundary, controller summary/isolation/settings round-trip, triangulates, UI wiring needles, es_CL term, version tracker) | RED | `tests/test_folder_soft_delete.py` (+ PR-2b test updates) | 12 failures + 35 errors — ALL in the 10 new PR-2h classes; 745 baseline untouched | `819 tests 27.88s`, `Module mail_management: 12 failures, 35 errors`, EXIT=1; class-level listing shows exclusively TestFolderSoftDeleteModel/TestCronPurgeTrash/TestFolderSoftDeleteController/…/TestSoftDeleteUiWiring |
| GREEN pass 1 (trashed_date field; `_delete_folder` soft rewrite + `_mirror_trashed_messages` + `_uid_batches`; `_sync_folder` stamping; `trash_retention_days` + clamps; `_cron_purge_trash` + `_purge_aged_trash`; controller summary + settings round-trip; Folders-tab actions + retention input + tree confirm; cron data; i18n +7/-1; version bump) | GREEN | same | 1 failure — TEST-SIDE off-by-one: first temp uid is exactly `1_000_000` (the established watermark convention), test asserted `> 1_000_000` | `818 tests 27.77s`, `1 failures, 0 errors`, EXIT=1 |
| GREEN pass 2 (expectation fixed to `>= 1_000_000`) | GREEN | same | 0 failures | `odoo.tests.stats: mail_management: 818 tests 27.62s`, EXIT=0 |
| TRIANGULATE (+2 probes: mixed-trash purge selectivity — ONE grouped IMAP delete for the aged uids only; clamped-to-1-day boundary — 2-day-old purged, 12-hour-old kept) | TRIANGULATE | same | 0 failures — all triangulates passed against the implementation | (same run as REFACTOR below) |
| REFACTOR (extracted `_get_trash_folder()` — the `role='trash'` lookup was duplicated at 3 call sites; AST re-verified after every edit) | REFACTOR | whole suite | 0 failures | `odoo.tests.stats: mail_management: 820 tests 27.41s`, EXIT=0 |
| polib merge-check (BEFORE the docker runs, in-container) | GATE | `python3` in the odoo:19.0 image | msgid sets identical POT↔PO; every entry keeps module+flavor markers after `po.merge(pot)` | `MERGE-CHECK: OK | entries: 137`, POLIB_EXIT=0 |

### Test Summary (PR-2h)

- **Total tests written (this PR)**: 58 new test methods in
  `tests/test_folder_soft_delete.py` (9 classes: 14 soft-delete model + 1
  field + 4 stamping + 5 retention + 11 purge cron + 7 controller + 6
  triangulate + 1 es_CL + 8 wiring); PLUS 3
  PR-2b tests reworked for the new contract (net -1 after merge); 8 legacy
  version trackers bumped to 19.0.13.0.0. Docker counts 820 total.
- **Total tests passing**: 820/820 canonical (es_CL active in the DB).
- **Counting note**: the canonical metric is the `odoo.tests.stats` line
  (the same counter every previous PR quoted). The log's `Starting Test`
  line delta (+57: 579 → 636) exactly matches the authored change (58 new
  methods, net −1 from the PR-2b rework); the pre-existing gap between
  the two counters (~166 lines at baseline) is a harness quirk untouched
  by this PR. The HttpCase tour skips in `-u` mode (`re-run with --workers
  0`) exactly as in the 745 baseline.
- **Layers used**: Integration (TransactionCase) 75, source-wiring
  (headless needles) inside the same files, no browser layer this PR.
- **UI verification honesty**: the Folders-tab Create/Rename/Delete actions,
  the disabled Delete on system-mapped roles (tooltip), the Trash retention
  input riding Save, and the tree kebab's new confirm text are verified via
  HEADLESS source needles only. The real-Chrome tour was NOT extended
  (deemed not cheap: the delete confirm needs a mailbox with a mapped Trash
  - message fixtures in the tour DB; the existing tour steps are unaffected
  by the new controls). No screenshot/manual verification was performed.

## What was built (PR-2h)

### A. Soft-delete folder semantics (replaces the PR-2b hard delete)

- `mail.management.message.trashed_date` (Datetime, indexed) — the LOCAL
  retention anchor; help text documents the approximation.
- `mailbox._delete_folder(folder)` (signature: **`force` removed**):
  - mapped system roles `inbox/sent/drafts/trash/junk` → `UserError`
    ("…is a mapped system folder. Remove its role in Settings ▸ Folders
    before deleting it."); `archive`/`all`/`other` stay deletable;
  - NO messages → IMAP DELETE + local unlink (PR-2b direct path kept);
  - WITH messages → ONE IMAP session: `move_messages` batches to the
    mapped Trash (COPY + `\Deleted` + EXPUNGE on the source; batch size
    `mail_management.move_batch_size`, default 100) + `delete_folder` on
    the now-empty source; then locally `_mirror_trashed_messages`
    (copies with `trashed_date=now`, temp uids above the Trash watermark
    — the same convention as the Sent mirror; attachment POINTER rows
    duplicated sharing the `ir.attachment`, no filestore copy) and the
    source folder unlink (cascades the local source records);
  - NO mapped Trash → `UserError` "No Trash folder is mapped for this
    mailbox. Map one in Settings ▸ Folders first." (ties into PR-2g).
- Trash-sync stamping: `_sync_folder` on a `role='trash'` folder stamps
  every un-stamped message with the sync time (covers other-client
  deletions; pre-existing content stamped on first observed sync —
  documented approximation).

### B. Retention + purge

- `mail.management.settings.trash_retention_days` (Integer, default 30,
  CLAMP 1..365 in `_normalize_settings_vals`; constants
  `TRASH_RETENTION_{DEFAULT,MIN,MAX}_DAYS`).
- `mailbox._cron_purge_trash()` — daily ir.cron (`cron_purge_trash` in
  `data/mail_management_cron.xml`, interval 1 day): bounded mailbox batch
  (cron_batch_size), per-mailbox try/except + savepoint + logging.
- `mailbox._purge_aged_trash()` — reads the OWNER's settings (sudo +
  explicit user_id filter, default 30 without a row), purges
  `trashed_date < now - retention` STRICTLY (exactly-N kept), never
  touches un-stamped messages (no known age), through `_purge_messages`
  (IMAP-first `\Deleted` + EXPUNGE + local unlink).

### C. Controller + UI

- `POST /folder/delete` → `{'success', 'moved_count', 'folder_removed'}`;
  confirmation is client-side.
- Settings payload + `/settings/save` accept `trash_retention_days`
  (clamped by the model).
- Settings ▸ Folders tab: per-mailbox **Create folder** button
  (`o_webmail_settings_create_folder`), per-folder **Rename**/**Delete**
  icon actions (`o_webmail_settings_folder_{rename,delete}`) — Delete
  DISABLED with tooltip on system-mapped roles (`isSystemFolder`), and a
  **Trash retention (days)** number input (`o_webmail_settings_trash_retention`)
  riding the normal Save. Each action resolves the FRESH all-folders
  payload that re-renders the tab (same contract as the PR-2g role
  dropdowns; the tree reloads too).
- Tree kebab: Rename/Delete kept; the confirm body now reads "Delete the
  folder '%s' with %s message(s)? Messages will be moved to Trash and
  automatically deleted after %s days." (retention read from the SAVED
  setting); success notification adds "…%s message(s) moved to Trash.".

### i18n + versions

- +7 entries (2 odoo-python: the two new UserErrors; 5 odoo-javascript:
  confirm body, moved-notification, retention label + help, system-folder
  tooltip) and ONE dead msgid REMOVED (the PR-2b "still contains… Delete it
  anyway?… removed from Odoo." confirm) — 137 real entries, POT/PO
  identical, polib merge-check OK.
- `19.0.12.0.0` → `19.0.13.0.0` (`__manifest__.py` + the 8 legacy version
  tracker tests + the new PR-2h tracker + `openspec/config.yaml`
  project.version).

## Contract change (PR-2b → PR-2h) — documented per the task

- `mailbox._delete_folder(folder, force=False)` → `_delete_folder(folder)`;
  controller `folder_delete(force)` → `folder_delete(folder_id)` returning
  the summary. Previously "has messages" + no force → UserError; now it is
  the SOFT path (messages relocated to Trash). Three PR-2b tests were
  reworked accordingly (`test_delete_folder_imap_then_local_cascade` →
  empty-path + soft-path pair, `test_delete_folder_force_with_messages`
  subsumed, `test_folder_delete_force_flag_forwarded` →
  `test_folder_delete_soft_summary`); `test_delete_folder_refuses_with_messages`
  now documents the no-Trash-mapped refusal (same assertion shape).

## Real bugs / findings (PR-2h)

1. **The formatter/indent hazard struck AGAIN (7th occurrence)**: the
   `_delete_folder`/`_mirror_trashed_messages` insert landed nested inside
   `_rename_folder` (8-space indent) after an auto-format pass, AND one
   import edit was silently re-flowed into a duplicate. Repaired with
   line-based surgery + dedent; AST class-method listing re-verified after
   every subsequent batch (the standing check).
2. **Temp-uid convention**: the first trash copy uid is EXACTLY
   `watermark + 1_000_000` (not strictly greater) — same as the Sent
   mirror; caught as a test expectation off-by-one in GREEN pass 1.
3. Host-Pyright flags `from odoo import …` in every touched file (no odoo
   package on the host) — pre-adjudicated noise; the docker runs prove the
   imports resolve in-container (820 tests green).

## Files changed (PR-2h)

- `models/mail_management.py` (`_protected_folder_roles`, `_get_trash_folder`,
  `_uid_batches`, `_delete_folder` rewrite, `_mirror_trashed_messages`,
  `_sync_folder` stamping, `_cron_purge_trash`, `_purge_aged_trash`, imports)
- `models/mail_management_message.py` (+`trashed_date`)
- `models/mail_management_settings.py` (+`trash_retention_days`, 3 constants,
  clamp)
- `controllers/main.py` (`folder_delete` soft summary; settings payload +
  save whitelist; import — AST-verified class-level)
- `data/mail_management_cron.xml` (+`cron_purge_trash` daily record)
- `static/src/webmail_client.js` (trashRetentionDays end-to-end, soft confirm
  - moved-count notification, `_confirmDeleteFolder`, settings-tab
  create/rename/delete actions, dialog props)
- `static/src/components/settings_dialog.js` (+`SYSTEM_FOLDER_ROLES`,
  `isSystemFolder`, actions, retention draft + save, getters)
- `static/src/xml/settings_dialog.xml` (Folders tab: retention input,
  per-mailbox Create, per-folder Rename/Delete with disabled system roles)
- `tests/test_folder_soft_delete.py` (NEW, 58 methods), `tests/__init__.py`
- `tests/test_folder_management.py` (PR-2b contract update + tracker)
- 8 legacy version trackers, `__manifest__.py`, `openspec/config.yaml`
- `i18n/mail_management.pot`, `i18n/es_CL.po` (+7/-1 each)
- `openspec/changes/webmail-complete-management/{tasks.md,apply-progress.md}`

## Remaining tasks (unchecked `- [ ]` in tasks.md)

- **None** — PR-2a…PR-2h are all checked; no parent-owned
  (`<!-- sdd-owner: parent -->`) rows exist in this tasks.md. Bounded review
  of PR-2h is a parent-lifecycle action.

## PR boundary (PR-2h — FOLDER ARC COMPLETE)

PR-2h complete: tasks.md PR-2h `[x]`, canonical harness **820 GREEN** with
es_CL active (745 baseline preserved except the documented PR-2b contract
update; +58 new test methods), polib merge-check OK (137 entries), ruff
clean on all changed python files, JS/XML well-formed, no `t-raw=`, AST
class-structure verified, nothing staged, nothing committed (user owns
commits). **The folder arc (2b CRUD/movement → 2g mapping/visibility → 2h
Settings actions + retention) is COMPLETE.** Remaining in the parent plan:
PR-1d/1e OAuth (Gmail/MS365) + AI slice 2 + archive feature.

---

> **PR-2g (settings overhaul part 2 — FINAL: folders mapping/visibility +
> compose + general/theme — SnappyMail parity) — COMPLETE, settings arc
> COMPLETE.** Strict TDD mode active. All evidence from real docker harness
> runs. Baseline before this PR: **647 tests GREEN @ 19.0.11.0.0** (safety-
> net run re-verified at the start: `mail_management: 647 tests 19.68s`,
> EXIT=0). Final: **745 tests, 0 failures** @ **19.0.12.0.0** (`mail_management:
> 745 tests 22.51s`, EXIT=0). The 647 baseline tests all stayed green
> throughout (every failure at every intermediate stage was confined to the
> NEW PR-2g test classes, verified by class-level failure listing).

---

## TDD Cycle Evidence (PR-2g)

| Task | Phase | Test file | Result | Docker evidence |
|------|-------|-----------|--------|-----------------|
| Safety net (before any edit) | BASELINE | whole suite | 647 tests, 0 failures | `odoo.tests.stats: mail_management: 647 tests 19.68s`, EXIT=0 |
| RED batch (62 new test methods: visibility + payload filtering, set_role validation/swap/inbox-fixed/isolation, remapped Sent APPEND + remapped Trash delete, 7 settings-field defaults + controller whitelists, reply_save_folder='same', receipt headers on the mocked SMTP seam, plain-editor/spellcheck/theme/badge wiring needles, 2 es_CL terms, version trackers) | RED | `tests/test_folder_mapping.py` + `tests/test_compose_settings.py` | 22 failures + 38 errors — ALL in the 13 new PR-2g classes; 647 baseline untouched; 3 control-case tests (unchanged behavior) pass by design | `735 tests 22.19s`, `Module mail_management: 22 failures, 38 errors`, EXIT=1; class-level listing shows exclusively TestFolderVisible/TestSetFolderRole/TestRemappedRolesHonored/…/TestVersionTracker |
| GREEN pass 1 (folder `visible` + `_set_system_role` + `_settings_role_labels`; controller set_visible/set_role/include_hidden; 7 settings fields + save whitelists; `_send_message` receipts + 'same' save folder; Folders tab + General extensions; plain textarea mode; hide menu + badge gating; SCSS custom properties + dark) | GREEN | same | 11 failures + 16 errors — root causes: (a) my folder-model edit landed methods NESTED inside `_compute_counts` after the external auto-formatter had normalized the file to 4-space (the STANDING indent hazard — fixed by an atomic full-file rewrite), which also broke `display_name` (8 legacy TestFolderLabels failures); (b) my rewrite accidentally dropped the `last_sync` field (view ParseError); (c) 3 test-side needles not matching the prettier-formatted reality | `735 tests 22.22s`, `11 failures, 16 errors`, EXIT=1; then `Field "last_sync" does not exist` ParseError, EXIT=255 |
| GREEN pass 2 (folder model rewritten atomically with `last_sync` restored; badge binding via `props.showUnreadBadge`; theme/plain-mode needles aligned) | GREEN | same | 0 failures | `odoo.tests.stats: mail_management: 735 tests 22.33s`, EXIT=0 |
| TRIANGULATE (+6 test methods: controller-level remap-then-delete routes to the NEW trash, swap back-and-forth, hidden folder keeps its mapping surface, receipts+same-folder TOGETHER, theme/badge persist for a fresh controller, plain-editor server path stays single-part) | TRIANGULATE | same | 0 failures — all new triangulate cases passed against the implementation on first run | `odoo.tests.stats: mail_management: 745 tests 21.85s`, EXIT=0 |
| REFACTOR (dropped the `sent_folder = save_folder` alias in `_send_message` — the save target is `save_folder` everywhere now; AST re-verified: 36 methods in class, no strays) | REFACTOR | whole suite | 0 failures | `odoo.tests.stats: mail_management: 745 tests 22.51s`, EXIT=0 |
| polib merge-check (BEFORE the docker runs, in-container) | GATE | `python3` in the odoo:19.0 image | msgid sets identical POT↔PO; every entry keeps module+flavor markers after `po.merge(pot)` | `MERGE-CHECK: OK | entries: 131`, POLIB_EXIT=0 |
| Host-tooling proof for the flagged `from odoo import …` imports | GATE | `python3` in the odoo:19.0 image | the imports resolve in the actual runtime (host Pyright lacks the Odoo package — noise) | `IMPORTS RESOLVE IN CONTAINER: odoo + odoo.exceptions OK`, EXIT=0 |

### Test Summary (PR-2g)

- **Total tests written (this PR)**: 68 new test methods — 36 python in
  `test_folder_mapping.py` (6 visibility + 14 set_role + 2 remap-honored +
  6 wiring + 1 es_CL + 3 triangulate + 1 version + header/mixin) and 32 in
  `test_compose_settings.py` (4 model + 7 controller + 6 same-folder + 4
  receipts + 6 plain-editor wiring + 4 general wiring + 1 es_CL + 3
  triangulate + version tracker shared); docker counts 745 total (647
  baseline preserved).
- **Total tests passing**: 745/745 canonical (es_CL active in the DB).
- **Layers used**: Integration (TransactionCase) 67, source-wiring
  (headless needles) inside the same files, no browser layer this PR.
- **UI verification honesty**: Folders tab, role dropdowns, hide toggle,
  theme class and badge gating are verified via HEADLESS source needles
  only (attribute-based selectors — the same pattern the PR-2f real-Chrome
  tour asserts). The real-Chrome tour was NOT extended (deemed not cheap:
  mapping actions need per-mailbox fixtures); the existing tour steps are
  attribute-based (`data-tab=…`) and unaffected by the new tab. No
  screenshot/manual verification was performed.

## What was built (PR-2g)

### A. Folder visibility

- `mail.management.folder.visible` (Boolean, default True) — hidden folders
  keep syncing, only the TREE payload omits them.
- `/mail_management/folders` gains `include_hidden=False`; the settings
  payload (`include_hidden=True`) returns EVERY folder with its `visible`
  flag + a server-translated `role_labels` map (one source of truth for
  role names — no duplicate JS msgids).
- New `POST /mail_management/folder/set_visible` (auth user, ir-rule
  isolated; forged cross-user id → `Folder not found.` UserError).
- FolderTree per-folder menu gains **Hide folder** (`o_webmail_folder_menu_hide`)
  → `WebmailClient.hideFolder` → set_visible(false); Settings ▸ Folders lists
  hidden folders (muted + **Hidden** badge) with a restore switch.

### B. System-folder mapping (user OVERRIDES the guesses)

- `folder._set_system_role(role)`: '' clears to 'other'; 'inbox' is FIXED
  both ways (assigning it OR changing/clearing an inbox folder → UserError);
  unknown keys → `Invalid folder role.`; per-mailbox uniqueness via SWAP —
  the previous holder is demoted to 'other' (one dropdown action,
  SnappyMail-style); 'other' stays free-form.
- New `POST /mail_management/folder/set_role` returns the demoted folder —
  the client notifies "'%s' was demoted to Other (one folder per role).".
- PROVEN honored: remapped Sent receives the APPEND + local mirror; remapped
  Trash receives the delete-to-trash move (model AND controller level).
- Settings ▸ Folders tab: per-mailbox sections, one role dropdown per folder
  (labels from the server payload), inbox rows show a fixed label; actions
  apply IMMEDIATELY (each callback resolves the fresh all-folders payload
  that replaces the dialog's local copy).

### C. Compose settings

- `compose_editor` (html default | plain): plain renders a
  `o_webmail_compose_plain` `<textarea>` (spellcheck attr) instead of the
  Wysiwyg; the initial body is TEXT (quote via `_quoteText` + signature via
  `htmlSignatureToText` — tags stripped, entities decoded); switching
  signatures swaps the `"-- "` block (`replacePlainSignature`, pure +
  exported); send passes `body_text` only with `body_html: ""` — the
  server keeps the single-part text path.
- `reply_save_folder` (sent default | same): 'same' APPENDs the reply to the
  ORIGINAL message's folder (+ `\Answered` + the local mirror there, temp
  uid above that folder's watermark); forwards and fresh composes ALWAYS
  use Sent; `_send_message` reads the OWNER's settings row via sudo() with
  an explicit user_id filter.
- `request_read_receipt` → `Disposition-Notification-To: <from_email>`;
  `request_delivery_receipt` → `Return-Receipt-To: <from_email>` (asserted
  on the mocked SMTP seam; absent by default; value follows a from_email
  change).
- `enable_spellcheck` (default True) → the `spellcheck` attribute on BOTH
  editor surfaces (textarea directly; the Wysiwyg wrapper div inherits).

### D. General

- `show_unread_badge` (default True): FolderTree badge renders only when
  `props.showUnreadBadge !== false && folder.unread_count`.
- `theme` (light default | dark): the webmail root template binds
  `state.settings.theme === 'dark' ? 'o_webmail_dark' : ''`; webmail.scss
  defines the palette on `--wmail-*` custom properties (10 vars) and
  `.o_webmail_dark` overrides ONLY the variables (+ scoped `--bs-border-color`,
  `.text-muted`, `.form-control/.form-select`, `.text-bg-light`); the
  `bg-white` utilities were dropped from the three pane templates so the
  variables own the backgrounds.
- 7 new settings fields + GET payload keys + save whitelists
  (`Invalid compose editor.` / `Invalid reply save folder.` /
  `Invalid theme.` UserErrors otherwise).

### i18n + versions

- +25 POT/PO entries (7 odoo-python: folder-mapping + controller errors;
  18 odoo-javascript: Folders tab labels, editor/theme options, receipts,
  spellcheck, Hidden, Hide folder, demoted-notification). Precedent kept:
  strings shared between Python field labels and JS `_t()` stay JS-flavored
  (no backend view renders the settings model).
- `19.0.11.0.0` → `19.0.12.0.0` (`__manifest__.py` + the 8 legacy version
  tracker tests + `openspec/config.yaml` project.version — was stale at
  19.0.1.0.0).

## Files changed (PR-2g)

- `models/mail_management_folder.py` (atomic rewrite: `visible`,
  `_set_system_role`, `_settings_role_labels`, MAPPABLE_ROLES; `last_sync`
  preserved)
- `models/mail_management_settings.py` (+7 fields, COMPOSE_EDITORS /
  REPLY_SAVE_FOLDERS / WEBMAIL_THEMES constants)
- `models/mail_management.py` (`_send_message`: owner-settings receipts +
  'same' save folder; `save_folder` refactor)
- `controllers/main.py` (`get_folders(include_hidden)` + visible/role_labels
  keys; `/folder/set_visible`; `/folder/set_role`; settings payload + save
  whitelists)
- `static/src/webmail_client.js` (+7 settings keys end-to-end, folders-tab
  payload + setFolderRole/toggleFolderVisible/hideFolder, compose props)
- `static/src/xml/webmail_client.xml` (theme root class + FolderTree props)
- `static/src/components/folder_tree.js` / `folder_tree.xml` (hide menu,
  badge gating, bg-white dropped)
- `static/src/components/compose_dialog.js` / `compose_dialog.xml` (plain
  textarea mode, spellcheck, signature text rendition, text-only send)
- `static/src/components/settings_dialog.js` / `settings_dialog.xml`
  (Folders tab + General extensions; full-file rewrite)
- `static/src/scss/webmail.scss` (palette on custom properties + dark)
- `static/src/xml/message_list.xml`, `reading_pane.xml` (bg-white dropped)
- `tests/test_folder_mapping.py`, `tests/test_compose_settings.py` (new),
  `tests/__init__.py`, 8 legacy version trackers bumped
- `i18n/mail_management.pot`, `i18n/es_CL.po` (+25 each)
- `__manifest__.py` (version), `openspec/config.yaml` (project.version)

## Deviations / interpretation notes (PR-2g)

1. **Swap, not reject**: the task allowed "swap or reject with a clear
   UserError" for per-mailbox role uniqueness. SWAP was chosen (the previous
   holder is demoted to 'other') — one dropdown action like SnappyMail's
   per-role mapping; the endpoint RETURNS the demoted folder and the client
   notifies the user, so nothing is silent.
2. **`\Answered` IMAP STORE seam unchanged**: `ImapClient.store(uids, flags,
   value)` operates on the selected folder (pre-existing behavior, mocked in
   all tests). The 'same' mode sets the LOCAL `flag_answered` on the original
   and keeps the existing STORE call — extending the seam signature would
   break the established FakeImapClient contract; flagged as a possible
   follow-up refinement, not a regression.
3. **Role dropdown labels come from the SERVER** (`role_labels` in the
   include_hidden payload) to avoid duplicate msgids between odoo-python
   and odoo-javascript flavors (the established single-entry POT precedent).
4. **Plain-mode signature switching** replaces the `"-- "` marker block
   (below: marker→end; above: start→marker). The initial layout is built by
   the same pure helpers, so the round-trip is exact in the normal flow.
5. **No tour extension** (stated honestly above); mapping UI verified via
   headless needles.
6. The **formatter/indent hazard** struck again (GREEN pass 1): an external
   auto-format normalized files mid-flight and one edit nested methods
   inside `_compute_counts`. All `models/*.py` + `controllers/main.py` were
   re-verified by AST after every subsequent edit (the standing check).

## Remaining tasks (unchecked `- [ ]` in tasks.md)

- **None** — every task in tasks.md is `[x]`; no parent-owned
  (`<!-- sdd-owner: parent -->`) rows exist in this tasks.md. Bounded review
  of PR-2g is a parent-lifecycle action.

## PR boundary (PR-2g — CHANGE settings arc COMPLETE)

PR-2g complete: tasks.md PR-2g `[x]`, canonical harness **745 GREEN** with
es_CL active (647 baseline preserved + new mapping/compose/general tests),
polib merge-check OK (131 entries), ruff: zero new findings in new code,
JS/XML well-formed, AST class-structure verified, no `t-raw=`, nothing
staged, nothing committed (user owns commits). **The SnappyMail settings
arc (PR-2e → PR-2f → PR-2g) is COMPLETE.** Remaining in the parent plan:
PR-1d/1e OAuth (Gmail/MS365) + AI slice 2 + archive feature.

---

> **PR-2f (settings overhaul part 1: message list + message view —
> SnappyMail parity) — COMPLETE.** Strict TDD mode active. All evidence from
> real docker harness runs. Baseline before this PR: **579 tests GREEN @
> 19.0.10.0.0** (safety-net run re-verified at the start: `579 tests
> 19.63s`, exit 0). Final: **647 canonical (es_CL active in the DB) + 648
> fresh-install real-Chrome tour (TOUR SUCCEEDED through the new TABBED
> settings dialog — Message list tab, show-checkboxes toggle off, save,
> list re-rendered WITHOUT the select-all checkbox), 0 failures** @
> **19.0.11.0.0**. All 579 baseline tests still green (the 68 new test
> methods are pure additions; the ONE updated legacy needle is the PR-2e
> `_buildBodyMarkup` 500→800-char source window — the helper legitimately
> grew with the strip/collapse pipeline stages).

---

## TDD Cycle Evidence (PR-2f)

| Task | Phase | Test file | Result | Docker evidence |
|------|-------|-----------|--------|-----------------|
| Safety net (before any edit) | BASELINE | whole suite | 579 tests, 0 failures | `odoo.tests.stats: mail_management: 579 tests 19.63s`, EXIT=0 |
| Settings + policy RED (60 test methods: model defaults/clamps/validation, controller round-trips, /messages page-size, wiring needles, es_CL term, version tracker) + node harness RED | RED | `tests/test_view_settings.py` + `tests/test_message_policy.mjs` | 50 test failures/errors, 579 baseline still green | `639 tests 21.76s`, `Module mail_management: 18 failures, 32 errors`, EXIT=1; node: `ENOENT … message_policy.js`, NODE_EXIT=1 |
| GREEN pass 1 (model fields + helpers + controller + /messages limit + message_policy.js + client pipeline + tabs + i18n + manifest) | GREEN | same | 2 failures 5 errors — ALL test-side: seed-fixture date `2024-010-01` for uid≥10, literal `data-tab` needle (tabs render via `t-att-data-tab`), PR-2e 500-char window too narrow | `639 tests`, `2 failures, 5 errors`, EXIT=1 |
| GREEN pass 2 (fixture + needle fixes) | GREEN | same | 1 error: Odoo 19 recordsets do NOT expose plain class constants (`MARK_READ_INSTANT`) → test imports them from the module | `639 tests`, `0 failures, 1 errors`, EXIT=1 |
| GREEN pass 3 (constants import + field-default resolution) | GREEN | same | 0 failures | `odoo.tests.stats: mail_management: 639 tests 19.32s`, EXIT=0 |
| Node pure-helper GREEN (20 executable checks) | GREEN | `tests/test_message_policy.mjs` | 20/20 passed (after 2 harness-expectation fixes: the `<br/>` lands BEFORE the last word; strip removes the plain-wrapper class too — documented interaction) | in-container `node …test_message_policy.mjs`: `20/20 passed`, NODE_EXIT=0 |
| TRIANGULATE (+8 python: combined save, suffix edges, mark-read 0/5/-1 untouched, saved default sort drives the real listing, session-override needles, whitelist dedupe; +2 node combined-policy checks were already in the 20) | TRIANGULATE | same | 2 failures — test-side (display-name form is NOT a bare-email line → correctly dropped by the spec'd normalization; unread flag inverted in fixture) → fixed | `647 tests`, `2 failures, 0 errors`, EXIT=1 → `647 tests 19.95s`, EXIT=0 |
| REFACTOR (named `MARK_READ_MAX_SECONDS` constant replacing the magic `10**6` clamp; the JS refactor happened as DESIGN — the pure module IS the refactor) | REFACTOR | whole suite | 0 failures | `odoo.tests.stats: mail_management: 647 tests 19.67s`, EXIT=0 |
| polib merge-check (BEFORE the docker run, in-container) | GATE | `python3` in the odoo:19.0 image | msgid sets identical POT↔PO; every entry keeps module+flavor markers after `po.merge(pot)` | `MERGE-CHECK: OK | entries: 106` |
| Fresh-install + real headless-Chrome tour (DB dropped/recreated) | UI/E2E | `tests/test_webmail_tour.py` (HttpCase) | +8 PR-2f steps: settings dialog → **Message list tab** (`o_webmail_settings_tab[data-tab="message_list"]`) → controls render → **show-checkboxes toggle OFF** → Save → “Settings saved.” → **list re-renders WITHOUT `.o_webmail_select_all`** | `-i … --workers 0 --db-filter='^local_test$'`: `648 tests 23.61s`, `║ TOUR mail_management_webmail_tour SUCCEEDED ║`, `0 failed, 0 error(s)` of 512 |
| Final canonical `-u` run (es_CL re-activated with `--load-language` after the tour DB drop) | FINAL | whole suite | 0 failures | `odoo.tests.stats: mail_management: 647 tests 19.67s`, EXIT=0 |

### Test Summary (PR-2f)

- **Total tests written (this PR)**: 68 new test methods — 60 python in
  `test_view_settings.py` (18 model + 14 controller + 20 wiring + 7
  triangulate + 1 es_CL) + 20 real node checks in
  `tests/test_message_policy.mjs` (the pure helpers executed, not
  string-matched); + 8 tour steps; 7 legacy version trackers bumped.
- **Total tests passing**: 647/647 canonical (648 with the browser tour)
- **Layers used**: Unit/Integration (TransactionCase) 67, pure-JS node 1,
  E2E (headless Chrome tour) 1
- **Approval tests**: none
- **Pure functions created**: `stripMessageStyles`, `collapseQuotes`,
  `plainTextToHtml`, `chooseBodyHtml`, `extractEmailAddress`,
  `senderInWhitelist`, `nextMessageId`, `markReadDelayMs`
  (static/src/utils/message_policy.js — import-free, DOM-free, node-run);
  model-side `_normalize_settings_vals`, `_parse_sort_tuple`,
  `_is_valid_default_sort`, `_normalize_image_whitelist`,
  `_extract_email_address`, `_clamp_int`

## What was built (PR-2f)

### A. Settings model (11 new fields, singleton + isolation untouched)

- `messages_per_page` (Integer, default 50, CLAMP 10..200), `default_sort`
  (Char default 'date_desc', validated against the EXISTING controller
  sort whitelist keys + ONE optional priority suffix
  `+unread_first|+flagged_first`; invalid → ignored on model write,
  UserError from the endpoint), `show_checkboxes` (Bool True),
  `mark_read_after_seconds` (Integer 0; constants MARK_READ_INSTANT=0 /
  MARK_READ_NEVER=-1; below -1 clamps up), `collapse_quoted_text` (Bool
  True), `max_quote_levels` (Integer 3, CLAMP 1..10),
  `strip_message_styles` (Bool False), `prefer_html` (Bool True),
  `simple_attachment_list` (Bool False), `show_next_on_move_delete` (Bool
  True), `image_whitelist` (Text — one email per line, normalized on
  save: lowercase, trimmed, blank/invalid lines dropped, duplicates
  collapsed).
- Helpers: `_image_sender_allowed(email)` (exact, case-insensitive;
  understands `Name <addr>` display forms via `_extract_email_address`),
  `_get_sort_tuple()`/`_parse_sort_tuple()` → validated `(sort,
  [priority])`, `_default_sort_keys()` (controller whitelists are the
  single source of truth, lazily imported), `_normalize_settings_vals`
  applied in BOTH `create` and `write` (Odoo 19 `@api.model_create_multi`).
- Help texts document the mark-read 0/-1 semantics (task requirement).

### B. Controller

- `/settings/save` accepts every new field (same validation/clamps via the
  model; invalid `default_sort` → `UserError("Invalid default sort.")`);
  GET/POST responses share the single `_settings_payload(settings)` helper
  (`image_whitelist` parsed to a list).
- `/messages` `limit=None` default → the CALLER's `messages_per_page`
  (search-only in the hot path, no auto-create; 50 without a row; explicit
  `limit` wins). The client no longer sends a hardcoded `limit: 50`.

### C. Client (every setting REAL)

- **`static/src/utils/message_policy.js` (NEW)**: 8 pure, import-free,
  DOM-free helpers — genuinely unit-testable without a webclient (executed
  by in-container node). `collapseQuotes` wraps top-level blockquotes in
  native `<details class="o_webmail_quote">` toggles (zero-JS collapse)
  and everything beyond `maxLevels` behind ONE
  `<details class="o_webmail_quote_more">`; summary labels arrive
  `_t()`-translated as parameters (they are injected into markup).
- **WebmailClient pipeline**: `chooseBodyHtml` (prefer_html; plain body
  escaped + line-broken) → `stripMessageStyles` (style/class/bgcolor/color
  attributes) → `collapseQuotes` → `prepareBody` (existing cid + remote
  policy). The chosen source is kept (`rawBodyHtml` + `body_text`) so a
  settings save re-chooses and re-renders in place (`_reprepareOpenBody`).
- **Mark-read**: `_scheduleMarkRead`/`_cancelMarkReadTimer` — 0 = instant
  (today's behavior), N = deferred `setTimeout` cancelled on message
  change/folder change/move/delete/mark-unread/unmount (`onWillUnmount`),
  -1 = no seen call at all.
- **Next-on-move/delete**: `_captureNextAfterRemoval` (pure `nextMessageId`
  — next, previous-at-end, false) → `_selectAfterRemoval` opens it after
  the reload; only when the OPEN message was among the removed ids.
- **Image whitelist**: `_remoteImagesAllowedFor` = setting OR per-message
  reveal OR `senderInWhitelist(open.email_from, imageWhitelist)` —
  whitelisted senders' remote images render even when globally blocked,
  and the "Images blocked" bar hides for them.
- **Default sort**: `_applyDefaultSort` on folder open (+ after a settings
  save) until the user picks a sort for the session (`sortTouched` set by
  both `setSort` and `togglePriority`).
- **MessageList**: `showCheckboxes` prop gates the select-all checkbox,
  per-row checkboxes AND the bulk bar.
- **ReadingPane**: `simpleAttachmentList` prop → compact
  `o_webmail_attachments_simple` list instead of cards.
- **SettingsDialog**: TABBED (nav-tabs: General / Message list / Message
  view / Accounts & signatures) — every new control wired save+apply
  live; tab ids render dynamically from the `tabs` getter
  (`t-att-data-tab`), panes carry literal `data-pane` anchors.

### i18n (PR-2f)

- **+25 entries** in `i18n/es_CL.po` (mirrored in POT): 1 odoo-python
  (`"Invalid default sort."` → `"Orden de clasificación predeterminada no
  válida."`) + 24 odoo-javascript (tab labels, every control label + help
  text, quote-toggle labels). Reused msgids (sort options, "Unread
  first", "Starred first", existing PR-2e strings) are NOT duplicated.
  106 real entries total; polib merge-check OK in-container.

### Versioning

- `19.0.10.0.0` → **`19.0.11.0.0`** (manifest + all SEVEN version
  trackers: test_webmail_ui, test_compose_send, test_folder_management,
  test_message_flags, test_signatures, test_message_search,
  test_webmail_settings + the new one in test_view_settings).

## Real bugs / findings (PR-2f)

1. **Auto-formatter daemon mangled the controller AGAIN (6th occurrence)**
   — this time it actively re-flowed the file BETWEEN tool calls
   (decorator re-indent, body split at two levels); one intermediate
   repair attempt of mine also wrote literal `\\n` sequences (my bug,
   caught immediately by AST/py_compile). Resolution: one ATOMIC
   full-file `write` of controllers/main.py from the known-good content,
   AST class-method listing re-verified after every batch (the standing
   check) — final state: all 16 methods class-level, order preserved.
2. **Odoo 19 recordsets do NOT expose plain class constants** —
   `settings.MARK_READ_INSTANT` → AttributeError (the orm `__getattr__`
   intercepts unknown names). Tests import the constants from the module
   instead; field defaults are callables (`field.default(model)`).
3. **`image_whitelist` lines must be BARE addresses** (spec: “one sender
   email per line”): a `Name <a@b.c>` line is INVALID on save (dropped) —
   the display-form tolerance lives in `_image_sender_allowed` (the MATCH
   side), which is where `email_from` values come from. Encoded as a
   triangulate test after the first expectation was corrected.
4. **Documented interaction**: `stripMessageStyles` also strips the
   `o_webmail_body_plain` wrapper class from our own plain-text render
   (strip removes EVERY class attr by design); cosmetic only — content
   preserved, asserted in the node harness.

## Files changed (PR-2f)

| File | Status |
|------|--------|
| `models/mail_management_settings.py` | +11 fields, +9 helpers, create/write normalization (~200 lines added) |
| `controllers/main.py` | `_settings_payload` helper, extended save/get, /messages limit=None (AST-verified after daemon repair) |
| `static/src/utils/message_policy.js` | NEW (~190 lines, pure) |
| `static/src/webmail_client.js` | settings state/mapping, pipeline, default sort, mark-read timer, next-on-delete, whitelist allow |
| `static/src/xml/webmail_client.xml` | +showCheckboxes/simpleAttachmentList props |
| `static/src/components/message_list.js` + xml | showCheckboxes prop + t-if gating (3 surfaces) |
| `static/src/components/reading_pane.js` + xml | simpleAttachmentList prop + compact list variant |
| `static/src/components/settings_dialog.js` + xml | REWRITTEN tabbed (4 tabs, all controls) |
| `static/src/scss/webmail.scss` | quote `<details>` toggles + plain-body + simple-attachment styles |
| `static/tests/tours/webmail_tour.js` | +8 PR-2f steps |
| `i18n/es_CL.po` + `i18n/mail_management.pot` | +25 mirrored entries each |
| `__manifest__.py` | 19.0.11.0.0 + message_policy.js asset |
| `tests/test_view_settings.py` | NEW (60 test methods) |
| `tests/test_message_policy.mjs` | NEW (20 real node checks) |
| `tests/__init__.py` | +1 import |
| 7 legacy test files | version trackers → 19.0.11.0.0 |
| `tests/test_webmail_settings.py` | `_buildBodyMarkup` source window 500→800 (helper grew) |
| `openspec/changes/webmail-complete-management/tasks.md` | PR-2f section → `[x]` |

## Deviations / interpretation notes (PR-2f)

1. **default_sort carries an optional priority suffix** (`date+unread_first`):
   the spec said “validated against the EXISTING controller sort whitelist
   keys incl. priority suffixes” — implemented as sort-key ∪ single
   optional `+priority` suffix, both validated against the controller's
   OWN whitelists (lazy import; the controller stays the single source of
   truth). `_get_sort_tuple()` returns `(sort, [priority])`.
2. **Invalid default_sort**: REJECTED with `UserError` by the endpoint
   (parity with `signature_position`) and IGNORED on direct model writes —
   the task's “reject/ignore” both honored at their natural layers.
3. **Pure-JS helpers are tested by REAL node execution in-container**
   (`tests/test_message_policy.mjs`, data-URL import so node 18 runs the
   import-free module) — reported SEPARATELY from the docker odoo suite
   (which holds the python + wiring contracts). The docker suite never
   fabricates JS behavior.
4. **Page size is server-side**: the client sends NO limit; the endpoint
   resolves the caller's `messages_per_page` (explicit `limit` still
   wins for other callers/tests). Search-only in the hot path — no
   singleton auto-create per list call.
5. **Quote collapse uses native `<details>/<summary>`** (zero JS
   handlers, keyboard-accessible); “levels beyond max hide behind ONE
   Show-more-quote toggle”. Labels are `_t()` parameters, not baked into
   the pure function.
6. **UI verified in a real browser**: tab switch to Message list, toggle
   show-checkboxes, save, notification, and the list re-rendering without
  the select-all checkbox (tour). NOT browser-verified: deferred mark-read
   timer firing (needs a >0s wait in a tour — wiring + pure
   `markReadDelayMs` covered), strip-styles/quote-collapse visual output
   (pure helpers + pipeline wiring covered; the seed message has no
   styling/quotes), next-on-delete selection (pure `nextMessageId` +
   wiring covered), whitelist allow in the browser (model helper +
   `senderInWhitelist` covered).
7. **`openspec/config.yaml` `project.version` left at its stale value**
   (19.0.1.0.0) — every previous bump in this chain left it too;
   syncing it is a parent/lifecycle decision (the file warns against
   destructive edits).

## Remaining tasks (unchecked `- [ ]` in tasks.md)

- **None** — PR-2a/2b/2c/2d/2e/2f are all checked; no parent-owned
  (`<!-- sdd-owner: parent -->`) rows exist in this tasks.md. Bounded
  review of PR-2f is a parent-lifecycle action.

## PR boundary (PR-2f)

PR-2f complete: tasks.md PR-2f `[x]`, canonical harness **647 GREEN**
with es_CL active (579 baseline preserved + 68 new test methods incl.
node checks), fresh-install real-Chrome tour **648 GREEN incl. TOUR
SUCCEEDED** (8 new PR-2f steps through the tabbed dialog), polib
merge-check OK (106 entries), ruff clean, all JS/XML well-formed, no
`t-raw=`, nothing staged, nothing committed (user owns commits).
**Next: PR-2g (folders-mapping + compose + theme)** per the parent plan.

---

> **PR-2e (UX polish: settings + image policy + simplified buttons) —
> COMPLETE.** Strict TDD mode active. All evidence from real docker harness
> runs. Baseline before this PR: **502 tests GREEN @ 19.0.9.0.0** (safety-net
> run re-verified at the start: `502 tests 15.09s`, exit 0 — matching the
> PR-2d final). Final: **579 canonical (es_CL active in the DB — the PO
> load path is exercised by the plain `-u` run) + 580 fresh-install
> real-Chrome tour (TOUR SUCCEEDED through the sort menu, the images-blocked
> bar, the settings dialog cycle and Refresh all), 0 failures** @
> **19.0.10.0.0**. The 502 baseline tests are ALL still green (the 77 new
> test methods are pure additions; the updated wiring needles in
> test_list_sorting / test_message_movement / test_webmail_ui assert the
> CONSOLIDATED UI — the CONTROLLER sort contract is byte-identical).

---

## TDD Cycle Evidence (PR-2e)

| Task | Phase | Test file | Result | Docker evidence |
|------|-------|-----------|--------|-----------------|
| Safety net (before any edit) | BASELINE | whole suite | 502 tests, 0 failures | `odoo.tests.stats: mail_management: 502 tests 15.09s`, EXIT=0 |
| Settings + UI RED (58 test methods: model/isolation/controller, compose position wiring, image policy wiring, consolidation needles, es_CL term, version tracker; + updated needles in test_list_sorting/test_message_movement/test_webmail_ui + 6 version trackers → 19.0.10.0.0) | RED | `tests/test_webmail_settings.py` (+ needle updates in 3 existing files) | 61 FAIL/ERROR, 502 baseline still green | `566 tests 16.91s`, EXIT=1; e.g. `KeyError: 'mail.management.settings'` model missing, every wiring needle missing |
| GREEN pass 1 (model + rules + ACL + endpoints + SettingsDialog + prepareBody policy + compose placement + consolidation + manifest + i18n + tour) | GREEN | same | 7 failures left — ALL test-side needles except ONE real API finding: **Odoo 19 REMOVED `_sql_constraints`** (logs `no longer supported, please define models.Constraint`) → migrated to `_settings_user_uniq = models.Constraint('unique (user_id)', …)` | `566 tests 21.27s`, EXIT=1 |
| GREEN pass 2 (needle fixes: applySort/togglePriority live in the component XML, `t-att-data-sort-key` dynamic binding, forward route-auth slice) | GREEN | same | 2 failures left (same class) | `566 tests 20.03s`, EXIT=1 |
| GREEN pass 3 | GREEN | same | 0 failures | `odoo.tests.stats: mail_management: 566 tests 20.30s`, EXIT=0 |
| TRIANGULATE (+13: idempotent same-value save, no-op save, position flip-flop, explicit False, fresh-controller persistence, third-user independence, reveal-or-setting composition (source), reveal-only-adds-open-id, per-message allow in _buildBodyMarkup, above/below replacement branches, image-signature above path) | TRIANGULATE | same | 2 slicing bugs in the SOURCE-ASSERTION helpers (empty slice: first `_reprepareOpenBody` occurrence is a CALL not the def) → anchored on method definitions | `579 tests 19.80s`, EXIT=1 → `579 tests 19.99s`, EXIT=0 |
| REFACTOR (extracted `_buildBodyMarkup(raw, attachments, messageId)` — single source of truth for the display policy, shared by openMessage + _reprepareOpenBody; `composeBodyHtml` was already the pure placement helper) | REFACTOR | whole suite | 1 test followed the refactor (needle moved to the shared helper), then 0 failures | `579 tests 22.50s`, EXIT=0 |
| polib merge-check (BEFORE the docker run, in-container) | GATE | `python3` in the odoo:19.0 image | `po.merge(pot)` keeps module+flavor markers on every entry (NOTE: polib stores `#.` lines in `entry.comment` WITHOUT the `#.` prefix — the PR-2d check compares those) | `MERGE-CHECK: OK | entries: 81` |
| Fresh-install + real headless-Chrome tour (DB dropped/recreated; seed message now carries a remote tracking image) | UI/E2E | `tests/test_webmail_tour.py` (HttpCase) | +12 PR-2e steps: sort menu open → “Date (oldest first)” → oldest first · open message → **images-blocked bar shown (default policy)** → click Show images → **remote img renders** · gear → dialog renders → toggle Allow remote images → Save → “Settings saved.” notification · manage menu → Refresh all → deterministic credential notification | `-i … --workers 0 --db-filter='^local_test$'`: `580 tests 24.79s`, `║ TOUR mail_management_webmail_tour SUCCEEDED ║`, `0 failed, 0 error(s)` |
| Final canonical `-u` run (es_CL re-activated with `--load-language` after the tour DB drop) | FINAL | whole suite | 0 failures | `odoo.tests.stats: mail_management: 579 tests 21.71s`, EXIT=0 |

### Test Summary (PR-2e)

- **Total tests written (this PR)**: 77 new test methods (58 in
  `test_webmail_settings.py` across 6 classes — 7 model + 5 isolation + 11
  controller + 4 compose-position wiring + 6 image-policy wiring + 13
  consolidation wiring + 1 es_CL + 13 triangulate, minus overlap — plus 3
  new tests in `test_webmail_ui.py` and the RENAMED/updated wiring tests in
  `test_list_sorting.py`/`test_message_movement.py`; 6 version-tracker
  updates across the chain).
- **Total tests passing**: 579/579 canonical (580 with the browser tour)
- **Layers used**: Unit/Integration (TransactionCase) 78, E2E (headless
  Chrome tour) 1
- **Approval tests**: none
- **Pure functions created**: `composeBodyHtml` (exported, compose
  placement), `containsRemoteImages` + the option-aware `prepareBody`
  (prepare_body.js), `_buildBodyMarkup` (client),
  `MailManagementSettings._get_or_create_for`

## What was built (PR-2e)

### A. Settings model + endpoints

- **`models/mail_management_settings.py`** (NEW): `mail.management.settings`
  — per-user singleton: `user_id` (required, default current user) with a
  **`models.Constraint('unique (user_id)')`** (Odoo 19 API — `_sql_constraints`
  is GONE, see finding #1), `allow_remote_images` (Boolean, default False),
  `signature_position` (Selection below|above, default below, required).
  `_get_or_create_for(user)` search-first idempotent singleton helper.
- **Security**: `rule_settings_user_own` + `rule_settings_manager_all`
  (the established pattern) + ACL rows (user 1,1,1,0 — a singleton is never
  unlinked by users; manager full).
- **`controllers/main.py`**: `GET /mail_management/settings` (auto-creates
  the caller's singleton; domain pinned to `env.user` so even a manager only
  reads their own through the webmail) + `POST
  /mail_management/settings/save` (partial updates; `signature_position`
  whitelisted to below|above else `UserError(“Invalid signature position.”)`).

### B. Settings panel

- **`SettingsDialog`** (new OWL component + template): Bootstrap switch for
  Allow remote images (with the tracking warning help text), select for
  signature position (Below/Above the quote), **Manage signatures…**
  button, Save/Close footer (`t-set-slot="footer"`).
- **Gear ⚙ button** (`o_webmail_settings_btn`, fa-cog, tooltip “Settings”)
  first in the tree header. **Manage signatures** opens the EXISTING action
  xmlid `mail_management.action_mail_management_signature` via `doAction`
  (see deviation #2).
- **`WebmailClient`**: `state.settings` loaded in `onWillStart`
  (`Promise.all` with folders), `saveSettings` → RPC → notification →
  `_reprepareOpenBody()` (LIVE apply, no reload).

### C. Image display policy

- **`prepare_body.js`**: `prepareBody(html, attachments, {allowRemoteImages})`
  — False (default): current blocking (src stripped, dashed placeholder,
  alt “Remote image blocked”); True: remote http(s) srcs render directly
  (stored bodies remain server-sanitized — this only skips the client-side
  strip). CID resolution unchanged regardless. NEW `containsRemoteImages(html)`
  pure helper drives the bar.
- **Per-message reveal**: `state.revealedImageIds` + `showImages()`; the
  reading pane shows a subtle **“Images blocked — Show images”** bar
  (`o_webmail_images_blocked_bar` / `o_webmail_show_images_btn`) ONLY when
  the open message `hasRemoteImages` AND the effective policy blocks it;
  effective allow = `settings.allowRemoteImages ||
  revealedImageIds.includes(messageId)`. The raw sanitized body is kept on
  the message (`rawBodyHtml`) so `_reprepareOpenBody` re-renders in place
  without refetching (settings change AND reveal share `_buildBodyMarkup`).

### D. Signature placement

- **`compose_dialog.js`**: `signaturePosition` prop ('below' default);
  pure exported `composeBodyHtml(quoteHtml, signatureHtml, position)` →
  above = `signatureHtml + quoteHtml`, below = `quoteHtml + signatureHtml`;
  `_replaceSignature` inserts before the `blockquote` when above (appendChild
  when below / no quote). 'new' mode has no quote (identical either way).

### E. Button consolidation (controller contracts UNCHANGED)

- **MessageList header**: ONE **Sort** dropdown (`o_webmail_sort_menu_btn`/
  `_item`): Date (newest/oldest first), From (A→Z / Z→A), Subject (A→Z /
  Z→A) + Unread-first / Starred-first checkboxes — replacing the 3 clickable
  headers + 2 toggle buttons. Column row is now plain labels. The menu emits
  the SAME PR-2a whitelisted keys (`setSort(key)` absolute assignment
  instead of the old toggle; sort/priority/filter RPC params byte-identical).
- **ReadingPane**: left `[Reply][Reply-all][Forward]`; right `[star icon]` +
  overflow ⋯ (`o_webmail_msg_menu`): Show images (when blocked) / Mark as
  unread / Move to folder (one item per destination — replaces the select) /
  Delete (label switches to Delete permanently in Trash).
- **FolderTree header**: `[⚙ Settings][➕ folder][⋯ mailbox menu: Add mailbox /
  Refresh all]` — the standalone Refresh button is GONE (Refresh all in the
  menu; `refresh()` method removed, `refreshAll()` kept).
- **Bulk bar**: icon-only buttons (mark read/unread, star/unstar, delete) with
  `t-att-title` tooltips; move select stays (it is not a button).
- **Tour**: sort toggle step → sort-menu steps; final Refresh steps →
  manage-menu Refresh all; +12 PR-2e steps (see evidence table).

### i18n (PR-2e)

- **+29 entries** in `i18n/es_CL.po` (mirrored in POT, msgstr ""): 1
  odoo-python (`"Invalid signature position."` → `"Posición de firma no
  válida."`) + 28 odoo-javascript (settings dialog, sort menu, overflow
  menu, images bar, notifications — every new string routed through `_t()`
  getters, the established translatable pattern). 81 entries total; PO/POT
  msgid sets identical; polib merge-check OK in-container.

### Versioning

- `19.0.9.0.0` → **`19.0.10.0.0`** (manifest + all SEVEN version trackers:
  test_webmail_ui, test_compose_send, test_folder_management,
  test_message_flags, test_signatures, test_message_search + the new one in
  test_webmail_settings).

## Real bugs / findings (PR-2e)

1. **Odoo 19 REMOVED `_sql_constraints`** (silent at model-load: just a
   warning log `no longer supported, please define models.Constraint`; the
   constraint simply never reaches the DB). Migrated to the declarative
   `models.Constraint('unique (user_id)', "message")` API (base's
   `ir.config_parameter._key_uniq` is the core precedent). The RED test
   caught it (`IntegrityError not raised`).
2. **Auto-formatter daemon mangled inserts AGAIN (5th+ occurrence across
   the change)**: controller methods landed one indent deep (nested inside
   `get_signatures`) AND test_webmail_ui tests nested inside a method —
   both caught by the AST class-method listing (the standing check) and
   repaired with line-based surgery; re-verified after every edit batch.
3. **polib merge-check gotcha**: `entry.comment` does NOT include the `#.`
   prefix (the first merge-check invocation reported all 81 entries “bad”;
   the correct comparison strips it) — documented so the next PR doesn't
   repeat it.

## Files changed (PR-2e)

| File | Status |
|------|--------|
| `models/mail_management_settings.py` | NEW (~75 lines) |
| `models/__init__.py` | +1 import |
| `controllers/main.py` | +2 endpoints (~70 lines, AST-verified class-level) |
| `security/security_groups.xml` | +2 settings ir.rules |
| `security/ir.model.access.csv` | +2 rows |
| `static/src/components/settings_dialog.js` + `static/src/xml/settings_dialog.xml` | NEW (~115 + ~60 lines) |
| `static/src/utils/prepare_body.js` | option-aware policy + `containsRemoteImages` (~30 lines) |
| `static/src/components/folder_tree.js` + xml | gear button, addFolder label, refresh button removed |
| `static/src/components/message_list.js` | REWRITTEN (sort menu + icon-only bulk bar, ~110 lines saved) |
| `static/src/xml/message_list.xml` | REWRITTEN (sort menu, plain columns, tooltips) |
| `static/src/components/reading_pane.js` + xml | REWRITTEN (overflow menu + images bar) |
| `static/src/components/compose_dialog.js` | `composeBodyHtml` + position-aware prefill/replace |
| `static/src/webmail_client.js` | settings state/load/save, reveal, `_buildBodyMarkup`, `setSort`, openSettings/openSignatures, refresh consolidation (~130 lines) |
| `static/src/xml/webmail_client.xml` | new bindings (onSetSort/onOpenSettings/imagesBlocked/onShowImages) |
| `static/src/scss/webmail.scss` | +images-bar/sort-menu/msg-menu styles |
| `static/tests/tours/webmail_tour.js` | sort steps replaced + refresh-all + 12 PR-2e steps |
| `tests/test_webmail_tour.py` | seed message gains a remote tracking image |
| `i18n/es_CL.po` + `i18n/mail_management.pot` | +29 mirrored entries each |
| `__manifest__.py` | 19.0.10.0.0 + 2 new assets |
| `tests/test_webmail_settings.py` | NEW (58 test methods) |
| `tests/test_webmail_ui.py` | +3 PR-2e wiring tests + version tracker |
| `tests/test_list_sorting.py`, `tests/test_message_movement.py` | consolidation needles updated |
| `tests/test_compose_send.py`, `test_folder_management.py`, `test_message_flags.py`, `test_signatures.py`, `test_message_search.py` | version trackers → 19.0.10.0.0 |
| `tests/__init__.py` | +1 import |
| `openspec/changes/webmail-complete-management/tasks.md` | PR-2e section → `[x]` |

## Deviations / interpretation notes (PR-2e)

1. **`_sql_constraints` → `models.Constraint`** (task said “unique” — the
   Odoo 19 way is the declarative Constraint; the old attribute is a no-op).
2. **Manage signatures opens the action xmlid with `doAction(xmlid)`** —
   the action's target is `current`, so it navigates to the Signatures list
   (breadcrumb back), the SAME established pattern as the module's own
   `openMailboxConfig`. Odoo 19's `doAction` has NO `options.target`
   override (verified in-container in `action_service.js`: target comes from
   the action record), so a dialog-target variant would have required an
   inline act_window copy — rejected to honor the task's literal “xmlid from
   views”. The button lives IN the webmail settings panel, which is the
   “without leaving” part (no menu hunting).
3. **`o_webmail_priority_toggle` class retired** (checkboxes in the sort
   menu are `o_webmail_sort_priority_check`); `o_webmail_sort_header`/
   `o_webmail_move_select`/`o_webmail_refresh_btn` removed — all needle
   updates are in the wiring tests; the CONTROLLER contracts (sort/priority
   whitelists, move/delete endpoints) are untouched.
4. **Reveal keeps the raw body in memory** (`rawBodyHtml` on the message
   state) instead of stashing the original src on the blocked element —
   simpler, and re-preparing from the stored sanitized body guarantees the
   revealed render matches a fresh open.
5. **Standalone per-mailbox Refresh button removed** (scope E names the ⋯
   menu's “Refresh all” as the surface); `refreshAll` syncs every mailbox of
   the user — slightly heavier per click, accepted per the scope letter.
6. **`allow_remote_images` coercion**: the endpoint accepts real JSON booleans
   (`bool()`); a non-bool truthy value would coerce True — jsonrpc sends
   booleans, and the panel is the only caller (documented in the route).
7. **UI verified in a real browser** through: sort menu apply, images-blocked
  bar → reveal → remote img render, settings dialog cycle (toggle → save →
  notification), Refresh-all path. NOT browser-verified: the Manage
  signatures navigation (doAction xmlid — same mechanism as the existing
  openMailboxConfig, wiring-asserted only) and the above-position compose
  placement (source-asserted via the pure helper + triangulation).

## Remaining tasks (unchecked `- [ ]` in tasks.md)

- **None** — PR-2a/2b/2c/2d/PR-2e are all checked; no parent-owned
  (`<!-- sdd-owner: parent -->`) rows exist in this tasks.md. Bounded review
  of PR-2e is a parent-lifecycle action.

## PR boundary (PR-2e)

PR-2e complete: tasks.md PR-2e `[x]`, canonical harness **579 GREEN** with
es_CL active (502 baseline preserved + 77 new test methods), fresh-install
real-Chrome tour **580 GREEN incl. TOUR SUCCEEDED** (12 new PR-2e steps),
polib merge-check OK, ruff clean, all JS/XML well-formed, no `t-raw=`,
nothing staged, nothing committed (user owns commits). **Next:
parent-lifecycle** (bounded review of PR-2e → verify/sync/archive of the
change; the ORIGINAL change `webmail-and-ai-assistant` still has PR-1d/1e
OAuth outstanding, plus slice 2 AI).

---

## TDD Cycle Evidence (PR-2d)

| Task | Phase | Test file | Result | Docker evidence |
|------|-------|-----------|--------|-----------------|
| Safety net (before any edit) | BASELINE | whole suite | 466 tests, 0 failures | `odoo.tests.stats: mail_management: 466 tests 12.23s`, EXIT=0 |
| Search RED (controller contract + UI wiring + es_CL + version) | RED | `tests/test_message_search.py` (22 tests first pass) | 27 FAIL/ERROR, 466 baseline still green | `497 tests 13.15s`, EXIT=1; e.g. `FAIL: TestMessageSearch.test_search_matches_subject — AssertionError: Lists differ: [2396, 2395, 2394, 2393, 2391] != [2391]` (search ignored → full folder listing), all wiring/es_CL/version tests failing |
| GREEN pass 1 (controller `search` param + `_escape_like`/`_search_domain` helpers + UI + i18n + version bump) | GREEN | same | 5 failures left — ALL test-side: wiring needles asserted in the wrong file (template renders via getters: "Searching:" lives in the JS, `onAddMailbox` in the JS props not the XML), `self.with_context` on a test class, `json.loads` on the commented manifest, an inverted comment-walk-back in the POT mirror check | `497 tests 13.23s`, EXIT=1 |
| GREEN pass 2 (test expectations corrected against correct behavior) | GREEN | same | 0 failures | `odoo.tests.stats: mail_management: 497 tests 13.15s`, EXIT=0 |
| TRIANGULATE (+5: multi-word PHRASE semantics — `report quarterly` ≠ match; unicode/accents `diseño`/`reunión`; ~10k-char body middle match; input trimming; search+limit/offset pagination) | TRIANGULATE | same | 0 failures (first try) | `502 tests 13.67s`, EXIT=0 |
| REFACTOR (search logic was factored into pure module-level helpers `_escape_like` + `_search_domain` DURING GREEN; no post-triangulate refactor needed — final run re-verifies) | REFACTOR | whole suite | 0 failures | `502 tests 13.84s`, EXIT=0 |
| polib merge-check (BEFORE the docker run, in-container) | GATE | `python3` in the odoo:19.0 image | `po.merge(pot)` keeps module+flavor markers on every entry | `MERGE-CHECK OK: 52 entries; every entry keeps module+flavor markers after po.merge(pot)` |
| Fresh-install + real headless-Chrome tour (DB dropped/recreated) | UI/E2E | `tests/test_webmail_tour.py` (HttpCase) | +8 PR-2d steps: search fill (debounced apply) → indicator `Searching: 'Webmail Tour'` → results replace the folder list → clear → folder view restored (order-aware: date asc from the earlier toggle) → manage menu opens → Add mailbox entry present → menu closed | `-i … --workers 0 --db-filter='^local_test$'`: `503 tests 16.75s`, `║ TOUR mail_management_webmail_tour SUCCEEDED ║`, `0 failed, 0 error(s)` |
| Final canonical `-u` run (es_CL active in the DB) | FINAL | whole suite | 0 failures | `odoo.tests.stats: mail_management: 502 tests 13.84s`, EXIT=0 |

### Test Summary (PR-2d)

- **Total tests written (this PR)**: 31 (22 core: 17 controller-search incl.
  escaping/scope/blank/isolation + 6 UI wiring + 4 es_CL/PO/POT/version minus
  overlap; +5 triangulate; 36 total test methods in the file across 3 classes)
- **Total tests passing**: 502/502 canonical (503 with the browser tour)
- **Layers used**: Unit/Integration (TransactionCase) 35, E2E (headless
  Chrome tour) 1
- **Approval tests**: none
- **Pure functions created**: controller module level `_escape_like`,
  `_search_domain` (+ JS getters `searchPlaceholder/searchLabel/
  searchIndicatorLabel/clearSearchLabel`, `addMailboxLabel/refreshAllLabel/
  manageMenuLabel`)

## What was built (PR-2d)

### Search contract (the parent's specified scope)

- **`POST /mail_management/messages`** gains `search=None`: when non-blank
  after strip, the domain becomes **mailbox-wide**: `folder_id` only RESOLVES
  the current mailbox (searched through `request.env` → per-user ir.rules; a
  forged cross-user id resolves nothing → `[(0, '=', 1)]` → no results, never
  a leak), then `|` `ilike` across `subject`, `email_from`, `email_to`,
  `body_text`. `%`/`_`/`\` in the input are escaped (Postgres default LIKE
  escape) so they match LITERALLY. Blank/whitespace search → plain folder
  view (param ignored). Composes with the PR-2a whitelists (`sort`,
  `priority`, `filter`) and `limit`/`offset`. Selecting another folder resets
  the search (folder view).
- **UI (`MessageList`)**: search bar above the list (`o_webmail_search_input`
  - `o_webmail_search_btn`); typing auto-applies after a 400 ms debounce,
  Enter/button apply immediately; while active a visible indicator row shows
  `Searching: 'x'` (`o_webmail_search_indicator`) with a **Clear search**
  button (`o_webmail_search_clear`) that returns the folder view; the empty
  state switches to "No messages match your search.". All strings via `_t`
  getters (translatable).
- **`WebmailClient`**: `state.searchQuery` (applied query), `applySearch`/
  `clearSearch`, `params.search` on the RPC.

### Mailbox management in webmail (minimal, per scope)

- **`FolderTree` header**: next to "+", an overflow menu
  (`o_webmail_manage_btn`) with **Add mailbox** (`o_webmail_add_mailbox_item`)
  and **Refresh all** (`o_webmail_refresh_all_item`).
- **`WebmailClient.openAddMailbox()`**: `doAction` of an inline
  `ir.actions.act_window` on `mail.management.mailbox` with
  `views: [[false, "form"]], target: "new"` — the STANDARD mailbox form in a
  dialog over the webmail (user stays in context; the tree reloads
  `on_close`). Full config remains in My Mailboxes.
- **`WebmailClient.refreshAll()`**: `POST /mail_management/refresh` with NO
  `mailbox_id` → the existing batched `_cron_sync_mailboxes` through
  `request.env` (per-user ir.rules; per-mailbox failures caught server-side).

### es_CL translations — the empirically learned grammar, applied

- **+45 entries** in `i18n/es_CL.po` (and mirrored in `mail_management.pot`,
  msgstr ""): 12 Python-flavored (`#. odoo-python` — PR-2a/2b UserError terms
  like `"The folder name cannot be empty."`) + 33 JavaScript-flavored
  (`#. odoo-javascript` — the `_t()` strings of PR-2a/2b/2c/2d; web
  translations are served by `/web/webclient/translations` via
  `CodeTranslations.get_web_translations`, which filters by that exact
  marker). Every entry carries BOTH lines `#. module: mail_management` AND
  the flavor marker; occurrence lines use
  `code:addons/mail_management/…:0`. The 7 pre-existing folder-label entries
  kept byte-for-byte.
- **POT mirrors the PO msgids exactly** (polib merge replaces entry comments
  with the POT's — the importer crash class is guarded by the in-container
  merge-check AND by `test_pot_mirrors_po_entries`).
- **Regression test**: `test_folder_name_empty_translated_es_cl` activates
  es_CL and asserts `env._("The folder name cannot be empty.")` →
  `"El nombre de la carpeta no puede estar vacío."` (mirrors the
  `test_folder_labels.py` pattern). `test_po_entries_have_odoo_grammar`
  guards every entry's marker lines.
- **The PO load path is exercised for real**: local_test has es_CL active
  (`--load-language` run), so the plain canonical `-u` run upgrades the
  module with the extended PO — a grammar error would crash the registry (it
  did not; `502 tests 13.84s`, 0 failures).

### Versioning

- `19.0.8.0.0` → **`19.0.9.0.0`** (manifest + all FIVE existing version
  trackers + a new one in `test_message_search.py`).

## Real bugs / friction found & fixed during GREEN (PR-2d)

1. **RED-fixture bug**: the second test mailbox reused the same `imap_user` →
   `mail_management_mailbox_mailbox_uniq` violation in setUp (first RED run
   ERRORed before reaching the contract). Fixed with a distinct `imap_user`.
2. **External auto-formatter daemon actively rewrites files between tool
   calls** (the standing-check enemy): it re-indented inserted blocks (once
   breaking Python — caught by `ast.parse` + the class-method listing,
   repaired immediately; once merely cosmetically in JS — JS indentation is
   NOT semantic, verified via `node --check` + class-level grep). Defense:
   re-read/grep every edited file BEFORE and AFTER each docker run.
3. **Tour sort-order assumption**: my first "clearing returns to the folder
   view" step expected the NEWEST message first, but the tour had toggled
   `date asc` six steps earlier — the real browser correctly showed the
   OLDER message first. Fixed the step's expectation (order-aware).
4. **`--load-language es_CL` on the tour DB makes the UI Spanish** → the
   English tour needles ("Inbox") fail. The established procedure (fresh
   install WITHOUT it) is what the tour is written for; es_CL is activated
   for the canonical `-u` guard instead.
5. **One transient infra failure** in the `-u`-with-`--load-language` run:
   `FileNotFoundError` inside `ir_attachment._same_content` during test-user
   avatar generation — a filestore/DB tearing artifact of drop/recreate
   cycles sharing `filestore/local_test/`. Unrelated to the change;
   re-run green (`502 tests 13.84s`, 0 failures) — same handling as the
   documented PR-2a postgres-shutdown flake.

## Files changed (PR-2d)

| File | Status |
|------|--------|
| `controllers/main.py` | +`search` param on `/messages`, +`_escape_like`, +`_search_domain` (~75 lines) |
| `static/src/components/message_list.js` | +search bar state/handlers/getters, +empty-search label (~85 lines) |
| `static/src/xml/message_list.xml` | +search bar + indicator rows (~28 lines) |
| `static/src/components/folder_tree.js` | +manage-menu state/handlers/labels (~35 lines) |
| `static/src/xml/folder_tree.xml` | +manage menu button + dropdown (~27 lines) |
| `static/src/components/reading_pane.js` | `deleteLabel` now `_t()`-wrapped (was a plain string) |
| `static/src/webmail_client.js` | +`searchQuery` state, `applySearch`/`clearSearch`, `refreshAll`, `openAddMailbox` (~75 lines) |
| `static/src/xml/webmail_client.xml` | +search + manage-menu prop bindings |
| `static/tests/tours/webmail_tour.js` | +8 PR-2d steps |
| `i18n/es_CL.po` | +45 entries (12 odoo-python, 33 odoo-javascript) |
| `i18n/mail_management.pot` | +45 mirrored entries (msgstr "") |
| `__manifest__.py` | 19.0.9.0.0 |
| `tests/test_message_search.py` | NEW (36 test methods / 31 counted new) |
| `tests/__init__.py` | +1 import |
| `tests/test_compose_send.py`, `test_folder_management.py`, `test_message_flags.py`, `test_signatures.py`, `test_webmail_ui.py` | version trackers → 19.0.9.0.0 |
| `openspec/changes/webmail-complete-management/tasks.md` | PR-2d → `[x]` |

Estimated ~600 test + ~360 code/UI/i18n lines — within the 400-line code
budget for the first time in this chain (test lines excluded per the
established reporting).

## Deviations / interpretation notes (PR-2d)

1. **Debounced auto-apply + Enter/button**: the task said "debounced, Enter
   or button to apply" — implemented as BOTH: typing schedules a 400 ms
   auto-apply (the only reading that makes a debounce meaningful), Enter and
   the button cancel the timer and apply at once.
2. **Search resets on folder change** (unspecified): predictable folder-view
   semantics; the clear button is the documented way back.
3. **`_t(MODE_TITLES[…])` strings ("Reply"/"Forward"…) predate this change**
   (PR-1c) → no PO entries added for them; same for the other PR-1b/1c
   strings still visible in the components. Scope kept to this change's new
   strings, per the task letter.
4. **`Manage` label** added for the menu button's aria-label (one extra
   odoo-javascript entry beyond the two named in the task).
5. **Add-mailbox opens an inline act_window** (form view, `target: "new"`)
   rather than the list action xmlid — the task's intent is "the standard
   mail.management.mailbox form … user stays in the webmail context"; the
   xmlid ACTION would navigate the whole client away from the webmail.
6. **UI verified in a real browser** through the whole search cycle (type →
   indicator → results → clear → folder view) + the manage menu. NOT
   verified: typing a real mailbox into the Add-mailbox dialog (tour asserts
   presence only — creating a mailbox needs IMAP fields; the dialog itself is
   the standard pre-existing form, covered by its own views/config tests).

## Remaining tasks (unchecked `- [ ]` in tasks.md)

- **None** — all 4 PRs (PR-2a/2b/2c/PR-2d) are checked in tasks.md.
- No parent-owned (`<!-- sdd-owner: parent -->`) rows exist in this
  tasks.md; bounded review of PR-2d + change completion (verify/sync/
  archive) are parent-lifecycle actions.

## PR boundary (PR-2d — CHANGE COMPLETE)

PR-2d complete: tasks.md PR-2d `[x]`, canonical harness **502 GREEN** with
es_CL active in the DB (466 baseline preserved + 36 new test methods),
fresh-install real-Chrome tour **503 GREEN incl. TOUR SUCCEEDED** (8 new
PR-2d steps through the full search cycle + manage menu), polib merge-check
OK in-container, ruff clean, all JS/XML well-formed, no `t-raw=`, nothing
staged, nothing committed (user owns commits). **Next: parent-lifecycle**
(bounded review of PR-2d → verify/sync of the completed change
`webmail-complete-management`; the ORIGINAL change `webmail-and-ai-assistant`
still has PR-1d/1e OAuth outstanding, plus slice 2 AI).

---

## PR-2c history (compose pro: signatures + rich text — COMPLETE)

> Strict TDD mode
> active. All evidence from real docker harness runs. Baseline before this
> PR: 379 tests GREEN @ 19.0.7.0.0 (safety-net run re-verified at the start:
> `379 tests 8.72s`, exit 0). Final: **466 canonical + 467 fresh-install
> real-Chrome tour (TOUR SUCCEEDED), 0 failures** @ 19.0.8.0.0.

## TDD Cycle Evidence (PR-2c)

| Task | Phase | Test file | Result | Docker evidence |
|------|-------|-----------|--------|-----------------|
| Safety net (before any edit) | BASELINE | whole suite | 379 tests, 0 failures | `odoo.tests.stats: mail_management: 379 tests 8.72s`, EXIT=0 |
| Editor API verification (STEP 0) | BASELINE | container introspection | `web_editor` does NOT exist in Odoo 19 — the editor module is **`html_editor`**; `Wysiwyg` component exported from `@html_editor/wysiwyg`; mail's `Composer` (`static/src/core/common/composer.js`) and `website_profile`'s **`ProfileDialog`** (a real Dialog-embedded Wysiwyg — our exact use case) both use `<Wysiwyg config="…" onLoad.bind="…"/>`; config keys: `content` (markup), `placeholder`, `classList`, `Plugins` (defaults to `MAIN_PLUGINS` — `editor.js preparePlugins`), `onChange`; read via `editor.getContent()`; DOM via `editor.editable`; history via `editor.shared.history.addStep()`; `HtmlField` declares `supportedTypes: ["html"]` (widget="html" is NOT valid on Text fields) | in-container source reads @ odoo:19.0 image |
| Signatures + html send RED | RED | `tests/test_signatures.py` (61 tests) + `tests/test_html_send.py` (22 tests) | 75 FAIL/ERROR, 379 baseline still green | `454 tests 11.99s`, EXIT=1; `TypeError: MailManagementMailbox._send_message() got an unexpected keyword argument 'body_html'`; signature model missing → every signature test ERROR |
| GREEN pass 1 | GREEN | both files | 12 failures left: controller indentation mangling (recurring bug class — my inserted `get_signatures`/`send` landed one indent deep, nested inside `message_delete`; AST-verified class-level fix), a missed 4th version tracker, manifest data-path convention (module-relative, not prefixed), wrapper div reusing the body-textarea id | `454 tests 12.20s`, EXIT=1 |
| GREEN pass 2 | GREEN | both files | 1 failure: no-mailbox `get_signatures` must return ALL own signatures (RED expectation was the intended contract — implementation fixed) | `454 tests 12.39s`, EXIT=1 |
| GREEN pass 3 | GREEN | both files | 0 failures | `odoo.tests.stats: mail_management: 454 tests 12.03s`, EXIT=0 |
| TRIANGULATE (+8: rich signature content — links/images/inline-styles kept, `javascript:` href dropped; quote+signature combined body → both blocks in html AND plain fallback; forward keeps original attachments + signature; malicious signature block stripped in transit; whitespace-only html falls back to plain path) | TRIANGULATE | both files | 0 failures (first try) | `466 tests 12.28s`, EXIT=0 |
| REFACTOR (single `plain_part` computed once — wire message and Sent mirror can never diverge; was duplicated in `_send_message` + sent-record vals) | REFACTOR | whole suite | 0 failures | `466 tests 12.28s`, EXIT=0 |
| Fresh-install + real headless-Chrome tour (DB dropped/recreated) | UI/E2E | `tests/test_webmail_tour.py` (HttpCase) | +4 PR-2c steps: reply opens the dialog → **`.o_webmail_compose_wysiwyg .odoo-editor-editable` exists (the real Odoo WYSIWYG mounted in our dialog)** → `.o_webmail_compose_signature` picker present → cancel | `-i … --workers 0 --db-filter='^local_test$'`: `467 tests 14.87s`, `║ TOUR mail_management_webmail_tour SUCCEEDED ║`, `0 failed, 0 error(s)` |
| Final canonical `-u` run (post-refactor) | FINAL | whole suite | 0 failures | `466 tests 12.28s`, EXIT=0 |

### Test Summary (PR-2c)

- **Total tests written (this PR)**: 83 (61 in `test_signatures.py`: 12 model +
  6 isolation + 6 resolution + 6 controller + 2 config-UI + 11 rich-text
  wiring + 4 signature-version tracker + 14 triangulate; 22 in
  `test_html_send.py`: 18 multipart/sanitize/controller + 4 triangulate)
- **Total tests passing**: 466/466 canonical (467 with the browser tour)
- **Layers used**: Unit/Integration (TransactionCase, mocked transports) 82,
  E2E (headless Chrome tour) 1
- **Approval tests**: none (behavior-preserving refactor covered by the suite)
- **Pure functions created**: `MailManagementMailbox._html_body_to_plain`,
  `MailManagementSignature._enforce_single_default`,
  `ComposeDialog._escapeHtml/_quoteHtml/_signatureHtml/_initialBodyHtml`

## What was built (PR-2c)

### Editor embedding path (honest answer)

**The sanctioned direct `Wysiwyg` component embed** — NOT FieldHtml/
useRecord/RelationalModel. Verified in-container first (STEP 0): in Odoo 19
the module is `html_editor` (no `web_editor`); `website_profile.ProfileDialog`
is core's own precedent for a `Dialog`-embedded OWL component using
`<Wysiwyg config="…" onLoad.bind="…"/>`, and `mail.Composer` uses the same
component with `Plugins: MAIN_PLUGINS` + `onChange` + `editor.getContent()`.
We follow that exact pattern (config: `content` markup, `placeholder`,
`classList`, `onChange`; `Plugins` left to the `MAIN_PLUGINS` default = full
toolbar bold/italic/underline/link/lists/powerbox). The fallback toolbar was
NOT needed. Proven in real Chrome (tour step asserts
`.o_webmail_compose_wysiwyg .odoo-editor-editable`).

### Backend

- **`models/mail_management_signature.py`** (NEW): `name`, `user_id`
  (default current, required), optional `mailbox_id` (empty = all the user's
  mailboxes, ondelete cascade), `is_default`. `html_body` is
  `fields.Html(sanitize=True)` **plus an explicit `html_sanitize` in
  `create`/`write`** — see deviation #1. `_enforce_single_default()`:
  auto-unsets the previous default in the same (user, mailbox) scope
  (mailbox-specific and user-global defaults coexist — different scopes).
- **`models/mail_management.py`**:
  - `_get_default_signature()`: mailbox-specific default → user-global
    default → empty. `sudo()` with an EXPLICIT `user_id = mailbox.user_id`
    filter (deterministic in manager/cron contexts; equivalent to the
    ir.rule contract).
  - `_send_message(body_html=...)`: sanitize-out (`html_sanitize`) before
    the MIME exists as HTML; `EmailMessage.set_content(plain) +
    `add_alternative(html, subtype="html")` builds **multipart/alternative**
    (with attachments the stdlib promotes to multipart/mixed wrapping the
    alternative — asserted by test); plain fallback = explicit `body_text`
    or `html2plaintext(safe_html)` (single `plain_part` shared by wire +
    Sent mirror after REFACTOR); plain-only bodies keep the single-part
    `text/plain` path byte-identical. Sent copy: `body_html` (sanitized) +
    `body_text` (the plain fallback).
  - `_html_body_to_plain()` helper (odoo `html2plaintext`).
- **`controllers/main.py`**: `POST /mail_management/signatures` (`auth=user`,
  ir.rules-scoped: own signatures for the mailbox = mailbox-specific +
  globals; forged mailbox id still returns own globals with `default_id: False`;
  no mailbox = all own signatures); `/mail_management/send` accepts and
  forwards `body_html` (sanitized model-side).
- **`security/`**: `rule_signature_user_own` (`[('user_id','=',user.id)]`) +
  `rule_signature_manager_all` (`[(1,'=',1)]`) mirroring the mailbox pattern;
  `ir.model.access.csv` full CRUD (1,1,1,1) for user AND manager groups.

### Frontend (OWL, no jQuery)

- **`ComposeDialog`**: Wysiwyg body (see path above); signature dropdown
  (`o_webmail_compose_signature`) with "No signature" + labeled options
  (mailbox name / all mailboxes); default signature appended UNDER the
  quoted content behind the standard `--&nbsp;` separator, loaded in
  `onWillStart` so the initial editor content already includes it;
  switching REPLACES the marked block (`[data-o-mail-signature]`) via
  DOMParser-parsed server-sanitized HTML + `history.addStep()`; `new`
  compose mode (no quote). Reply/forward keep PR-1c behavior.
- **`MessageList`**: "Compose" button (`o_webmail_compose_new`) → parent's
  `openCompose('new')` (new-mode needs no opened message).
- **`WebmailClient`**: `openCompose` supports `"new"` (empty message payload).
- **Tour**: +4 steps (see evidence table).
- **Version** 19.0.7.0.0 → **19.0.8.0.0** (manifest + FOUR version-tracker
  tests updated — test_folder_management's was missed in GREEN pass 1).

## Real bugs / friction found & fixed during GREEN

1. **Recurring indentation-mangling bug** (3rd occurrence in this change's
   history): my inserted controller block landed one indent level deep —
   `get_signatures` + `send` became NESTED functions of `message_delete`
   (legal Python, ruff-silent). Detected via `'MailManagementController'
   object has no attribute 'send'`; fixed with AST-verified class-level
   surgery (the AST class-method listing is now the post-edit check).
2. **Manifest data-path convention**: `data` uses module-relative paths
   (`views/...`), only `assets` use the `mail_management/...` prefix — my
   RED wiring expectation used the wrong form; test corrected to the
   convention.
3. **Body wrapper id collision**: reusing `id="o_webmail_compose_body"`
   (the old textarea id) on the editor wrapper div tripped the
   "textarea is gone" wiring assertion — renamed to
   `o_webmail_compose_body_wrap`.
4. **A fourth version-tracker test** (`test_folder_management.py`) needed
   the 19.0.8.0.0 bump (only three were updated in GREEN pass 1).
5. **No-mailbox `get_signatures` contract**: implementation initially
   filtered to globals-only; the RED test encoded the intended UX (all own
   signatures, global default) — implementation aligned to the test.

## Files changed (PR-2c)

| File | Status |
|------|--------|
| `models/mail_management_signature.py` | NEW (~110 lines) |
| `models/mail_management.py` | +`_get_default_signature`, +`_html_body_to_plain`, `_send_message` html/multipart path (~90 lines) |
| `models/__init__.py` | +1 import |
| `controllers/main.py` | +`get_signatures` route, +`body_html` on send (~90 lines) |
| `security/security_groups.xml` | +2 signature ir.rules |
| `security/ir.model.access.csv` | +2 rows |
| `views/mail_management_signature_views.xml` | NEW (list + form + action + menu) |
| `static/src/components/compose_dialog.js` | REWRITTEN (Wysiwyg + signatures, ~350 lines) |
| `static/src/xml/compose_dialog.xml` | REWRITTEN (editor + picker) |
| `static/src/components/message_list.js` + `xml` | +Compose-new button + prop |
| `static/src/webmail_client.js` + `xml` | openCompose 'new' + binding |
| `static/tests/tours/webmail_tour.js` | +4 PR-2c steps |
| `__manifest__.py` | 19.0.8.0.0, +html_editor dep, +signature views |
| `tests/test_signatures.py` | NEW (61 tests) |
| `tests/test_html_send.py` | NEW (22 tests) |
| `tests/__init__.py` | +2 imports |
| `tests/test_webmail_ui.py`, `test_compose_send.py`, `test_message_flags.py`, `test_folder_management.py` | version trackers → 19.0.8.0.0 |

Estimated ~1,050 test + ~700 code/UI lines — over the 400-line review
budget like every PR in this chain (parent forecast accepted chained PRs;
reported honestly).

## Deviations / interpretation notes (PR-2c)

1. **`html_body` is `fields.Html(sanitize=True)`, not `Text`**: the task
   letter said Text + sanitize-on-write. STEP 0 found the Odoo 19 html
   widget (`widget="html"` → `HtmlField`) declares `supportedTypes:
   ["html"]` and NO core view uses it on a Text field — following the task's
   own "DO NOT invent an API; adapt to what you find" rule, the field is
   the sanctioned Html type AND the explicit `tools.html_sanitize`
   create/write override is kept (task requirement, defense in depth — the
   same double-guard pattern as `mail.management.message.body_html` in this
   module). RPC/programmatic writes are sanitized regardless of field type.
2. **Single-default = auto-unset** (task offered constraint OR auto-unset):
   auto-unset chosen — friendlier for the config UI; enforced in create and
   write; scope = (user, mailbox) with `False` = global scope.
3. **Signature switch insertion**: the task said "inserting replaces the
   previously inserted block" — implemented with a `data-o-mail-signature`
   marker div (findable after any user editing); signature HTML parsed via
   DOMParser (no innerHTML assignment), source is server-stored-sanitized
   content (same trust level as the reading pane's `t-out`).
4. **`--&nbsp;` separator**: standard sig marker is `--` (dash dash space);
   in HTML the trailing space collapses, so the separator paragraph uses
   `--&nbsp;`. The plain fallback renders it as `--` + newline boundary.
5. **Editor toolbar**: `MAIN_PLUGINS` default kept (full standard toolbar
   incl. powerbox) — no plugin subset invented.
6. **UI verified headlessly**: source wiring (11 tests) + REAL headless
   Chrome tour through the compose dialog (editor mounted, picker present,
   cancel). NOT verified in a browser: typing in the editor, switching a
   signature from the dropdown (no signatures exist in the tour DB — the
   picker shows only "No signature"), the signature config form's rich
   editor, and the sent result rendering. These are wiring-tested only.
7. **i18n**: new UI strings English-only (es_CL lands in PR-2d per plan).

## Test commands run (all real)

```bash
# canonical harness (final): 466 tests, EXIT=0
docker compose run --rm -T --no-deps --entrypoint odoo web \
  -d local_test --db_host=db -r odoo -w odoo -u mail_management \
  --test-enable --stop-after-init --test-tags=/mail_management

# browser tour (fresh DB): 467 tests incl. TOUR SUCCEEDED, 0 failed 0 error
docker compose exec -T db psql -U odoo -d postgres -c "DROP DATABASE IF EXISTS local_test;"
docker compose run --rm -T --no-deps --entrypoint odoo web \
  -d local_test --db_host=db -r odoo -w odoo -i mail_management \
  --test-enable --stop-after-init --test-tags=/mail_management \
  --workers 0 --db-filter='^local_test$'

# static gates
ruff check --no-cache lib/ models/ controllers/ tests/   # All checks passed!
node --check static/src/*.js static/src/components/*.js \
  static/tests/tours/webmail_tour.js                     # OK
python3 -c "xml.dom.minidom.parse(...)" per template     # OK (all)
```

## PR boundary (PR-2c)

PR-2c complete: 3/3 tasks checked in tasks.md, canonical harness **466
GREEN** (379 baseline preserved + 87 new incl. version trackers),
fresh-install real-Chrome tour **467 GREEN incl. TOUR SUCCEEDED** (the
WYSIWYG really mounts in the dialog), ruff clean, all JS/XML well-formed,
no `t-raw=`, nothing staged, nothing committed (user owns commits).
**Next: PR-2d (local search + mailbox mgmt polish + es_CL PO).**

---

## PR-2b history (folders & movement — COMPLETE)

> Strict TDD mode
> active. All evidence from real docker harness runs. Baseline before this
> PR: 286 tests GREEN @ 19.0.6.0.0 (safety-net run re-verified at the
> start: `286 tests 6.68s`, exit 0). Final: **379 canonical + 380
> fresh-install real-Chrome tour (TOUR SUCCEEDED), 0 failures** @
> 19.0.7.0.0.

---

## TDD Cycle Evidence (PR-2b)

| Task | Phase | Test file | Result | Docker evidence |
|------|-------|-----------|--------|-----------------|
| Safety net (before any edit) | BASELINE | whole suite | 286 tests, 0 failures | `odoo.tests.stats: mail_management: 286 tests 6.68s`, EXIT=0 |
| imap_tools 1.15 API verification (STEP 0) | BASELINE | container introspection | `folder.create(folder)`, `folder.rename(old,new)`, `folder.delete(folder)`, `copy(uid_list, destination_folder)`, `expunge()`, `flag(uids, flag_set, value)`; `MailMessageFlags.DELETED` | `imap_tools version: 1.15.0` inside the odoo:19.0 image |
| Folder CRUD RED (seams + model IMAP-first + controller + UI wiring) | RED | `tests/test_folder_management.py` | AttributeErrors: `_create_folder`/`_rename_folder`/`_delete_folder` (model), `folder_create/rename/delete` (controller), `ImapClient.create_folder/rename_folder/delete_folder/copy_messages/delete_messages/expunge/move_messages` (lib), UI needles missing | `373 tests 8.66s`; grep: 8x `'mail.management.mailbox' object has no attribute '_create_folder'` etc. |
| Movement RED (seams + move/trash model + controller + UI wiring) | RED | `tests/test_message_movement.py` | AttributeErrors: `_move_messages`, `message_move/message_delete`; `o_webmail_bulk_move`/`o_webmail_move_select` not found | same run: `373 tests 8.66s` |
| GREEN pass 1 | GREEN | both files | 31 failures left: malformed test date string (test bug), over-specific wiring needles, REAL ACL finding — user `perm_unlink=0` on folder+message blocked every user-driven delete | `373 tests 8.68s` |
| GREEN pass 2 (date + needles fixed; user unlink granted, ir.rule isolation unchanged) | GREEN | both files | 2 failures left: cross-user-move test conflated dest/message isolation; bulk-bar needle | `373 tests 8.77s` |
| GREEN pass 3 (expectations corrected against correct behavior) | GREEN | both files | 0 failures | `373 tests 8.77s` |
| TRIANGULATE (+6: empty-uid lib no-ops; same-folder move no-op [found a real wart: a pointless EXPUNGE could purge unrelated pending \Deleted marks — implementation guarded]; mixed same/other-folder move; empty-trash bulk purge; cross-mailbox create isolation) | TRIANGULATE | both files | 1 failure → fixed in the IMPLEMENTATION (`move_messages` empty guard), then 0 | `379 tests 9.26s` → `379 tests 8.78s`, 0 failures |
| REFACTOR (`_imap_session` ctx mgr shared by the 5 new methods, `_uids_by_folder` grouping helper, `_sync_folder` extracted from `_sync_mailbox` for the post-move refresh) | REFACTOR | whole suite | 0 failures | `379 tests 8.80s` |
| Fresh-install + real headless-Chrome tour (DB dropped/recreated) | UI/E2E | `tests/test_webmail_tour.py` (HttpCase) | first runs caught REAL bugs: Odoo 19 Dialog takes `t-set-slot="footer"` (not `buttons`) and the tour helper `text` is now `fill`; after fixes TOUR SUCCEEDED incl. 4 new PR-2b steps | `-i … --workers 0 --db-filter='^local_test$'`: `380 tests 11.22s`, `TOUR mail_management_webmail_tour SUCCEEDED`, `0 failed, 0 error(s)` |
| Final canonical `-u` run | FINAL | whole suite | 0 failures | `379 tests 8.82s`, EXIT=0 |

### Test Summary (PR-2b)

- **Total tests written (this PR)**: 93 (48 in `test_folder_management.py`;
  45 in `test_message_movement.py`)
- **Total tests passing**: 379/379 canonical (380 with the browser tour)
- **Layers used**: Unit/Integration (TransactionCase, mocked transports) 92,
  E2E (headless Chrome tour) 1
- **Approval tests**: none — the `_sync_mailbox` refactor was covered by the
  pre-existing `test_imap_sync.py` suite (all still GREEN after the
  `_sync_folder` extraction).
- **Pure functions created**: `_sanitize_folder_name`, `_uids_by_folder`
  (+ JS getters `moveTargets`, `viewingTrash`, `deleteLabel`)

## What was built (PR-2b)

### Backend — IMAP-FIRST policy (documented in code; deliberately different

from the PR-2a flags best-effort mirror: a failed IMAP op = UserError to the
client, NO local change — the local mirror must never diverge from the
server shape)

- **`lib/mail_imap_client.py`** — new seams over imap_tools 1.15 (verified
  in-container): `create_folder(name)`, `rename_folder(old,new)`,
  `delete_folder(name)`, `copy_messages(src, uids, dest)`,
  `delete_messages(src, uids)` (flags `\Deleted`), `expunge(folder)`,
  `move_messages(src, uids, dest)` = COPY + `\Deleted` + EXPUNGE (portable;
  no raw MOVE — RFC 3501 has none and imap_tools 1.15 does not wrap the
  extension). Folder-aware signatures (imap_tools operates on the SELECTED
  folder); empty uid lists are full no-ops.
- **`models/mail_management.py`** —
  - `_sanitize_folder_name` (strip; reject empty / >255 chars / `/` and
    `.` separators — simple child names only, v1);
  - `_create_folder(name)`: local-duplicate pre-check → IMAP CREATE →
    local `mail.management.folder` (role='other');
  - `_rename_folder(folder, new)`: same-name = idempotent no-op (some
    servers answer NO to `RENAME x x`); IMAP RENAME → local imap_name/name;
  - `_delete_folder(folder, force=False)`: guard `message_count > 0`
    without force → UserError naming the count; IMAP DELETE → local unlink
    (messages cascade);
  - `_move_messages(messages, dest)`: cross-mailbox dest → UserError;
    same-folder messages filtered out (no-op — COPY-to-self would churn
    server UIDs); IMAP move per source folder → LOCAL source records
    deleted (dest copies NOT fabricated — dest gets a cheap
    same-connection `_sync_folder` refresh, best-effort: a refresh failure
    after a successful server move is logged, never raised);
  - `_purge_messages(messages)`: `\Deleted` + EXPUNGE per folder → local
    unlink;
  - `_delete_messages_to_trash(messages)`: outside Trash + a trash folder
    exists → MOVE there; already in Trash (or NO trash folder — documented
    fallback) → PERMANENT; mixed sets partitioned; returns
    `{'moved_to_trash', 'purged'}`;
  - `_imap_session()` ctx manager + `_uids_by_folder()` helper (REFACTOR);
    `_sync_folder()` extracted from `_sync_mailbox` (behavior-identical;
    pre-existing sync tests untouched and GREEN).
- **`controllers/main.py`** — `POST /mail_management/folder/create
  {mailbox_id, name}`, `/folder/rename {folder_id, name}`,
  `/folder/delete {folder_id, force?}`, `/message/move {message_ids,
  folder_id}`, `/message/delete {message_ids}` (trash semantics
  server-side). All `auth='user'`, searched through `request.env`
  (ir.rules): cross-user mailbox/folder ids → UserError (not found);
  cross-user message ids silently ignored; cross-mailbox moves ignored
  (the destination's mailbox owns the move); `/message/delete` returns
  `{deleted, moved_to_trash, purged}`.
- **`security/ir.model.access.csv`** — REAL finding: the user group had
  `perm_unlink=0` on folder+message (deletes were manager-only under the
  prior change). PR-2b makes delete/move a per-user feature → user-level
  unlink granted for `mail.management.folder` +
  `mail.management.message`; owner-only isolation stays enforced by the
  per-user ir.rules.

### Frontend (OWL, no jQuery)

- **FolderTree**: "+" header button (`o_webmail_add_folder_btn`, disabled
  with no mailboxes) → FolderDialog; per-folder kebab menu
  (`o_webmail_folder_menu_btn`) with Rename/Delete items (menu is local
  presentational state; drag-free v1 by design).
- **FolderDialog** (new component + template): create/rename modes, mailbox
  select when several mailboxes exist (create mode), name input with the
  separator hint, confirm disabled on empty name; delegates via
  `onConfirm` (the parent owns the RPC). Odoo 19 `Dialog` with
  `t-set-slot="footer"`.
- **WebmailClient**: `moveTargets` (current mailbox folders minus the
  current folder) + `viewingTrash` getters; `createFolder`/`renameFolder`/
  `deleteFolder` (ConfirmationDialog WITH the message count before force;
  plain delete when the folder is empty), `moveMessages` (shared by bulk
  bar + reading pane), `deleteMessages`, `bulkMoveToFolder`,
  `moveOpenMessage`, `bulkDeleteMessages`/`deleteOpenMessage`
  (ConfirmationDialog "Delete permanently" when viewing Trash); every op
  refreshes the affected folders + message list through the existing flow.
- **MessageList** bulk bar: "Move to…" select (`o_webmail_bulk_move`,
  resets after the pick) + Delete button (`o_webmail_bulk_delete`; label
  switches to "Delete permanently" inside Trash).
- **ReadingPane**: "Move to…" select (`o_webmail_move_select`) + Delete
  button (`o_webmail_msg_delete`) in the action row.
- **Tour**: +4 deterministic PR-2b steps before the refresh step (dialog
  open → `fill Projects` → confirm → credential-error notification — the
  seeded credentialless mailbox fails IMAP-first with the same
  deterministic UserError the refresh step uses). Verified in REAL
  headless Chrome (TOUR SUCCEEDED).
- **scss**: folder row `position-relative` (dropdown anchor), menu
  z-index, count size. **Version** 19.0.6.0.0 → **19.0.7.0.0** (manifest +
  3 version-tracker tests updated).

## Real bugs found & fixed during GREEN/tour

1. **ACL**: user-level `perm_unlink=0` on folder+message made every
   user-driven delete/move fail with Access Denied (surfaced by
   `test_folder_delete_ok` through user A's env). Granted user unlink +
   ir.rule isolation — a scope-necessary security-surface change.
2. **Odoo 19 Dialog API**: `t-set-slot="buttons"` is invalid (slots are
   `default`/`footer`) — caught ONLY by the real-browser tour (source-level
   wiring tests cannot see OWL prop validation). Fixed to `footer`,
   matching ComposeDialog's established pattern.
3. **Odoo 19 tour helper**: `run: "text X"` was removed (renamed
   `fill`/`edit` in `tour_helpers_hoot.js`) — the tour failed at the
   typing step; fixed to `run: "fill Projects"`.
4. **Empty-uid `move_messages`** ran a pointless EXPUNGE that could purge
   UNRELATED pending `\Deleted` marks from other clients (triangulation
   caught it); now a full no-op.
5. Two RED-phase expectation mistakes corrected against correct behavior:
   a cross-user MOVE with an invisible DEST is a UserError (message-id
   ignoring requires the caller's OWN dest folder — the test now does
   exactly that), and the bulk-bar move select delegates through the local
   `bulkMove` handler.
6. My RED test-date f-string produced `2026-01-01 02 09:00:00`
   (non-ISO) — fixed with `to_datetime(...) + timedelta(hours=uid)`.

## Files changed (PR-2b)

| File | Status |
|------|--------|
| `tests/test_folder_management.py` | NEW (48 tests) |
| `tests/test_message_movement.py` | NEW (45 tests) |
| `lib/mail_imap_client.py` | +7 seams (~80 lines) |
| `models/mail_management.py` | +9 methods + `_sync_folder` extraction (~330 lines) |
| `controllers/main.py` | +5 routes (~130 lines) |
| `security/ir.model.access.csv` | folder+message user `perm_unlink` 0→1 |
| `static/src/components/folder_dialog.js` + `xml` | NEW (create/rename dialog) |
| `static/src/components/folder_tree.js` + `xml` | +add-folder button, kebab menu, count |
| `static/src/components/message_list.js` + `xml` | +bulk move select + delete (+props) |
| `static/src/components/reading_pane.js` + `xml` | +move select + delete (+props) |
| `static/src/webmail_client.js` + `xml` | +10 handlers, moveTargets/viewingTrash, dialog wiring |
| `static/src/scss/webmail.scss` | +menu positioning |
| `static/tests/tours/webmail_tour.js` | +4 PR-2b steps |
| `__manifest__.py` | version 19.0.7.0.0 + 2 new assets |
| `tests/__init__.py` | +2 imports |
| `tests/test_webmail_ui.py`, `tests/test_compose_send.py`, `tests/test_message_flags.py` | version trackers → 19.0.7.0.0 |

Estimated ~1,000 test + ~800 code/UI lines — over the 400-line review
budget like every PR in this chain (parent forecast accepted chained PRs;
reported honestly).

## Deviations / interpretation notes (PR-2b)

1. **Folder-aware seam signatures**: the task listed `copy_messages(uids,
   dest)` / `move_messages(uids, dest)` / `delete_messages(uids)`, but
   imap_tools operates on the SELECTED folder, so every seam takes the
   source folder name first (`copy_messages(src, uids, dest)` etc.).
   Documented in the seam comment; names and semantics otherwise exact.
2. **No-Trash fallback**: tasks.md defined trash routing and
   already-in-trash permanent delete; the no-trash-folder case was
   unspecified — implemented as PERMANENT delete (docstring + test).
3. **Same-folder move**: unspecified in the task; triangulation forced a
   no-op guard (COPY-to-self + EXPUNGE would churn server UIDs).
4. **Dest refresh**: implemented as a same-connection `_sync_folder` on
   the destination ("cheap"); its failure is best-effort AFTER the server
   move succeeded. Local destination records are never fabricated by the
   move itself.
5. **ACL change** (bug #1) is a real security-surface change within
   scope: per-user delete requires user-level unlink; owner-only
   isolation via the existing ir.rules is unchanged.
6. **UI verified headlessly**: source wiring assertions (7 tests) + a REAL
   headless-Chrome tour through the new dialog and its deterministic
   IMAP-first failure. The kebab-menu rename/delete paths and the Trash
   "Delete permanently" label are wiring-verified only (no browser click
   of the menu itself; the dialog flow itself is tour-proven).
7. **i18n**: new UI strings English-only (es_CL lands in PR-2d per plan).

## Test commands run (all real)

```bash
# canonical harness (final): 379 tests, EXIT=0
docker compose run --rm -T --no-deps --entrypoint odoo web \
  -d local_test --db_host=db -r odoo -w odoo -u mail_management \
  --test-enable --stop-after-init --test-tags=/mail_management

# browser tour (fresh DB): 380 tests incl. TOUR SUCCEEDED, 0 failed 0 error
docker compose exec -T db psql -U odoo -d postgres -c "DROP DATABASE IF EXISTS local_test;"
docker compose run --rm -T --no-deps --entrypoint odoo web \
  -d local_test --db_host=db -r odoo -w odoo -i mail_management \
  --test-enable --stop-after-init --test-tags=/mail_management \
  --workers 0 --db-filter='^local_test$'

# static gates
ruff check --no-cache lib/ models/ controllers/ tests/   # All checks passed!
node --check static/src/*.js static/src/components/*.js \
  static/tests/tours/webmail_tour.js                     # OK
python3 -c "xml.dom.minidom.parse(...)" per template     # OK
```

## PR boundary (PR-2b)

PR-2b complete: 3/3 tasks checked in tasks.md, canonical harness **379
GREEN** (286 baseline preserved + 93 new), fresh-install real-Chrome tour
**380 GREEN incl. TOUR SUCCEEDED**, ruff clean, all JS/XML well-formed,
no `t-raw=`, nothing staged, nothing committed (user owns commits).
**Next: PR-2c (signatures + rich text).**

---

## PR-2a history (message list power: sorting + quick filters + flags + bulk)

> **PR-2a.** Strict TDD mode active. All evidence from real docker harness
> runs. Baseline before this PR: 216 tests GREEN @ 19.0.5.0.0 (safety-net run
> re-verified at the start: `216 tests 4.84s`, exit 0).

---

## TDD Cycle Evidence (PR-2a)

| Task | Phase | Test file | Result | Docker evidence |
|------|-------|-----------|--------|-----------------|
| Safety net (before any edit) | BASELINE | whole suite | 216 tests, 0 failures | `odoo.tests.stats: mail_management: 216 tests 4.84s`, EXIT=0 |
| Flags RED (seam + model + controller + UI wiring) | RED | `tests/test_message_flags.py` (36 tests) | 31 FAIL/ERROR (of 64 new RED across both files) | EXIT=1; `AttributeError: 'mail.management.mailbox' object has no attribute '_set_message_flags'`; `AssertionError: Lists differ: [870, 869, 868, 867] != [869, 867, 870, 868]` (priority ignored); `280 tests 6.73s` (216 baseline still green) |
| Sorting RED (sort/priority/filter + UI wiring) | RED | `tests/test_list_sorting.py` (28 tests) | 33 FAIL/ERROR across both files | same run: EXIT=1, `280 tests 6.73s` |
| GREEN pass 1 | GREEN | both files | 2 failures left (nested-method mangling + version tracker) | EXIT=1 |
| GREEN pass 2–3 (indent-nesting repairs, expectation fixes, `original_routing`, ciphertext clear) | GREEN | both files | 0 failures | `odoo.tests.stats: mail_management: 280 tests 8.76s`, EXIT=0 |
| TRIANGULATE (+6 tests: bulk mixed flags over many messages, star/unstar round-trip, cross-folder bulk via controller, filter+limit/offset, reversed priority order, priority+asc sort) | TRIANGULATE | both files | 1 expectation corrected (chained priority semantics), then 0 failures | `286 tests 6.90s`, EXIT=0 |
| REFACTOR (`markUnread(message)` symmetry; verified pure `_build_message_order` + presentational component helpers) | REFACTOR | whole suite | 0 failures | `286 tests 6.79s`, EXIT=0 |
| Fresh-install + real headless-Chrome tour (DB dropped/recreated) | UI/E2E | `tests/test_webmail_tour.py` (HttpCase) | tour SUCCEEDED incl. new PR-2a steps | `-i … --workers 0 --db-filter='^local_test$'`: `287 tests 8.97s`, EXIT=0; log shows `[17..19/21] bulk bar → mark unread → badge recomputes to 2`, `║ TOUR mail_management_webmail_tour SUCCEEDED ║` |
| Final canonical `-u` run (post-refactor, post-stash-verify) | FINAL | whole suite | 0 failures | `286 tests 7.27s`, EXIT=0 |

Infra note: one TRIANGULATE run aborted with `ERROR: server closed the
connection unexpectedly` — the postgres container received an external fast
shutdown at that moment (db logs: `received fast shutdown request` + restart
< 1 s). Not a code failure; re-run was green.

### Test Summary

- **Total tests written (this PR)**: 64 (37 in `test_message_flags.py` incl. 5
  seam + 9 model + 10 controller + 3 triangulate + 5 UI-wiring; 27 in
  `test_list_sorting.py` incl. 23 controller + 4 UI-wiring + 3 triangulate)
- **Total tests passing**: 286/286 canonical (287 with the browser tour)
- **Layers used**: Unit/Integration (TransactionCase, mocked transports) 63,
  E2E (headless Chrome tour) 1
- **Approval tests**: none — no refactoring of pre-existing behavior (legacy
  `order` param contract kept; tour selectors preserved)
- **Pure functions created**: `_build_message_order` (controller module
  level), `MessageList.sortDirection/arrow/isFilterActive/isSelected`

## What was built (PR-2a)

### Backend

- **`lib/mail_imap_client.py`** — `ImapClient.set_flags(folder_imap_name,
  uids, seen=None, flagged=None)`: one imap_tools `flag(uid_list, flag_set,
  value)` call per `(flag_set, value)` pair (mixed seen=True + flagged=False →
  2 STOREs); no-op on empty uids / all-None; selects the folder first.
  Mockable like every seam method (tests patch `MailBox` at lib level).
- **`models/mail_management.py`** — `mailbox._set_message_flags(message_ids,
  flags)` implementing the tasks.md POLICY: (1) validate flags ⊆
  `{'seen','flagged'}` (else `UserError`, nothing written, no mirror);
  (2) filter ids to THIS mailbox's `synced_message_ids` (cross-mailbox ids
  ignored); (3) write LOCAL `flag_seen`/`flag_flagged` FIRST (stored
  `folder.unread_count` recomputes via its existing depends); (4) best-effort
  IMAP mirror through the seam grouped per folder (sorted uids) — any
  failure (incl. missing credential) is logged with the mailbox id +
  redaction and NEVER raised.
- **`controllers/main.py`**:
  - `POST /mail_management/message/set_flags` (`auth='user'`, jsonrpc):
    `message_ids` + `flags` → search through `request.env` (ir.rules), group
    by mailbox, delegate to `_set_message_flags`; returns
    `{'success': True, 'updated': N}`; cross-user ids resolve to nothing →
    ignored (proved by test).
  - `/mail_management/messages` extended with WHITELISTED params:
    `sort ∈ {date, date_desc, from, from_desc, subject, subject_desc}` (→
    ORM order on `date`/`email_from`/`subject`), `priority` (str or list) ⊆
    `{unread_first, flagged_first}` (clauses PREPENDED to the primary sort —
    priority groups rise to the top; both may combine in caller order),
    `filter ∈ {unread, flagged, attachments}` (domain extension). Invalid
    values fall back to the safe default; nothing raw ever reaches
    `order`/domain (injection tests included). Legacy `order` param kept
    working; a valid `sort` wins over it. Deterministic `id desc` tiebreak.
  - `/mail_management/message_body` now also returns `flag_flagged` (the
    reading-pane star reflects server state).

### Frontend (OWL, no jQuery)

- **MessageList**: clickable column headers (From/Subject/Date) with ▲/▼
  indicator (`data-sort-field`), "Unread first"/"Starred first" toggle
  buttons (`data-priority`), quick-filter chips All | Unread | Starred |
  Attachments (`data-filter`, single-select, All default), per-row star
  toggle (instant optimistic flip), per-row checkboxes + header select-all,
  bulk action bar (Mark read / Mark unread / Star / Unstar) shown when >0
  selected. All state lives in `WebmailClient`; the component stays
  presentational (all actions via `on*` props).
- **ReadingPane**: star toggle + "Mark as unread" button (delegating to the
  parent handlers).
- **WebmailClient**: `messageSort` / `messagePriority` / `messageFilter` /
  `selectedIds` state; `setSortBy` (toggle asc/desc per column),
  `togglePriority`, `setFilter`, `toggleStar` (optimistic + revert on RPC
  error, syncs the reading pane), `markUnread`, `toggleSelect`,
  `toggleSelectAll`, `bulkSetFlags` (then reloads folders for unread badges
  - the list rows); RPC params flow through the existing `rpc` layer only.
- **Tour** (`webmail_tour.js`): +5 cheap PR-2a steps (star → solid star,
  select-all, bulk bar visible, bulk mark unread, unread badge recomputes
  to 2) — verified in real headless Chrome. Requires the documented
  `--workers 0 --db-filter='^local_test$'` run (canonical workers=4 run
  skips HttpCase by design, established PR-1b-ii pattern).

### Versioning

- `__manifest__.py` 19.0.5.0.0 → **19.0.6.0.0**; both version-tracking tests
  updated (`test_webmail_ui.py`, `test_compose_send.py::test_manifest_version_bumped_5`).

## Real bugs found & fixed during GREEN

1. **Indentation mangling on multi-edit inserts (recurring)**: the first
   GREEN run showed `'MailManagementController' object has no attribute
   'get_messages'` / `'ImapClient' object has no attribute 'set_flags'` /
   `_set_message_flags` nested inside `_parse_raw_message` — my inserted
   blocks landed one indent level deep, becoming dead nested functions
   (legal Python, ruff-silent). Same bug class the PR-1c log documented.
   Fixed by AST-verified dedents; final class-body listings re-checked.
2. **Missing `</t>` in `message_list.xml`** — would have broken the whole
   backend asset bundle; caught by a local XML well-formedness check before
   the tour run (not by Python tests).
3. Two RED-phase expectation mistakes corrected against correct behavior:
   quick filters keep the DEFAULT sort (date desc), and chained priorities
   apply in caller order inside every group (`flagged_first, unread_first`
   → unread still rises inside the unflagged group).

## Files changed (PR-2a)

| File | Status |
|------|--------|
| `tests/test_message_flags.py` | NEW (37 tests) |
| `tests/test_list_sorting.py` | NEW (27 tests) |
| `lib/mail_imap_client.py` | +`MailMessageFlags` import, +`set_flags` seam (~35 lines) |
| `models/mail_management.py` | +`_settable_flags`, +`_set_message_flags` (~85 lines) |
| `controllers/main.py` | +3 whitelists, +`_build_message_order`, extended `get_messages`, +`message_set_flags` route, +`flag_flagged` payload (~120 lines) |
| `static/src/components/message_list.js` | REWRITTEN (sort/priority/filter/selection props + helpers, ~145 lines) |
| `static/src/xml/message_list.xml` | REWRITTEN (headers, chips, bulk bar, star, checkboxes, ~165 lines) |
| `static/src/components/reading_pane.js` | +`onToggleStar`/`onMarkUnread` props + delegating methods |
| `static/src/xml/reading_pane.xml` | +star button, +mark-as-unread button |
| `static/src/webmail_client.js` | +4 state keys, +9 handlers, param'd `loadMessages` |
| `static/src/xml/webmail_client.xml` | new MessageList/ReadingPane prop bindings |
| `static/src/scss/webmail.scss` | +chips/bulk bar/columns/star styles |
| `static/tests/tours/webmail_tour.js` | +5 PR-2a steps |
| `__manifest__.py` | version 19.0.6.0.0 |
| `tests/__init__.py` | +2 imports |
| `tests/test_webmail_ui.py`, `tests/test_compose_send.py` | version trackers → 19.0.6.0.0 |

Estimated ~1,050 new test + ~600 code/UI lines — over the 400-line review
budget like every PR in this chain (parent forecast accepted chained PRs;
reported honestly).

## Deviations / interpretation notes

1. **Priority "secondary ordering" interpretation**: the parent phrasing
   said priority is "applied as secondary ordering"; implemented as priority
   clauses PREPENDED to the primary sort (priority groups rise to the top,
   the chosen sort orders within groups). A tiebreaker-only reading would
   make the toggles inert under `date` sorting (no ties) — implemented the
   reading that matches the described UI and every real webmail. Caller's
   list order is respected (test-documented).
2. **Legacy endpoints kept**: single-message `/mail_management/set_flags`
   (PR-1b-ii) and the `order` param on `/messages` remain (no regression);
   the new bulk endpoint is `/mail_management/message/set_flags` per scope.
3. **UI verified headlessly**: template/JS wiring assertions (5 tests) +
   real headless-Chrome tour (5 new steps, incl. optimistic star, bulk bar,
   unread-badge recompute through the full RPC loop). Interactive
   human-flavored checks (chip aesthetics, keyboard nav) not verified.
4. **i18n**: new UI strings are English-only, no PO entries touched (per
   parent instruction; es_CL lands in PR-2d).
5. This change has no separate `design.md`/`specs/` — the parent scoped
   PR-2a from `proposal.md` + `tasks.md` (flag POLICY included) + the prior
   change's design as the standing architecture reference.

## Remaining tasks (unchecked `- [ ]` in tasks.md)

- PR-2c: 3 tasks (signatures + rich text)
- PR-2d: 1 task (search + mailbox mgmt + es_CL PO)
- No parent-owned (`<!-- sdd-owner: parent -->`) rows exist in this
  tasks.md; bounded review of PR-2a is a parent-lifecycle action.

## Structured status consumed / produced

- Consumed: parent-assigned PR-2a slice in the stacked chain (delivery path
  resolved by parent: chained PRs, auto-forecast); repo-local edit scope;
  strict TDD active (global support file read; project has no override).
- actionContext warnings: none; all edits inside the module + tests.
- Produced: PR-2a `all_done` (5/5 implementation tasks checked);
  `nextRecommended: parent-lifecycle` (bounded review of PR-2a, then PR-2b).

## Test commands run (all real)

```bash
# canonical harness (final): 286 tests, EXIT=0
docker compose run --rm -T --no-deps --entrypoint odoo web \
  -d local_test --db_host=db -r odoo -w odoo -u mail_management \
  --test-enable --stop-after-init --test-tags=/mail_management

# browser tour (fresh DB): 287 tests incl. TOUR SUCCEEDED, EXIT=0
docker compose exec -T db psql -U odoo -d postgres -c "DROP DATABASE IF EXISTS local_test;"
docker compose run --rm -T --no-deps --entrypoint odoo web \
  -d local_test --db_host=db -r odoo -w odoo -i mail_management \
  --test-enable --stop-after-init --test-tags=/mail_management \
  --workers 0 --db-filter='^local_test$'

# static gates
ruff check --no-cache lib/ models/ controllers/ tests/test_message_flags.py \
  tests/test_list_sorting.py tests/__init__.py  # All checks passed!
node --check static/src/*.js static/src/components/*.js  # OK
python3 -c "xml.dom.minidom.parse(...)" per template      # OK
```

## PR boundary

PR-2a is complete: 5/5 tasks checked in tasks.md, canonical harness
**286 GREEN** (baseline 216 preserved + 70 new incl. tour-specific wiring),
fresh-install real-Chrome tour **287 GREEN**, ruff clean on all changed
files (pre-existing `__init__.py` F401s untouched at baseline), no
`t-raw=`, nothing staged, nothing committed (user owns commits).
**Next: PR-2b (folders & movement).**
