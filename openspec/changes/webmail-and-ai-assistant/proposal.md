# Proposal — `webmail-and-ai-assistant`

> **SDD phase: PROPOSAL.** This document captures intent, scope, impact, risks, key
> decisions, and open questions for a single Odoo 19 Community addon change. It is
> decision-oriented and contains **no code**. Spec/design/tasks/apply/verify are
> produced in later SDD phases.
>
> **Primary technical reference:** `openspec/changes/webmail-and-ai-assistant/exploration.md`
> (authoritative, written this session). Every IMAP/SMTP, credential, parsing, UI, and
> sync statement below is consistent with that exploration. Where this proposal makes a
> finer choice than the exploration, it is flagged explicitly.
>
> **Scope note.** This proposal scopes **Slice 1 (Webmail core)** in detail. Slice 2
> (AI layer) is noted as the next change and is **not** detailed here.

---

## Metadata

| Field | Value |
|---|---|
| Change slug | `webmail-and-ai-assistant` (confirmed — §12) |
| Module | `mail_management` (`addons/mail_management`, Odoo 19 Community) |
| Manifest version | `19.0.1.0.0` → **`19.0.2.0.0`** (first feature bump; slice 1 is a new app surface) |
| Scope | Whole product direction (webmail + AI). **Slice 1 (Webmail core) is the implementable-now scope of this proposal.** Slice 2 (AI) is the next change. |
| SDD execution mode | interactive |
| Artifact store | openspec |
| Chained PR strategy | auto-forecast |
| Review budget | 400 changed lines per PR |
| strict_tdd | true (docker-compose `odoo --test-enable --stop-after-init --test-tags=/mail_management` harness on the dedicated `local_test` DB; RED→GREEN evidence in stdout) |
| Skill resolution | `paths-injected` — `~/.config/opencode/skills/odoo-19/SKILL.md` loaded, plus the field-guide reference consulted (confirms **no `fields.Encrypted`** type in the Odoo 19 Community type summary → drives the credential-design decision, §5/§10) |

---

## 1. Problem / why (current-state gap)

Odoo has **no usable webmail**. A user who lives in Odoo all day must keep a separate
mail client (Roundcube/SnappyMail/Gmail/Outlook) open in another tab to read, reply to,
and forward their own email. That context switch is friction today, and it becomes a
hard blocker the moment the product wants the email conversation to feed an **AI
assistant** (draft replies, summarize threads, improve replies, give conversation
context) — the AI cannot act on mail that is not inside Odoo.

The end-user goal is to **manage email inside Odoo like a webmail client** (Roundcube /
SnappyMail parity for the common actions), then **layer an AI assistant on top** so the
AI reads the same mail the user reads. This is the **email** domain and is distinct
from — and complementary to — the sibling `message_management_ai`, which covers the
**WhatsApp/Telegram messaging** channels, not email.

**Why now:** the module scaffold exists and installs green on the dedicated `local_test`
DB, the verified headless test harness is in place, and the AI provider to reuse (from
the sibling) has been analysed and confirmed reusable. The technical path is clear and
unblocked; the missing piece is the webmail itself.

---

## 2. Target users and situations

- **Primary user: an Odoo internal user** (`res.users`) who wants to read and answer
  their own email without leaving Odoo. Each user connects **one or more** of their own
  mailboxes (personal or Workspace/organisational) and manages them privately.
- **Situation:** during the workday the user opens the **Webmail** app in Odoo, sees a
  3-pane view (folder tree — message list — reading pane), reads new mail, replies or
  forwards, and downloads/uploads attachments — all from inside Odoo.
- **Urgency:** medium-to-high. There is no email capability in the module today at all;
  the AI layer (slice 2, the higher-value feature) is *blocked* until mail lives in
  Odoo. Slice 1 is the prerequisite that unlocks slice 2.
- **Operator/admin:** configures the module defaults and the AI provider key (slice 2);
  in slice 1 the admin only ensures the security groups / privilege migration and the
  non-code OAuth app registrations (§7) are in place.

---

## 3. Business rules

1. **Private-per-user isolation (hard).** Each Odoo user sees and manages **only their
   own** mailboxes and messages. There are **no shared mailboxes** in v1. Isolation is
   enforced by an `ir.rule` (`('user_id', '=', user.id)`) on the mailbox, folder, and
   message models, plus field-level `groups` on secret fields. This is unit-tested for
   cross-user leakage (like the sibling's multi-company rules).
2. **Credential encryption at rest (hard, first-class).** IMAP/SMTP app-passwords and
   OAuth2 refresh tokens are stored **encrypted** in the DB; plaintext is held
   **in-memory only** at connect time and is **never** written to logs, `request_json`,
   error text, or serialised payloads. Plaintext-only storage (the sibling's
   `Char(password=True)` approach) is **rejected** for mailbox credentials because a DB
   dump would equal full takeover of every connected mailbox. The exact primitive is
   locked in design after verifying `fields.Encrypted` availability (§10 — the Odoo 19
   field guide lists **no `Encrypted` type**, so app-level Fernet is the leading
   candidate pending image verification).
3. **HTML sanitisation on render (hard).** Arbitrary sender HTML is **untrusted**. Every
   rendered message body is run through `odoo.tools.html_sanitize` and rendered with
   `t-out` (never `t-raw`). Inline images (CID) and remote-image blocking are handled
   deliberately. This is a security invariant, not a polish item.
4. **Three providers required.** Slice 1 must connect (a) **generic IMAP/SMTP with
   app-password**, (b) **Gmail / Google Workspace via OAuth2** (refresh token), and
   (c) **Microsoft 365 / Outlook via OAuth2** (refresh token). OAuth2 is preferred over
   app-passwords where the provider supports it (revocable, scoped).
5. **Threading correctness.** A reply must set `In-Reply-To` + `References` from the
   original `Message-ID`, and a sent copy must be APPENDed to the account's "Sent"
   folder so the user's own sent mail is visible in the webmail.
6. **Sync fits the worker pool.** With `workers = 4`, no sync path may pin a worker
   long-term. Sync is cron-pull (bounded batches) + on-demand refresh; **IMAP IDLE is
   out of scope for v1** (it blocks a worker per mailbox).
7. **Folders.** v1 supports Inbox + Sent + Trash + the standard IMAP/SPECIAL-USE
   folders the server advertises. Custom-folder management is a later refinement.

---

## 4. Product outcome

After Slice 1, an Odoo user can:

- connect one or more of their own mailboxes (generic IMAP/SMTP, Gmail, or Microsoft
  365);
- see a 3-pane webmail (folders → message list → reading pane) inside Odoo;
- read mail (sanitised HTML + attachments), reply, reply-all, and forward;
- upload attachments on send and download/view them on read;
- do all of the above **privately** — no other user can see their mailboxes or messages;
- have their credentials stored encrypted at rest.

After Slice 1, the module is **ready for Slice 2 (AI)**: the AI provider reuse path is
confirmed, the message/thread model exists for the AI to read, and the cron-queue
orchestration pattern is available. Slice 2 itself is the next change.

---

## 5. Scope

### IN — Slice 1 (Webmail core), detailed

- **Mailbox configuration** per user: IMAP host/port/user, encrypted credential (or
  OAuth2 refresh token), SMTP host/port, provider type, company link, last-sync state.
  Owns a dedicated IMAP/SMTP client (prefer `imap-tools` + stdlib `smtplib`; stdlib-only
  fallback documented).
- **Per-user privacy:** `ir.rule` isolation on mailbox/folder/message; secret fields
  `groups`-restricted; `password=True` UI masking.
- **Credential encryption at rest:** ciphertext on the mailbox field; master key in
  `res.config.settings` / `ir.config_parameter` (or a mounted secret — open decision
  Q-KEY); decrypt in-memory only; never logged. `cryptography` (Fernet) pip dep unless
  `fields.Encrypted` is verified present and stable.
- **Folders:** Inbox + Sent + Trash + standard IMAP/SPECIAL-USE folders; per-folder
  `uidvalidity` + `last_uid` sync state; unread/message counts.
- **Read:** list messages (paginated, unread badges, sort by date), reading pane
  (sanitised HTML + attachment list + raw-header view), per-folder threading via
  `References`/`In-Reply-To`.
- **Reply / Reply-All / Forward:** plain-text compose; correct threading headers;
  APPEND a copy to "Sent"; send via SMTP.
- **Attachments:** upload on send (MIME-attach), download/view on read (via
  `ir.attachment`/filestore, not DB blobs).
- **Three providers:** generic IMAP/SMTP (app-password), Gmail OAuth2, Microsoft 365
  OAuth2.
- **Sync:** cron-pull backbone (UID-incremental, idempotent by `message_id` + UID,
  UIDVALIDITY-aware) + on-demand refresh when a folder is opened.
- **Webmail UI:** custom OWL client action (3-pane), JSON-RPC controllers in
  `controllers/main.py`.
- **Security migration:** `security_groups.xml` migrated from the removed
  `res.groups.category_id` pattern to the Odoo 19 `res.groups.privilege` bridge (preflight
  blocker from the exploration).
- **Tests:** mailbox model/isolation, encrypt/decrypt round-trip, IMAP/SMTP client
  (mocked), sync dedup + UIDVALIDITY recovery, MIME parsing/sanitisation, send +
  threading + APPEND, OAuth2 flows (mocked). strict_tdd: RED→GREEN on the `local_test` DB.

### OUT — Slice 2 (AI layer), noted as the next change (lightly scoped only)

The four AI capabilities the user wants — **draft reply, summarize thread, improve
reply, conversation context**. Built by **reusing the sibling's AI provider**
(`message_management_ai/lib/ai_provider.py` + `gemini_provider.py`, per the exploration's
reuse verdict — Option A: copy the two provider files for v1, shared-base-module later),
with a new email-specific `prompt_assembly` and a `mail.ai.config` mirroring the
duck-typed contract, plus the sibling's cron-queue + error-routing orchestration
pattern. **AI is explicitly out of Slice 1** and is detailed in its own proposal.

### OUT — other non-goals for v1 (Slice 1)

- **AI layer** (Slice 2 — separate change).
- **Calendar, contacts, mailing lists** (not webmail core).
- **Full Roundcube/SnappyMail parity:** full-text search, mail filters/rules,
  drag-to-move, rich-text/HTML compose, keyboard shortcuts, multiple-account UX chrome,
  send-later, signatures management, identities/aliases.
- **IMAP IDLE / real-time push** (needs a sidecar outside `workers=4`).
- **Shared/team mailboxes** (v1 is strictly private-per-user).
- **Exotic MIME:** calendar invites (`text/calendar`), S/MIME, PGP; v1 collapses
  `multipart/alternative` to the HTML part.
- **No CI pipeline added in this slice.** TDD evidence comes from the docker-compose
  batch test harness.

---

## 6. Implications & impact (affected areas)

| Area | File(s) | Change |
|---|---|---|
| Manifest | `__manifest__.py` | version `19.0.1.0.0` → `19.0.2.0.0`; `external_dependencies.python` gains `imap-tools` (preferred) or stays `[]` (stdlib-only fallback) + `cryptography` (Fernet) unless `fields.Encrypted` verified; `assets` declares the OWL bundle; `depends` gains `fetchmail`? — no, dedicated client, keep `[base, mail]` |
| Scaffold model | `models/mail_management.py` | the draft `mail.management` scaffold is **repurposed/renamed** into the new webmail model family (mailbox/folder/message) — see below |
| Mailbox model (NEW core) | `models/mail_management_mailbox.py` | `mail.management.mailbox` — per-user IMAP/SMTP config + provider type + encrypted cred + SMTP + sync state; owns the IMAP/SMTP client; `(user_id, imap_host, imap_user)` uniqueness |
| Folder model (NEW) | `models/mail_management_folder.py` | `mail.management.folder` — per mailbox: name, SPECIAL-USE role, `uidvalidity`, `last_uid`, counts |
| Message model (NEW) | `models/mail_management_message.py` | `mail.management.message` — `mailbox_id`, `folder_id`, `uid`, `message_id`, `in_reply_to`, `references`, `subject`, sanitised `body_html`, raw `headers`, `flags`, attachment pointers; per-folder threading; `(mailbox_id, uid)` + `message_id` dedup constraints |
| Credential encryption (NEW) | `models/...` + `lib/crypto.py` (if Fernet) | encrypt-at-rest helper; master-key accessor via `res.config.settings`/`ir.config_parameter`; `_redact_token`-style scrubbing (mirror sibling) |
| IMAP/SMTP client (NEW) | `lib/imap_client.py`, `lib/smtp_client.py` (or one `mail_client.py`) | dedicated client over `imap-tools`/`imaplib` + `smtplib`; connect/list/search/fetch/store/append; timeout-guarded |
| OAuth2 (NEW) | `lib/oauth_provider.py` + `models/...` | OAuth2 abstraction (auth-code → refresh token → access token, refresh path); Gmail + MS365 concrete configs |
| Controllers | `controllers/main.py` (currently empty scaffold) | JSON-RPC endpoints for the OWL client: list-folders, fetch-messages, fetch-body, send (reply/forward), set-flags, refresh-folder, oauth-callback |
| OWL UI (NEW) | `static/src/...` + `views/...` | custom OWL client action: folder tree, message list, reading pane, compose; assets bundle |
| Views | `views/mail_management_views.xml` | mailbox config form (secret fields masked/grouped); menu/action for the Webmail client action |
| Security | `security/security_groups.xml`, `security/ir.model.access.csv`, `security/...` | **migrate `security_groups.xml` to the `res.groups.privilege` bridge** (preflight blocker); per-user `ir.rule` on mailbox/folder/message; ACL for the new models; secret-field `groups` |
| Tests | `tests/` | new test files for model/isolation/crypto/client/sync/parsing/send/oauth (currently only the scaffold `test_module.py`) |
| Runtime | `docker-compose.yml` entrypoint pip line | add `imap-tools` (+ `cryptography` if Fernet) to the `pip3 install ... --break-system-packages` line (mirrors the sibling's `pyjwt`/`python-docx`/`pdf2docx` extension) |

### Effort shape

- **UI is ~60–70% of total effort** and is the dominant schedule risk (exploration Area
  5). It **will exceed the 400-line review budget** in a single PR and **must be split**
  into chained PRs (§8).
- **Data model + security + credential encryption** is the first, foundational,
  highest-risk slice (it gates everything and carries the #1 security risk).
- **Cron sync** adds worker-pool pressure under `workers=4`; batches must be bounded
  (`cron_batch_size`) and timeout-guarded.

---

## 7. Non-code gating tracks (parallel; user action required)

OAuth2 for Gmail and Microsoft 365 each require **registering an OAuth app** to obtain a
`client_id` / `client_secret`. The user must do this for real OAuth to work end-to-end.
This mirrors the sibling's Meta-Tech-Provider pattern: **the code can be built and
tested today with the generic IMAP/SMTP path + mocked OAuth**, while the real provider
flows come online once each app registration is complete.

- **Track A — Google Cloud Console (for Gmail / Google Workspace OAuth2).** Create an
  OAuth consent screen + OAuth client (Web application) → `client_id`/`client_secret`;
  add Gmail API scopes (e.g. `gmail.readonly` / `gmail.send` / `gmail.modify`); register
  the redirect URI the Odoo OAuth callback will use. User action; non-code.
- **Track B — Microsoft Azure / Entra ID (for Microsoft 365 / Outlook OAuth2).** Register
  an application in Microsoft Entra admin → `client_id`/`client_secret` (or cert);
  configure the redirect URI; add the IMAP/SMTP-over-OAuth2 scopes
  (`https://outlook.office.com/IMAP.AccessAsUser.All`, `SMTP.Send`); grant admin consent.
  User action; non-code.

Both tracks can run in parallel with the code build. **Neither blocks the generic
IMAP/SMTP path (PR-1a) or the core read/reply/forward/attachments (PR-1b/1c)**, which are
fully testable today. The OAuth code PRs (PR-1d/1e) are testable with **mocked OAuth**
until the real registrations land.

---

## 8. Delivery / PR forecast (Slice 1 sub-sliced into chained PRs)

Slice 1 is large and the review budget is **400 changed lines per PR**. It is split into
five chained, reviewable PRs. The UI is built **incrementally** (its heaviest surface
lands in PR-1b) rather than as one monolithic UI PR.

| PR | Title | Scope | Testable how | Non-code track |
|---|---|---|---|---|
| **PR-1a** | Foundation: mailbox model + credential encryption + per-user isolation + generic IMAP/SMTP connect/send | `mail.management.mailbox` model; credential encryption-at-rest (verify `fields.Encrypted` else Fernet + master key); `security_groups.xml` → `res.groups.privilege` migration; per-user `ir.rule`; `lib/imap_client`/`lib/smtp_client` connect + test-connection; minimal mailbox-config form view; `external_dependencies` + entrypoint pip line | model + isolation + crypto round-trip + client connect/send (mocked IMAP/SMTP) tests on `local_test` | none |
| **PR-1b** | Read webmail: folders + cron sync + message storage/parsing + 3-pane UI (read) | `mail.management.folder` + `mail.management.message`; cron-pull sync (UID-incremental, UIDVALIDITY-aware, idempotent); folder list (Inbox/Sent/Trash/standard); **OWL 3-pane client action** (folder tree + message list + reading pane, sanitised HTML + attachments); JSON-RPC controllers (list-folders/fetch-messages/fetch-body/refresh/set-flags); on-demand refresh | sync dedup + UIDVALIDITY recovery + parsing/sanitisation + controller tests; OWL via HttpCase where feasible | none |
| **PR-1c** | Compose: reply / reply-all / forward + attachments + APPEND-to-Sent | plain-text compose; threading headers (`In-Reply-To`/`References`); attachments upload-on-send + download-on-read (`ir.attachment`/filestore); APPEND sent copy; SMTP send; controller `send` endpoint; compose pane in the OWL UI | send + threading + APPEND + attachment tests (mocked SMTP/IMAP) | none |
| **PR-1d** | Gmail / Google Workspace OAuth2 | OAuth2 abstraction (`lib/oauth_provider.py`); Gmail auth-code → refresh-token acquisition (callback controller) + token refresh; mailbox model gains OAuth fields; reuses PR-1a credential encryption for the refresh token | OAuth flow + refresh (mocked OAuth server) tests | **Track A** (Google Cloud Console app) |
| **PR-1e** | Microsoft 365 / Outlook OAuth2 | MS365 auth-code → refresh-token acquisition + token refresh; reuses the OAuth2 abstraction + mailbox model from PR-1d; Azure/Entra scopes | OAuth flow + refresh (mocked) tests | **Track B** (Azure/Entra app) |

**Ordering rationale.** PR-1a establishes the security-critical foundation (encryption +
isolation) and the generic IMAP/SMTP path that is testable today with no non-code
prerequisite. PR-1b/1c deliver the read + reply/forward/attachments webmail on the
generic provider (the most demoable, highest-value slice). PR-1d/1e add the two OAuth
providers, reusing the same mailbox model and encryption; their non-code tracks run in
parallel and only gate the *real* provider test, not the code or the mocked tests.

**UI risk note.** PR-1b's OWL 3-pane is the single largest surface. If a draft of PR-1b
exceeds 400 changed lines, the spec/design phase **must further split it** (e.g.
1b-i controllers + message model + sync; 1b-ii OWL folder-tree + list; 1b-iii reading
pane). The proposal flags this; the split is finalised in tasks.

---

## 9. Edge cases

- **UIDVALIDITY change:** a folder recreated on the server resets UIDs; sync state must
  detect the change and re-fetch from scratch without corrupting or duplicating
  messages (clear `last_uid`, re-scan, dedup by `message_id`).
- **Large attachments / messages:** bodies and attachments go to the filestore
  (`ir.attachment`), not DB blobs; sync FETCH is bounded and timeout-guarded to avoid
  starving the 4-worker pool.
- **IMAP provider variance:** folder naming (`INBOX` vs `Inbox`), `XLIST`/`SPECIAL-USE`,
  UTF-7 folder names, flag support, and IDLE availability differ across Gmail / Outlook
  / Dovecot / Courier → a per-provider test matrix is needed (spec/design).
- **OAuth token refresh / expiry:** access tokens are short-lived; the refresh path must
  run transparently before each connect, handle a revoked/expired refresh token with a
  clear re-authorise prompt, and avoid refresh races (cron + on-demand hitting the same
  mailbox concurrently).
- **Untrusted HTML / XSS:** every rendered body is sanitised (`html_sanitize`) and
  rendered with `t-out` (never `t-raw`); remote-image blocking and CID inline images are
  handled deliberately.
- **Concurrency:** two cron ticks, or cron + on-demand, hitting one mailbox must not
  double-insert — rely on `(mailbox_id, uid)` / `message_id` unique constraints.
- **Encoding / malformed MIME:** RFC2047 encoded-words, charset mismatches, malformed
  multipart — rely on Python's `email` + Odoo's `tools.mail` helpers; budget for corner
  cases.
- **Sent-folder APPEND failure:** if APPEND to "Sent" fails after SMTP send succeeds, the
  mail was sent but not mirrored — log clearly and surface to the user; do not silently
  lose the send state.

---

## 10. Security — credential storage (first-class requirement)

> This is the **#1 risk** of the whole change (exploration Area 3). It is a
> **design-phase lock-in**, not an afterthought.

- **Encryption-at-rest is mandatory** for IMAP/SMTP app-passwords and OAuth2 refresh
  tokens. Plaintext `Char(password=True)`-only storage is **rejected** (DB dump = full
  takeover of every connected mailbox).
- **Primitive decision (to verify in spec/design):** the Odoo 19 field-guide type
  summary lists **no `Encrypted` field type**. So the leading design is **app-level
  Fernet** (`cryptography`, added to the entrypoint pip line) with the master key held
  in `res.config.settings`/`ir.config_parameter`; ciphertext in the mailbox field;
  plaintext decrypted in-memory only at connect time. Spec **must** still
  `docker compose exec web …` to verify whether a stock `fields.Encrypted` exists in the
  running `odoo:19.0` image before locking the design — if it does and is stable, it is
  the cleaner option (less bespoke crypto code).
- **Master-key location (open decision Q-KEY):** a Fernet key in
  `ir.config_parameter` means anyone with DB read gets ciphertext *and* key. Prefer a
  key mounted into the container via an env var / Docker secret, read at runtime, never
  in the DB. Decide in design.
- **Defence-in-depth (regardless of primitive):** `password=True` UI masking;
  `groups`-restricted field access (owner + mail-management managers only); per-user
  `ir.rule`; `_redact_token`-style scrubbing on any audit/payload field so a credential
  can never leak into a log or a serialised response.
- **Prefer OAuth2** for Gmail/Outlook (revocable, scoped refresh tokens) over
  long-lived app-passwords; app-passwords (still encrypted) for providers without OAuth.

---

## 11. Open decisions / decision gaps to resolve in spec/design

The scope above is **fixed and confirmed** (not re-litigated). The following are
genuinely still product/spec/design unknowns and carry a default **assumption** if the
user defers them:

- **Q-ENCRYPT — encrypted-field primitive.** Is there a stock, stable `fields.Encrypted`
  in the running `odoo:19.0` Community image? *Assumption: no (field guide lists none);
  use app-level Fernet (`cryptography`) with a mounted/env master key.* Verify against
  the image in spec.
- **Q-KEY — master-key location.** DB row (`ir.config_parameter`) vs mounted secret
  (env var / Docker secret). *Assumption: mounted secret, never in the DB, to avoid
  ciphertext+key co-location on a dump.*
- **Q-IMAP-LIB — `imap-tools` vs stdlib-only.** Cleaner code (one pip dep) vs zero new
  dependency. *Assumption: `imap-tools` + stdlib `smtplib` (consistent with the
  sibling's pip-extension practice); stdlib-only fallback documented.*
- **Q-SYNC-CADENCE — cron interval.** How often does the cron pull run? *Assumption: a
  configurable interval (default ~5 min) with bounded `cron_batch_size`, mirroring the
  sibling's cron pattern.*
- **Q-COMPOSE — plain text vs HTML compose.** v1 compose is plain-text (per non-goals);
  confirm no rich-text editor in slice 1. *Assumption: plain-text only in slice 1; rich
  compose is a later refinement.*
- **Q-UIDVALIDITY-RECOVERY — full re-fetch policy.** On a UIDVALIDITY change, re-fetch
  all and dedup by `message_id`, or wipe and re-fetch clean? *Assumption: re-fetch +
  dedup by `message_id` (preserves any user flags on still-present messages by
  `message_id`).*
- **Q-SENT-APPEND — which "Sent" folder.** APPEND to the SPECIAL-USE `\Sent` folder the
  server advertises, or a configured folder name? *Assumption: SPECIAL-USE `\Sent` if
  advertised, else a per-mailbox configurable `sent_folder_name` defaulting to `Sent`.*
- **Q-OAUTH-SCOPES — exact Gmail / MS365 scopes.** *Assumption: Gmail `gmail.modify`
  (read + send, no permanent deletion); MS365 `IMAP.AccessAsUser.All` + `SMTP.Send`
  (IMAP/SMTP over OAuth2). Confirm minimal sufficient scopes in design.*

These are product/contract unknowns, not delivery mechanics, and should be answered (or
explicitly deferred to spec) before the spec phase begins.

---

## 12. Change slug — confirmed

`webmail-and-ai-assistant` is **confirmed**. It correctly captures the whole product
direction (webmail + AI assistant). Slice 1 (this proposal) is webmail-only; Slice 2
(AI) is the next change and will be detailed in its own proposal.

**Refinement consideration (not adopted):** since this proposal implements only Slice 1
(webmail) and Slice 2 (AI) is the *next* change, one could split into two slugs (e.g.
`webmail-core` + `webmail-ai-assistant`). Not adopted because (a) the exploration,
config, and existing folder all use `webmail-and-ai-assistant`; (b) keeping one umbrella
slug preserves the single authoritative technical reference. Slice 2 may optionally be
filed as a follow-on change slug when that work begins — a bookkeeping decision, not a
technical one.

---

## 13. Rollback

The module is **installed only in the throwaway `local_test` DB** (verified) and is
**not installed** in any production/shared DB. Rollback is therefore a clean revert:

1. Revert the module to `19.0.1.0.0` (git revert / redeploy prior manifest).
2. Drop/recreate `local_test` (it is a dedicated throwaway test DB) if any partial
   state is undesirable.

There is **no production data** and **no installed production DB** to migrate or restore.
If the module is ever deployed to a real DB before all PRs land, the credential fields
(encrypted) and the message/folder/mailbox tables are additive — a downgrade removes
the new tables; no destructive schema change is expected. **No irreversible data state**
is created by Slice 1.

---

## 14. Success criteria (Slice 1)

- An Odoo user can **connect a generic IMAP/SMTP mailbox** (app-password) and, after
  cron sync + on-demand refresh, see their **Inbox/Sent/Trash** in a 3-pane webmail,
  **read** a message (sanitised HTML + attachments), **reply / reply-all / forward**
  (correct `In-Reply-To`/`References`, sent copy APPENDed to "Sent"), and
  **upload/download attachments**.
- **Per-user isolation is airtight:** user A cannot read user B's mailboxes, folders,
  messages, or secret fields (verified by an isolation unit test, like the sibling's
  multi-company rules).
- **Credentials are encrypted at rest:** the DB stores only ciphertext for IMAP
  passwords and OAuth refresh tokens; plaintext appears only in-memory at connect time;
  no credential appears in logs, `request_json`, or error text.
- **OAuth2 providers work:** Gmail and Microsoft 365 mailboxes connect via OAuth2
  refresh token (once Tracks A/B registrations are done); token refresh is transparent;
  a revoked token surfaces a clear re-authorise prompt. Both are unit-testable with
  mocked OAuth before the registrations land.
- **Sync is worker-safe:** cron sync is batch-bounded and timeout-guarded; no sync path
  pins a worker long-term under `workers=4`; UIDVALIDITY changes recover without
  duplication.
- **Untrusted content is safe:** every rendered body is sanitised and rendered with
  `t-out`; no `t-raw`; remote-image blocking and CID handling are deliberate.
- **Security migration done:** `security_groups.xml` uses the `res.groups.privilege`
  bridge (no removed `category_id`); group assignment works on the Odoo 19 user form.
- **Tests green under strict_tdd:** all new test files pass the docker-compose
  `--test-enable --stop-after-init --test-tags=/mail_management` harness on `local_test`,
  with RED→GREEN evidence recorded.
- Manifest reports `19.0.2.0.0` and the module installs/upgrades cleanly on `local_test`.

---

## 15. Key decisions (baked in — scope confirmed with the user this session)

- **D-SLICE-SPLIT.** Two slices: **Slice 1 = Webmail core (this proposal, detailed)**;
  **Slice 2 = AI layer (next change, noted only)**. AI is explicitly out of Slice 1.
- **D-THREE-PROVIDERS.** Slice 1 must support (a) generic IMAP/SMTP app-password,
  (b) Gmail/Google Workspace OAuth2, (c) Microsoft 365/Outlook OAuth2. OAuth2 preferred
  where supported.
- **D-PER-USER-PRIVATE.** Each Odoo user sees and manages only their own mailboxes;
  isolation via `ir.rule` on `user_id`; no shared mailboxes in v1.
- **D-CRED-ENCRYPT (hard).** Credential encryption-at-rest is mandatory; plaintext-only
  rejected; primitive verified in spec (`fields.Encrypted` likely absent → Fernet);
  OAuth2 refresh tokens also encrypted.
- **D-IMAP-SMTP-CLIENT.** Dedicated Python IMAP/SMTP client (prefer `imap-tools` +
  stdlib `smtplib`); Odoo native `fetchmail`/`ir.mail_server` is receive-only and cannot
  be the webmail backbone; `mail.thread` reused only for AI chatter in Slice 2, not
  mailbox state.
- **D-SYNC-CRON-PULL.** Cron-pull backbone (UID-incremental, idempotent, UIDVALIDITY-
  aware) + on-demand refresh; IMAP IDLE out of scope for v1 (needs a sidecar outside
  `workers=4`).
- **D-UI-PHASED-OWL.** Custom OWL client action built incrementally across PRs; v1 =
  3-pane (folders + list + reading pane) + plain-text compose; rich compose/search/
  filters/move deferred. UI is the dominant schedule risk (~60–70% of effort) and is
  split into chained PRs.
- **D-TWO-OAUTH-TRACKS.** Gmail OAuth2 and MS365 OAuth2 each require a non-code OAuth
  app registration (Track A: Google Cloud Console; Track B: Azure/Entra ID). Code is
  built testable with generic IMAP/SMTP + mocked OAuth; real flows come online when the
  registrations complete.
- **D-AI-REUSE (Slice 2 only).** Slice 2 reuses the sibling's `ai_provider.py` +
  `gemini_provider.py` (copy for v1; shared-base-module later) per the exploration's
  reuse verdict; new email `prompt_assembly` + `mail.ai.config`. Not in this proposal.
- **D-SECURITY-MIGRATION.** `security_groups.xml` migrated to the Odoo 19
  `res.groups.privilege` bridge (preflight blocker) as part of PR-1a.
