# Writer comparison: `submission-aws-lambda` vs `bureau-submissions-core`

Date: 2026-11-26 · Compared: `submission-aws-lambda` `main` (5a7eb26) with this repository's `main` (v1.19.0).
Purpose: before an AWS host is built here, find what exists only in the AWS repository, so it can be retired without
losing work. Related: [ADR 0004](adr/0004-aws-host-and-optional-modules.md).

## Summary

**This repository is ahead.** The writer, rules and canonical model here are a superset of the AWS repository's:
same code, reformatted (ruff), split from the database, and extended for MFI. The golden files for the shared cases
are byte-identical. Only a short list of items needs to come across, and none of them is in the bureau file writer
itself.

**One blocker for AWS is in this repository:** the SQL in the rule packs is written for SQL Server (`automn.`
schema prefix, `LEN`). It cannot run on Postgres or DuckDB as it is (see "Rule SQL dialect" below).

## Method

Each module was compared after removing the noise: line endings (CRLF vs LF), import paths
(`submissions.` → `bureau_core.`) and formatting (the AWS copy run through this repository's `ruff format`).
Counts below are the lines that still differ.

## Bureau file writer

| Module | Differing lines | Finding |
|---|---|---|
| `formats/tudf/encoding.py`, `parser.py` | 0 | Identical |
| `formats/tudf/engine.py` | 17 | Same renderer. Here: `render_file(data: SnapshotData, …)` takes records in memory; AWS `build_file(engine, snapshot_id, …)` reads the database itself. Here is the better shape (no database in `bureau_core`) |
| `formats/tudf/layout.py`, `formatters.py` | 6 / 4 | Import paths and type hints only |
| `formats/delimited/*` | 150–170 per file | **Superset here**: line layouts (one line per account, several files per bureau), fixed-width header / trailer, `address.*.full`, `phone.<n>`, `relative.*`, `group.*` heads, de-duplication (ADR 0003, MFI). Nothing in the AWS copy is missing here |
| `types/consumer/layout.yaml`, `types/commercial/layout.yaml` | 2–4 | Comment paths only |
| `types/*/codes.yaml`, `requirements.yaml` | 0 | Identical |
| `model.yaml` (canonical model) | +230 | **Superset here**: version 1.2 adds the MFI entities and code lists (marital status, education, religion, member role, relatives, borrower group …). Nothing removed |
| Golden files `nbfc_001_*`, `commercial_nbfc_001_*` | 0 | Byte-identical |

## Validation

| Module | Finding |
|---|---|
| `validation/engine.py`, `cascades.py`, `python_rules.py`, `checks/*` | Same logic; database-bound parts moved to `app/validation` here |
| Custom SQL rules, `validation_policy`, rule parameters per client | Here in `app/validation/settings.py` / `custom.py` / `tables.py` |
| Findings report (business categories, `expected: true`, report hints) | Here in `app/validation/results.py`, `api/findings.py` |
| Exceptions (adjustments), commercial relationship fix (AWS 39357ca) | Here in `app/adjustments/` (fix included) |
| Frozen code translations per snapshot (`snapshot_code_translation`) | Here in `bureau_core/validation/reference.py` |
| `types/consumer/rules.yaml` / `commercial/rules.yaml` | Same rules; **the SQL differs only in dialect** (next section) |

## Rule SQL dialect (blocker for AWS)

The rule packs here are written for SQL Server:

- every table is written `automn.c_party`, `automn.c_account` … (the AWS copy: `c_party`);
- `LEN(...)` instead of `LENGTH(...)`;
- the commercial pack drops three `ref_code_group` sub-queries that the AWS copy has (check whether this is
  intended or a gap: `entity_related_type` / `individual_related_type`).

The source views already solve this for the views (T-SQL is the source, Postgres / MySQL copies are generated with
SQLGlot, `tests/test_views_build.py`). The rule packs need the same: either write the rule SQL once in a neutral
form and generate per dialect, or have the engine apply the schema and dialect at load time
(`{schema}.c_party`, SQLGlot transpile). Needed before any AWS data store (Postgres or DuckDB).

## Only in `submission-aws-lambda` (to bring across or retire)

| Item | What it is | Recommendation |
|---|---|---|
| LMS adapter mappings + compiler (`submissions/adapters/`, `clients/_base`, `clients/<t>/*.yaml`) | YAML mapping from a standard LMS schema to canonical, compiled to SELECTs; tenant files list overrides only | Bring as a `sources` adapter ("LMS mapping") for AWS clients reading an LMS database. Compare with ADR 0002 source adapters first; keep one mechanism |
| Onboarding tools (`onboarding/introspect.py`, `dry_run.py`, `new_tenant.py`) | Inspect an LMS schema, dry-run a mapping, create a tenant | Bring with the LMS adapter |
| Mapping-override test tenant `nbfc_002` + its golden files | Proves a customised mapping gives the right file | Bring with the LMS adapter |
| Golden `nbfc_001_cibil_tlv_newline.txt` | Encoding variant (line break between records) | Bring now; tests an engine option already here |
| AWS host (`template.yaml`, `lambda_handlers.py`, `dispatch.py`, S3 store, Cognito, `deploy.ps1`) | Working SAM stack | Reference for `shell_aws`; not ported as is (ADR 0004 decides the AWS design) |
| Rules page: rule SQL hidden from client roles | Design only (not built in either) | Build here, for every client |
| Target architecture (`docs/plans/lms-partnership-architecture.md`, `test-plan.md`) | Account split, key account, SSE-KMS, DynamoDB, Step Functions, medallion, audit packs, pack versions | Sorted into core / optional / AWS shell in ADR 0004 |

## Only here (for completeness)

Excel / CSV uploads and staging, demographics master, account position, input checks, SQL views per dialect,
MFI type, Excel-to-TUDF converter and reconciliation, client configuration store, Admin and Bureaus pages,
release pipeline, parity tooling. None of it is needed in the AWS repository: it retires.
