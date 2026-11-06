# CLAUDE.md

Credit bureau submission files for NBFCs: cloud-neutral layers (uv workspace under `packages/`) in thin per-cloud
shells. Design: `docs/requirements.md`. Plan and status: `docs/PLAN.md` and the GitHub issues.

## Rules

- **Layers** (enforced by `lint-imports`): `shell_*` → `app` → `sources` → `canonical` → `bureau_core`;
  `clientconfig` stands alone. Cloud SDKs (`azure.*`, `boto3`) only in `shell_*`.
- **Workflow:** one short branch per step or sub-step, strictly in order; PR with the template filled in, CI green.
  The owner (a data engineer, not a web developer) does not review code: each PR opens with **Decide / Risk / Merge
  if CI is green** in plain words, details folded away. The owner squash-merges; never merge, never push to `main`.
  Reference the issue (`Closes #N`). Verification: CI, golden files, the Database job, `/code-review` on risky PRs,
  and a parity report (old app vs new on the same inputs) before go-live.
- **Behaviour changes need the owner's OK.** Ports keep behaviour; golden files stay byte-identical. A changed
  bureau file is stated in the PR with the reason. Gaps found in the reference behaviour become `known-gap` issues
  (and an `xfail` test), not silent fixes.
- **Data:** no workbooks, CSVs or real data in git (gitignored + pre-commit). CI uses synthetic fixtures built in
  code; workbook tests are marked `workbook` and run locally with `BSC_WORKBOOK_DIR` set to the input templates.
- **No secrets** in the repo; never read `.env*` or `local.settings.json`. The real database is reached only by the
  owner, running scripts prepared here.
- **Never** create, change or delete cloud resources; infrastructure is written as code for review.
- **Types:** strict mypy for new code. The ported-module baseline in `pyproject.toml` only shrinks.
- **Coverage gate:** `fail_under` in `pyproject.toml`, never lowered.

## Commands

```
uv sync                                   # Python 3.14, all packages editable, dev tools
uv run pytest --cov                       # tests + coverage gate
uv run ruff check . && uv run ruff format --check . && uv run mypy packages && uv run lint-imports
REGENERATE_GOLDEN=1 uv run pytest packages/bureau_core   # only for an intended, reviewed output change
BSC_WORKBOOK_DIR=/workspace/submission-azure-functionapp/input-templates uv run pytest -m workbook
```

## Reference code

`/workspace/submission-azure-functionapp/` is the previous app (read only): code is ported from it package by
package. Its `CLAUDE.md` and `docs/` hold the domain rules (cycles, workbook handling, views, file formats).
