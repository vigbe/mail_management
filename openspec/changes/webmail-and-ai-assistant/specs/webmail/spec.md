# Webmail Specification

> **SDD phase: SPEC (Slice 1 — Webmail core).** This is a **full new-domain spec**
> because no canonical `openspec/specs/webmail/spec.md` exists yet. Archive will copy it
> into the canonical location.
>
> **Scope.** This spec covers **Slice 1 (Webmail core) ONLY**. Slice 2 (the AI assistant
> layer — draft replies, summaries, reply improvement, conversation context) is the **next
> change** and is **explicitly out of spec** here (see §Non-goals).
>
> **Traceability.** Each requirement is tagged with the proposal PR sub-slice it belongs to
> (`PR-1a` … `PR-1e`) so the design/tasks/apply phases can split work by the chained-PR plan.
>
> **Resolved fact (locked this session).** Odoo 19 Community has **no `fields.Encrypted`
> type** (`hasattr(odoo.fields, 'Encrypted') == False`, confirmed by the orchestrator).
> Therefore credential encryption-at-rest MUST use an **external mechanism: Fernet
> (`cryptography` library) with a master key**. This is stated below as a decided
> requirement, not an open question. The proposal's §11 "verify fields.Encrypted" item is
> **CLOSED**.

## Purpose

Give each Odoo user a private, integrated webmail: connect one or more of their own
mailboxes (generic IMAP/SMTP, Gmail, or Microsoft 365), read mail (sanitised HTML +
attachments), reply / reply-all / forward, and sync folders — all inside Odoo, with
credentials stored encrypted at rest. This is the **email** domain; it is distinct from
the sibling `message_management_ai` (WhatsApp/Telegram). Slice 1 unlocks Slice 2 (AI).

---

## Requirements

### Requirement: Per-user mailbox configuration (PR-1a)

The system MUST allow an Odoo user (`res.users`) to create and configure one or more of
their own mailboxes. Each mailbox MUST capture, at minimum: a provider type, IMAP host,
IMAP port, IMAP user, an encrypted credential, SMTP host, and SMTP port. A mailbox MUST
default its `user_id` to the current user on creation. The combination
`(user_id, imap_host, imap_user)` MUST be unique to prevent duplicate mailbox records for
the same account.

#### Scenario: User connects a generic mailbox

- GIVEN an internal user with the webmail user privilege
- WHEN they create a mailbox with provider type `generic`, IMAP host/port/user, an
  app-password, and SMTP host/port
- THEN the mailbox is created with `user_id` set to that user and is persisted

#### Scenario: Duplicate mailbox for same account is rejected

- GIVEN a user already has a mailbox for `(imap_host=imap.example.com, imap_user=alice)`
- WHEN the same user tries to create another mailbox with the same
  `(user_id, imap_host, imap_user)`
- THEN the creation is rejected with a uniqueness-constraint error

#### Scenario: A mailbox belongs to exactly one user

- GIVEN user A and user B
- WHEN user A creates a mailbox
- THEN the mailbox's `user_id` is user A and is never reassigned to user B

### Requirement: Mailbox connection test (PR-1a)

The system MUST let a user verify IMAP and SMTP connectivity for a mailbox (with the
current credentials) before or after saving, and MUST report success or a clear, redacted
failure reason. The test MUST NOT expose the plaintext credential in the result or in any
log line.

#### Scenario: Successful connection test

- GIVEN a saved mailbox with valid IMAP/SMTP credentials
- WHEN the user runs the connection test
- THEN the system reports success for both IMAP login and SMTP AUTH

#### Scenario: Failed connection test is redacted

- GIVEN a saved mailbox with an invalid password
- WHEN the user runs the connection test
- THEN the system reports a connection failure whose message does not contain the plaintext
  password or any credential value

### Requirement: Credential encryption at rest (PR-1a) — CRITICAL

IMAP/SMTP app-passwords and OAuth2 refresh tokens MUST be stored **encrypted at rest** as
Fernet ciphertext. Plaintext credentials MUST be held **in-memory only** at connect time
and MUST NEVER be persisted in plaintext, written to logs, included in `request_json`,
shown in error text, or present in any serialised response payload. Plaintext-only storage
of mailbox credentials (e.g. a bare `Char(password=True)`) is explicitly **rejected**.

Because Odoo 19 Community has no `fields.Encrypted` type, the system MUST implement
encryption with **Fernet from the `cryptography` library** and a **master key**.

#### Scenario: Stored credential is ciphertext, not plaintext

- GIVEN a mailbox created with app-password `s3cret-app-pw`
- WHEN the credential column is read directly from the database
- THEN the stored value is Fernet ciphertext and does not contain the substring
  `s3cret-app-pw`

#### Scenario: Plaintext never leaves memory

- GIVEN a mailbox with an encrypted credential
- WHEN the mailbox is saved, connection-tested, or synced
- THEN no plaintext credential appears in Odoo logs, `request_json`, error messages, or
  serialised payloads (defence-in-depth redaction MUST scrub credential values)

#### Scenario: Encrypt/decrypt round-trip

- GIVEN a master key is available
- WHEN a credential is encrypted then decrypted with the same key
- THEN the decrypted value equals the original plaintext

### Requirement: Master key management (PR-1a)

The system MUST obtain the Fernet master key, in priority order, from: (1) the environment
variable `MAIL_MGMT_MASTER_KEY`; (2) the `ir.config_parameter` key
`mail_management.master_key`. The system MUST NOT store the master key on the mailbox
record. The env var is the preferred location to avoid ciphertext+key co-location on a
database dump.

For a clear bootstrap story: if neither the env var nor the config parameter holds a key
when encryption is first needed, the system MUST auto-generate a new Fernet key and persist
it to `mail_management.master_key` so the module is usable out of the box, and SHOULD
emit a warning that an env-var key is preferred. If the key is rotated or removed, the
system MUST surface a clear error when attempting to decrypt credentials encrypted under a
different/missing key.

#### Scenario: Master key read from env var first

- GIVEN `MAIL_MGMT_MASTER_KEY` is set in the environment and a config parameter also exists
- WHEN the system resolves the master key
- THEN it uses the env var value, not the config parameter value

#### Scenario: Fallback to config parameter

- GIVEN `MAIL_MGMT_MASTER_KEY` is not set and `mail_management.master_key` is set
- WHEN the system resolves the master key
- THEN it uses the config parameter value

#### Scenario: Auto-generate on first use

- GIVEN neither `MAIL_MGMT_MASTER_KEY` nor `mail_management.master_key` is set
- WHEN the system first needs to encrypt a credential
- THEN it generates a new Fernet key, persists it to `mail_management.master_key`, and the
  encryption succeeds

#### Scenario: Missing/changed key surfaces a clear error

- GIVEN credentials were encrypted under master key K1
- WHEN the available master key is K2 (rotated) or absent
- THEN decryption surfaces a clear, non-redacted-by-credential error indicating a key
  mismatch

### Requirement: Credential field access control (PR-1a)

The system MUST mask credential fields in the UI (`password=True`) and MUST restrict
read/write access to credential fields by Odoo security group so that only the mailbox
owner and mail-management managers can access the secret value. The `password` field
attribute MUST be whitelisted via the Odoo 19 `_valid_field_parameter` escape hatch where
required.

#### Scenario: Credential masked in the UI

- GIVEN a mailbox with a stored credential
- WHEN the owner opens the mailbox form
- THEN the credential field is masked (not displayed in plaintext)

#### Scenario: Credential field is group-restricted

- GIVEN a user without the mail-management manager/admin privilege who is not the owner
- WHEN they attempt to read a mailbox credential field
- THEN the access is denied (field removed / access error)

### Requirement: Per-user privacy and isolation (PR-1a) — CRITICAL

The system MUST enforce private-per-user isolation: an Odoo user MUST see and manage **only
their own** mailboxes, folders, and messages. Isolation MUST be enforced by an `ir.rule`
with domain `('user_id', '=', user.id)` on the mailbox, folder, and message models. There
MUST be no shared/team mailboxes in Slice 1. Cross-user leakage MUST be unit-tested (like
the sibling's multi-company isolation).

#### Scenario: User sees only their own mailboxes

- GIVEN user A and user B each have mailboxes
- WHEN user A lists mailboxes (as user A, without sudo)
- THEN user A sees only user A's mailboxes

#### Scenario: User cannot read another user's message

- GIVEN user B owns a message M
- WHEN user A attempts to read M by id (without sudo)
- THEN access to M is denied

#### Scenario: No shared mailbox in v1

- GIVEN any configuration
- WHEN a mailbox is created
- THEN the mailbox has exactly one `user_id` and is never visible to other users except via
  privileged manager access

### Requirement: Odoo 19 privilege-based security groups (PR-1a)

The module's `security_groups.xml` MUST define groups using the Odoo 19
`res.groups.privilege` bridge (`ir.module.category` → `res.groups.privilege` →
`res.groups.privilege_id`). It MUST NOT use the removed `res.groups.category_id` pattern.
Group assignment MUST work correctly on the Odoo 19 user form.

#### Scenario: Groups use the privilege bridge

- GIVEN the installed module
- WHEN its security groups are inspected
- THEN the groups reference `res.groups.privilege` and do not set the removed `category_id`

#### Scenario: Group assignment works on the user form

- GIVEN an administrator
- WHEN they grant the webmail user privilege to a user from the user form
- THEN the privilege is saved and the user gains the webmail access rights

### Requirement: Generic IMAP/SMTP provider works end-to-end first (PR-1a)

The generic IMAP/SMTP provider (app-password authentication) MUST be fully functional
end-to-end and testable with no non-code prerequisite, before the OAuth providers. IMAP I/O
MUST use the `imap-tools` library; SMTP I/O MUST use the stdlib `smtplib`. This pairing is
a fixed constraint of Slice 1 (stdlib-only IMAP fallback is documented but not required).

#### Scenario: Connect and authenticate with app-password

- GIVEN a generic mailbox with a valid app-password
- WHEN the system connects to the IMAP and SMTP servers
- THEN both authentications succeed using the decrypted in-memory credential

#### Scenario: Library constraint

- GIVEN the module's IMAP/SMTP client
- WHEN it performs mailbox I/O
- THEN IMAP operations use `imap-tools` and SMTP operations use `smtplib`

### Requirement: Folder discovery (PR-1b)

The system MUST discover and present the standard IMAP/SPECIAL-USE folders the server
advertises, including at least Inbox, Sent, and Trash. The system MUST record each folder's
SPECIAL-USE role. Custom-folder creation/management is deferred (non-goal).

#### Scenario: Standard folders discovered

- GIVEN a connected mailbox
- WHEN folders are listed
- THEN Inbox, Sent, and Trash are present and recognised by their SPECIAL-USE roles

#### Scenario: Provider folder-naming variance is tolerated

- GIVEN providers that name folders differently (`INBOX` vs `Inbox`) or advertise SPECIAL-USE
- WHEN folders are listed for any supported provider
- THEN the standard folders are correctly identified regardless of casing

### Requirement: Sync backbone — cron-pull (PR-1b)

The system MUST synchronise mail via an `ir.cron` periodic pull that fetches new messages
incrementally by IMAP UID per mailbox+folder, tracks `last_uid` and `uidvalidity` per
folder, and persists new messages into the webmail message model. The default cron interval
MUST be approximately 5 minutes (configurable). IMAP IDLE (long-lived push) is a non-goal
for Slice 1 because it would pin a worker per mailbox.

#### Scenario: Incremental UID fetch

- GIVEN a folder synced up to `last_uid=100`
- WHEN the cron runs again and the server now has UIDs 101–120
- THEN only messages with UIDs > 100 are fetched and stored

#### Scenario: Configurable cadence

- GIVEN an administrator
- WHEN they change the sync cron interval
- THEN subsequent sync frequency follows the configured interval (default ~5 min)

### Requirement: Sync idempotency (PR-1b)

Sync MUST be idempotent. Re-running sync for the same mailbox+folder MUST NOT create
duplicate messages. Deduplication MUST rely on a `(mailbox_id, uid)` uniqueness and the
RFC822 `message_id`. Concurrent runs (two cron ticks, or cron + on-demand on one mailbox)
MUST NOT double-insert.

#### Scenario: Re-sync does not duplicate

- GIVEN a folder already synced with UID 150 present
- WHEN sync runs again and UID 150 is still on the server
- THEN no duplicate message record is created for UID 150

#### Scenario: Concurrent sync is safe

- GIVEN two sync runs targeting the same mailbox concurrently
- WHEN both attempt to insert the same UID
- THEN only one message record is persisted (the other is rejected by the unique constraint)

### Requirement: UIDVALIDITY change recovery (PR-1b)

The system MUST detect a UIDVALIDITY change on a folder (the server recreated it and reset
UIDs) and recover without corrupting or duplicating message state. On a change, the system
MUST reset the folder's `last_uid`, re-fetch from scratch, and deduplicate by `message_id`
so that any user flags on still-present messages are preserved.

#### Scenario: UIDVALIDITY change triggers clean re-fetch

- GIVEN a folder with stored `uidvalidity=V1` and `last_uid=200`
- WHEN the server now reports `uidvalidity=V2` (changed)
- THEN the system resets `last_uid`, re-fetches from scratch, and deduplicates by
  `message_id` without duplicating or losing messages

#### Scenario: User flags preserved across re-fetch

- GIVEN a message with `message_id=M` had user flag `\Flagged` set before a UIDVALIDITY
  change
- WHEN the re-fetch finds `M` again under the new UIDs
- THEN the message record is not duplicated and the existing flags are preserved

### Requirement: Worker-safe sync (PR-1b)

Sync MUST be bounded and timeout-guarded so that no sync path pins an Odoo worker long-term
under `workers=4`. Each cron tick MUST process at most a configurable `cron_batch_size` and
MUST enforce per-operation timeouts on IMAP/SMTP I/O. Message bodies and attachments MUST
go to the Odoo filestore (`ir.attachment`), not DB blobs.

#### Scenario: Batch-bounded cron tick

- GIVEN a folder with many new messages
- WHEN a cron tick runs
- THEN it processes at most `cron_batch_size` messages and yields (remaining handled on
  subsequent ticks)

#### Scenario: I/O timeout enforced

- GIVEN a slow/unresponsive IMAP server
- WHEN a sync operation exceeds its timeout
- THEN the operation aborts cleanly without hanging the worker

### Requirement: On-demand folder refresh (PR-1b)

The system MUST support an on-demand refresh of a folder (fetch new mail immediately) when
the user opens or refreshes the folder in the webmail, in addition to the cron backbone.
On-demand refresh MUST remain idempotent and worker-safe.

#### Scenario: User-triggered refresh fetches new mail

- GIVEN a folder with new mail not yet seen by cron
- WHEN the user opens the folder in the webmail
- THEN new messages are fetched immediately and shown without creating duplicates

### Requirement: Reading a message (PR-1b)

The system MUST let a user open and read a message: fetch the body, parse the MIME
structure (reusing Odoo's `tools.mail` helpers for the hard parsing), render a sanitised
HTML body, list attachments, and provide a raw-header view. For `multipart/alternative`,
the system MUST render the HTML part (exotic multipart is a non-goal).

#### Scenario: Read renders parsed body

- GIVEN a stored message with an HTML body and attachments
- WHEN the user opens it in the reading pane
- THEN the parsed body is rendered and the attachment list is shown

#### Scenario: multipart/alternative rendered as HTML

- GIVEN a `multipart/alternative` message with text and HTML parts
- WHEN the message is read
- THEN the HTML part is rendered (not the plain-text part)

### Requirement: Untrusted content is sanitised on render (PR-1b) — SECURITY

Every rendered message body MUST be run through `odoo.tools.html_sanitize` and rendered with
`t-out` (or an equivalent safe escaping mechanism). The system MUST NEVER render untrusted
HTML with `t-raw`. Remote-image loading MUST be blocked by default and CID inline images
MUST be handled deliberately. This is a security invariant.

#### Scenario: Script tags are stripped

- GIVEN a message whose HTML body contains a `<script>` block
- WHEN the body is rendered in the reading pane
- THEN the `<script>` is removed by `html_sanitize` and no script executes

#### Scenario: Never t-raw for untrusted content

- GIVEN any untrusted message body rendered in the OWL UI
- WHEN the template renders the body
- THEN it uses `t-out`/safe rendering and never `t-raw`

#### Scenario: Remote images blocked, CID images handled

- GIVEN a message with a remote `<img src="https://...">` and a CID inline image
- WHEN the body is rendered
- THEN remote images are blocked and CID inline images are rendered from their attachment

### Requirement: Attachments on read (PR-1b)

The system MUST let a user download (and where feasible view) attachments on a read
message. Attachments MUST be stored via `ir.attachment`/filestore, not as DB blobs.

#### Scenario: Download an attachment

- GIVEN a read message with an attached PDF
- WHEN the user downloads the attachment
- THEN the file is served from the filestore and its content matches the original

### Requirement: Compose — reply / reply-all / forward (PR-1c)

The system MUST let a user compose a reply, reply-all, or forward in plain text. Rich-text
(HTML) compose is a non-goal for Slice 1. Sending MUST use SMTP via the mailbox's
configured SMTP server and decrypted in-memory credential.

#### Scenario: Reply sends via the mailbox SMTP

- GIVEN a read message and a plain-text reply body
- WHEN the user sends a reply
- THEN the message is sent via SMTP using the mailbox's SMTP config

#### Scenario: Plain-text only in Slice 1

- GIVEN the compose surface in Slice 1
- WHEN the user composes a message
- THEN composition is plain text (no rich-text editor)

### Requirement: Threading headers on send (PR-1c)

A reply MUST set the `In-Reply-To` and `References` headers derived from the original
message's `Message-ID`, so the conversation threads correctly on the recipient side. A
forward MUST create a new `Message-ID` (it is a new message, not a reply).

#### Scenario: Reply sets threading headers

- GIVEN an original message with `Message-ID: <orig@example.com>`
- WHEN the user sends a reply
- THEN the outgoing message sets `In-Reply-To: <orig@example.com>` and includes it in
  `References`

#### Scenario: Forward creates a new Message-ID

- GIVEN an original message
- WHEN the user forwards it
- THEN the outgoing message has a fresh `Message-ID` (not the original's)

### Requirement: Sent-folder APPEND (PR-1c)

After a successful SMTP send, the system MUST APPEND a copy of the sent message to the
account's "Sent" folder so the user's own sent mail is visible in the webmail. The target
folder MUST be the server-advertised SPECIAL-USE `\Sent` folder if present, otherwise a
per-mailbox configurable `sent_folder_name` defaulting to `Sent`. If APPEND fails after SMTP
send succeeded, the system MUST log clearly and surface the failure to the user (the mail
was sent but not mirrored) — it MUST NOT silently lose the send state.

#### Scenario: Sent copy appears in Sent folder

- GIVEN a mailbox with a SPECIAL-USE `\Sent` folder
- WHEN the user sends a message
- THEN a copy is APPENDed to the `\Sent` folder and is visible in the webmail Sent list

#### Scenario: APPEND failure is surfaced, not swallowed

- GIVEN a mailbox whose Sent folder cannot be appended to
- WHEN the user sends a message and SMTP succeeds but APPEND fails
- THEN the system reports the APPEND failure to the user and logs it, without claiming the
  send failed

### Requirement: Attachments on send (PR-1c)

The system MUST let a user attach files when sending. Attachments MUST be MIME-attached to
the outgoing message and sent via SMTP. Uploaded attachments MUST be persisted via
`ir.attachment`/filestore.

#### Scenario: Send with attachments

- GIVEN a compose with one or more files attached
- WHEN the user sends
- THEN each file is attached to the outgoing MIME message and delivered

### Requirement: Three-pane OWL webmail UI (PR-1b/1c)

The system MUST provide a custom OWL client action presenting a three-pane webmail: a
folder tree, a paginated message list (unread badges, sort by date), and a reading pane,
plus a plain-text compose surface. The UI MUST be backed by JSON-RPC controllers. Full-text
search, mail filters/rules, drag-to-move, and multiple-account UX chrome are non-goals for
Slice 1.

#### Scenario: Three-pane layout

- GIVEN a user with a synced mailbox
- WHEN they open the Webmail client action
- THEN they see a folder tree, a message list, and a reading pane

#### Scenario: Unread badges and sort

- GIVEN a folder with read and unread messages
- WHEN the user views the message list
- THEN unread messages are badged and the list is sortable by date

### Requirement: Gmail / Google Workspace OAuth2 provider (PR-1d)

The system MUST support connecting Gmail / Google Workspace mailboxes via OAuth2 (refresh
token). Authentication MUST use XOAUTH2 against `imap.gmail.com` / the Gmail SMTP endpoint
with the Gmail IMAP/SMTP OAuth2 scope. The OAuth2 refresh token MUST be stored encrypted at
rest (per the credential-encryption requirement). Access-token refresh MUST run
transparently before each connect; an expired or revoked refresh token MUST surface a clear
re-authorise prompt. The OAuth code path MUST be testable with mocked OAuth tokens until the
non-code Google Cloud Console app registration (Track A) completes.

#### Scenario: Connect via OAuth2 refresh token

- GIVEN a Gmail mailbox with a valid encrypted refresh token
- WHEN the system connects
- THEN it exchanges the refresh token for an access token and authenticates via XOAUTH2

#### Scenario: Transparent token refresh

- GIVEN a Gmail mailbox whose access token has expired but whose refresh token is valid
- WHEN the system connects
- THEN it refreshes the access token transparently before connecting

#### Scenario: Revoked token surfaces re-authorise prompt

- GIVEN a Gmail mailbox whose refresh token has been revoked
- WHEN the system attempts to connect
- THEN it surfaces a clear re-authorise prompt (no opaque error)

#### Scenario: Testable with mocked OAuth

- GIVEN the OAuth code path and a mocked OAuth/token server
- WHEN the OAuth flow and refresh are exercised in tests
- THEN the flow and refresh behave correctly without a real Google app registration

### Requirement: Microsoft 365 / Outlook OAuth2 provider (PR-1e)

The system MUST support connecting Microsoft 365 / Outlook mailboxes via OAuth2 (refresh
token), reusing the OAuth2 abstraction and mailbox model established for Gmail. The MS365
OAuth2 scope MUST include `IMAP.AccessAsUser.All` and `offline_access` for IMAP/SMTP over
OAuth2. The refresh token MUST be stored encrypted at rest. Token refresh and
expiry/revocation handling MUST behave as for Gmail. The OAuth code path MUST be testable
with mocked OAuth tokens until the non-code Azure/Entra ID app registration (Track B)
completes.

#### Scenario: Connect via OAuth2 refresh token

- GIVEN an MS365 mailbox with a valid encrypted refresh token
- WHEN the system connects
- THEN it exchanges the refresh token for an access token and authenticates via OAuth2

#### Scenario: Required scopes

- GIVEN the MS365 OAuth2 configuration
- WHEN the OAuth2 request is made
- THEN it requests a scope set that includes `IMAP.AccessAsUser.All` and `offline_access`

#### Scenario: Testable with mocked OAuth

- GIVEN the MS365 OAuth code path and a mocked OAuth/token server
- WHEN the OAuth flow and refresh are exercised in tests
- THEN the flow and refresh behave correctly without a real Azure/Entra app registration

### Requirement: Uniqueness and data-integrity constraints (PR-1a/1b)

The message model MUST enforce a unique constraint on `(mailbox_id, uid)` to prevent
duplicate message records, and deduplication MUST also key on RFC822 `message_id`. Mailbox
records MUST enforce `(user_id, imap_host, imap_user)` uniqueness. Folder records MUST
uniquely identify a folder within a mailbox.

#### Scenario: Duplicate UID rejected

- GIVEN a mailbox with a stored message at UID 42
- WHEN another record with the same `(mailbox_id, uid)` is inserted
- THEN the insert is rejected by the unique constraint

### Requirement: Provider testability with mocked transports (cross-cutting)

The IMAP/SMTP/OAuth client layers MUST be structured so that mailbox I/O and OAuth flows
can be unit-tested with mocked transports and mocked OAuth/token servers, without real
network access or real provider app registrations, producing real RED→GREEN evidence in the
docker-compose `--test-enable --stop-after-init --test-tags=/mail_management` harness on
the `local_test` DB.

#### Scenario: IMAP/SMTP client testable with mocks

- GIVEN unit tests that mock `imap-tools` and `smtplib` transports
- WHEN the connect/sync/send paths are exercised
- THEN they pass deterministically without a live mail server

#### Scenario: OAuth testable with mocks

- GIVEN unit tests that mock the OAuth/token server
- WHEN Gmail and MS365 OAuth flows and refresh are exercised
- THEN they pass deterministically without real app registrations

---

## Non-goals (Slice 1)

The following are **explicitly out of scope** for Slice 1 and MUST NOT be implemented or
spec'd as Slice 1 behaviour. They may appear in Slice 2 or later changes.

- **AI layer** (Slice 2 — draft replies, summaries, reply improvement, conversation context).
- **Calendar, contacts, mailing lists.**
- **Full Roundcube/SnappyMail parity:** full-text search, mail filters/rules, drag-to-move,
  rich-text/HTML compose, keyboard shortcuts, multiple-account UX chrome, send-later,
  signatures management, identities/aliases.
- **IMAP IDLE / real-time push** (needs a sidecar outside the `workers=4` pool).
- **Shared / team mailboxes** (v1 is strictly private-per-user).
- **Exotic MIME:** calendar invites (`text/calendar`), S/MIME, PGP; v1 collapses
  `multipart/alternative` to the HTML part.
- **CI pipeline** (TDD evidence comes from the docker-compose batch test harness, not a CI
  job added in this slice).

---

## Decisions locked by this spec (resolving proposal §11 open items)

- **D-ENCRYPT (closed):** No `fields.Encrypted` in Odoo 19 Community → credential
  encryption-at-rest MUST use Fernet (`cryptography`) with a master key. Plaintext-only
  storage is rejected.
- **D-KEY (resolved):** Master key read from env var `MAIL_MGMT_MASTER_KEY` first, falling
  back to `ir.config_parameter` `mail_management.master_key`; auto-generate on first use if
  neither exists (persisted to the config parameter). Env var preferred to avoid
  ciphertext+key co-location on a dump.
- **D-IMAP-LIB (resolved):** IMAP via `imap-tools`; SMTP via stdlib `smtplib`. Fixed
  constraint for Slice 1.
- **D-SYNC-CADENCE (resolved):** `ir.cron` backbone with a default ~5-minute configurable
  interval, bounded `cron_batch_size`, plus on-demand refresh; IMAP IDLE out of scope.
- **D-UIDVALIDITY (resolved):** On a UIDVALIDITY change, reset `last_uid`, re-fetch, dedup
  by `message_id`, preserving user flags on still-present messages.
- **D-SENT-APPEND (resolved):** APPEND to SPECIAL-USE `\Sent` if advertised, else a
  per-mailbox configurable `sent_folder_name` defaulting to `Sent`.
- **D-OAUTH-SCOPES (resolved):** Gmail = XOAUTH2 with the Gmail IMAP/SMTP OAuth2 scope;
  MS365 = `IMAP.AccessAsUser.All` + `offline_access`.
- **D-COMPOSE (resolved):** Slice 1 compose is plain-text only.
