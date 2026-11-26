# 4. AWS host and optional modules: one core, decisions sorted by where they apply

Date: 2026-11-26 · Status: proposed · Decisions by the owner on 2026-11-26 marked **(owner)**

## Context

Two repositories have grown side by side:

- **this one** (`bureau-submissions-core`): layered packages, live on Azure for rarfincare (Excel uploads, Azure SQL);
- **`submission-aws-lambda`**: the same pipeline on AWS (SAM, Lambda, RDS MySQL), plus a target architecture for
  selling through LMS partners (`docs/plans/lms-partnership-architecture.md` there), agreed 2026-11-21 and reviewed
  against AWS best practice 2026-11-26.

Keeping both means two copies of the writer and rules: the maintenance problem this ADR prevents.
The writer comparison ([aws-writer-comparison.md](../aws-writer-comparison.md)) shows this repository is already the
superset.

## Decision

1. **This repository is the only home** for the core, the writer and every host **(owner)**. `submission-aws-lambda`
   gets fixes only, and is archived once its items in the comparison are here.
2. **Every design decision is tagged with where it applies:**

   | Tag | Meaning | Where the code lives | Effect on rarfincare |
   |---|---|---|---|
   | `core` | Every client, every cloud | `bureau_core`, `canonical`, `sources`, `app` | Gets it (with the owner's OK when a file changes) |
   | `optional:<module>` | A feature some clients have (plan, product add-on) | Its own package or package extra; switched on in `clientconfig`; reached only through a port or hook | Off |
   | `shell:aws` / `shell:azure` | True only because of the cloud or deployment model | `shell_aws` / `shell_azure`, `deploy/<cloud>/` | None / its own |

3. **New import rule:** core packages never import an optional module (added to `lint-imports` when the first one
   lands). Optional modules register themselves at start-up from the shell or `app`.
4. **Build optional and AWS-shell items only when an AWS client needs them.** When core code is touched in that area,
   add the port or hook with a do-nothing default, nothing more.
5. **Both configurations in CI:** Azure (Excel source, SQL views) and AWS (its data store), all optional modules on,
   must produce the same golden bureau files from the same synthetic data.

## Sorted decisions from `submission-aws-lambda`

### `core`

| Decision | Note |
|---|---|
| Canonical model; code columns hold canonical values, never file codes; `canonical_version` | Here already (1.2) |
| Type packs (codes, requirements, rules, layouts); file format as data; golden files byte for byte | Here already |
| Validation: built-in rules, Python checks, custom SQL rules per client, policies, cascade, findings categories, `expected: true` | Here already |
| Request flow: draft → submit locks exceptions → validate → confirm → one file per type × bureau from the same validated snapshot; no file without validation | Here already |
| Generated files are never overwritten | Here already |
| Exceptions (exclusions / overrides) | Here already. Storage behind a port: SQL table (Azure), append-only log (AWS target) |
| **Rule logic hidden from client roles** on the Rules page: description, category, effect, on/off, outcome and parameters visible; rule SQL / Python only to our roles; client-built custom rules stay visible | New, for every client |
| **Pack versions with `effective_from`**: the engine picks rule and layout versions by the cycle's as-of date; clients pin a pack version or follow `stable`; old cycles re-run with old logic | New, for every client |
| **Rule SQL independent of dialect and schema** (see the comparison) | New; needed before any AWS data store |
| One NBFC per install (`SUBMISSIONS_TENANT`); no hosted SaaS | Here already ("ruled out for now: hosted SaaS") |

### `optional:<module>`

| Module | Decision |
|---|---|
| `optional:lms-mapping` | YAML mapping from a standard LMS schema to canonical, tenant files list overrides only, onboarding tools (introspect, dry run, new tenant). Merge with ADR 0002 source adapters; one mechanism |
| `optional:lite-csv` | Lite plan: one CSV per canonical entity in our templates (generated from `model.yaml`), 1,000 records per upload |
| `optional:medallion` | Bronze (raw, hashed, archive) → silver (canonical, dated history, always rebuildable from bronze, additive changes only, schema contract test) → gold (marts) |
| `optional:audit-pack` | Per submission, write-once: manifest (hashes of raw, file and pack; engine, pack and mapping versions), exceptions log with approvers, findings report, run log; version ledger |
| `optional:plans` | Plan gating (Lite / Standard / Complete), metering (records, files, runs per cycle), Usage page |
| `optional:addons` | Write-off, ARC sale, co-lending |
| `optional:ai-bi` | Analytics and AI on gold; in-region models only |

### `shell:aws`

| Decision | Note |
|---|---|
| Region `ap-south-1` only (RBI / DPDP) | |
| **Two accounts per client**: Account 1 (client data, client-owned) and Account 2 (our add-on, in the client's Organization, root and admin roles ours); access grant = client's kill switch | |
| **Key account** (ours): one KMS key per client, policy allows only that client's Account 2 engine roles; key lifecycle in IaC, alarms on disable / deletion | |
| Silver, gold, working store: **S3 SSE-KMS with the client's key in our key account**; client admins download ciphertext only **(owner)** | Azure uses application-level Parquet encryption instead, if ever needed **(owner)** |
| **Control store: DynamoDB** (requests, status, atomic claim, submit lock, users, metering) **(owner)** | Azure keeps Azure SQL **(owner)** |
| **Serverless only, no Fargate / containers** **(owner)**. Step Functions Standard per request (validate → wait for confirmation → generate per bureau); Distributed Map over Lambda chunks for large cycles | Replaces S3 job objects and the recovery job |
| No VPC: Lambdas reach S3, KMS, DynamoDB, Step Functions over IAM | |
| Exceptions log: client's own key, S3 Object Lock compliance mode, readable by the client and **kept after exit** **(owner)** | |
| **Centralized root access blocked for Account 2** (SCP denying `sts:AssumeRoot`, contract clause, alarm on any root session); Account 2 holds nothing safe only while the client stays out **(owner)** | |
| Releases: self-managed StackSets or a pipeline loop with a deploy role per Account 2; GitHub OIDC; rings (dev → staging → canary → all) | Extends this repository's release / publish workflows |
| Cognito sign-in (or the client's OIDC), WAF on CloudFront, required MFA, arm64 Lambdas | |

### `shell:azure`

Unchanged: Functions, Blob, Storage queues, Entra, Azure SQL with managed identity, T-SQL views, Static Web App
(rarfincare). Uploads (`sources/excel.py`) stay a source adapter, usable by any client.

## Conflict to resolve

[requirements.md](../requirements.md) and [PLAN.md](../PLAN.md) Phase B (#22–#24) describe a different AWS host:
**RDS Postgres** as the app database (same SQL as Azure, generated Postgres views), SQS, EventBridge, Lambda
Function URL, CDK, about 30 clients in their own accounts. The 2026-11-26 decisions above use **S3 / Parquet +
DuckDB** for data and **DynamoDB** for control, SAM / CloudFormation, Step Functions.

| | Phase B as written | 2026-11-26 decisions |
|---|---|---|
| Data the engine reads | RDS Postgres tables and views | Parquet in the client's S3 (SSE-KMS, our key) |
| Control data | RDS Postgres | DynamoDB |
| Snapshot, validation SQL | Postgres (generated from T-SQL) | DuckDB |
| Kill switch / IP protection | Not addressed | Key account, two accounts |
| Fixed monthly cost per client | RDS (~$15–25) | ~0 when idle |
| Work to first AWS client | Smaller: one more SQL dialect | Larger: DuckDB data store, DynamoDB control store |

**Decide (owner):** which one Phase B builds. A middle path exists: Phase B on Postgres first (fastest to a first
AWS client, reuses everything), then the S3 / DuckDB data store as a second data-store adapter when the
partnership model starts. Either way the rule SQL must become dialect-independent first.

## Consequences

- One writer, one rule set, one canonical model for every client; a UCRF change is made once.
- AWS-only design stays out of rarfincare's build and pages.
- The AWS repository's documents are superseded by this ADR and the comparison; their sections are marked "moved"
  when each item lands here.

## Order of work

See [CONVERGENCE-PLAN.md](../CONVERGENCE-PLAN.md) §5 (steps) and §6 (open decisions, including the conflict above).
