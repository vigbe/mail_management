# Exploration — `webmail-and-ai-assistant` (mail_management, Odoo 19 Community)

> SDD **explore** phase. Discovery + recommendations ONLY. No code is implemented.
> Final decisions belong to the **proposal** phase. This document is the
> authoritative technical reference (`change_context.authoritative_reference`)
> that all downstream phases (proposal/spec/design/tasks/apply/verify) MUST read.

- **Change slug:** `webmail-and-ai-assistant`
- **Module:** `mail_management` (`addons/mail_management`, Odoo 19.0.1.0.0, LGPL-3)
- **Intent:** an integrated webmail inside Odoo (per-user IMAP/SMTP mailbox config
  - sync, read/reply/forward like Roundcube/SnappyMail) **plus** an AI assistant
  layer (draft replies, summaries, reply improvement, conversation context).
  Complementary to the sibling `message_management_ai` (WhatsApp/Telegram).
- **Scope at this phase:** answer the six `open_questions_for_proposal` from
  `openspec/config.yaml`.

## How evidence was gathered (confidence levels)

| Source | Available? | Used for |
|---|---|---|
| Sibling code on host (`../message_management_ai/**`) | ✅ read directly | Area 1 (AI provider reuse) — high confidence, code cited by path/class/method |
| Odoo 19 skill references (`~/.config/opencode/skills/odoo-19/references/*`) | ✅ read directly | Areas 2–6 framework facts — based on the skill's source analysis |
| `docker-compose.yml` / `odoo.conf` (workspace root) | ✅ read directly | Runtime facts (image, workers, pip deps, mounts) |
| Odoo 19 framework source (`odoo/addons/mail`, `fetchmail`, `tools/mail.py`) | ❌ NOT on host filesystem — lives inside the `odoo:19.0` image | Could NOT be read directly |
| `webfetch` of Odoo 19 docs/source | ❌ tool not available in this runtime | N/A |

**Implication:** framework facts below are graded **[skill-ref]** (from the
odoo-19 guides, which are themselves based on source analysis) or
**[well-established]** (stable, long-standing Odoo behaviour). Items that depend
on the exact Odoo 19 Community source are explicitly flagged
**[VERIFY-AGAINST-IMAGE]** for the proposal/spec phase — the spec/design phase
should open the running container (`docker compose exec web …`) and confirm
these against the real `odoo:19.0` source before locking the design.

---

## Runtime facts (read this run)

From `../../docker-compose.yml` and `../../odoo.conf`:

- Image: `odoo:19.0` (Community). DB: `postgres:17`. nginx reverse proxy on 8069.
- `addons_path = /mnt/extra-addons`; only `./addons` is bind-mounted there. The
  Odoo framework core (`odoo/addons/mail`, `fetchmail`, `odoo/tools/mail.py`,
  `odoo/fields.py`) is **inside the image**, not mounted, not readable from host.
- **`workers = 4`** (multi-worker prefork), `db_maxconn = 64`, `gevent_port = 8072`.
  → Long-lived/blocking connections (e.g. IMAP IDLE) compete for one of 4 workers.
  This is a hard constraint for the sync strategy (Area 6).
- Web entrypoint pip-installs **only**: `pyjwt python-docx pdf2docx`
  (`--break-system-packages`). It does **NOT** install `imap-tools` or
  `cryptography`. → `imaplib` / `smtplib` / `email` (stdlib) are always available;
  `imap-tools` and/or `cryptography` (Fernet) would require adding them to the
  entrypoint pip line. (`mail_management` is currently `external_dependencies.python: []`.)

### Preflight finding (corrective — not part of the 6 areas, but blocks apply)

`security/security_groups.xml` still uses the **removed** Odoo 19 pattern:
`ir.module.category` + `res.groups` with `category_id`. Per the odoo-19
security guide, **Odoo 19 removed `category_id` from `res.groups`** and replaced
it with the `res.groups.privilege` bridge. The sibling
(`message_management_ai/security/message_ai_security.xml`) uses the correct
3-piece pattern (`ir.module.category` → `res.groups.privilege` → `res.groups.privilege_id`).
→ `mail_management`'s groups must be migrated to the privilege pattern before/at
install; otherwise group assignment from the user form is broken on Odoo 19.

---

# AREA 1 — AI PROVIDER REUSE (highest leverage) ✅ high confidence

## Findings (read directly from the sibling)

The sibling's AI layer is split into a deliberately **Odoo-model-free `lib/`
package** and thin Odoo models that implement a duck-typed contract. The seam is
designed for reuse.

### 1a. The provider interface — `../message_management_ai/lib/ai_provider.py`

- `AIProvider` (ABC) with one abstract method:
  `generate(system_instruction, contents, generation_config=None) -> AIResponse`.
- `@classmethod for_config(cls, config)` **factory**: dispatches on
  `config.provider`, reads `config.model_name` and `config._get_provider_api_key()`,
  and constructs the concrete provider. **Duck-typed** — any object exposing
  `provider`, `model_name`, `_get_provider_api_key()` works; the package has
  **no Odoo model imports**.
- `AIResponse` dataclass: `text`, `total_tokens`, `latency_ms`, `raw`.
- `ProviderError(message, status_code=None, *, retryable=False)` — carries a
  classified `retryable` flag.
- `_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})`.
- `@classmethod classify_error(exc)` → `"retryable"` | `"terminal"`.

### 1b. The concrete client — `../message_management_ai/lib/gemini_provider.py`

- `GeminiProvider(AIProvider)` (`__init__(model_name, api_key, timeout=30)`).
- POSTs to `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`.
  - Payload map: `system_instruction → systemInstruction`,
    `contents` verbatim, `generation_config → generationConfig`.
  - Auth: `x-goog-api-key` header only (key never in body).
- `generate()` raises `ProviderError(retryable=…)` on HTTP ≥400 / Timeout /
  ConnectionError; returns `AIResponse` on success.
- `_extract_text` parses `candidates[0].content.parts[0].text` (empty on safety-block);
  `_extract_tokens` parses `usageMetadata.totalTokenCount`.
- Default model `gemini-1.5-pro`; `provider` Selection supports `gemini`/`openai`
  (only Gemini is implemented).

### 1c. Per-company config — `../message_management_ai/models/message_ai_config.py`

`message.ai.config` owns the runtime knobs and is the model the factory reads:

- Fields: `provider` (gemini/openai), `model_name`, `persona`, `business_context`,
  `system_prompt_template`, `product_limit`, `history_turns`, `temperature`,
  `conversation_timeout_hours`, `cron_batch_size`, `max_retries`, `company_id`
  (`unique(company_id)` constraint — one config per company).
- **Secrets bridge** (the pattern to mirror): API key lives in
  `ir.config_parameter` (`message_ai.gemini_api_key`), read via
  `_get_provider_api_key()` under `sudo()`. The key is **never** stored on the
  model and **never** persisted into message fields.

### 1d. Conversation/FSM — `../message_management_ai/models/message_ai_conversation.py`

- `message.ai.conversation` (`_inherit = ["mail.thread","mail.activity.mixin"]`),
  `(channel_id, chat_id)` unique. FSM `new→greeting→qualifying→{qualified|disqualified}→handed_off/closed`
  enforced authoritatively in `write()` via an allow-list
  (`_ALLOWED_STATE_TRANSITIONS`). **The FSM is WhatsApp-lead-qualification
  specific** (greeting/qualifying/disqualified) — not generically reusable as-is.

### 1e. Orchestration pattern — `../message_management_ai/models/message_ai_message.py`

- `message.ai.message` is a per-inbound **queue row + audit log**; an `ir.cron`
  worker `_cron_drain_pending()` drains `pending` rows: assemble prompt →
  `AIProvider.for_config(config).generate(...)` → send via transport → flip status.
- Failure routing `_route_processing_failure(exc)` maps `classify_error` →
  retry / dead-letter / failed, capped by `config.max_retries`.
- **This pattern (cron queue → provider → transport → FSM) is directly reusable**
  for email AI tasks (draft/summarize/improve).

### 1f. Proven unit-testability

`../message_management_ai/tests/test_ai_provider.py` drives the provider with a
**`_StubConfig`** (a plain Python object implementing the duck-typed contract,
no DB) and `unittest.mock` on `requests.post`. The `lib` package is imported as
`from odoo.addons.message_management_ai.lib import AIProvider, AIResponse,
GeminiProvider, ProviderError`. → The provider abstraction is verifiably
decoupled and independently testable.

## What is reusable vs not

| Sibling asset | Reuse for email? | Why |
|---|---|---|
| `lib/ai_provider.py` (AIProvider/AIResponse/ProviderError/for_config/classify_error) | **YES — reuse** | Generic, Odoo-free, ~100 lines, the model-agnostic seam |
| `lib/gemini_provider.py` (GeminiProvider) | **YES — reuse** | Generic Gemini REST client, ~95 lines |
| Provider secret pattern (`ir.config_parameter` + `_get_provider_api_key()`) | **YES — reuse the pattern** | Same one-key-per-company story fits AI config; but mailbox IMAP creds are per-record (Area 3) |
| Cron-queue + error-routing orchestration (`message.ai.message._cron_drain_pending` / `_route_processing_failure`) | **YES — reuse the pattern** | Draft/summarize/improve are async AI tasks with the same retry/dead-letter needs |
| `lib/prompt_assembly.py` | **NO** | Real-estate catalog + qualification-marker extraction; email needs its own (draft/summarize/improve) prompts |
| `lib/whatsapp_cloud_client.py` | **NO** | WhatsApp Cloud API transport; irrelevant to email |
| `message.ai.config` model | **COPY the shape, not the model** | Same fields (provider/model/temperature/history), but it must be a separate `mail.ai.config` (or extend) — do not couple email to a WhatsApp config |
| `message.ai.conversation` FSM | **NO (shape only)** | States are lead-qualification specific; email threads have different lifecycle |

## Options

- **A. Copy the two provider files** (`ai_provider.py` + `gemini_provider.py`,
  ~195 lines total) into `mail_management/lib/`, plus a new `mail.ai.config`
  model implementing the duck-typed contract. Zero cross-module coupling;
  accept minor duplication. Tests copy cleanly with `_StubConfig`.
- **B. Depend on `message_management_ai`** and import
  `from odoo.addons.message_management_ai.lib import AIProvider, …`. Zero
  duplication, but it makes the **email module depend on the WhatsApp module**
  — wrong-direction coupling, and forces both to be installed together.
- **C. Extract a shared provider package** into a new tiny base module
  (e.g. `common_ai_provider`) that both depend on. Cleanest long-term, but
  requires refactoring the sibling now (scope creep into another repo).

## 🟢 RECOMMENDED DIRECTION (Area 1)

**Reuse the provider client; build an email-specific prompt + config layer.**

Concretely: **Option A for v1** (copy `ai_provider.py` + `gemini_provider.py`,
keep the duck-typed contract byte-for-byte so a stub-config test still passes),
with a new `mail.ai.config` model mirroring the sibling's fields
(`provider`/`model_name`/`temperature`/`history_turns`/`max_retries`) and its own
`_get_provider_api_key()` reading `ir.config_parameter`. Build a **new**
`mail_management/lib/mail_prompt_assembly.py` for the email tasks
(draft-reply, summarize-thread, improve-reply, extract-context) using the same
pure-function, Odoo-free pattern. Reuse the **cron-queue + error-routing
orchestration pattern** for asynchronous AI tasks.

If/when the team wants to kill the duplication, **Option C** (shared base module)
is the target architecture — flag it as a follow-up, not a v1 dependency, to keep
this change self-contained. **Reject Option B** (coupling email → WhatsApp).

## Risks (Area 1)

- **Duplication drift:** two copies of the provider can diverge. Mitigation:
  identical contract + a shared test contract; plan Option C follow-up.
- **Model defaults:** `gemini-1.5-pro` is the sibling default; email tasks
  (drafting, summarizing) may want a different/cheaper model — make `model_name`
  configurable per AI task or per config, not hardcoded.
- **Gemini-only today:** `provider` Selection lists `openai` but only Gemini is
  implemented. Email inherits the same single-provider limitation unless extended.

---

# AREA 2 — IMAP/SMTP PATH IN ODOO 19 [skill-ref + well-established]

## Findings

Two candidates were evaluated.

### (a) Odoo native mail framework [well-established + skill-ref]

- **`fetchmail.server`** (the `fetchmail` addon): Odoo's native inbound mail
  fetcher. It is **RECEIVE-ONLY** (POP3 or IMAP) and routes each inbound mail to
  a target model by invoking `mail.thread.message_new` / `message_update` via a
  mail alias / fetchmail object mapping (odoo-19 mixins guide: `message_new(msg_dict,…)`).
  It is designed to **convert inbound email into Odoo business records**
  (leads, tickets, helpdesk), not to be a mailbox client.
- **What fetchmail does NOT do:** send mail, expose a folder tree (Inbox/Sent/
  Drafts/Trash/custom), mirror IMAP flags (`\Seen`, `\Answered`, `\Flagged`,
  `\Deleted`), move/copy messages between folders, populate a "Sent" folder, or
  keep Odoo state bidirectionally in sync with the IMAP server. These are core
  requirements of a Roundcube/SnappyMail-parity webmail.
- **`mail.thread` mixin** (skill-ref): gives `message_post`, `message_new`,
  `message_update`, followers, chatter, tracking, `message_subscribe`. Posting
  with `subtype_xmlid='mail.mt_comment'` / `message_type='email'` **sends** mail
  via Odoo's own SMTP queue (`ir.mail_server` → `mail.mail`).
- **`ir.mail_server`** (well-established): global SMTP server config used by
  `mail.mail` to send. It is **not per-user** and does not place sent mail into
  the sender's IMAP "Sent" folder.
- **Net:** Odoo's native stack provides *inbound→record* (fetchmail) and
  *outbound SMTP* (ir.mail_server/mail.mail), but **not** the per-user IMAP
  mailbox semantics a webmail needs. The odoo-19 mixins guide confirms
  `message_new`/`message_update` are the only inbound hooks — there is no
  "list folders / set \Seen / sync Sent" API in `mail.thread`.

### (b) Dedicated Python IMAP/SMTP client inside the module

- **stdlib `imaplib` / `smtplib` / `email`**: always present in the `odoo:19.0`
  image (no pip change). Sufficient for a full webmail: LIST folders, SELECT,
  SEARCH by UID, FETCH (RFC822 + FLAGS, BODYSTRUCTURE), STORE (flag changes),
  APPEND (to "Sent"), EXPUNGE, SMTP send. Verbose but dependency-free.
- **`imap-tools`**: a friendlier wrapper over imaplib (`MailBox`, `folder.list()`,
  `fetch(mark_seen=False)`, `flag`, `append`). Cleaner code, but **not installed**
  — would require adding `imap-tools` to the web entrypoint pip line (like the
  sibling already extends pip for `pyjwt`/`python-docx`/`pdf2docx`).
- `smtplib` (stdlib) covers sending (AUTH LOGIN/PLAIN/CRAM-MD5, STARTTLS), with
  `email.message.EmailMessage` for MIME construction (multipart, attachments,
  `In-Reply-To`/`References` for proper threading/reply).

## Options

- **A. Dedicated stdlib client** (`imaplib` + `smtplib` + `email`): zero new
  dependency, full control, more boilerplate, must handle UIDVALIDITY/UID
  tracking and MIME parsing yourself.
- **B. Dedicated `imap-tools` + stdlib `smtplib`**: cleaner IMAP code, one new
  pip dep (add to entrypoint), still full control.
- **C. Build on Odoo native (`fetchmail` + `ir.mail_server` + `mail.thread`)**:
  least new code, but **cannot meet the webmail requirements** (no folders, no
  flag sync, no Sent-folder population). Suitable only for an "email→record"
  feature, not a webmail.

## 🟢 RECOMMENDED DIRECTION (Area 2)

**Use a dedicated Python IMAP/SMTP client (Option A or B) for mailbox I/O, and
REUSE `mail.thread` only for AI conversation/chatter tracking — not for mailbox
state.**

Odoo's native mail gateway is receive-only and lacks folders/send/flags, so it
cannot be the backbone of a webmail. Prefer **Option B (`imap-tools` + stdlib
`smtplib`)** for cleaner IMAP code, accepting one new pip dep in the entrypoint
(consistent with how the sibling already extends pip). Fall back to **Option A
(stdlib only)** if the team wants zero new dependencies. In either case:

- `mail.thread`/`mail.activity.mixin` are still inherited on the
  **conversation/thread model** (for AI chatter, activities, followers) and on
  the **AI-task queue model** (audit), exactly as the sibling does — but the
  mailbox I/O layer is a dedicated client, not `mail.thread`.
- Each user's mailbox is configured per-record (host/port/user/cred), not via
  the global `ir.mail_server`/`fetchmail.server`.

## Risks (Area 2)

- **IMAP server variance:** folder naming (`INBOX` vs `Inbox`), `XLIST`/`SPECIAL-USE`,
  UTF-7 folder names, flag support, IDLE availability vary wildly across
  providers (Gmail, Outlook, Dovecot, Courier). Need per-provider testing.
- **UIDVALIDITY changes:** if a folder is recreated, UIDs reset; sync state must
  detect and recover (re-fetch). [VERIFY-AGAINST-IMAGE] confirm no Odoo helper exists.
- **`imap-tools` not in image:** must edit `docker-compose.yml` entrypoint pip
  line; the sibling's pattern shows this is accepted practice here.
- **Threading correctness:** replying must set `In-Reply-To` + `References` from
  the original `Message-ID`, and APPEND a copy to the account's "Sent" folder.

---

# AREA 3 — CREDENTIAL STORAGE & ENCRYPTION 🔴 SECURITY CRITICAL

> This is the **single highest-risk area** of the change. Storing IMAP passwords
> / OAuth tokens for many users' mailboxes is a credentials-at-rest problem. A
> breach = read access to every connected mailbox. Lock the design here before
> any credential touches the DB.

## Findings

- **Per-record, per-user secrets:** unlike the sibling's single company-wide
  Gemini API key (which fits `ir.config_parameter`), webmail credentials are
  **one set per mailbox per user** — `ir.config_parameter` (global key/value)
  is the wrong shape for this. We need field-level storage on a mailbox model.
- **`Char` + `password=True` (the sibling's `access_token` approach):**
  `message.ai.channel.access_token = fields.Char(password=True, groups="base.group_system")`
  — masks in the UI and restricts field access by group, but stores the value
  **plaintext in the DB** (see Area 1c / `message_ai_channel.py`). Acceptable
  only for low-blast-radius secrets; **not acceptable as-is for IMAP passwords**
  because DB-level access (backup, dump, SQL) exposes every mailbox password.
  Note the sibling also overrides `_valid_field_parameter` to whitelist the
  `password` attribute (Odoo 19 escape hatch) — that pattern would be reused.
- **`fields.Encrypted` [VERIFY-AGAINST-IMAGE]:** the odoo-19 **field-guide does
  NOT list an `Encrypted` field type** in its type summary (Boolean/Char/Float/
  Integer/Binary/Html/Image/Monetary/Selection/Text/Date/Datetime/relational/
  Reference/Many2oneReference). The config.yaml names `fields.Encrypted` as a
  candidate, but its presence/behaviour in Odoo 19 **Community** could not be
  confirmed from the skill refs and the source is not on the host. **This must
  be verified against the running image before the design locks.** If a stock
  encrypted field exists and is stable, it is the cleanest option; otherwise
  application-level encryption is required.
- **`res.config.settings` vault pattern [well-established]:** Odoo's settings
  wizard is the conventional place to hold a master key / system secret; the
  per-record ciphertext then lives on the mailbox model. The sibling already
  uses `ir.config_parameter` (via `sudo()`) as its secrets store.
- **OAuth2 vs app-passwords:** Gmail and Outlook support **OAuth2** (scoped,
  **revocable** refresh tokens) — far better than long-lived passwords.
  Providers without OAuth require **app-specific passwords** (Gmail requires
  app passwords now that Less-Secure-Apps is gone). Storing a refresh token
  still needs encryption-at-rest, but it is revocable and scoped.

## Options

- **A. Stock `fields.Encrypted`** (if it exists & is stable in Odoo 19 Community):
  least code; relies on Odoo's own key management. [VERIFY-AGAINST-IMAGE]
- **B. App-level symmetric encryption** (`cryptography.fernet.Fernet`) with the
  master key held in `ir.config_parameter`/`res.config.settings`; store the
  **ciphertext** in a `Char` field; decrypt only in-memory at connect time;
  never log/serialize the plaintext. `password=True` for UI masking +
  `groups="mail_management.group_mail_management_manager"` field access + record
  rules so a user sees only their own mailbox. Adds a `cryptography` pip dep.
- **C. Plaintext `Char(password=True, groups=…)` only** (sibling's approach):
  simplest, but **reject for mailbox passwords** — DB-dump exposure of every
  connected mailbox is unacceptable.
- **D. OAuth2 where supported** (Gmail/Outlook): store the **refresh token**
  (encrypted, per B), exchange for short-lived access tokens; fall back to
  app-passwords (per B) for providers without OAuth.

## 🟢 RECOMMENDED DIRECTION (Area 3)

**Layered credential model, encryption-at-rest mandatory:**

1. **Verify `fields.Encrypted` in the image first.** If present & stable in Odoo
   19 Community → use it (Option A). Otherwise → **Option B**: Fernet
   (`cryptography`, add to entrypoint pip) with the master key in
   `res.config.settings`/`ir.config_parameter`; ciphertext in the mailbox field;
   plaintext decrypted in-memory only at connect time; **never** written to
   `request_json`/logs (mirror the sibling's `_redact_token` defence-in-depth).
2. **Prefer OAuth2 (Option D) for Gmail/Outlook** — store the encrypted refresh
   token; use app-passwords (still encrypted) for providers without OAuth.
3. **Defence-in-depth, regardless of storage:** `password=True` UI masking,
   `groups`-restricted field access (only the mailbox owner + managers can read
   the secret field), `ir.rule` record rule `('user_id','=',user.id)` so a user
   sees only their own mailboxes, and `_redact_token`-style scrubbing on any
   audit/payload field.
4. **Reject Option C** (plaintext-only) for mailbox credentials.

## Risks (Area 3) — PROMINENT

- 🔴 **Credentials at rest = top risk of the whole change.** Any plaintext
  storage makes a DB dump = full mailbox takeover. Encryption-at-rest is mandatory.
- 🔴 **Master-key management:** if the Fernet key lives in `ir.config_parameter`,
  anyone with DB read gets both ciphertext and key. Consider an env-var /
  file-based key mounted into the container (Docker secret) rather than a DB row.
- **`fields.Encrypted` uncertainty:** if it does not exist in Community, Option B
  adds a `cryptography` dependency and bespoke encrypt/decrypt code (more surface
  to get wrong). [VERIFY-AGAINST-IMAGE in spec phase.]
- **OAuth2 refresh-token refresh races & expiry:** need a token-refresh path with
  re-auth prompts; more state than a password.
- **Field-level access + record rules must be airtight:** a missing `ir.rule`
  would expose other users' mailboxes. Unit-test isolation (like the sibling's
  multi-company rules).

---

# AREA 4 — MESSAGE PARSING (MIME / attachments / threading)

## Findings

### What `mail.thread` already gives [skill-ref + well-established]

- **Inbound parsing seam:** `mail.thread.message_new(msg_dict, custom_values)`
  and `message_update(msg_dict,…)` receive a parsed `msg_dict`. Odoo's
  `tools.mail` (`email_message_parse` / `email_split` / `decode_smtp_header`,
  built on Python's `email` module) already turns a raw RFC822 message into a
  dict with `subject`, `body` (sanitized HTML), `from`/`to`/`cc`, `date`,
  `attachments`, `references`, `in_reply_to`, `message_id`, and `headers`.
- **Threading:** `mail.thread` derives `parent_id` from `In-Reply-To`/`References`
  and dedups by `message_id`. So **conversation threading by headers is already
  solved at the record level** by the framework.
- **Attachments:** inbound attachments become `ir.attachment` records linked to
  the thread.

### What a webmail needs beyond `mail.thread`

- **Full MIME structure preservation:** `mail.thread` collapses a message to a
  single sanitized HTML body. A webmail must preserve `multipart/alternative`
  (text/html), nested multipart, inline images (CID), calendar invites
  (`text/calendar`), RFC822 message-as-attachment, and offer a raw-source view.
- **Rich rendering with sanitization:** must render arbitrary untrusted HTML
  safely (the odoo-19 security guide is explicit: avoid `t-raw`; use `t-out`;
  use `odoo.tools.html_sanitize` / `Markup` escaping). Remote image blocking and
  attachment download are webmail staples.
- **Raw header access:** webmail shows `Received`, `DKIM-Signature`, `Return-Path`,
  etc. `mail.thread` keeps a flattened `headers` blob; a webmail wants structured
  header access.
- **Per-folder threading:** `mail.thread` threads across the whole record set;
  a webmail threads within the current folder/view by `References`/`In-Reply-To`.

## Options

- **A. Reuse Odoo's `tools.mail` parse helpers** for header/date/attachment
  extraction (don't reinvent RFC822 parsing), but store the **raw RFC822** (or
  parsed `Message` parts) on a dedicated webmail message model and **render
  client-side** (sanitized) from the preserved structure.
- **B. Parse everything fresh with Python's `email` module** on the dedicated
  client, ignoring `tools.mail`. More control, more code, re-implements what
  Odoo already wraps.
- **C. Rely solely on `mail.thread` storage:** store webmail mail as
  `mail.message` rows and render from `mail.message.body`. Simplest, but loses
  MIME structure / raw headers / per-folder threading fidelity.

## 🟢 RECOMMENDED DIRECTION (Area 4)

**Hybrid: dedicated webmail message model that stores the raw/parsed MIME, using
Odoo's `tools.mail` helpers for the hard parsing and rendering sanitized HTML.**

- Reuse `tools.mail` (email_message_parse, html_sanitize) for header/date/
  attachment extraction — don't reimplement RFC822.
- Keep a **dedicated message model** (`mail.management.message`) storing
  `message_id`, `in_reply_to`, `references`, `subject`, sanitized `body_html`,
  raw `headers`, attachment pointers, and IMAP linkage (`mailbox_id`, `uid`,
  `flags`). Render the reading pane from `body_html` (sanitized) with attachment
  list; offer raw-source view from `headers`.
- Thread within a folder using `references`/`in_reply_to` (build a thread tree
  per folder), independent of `mail.thread`'s global threading.
- v1 can collapse `multipart/alternative` to the HTML part; defer exotic
  multipart/calendar rendering to a later phase.

## Risks (Area 4)

- **Untrusted HTML XSS:** rendering arbitrary sender HTML is dangerous; must
  always sanitize (`html_sanitize`) and never `t-raw` (security guide is explicit).
- **Encoding edge cases:** RFC2047 encoded-words, charset mismatches, malformed
  MIME — Python's `email` + Odoo's helpers handle most, but corner cases will
  surface; budget for it.
- **Large attachments / messages:** storing raw RFC822 can bloat the DB; prefer
  `ir.attachment` (filestore) for bodies/attachments over DB blobs.
- **Attachment fidelity:** inline-vs-attachment disposition, CID image rendering
  need care.

---

# AREA 5 — WEBMAIL UI APPROACH (OWL) 🟠 large surface / schedule risk

## Findings (odoo-19 OWL guide)

- OWL is Odoo 19's UI framework: `Component`, `setup()`, `useState`, lifecycle
  hooks (`onWillStart`/`onMounted`/…), services (`orm`/`rpc`/`action`/`dialog`/
  `notification`/`router`/`user`), QWeb templates (`t-out`, `t-foreach`, `t-if`,
  `t-on-click`), and the `registry.category("actions")` registration that lets a
  component be opened as a client action. The guide's "split components, keep
  them small" guidance is directly relevant.
- A webmail is not one component — it is a **multi-pane application**: folder
  tree, message list (sort/pagination/unread counts), reading pane (sanitized
  HTML + attachments), compose (rich text + attachments + reply/forward), and
  search. This is an entire sub-application, comparable in surface to Roundcube.
- The module's `controllers/main.py` is an **empty scaffold** (`MailManagementController`
  with `pass`) ready for the JSON-RPC endpoints the OWL client will call
  (list-folders, fetch-messages, fetch-body, send, set-flags, move).
- `assets: {}` in the manifest is empty — OWL bundles must be declared here.

## Options

- **A. Full custom OWL webmail** (folder tree + list + reading pane + compose):
  maximum fidelity, **by far the largest effort** of the whole change.
- **B. OWL client on top of Odoo backend views** (kanban/list for message list,
  form for reading, chatter-style compose): faster to ship, but it is a
  "record manager", not a webmail UX — no 3-pane, no real-time folder feel.
- **C. Phased OWL:** v1 = a minimal 3-pane (folder tree + list + reading pane +
  plain-text reply/forward) as a client action; defer search/filters/move/rich
  compose to later phases.

## 🟢 RECOMMENDED DIRECTION (Area 5)

**Phased custom OWL (Option C), explicitly scoped down for v1.**

- v1 OWL client action: folder tree (Inbox/Sent/Drafts/Trash + provider folders),
  paginated message list (unread badges, sort by date), reading pane (sanitized
  HTML + attachment list), and plain-text Reply / Reply-All / Forward via SMTP +
  APPEND-to-Sent. Endpoints live in `controllers/main.py` (JSON-RPC).
- Defer to later phases: full-text search, mail filters/rules, drag-to-move,
  rich-text compose, contacts/calendar, keyboard shortcuts, multiple accounts UI.
- **Risk callout:** this single area is likely **~60–70% of total effort** and
  will exceed the 400-line review budget per phase → it **must be split into
  chained PRs** (folder tree; list; reading pane; compose; AI panel). Do not
  attempt the whole UI in one apply phase.

## Risks (Area 5) 🟠

- **Schedule/risk dominant:** the UI is the biggest unknown and the most likely
  to overrun. Mitigate by aggressive phasing + chained PRs.
- **Review-budget blow-up:** one monolithic UI PR will blow the 400-line budget;
  plan the split now.
- **OWL ergonomics:** real-time unread counts / new-mail refresh need either
  polling or a bus channel (Odoo `bus`/longpoll on `gevent_port 8072`); the
  worker model (Area 6) constrains push options.
- **Rendering untrusted content** (ties to Area 4): XSS surface inside the OWL
  reading pane.

---

# AREA 6 — SYNC STRATEGY (cron-pull vs IMAP IDLE vs on-demand)

## Findings & constraints

- **Hard constraint:** `workers = 4` (prefork). Odoo cron jobs and HTTP requests
  run inside this worker pool. A **long-lived blocking connection (IMAP IDLE)
  pins one worker per mailbox** for the whole IDLE session — with 4 workers,
  that is an untenable scaling model and fights the prefork lifecycle (workers
  are recycled; long sockets get killed). `ir.cron` is the native fit for the
  worker model.
- **(a) Cron-pull (fetchmail style):** periodic `ir.cron` SELECT/SEARCH/FETCH of
  new UIDs since last sync, tracked per mailbox+folder with UIDVALIDITY. Fits
  `workers=4`; simple; no long connections; naturally batched (like the sibling's
  `_cron_drain_pending`). Cost: poll latency (minutes), must track `last_uid` +
  `uidvalidity` per folder, idempotent de-dup by `message_id`.
- **(b) IMAP IDLE:** near-real-time push, but **blocks a worker per mailbox** and
  needs a dedicated long-running process/thread model outside Odoo's prefork
  workers (e.g. a busyo/sidecar). IDLE also times out (~29 min) and must be
  re-armed; flag changes need re-IDLE. **Poor fit for `workers=4`.**
- **(c) On-demand fetch:** fetch the folder list/message bodies only when the
  user opens the webmail. Zero background cost, always fresh on open; but slow
  first-load, no background sync, no proactive AI tasks/notifications, no unread
  badge without polling.

## Options

- **A. Cron-pull backbone** (UID-incremental, per-mailbox+folder, idempotent) +
  **on-demand refresh** when the user is actively viewing a folder. Defer IDLE.
- **B. IMAP IDLE** for real-time: requires a dedicated long-running sidecar
  outside the worker pool.
- **C. On-demand only:** simplest, but no background sync / AI / notifications.

## 🟢 RECOMMENDED DIRECTION (Area 6)

**Hybrid A: cron-pull as the sync backbone + on-demand refresh for the live UI.
Defer IMAP IDLE.**

- `ir.cron` (mirroring the sibling's cron pattern) drains each mailbox
  incrementally by UID (store `last_uid` + `uidvalidity` per folder), persists
  new messages into the webmail message model, and seeds AI tasks (summary/
  draft) as needed. This fits `workers=4` cleanly and is idempotent.
- The OWL client triggers an on-demand `refresh_folder` when the user opens/it,
  for low-latency "live" feel without a background push channel.
- **Defer IMAP IDLE** to a future phase; if real-time becomes mandatory, it needs
  a dedicated sidecar process (busyo/asyncio) outside the Odoo prefork pool —
  out of scope for v1.

## Risks (Area 6)

- **Worker exhaustion:** if any sync path blocks too long (large mailbox, slow
  IMAP), it starves the 4-worker pool. Cron batches must be bounded
  (`cron_batch_size`, like the sibling) and per-tick timeouts enforced.
- **De-dup correctness:** `message_id` + UID dedup must be airtight across
  folders/providers; UIDVALIDITY changes require a full re-fetch.
- **Latency vs freshness trade-off:** cron-pull is minutes-level; on-demand
  mitigates for the active user but background notifications stay cron-paced.
- **Concurrency:** two cron ticks or cron+on-demand hitting one mailbox must not
  double-insert; rely on the `(mailbox_id, uid)` / `message_id` unique constraint.

---

# CONSOLIDATED — RECOMMENDED TECHNICAL DIRECTION

| Area | Direction | Confidence |
|---|---|---|
| 1. AI provider | **Reuse** `ai_provider.py` + `gemini_provider.py` (copy for v1, shared-module later); new email `prompt_assembly`; new `mail.ai.config` mirroring the duck-typed contract; reuse cron-queue + error-routing orchestration | High |
| 2. IMAP/SMTP | **Dedicated Python client** (`imap-tools` + stdlib `smtplib`); reuse `mail.thread` only for AI chatter, NOT mailbox state | High |
| 3. Credentials 🔴 | **Encryption-at-rest mandatory** (verify `fields.Encrypted`; else Fernet + vault key); prefer **OAuth2 refresh tokens** for Gmail/Outlook; `password=True` + `groups` field access + per-user `ir.rule`; reject plaintext | High (design), medium on `fields.Encrypted` availability |
| 4. Parsing | Dedicated webmail message model storing raw/parsed MIME; **reuse `tools.mail`** for the hard parsing; sanitize all rendered HTML | High |
| 5. UI | **Phased custom OWL** client action; v1 = folder tree + list + reading pane + plain-text reply/forward; defer search/filters/rich/move | High (direction), **high schedule risk** |
| 6. Sync | **Cron-pull backbone + on-demand refresh**; defer IMAP IDLE; bounded cron batches for the 4-worker pool | High |

**Shape of the new models (proposal starting point — not final):**

- `mail.management.mailbox` — per-user IMAP/SMTP config (host/port/user/
  encrypted-cred/oauth, SMTP host/port, company_id, user_id, last_uid,
  uidvalidity). Owns the dedicated IMAP/SMTP client.
- `mail.management.folder` — IMAP folder per mailbox (name, uidvalidity,
  last_uid, unread_count, message_count).
- `mail.management.message` — webmail message (mailbox_id, folder_id, uid,
  message_id, in_reply_to, references, subject, body_html, headers, flags,
  attachments). Threading via `references`/`in_reply_to` per folder.
- `mail.ai.config` — per-company AI provider settings (mirror sibling contract:
  provider/model_name/temperature/history_turns/max_retries + `_get_provider_api_key()`).
- `mail.ai.task` (or extend the message model) — async AI task queue
  (draft/summarize/improve) with the sibling's retry/dead-letter routing.
- `controllers/main.py` — JSON-RPC endpoints for the OWL client (list-folders,
  fetch-messages, fetch-body, send, set-flags, refresh, ai-actions).

---

# TOP RISKS TO RESOLVE IN PROPOSAL / SPEC / DESIGN

1. 🔴 **Credential-at-rest (Area 3)** — the #1 risk. Lock the encryption model
   (verify `fields.Encrypted` vs Fernet) and master-key location (DB vs mounted
   secret) in design before any credential is stored. Test isolation of secrets.
2. 🟠 **UI scope/schedule (Area 5)** — the largest effort; must be split into
   chained PRs to fit the 400-line review budget. Define the **v1 first-slice
   boundary** (open question Q3) explicitly in proposal.
3. 🟠 **`fields.Encrypted` availability in Odoo 19 Community [VERIFY-AGAINST-IMAGE]**
   — unverified; affects the whole credential design. Resolve in spec by reading
   the running container source.
4. **Worker-pool pressure (Area 6)** — sync/parse must be batch-bounded and
   timeout-guarded to avoid starving `workers=4`; IMAP IDLE is out of scope for v1.
5. **IMAP provider variance (Area 2)** — folders/flags/IDLE/UTF-7 differ across
   Gmail/Outlook/Dovecot/Courier; need a provider test matrix.
6. **Untrusted-content XSS (Areas 4 & 5)** — every rendered message body must be
   sanitized; never `t-raw`; inline images/CID and attachment downloads need care.
7. **AI-provider packaging/drift (Area 1)** — copy vs shared-module; identical
   contract required; plan dedupe follow-up.
8. **`security_groups.xml` migration (preflight)** — current file uses the removed
   `res.groups.category_id`; must migrate to `res.groups.privilege` before install.
9. **Threading/Sent fidelity (Areas 2 & 4)** — replies must set `In-Reply-To`/
   `References` and APPEND to "Sent"; UIDVALIDITY changes must not corrupt sync.

## Direct answers to `open_questions_for_proposal`

- **Q1 IMAP/SMTP path:** dedicated Python client (prefer `imap-tools` + stdlib
  `smtplib`); Odoo native (`fetchmail`/`ir.mail_server`) is receive-only and
  cannot be the webmail backbone. `mail.thread` reused for AI chatter only.
- **Q2 AI provider reuse:** **YES — reuse** `ai_provider.py` + `gemini_provider.py`
  (Odoo-free, duck-typed, unit-tested); do NOT reuse `prompt_assembly`
  (real-estate) or the WhatsApp client; new email `prompt_assembly` + `mail.ai.config`.
- **Q3 First-slice scope:** v1 = connect mailbox + folder tree + Inbox read + list +
  plain-text reply/forward + AI draft-reply/summarize. Defer: search, filters,
  move/copy, rich compose, calendar/contacts, multiple-account UX.
- **Q4 Credentials:** encryption-at-rest mandatory (verify `fields.Encrypted`;
  else Fernet + vault key); prefer OAuth2 refresh tokens; `password=True` +
  `groups` + per-user `ir.rule`; reject plaintext.
- **Q5 Sync:** cron-pull backbone (UID-incremental, idempotent) + on-demand
  refresh; defer IMAP IDLE (needs a sidecar outside `workers=4`).
- **Q6 Non-goals v1:** calendar, contacts, full MIME edge-case rendering,
  send-later, mail filters/rules, IMAP IDLE real-time push, rich-text compose,
  cross-folder search.

## Items explicitly deferred to PROPOSAL (not decided here)

- Exact packaging of the reused provider (copy vs shared base module).
- Final field-level encryption primitive (post `fields.Encrypted` verification).
- v1 UI feature cutoff and the chained-PR split.
- Whether OAuth2 is v1 or v1.1 (depends on target providers).
