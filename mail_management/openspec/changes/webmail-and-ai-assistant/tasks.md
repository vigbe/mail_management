# Tasks — `webmail-and-ai-assistant` (Slice 1 — Webmail core)

> **SDD phase: TASKS.** Ordered, checkable, strict-TDD implementation plan for **Slice 1
> (Webmail core)**, organized into the chained PRs the design defined. This document feeds
> `sdd-apply`; it contains **no code**, only concrete, verifiable tasks. The first thing
> `sdd-apply` executes is **PR-1a**.
>
> **Skill resolution: `paths-injected`** — `~/.config/opencode/skills/odoo-19/SKILL.md` plus
> the testing / transaction / decorator / security / development guides were read directly
> before finalizing task wording. No registry/fallback discovery was used.

---

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~3,000–3,900 across the whole Slice-1 chain (sum of all PRs) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR-1a-1 → PR-1a-2 → PR-1b-i → PR-1b-ii → PR-1c → PR-1d ∥ PR-1e |
| Delivery strategy | ask-on-risk |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High
```

> **Why "ask-on-risk".** Several PRs (PR-1a, PR-1b-i, PR-1b-ii) are each forecast **over the
> 400-line budget**. The security-critical foundation (PR-1a crypto + isolation) is **cohesive
> and should not be split dangerously** merely to hit 400; a size exception there is safer than
> a mid-security review break. The user must confirm the split points / size exceptions before
> `sdd-apply` runs (see §Review Workload Guard).

---

## Conventions (apply to every PR)

- **Canonical headless test command** (from `openspec/config.yaml` — verified working this
  session; `strict_tdd: true`). Install + test in a fresh one-shot container on the throwaway
  `local_test` DB:

  ```bash
  docker compose run --rm -T --no-deps --entrypoint odoo web \
    -d local_test --db_host=db -r odoo -w odoo \
    -i mail_management --test-enable --stop-after-init \
    --test-tags=/mail_management
  ```

  Re-run after install (faster, uses `-u`):

  ```bash
  docker compose run --rm -T --no-deps --entrypoint odoo web \
    -d local_test --db_host=db -r odoo -w odoo \
    -u mail_management --test-enable --stop-after-init \
    --test-tags=/mail_management
  ```

  All RED→GREEN evidence is captured from this command's stdout (Odoo per-test
  PASS/FAIL/ERROR + summary). **Never fabricate results that were not actually run.**
- **Strict TDD:** for every non-trivial implementation task, write the **failing test first
  (RED)**, then implement to **GREEN**. Tasks below are paired RED→GREEN. Trivial glue edits
  (manifest, pip line, CSV rows) carry no dedicated RED.
- **Odoo-19-correct patterns** (enforced everywhere, per the skill guides):
  - `models.Constraint` (NOT `_sql_constraints`) and `models.Index`.
  - `@api.ondelete(at_uninstall=False)` for delete guards (NOT `unlink()` overrides).
  - `@api.model_create_multi` for `create`; `@api.depends` on computed/inverse fields;
    `@api.private` for `_get_credential_plaintext()`.
  - `res.groups.privilege` bridge (already migrated — verify only); `list` views (not `tree`);
    `Command`; `t-out` (never `t-raw`).
- **Mocking seam:** the `lib/` package is Odoo-model-free; tests mock
  `imap-tools` / `smtplib` / OAuth transports at this seam — **no real network, no real
  provider app** in any test.
- **Ownership marker:** every checkbox ends with exactly one marker. `implementation` =
  code/tests/apply-owned verification; `parent` = post-apply bounded review / lifecycle gate.

---

## PR-1a — Foundation (models + Fernet helper + generic IMAP/SMTP + isolation)

- **Goal:** Replace the placeholder `mail.management` scaffold with the security-critical
  foundation: the `mail.management.mailbox` model, Fernet encrypt-at-rest via a model-free
  `lib/mail_crypto.py`, per-user isolation via two group-scoped `ir.rule`s, a mockable generic
  IMAP/SMTP client seam (`lib/`), `imap-tools` install, and the manifest/pip wiring. After
  PR-1a the module installs clean, credentials are ciphertext at rest, isolation is airtight,
  and connect/send is exercisable (mocked).
- **Satisfies spec requirements:** Per-user mailbox configuration; Mailbox connection test;
  Credential encryption at rest (CRITICAL); Master key management; Credential field access
  control; Per-user privacy and isolation (CRITICAL); Odoo 19 privilege-based security groups;
  Generic IMAP/SMTP provider works end-to-end first; Uniqueness and data-integrity constraints
  (mailbox part); Provider testability with mocked transports (client part).
- **Satisfies design sections:** A.1, A.5, B (B.1–B.5), I, J (scaffold removal).
- **Estimated changed lines:** ~850–1,100 (additions + deletions incl. tests + scaffold
  removal). **Over 400 → see Review Workload Guard for the recommended 1a-1 / 1a-2 split.**

### PR-1a — Foundation: tasks

**0. Dependency + manifest wiring (the import-resolution gate; do this FIRST).**

- [x] Add `imap-tools` to the docker-compose web `entrypoint` pip line so `import imap_tools`
      resolves at module load. Current line (`../../docker-compose.yml:11`):
      `pip3 install --user pyjwt python-docx pdf2docx --break-system-packages && /entrypoint.sh odoo`
      → append `imap-tools` (keep `--break-system-packages`). Rebuild/restart web to confirm
      `import imap_tools` succeeds inside the container. <!-- sdd-owner: implementation -->
- [x] Edit `__manifest__.py`: bump `version` `19.0.1.0.0` → `19.0.2.0.0`; set
      `external_dependencies.python` to `['imap-tools', 'cryptography']`; add the new `data`
      entries (`security/security_groups.xml` already present, add `data/` files + views) and
      keep `depends = [base, mail]`. (Register `lib/` is NOT needed in manifest — it is an
      imported package, not an Odoo data file.) <!-- sdd-owner: implementation -->

**1. Fernet crypto helper — `lib/mail_crypto.py` + `lib/__init__.py` (model-free, unit-testable).**

- [x] RED: Create `tests/test_mail_crypto.py` (`TransactionCase`, no network needed). Assert:
      (a) `encrypt`→`decrypt` round-trip returns the original plaintext; (b) ciphertext ≠
      plaintext and does not contain the plaintext substring; (c) `get_master_key` priority
      env > icp > autogen (inject fake `get_param`/`set_param` + `monkeypatch.setenv`); (d)
      autogen path persists to the injected `set_param`; (e) decrypt under a different/missing
      key raises `MailCryptoError` with a **clear, non-redacted-by-credential** message; (f)
      `redact(value, secret)` replaces each secret with `***REDACTED***`; (g) the resolved key
      never appears in any output. Run the harness → RED. <!-- sdd-owner: implementation -->
- [x] GREEN: Create `lib/__init__.py` exporting `FernetCrypto`, `MailCryptoError` (later PRs add
      `ImapClient`, `SmtpClient`, `OAuthProvider`). Create `lib/mail_crypto.py` with
      `FernetCrypto` (`get_master_key(get_param, set_param)`, `encrypt`, `decrypt`, `redact`,
      a lazy `fernet(...)` accessor) using `cryptography.fernet.Fernet`. Master-key constants
      `MAIL_MGMT_MASTER_KEY` (env) / `mail_management.master_key` (icp). Bootstrap auto-generate
      emits one `_logger.warning` that does **not** contain the key. Run the harness → GREEN.
      <!-- sdd-owner: implementation -->

**2. Replace the scaffold model.**

- [x] Delete `models/mail_management.py` (the placeholder `mail.management` FSM) and remove its
      import from `models/__init__.py`. Remove the corresponding ACL rows + view entries for the
      old model. Safe because the module is installed only in throwaway `local_test`
      (verified). <!-- sdd-owner: implementation -->

**3. `mail.management.mailbox` model — `models/mail_management_mailbox.py` (design A.1).**

- [x] RED: Create `tests/test_mailbox_model.py` (`TransactionCase`). Assert:
      (a) `user_id` defaults to the current user on create; (b) duplicate
      `(user_id, imap_host, imap_user)` is rejected by the `UNIQUE(user_id, imap_host,
      imap_user)` `models.Constraint`; (c) writing a plaintext `credential` persists **only
      ciphertext** — read the raw column via `env.cr.execute` and assert it does not contain
      the plaintext and is valid Fernet ciphertext; (d) reads of `credential` return the
      constant mask, never the decrypted value; (e) `credential_encrypted` is `copy=False`
      (a duplicated mailbox carries no secret); (f) `provider_type` defaults to `generic`.
      Run the harness → RED. <!-- sdd-owner: implementation -->
- [x] GREEN: Create `models/mail_management_mailbox.py` with all A.1 fields (`name`, `user_id`,
      `company_id` `check_company=True`, `provider_type`, imap_*/ smtp_* / `from_email`,
      transient `credential` (compute+inverse, `password=True`, NOT stored), `credential_encrypted`
      (`password=True`, `copy=False`, `groups=group_mail_management_manager`), `sent_folder_name`,
      `last_sync`, `state`, `sync_error`, `active`, `folder_ids`). Implement:
      `models.Constraint('UNIQUE(user_id, imap_host, imap_user)')` +
      `models.Index('(user_id, company_id)')`; `_valid_field_parameter` whitelisting
      `password=True` on `credential`/`credential_encrypted`; `_compute_credential` (constant
      mask, never decrypts) + `_inverse_credential` (encrypts, writes via `sudo()`, drops
      plaintext from the in-memory record); `@api.model_create_multi create` (defaults + encrypt
      any plaintext `credential`); `@api.ondelete(at_uninstall=False)` blocking delete while
      `state == 'active'`; `@api.private _get_credential_plaintext()` (`sudo()`-reads ciphertext,
      `FernetCrypto.decrypt`, returns plaintext to a local var only). Run → GREEN.
      <!-- sdd-owner: implementation -->

**4. Per-user isolation — `ir.rule`s + `ir.model.access.csv` (design B.3, B.4).**

- [x] Verify `security/security_groups.xml` already uses the `res.groups.privilege` bridge and
      contains NO removed `category_id` (spec: privilege-bridge requirement). This is a
      read-only verification; PR-1a adds nothing to the bridge itself. <!-- sdd-owner: implementation -->
- [x] RED: Create `tests/test_isolation.py` (`TransactionCase`). Create two `res.users`
      (`@tagged('standard')`), each granted `group_mail_management_user`; each owns a mailbox +
      (for later PRs) folder/message/attachment. Assert: (a) as user A, `search([])` returns
      only A's mailbox; (b) user A cannot read user B's mailbox by id → empty/`AccessError`;
      (c) a user who is in BOTH groups (manager) sees all mailboxes; (d) `credential_encrypted`
      is removed from `fields_get()`/read for a non-manager. Run → RED. <!-- sdd-owner: implementation -->
- [x] GREEN: Add to `security/security_groups.xml` **two group-scoped ir.rules** for
      `mail.management.mailbox` (NOT a global own-only rule): `rule_mailbox_user_own`
      `domain_force=[('user_id','=',user.id)]` group=`group_mail_management_user`, and
      `rule_mailbox_manager_all` `domain_force=[(1,'=',1)]` group=`group_mail_management_manager`
      (design B.3 — manager implied into user group → `own OR all = all`). Update
      `security/ir.model.access.csv` rows: replace the old `mail.management` rows with
      `mail.management.mailbox` rows (user = read/write/create; manager = full). Run → GREEN.
      <!-- sdd-owner: implementation -->

**5. Generic IMAP/SMTP client seam — `lib/mail_imap_client.py`, `lib/mail_smtp_client.py`.**

- [x] RED: Create `tests/test_connect_send.py` (`TransactionCase`) with **mocked** transports
      (`unittest.mock.patch` the `imap-tools` / `smtplib` entry points at the `lib` seam — no
      network). Assert: (a) `ImapClient(mb, plain)` context manager connects + logs in with the
      in-memory plaintext and closes cleanly; (b) `SmtpClient(mb, plain)` connects + STARTTLS +
      AUTH + sends an `EmailMessage`; (c) `action_test_connection()` returns success when both
      mocks succeed and a **redacted** failure (no plaintext) when login fails; (d) the plaintext
      credential never appears in the result/log strings. Run → RED. <!-- sdd-owner: implementation -->
- [x] GREEN: Implement `lib/mail_imap_client.py` (`ImapClient` context manager over
      `imap_tools.MailBox`/`MailBoxUnencrypted`: connect, `search_uids`, `fetch`, `uidvalidity`,
      `folder_list`, `store`, `append`; configurable socket timeout from
      `mail_management.imap_timeout`) and `lib/mail_smtp_client.py` (`SmtpClient` over stdlib
      `smtplib`+`email.message.EmailMessage`: STARTTLS/SSL per `smtp_use_tls`, timeout-guarded).
      Export both from `lib/__init__.py`. Implement `action_test_connection(self)` on the
      mailbox: decrypt in-memory via `_get_credential_plaintext()`, try IMAP+SMTP connect,
      return `{success: bool, message: redacted}`. Run → GREEN. <!-- sdd-owner: implementation -->

**6. Mailbox config view + manifest data list.**

- [x] Rewrite `views/mail_management_views.xml`: a `mail.management.mailbox` config **list** +
      **form** view (secret fields masked via `password=True` widget / `groups`-hidden
      `credential_encrypted`), and a menu/action to open it. No OWL client action yet (that is
      PR-1b-ii). <!-- sdd-owner: implementation -->
- [x] Update `__manifest__.py` `data` list to load the new security rules, access CSV, and
      views; ensure `models/__init__.py` imports `mail_management_mailbox`. <!-- sdd-owner: implementation -->
- [x] TRIANGULATE: re-run the full harness with `-i` (fresh install) AND `-u` (update) on
      `local_test`; confirm all PR-1a tests GREEN, no lint errors (`ruff`), module installs
      clean, version reports `19.0.2.0.0`. <!-- sdd-owner: implementation -->

### PR-1a — Definition of done

- Module installs clean on `local_test`; all PR-1a tests GREEN via the docker harness
  (`-i` and `-u`); `ruff` clean; `imap_tools` importable in the container; the stored credential
  column is Fernet ciphertext (asserted by raw-column read test); two users are isolated; the
  module reports `19.0.2.0.0`.
- [ ] Start or reuse bounded review. <!-- sdd-owner: parent -->

---

## PR-1b-i — Read webmail data layer (models + cron sync + read controllers, NO UI)

- **Goal:** The data + sync + controller layer under the read UI: `mail.management.folder`,
  `.message`, `.attachment`, the cron-pull sync (UID-incremental, `UNIQUE(folder_id,uid)` +
  `message_id` upsert, UIDVALIDITY recovery), and the JSON read endpoints. Independently
  testable (mocked IMAP) before any UI lands.
- **Depends on:** PR-1a (mailbox model, crypto, isolation, IMAP client seam).
- **Satisfies spec requirements:** Folder discovery; Sync backbone — cron-pull; Sync
  idempotency; UIDVALIDITY change recovery; Worker-safe sync; On-demand folder refresh;
  Reading a message; Untrusted content is sanitised on render (sanitize-at-sync part);
  Attachments on read; Uniqueness and data-integrity constraints (message/folder part).
- **Satisfies design sections:** A.2, A.3 (incl. A.3.1 HIGH-RISK), A.4, C (C.1–C.3), F (read
  endpoints), H (test plan: imap_sync, sanitize, controllers).
- **Estimated changed lines:** ~700–900. **Over 400 → HIGH risk (flagged in Review Workload Guard).**

### PR-1b-i — tasks

- [x] RED: Create `tests/test_imap_sync.py` (mocked `ImapClient`) covering: incremental fetch
      (only `> last_uid`); re-sync creates no duplicate; concurrent insert of the same
      `(folder_id, uid)` → `UniqueViolation` treated as idempotent no-op (per transaction guide);
      UIDVALIDITY change → `last_uid` reset + re-fetch + `message_id` upsert preserving user
      `\Flagged`; sanitized body stored; flags updated; `cron_batch_size` bounds a tick.
      <!-- sdd-owner: implementation -->
- [x] GREEN: Create `models/mail_management_folder.py` (A.2: `mailbox_id`, stored `user_id`
      related, `imap_name`, `role`, `uidvalidity` (Char), `last_uid`, counts compute+store;
      `models.Constraint('UNIQUE(mailbox_id, imap_name)')`, `models.Index('(mailbox_id, role)')`).
      Extend isolation ir.rules + access CSV to the folder model (same two-rule pattern, domain
      on stored `user_id`). <!-- sdd-owner: implementation -->
- [x] GREEN: Create `models/mail_management_message.py` (A.3: `mailbox_id`, `folder_id`, stored
      `user_id`, `uid`, `message_id`, `subject`, `email_from`/`email_to`/`email_cc`, `date`,
      `body_html` (Html `sanitize=True`), `body_text`, flags (`flag_seen`/`flag_answered`/
      `flag_flagged`/`flag_deleted`), `in_reply_to`/`references`, `parent_id` compute, counts;
      **HIGH-RISK** `models.Constraint('UNIQUE(folder_id, uid)')` + indexes; `_upsert_message`
      message_id-first dedup). Extend ir.rules + access CSV to the message model.
      <!-- sdd-owner: implementation -->
- [x] GREEN: Create `models/mail_management_attachment.py` (A.4: thin pointer to
      `ir.attachment` filestore; `ir_attachment_id` `ondelete='restrict'`; `content_id`, `inline`;
      `models.Index('(webmail_message_id, content_id)')`). Extend ir.rules + access CSV.
      <!-- sdd-owner: implementation -->
- [x] RED: Create `tests/test_sanitize.py`: `<script>` stripped by `odoo.tools.html_sanitize`;
      `multipart/alternative` → HTML part stored; bodies stored sanitized **before** persist.
      <!-- sdd-owner: implementation -->
- [x] GREEN: Implement sync on the mailbox: `_cron_sync_mailboxes(self, *, limit=None)`
      (design C.2 — bounded `cron_batch_size`, `ir.cron._commit_progress`, per-mailbox
      try/except + savepoint, redacted `sync_error`), `_sync_mailbox(self)` (C.3 — credential in
      memory, per-folder UIDVALIDITY-aware fetch, `html_sanitize` before store, `last_uid`
      advance), and `action_discover_folders` (SPECIAL-USE role match, casing-tolerant). Create
      `data/mail_management_cron.xml` (`ir.cron` `cron_sync_mailboxes`, ~5 min, code
      `model._cron_sync_mailboxes()`). <!-- sdd-owner: implementation -->
- [x] RED: Create `tests/test_controllers.py` (`HttpCase`/controller `TransactionCase`):
      `/mail_management/folders`, `/messages`, `/message_body`, `/refresh`, `/set_flags`,
      `/attachment/<id>` return only the caller's records; a forged cross-user id → empty/`AccessError`;
      never leak `credential*` fields. <!-- sdd-owner: implementation -->
- [x] GREEN: Implement the read JSON-RPC + http endpoints in `controllers/main.py`
      (`auth='user'`, operate through `request.env` so ir.rules apply, assert
      `record.user_id == request.env.user` before mutating). <!-- sdd-owner: implementation -->
- [x] TRIANGULATE: full harness `-i` + `-u` on `local_test`; all PR-1a + PR-1b-i tests GREEN;
      `ruff` clean; no DB blobs (attachments resolve through `ir.attachment`/filestore).
      <!-- sdd-owner: implementation -->

### PR-1b-i — Definition of done

- Folders/messages/attachments models + cron + read controllers pass mocked-IMAP tests; sync is
  idempotent and UIDVALIDITY-recoverable; isolation extended to all child models; bodies
  sanitized at sync time; module still installs clean.
- [ ] Start or reuse bounded review. <!-- sdd-owner: parent -->

---

## PR-1b-ii — Read-only OWL 3-pane UI

- **Goal:** The read surface: `WebmailClient` shell + `FolderTree` + `MessageList` +
  `ReadingPane` (sanitized body via `t-out`, attachments, raw-header toggle), the assets bundle,
  and the client action/menu. No compose yet.
- **Depends on:** PR-1b-i (read controllers + models).
- **Satisfies spec requirements:** Three-pane OWL webmail UI (read part); Untrusted content is
  sanitised on render (render-via-`t-out`, no `t-raw`); reading-pane scenarios.
- **Satisfies design sections:** G.1 (read components), F (consumes read endpoints).
- **Estimated changed lines:** ~500–700. **Over 400 → HIGH risk (design already flagged this
  surface as at-risk; see Review Workload Guard for a further UI split).**

### PR-1b-ii — tasks

- [x] RED: Create `tests/test_webmail_tour.py` (`HttpCase`, `start_tour`) covering: open the
      Webmail client action → 3 panes render; folder tree shows Inbox/Sent/Trash with unread
      badges; message list sortable by date; reading pane renders a sanitized body (no
      `<script>`) and lists attachments. Seed a synced mailbox/message in setUp. <!-- sdd-owner: implementation -->
- [x] GREEN: Create the OWL client under `static/src/` (`webmail_client.js` registry action
      `mail_management.webmail` + `components/` FolderTree/MessageList/ReadingPane + QWeb
      `xml/*.xml` with **`t-out` only**; consume the PR-1b-i JSON controllers; block remote
      images, resolve CID inline images). <!-- sdd-owner: implementation -->
- [x] GREEN: Declare the `assets` bundle in `__manifest__.py` (`web.assets_backend`) and add a
      client action + menu in `views/`. <!-- sdd-owner: implementation -->
- [x] TRIANGULATE: harness `-u` + the tour GREEN; confirm no `t-raw` anywhere in
      `static/src/xml/` (grep guard); `ruff` clean. <!-- sdd-owner: implementation -->

### PR-1b-ii — Definition of done

- 3-pane read UI renders from the controllers; bodies sanitized + `t-out`; unread badges + date
  sort; tour GREEN; module installs clean.
- [ ] Start or reuse bounded review. <!-- sdd-owner: parent -->

---

## PR-1c — Compose: reply / reply-all / forward + attachments + SMTP send + APPEND

- **Goal:** Plain-text compose (`ComposeDialog`), `/mail_management/send` controller, the
  `lib/mail_smtp_client.py` send path, Sent-folder APPEND, `\Answered` mirroring, and
  send-attachments via `ir.attachment`/filestore.
- **Depends on:** PR-1b-ii (the UI shell the compose dialog attaches to). *(PR-1c's data layer —
  `_send_message` on the mailbox — depends logically on PR-1a's client seam; the UI integration
  depends on PR-1b-ii.)*
- **Satisfies spec requirements:** Compose — reply/reply-all/forward; Threading headers on send;
  Sent-folder APPEND; Attachments on send.
- **Satisfies design sections:** D (D.1), G.1 (compose), F (`/send`), H (test plan: send).
- **Estimated changed lines:** ~400–550. Medium–High.

### PR-1c — tasks

- [x] RED: Create `tests/test_send.py` (mocked `SmtpClient` + `ImapClient.append/store`)
      covering: reply sets `In-Reply-To` + `References` from the original `message_id`; forward
      creates a fresh `Message-ID`; SMTP sends via mailbox creds; APPEND to `\Sent` (or
      `sent_folder_name`); APPEND failure after SMTP success is **surfaced, not swallowed**
      (never claims send failed); `\Answered` mirrored locally + via STORE; attachments
      MIME-attached from filestore. <!-- sdd-owner: implementation -->
      *(applied as `tests/test_compose_send.py` — same scope + controller isolation/
      redaction + UI-wiring + seam tests)*
- [x] GREEN: Implement `mailbox._send_message(...)` (design D.1 — build `EmailMessage`, decrypt
      in-memory, `SmtpClient.sendmail`, APPEND sent copy with APPEND-failure handling, STORE
      `\Answered`) + the `/mail_management/send` controller (`auth='user'`, owner-checked).
      <!-- sdd-owner: implementation -->
- [x] GREEN: Add the plain-text `ComposeDialog` OWL component (reply/reply-all/forward, file
      upload → `ir.attachment` → MIME-attach) wired to `/send`. <!-- sdd-owner: implementation -->
- [x] TRIANGULATE: harness `-u` + send tests GREEN; `ruff` clean. <!-- sdd-owner: implementation -->

### PR-1c — Definition of done

- Reply/forward/attachments end-to-end (mocked transports) with correct threading + APPEND +
  `\Answered`; module installs clean.
- [ ] Start or reuse bounded review. <!-- sdd-owner: parent -->

---

## PR-1d — Gmail / Google Workspace OAuth2 (XOAUTH2)

- **Goal:** The OAuth2 abstraction (`lib/oauth_provider.py`) + Gmail config, mailbox OAuth
  fields/interpretation, the callback controller, refresh-before-connect, XOAUTH2 SASL. Testable
  fully with **mocked** OAuth; the real Gmail flow is gated by non-code **Track A**.
- **Depends on:** PR-1c (mailbox model + client seam + send/sync connect path). Independent of
  PR-1e.
- **Satisfies spec requirements:** Gmail / Google Workspace OAuth2 provider; Provider
  testability with mocked transports (OAuth part).
- **Satisfies design sections:** E (E.1 — OAuthConfig/OAuthProvider, Gmail registry entry,
  XOAUTH2), F (`/mail_management/oauth/callback`).
- **Estimated changed lines:** ~350–450. Medium.

### PR-1d — tasks

- [ ] RED: Create `tests/test_oauth.py` (mock `requests.post` token endpoint + transports)
      covering: refresh → access token (cached in-process); `xoauth2(user, token)` SASL string
      shape; revoked refresh → `ReauthorizationRequired` → mailbox `state='error'` with a clear
      re-authorise message; Gmail scope set. <!-- sdd-owner: implementation -->
- [ ] GREEN: Create `lib/oauth_provider.py` (`OAuthConfig`, `OAuthProvider.access_token()`,
      `xoauth2()`, Gmail registry entry). Wire the mailbox connect path
      (`_sync_mailbox`/`_send_message`) to use `OAuthProvider.access_token()` when
      `provider_type == 'gmail'`, interpreting `credential_encrypted` as the encrypted refresh
      token. <!-- sdd-owner: implementation -->
- [ ] GREEN: Add the `/mail_management/oauth/callback` http endpoint (`auth='user'`) that
      exchanges the auth code for a refresh token and stores it encrypted. <!-- sdd-owner: implementation -->
- [ ] TRIANGULATE: harness `-u` + OAuth tests GREEN (mocked); `ruff` clean. <!-- sdd-owner: implementation -->

### PR-1d — Definition of done

- Gmail OAuth path (refresh + XOAUTH2) works end-to-end with mocked OAuth; revoked-token path
  surfaces a re-authorise prompt; refresh token stored encrypted; module installs clean.
- [ ] Start or reuse bounded review. <!-- sdd-owner: parent -->

---

## PR-1e — Microsoft 365 / Outlook OAuth2 (XOAUTH2)

- **Goal:** MS365 provider config + scopes, reusing the PR-1d `OAuthProvider` abstraction and
  mailbox model. Testable with **mocked** OAuth; the real MS365 flow is gated by non-code **Track B**.
- **Depends on:** PR-1c; reuses PR-1d's `OAuthProvider`. Independent of PR-1d's Gmail-specific
  registry entry (can land before or after PR-1d if the abstraction is present).
- **Satisfies spec requirements:** Microsoft 365 / Outlook OAuth2 provider (incl. required
  scopes `IMAP.AccessAsUser.All` + `offline_access`).
- **Satisfies design sections:** E (MS365 registry entry + scopes).
- **Estimated changed lines:** ~200–300. Low–Medium.

### PR-1e — tasks

- [ ] RED: Extend `tests/test_oauth.py` (or `test_oauth_ms365.py`) with mocked MS365 token
      endpoint: refresh → access token; required scopes
      (`IMAP.AccessAsUser.All` + `offline_access` + `SMTP.Send`); XOAUTH2 connect; revoked →
      re-authorise. <!-- sdd-owner: implementation -->
- [ ] GREEN: Add the MS365 registry entry to `lib/oauth_provider.py` (tenant-templated token URL
      + scopes) and wire `provider_type == 'oauth_ms365'` through the same connect path.
      <!-- sdd-owner: implementation -->
- [ ] TRIANGULATE: harness `-u` + MS365 OAuth tests GREEN (mocked); `ruff` clean.
      <!-- sdd-owner: implementation -->

### PR-1e — Definition of done

- MS365 OAuth path works end-to-end with mocked OAuth with the correct scopes; reuses the PR-1d
  abstraction; module installs clean.
- [ ] Start or reuse bounded review. <!-- sdd-owner: parent -->

---

## Dependency graph (PRs)

```
                          PR-1a (foundation)
                 ┌─────────────┴─────────────┐
            PR-1a-1                     PR-1a-2          (recommended apply-time split)
        (crypto+mailbox+              (generic IMAP/SMTP
         isolation+manifest)           client seam + connect/send)
                 └─────────────┬─────────────┘
                               │
                          PR-1b-i (read data layer: models + cron + sync + controllers)
                               │
                          PR-1b-ii (read-only OWL 3-pane UI)   ◀── at-risk >400 (further UI split possible)
                               │
                          PR-1c (compose + send + APPEND + SMTP)
                               │
                    ┌──────────┴──────────┐
               PR-1d (Gmail OAuth)   PR-1e (MS365 OAuth)        ◀── mutually independent; both reuse PR-1c seam
                    │                       │
              Track A (non-code)      Track B (non-code)        ◀── gate ONLY the real (non-mocked) flows
```

Legend: `│`/`└` = depends-on (lower PR must merge first). `∥` = independent. Solid lines are code
dependencies; the non-code Tracks gate only the real OAuth provider tests, not the mocked code paths.

---

## Review Workload Guard

Per-PR changed-line estimate (additions + deletions incl. tests) vs the **400-line budget**:

| PR | Est. changed lines | Budget risk | Recommendation |
|----|--------------------|-------------|----------------|
| **PR-1a** | ~850–1,100 | **High** | Split into **PR-1a-1** (pip+manifest + crypto lib + mailbox model + credential pattern + isolation ir.rules/access + form view; tests: crypto, mailbox model, isolation) and **PR-1a-2** (generic IMAP/SMTP lib seam + `action_test_connection`; tests: connect/send mocked). NOTE: PR-1a-1's crypto + isolation are security-cohesive and may still exceed 400 — a **size exception is safer than splitting crypto away from isolation**. Confirm with user (ask-on-risk). |
| **PR-1b-i** | ~700–900 | **High** | If over 400 at apply, split into **1b-i-data** (folder/message/attachment models + constraints + ir.rules + sanitize) and **1b-i-sync** (cron + `_sync_mailbox`/`_upsert_message` + read controllers). |
| **PR-1b-ii** | ~500–700 | **High** | Design already flagged the UI surface at-risk. Further split into **1b-ii-shell** (WebmailClient + FolderTree + assets/action) and **1b-ii-read** (MessageList + ReadingPane + sanitize/t-out tour). |
| **PR-1c** | ~400–550 | Medium–High | Likely near budget; split compose component from `_send_message`/controller if it spills over. |
| **PR-1d** | ~350–450 | Medium | Close to budget; keep `OAuthProvider` abstraction + Gmail entry together. |
| **PR-1e** | ~200–300 | Low | Within budget. |
| **Total Slice 1** | ~3,000–3,900 | — | Chained PRs are mandatory; no single PR should be merged above budget without an explicit decision. |

**Decision needed before apply: Yes** — the user must approve (a) the PR-1a-1/1a-2 split (or a size
exception for the security foundation), and (b) the further splits for any PR that measures over 400
at apply time. `sdd-apply` must measure the actual diff and pause (ask-on-risk) before exceeding budget.

---

## Non-code gating tracks (gate ONLY the real OAuth flows)

These are **user actions, not code tasks.** They do NOT block PR-1a / 1b / 1c (generic IMAP/SMTP +
read/reply/forward are fully testable today), and they do NOT block the **mocked** OAuth tests in
PR-1d/1e. They gate only the **real** (non-mocked) Gmail/MS365 provider end-to-end tests.

- **Track A — Google Cloud Console (Gmail / Google Workspace OAuth2).** Create the OAuth consent
  screen + an OAuth Web client → `client_id` / `client_secret`; add the Gmail IMAP/SMTP XOAUTH2 scope
  (`https://mail.google.com/`); register the redirect URI that
  `/mail_management/oauth/callback` will use. **Gates the real PR-1d flow.**
- **Track B — Microsoft Entra ID (Microsoft 365 / Outlook OAuth2).** Register an application →
  `client_id` / `client_secret`; configure the redirect URI; add the IMAP/SMTP-over-OAuth2 scopes
  (`https://outlook.office.com/IMAP.AccessAsUser.All`, `SMTP.Send`, `offline_access`); grant admin
  consent. **Gates the real PR-1e flow.**

Both tracks run in parallel with the code build. `client_id` / `client_secret` land in
`ir.config_parameter` (read via `sudo()`); the encrypted **refresh token** lives on the mailbox in
`credential_encrypted` (same field/crypto as the generic app-password — `provider_type` determines
interpretation).

---

## SDD Result Contract

- **status:** complete
- **executive_summary:** Slice-1 (Webmail core) decomposed into an ordered, checkable, strict-TDD
  implementation plan across the design's chained PRs. **PR-1a** (the security-critical foundation)
  is detailed in full: `imap-tools` install → Fernet `lib/mail_crypto.py` (env>icp>autogen key) →
  removal of the placeholder `mail.management` scaffold → `mail.management.mailbox` with the
  transient compute+inverse credential pattern (ciphertext-only at rest) and `UNIQUE(user_id,
  imap_host, imap_user)` → two group-scoped ir.rules (NOT global own-only) for per-user isolation →
  mockable generic IMAP/SMTP `lib/` seam + `action_test_connection` → manifest/`pip` wiring, each
  paired RED→GREEN under the exact docker harness. PR-1b-i (read data layer: folder/message/
  attachment models, cron sync, `UNIQUE(folder_id,uid)` + `message_id` upsert, UIDVALIDITY recovery,
  read controllers), PR-1b-ii (OWL read UI), PR-1c (compose + send + APPEND), PR-1d (Gmail OAuth) and
  PR-1e (MS365 OAuth) follow with concrete, dependency-ordered tasks and test mappings. The plan
  flags PR-1a / 1b-i / 1b-ii as over the 400-line budget with explicit split recommendations
  (PR-1a-1/1a-2; 1b-i-data/sync; 1b-ii-shell/read), records the PR dependency graph, and calls out
  the two non-code gating tracks (Google Cloud Console; Microsoft Entra ID) that gate only the real
  OAuth flows. Odoo-19-correct patterns (`models.Constraint`/`models.Index`, `@api.ondelete`,
  `@api.model_create_multi`, `@api.private`, privilege bridge, `t-out` only) are baked into every
  task wording from the loaded skill guides.
- **artifacts:**
  - `openspec/changes/webmail-and-ai-assistant/tasks.md` (this file)
- **next_recommended:** `sdd-apply`, starting with **PR-1a** (and confirming the PR-1a-1 / PR-1a-2
  split vs. a security-foundation size exception per the Review Workload Guard before exceeding the
  400-line budget).
- **risks:**
  - R-BUDGET: PR-1a, PR-1b-i, PR-1b-ii are each forecast >400 changed lines → chained PRs + splits
    are mandatory; a size exception may be required for the crypto+isolation security foundation.
    (ask-on-risk before apply.)
  - R1 (carried): `UNIQUE(folder_id, uid)` + UIDVALIDITY `message_id` upsert data integrity — the #1
    HIGH-RISK item; dedicated RED→GREEN in PR-1b-i.
  - R2 (carried): credential plaintext leak — mitigated by the transient credential pattern +
    manager-group ciphertext + `@api.private` accessor + `redact()`; dedicated tests in PR-1a.
  - R-OAUTH-GATE: real Gmail/MS365 OAuth flows depend on non-code Track A/B registrations; only the
    mocked paths are code-gated.
  - R8 (carried): `imap-tools` not present → the pip step is the FIRST PR-1a task so imports resolve.
- **skill_resolution:** `paths-injected` — `~/.config/opencode/skills/odoo-19/SKILL.md` plus the
  testing / transaction / decorator / security / development guides were read directly before
  finalizing; no registry/fallback discovery was used.
