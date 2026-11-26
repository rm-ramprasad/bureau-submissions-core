# Convergence plan: one product for Azure and AWS, deployed to many clients

Date: 2026-11-26 · Status: agreed direction (owner), steps open · Supersedes `submission-aws-lambda` (archived)

Related: [ADR 0004](adr/0004-aws-host-and-optional-modules.md) (where each decision applies) ·
[aws-writer-comparison.md](aws-writer-comparison.md) (what this repository already has) ·
[aws-target/](aws-target/lms-partnership-architecture.md) (the AWS target design) · [PLAN.md](PLAN.md) (phase A steps).

## Goal

- **Product changes happen once**, in this repository (`rm-ramprasad/bureau-submissions-core`) **(owner)**.
- The same core (canonical model, rules, validation, bureau file writers) runs on Azure and AWS.
- Each client receives only what belongs to its cloud and plan: no AWS components in an Azure client, no other
  client's configuration, no product source.
- rarfincare (Azure, live) keeps working throughout; its bureau files change only with the owner's OK.

## 1. Repositories

| Repository | Owner | Holds | Never holds |
|---|---|---|---|
| `rm-ramprasad/bureau-submissions-core` (this one) | Us | All product code: core packages, `shell_azure`, `shell_aws`, optional modules, migrations and view sources, IaC templates for both clouds, tests, synthetic data, release pipeline | Client data, client secrets, client-only mappings |
| Client deployment repository, one per client (rarfincare: under `bi@rarfincare.com`) | Client | `client.yaml` (version pin, cloud, schema, types, modules, bureaus), `client/` (their sheet mappings, client-only views, custom rules), one deploy workflow | Product code (a check rejects `.py` files and product folders) |
| `rm-ramprasad/bureau-submission-awslambda` | Us | Archived after step 0 | — |

Client deployment repository layout:

```
client.yaml                  product_version: v1.20.0 · cloud: azure · schema: automn
                             types: [consumer, commercial] · modules: [excel-upload] · bureaus: [...]
client/                      workbook mappings · client-only views · custom rules
.github/workflows/deploy.yml download bureau-<cloud>-<version>.zip → migrate → publish (approval before production)
```

Releasing to a client = change `product_version` in its repository and run its deploy.
Until the deployment repository exists, today's Publish workflow (from this repository, OIDC into the client's
subscription) stays in use.

## 2. One package per cloud

A release (tag) builds, once:

| Asset | Contains | Excludes |
|---|---|---|
| `bureau-azure-<v>.zip` | core packages + `shell_azure` + Azure libraries, web pages, SQL Server migrations and views | `shell_aws`, `boto3`, AWS templates, Postgres / DuckDB files |
| `bureau-aws-<v>.zip` | core packages + `shell_aws` + AWS libraries, web pages, AWS templates and migrations | `shell_azure`, `azure.*`, T-SQL files |
| Optional modules | Separate wheels; installed only when listed in `client.yaml` `modules:` | — |

Controls:

1. **Build per shell.** `deploy/build.sh` builds only the packages the shell depends on. **Fix needed:** line 18
   (`uv build --all-packages`) would put `shell_aws` into the Azure zip once it exists.
2. **Package-contents test** in the release workflow: the Azure zip must contain no `shell_aws`, `boto3`, `botocore`,
   AWS template or unlisted module; the AWS zip the mirror. Fails the release.
3. **Import rules** (`lint-imports`): cloud SDKs only in `shell_*`; core never imports an optional module.
4. **The client workflow** knows one asset pattern (`bureau-<its cloud>-*`) and holds credentials for its own cloud only.

## 3. Database objects

**Rule: the schema name is configuration.** `automn` exists only in rarfincare's `client.yaml` and database. Today
it is written literally in migrations, views and rule SQL (`tests/test_schema_prefix.py` enforces it); this changes
to a `{schema}` placeholder filled at load / migration time, and the test changes to "no literal schema name".

| Object | Source | Applied by |
|---|---|---|
| App tables (migrations) | This repository | The migration runner, run by the client's deploy workflow (managed identity), only new migrations |
| Standard views (T-SQL source, generated dialects) | This repository | Same runner, after migrations |
| Client-only views and mappings | Client repository `client/` | Same runner, from the client folder |
| Reference data (codes, translations) | This repository | Seed step, insert-missing only |

This ends running view scripts by hand in SSMS. The owner still approves each production deploy.

### Where database objects are tested

| Stage | Database | Runs | Data | Client access |
|---|---|---|---|---|
| 1 Every PR | SQL Server + Postgres containers (CI Database job, exists) | Migrations, views, rule SQL, full pipeline, golden files; schema named `automn` **and** another name | Synthetic | No |
| 2 Our dev | Azure SQL in **our** subscription (free offer); a dev AWS account when the AWS host starts | The release deployed end to end | Synthetic + anonymised samples | No |
| 3 Client staging | A second schema (e.g. `automn_test`) or a staging database in the client's subscription | The client repository's deploy with the new pin | Client's real sheets, a test cycle | Yes |
| 4 Production | `automn` | Same workflow after stage 3 | Live | Yes |

## 4. Sorting rule for design decisions

Every decision is tagged `core`, `optional:<module>`, `shell:aws` or `shell:azure` (full list in ADR 0004).
Optional and AWS-shell items are built only when an AWS client needs them; core gets only the port or hook, with a
do-nothing default. CI runs both configurations (Azure + Excel source; AWS + its data store, all modules on) against
the same golden bureau files.

## 5. Steps

Each step is one branch and PR (repository rule), in order. Steps 1–5 change nothing for rarfincare's files.

| # | Step | Outcome | Depends on |
|---|---|---|---|
| 0 | **Move in** (this PR): ADR 0004, writer comparison, AWS target documents, this plan | One home for all design | — |
| 1 | **Build per shell + package-contents test** | Azure zip provably contains only Azure parts | 0 |
| 2 | **Schema as configuration**: `{schema}` in migrations, views, rule SQL; test with two schema names | `automn` only in rarfincare's config | 0 |
| 3 | **Rule SQL dialect-neutral**: rule packs written once, transpiled per dialect (as the views are); fix `LEN`; check the three `ref_code_group` sub-queries missing from the commercial pack (comparison) | Rules run on SQL Server and Postgres (and DuckDB if chosen) | 2 |
| 4 | **Migration runner as a deploy step** (schema + client folder as inputs); views and reference seed included | No hand-run SQL | 2 |
| 5 | **Our dev environment** (stage 2): Azure SQL free offer, dev Function App and Static Web App; deploy from a release | Releases tested before any client | 1, 4 |
| 6 | **rarfincare deployment repository**: `client.yaml`, `client/` (their views and mappings moved out of this repository), OIDC deploy workflow, staging schema, production approval; release assets readable by the client workflow | Client-owned deploy record; product code stays here | 1, 4, 5 |
| 7 | **Bring across from the AWS repository**: `tlv_newline` golden file; Rules page hides rule logic from client roles | Nothing left only in the old repository except the LMS adapter | 0 |
| 8 | **Pack versions** with `effective_from`, client pinning | Regulatory changes released ahead, old cycles reproducible | 3 |
| 9 | **AWS host (phase B)** as decided below: `shell_aws`, ports, AWS template, AWS package | First AWS client installable | 1, 3, decision A |
| 10 | **Optional modules** as AWS clients need them, starting with `optional:lms-mapping` (the AWS repository's mapping compiler and onboarding tools, merged with ADR 0002 source adapters; `nbfc_002` golden files) | — | 9 |

Phase A steps in [PLAN.md](PLAN.md) continue in parallel; where they touch migrations or rule SQL, they follow steps
2–3.

## 6. Decisions open

| # | Decision | Options | Needed by |
|---|---|---|---|
| A | AWS data and control stores for phase B | (1) RDS Postgres as in PLAN.md / requirements.md: fastest, one more SQL dialect; (2) S3 / Parquet + DuckDB and DynamoDB (agreed 2026-11-26 for the partnership model): ~0 idle cost, kill switch, more work; (3) Postgres first, S3 / DuckDB later as a second data-store adapter | Step 9 |
| B | How the client workflow gets release assets | Read-only token in the client repository; or a package feed the client can read | Step 6 |
| C | rarfincare staging | Second schema in their database, or a separate staging database | Step 6 |
| D | Client deployment repository for rarfincare, or keep publishing from this repository | Deployment repository recommended once there is a second client | Step 6 |

## 7. Taken from `submission-aws-lambda` before archiving

| Item | Where it is now |
|---|---|
| Target architecture, test plan, diagram (incl. the 2026-11-26 review decisions) | [aws-target/](aws-target/lms-partnership-architecture.md) |
| Module comparison and what is only there | [aws-writer-comparison.md](aws-writer-comparison.md) |
| Decisions sorted by where they apply | [ADR 0004](adr/0004-aws-host-and-optional-modules.md) |
| Code: LMS mapping compiler, onboarding tools, `nbfc_002`, AWS SAM stack, Cognito pages | Stays in the archived repository (tag `v1.0` plus final `main`), read-only reference for steps 9–10 |

## Done when

- One repository holds all product code; the AWS repository is archived.
- A release produces one package per cloud, and the contents test proves no cross-cloud parts.
- rarfincare deploys from a pinned version, with migrations and views applied by the workflow, tested in stages 1–3.
- The same synthetic data gives byte-identical bureau files on the Azure and AWS configurations.
