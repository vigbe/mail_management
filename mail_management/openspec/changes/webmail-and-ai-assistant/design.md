# Design — `webmail-and-ai-assistant` (Slice 1 — Webmail core)

> **SDD phase: DESIGN.** Concrete architecture document for **Slice 1 (Webmail core)
> ONLY**. It feeds `sdd-tasks` (strict TDD) and then `sdd-apply`. It contains
> **field-level design + architecture**, not finished `.py` files; code blocks are
> illustrative sketches of pattern/contract, not the implementation.
>
> **Inputs consumed (read this session):**
>
> - `openspec/changes/webmail-and-ai-assistant/specs/webmail/spec.md` (PRIMARY — 27
>   requirements / 55 scenarios, Slice 1 only).
> - `openspec/changes/webmail-and-ai-assistant/proposal.md` (PRD + 5-PR forecast).
> - `openspec/changes/webmail-and-ai-assistant/exploration.md` (authoritative tech ref).
> - `openspec/config.yaml` (project context, exact test command, strict_tdd).
> - Existing scaffold: `models/mail_management.py`, `__manifest__.py`,
>   `security/security_groups.xml`, `security/ir.model.access.csv`, `controllers/main.py`.
> - Sibling patterns (`../message_management_ai`): `access_token` field with
>   `password=True` + `_valid_field_parameter` escape hatch, `_redact_token` helper,
>   `res.groups.privilege` 3-tier security, cron-queue + error routing.
> - Skill: `~/.config/opencode/skills/odoo-19/SKILL.md` + the model/field/security/
>   decorator/mixin/OWL/actions/controller/testing/transaction/development guides.
>
> **Skill resolution: `paths-injected`** — `~/.config/opencode/skills/odoo-19/SKILL.md`
> plus the referenced guides were read directly before designing. No registry/fallback
> discovery was needed.

---

## 0. Verified facts baked into this design (confirmed by the orchestrator)

| Fact | Impact on design |
|---|---|
| Odoo 19 Community has **no `fields.Encrypted`** (`hasattr(odoo.fields,'Encrypted')==False`) | Credential encryption-at-rest **MUST** be app-level **Fernet**. (Closes proposal §11 Q-ENCRYPT.) |
| `cryptography` **is installed** in the image (v41.0.7) → Fernet usable today | No pip step for `cryptography`; still declared in `external_dependencies.python` for portability. |
| `imap-tools` **is NOT installed** | Added to the docker-compose web `entrypoint` pip line (`--break-system-packages`) **and** declared in `external_dependencies.python`. This install step is the **first task of PR-1a** so module imports resolve at install. (Closes Q-IMAP-LIB: `imap-tools` + stdlib `smtplib`.) |
| Module installs **green** on `local_test` (5 baseline tests); `strict_tdd: true` | Every design piece must be TDD-testable with the exact harness command (§H). |
| `mail.management` scaffold (draft/confirmed/done) is a **placeholder** | **Replaced/removed** by the new webmail model family. No prod DB holds it (verified), so a clean redesign is safe (see §J Migration). |
| `security_groups.xml` already migrated to the `res.groups.privilege` bridge | The bridge is correct; PR-1a only **adds groups/rules** for the new models, it does not re-migrate. |

---

## 1. Design decisions summary (locked by this design)

| ID | Decision |
|---|---|
| **D-NAMESPACE** | New models live under the **`mail.management.*`** namespace (`mail.management.mailbox`, `.folder`, `.message`, `.attachment`). The suggested bare names `mail.message`/`mail.attachment` **collide with Odoo core models** (`mail.message` exists; `ir.attachment` is the filestore model) and **MUST NOT** be reused as new `_name`s. |
| **D-SCAFFOLD** | The placeholder `mail.management` model, its `draft/confirmed/done` FSM, and its scaffold tests are **removed** in PR-1a. The ACL/view entries for the old model are replaced. |
| **D-ENCRYPT** | Fernet (`cryptography`) encrypt-at-rest on every credential; **plaintext never persists** (transient computed+inverse write field; ciphertext-only stored field). |
| **D-KEY** | Master key resolved **env var `MAIL_MGMT_MASTER_KEY` → `ir.config_parameter` `mail_management.master_key` → auto-generate+persist**. Never on the mailbox record; never logged. |
| **D-ISOLATION** | Per-user `ir.rule` isolation via **two group-scoped rules** (user = own; manager = all), **not** a global own-only rule (a global rule would wrongly force managers to own-only). |
| **D-UID-KEY** | Idempotency hard constraint is **`UNIQUE(folder_id, uid)`** (UIDs are unique only within a folder+uidvalidity). The spec's "`(mailbox_id, uid)`" shorthand resolves to `(folder_id, uid)` because `folder_id` implies `mailbox_id`. Cross-epoch/cross-folder dedup is a **soft upsert by RFC822 `message_id`**. (HIGH-RISK — §A.3.) |
| **D-CLIENT** | Dedicated client: `imap-tools` for IMAP I/O, stdlib `smtplib`+`email` for SMTP/MIME. `mail.thread` is **not** used for mailbox state. |
| **D-SYNC** | `ir.cron` backbone (`_commit_progress` batched, ~5 min default) + on-demand refresh; IMAP IDLE out of scope. |
| **D-ATTACH** | Attachments/bodies stored via **`ir.attachment` (filestore)**, never DB blobs; `mail.management.attachment` is a thin pointer model. |
| **D-UI-SPLIT** | The OWL webmail is the dominant surface; PR-1b is further split into **1b-i (data layer)** and **1b-ii (read UI)** to respect the 400-line review budget. Compose stays PR-1c. |
| **D-OAUTH** | XOAUTH2 SASL for IMAP/SMTP; access-token refresh before each connect; `client_id/secret` in system params, **refresh token** encrypted on the mailbox; flows testable with mocked OAuth. |

---

## 2. Module file layout (target after Slice 1)

```
mail_management/
├── __manifest__.py                      # version 19.0.2.0.0; external_deps + assets
├── __init__.py                          # imports models, controllers, (lib via models)
├── lib/
│   ├── __init__.py                      # exports: FernetCrypto, ImapClient, SmtpClient, OAuthProvider
│   ├── mail_crypto.py                   # Fernet encrypt/decrypt + master-key accessor + redact()
│   ├── mail_imap_client.py              # imap-tools wrapper: connect/list/fetch/store/append
│   ├── mail_smtp_client.py              # smtplib wrapper: send EmailMessage (STARTTLS/SSL)
│   └── oauth_provider.py                # OAuth2 refresh + XOAUTH2 SASL (Gmail + MS365 configs)
├── models/
│   ├── __init__.py
│   ├── mail_management_mailbox.py       # mail.management.mailbox  (per-user config + sync owner)
│   ├── mail_management_folder.py        # mail.management.folder    (IMAP folder + sync state)
│   ├── mail_management_message.py       # mail.management.message   (webmail message + threading)
│   └── mail_management_attachment.py    # mail.management.attachment (thin ir.attachment pointer)
├── controllers/
│   ├── __init__.py
│   └── main.py                          # JSON-RPC + http endpoints for the OWL client
├── security/
│   ├── security_groups.xml              # privilege bridge + new-model ir.rules + manager rule
│   └── ir.model.access.csv              # ACLs for the 4 new models (old mail.management removed)
├── data/
│   └── mail_management_cron.xml         # ir.cron: sync_mailboxes (~5 min)
├── views/
│   └── mail_management_views.xml        # mailbox config form/list/menu + client action
└── static/
    └── src/
        ├── webmail_client.js            # OWL root client action
        ├── components/                  # folder_tree, message_list, reading_pane, compose
        └── xml/*.xml                    # QWeb templates (t-out only)
```

`lib/` is an Odoo-model-free package (importable as
`from odoo.addons.mail_management.lib import FernetCrypto, ImapClient, SmtpClient,
OAuthProvider`) so the crypto/client/oauth layers are **unit-testable without a DB**
using plain Python stubs + `unittest.mock` (mirrors the sibling's `lib/` seam and its
`_StubConfig` test pattern). The Odoo models are thin: they delegate I/O to `lib/` and
hold only state.

---

## A. DATA MODEL

> All field names follow Odoo conventions. `from` is a Python keyword → the message
> uses `email_from`/`email_to`/`email_cc` (the same names `mail.thread` uses).
> Odoo 19 patterns applied: `_name` explicit (to force the dotted namespace),
> `models.Constraint` (NOT `_sql_constraints`), `models.Index`, `@api.depends`,
> `@api.constrains`, `@api.ondelete(at_uninstall=False)`, `@api.model_create_multi`,
> `Command`, `tracking`, `check_company`, `list` views (not `tree`).

### A.1 `mail.management.mailbox` — per-user mailbox config (PR-1a)

| Field | Type | Attrs / Notes |
|---|---|---|
| `name` | Char | required, tracking — display name, e.g. "Alice's Gmail" |
| `user_id` | Many2one `res.users` | required, `default=lambda self: self.env.user`, index — **owner** (drives ir.rule) |
| `company_id` | Many2one `res.company` | required, `default=lambda self: self.env.company`, `check_company=True` — admin grouping |
| `provider_type` | Selection `[('generic','Generic IMAP/SMTP'),('gmail','Gmail (OAuth2)'),('oauth_ms365','Microsoft 365 (OAuth2)')]` | required, default `generic` |
| `imap_host` | Char | required |
| `imap_port` | Integer | default 993 |
| `imap_use_ssl` | Boolean | default True (True=imaps/SSL 993; False=STARTTLS 143) |
| `imap_user` | Char | required — the IMAP login name |
| `smtp_host` | Char | required |
| `smtp_port` | Integer | default 587 |
| `smtp_use_tls` | Boolean | default True (STARTTLS) |
| `from_email` | Char | required — RFC5322 `From:` address used on send |
| **`credential`** | Char | `compute='_compute_credential'`, `inverse='_inverse_credential'`, `password=True`, **NOT stored** — transient write target (user types plaintext here). **Compute returns a constant mask and never decrypts.** |
| **`credential_encrypted`** | Char | `password=True`, `copy=False`, `groups='mail_management.group_mail_management_manager'`, index — **Fernet ciphertext at rest** (app-password OR OAuth2 refresh_token, depending on `provider_type`). |
| `sent_folder_name` | Char | default `Sent` — APPEND target when no SPECIAL-USE `\Sent` is advertised (Q-SENT-APPEND) |
| `last_sync` | Datetime | last successful sync timestamp |
| `state` | Selection `[('draft','Draft'),('configured','Configured'),('active','Active'),('error','Error')]` | default `draft`, tracking — `active` gates cron eligibility |
| `sync_error` | Text | last redacted error (never a credential) |
| `active` | Boolean | default True |
| `folder_ids` | One2many `mail.management.folder` inverse `mailbox_id` | |

**Constraints / indexes**

```python
_mailbox_uniq = models.Constraint(
    'UNIQUE(user_id, imap_host, imap_user)',
    'A mailbox for this (user, IMAP host, IMAP user) already exists.',
)
_mailbox_user_company_idx = models.Index('(user_id, company_id)')
```

**CRUD / decorators**

- `@api.model_create_multi def create(self, vals_list)` — set `user_id` default, encrypt any
  plaintext `credential` present in vals (strip it before `super().create`), then write
  `credential_encrypted` via `sudo()`.
- `@api.ondelete(at_uninstall=False) def _unlink_if_syncing(self)` — block delete while
  `state == 'active'` unless explicitly stopped; cascade handled by FKs.
- `_valid_field_parameter(self, field, name)` — whitelist `password=True` on
  `credential` and `credential_encrypted` (Odoo 19 escape hatch; sibling-verified pattern).
- `def action_test_connection(self)` — connects IMAP + SMTP with the **in-memory decrypted**
  credential, returns success/redacted-failure (no plaintext in result/logs).
- `def _cron_sync_mailboxes(self, *, limit=None)` — cron entry; batched (§C).
- `def _sync_mailbox(self)` — connect + per-folder sync (§C).
- `def _send_message(self, ...)` — build/send/APPEND (§D).
- `def _get_credential_plaintext(self)` — **`@api.private`**; `sudo()`-reads
  `credential_encrypted`, calls `FernetCrypto.decrypt(...)`, returns plaintext to the
  caller's local variable only. **Never** logged, serialised, or stored.

**Credential write/read pattern (critical — see §B for the security rationale)**

```python
credential = fields.Char(compute='_compute_credential',
                         inverse='_inverse_credential', password=True)  # NOT stored

@api.depends('credential_encrypted')
def _compute_credential(self):
    # NEVER decrypt here. Return a constant mask so reads never surface plaintext.
    for rec in self:
        rec.credential = '**********' if rec.credential_encrypted else False

def _inverse_credential(self):
    for rec in self:
        plain = rec.credential
        if plain:  # empty/unchanged mask is ignored
            rec.sudo().credential_encrypted = FernetCrypto.encrypt(plain)
        rec.credential = False  # drop plaintext from the in-memory record immediately
```

> Because `credential` is not stored, the plaintext exists only transiently inside the
> request transaction; the only persisted value is `credential_encrypted` (ciphertext),
> readable solely by the manager group.

### A.2 `mail.management.folder` — IMAP folder + sync state (PR-1b)

| Field | Type | Attrs / Notes |
|---|---|---|
| `mailbox_id` | Many2one `mail.management.mailbox` | required, `ondelete='cascade'`, index |
| `user_id` | Many2one `res.users` | `related='mailbox_id.user_id'`, `store=True`, index — **enables the per-user ir.rule** |
| `name` | Char | display name (e.g. "Inbox") |
| `imap_name` | Char | required — server folder name (e.g. `INBOX`) |
| `role` | Selection `[('inbox','Inbox'),('sent','Sent'),('drafts','Drafts'),('trash','Trash'),('all','All'),('archive','Archive'),('other','Other')]` | SPECIAL-USE role; `other` default |
| `uidvalidity` | Char | current UIDVALIDITY for the folder (Char to be safe for large values) |
| `last_uid` | Integer | default 0 — highest UID fetched this epoch |
| `last_sync` | Datetime | |
| `message_ids` | One2many `mail.management.message` inverse `folder_id` | |
| `message_count` | Integer | `compute='_compute_counts'`, `store=True` |
| `unread_count` | Integer | `compute='_compute_counts'`, `store=True` |

```python
_folder_uniq = models.Constraint(
    'UNIQUE(mailbox_id, imap_name)',
    'A folder with this IMAP name already exists for this mailbox.',
)
_folder_mailbox_idx = models.Index('(mailbox_id, role)')

@api.depends('message_ids', 'message_ids.flag_seen')
def _compute_counts(self):
    for rec in self:
        msgs = rec.message_ids
        rec.message_count = len(msgs)
        rec.unread_count = len(msgs.filtered(lambda m: not m.flag_seen))
```

### A.3 `mail.management.message` — webmail message (PR-1b) — incl. HIGH-RISK uniqueness

| Field | Type | Attrs / Notes |
|---|---|---|
| `mailbox_id` | Many2one `mail.management.mailbox` | required, `ondelete='cascade'`, index |
| `folder_id` | Many2one `mail.management.folder` | required, `ondelete='cascade'`, index |
| `user_id` | Many2one `res.users` | `related='mailbox_id.user_id'`, `store=True`, index — ir.rule |
| `uid` | Integer | required, index — IMAP UID **within `folder_id`** |
| `message_id` | Char | RFC822 `Message-ID`, index — soft-dedup key |
| `subject` | Char | |
| `email_from` | Char | (not `from` — keyword) |
| `email_to` | Text | |
| `email_cc` | Text | |
| `date` | Datetime | message Date header (UTC) |
| `body_html` | Html | `sanitize=True` (stored **sanitized** HTML) |
| `body_text` | Text | plain-text fallback |
| `has_attachment` | Boolean | compute depends on `attachment_ids` |
| `flag_seen` | Boolean | default False — `\Seen` |
| `flag_answered` | Boolean | default False — `\Answered` |
| `flag_flagged` | Boolean | default False — `\Flagged` |
| `flag_deleted` | Boolean | default False — `\Deleted` |
| `in_reply_to` | Char | RFC822 `In-Reply-To` |
| `references` | Text | RFC822 `References` |
| `parent_id` | Many2one `mail.management.message` | `compute='_compute_parent'`, optional store — within-mailbox threading |
| `attachment_ids` | One2many `mail.management.attachment` inverse `webmail_message_id` | |

```python
_msg_folder_uid_uniq = models.Constraint(
    'UNIQUE(folder_id, uid)',
    'A message with this (folder, UID) already exists.',
)
_msg_mailbox_msgid_idx = models.Index('(mailbox_id, message_id)')
_msg_folder_date_idx = models.Index('(folder_id, date DESC)')
_msg_mailbox_date_idx = models.Index('(mailbox_id, date DESC)')

@api.depends('in_reply_to', 'message_id')
def _compute_parent(self):
    # thread WITHIN mailbox by In-Reply-To -> message_id match (v1-light)
    for rec in self:
        parent = rec.mailbox_id.message_ids.filtered(
            lambda m: m.message_id and m.message_id == rec.in_reply_to
        )[:1]
        rec.parent_id = parent or False
```

#### A.3.1 HIGH-RISK: `(mailbox_id, uid)` vs `(folder_id, uid)` + UIDVALIDITY handling

This is the single most error-prone data-integrity area of Slice 1. It is stated
explicitly here so `sdd-tasks` produces dedicated RED→GREEN tests.

1. **UIDs are per-folder, not per-mailbox.** The same UID integer exists independently in
   Inbox (uid=5) and Sent (uid=5). Therefore the hard uniqueness **cannot** be
   `(mailbox_id, uid)` — that would reject legitimate messages in different folders.
   - **Resolution:** the hard constraint is **`UNIQUE(folder_id, uid)`**. Because
     `folder_id` already implies `mailbox_id`, this satisfies the spec's intent
     ("`(mailbox_id, uid)`" is the spec's shorthand for "the IMAP idempotency key").
   - The spec scenario *"Duplicate UID rejected"* is satisfied by this constraint: two
     inserts of the same `(folder_id, uid)` are rejected.

2. **Concurrency.** Two cron ticks, or cron + on-demand, may try to insert the same
   `(folder_id, uid)` at once. The `UNIQUE(folder_id, uid)` constraint is the source of
   truth: the loser raises `psycopg2.errors.UniqueViolation`, which the sync code catches
   (transaction guide pattern) and treats as "already present" (idempotent no-op). This
   satisfies *"Concurrent sync is safe"*.
   - Optional hardening: declare the constraint **`DEFERRABLE INITIALLY IMMEDIATE`** if
     measured contention warrants; for `workers=4` batched sync the immediate form is
     sufficient. (Decision: keep immediate; revisit only if contention is measured.)

3. **UIDVALIDITY change (cross-epoch dedup).** When a folder is recreated server-side,
   its UIDVALIDITY changes and all UIDs reset. The same physical message reappears under a
   **new** UID, so `UNIQUE(folder_id, uid)` would NOT detect the duplicate (new uid → new
   key). Recovery relies on the RFC822 `message_id`:
   - On detecting `folder.uidvalidity != server_uidvalidity`: set `folder.last_uid = 0`,
     store the new `uidvalidity`, and **re-fetch from scratch**.
   - **Upsert by `message_id`**: before creating a message, look up an existing row in the
     same `mailbox_id` with the same `message_id`. If found, **UPDATE** its `folder_id` /
     `uid` (and any still-valid flags) instead of inserting — this **preserves user flags**
     (`\Flagged`) on still-present messages. If not found, insert normally.
   - Messages that no longer exist server-side after the re-fetch are left as-is (v1 does
     not expunge; a future `stale` flag is noted as a refinement). They remain readable
     and are simply not re-linked.
   - This satisfies *"UIDVALIDITY change triggers clean re-fetch"* and *"User flags
     preserved across re-fetch"*.

4. **`message_id` may be empty.** Some malformed messages lack `Message-ID`. For those,
   the `message_id`-based upsert cannot dedup, so they fall back to the hard
   `(folder_id, uid)` constraint alone (acceptable; such messages are rare and the
   hard constraint still prevents intra-epoch duplicates). Documented as a known edge case.

### A.4 `mail.management.attachment` — thin filestore pointer (PR-1b/1c)

| Field | Type | Attrs / Notes |
|---|---|---|
| `webmail_message_id` | Many2one `mail.management.message` | required, `ondelete='cascade'`, index (named to avoid confusion with the `message_id` Char) |
| `user_id` | Many2one `res.users` | `related='webmail_message_id.user_id'`, `store=True`, index — ir.rule |
| `name` | Char | required — filename |
| `mimetype` | Char | |
| `file_size` | Integer | |
| `content_id` | Char | CID for inline (`multipart/related`) images |
| `ir_attachment_id` | Many2one `ir.attachment` | `ondelete='restrict'`, required — **the bytes live in the Odoo filestore here, never in a DB blob** |
| `inline` | Boolean | default False |

```python
_attachment_msg_idx = models.Index('(webmail_message_id, content_id)')
```

- On **sync (read)**: parsed MIME parts with content → create `ir.attachment`
  (`res_model='mail.management.message'`, `res_id=msg.id`, raw `datas`) → the framework
  stores it in the filestore (`store_fname`); link via `mail.management.attachment`.
- On **send (compose)**: user-uploaded files become `ir.attachment` first, then are
  MIME-attached from the filestore bytes. This keeps the *"bodies/attachments via filestore,
  not DB blobs"* invariant (spec: Worker-safe sync / Attachments).

### A.5 Relationship map

```
res.users (user_id, owner)
   │  ir.rule [('user_id','=',user.id)]   (user group)   +   [(1,'=',1)] (manager group)
   ▼
mail.management.mailbox ──< mail.management.folder ──< mail.management.message ──< mail.management.attachment
   (config + credential)     (uidvalidity/last_uid)      (uid, message_id, body)     └─> ir.attachment (filestore)
                                ▲                              │ parent_id (self, threaded)
                                └──────────────────────────────┘
```

- Cascade: `mailbox` delete → folders → messages → attachments → (ir.attachment restrict,
  so a stale filestore file is never silently lost; a cleanup job can GC later).
- Every child model carries a stored `user_id` (related to mailbox) so the **same**
  `[('user_id','=',user.id)]` rule isolates folders/messages/attachments, not just mailboxes.

---

## B. SECURITY DESIGN (#1 risk area)

### B.1 Fernet helper — `lib/mail_crypto.py` (`FernetCrypto`)

A model-free module (unit-testable without a DB). Public surface:

```python
MASTER_KEY_ENV = 'MAIL_MGMT_MASTER_KEY'
MASTER_KEY_ICP = 'mail_management.master_key'

def get_master_key(get_param, set_param):
    """Resolve the Fernet key with priority env -> icp -> auto-generate.
       get_param/set_param are injected callables (so tests inject fakes; the Odoo
       model passes env['ir.config_parameter'].sudo().get_param / set_param)."""
    key = os.environ.get(MASTER_KEY_ENV)
    if not key:
        key = get_param(MASTER_KEY_ICP)
    if not key:
        key = Fernet.generate_key().decode()        # bootstrap
        set_param(MASTER_KEY_ICP, key)
        _logger.warning("mail_management: generated a Fernet master key in "
                        "ir.config_parameter. Set the %s env var (preferred) to "
                        "avoid key+ciphertext co-location on a DB dump.",
                        MASTER_KEY_ENV)
    return key

def encrypt(plaintext: str, key=None) -> str: ...     # Fernet(token).encode()->str
def decrypt(token: str, key=None) -> str: ...          # raises MailCryptoError on InvalidToken
def redact(value, *secrets) -> str: ...                # replace each secret with ***REDACTED***
```

Odoo binding (`mail.management.mailbox`) injects the accessors and holds a lazy `Fernet`:

```python
from odoo.addons.mail_management.lib import FernetCrypto

def _fernet(self):
    Icp = self.env['ir.config_parameter'].sudo()
    return FernetCrypto.fernet(
        lambda k: Icp.get_param(k), lambda k, v: Icp.set_param(k, v))
```

**Invariants (spec scenarios):**

- `MAIL_MGMT_MASTER_KEY` set + icp set → env wins (no icp read needed).
- env unset, icp set → icp used.
- both unset → auto-generate + persist to icp + warning; encryption succeeds.
- decrypt under a different/missing key → `MailCryptoError("master key mismatch …")`
  (a **clear, non-redacted-by-credential** message; it names the *cause*, never a
  credential value).
- The key is **never** returned by any controller, never written to `sync_error`, and
  never logged (only the one bootstrap *warning* is emitted, which does not contain the key).

### B.2 Credential field pattern (on `mail.management.mailbox`)

| Layer | Field | Stored? | Who can read | Who can write |
|---|---|---|---|---|
| Plaintext input (UI) | `credential` (compute+inverse, `password=True`) | **No** | compute returns constant mask (never decrypts) | owner (user group) — plaintext lives only in the request memory |
| Ciphertext at rest | `credential_encrypted` (`password=True`, `copy=False`, `groups=manager`) | **Yes** | manager/admin only | written by the inverse via `sudo()` |

- `_valid_field_parameter` whitelists `password=True` on both fields (Odoo 19 escape hatch;
  base whitelist only allows `related_sudo`).
- `copy=False` on `credential_encrypted` prevents a duplicated mailbox from carrying the
  secret (mirrors sibling `access_token` hygiene).
- Reads: `credential_encrypted` is removed from `fields_get()`/views for non-managers; the
  owner only ever sees the `credential` mask. The plaintext is obtained **only** inside
  `@api.private _get_credential_plaintext()` and never leaves that call's local scope.

### B.3 `ir.rule` per-user isolation (the critical correctness point)

**Design choice:** use **two group-scoped rules**, NOT a global own-only rule.

```xml
<!-- mail.management.mailbox: user sees own; manager sees all -->
<record id="rule_mailbox_user_own" model="ir.rule">
    <field name="name">Mailbox: user sees own</field>
    <field name="model_id" ref="model_mail_management_mailbox"/>
    <field name="domain_force">[('user_id', '=', user.id)]</field>
    <field name="groups" eval="[(4, ref('group_mail_management_user'))]"/>
</record>
<record id="rule_mailbox_manager_all" model="ir.rule">
    <field name="name">Mailbox: manager sees all</field>
    <field name="model_id" ref="model_mail_management_mailbox"/>
    <field name="domain_force">[(1, '=', 1)]</field>
    <field name="groups" eval="[(4, ref('group_mail_management_manager'))]"/>
</record>
<!-- identical pair for folder, message, attachment (domain on their stored user_id) -->
```

**Why not a global rule:** Odoo ANDs all global rules with the group-rule OR. A global
`[('user_id','=',user.id)]` would intersect the manager's `[(1,'=',1)]` and **still** force
managers to own-only. With two group rules, a manager (who is in both groups via
`implied_ids`) gets `own OR all = all`; a plain user gets `own` only. (Sibling uses global
rules for *company* scoping because every user is subject to their companies; owner-scoping
with a manager override requires group rules.) This is unit-tested: two users, each owns a
mailbox → cross search empty; cross-user `browse` → `AccessError`.

### B.4 Privilege bridge (already migrated; PR-1a adds nothing here)

`security/security_groups.xml` already uses `ir.module.category → res.groups.privilege →
res.groups.privilege_id`. PR-1a only **adds** the new-model rules above and the
`group_mail_management_user`/`group_mail_management_manager` groups are reused (manager
implies user). The field-level `groups=...` and the ir.rules reference these existing
external ids. No `category_id` anywhere.

### B.5 Defence-in-depth redaction

- Any audit/`sync_error`/controller payload that could transit a credential is passed
  through `FernetCrypto.redact(value, plaintext)` (mirrors sibling `_redact_token`).
- Connection-test failures are constructed from **non-credential** substrings only (e.g.
  "IMAP authentication failed" — never the password).
- Logging in `lib/` never logs the key or plaintext; only host/port/user (user is not
  secret; it is the IMAP login name, already visible to the owner).

---

## C. IMAP SYNC DESIGN (cron + on-demand)

### C.1 Cron entry — `data/mail_management_cron.xml`

```xml
<record id="cron_sync_mailboxes" model="ir.cron">
    <field name="name">Mail Management: sync mailboxes</field>
    <field name="model_id" ref="model_mail_management_mailbox"/>
    <field name="state">code</field>
    <field name="code">model._cron_sync_mailboxes()</field>
    <field name="interval_number">5</field>
    <field name="interval_type">minutes</field>
    <field name="active" eval="True"/>
</record>
```

- Interval configurable in the cron record (Q-SYNC-CADENCE resolved: default ~5 min).
- Per the actions guide, cron code calls `model._cron_sync_mailboxes()` directly; tests use
  `IrCron.method_direct_trigger()` or call the method directly with a batch limit.

### C.2 `_cron_sync_mailboxes(self, *, limit=None)` — batched, worker-safe

```python
@api.model
def _cron_sync_mailboxes(self, limit=None):
    batch = int(limit or self.env['ir.config_parameter'].sudo()
                .get_param('mail_management.cron_batch_size', '20'))
    mailboxes = self.search([('state', '=', 'active'), ('active', '=', True)], limit=batch)
    remaining = 0 if len(mailboxes) < batch else self.search_count(
        [('state', '=', 'active'), ('active', '=', True)])
    self.env['ir.cron']._commit_progress(len(mailboxes), remaining=remaining)  # batch progression
    for mb in mailboxes:
        try:
            mb._sync_mailbox()
        except Exception as exc:                       # one mailbox must not abort the batch
            mb.sudo().write({'sync_error': str(FernetCrypto.redact(str(exc)))[:1000]})
            _logger.exception("sync failed for mailbox %s (user %s)", mb.id, mb.user_id.id)
```

- **Worker-safe:** bounded `cron_batch_size` per tick; per-mailbox try/except + savepoint so
  one failure rolls back only that mailbox; a slow/large mailbox is naturally capped because
  the inner fetch is also bounded (`fetch_batch_size`, default 50 messages/folder/tick) and
  `remaining` is reported for the next tick. No long-blocking IDLE.
- Timeouts: `imap-tools` connect/fetch use a configurable socket timeout
  (`mail_management.imap_timeout`, default ~30s); a hung server aborts cleanly, not pinning
  a worker.

### C.3 `_sync_mailbox(self)` — connect, per-folder incremental fetch

```
1. plaintext = self._get_credential_plaintext()            # @api.private, in-memory only
2. oauth: if provider_type in (gmail, oauth_ms365):
       plaintext = OAuthProvider(self).access_token()       # refresh -> short-lived token (§E)
3. with ImapClient(self, plaintext) as imap:               # imap-tools login; context-managed
     for folder in self.folder_ids:
         server_uidval = imap.uidvalidity(folder.imap_name)
         if folder.uidvalidity and folder.uidvalidity != server_uidval:
             folder.last_uid = 0; folder.uidvalidity = server_uidval   # UIDVALIDITY reset (A.3.1)
         elif not folder.uidvalidity:
             folder.uidvalidity = server_uidval
         new_uids = imap.search_uids(folder.imap_name, since_uid=folder.last_uid,
                                     limit=fetch_batch_size)
         for raw, flags, uid in imap.fetch(folder.imap_name, new_uids, mark_seen=False):
             self._upsert_message(folder, uid, raw, flags)  # message_id upsert + html_sanitize (A.3.1)
         if new_uids:
             folder.last_uid = max(new_uids)
         folder.last_sync = now()
4. self.last_sync = now(); self.sync_error = False
```

- **Idempotent:** `_upsert_message` first resolves an existing row by `message_id` within
  `mailbox_id` (update) else falls back to `create` under the `UNIQUE(folder_id, uid)`
  constraint (catching `UniqueViolation` for the concurrent case).
- **Sanitise on write:** `body_html = html_sanitize(parsed_html)` (odoo.tools) **before**
  storing; the stored field is already safe (defense even before rendering).
- **Folders discovery** (`action_discover_folders`, called once after a successful test
  connection): `imap.folder.list()` → for each SPECIAL-USE folder create/update a
  `mail.management.folder` with the right `role`. Tolerates casing (`INBOX`/`Inbox`) by
  matching SPECIAL-USE attributes, not names. Inbox/Sent/Trash guaranteed present
  (spec: Folder discovery).
- **On-demand refresh** reuses the exact same `_sync_mailbox`/per-folder path, just scoped
  to one mailbox/folder; it is idempotent and worker-safe by construction.

---

## D. SEND / REPLY / FORWARD DESIGN (stdlib smtplib + email)

### D.1 `_send_message` contract (`mail.management.mailbox`)

```
def _send_message(self, *, to, cc, subject, body_text, attachments=None,
                  reply_to_message=None, forward=False):
    1. build EmailMessage (stdlib):
         set From=self.from_email, To, Cc, Subject, plain-text body
         reply:   In-Reply-To = reply_to_message.message_id
                  References  = (reply_to_message.references or '') + ' ' + reply_to_message.message_id
         forward: fresh Message-ID (EmailMessage auto-generates), no In-Reply-To
         MIME-attach each attachment (bytes from ir.attachment/filestore)
    2. plaintext = self._get_credential_plaintext() / oauth access_token
    3. SmtpClient(self, plaintext).sendmail(msg)            # STARTTLS per smtp_use_tls; timeout-guarded
    4. APPEND sent copy:
         sent = self.folder_ids.filtered(lambda f: f.role == 'sent')[:1] or
                self._find_folder_by_name(self.sent_folder_name)
         try: imap.append(sent.imap_name, '\\Seen', now(), msg.as_bytes())
         except Exception:                                   # SMTP already succeeded
             notify_user("Sent, but could not mirror to Sent folder"); _logger.warning(...)
             # NEVER claim the send failed (spec: APPEND failure is surfaced, not swallowed)
    5. reply only: imap.store(original folder/uid, '+FLAGS', '\\Answered')
                   original.flag_answered = True             # local mirror
```

- Threading headers (`In-Reply-To`/`References`) come from the original `message_id`
  (spec: Threading headers on send; Forward creates a new Message-ID).
- Plain-text compose only in Slice 1 (Q-COMPOSE resolved).
- Attachments on send: uploaded files → `ir.attachment` → MIME-attached; never DB blobs
  (spec: Attachments on send).

---

## E. OAUTH2 DESIGN (Gmail + MS365) — PR-1d / PR-1e

### E.1 `lib/oauth_provider.py` (`OAuthProvider`) — model-free, mockable

```python
@dataclass
class OAuthConfig:
    provider: str          # 'gmail' | 'oauth_ms365'
    client_id: str
    client_secret: str
    refresh_token: str
    token_url: str
    scopes: str

# Provider registry (token URLs + scopes):
GMAIL    = dict(token_url='https://oauth2.googleapis.com/token',
                scopes='https://mail.google.com/')           # Gmail IMAP/SMTP XOAUTH2
MS365    = dict(token_url='https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token',
                scopes='offline_access https://outlook.office.com/IMAP.AccessAsUser.All '
                       'https://outlook.office.com/SMTP.Send')

class OAuthProvider:
    def __init__(self, mailbox): ...                         # reads config from system params
    def access_token(self) -> str:
        # POST refresh_token -> {access_token, expires_in}; short-lived; cached in-process
        # revoked/expired refresh -> raise ReauthorizationRequired (clear re-authorise prompt)
    @staticmethod
    def xoauth2(user, access_token) -> str:
        # SASL: "user={u}\x01auth=Bearer {t}\x01\x01" -> base64
```

- **Where secrets live:** `client_id`/`client_secret` in `ir.config_parameter`
  (`mail_management.gmail_client_id`, `.gmail_client_secret`,
  `.ms365_client_id`, `.ms365_client_secret`, `.ms365_tenant`), read via `sudo()`, **not**
  on the mailbox record. The mailbox holds `provider_type` + the **encrypted refresh token**
  in `credential_encrypted` (same field/crypto as the generic app-password — provider_type
  determines interpretation).
- **Auth:** XOAUTH2 SASL string is passed to `imap-tools` login (xoauth2) and to
  `smtplib` (`auth('XOAUTH2', sasl_string)`); IMAP/SMTP then authenticate as the user.
- **Refresh flow:** before each connect, `OAuthProvider.access_token()` refreshes the
  short-lived token; a revoked refresh token raises `ReauthorizationRequired` → the mailbox
  flips to `state='error'` with a redacted, clear "re-authorise this mailbox" message
  (spec: Revoked token surfaces re-authorise prompt).
- **Testability:** `requests.post` (token endpoint) and the IMAP/SMTP transports are mocked
  in tests (§H); no real Google/Azure app registration is needed to exercise the code path
  (non-code Tracks A/B only gate the *real* provider test).

---

## F. CONTROLLERS / ROUTES (`controllers/main.py`)

All endpoints use **`auth='user'`**; per-user scoping is enforced two ways:
(1) `http.request.env.user` selects the caller's own records; (2) the ir.rule
(`user_id == user.id`) makes any cross-user id by another user return empty/`AccessError`
even if forged. Controllers never return `credential*` fields.

| Route | type | Purpose | PR |
|---|---|---|---|
| `/mail_management/folders` | json | list mailboxes + folders for the current user (with unread/message counts) | 1b |
| `/mail_management/messages` | json | paginated message list for a folder (sort by date, unread badges) | 1b |
| `/mail_management/message_body` | json | one message body (sanitized) + attachment metadata + raw headers | 1b |
| `/mail_management/refresh` | json | on-demand folder/mailbox refresh (idempotent, worker-safe) | 1b |
| `/mail_management/set_flags` | json | mark seen/flagged (STORE mirrored locally + server) | 1b |
| `/mail_management/send` | json | reply / reply-all / forward (threading + APPEND) | 1c |
| `/mail_management/attachment/<int:attach_id>` | http | download an attachment from the filestore (auth=user) | 1b |
| `/mail_management/oauth/callback` | http | OAuth2 authorization-code callback → stores encrypted refresh token | 1d/1e |

- The controller always operates through `request.env[...]` (ir.rule applies automatically);
  it additionally asserts `record.user_id == request.env.user` before mutating, as a
  second line of defense.

---

## G. OWL UI ARCHITECTURE (PR-1b / 1c) + SPLIT

### G.1 Component tree (custom client action)

```
WebmailClient (registered: registry.category("actions").add("mail_management.webmail", WebmailClient))
├── services: orm, rpc, notification, user, action, dialog
├── state (useState): selectedMailboxId, selectedFolderId, messageList[], selectedMessage, loading
├── FolderTree        (pane 1)  — mailboxes ▸ folders; unread badges; t-on-click select
├── MessageList       (pane 2)  — paginated; sort by date; unread bold; t-on-click open
├── ReadingPane       (pane 3)  — subject/from/date; sanitized body (t-out, never t-raw);
│                                  attachment list; raw-header toggle; reply/reply-all/forward buttons
└── ComposeDialog     (via dialog service) — To/Cc/Subject/plain-text body; attach files; send
```

- Communication: `rpc('/mail_management/...')` JSON controllers (§F); `orm` for flag writes.
- Templates: QWeb with **`t-out` only**; the message body is the *already-sanitized*
  `body_html` (sanitized at sync time, §C.3) and is rendered via `t-out`/`Markup`-safe path
  (never `t-raw`). Remote images are blocked by default; CID inline images resolve from
  `mail.management.attachment.content_id`.

### G.2 Review-budget split (this WILL exceed 400 lines)

| Sub-PR | Scope | ~Lines |
|---|---|---|
| **1b-i** | Data layer: `folder` + `message` + `attachment` models, cron + on-demand sync, message_id upsert/UIDVALIDITY recovery, JSON controllers (folders/messages/message_body/refresh/set_flags), `lib/` IMAP client, **no UI** | ≤400 |
| **1b-ii** | Read-only OWL UI: `WebmailClient` shell + `FolderTree` + `MessageList` + `ReadingPane` (sanitized render + attachments + raw headers) + assets bundle + client action/menu | ≤400 |
| **1c** | Compose: `ComposeDialog` (reply/reply-all/forward, plain text, attachments) + `/send` controller + `lib/` SMTP client + APPEND + `\Answered` mirroring | ≤400 |

> The proposal already splits PR-1b (read) / PR-1c (compose). This design further splits
> **1b into 1b-i (data) and 1b-ii (UI)** so neither exceeds the 400-line review budget.
> 1b-i is independently testable (controllers + sync, mocked transports) before any UI lands.

---

## H. TESTING STRATEGY (strict TDD, docker harness)

Every design piece maps to a RED→GREEN test under the exact harness command from
`config.yaml`:

```bash
# install + test (fresh one-shot container; local_test throwaway DB)
docker compose run --rm -T --no-deps --entrypoint odoo web \
  -d local_test --db_host=db -r odoo -w odoo \
  -i mail_management --test-enable --stop-after-init \
  --test-tags=/mail_management
# re-run after install:
docker compose run --rm -T --no-deps --entrypoint odoo web \
  -d local_test --db_host=db -r odoo -w odoo \
  -u mail_management --test-enable --stop-after-init \
  --test-tags=/mail_management
```

### H.1 Test plan (file → spec scenarios)

| Test file | What it proves (spec scenarios) | Mock strategy |
|---|---|---|
| `tests/test_mail_crypto.py` | env>icp>autogen resolution; encrypt/decrypt round-trip; ciphertext ≠ plaintext; rotated/missing key raises clear error; key never in output | pure-Python, inject fake `get_param/set_param`; no DB needed (can also be a `TransactionCase`) |
| `tests/test_mailbox_model.py` | `(user_id, imap_host, imap_user)` unique constraint; `user_id` default = current user; `credential` write encrypts, never persists plaintext (DB column is ciphertext); `copy=False` on credential; `password=True` whitelisted; ondelete guard | `TransactionCase`; read raw column via `env.cr.execute` to assert ciphertext |
| `tests/test_isolation.py` | user A cannot see/read user B's mailbox/folder/message/attachment (ir.rule); manager sees all; credential field denied to non-manager | two `res.users` + `with_user`; assert empty search + `AccessError` |
| `tests/test_imap_sync.py` | incremental UID fetch (only >last_uid); re-sync no duplicate; concurrent insert → UniqueViolation treated as idempotent; UIDVALIDITY change → reset + re-fetch + message_id dedup + flags preserved; sanitised body stored; flags updated | mock `lib/` ImapClient / imap-tools (no network) |
| `tests/test_send.py` | reply sets In-Reply-To/References; forward creates new Message-ID; SMTP send via mailbox creds; APPEND to `\Sent`; APPEND failure surfaced not swallowed; `\Answered` mirrored; attachments MIME-attached | mock `lib/` SmtpClient + ImapClient.append/store |
| `tests/test_oauth.py` (1d/1e) | refresh→access token; XOAUTH2 SASL string shape; revoked→ReauthorizationRequired; Gmail + MS365 scope sets | mock OAuth token endpoint (`requests.post`) + transports |
| `tests/test_controllers.py` | controllers return only the caller's records; forge-proof (cross-user id → empty/error); never leak credential fields | `HttpCase` or controller-level `TransactionCase` with mocked sync |
| `tests/test_sanitize.py` | `<script>` stripped by `html_sanitize`; `multipart/alternative`→HTML part; remote img blocked, CID handled | pure parsing via `tools.mail` + `html_sanitize` |

- **No real network / no real provider app** in any test: `imap-tools`/`smtplib`/OAuth
  are mocked at the `lib/` seam. Tests are deterministic and run inside the docker harness.
- The scaffold `tests/test_module.py` (tests the removed `mail.management` placeholder) is
  **replaced** by the new test files in PR-1a; `tests/__init__.py` imports are updated.

---

## I. EXTERNAL DEPENDENCIES & INSTALL (PR-1a gating)

1. **`imap-tools` (NOT installed):**
   - Add to `docker-compose.yml` web `entrypoint` pip line:
     `pip3 install --user ... imap-tools --break-system-packages` (mirrors the sibling's
     `pyjwt python-docx pdf2docx` extension).
   - Declare in `__manifest__.py`: `'external_dependencies': {'python': ['imap-tools', 'cryptography']}`.
2. **`cryptography` (already installed v41.0.7):** declared in `external_dependencies` for
   portability; no pip step required in this image.
3. **`__manifest__.py` bump:** `19.0.1.0.0 → 19.0.2.0.0`; add `data/` entries
   (`security_groups.xml`, `ir.model.access.csv`, `data/mail_management_cron.xml`,
   `views/...`) and the OWL `assets` bundle; `depends` stays `[base, mail]`.
4. **Order so PR-1a is independently installable + testable:**
   - (a) edit docker-compose pip line + manifest `external_dependencies` (so `import
     imap_tools` resolves at module load);
   - (b) remove the `mail.management` placeholder model + replace ACLs/views;
   - (c) add `mail.management.mailbox` + crypto + isolation + generic connect/send
     (`lib/` IMAP/SMTP clients);
   - (d) RED→GREEN tests for crypto, mailbox, isolation, connect/send (mocked).
   PR-1a needs **no non-code track**; the generic IMAP/SMTP path is fully testable today.

---

## J. Migration / scaffold handling

- The module is installed **only in the throwaway `local_test` DB** (verified) and in no
  prod/shared DB. Removing the placeholder `mail.management` model is therefore safe: a
  clean `-i` reinstall of `local_test` drops the old table and creates the new schema.
- No destructive production migration is needed for Slice 1. If the module is ever deployed
  to a real DB before all PRs land, the change is purely additive (new tables) plus a model
  removal that a `migrations/19.0.2.0.0/` script can handle (drop the old
  `mail_management` table + its ACL rows); noted as a future-deploy guard, not a local_test
  requirement. **No irreversible data state** is created by Slice 1.

---

## K. Slice 2 hooks (reference only — NOT designed here)

- The `mail.management.message` model (with `message_id`/`references`/`parent_id`) is the
  substrate Slice 2's AI will read (draft/summarize/improve/context).
- The cron-queue + error-routing pattern and the sibling's `ai_provider.py`/
  `gemini_provider.py` (copy for v1 per exploration Option A) will attach here; the
  `@api.private _get_credential_plaintext()`/Fernet helper is reusable for any AI key.
- No AI code, `mail.ai.config`, or `prompt_assembly` is part of this design.

---

## L. Risks (carried into tasks)

| # | Risk | Mitigation (design-level) |
|---|---|---|
| R1 | 🔴 `(mailbox_id, uid)` vs `(folder_id, uid)` + UIDVALIDITY data-integrity bug | Hard `UNIQUE(folder_id, uid)` + soft `message_id` upsert + dedicated RED→GREEN tests (§A.3.1, H). Documented as the #1 HIGH-RISK item. |
| R2 | 🔴 Credential plaintext leak (logs/serial/error) | Transient compute+inverse field (plaintext never stored); ciphertext-only stored + manager group; `@api.private` accessor; `redact()` on all audit/error paths; isolation + redaction tests. |
| R3 | 🔴 Master-key co-location (ciphertext+key on a dump) | Env-var key preferred over icp; auto-generate only as bootstrap with a warning; documented ops guidance. |
| R4 | 🟠 Worker starvation under `workers=4` | Bounded `cron_batch_size` + per-folder `fetch_batch_size` + socket timeouts + per-mailbox savepoint; no IDLE. |
| R5 | 🟠 UI review-budget blow-up | 1b split into 1b-i (data) + 1b-ii (read UI); compose in 1c (§G.2). |
| R6 | 🟠 IMAP provider variance (folder names/SPECIAL-USE/flags/UTF-7) | Match SPECIAL-USE attributes not names; provider-scoped test matrix (§H). |
| R7 | XSS from untrusted HTML | `html_sanitize` at sync time + `t-out` render (never `t-raw`); remote-image block + CID handling; sanitize test. |
| R8 | imap-tools not present → module import failure | Pip step is the FIRST PR-1a task + declared `external_dependencies`. |
| R9 | OAuth refresh races (cron + on-demand) | In-process token cache; refresh is idempotent; revoked → clear re-authorise. |
| R10 | APPEND failure after SMTP success | Surfaced to user + logged; never claims send failed (§D). |

---

## M. PR → design mapping (for sdd-tasks)

| PR | Design sections | Models/files | Key tests |
|---|---|---|---|
| **PR-1a** | A.1, B, I, J | `mailbox`, `lib/mail_crypto.py`, `lib/mail_imap_client.py`, `lib/mail_smtp_client.py`, isolation rules, manifest+pip | crypto, mailbox model, isolation, connect/send (mocked) |
| **PR-1b-i** | A.2, A.3, A.4, C, F (read endpoints) | `folder`, `message`, `attachment`, cron, sync, read controllers | imap_sync, sanitize, controllers, UIDVALIDITY |
| **PR-1b-ii** | G.1 (read) | OWL read UI (folder tree/list/reading pane), assets, client action | tour/HttpCase (read) |
| **PR-1c** | D, G.1 (compose), F (/send) | `lib/` SMTP/APPEND, compose dialog, `/send` | send, threading, APPEND, attachments |
| **PR-1d** | E (Gmail) | `lib/oauth_provider.py` Gmail config, mailbox OAuth fields, callback | OAuth mocked |
| **PR-1e** | E (MS365) | MS365 config + scopes | OAuth mocked |

---

## N. SDD Result Contract

- **status:** complete
- **executive_summary:** Slice-1 webmail architecture designed end-to-end at field level:
  four new models under the `mail.management.*` namespace (mailbox/folder/message/attachment)
  replacing the placeholder scaffold; Fernet encrypt-at-rest via a model-free `lib/mail_crypto.py`
  with an env→icp→autogen master-key policy; per-user isolation via two group-scoped ir.rules
  (not a global own-only rule); `imap-tools`+`smtplib` dedicated client behind a mockable
  `lib/` seam; cron-pull sync (batched, UIDVALIDITY-aware, idempotent by `UNIQUE(folder_id,uid)`
  - `message_id` upsert) plus on-demand refresh; SMTP send with correct threading + Sent-folder
  APPEND; Gmail/MS365 OAuth2 via XOAUTH2 with encrypted refresh tokens; JSON controllers scoped
  to the caller; a 3-pane OWL UI explicitly split into 1b-i/1b-ii/1c to respect the 400-line
  budget. All pieces are TDD-testable with mocked transports under the exact docker harness
  command. Top risks (UID uniqueness/UIDVALIDITY, credential leaks, worker starvation, UI
  budget) are called out with mitigations and dedicated tests.
- **artifacts:**
  - `openspec/changes/webmail-and-ai-assistant/design.md` (this file)
- **next_recommended:** `sdd-tasks` — decompose PR-1a first (pip+manifest → crypto → mailbox
  model → isolation → connect/send, strict RED→GREEN per §H).
- **risks:** R1–R10 above (R1 UID/UIDVALIDITY and R2 credential leak are the critical ones).
- **skill_resolution:** `paths-injected` — `~/.config/opencode/skills/odoo-19/SKILL.md` plus
  the model/field/security/decorator/mixin/OWL/actions/controller/testing/transaction/
  development guides were read directly; no registry/fallback discovery was used.
