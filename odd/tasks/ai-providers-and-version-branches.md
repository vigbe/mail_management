# Feature: ai-providers-and-version-branches

Two phases, in the order the user asked: (1) provider-agnostic AI configuration
inside the module, tested on Odoo 19; (2) one branch per Odoo version (16.0–20.0),
each installable and green on its own `odoo:NN.0` image, with `main` = the 20.0 line.

## Specs

- **S1 (phase order)** — "quiero que crees una rama por cada version de odoo
  testealas y hazlas funcionales, pero antes [...] quiero que cambies la variable
  en los parametros de sitema que se usa para la api de google". The AI change
  lands on `main` first and is tested on 19.0; every version branch is cut from
  that commit so all of them inherit it.
- **S2 (provider-agnostic config)** — "hazlo mas varialbe para que admita otras
  ias como antropic google deepseek y open ia y cambile el nombre a la variable o
  hazlo confirurable desde dentro". Decision (ask_user_question, 2026-11-23):
  "Asistente en Ajustes, sin fallback (recomendado)". The Settings ▸ Mail
  Management AI wizard exposes: provider (`google` | `openai` | `anthropic` |
  `deepseek` | `custom`), API key (stored encrypted with the module's Fernet),
  model, optional base URL. Parameters are module-owned:
  `mail_management.ai.provider`, `mail_management.ai.api_key_encrypted`,
  `mail_management.ai.model`, `mail_management.ai.base_url`.
- **S3 (no legacy fallback)** — `ia_prop.gemini_api_key` and
  `real_estate.ai_gemini_model` are no longer read anywhere in the module. A
  missing configuration raises a `UserError` that names the wizard and
  `mail_management.ai.*`.
- **S4 (one client interface)** — every provider implements `chat(system,
  messages)` and `ask(system, user_text)` with the existing message shape
  `[{"role": "user" | "model", "text": str}]` and returns `str`. Pure Python on
  `requests`, no new external dependency. Transient-HTTP retry and
  definitive-error behavior are preserved; `GeminiError` stays importable from
  `lib` as an alias of the new common error class.
- **S5 (unchanged call sites)** — `chat`, `summarize`, draft reply, `triage`,
  `search_summary` in `models/mail_management_assistant.py` and
  `controllers/assistant.py` keep their signatures; only the client factory
  changes.
- **S6 (test-first)** — one RED test per rule before the code: factory selection
  per provider, missing-config error, encrypted round trip, custom base URL,
  request building + response parsing per provider (mocked `requests`), retry on
  408/429/5xx, definitive error, wizard save / `key_is_set` / default model per
  provider, assistant uses the configured provider.
- **S7 (versions)** — "las ramas y las versiones de odoo que tiene que funcionar
  son 16.0 17.0 18.0 19.0 y 20.0". Branches `16.0`, `17.0`, `18.0`, `19.0`,
  `20.0`; each installs and passes its suite on `odoo:NN.0` in an isolated
  stack. Decision "main es 20": `main` ends at the 20.0 tip. Ports are recovered
  from the user's copy (`_odoo_modules/copia/mail_management`: 387d21c, 557aab2,
  59acf0b, 5d9f83c, all cut at 295f94e) and reapplied on the new base; the 3
  post-cut commits (295f94e..b3ced61), the de-branding and the AI change are
  carried into every branch. 20.0 also gets the OWL 3 frontend port (pending in
  the old branch: it touched no `static/` file).
- **S8 (docs / i18n)** — new wizard strings go to `i18n/mail_management.pot` and
  `i18n/es_CL.po`. `openspec/config.yaml` keeps `module_repo_is_nested: false`.

## Tasks

| ID | Spec | Route | Commit |
| --- | --- | --- | --- |
| T1 | — | Track: this doc, Engram mirror, visible todo | — |
| T2 | S7 | Harness: isolated compose project, parametrized Dockerfile, per-version images; baseline 19.0 green on current `main` | 872 tests green (tour skipped until image rebuild) |
| T3 | S4, S6 | `lib/ai_provider.py` (base, registry, errors) + google/openai/anthropic/deepseek clients + tests (RED → GREEN) | c8c9565 |
| T4 | S2, S3, S5, S6, S8 | Factory in the assistant, wizard model + view, params, `UserError`, tests, i18n; suite green on 19.0 | 25927c6, 0a2b9a5 |
| T5 | S7 | `19.0` branch = `main` snapshot | |
| T6 | S7 | 18.0: reapply 59acf0b on the new base, test on `odoo:18.0`, fix | 0399d11 — 922 tests 0 failed + tour green | |
| T7 | S7 | 17.0: reapply 557aab2, test on `odoo:17.0`, fix | 29019dd — 922 tests 0 failed (tour skip inherited) | |
| T8 | S7 | 16.0: reapply 387d21c (cumulative), test on `odoo:16.0`, fix | 7dbd039+26824d1 — 922 tests 0 failed (tour skip inherited) | |
| T9 | S7 | 20.0: reapply 5d9f83c + OWL 3 frontend, test on `odoo:20.0` (suite + tour), fix; `main` := 20.0 | backend 2a1a650+60544c3 — 1314 tests 0 failed; OWL 3 frontend port in progress | |
| T10 | S7 | Close: report, memory, push decision (push is the user's) | — |

## Log

- **L1** — "creo que ya lo tiene dentro pero quiero que crees una rama por cada
  version de odoo testealas y hazlas funcionales, pero antes dime quiero que
  cambies la variable en los parametros de sitema que se usa para la api de
  google, hazlo mas varialbe para que admita otras ias como antropic google
  deepseek y open ia y cambile el nombre a la variable o hazlo confirurable desde
  dentro las ramas y las versiones de odoo que tiene que funcionar son 16.0 17.0
  18.0 19.0 y 20.0"
- **L2** — Decisions (ask_user_question): Q1 "No, rehacer los ports desde la
  matriz" (superseded by L3); Q2 "Asistente en Ajustes, sin fallback
  (recomendado)"; Q3 "main es 20"; Q4 "Sí: stack aislado por versión".
- **L3** — "ahi tienes una copia /Users/vigbe/Documents/Documentos - Mac Studio de
  Victor/work/project/_vicbas.com/odoo/_odoo_modules/copia/mail_management" — a
  full clone of the old repo with every port commit; ports are recovered, not
  redone.
- **L4** — Evidence: copy refs `origin/{16.0,17.0,18.0,19.0,20.0}` = 387d21c,
  557aab2, 59acf0b, 295f94e, 5d9f83c, all cut at 295f94e (16.0 stacks 6 commits,
  17.0 stacks 2, 18.0 and 20.0 are single commits); post-cut development
  295f94e..b3ced61 = 3 commits, 4 files (+50/−20); the 20.0 port touched no
  `static/` file. Docker Desktop 29.3.1 (24 CPUs, 8 GB VM). Recipe at
  `_ia-prop.com/desarrollos/{Dockerfile,docker-compose.yml,odoo.conf}` (image
  `odoo:19.0-chrome`, `postgres:17`, addon mounted at `/mnt/extra-addons`).
- **L5** — Existing AI seam: `models/mail_management_assistant.py:77` `_get_gemini()`
  → `GeminiClient(api_key, model)`; 5 call sites (`:169` chat, `:192` summarize,
  `:210` draft, `:248` triage, `:267` search_summary) via `chat(system, messages)`
  / `ask(system, text)`. Bug found: the assistant reads `ia_prop.gemini_api_key`
  in clear while the wizard stores `mail_management.ai_gemini_key_encrypted`, so
  the key saved from the module was never used. No AI tests exist.
- **L6** — Work happens on branch `feat/ai-providers` (cut from `main` @ 1c57710;
  `main` is the default branch). Harness lives in `/tmp/mm-ports/` (isolated
  compose project `mmports`: postgres:17 + `mm-odoo:<ver>-test` images built
  from a parametrized copy of the `odoo:19.0-chrome` recipe; runner
  `run-tests.sh <ver> <checkout> [--test-tags]` → DB `test_mm_<ver>_<checkout>`,
  log in `logs/`). Images: 19.0, 18.0, 17.0, 20.0 built; 16.0 needs the Debian 11
  security mirror pointed at archive.debian.org. Tag selection is exact
  (`:TestFoo` does not match `TestFooBar`); a file path works:
  `--test-tags=/mail_management/tests/test_ai_provider.py`.
- **L7** — T3 evidence: RED `ModuleNotFoundError: No module named
  'odoo.addons.mail_management.lib.ai_provider'` (exit 255) → GREEN
  `odoo.tests.result: 0 failed, 0 error(s) of 19 tests` (stats: 29 with
  subtests) on `odoo:19.0`. Commit c8c9565 = one slice of 603 lines (library +
  its tests, one behavior unit). Slice boundary recorded here per the 400-line
  guideline; delivery shape is decided at publishing time.
- **L8** — T2 harness incident: two concurrent runs shared one DB name and one
  log file → `duplicate key ... pg_database_datname_index`. Fixed in the runner
  (DB and log keyed by version + checkout).
- **L9** — T2 harness root causes (keep for the port workers): (1) `docker
  compose run <svc> <args>` REPLACES the service `command`, so the test options
  must go on the command line; (2) `--test-enable` and `--stop-after-init` are
  `file_loadable=False` in `odoo/tools/config.py` — they are ignored in
  `odoo.conf` (while `--test-tags` on the CLI implies both, which is why scoped
  runs worked); (3) a single-file bind mount keeps the old inode after the host
  file is rewritten — mount the directory (`./conf:/etc/odoo`); (4) the webmail
  tour is skipped without `websocket-client` — added to the image, all five
  images rebuilt. Runner: `run-tests.sh <ver> <checkout> [--test-tags=...]`.
- **L10** — T2 baseline on clean `main` (1c57710): `mail_management: 872 tests
  28.09s`, `0 failed, 0 error(s) of 672 tests` (loading) + 1 post-test; the tour
  was skipped for the missing `websocket-client` (image rebuild in progress;
  the tour is re-verified on the rebuilt image).
- **L11** — T4: the worker recorded RED `4 failed, 20 error(s) of 17 tests`,
  implemented everything, and timed out at 20 min with `1 failed, 0 error(s) of
  17 tests` — the translation test. Cause: field labels are model terms, not
  code strings; `_activate_lang` alone does not load them (the existing
  `test_folder_labels` pattern translates a code string). Fixed inline with
  `ir.module.module._update_translations(['es_CL'])`; scoped GREEN `0 failed, 0
  error(s) of 17 tests`. Commit 25927c6 (slice: 1 608 lines over 3 commits since
  `main`; library+tests, docs, wizard+factory+tests+i18n — one behavior unit
  each; delivery shape decided at publishing time).
- **L12** — T4 full suite on `feat/ai-providers` (image rebuilt with
  `websocket-client`): first run `2 failed` in the pre-existing i18n grammar
  guard (`TestNewTermTranslatedEsCl`): it treated `#: model_terms:` entries as
  code and demanded a flavor marker; Odoo exports view-arch/field-help terms
  without one. Fixed the guard (0a2b9a5). Final: `mail_management: 922 tests
  35.74s`, `0 failed, 0 error(s) of 708 tests`, exit 0, and the webmail tour
  ran for real (Chrome headless, 52/52 steps, "tour succeeded").
- **L13** — Port evidence (all in isolated Docker, runner `run-tests.sh`):
  18.0 `0 failed, 0 error(s) of 708 tests` (922 stats) + Chrome tour
  succeeded; 17.0 same numbers, tour skipped by the inherited `@unittest.skip`
  (Odoo 17 macro-engine stall, verified with in-browser probes in the original
  port); 16.0 first failed to install — the AI wizard view used Odoo 17+
  `invisible=`/`required=` expressions — fixed with classic `attrs` plus
  `from __future__ import annotations` for the Python 3.9 container
  (26824d1), then green `0 failed of 708` (922 stats), tour skip inherited
  (legacy engine stalls on the debounced search step); 20.0 backend green
  `0 failed, 0 error(s) of 1314 tests` (2a1a650 + 60544c3: finished the
  `get_str`/`set_str` rename — two higher-order references without parentheses
  escaped the sed — and normalized the selection assertion to tuples); its
  tour is skipped pending the OWL 3 frontend port (the only S7 remainder,
  together with `main` := 20.0 tip).
