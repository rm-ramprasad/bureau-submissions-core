# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Bureau Submissions: generates credit bureau submission files (UCRF consumer v3.74 now; commercial and NeSL later) for one NBFC
per deployment (one AWS account per customer, alongside their LMS). Python 3.14, Flask JSON API, SQLAlchemy Core (no ORM) + PyMySQL, plain SQL migrations,
pytest; Alpine.js frontend with no build step; serverless on AWS (`template.yaml`, SAM transform).

Read before larger changes: `docs/ARCHITECTURE.md` (platform, data model, request flow, UI), `docs/PIPELINE.md`
(canonical model → adapter → snapshot → validation → formatter), `RUNBOOK.md` (deployment rules), `ONBOARDING.md`
(new NBFC), `docs/ROADMAP.md` (open work).

## Commands (PowerShell, Windows)

```powershell
.\.venv\Scripts\python -m pip install -e ".[dev]"          # setup; then: Copy-Item env.example.ps1 env.ps1; . .\env.ps1
.\.venv\Scripts\python -m pytest                            # all tests (in-memory SQLite, no DB needed)
.\.venv\Scripts\python -m pytest tests/test_api.py::test_name   # single test
.\.venv\Scripts\cfn-lint -- template.yaml                   # whenever template.yaml changes
$env:REGENERATE_GOLDEN = "1"; .\.venv\Scripts\python -m pytest tests/test_ucrf_golden.py   # only after an intended format change

.\.venv\Scripts\python -m submissions.api                   # local API + pages, http://127.0.0.1:5000 (SUBMISSIONS_AUTH=none)
.\.venv\Scripts\python -m submissions.worker [--once]       # local polling worker (replaces the S3-triggered Lambdas)
.\.venv\Scripts\python -m submissions.migrate               # apply migrations/NNN_*.sql
.\.venv\Scripts\python -m submissions.adapters.check   clients/<t>/consumer.yaml --schema <t> --as-of 2026-09-30
.\.venv\Scripts\python -m submissions.adapters.compiler clients/<t>/consumer.yaml --schema <t> --out clients/<t>/generated/consumer
.\deploy\deploy.ps1 [-FrontendOnly | -BackendOnly]           # backend first when both
```

Migration/table parity against real MySQL: set `SUBMISSIONS_TEST_MYSQL_URL` to an **empty** scratch DB, run `pytest tests/test_migrate.py`.

## Architecture

- **Request flow:** user creates a DRAFT request (one `submission_request` row per type, grouped by `batch_id`) → adds
  exceptions (`submission_adjustment`) → submit locks exceptions (DRAFT → PENDING) → VALIDATE worker extracts a snapshot
  and validates → user confirms and picks bureaus → one GENERATE row per type × bureau formats a file from the *same*
  validated snapshot. Files never come without validation. Request status in MySQL is the source of truth; there is no orchestrator.
- **Dispatch:** the API inserts rows and writes `jobs/{validate,generate}/<request_id>.json` to the jobs bucket
  (`submissions/dispatch.py`); S3 events invoke `ValidateWorker` / `GenerateWorker` (same handler,
  `submissions.lambda_handlers.worker`). An atomic claim on the row makes duplicate deliveries harmless. `JobsFunction`
  handles schedules, recovery (re-dispatches stale PENDING), retention, `migrate` and `users` admin commands. Locally there is no dispatch; the polling worker picks up PENDING rows.
- **Pipeline layering (strict):** validation and the formatter depend only on the canonical model, never on each other or
  on adapters. NBFC schema changes → adapter mapping; UCRF spec changes → validation/formatter; product changes → canonical model.
- **Canonical model** `submissions/canonical/model.yaml` is the single model for all submission types: entities,
  columns, keys and code lists (`c_*` tables, record dataclasses and `ref_code` derive from it); no type knowledge.
  Code columns hold **canonical values** (`FEMALE`, `HOUSING_LOAN`), never file codes. Changing it means bumping
  `canonical_version` and a new migration (`python -m submissions.canonical.generate` prints the DDL to copy from).
- **Submission types:** one self-contained folder each, `submissions/types/<type>/`: `codes.yaml` (canonical value →
  file code; seeded into `ref_code_translation` / `ref_code_group`), `requirements.yaml` (generated REQUIRED rules,
  mapping coverage), `rules.yaml`, `layout.yaml`, `pipeline.py` (discovered by the registry). Nothing shared between types.
- **Reference data master = database:** `submissions/reference.py` seeds after migrations, **insert-missing only** (edits
  in the tables are never overwritten). Files use the active translations (`translate:` on a layout field).
- **Adapters:** one mapping per type: `clients/_base/consumer.yaml` maps the standard LMS; `clients/<tenant>/consumer.yaml`
  lists only overrides; `tenant.mapping_path` is the client folder. Lookups give canonical values (checked at load).
  Compiled to plain MySQL SELECTs committed under `clients/<tenant>/generated/<type>/` (regenerate after editing a mapping).
  Exceptions are joined into the extract in SQL (`submissions/adjustments/sql.py`), so validation/formatter need no knowledge of them.
- **Validation:** built-in rules per type in `submissions/types/<type>/rules.yaml` (consumer `UCRF.`, commercial
  `CUCRF.`); algorithmic ones in `python_rules.py` / `checks/`; per-tenant custom SQL rules in `validation_rule` (IDs
  must not start with a built-in prefix); `validation_policy` overrides enabled/outcome per tenant. SQL rules must use
  `:snapshot_id`. The pack's cascade computes `record_outcome` (consumer: account × holder; commercial: facility ×
  borrower, a borrower reject rejects all its facilities).
- **Formatter:** file format is data — `submissions/types/<type>/layout.yaml` rendered by a generic engine: consumer
  by the TUDF engine (`submissions/formats/tudf/`), commercial by the pipe-delimited one (`submissions/formats/delimited/`). Bureau files differ only by member ID, header tag and file name (from `bureau_profile`).
  `tests/test_ucrf_golden.py` and `tests/test_commercial_golden.py` compare output byte-for-byte with `tests/golden/`.
- **Tenancy:** isolation by schema at extract; everything after carries `tenant_id` / `snapshot_id` and must be scoped by it.
- **One NBFC per deployment:** each customer gets their own AWS account with their LMS on RDS; the same `bureau-submissions`
  stack is deployed alongside it, with the app tables on that same RDS server. `SUBMISSIONS_TENANT` (stack parameter
  `Tenant`) names the NBFC; the API applies it to every call and refuses other tenants' requests/snapshots. Users raise
  every request themselves (no scheduled validation runs). There is no NBFC selector. `Settings.tenant = None` (all NBFCs) exists only for tests. The mock
  `nbfc_002` (customised layout) stays as a second configuration to test mapping overrides.
- **Findings report:** rules are grouped into business categories (`results.CATEGORIES`, from the rule ID's segment or
  the pack's `category:`) with plain-language effects; counts are records (loan × borrower), not findings. A pack rule
  with `expected: true` is the spec's normal handling and never counts as a "gap".

## Non-obvious rules

These follow from deliberate design trade-offs, not defects. They are listed because the code alone does not enforce
them; the column says what catches a mistake.

| Rule | Why | What catches a mistake |
|---|---|---|
| **Migrations:** a new numbered file in `migrations/`; never edit an applied one; must work with the code already running (drop a column in a later deploy than the one that stops using it) | During a deploy the pages, API and in-flight runs can be a version apart | Nothing automatic |
| When changing a platform table, also update `submissions/*/tables.py` | Tests build the schema from `tables.py` (`metadata.create_all`), production from the SQL migrations | `tests/test_migrate.py`, but only with `SUBMISSIONS_TEST_MYSQL_URL` set; otherwise the two can drift silently. Planned fix: run it against a MySQL container in CI (ROADMAP: GitHub deploys) |
| SQL must run on both MySQL and SQLite | Tests use in-memory SQLite so they need no database; `tests/conftest.py` attaches `nbfc_001`/`nbfc_002` and shims `CONCAT_WS`, `REGEXP_LIKE`, `CHAR_LENGTH` | Tests fail on an unshimmed MySQL built-in; add a shim in `conftest.py` |
| Don't edit `frontend/config.js` for AWS, or anything in `build/` | Both are generated by `deploy.ps1`; the committed `config.js` is the local-dev version (`auth: "none"`) | Deploy overwrites them, so edits are silently lost |
| Lambdas reach only RDS and S3 (gateway endpoint): no other AWS services or external APIs | VPC with no NAT or interface endpoints keeps cost ~$1/month and the network closed | Runtime timeouts only; new dependencies need network changes (RUNBOOK "Network") |
| Generated files are never overwritten | Audit trail of what was submitted | Conditional writes (local `output/` or S3) |

Frontend: plain HTML + Alpine.js, vendored libs in `frontend/vendor/`, no CDN (CSP allows only `'self'`). `js/core.js`
holds sign-in, the API client and the page shell; one script per page.
