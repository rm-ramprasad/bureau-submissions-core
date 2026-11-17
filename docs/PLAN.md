# Plan

The to-do list is **GitHub Issues** (one per step, checklists inside), grouped by milestone. This page is the
overview; the issue or pull request is the source of truth for status. Design: [requirements.md](requirements.md),
decisions: [adr/](adr/).

Status as of 2026-11-16: rarfincare runs the new app on Azure (go-live 2026-11-06, deployed from GitHub since
v1.0.0 on 2026-11-08). Latest release **v1.19.0** (PR #83, migration `023_position_terms`).

## Phase A: Azure (rarfincare)

| Step | Issue | Status |
|---|---|---|
| 0 Repository, tooling, CI | #8 | done |
| 1.1 `bureau_core` + golden regression | #9 | done |
| 1.2 `canonical` + `sources`: staging, input checks, views, Excel/CSV upload, workbook tests, database CI | #10 | done |
| 1.3 `app` + `shell_azure`: workflow, API, ports, Functions; deploy to rarfincare unchanged | #11 | done (go-live 2026-11-06) |
| 2 Performance for 100,000 rows per file | #14 | done |
| 3 Generic OIDC sign-in; `config.json` | #15 | done |
| 4 Client configuration store (versioned, audited) | #16 | done (maker–checker approval deferred) |
| 5 Type registry (pluggable submission types) | #17 | done |
| 6 Admin page part 1 | #18 | done |
| 7 Data-quality rules on the admin page | #19 | deferred (the Rules page stays as it is) |
| 8 Source mapping on the admin page | #20 | ADR 0002, see below; Admin-page mapping waits for a second client |
| 9 Release pipeline, deploy from GitHub | #21 | done ([release.md](release.md)) |

Alongside: #12 type-error baseline (tech debt), #35 known gaps from the API port review, #39 reconciliation results
(done).

## ADR 0002: load once, extract many (layers L0–L4)

| PR | What | Status |
|---|---|---|
| 1 | L2a customer tables (migration 010) | done #57 |
| 2 | Apply an upload to the customer tables | done #58 |
| 3 | L2b account position view `vw_pos_account_position` (migration 012, `views/position.sql`) | done #60 |
| 4 | Extract from L2a + L2b into the snapshot (migration 013) | done #61 |
| 5 | Party + role in the layouts | deferred (owner 2026-11-09) |
| 6 | rarfincare load through the source views, parity report, switch-over per type (Admin page "Data source") | done #62–#66 (parity identical) |
| 7 | Uploads page | done #73, then #80–#83 |
| 8 | Plugins | when the first client needs one |

Since 2026-11-11 the customer tables follow the **sanction model** (owner decision, replaces the party model of the
ADR draft): one model for consumer and commercial, keyed by sanction reference number and `sanction_ref_key`
(migrations 019, 020). Uploads run in the background with progress (021), have a details page with items not saved
(022) and no as-of date: the LMS sanction terms are read at request time for the request's as-of date (023).

L4 output files (fingerprint per file, "Mark as submitted") are done: #59, migration 011.

## ADR 0003: MFI format (UCRF MFI 3.3.1)

| Step | What | Status |
|---|---|---|
| 1 | ADR and spec | done #68 |
| 2 | Data model (migration 016) | done #69 |
| 3 | Engine: fixed-width header/trailer, several files per request | done #70 |
| 4 | Layout SHG (136) / JLG (100) | done #71 |
| 5 | Validation rules | done #72 |
| 6 | Provisional workbook, upload checks, `views/mfi.sql`; MFI on the New Request page (migration 018) | done #74 |
| 7 | Bureau profiles and CIC test submission | waiting for the client's data and a CIC test window |

## Database migrations

Files: `packages/canonical/src/canonical/migrations/azure/` (latest `023_position_terms`; the next new one is
**024**). A migration is never edited once in a pull request; a change gets a new number.

The app identity has DML rights only, so for every release that lists migrations the owner runs each file in SSMS
first and then deploys with `mark_applied`. **Deploy does not apply view files** (`azure/views/*.sql`): a pull request
that changes one adds the step "run `azure\views\<file>.sql` in SSMS". Manual scripts (e.g. `manual/reset_run_data.sql`)
are run only on the owner's request. Steps: [github-deploy-rarfincare.md](github-deploy-rarfincare.md).

## Phase B: AWS

#22 Postgres DDL and migration runner · #23 `shell_aws` adapters · #24 entry points, CDK, first install. Deferred.

## Later (not scheduled)

NeSL type (shown as "coming soon"), rejections and resubmissions, DQI / DQR, licensing and compiled releases
(requirements.md §10–§18). Ruled out for now: hosted SaaS / multi-tenant.

## Testing at each stage

| Layer | Runs | Blocks merge |
|---|---|---|
| Lint, format, strict types, layer rules, secret scan, dependency audit | every PR | yes |
| Unit tests per package, coverage gate (91 %, never lowered) | every PR | yes |
| Golden regression (bureau files byte for byte) | every PR | yes |
| Database integration (SQL Server + Postgres containers) | every PR | yes |
| Workbook tests (`-m workbook`, local input templates, never committed) | before each PR, locally | reported in the PR |
| API tests (all request steps) | every PR | yes |
| Performance regression (100k rows) | nightly | release |
| Smoke test after deploy | each deploy | rollback |
