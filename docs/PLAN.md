# Plan

The to-do list is **GitHub Issues** (one per step, checklists inside), grouped by milestone. This page is the
overview; the issue is the source of truth for status. Design: [requirements.md](requirements.md).

## Phase A: Azure (rarfincare)

| Step | Issue | Status |
|---|---|---|
| 0 Repository, tooling, CI | #8 | done (#1) |
| 1.1 `bureau_core` + golden regression | #9 | done (#7) |
| 1.2 `canonical` + `sources`: staging, input checks, views, Excel/CSV upload, workbook tests, database CI | #10 | next |
| 1.3 `app` + `shell_azure`: workflow, API, ports, Functions; deploy to rarfincare unchanged | #11 | |
| 2 Performance for 100,000 rows per file | #14 | |
| 3 Generic OIDC sign-in; `config.json` | #15 | |
| 4 Client configuration store (versioned, audited, optional approval) | #16 | |
| 5 Type registry (pluggable submission types) | #17 | |
| 6 Admin page part 1 | #18 | |
| 7 Data-quality rules on the admin page | #19 | |
| 8 Source mapping on the admin page | #20 | |
| 9 Release pipeline, deploy from GitHub | #21 | |

Alongside: #12 type-error baseline (tech debt), #13 decision on MOBILE digits (known gap).

## Phase B: AWS

#22 Postgres DDL and migration runner · #23 `shell_aws` adapters · #24 entry points, CDK, first install.

## Later (not scheduled)

Ingest and data model (lake), MFI / NeSL types, rejections and resubmissions, DQI / DQR, licensing and compiled
releases (requirements.md §10–§18). Ruled out for now: hosted SaaS / multi-tenant.

## Testing at each stage

| Layer | Runs | Blocks merge |
|---|---|---|
| Lint, format, strict types, layer rules, secret scan, dependency audit | every PR | yes |
| Unit tests per package, coverage gate (91 %, never lowered) | every PR | yes |
| Golden regression (bureau files byte for byte) | every PR | yes |
| Database integration (SQL Server + Postgres containers) | every PR, from step 1.2 | yes |
| Workbook tests (`-m workbook`, local input templates, never committed) | before each PR, locally | reported in the PR |
| API tests (all six request steps) | every PR, from step 1.3 | yes |
| Performance regression (100k rows) | nightly, from step 2 | release |
| Smoke test after deploy | each deploy, from step 9 | rollback |
