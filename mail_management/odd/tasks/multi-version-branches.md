# Feature: multi-version-branches

## Goal

Create and validate per-Odoo-version branches for the `mail_management` module:

- `16.0`, `17.0`, `18.0`, `19.0`, `20.0` — one branch per Odoo version, each
  installable and passing its test suite on the matching `odoo:NN.0` Docker
  image.
- `main` — snapshot of the current `workbench` tip (local only; user pushes it
  when ready). `workbench` stays the development branch, always separate from
  `main` and from the version branches.

## User decisions (2026-xx session)

1. `main` content = snapshot of `workbench` (= same commit as `19.0`).
2. Push policy: push only `16.0`–`20.0` to origin. `main` stays local because
   the deploy workflow triggers a production deploy on every push to `main`.

## Port matrix (derived from sibling repos in the same parent directory)

| Target | Changes vs workbench (19.0) |
| --- | --- |
| 19.0 | none — branch at workbench tip (baseline) |
| 18.0 | `res.groups.privilege` -> classic `category_id` groups; manifest version bump |
| 17.0 | 18.0 changes + `<list>` -> `<tree>` in views; verify JS import paths (17-level `@web/core/confirmation_dialog` etc.) |
| 16.0 | 17.0 changes + classic `attrs="{'invisible': ...}"` view syntax; `@api.ondelete(at_uninstall=False)` -> `unlink()` override gated on `_force_unlink`; `html_editor` -> `web_editor`; frontend on OWL 1 (Odoo 16) |
| 20.0 | `ir.model.access.csv` -> `ir.access.csv` new unified format (operation codes cru/crud/cr, model by name, group by full external id); manifest data list update; verify OWL/JS runtime |

## Test strategy

- Isolated Docker network + postgres container (no reuse of the running
  `desarrollos-*` stack).
- One git worktree per branch mounted at `/mnt/extra-addons/mail_management`.
- Per version: derived test image (odoo:NN.0 + `cryptography`, `imap_tools`,
  playwright chromium, like the existing `odoo:19.0-chrome` recipe), then
  `odoo -d test_mm -i mail_management --test-enable --stop-after-init
  --test-tags /mail_management`.
- Baseline first: prove the suite is green on 19.0 before porting.

## Tasks

- [x] T1. Exploration + port matrix + user decisions
- [x] T2. Test harness: network, postgres, per-branch worktrees, test images
- [x] T3. Baseline: `19.0` branch green on `odoo:19.0`
- [x] T4. Port + test `18.0`
- [x] T5. Port + test `17.0`
- [x] T6. Port + test `16.0` (views, unlink guard, web_editor, OWL 1)
- [x] T7. Port + test `20.0` (ir.access)
- [x] T8. Create local `main` (snapshot of workbench)
- [x] T9. Push `16.0`–`20.0` to origin (NOT `main`)
- [x] T10. Close: report + memory

## Evidence

(recorded per task: commit ids, test results)


## Evidence

| Branch | Commit | Validation (Docker odoo:NN.0) |
| --- | --- | --- |
| 19.0 / workbench / main | 295f94e | 672 tests 0 failed + tour E2E green |
| 18.0 | 59acf0b | 672 tests 0 failed + tour E2E green |
| 17.0 | 557aab2 | 672 tests 0 failed (tour skip documented) |
| 16.0 | 387d21c | 672 tests 0 failed (tour skip documented) |
| 20.0 | 5d9f83c | 1278 tests 0 failed (OWL 3 frontend port pending) |

Baseline bugs fixed on all branches: undeclared FolderTree assistant props
(OWL validation crash); allow_remote_images default flipped open (tracking
protection dead). Pushed to origin: workbench + 16.0-20.0 (2026-10-06).
main kept LOCAL (push triggers the production deploy workflow).
