# LMS partnership architecture

> **Target design for the AWS host, not yet built.** Moved here from `submission-aws-lambda` (archived) on
> 2026-11-26. Where each decision applies (core / optional module / AWS shell):
> [ADR 0004](../adr/0004-aws-host-and-optional-modules.md); the plan: [CONVERGENCE-PLAN.md](../CONVERGENCE-PLAN.md).
> Paths such as `submissions/...` and "ARCHITECTURE.md" refer to the archived repository.

Status: agreed direction, 2026-11-21. Diagram: [modular-architecture.png](diagrams/modular-architecture.png)
(source [modular-architecture.html](diagrams/modular-architecture.html)).

## Goal

Sell the submission product through LMS partners, to many NBFCs, on AWS and Azure, so that:

1. a client (or the LMS) can stop using us at any time and keep all their data,
2. our IP (canonical model, mappings, rules, layouts, add-ons) never stays with the client or the LMS,
3. a regulatory change is released once and reaches every client, with old cycles still reproducible for audit.

## Products and plans

One product; plans and submission types are configuration per client.

| Plan | Ingestion | Add-on modules |
|---|---|---|
| Lite | CSV upload in our templates (one CSV per canonical entity) | none |
| Standard | LMS connector (MySQL / Postgres / SQL Server / S3-Parquet) | none |
| Complete | as Standard | write-off, ARC sale, co-lending (more later) |

Submission types per client: any of Consumer, Commercial, MFI, NeSL. The AI/BI products run on the same gold layer
and are sold separately.

## Accounts

### Account 1: client data (client-owned AWS account or Azure subscription)

All client data lives here, before and after exit.

| Store | Contents | Readable by client? |
|---|---|---|
| Bronze / raw archive | Lite CSVs and LMS extracts per cycle, unchanged, hashed, archive tier 7+ years | Yes |
| Silver | Curated data in our canonical model, Parquet; add-on outputs; PII tokenised, PII vault separate; neutral physical column names | No (our encryption) |
| Gold | BI marts, KPIs, AI features | No (our encryption) |
| Submission working store | Per-cycle snapshot, findings, record outcomes, Parquet; purged when the cycle closes | No (our encryption) |
| Exceptions log | Append-only Parquet (see below); S3 Object Lock compliance mode / immutable blob; client's own key; kept and readable after exit | Yes |
| Submission files + audit packs | Bureau files and per-submission audit pack; write-once | Yes |

The client's kill switch is the access grant for Account 2: a bucket policy for our roles (AWS) or a role assignment
for our managed identity (Azure).

### Account 2: our add-on (one per client, inside the client's AWS Organization, administered by us)

Code, metadata and keys only; no client data at rest.

- Ingestion: CSV loader, LMS connectors.
- Medallion builds: Python + DuckDB jobs on Lambda / Functions only (no Fargate / containers: serverless, low cost),
  orchestrated by Step Functions Standard / Durable Functions. Large clients are split into chunks by a Distributed
  Map, each chunk one Lambda well inside 15 minutes. No Glue: keeps one portable codebase.
- Request orchestration: one Step Functions execution per request (validate → wait for user confirmation via task
  token → generate per bureau in parallel), with retries and timeouts. Replaces S3 job objects and the recovery job.
  Cost: ~$0.025 per 1,000 transitions, a few paise per request.
- Mappings and cleaning rules, add-on modules, catalog / schema registry (real column names, versions).
- No VPC on AWS: Lambdas reach S3, KMS, DynamoDB and Step Functions over IAM; no NAT, no interface endpoints, no RDS.
- Version ledger: request IDs, hashes, who / when, engine and mapping versions per file. No PII.
- Submission engine: Lambda / Functions + DuckDB, reading silver and the working store from Account 1, data in memory only.
- Control DB: **DynamoDB on-demand** (AWS; conditional writes give the atomic claim and submit lock) / Azure DB for
  MySQL or another store chosen in phase 6. Requests, status, locks, users, metering. No account-level data. Behind a
  control-store port so the engine does not depend on either.
- Web app + API, AI/BI apps (Athena + QuickSight / Synapse + Power BI on gold through our query layer; Intelligence
  Hub on Bedrock / Azure OpenAI).

**Not SaaS: one install per client.** Each NBFC's AWS Organization holds Account 1 (LMS + data) and Account 2 (our
add-on). One Account 2 serves one NBFC (as today's code: `SUBMISSIONS_TENANT`). Its running cost is usage only
(Lambda, S3, DynamoDB, Step Functions, KMS calls; near zero when idle) and is on the client's organisation bill; the
client's key lives in our key account.

### Key account (ours, common to all clients)

A separate AWS account in **our** Organization, `ap-south-1`, running no workloads. It holds **one KMS key per client**
(never a shared key); each key's policy allows only that client's Account 2 engine roles.

- Our kill switch survives even if a client gains admin in Account 2 (it sits in their Organization).
- Single point of control: rotation, disable, key policies (IaC only) and one CloudTrail of every decrypt.
- Hardened: very few admins, MFA, key deletion needs two-person approval and the 30-day waiting period.
- Every run depends on it; KMS is highly available and called once per run. A mistake here affects all clients.
- Keys (~$1/month each) are on our bill; the endpoint stays in each Account 2.
- Contract states that we hold the key to derived data; bronze, files and audit packs stay readable by the client.
- Azure: Key Vault in our tenant with cross-tenant access.

**Protecting our IP inside the client's Organization:**
- Account 2's root user email and MFA are ours. We create the account and invite it into the client's Organization,
  so `OrganizationAccountAccessRole` (full admin for the management account) never exists; if the account is created
  from their Organization, that role is deleted at handover.
- **Centralized root access** (AWS Organizations feature): the client's management account can run privileged root
  tasks on member accounts (delete root credentials, allow root password recovery, unlock bucket policies) whoever owns
  the root email. Must be handled or our IP is exposed: the client keeps the feature off for Account 2 or excludes it
  (SCP denying `sts:AssumeRoot` against Account 2, agreed in the handover), the contract forbids its use, and an
  alarm in our tooling fires on any root session in Account 2 (CloudTrail `AssumeRoot` / root sign-in). Design rule:
  Account 2 holds nothing that is safe only while the client stays out; mappings and rules are encrypted with the
  client's key in our key account and loaded at run time.
- All admin roles in Account 2 are ours. The client keeps guardrail SCPs, billing visibility and read access to
  CloudTrail: they can see what we do, not do it.
- The engine ships as packaged / compiled code, so a misconfigured access does not hand over readable source.
- Azure: a subscription in the client's Entra tenant can be taken over by their Global Administrator; the add-on likely
  sits in our tenant with cross-tenant access to the client's data (open question, phase 6).

## Encryption: per cloud (decided 2026-11-26)

One identical mechanism is not required.

- **AWS: S3 SSE-KMS** with the client's key in our key account on the silver, gold and working-store prefixes (bucket
  default + a bucket-policy deny on uploads without that key; S3 Bucket Keys on to keep KMS calls cheap). Reading an
  object needs `kms:Decrypt` on our key, which only Account 2 engine roles have, so a client administrator downloads
  ciphertext only. No encryption code in the engine; Athena / QuickSight in Account 2 can read gold directly.
- **Azure: application-level** Parquet modular encryption (DuckDB), data keys wrapped by the client's key in our Key
  Vault, because Azure Storage CMK decrypts for anyone with data access. Behind the key-service port; AWS doesn't use it.

Consequences:
- Client tools cannot read silver or gold; gold is served through Account 2 (Athena on AWS, our query layer on Azure).
- Disabling a client's key makes all their derived data unreadable (our exit); revoking the access grant cuts us off
  (their exit).
- A database in Account 1 is ruled out for derived data: the client could read it live.

## Exceptions: append-only log

- Each save writes one small Parquet file: `exceptions/<request_id>/<timestamp>.parquet`.
- Rows: request, account/borrower key, field, new value or exclude, reason, user, time, action
  `ADD | SUPERSEDE (ref) | WITHDRAW`. Never updated.
- Current state = latest non-withdrawn row per key (DuckDB window function), joined into the snapshot as
  `adjustments/sql.py` does today.
- Submit (DRAFT → PENDING) records the lock in the control DB; no appends after. The log for the request is then copied
  into the audit pack.
- The log is the client's record of their own decisions: stored with the client's own key (not ours), S3 Object Lock
  in **compliance mode** for the retention period, readable by the client and **kept after exit** by either side.
  Field names in it are canonical field names only; no rule logic.

## Audit

| Question | Answered by |
|---|---|
| What was submitted? | Write-once file in Account 1 |
| From what data? | Hashed raw extract in bronze |
| What changed, who approved? | Exceptions log (in the audit pack) |
| Not altered? | Manifest hashes vs our version ledger |
| Deep dispute | Re-run from bronze with the recorded engine, pack and mapping versions → byte-identical file |

Audit pack per submission (Account 1, client-readable, write-once): manifest (hashes of raw, file and pack; engine,
type-pack and mapping versions; timestamps), exceptions log with approvers, findings report in plain language, run log.
The pack shows what failed, not how we check.

## Exit

- Client exits: revokes the access grant. Keeps bronze, every submission file, every audit pack and the exceptions log.
- We exit: disable the client's key; the client deletes silver, gold and the working store. The exceptions log stays
  (Object Lock until retention ends) and stays readable.
- Optional by contract: one-time flat export of gold.
- Contract: the curated structure is our IP and is deleted on exit; encryption enforces it.

## Azure mapping

| AWS | Azure |
|---|---|
| S3 + bucket policy | ADLS Gen2 / Blob + role assignment (multi-tenant app or Lighthouse) |
| S3 Object Lock | Immutable blob storage |
| KMS | Key Vault |
| Lambda | Azure Functions (Python) |
| Step Functions | Durable Functions |
| DynamoDB (control DB) | Azure DB for MySQL Flexible Server or Cosmos DB (phase 6) |
| S3 SSE-KMS (cross-account key) | Application-level Parquet encryption + Key Vault |
| S3 events | Event Grid / Storage Queue |
| Cognito | Entra ID |
| Athena + QuickSight | Synapse SQL + Power BI (via our query layer) |
| Bedrock | Azure OpenAI |
| SAM / CloudFormation, CloudFront | Bicep, Static Web Apps |

Per-cloud code is limited to: FileStore, job dispatch, auth and key-service adapters; entry handlers; scheduler; IaC.

## Release and versioning

**Pipeline (GitHub Actions), build once:**
1. Test: pytest, golden files (byte-identical), cfn-lint, Bicep lint.
2. Build: core wheel (semver) → Lambda package and Function package from the same wheel → ECR / CodeArtifact, ACR.
3. Promote: dev → staging (mock tenants `nbfc_001`, `nbfc_002`) → canary client → all, with approval gates.
4. Roll back by redeploying the previous version.

One release run deploys to every client's Account 2 (CloudFormation StackSets from our tooling account, or a pipeline
loop assuming each account's deploy role; Azure: matrix over subscriptions). The canary ring is one client's Account 2.

**Regulatory changes:**
- Type packs are versioned separately from the engine (e.g. `consumer 3.74.2`).
- Rules and layout fields carry `effective_from`; the engine picks logic by the cycle's as-of date. Ship before the
  regulation takes effect; old cycles re-run with old logic.
- Each client's config pins a pack version or follows `stable`.
- Canonical model changes bump `canonical_version` and run a silver rebuild; stay backward compatible for one release.
- Client mappings, custom rules and plan are configuration, deployed separately from code.

## Front end

The pages in ARCHITECTURE.md §6 (archived repository) and the request flow in §5 carry over; the pages are
hosted and signed in through Account 2 (Cognito there; Entra ID on Azure).

### Existing pages

| Page | Change |
|---|---|
| Dashboard, New request, Submission tracker, Request, Findings, Files | Same behaviour; read from the control DB and the Parquet stores instead of MySQL. Request and Files gain the audit pack download (phase 4) and show the pack version and effective date (phase 5) |
| Exceptions | Same screen; writes to the append-only log. "Removed" becomes a WITHDRAW row; a history view shows ADD / SUPERSEDE / WITHDRAW per exception; read-only after submit (phase 4) |
| Audit | Unchanged; the audit pack adds a per-submission record next to it |
| Rules & policies | **IP fix (phase 4):** client roles see each rule's description, category and effect, plus on/off, outcome and parameters; the rule SQL / Python check is visible to our roles only. Custom rules built from the form stay visible to the client (their own rules) |
| Admin (sidebar) | Grows into the admin console below; not a separate app |

### New pages

| Page | Content | Phase |
|---|---|---|
| Data upload (Lite) | Download CSV templates (from `model.yaml`), upload, readable row errors, 1,000-record limit | 3 |
| Data sources (Standard / Complete) | Last bronze load and silver build per source, freshness, failures | 3 |
| Connection health | Banner and status when the access grant is revoked or the key is disabled; runs fail cleanly with that reason | 4 |
| Cycle close | Close a cycle with confirmation; purges the working store; shown in Audit | 4 |
| Admin console | Plans, submission types, bureaus, reporting cycles, pack-version pins, users | 5 |
| Usage | Records, files and runs per cycle (metering) | 5 |
| Plan gating | Types and add-ons outside the plan are hidden or locked across pages | 5 |
| AI/BI | On gold; TBD | 7 |

## Changes from today

| Today | Target |
|---|---|
| Stack in the client's account; app tables on the client's RDS | Add-on in Account 2 (administered by us) in the client's Organization; control DB in Account 2; data in Account 1 as encrypted Parquet |
| Adapter compiles to MySQL SELECTs against the LMS DB | Connectors land bronze; mappings build silver; engine reads silver |
| Snapshot, validation, cascade in MySQL | DuckDB over Parquet (custom tenant SQL rules must be DuckDB-compatible) |
| `submission_adjustment` table | Append-only exceptions log in Account 1 |
| `FileStore` with S3 / local | + Azure Blob; + job dispatch, auth, key-service adapters |
| One type pack version | Versioned packs with `effective_from` |
| Manual `deploy.ps1` | GitHub Actions pipeline with rings |

## Phases

0. **Spike (1–2 weeks):** Parquet modular encryption with DuckDB in Lambda and Azure Functions, keys wrapped by
   KMS and Key Vault from another account/tenant; serving gold through our query layer; DuckDB run of one full
   consumer cycle (1 lakh records) for time and memory. Go/no-go for the encryption design.
1. **Ports:** job dispatch, auth and key-service interfaces; move `boto3` out of the core; core packaged as a wheel.
2. **Engine on DuckDB:** snapshot, validation, cascade, findings on Parquet; golden tests unchanged; tests on
   in-memory DuckDB.
3. **Medallion:** bronze landing, silver build from mappings, gold marts; Lite CSV loader with templates generated from
   `model.yaml`.
4. **Cross-account:** Account 2 setup and handover (root, roles, invite into the Organization), access grant and key, control DB, exceptions log, audit packs,
   retention / purge, version ledger.
5. **Versioning and CI/CD:** pack versions with `effective_from`, client pinning, GitHub Actions with rings.
6. **Azure host:** Functions, Blob, Key Vault, Entra ID, Bicep; same pipeline.
7. **Products:** MFI and NeSL packs; Complete add-ons (write-off, ARC, co-lending); AI/BI on gold.
8. **Cleanup:** remove the old code paths and docs listed below.

Testing for each phase: [test-plan.md](test-plan.md).

### Retiring old code

Each phase ends with its "done" step: the old path it replaces is deleted (after parity tests pass) and the current
docs (CLAUDE.md, ARCHITECTURE.md, PIPELINE.md, RUNBOOK.md) are updated in the same change. Phase 8 is a final sweep
for anything left over.

| Old | Replaced in | Remove |
|---|---|---|
| MySQL snapshot, validation and cascade SQL; MySQL engine kept for parity | 2 | Engine code, snapshot tables (migration in a later deploy), parity tests |
| SQLite shims in `tests/conftest.py` | 2 | Shims no longer needed once tests run on DuckDB |
| Adapter compiler output (`clients/*/generated/`) reading the LMS directly | 3 | Generated SELECTs; compiler replaced by silver build |
| `submission_adjustment` table and `submissions/adjustments/sql.py` | 4 | Table (later migration), SQL join code |
| App tables on the client's RDS; single-account `template.yaml` | 4 | Old stack resources, `deploy.ps1` single-account path |
| Manual `deploy.ps1` | 5 | Script, once GitHub Actions deploys every ring |
| `boto3` imports in core | 1 | Moved behind ports |

## Review decisions (2026-11-21)

- **No client live yet:** restructure on `main` freely; tag the current state `v1.0` as a baseline.
- **First release: submissions first, silver in full.** Lite + Standard for Consumer and Commercial on the new
  architecture. Silver is built as the general canonical layer (all entities, dated history), not shaped for
  submissions, so gold and AI/BI can follow without changing it.
- **Silver is the contract** between builds and products. Submissions never reads gold. Silver changes are additive;
  renames and removals go expand → contract over one release. The engine resolves physical names through the
  catalog, so a physical rename is a catalog change. A schema contract test fails on non-additive changes.
- **Clients have AWS (assumed for now).** For a client without cloud: (A) a minimal AWS account set up with our
  client-side template, billed to them, possibly by the LMS partner; or (B) the LMS partner hosts their Account 1.
  We do not host client data (no ROI). Desktop or browser-only versions expose the engine; only if a deal needs one.
- **Account 1 ownership is mixed** (NBFC or LMS vendor). One client-side template covers both; whoever owns
  Account 1 holds the kill switch; the partner contract covers an NBFC exiting through the vendor.

## Review decisions (2026-11-26, AWS best-practice review)

- **Encryption per cloud:** SSE-KMS with the cross-account key on AWS; application-level Parquet encryption only on Azure.
- **Control DB:** DynamoDB on AWS; Azure store chosen separately in phase 6. No VPC, no RDS in Account 2.
- **Exceptions log:** client's key, Object Lock compliance mode, readable by the client and kept after exit.
- **Centralized root access** must be blocked for Account 2 (SCP + contract + alarm) to protect IP; part of the handover.
- **Serverless only:** no Fargate / containers. Step Functions Standard orchestrates each request and splits large
  cycles with a Distributed Map over Lambda chunks; replaces S3 job objects and the recovery job.
- **Other:** arm64 Lambdas; StackSets self-managed (Account 2 is not in our Organization) with a deploy role trusting
  our tooling account and GitHub OIDC; WAF on CloudFront; required MFA for client users; Bedrock pinned to models
  served in ap-south-1 (no cross-region inference profiles).
- **Phase 0 now tests:** SSE-KMS cross-account read/deny (AWS), Parquet modular encryption (Azure only), Distributed Map
  chunking of 10 lakh records.

## Gaps to design before or during the phases

| # | Gap | Phase |
|---|---|---|
| 1 | Silver keeps dated snapshots per as-of date; the engine reads last cycle's submission state (closures, dropped accounts, history fields) | 3 |
| 2 | Bureau rejection files and correction submissions (inbound flow, linked to records) | after first release |
| 3 | ~~Multi-tenancy~~ not needed: one Account 2 per NBFC keeps today's single-tenant model. Instead: Account 2 creation and handover runbook (root ownership, no Organization admin role, SCPs agreed with the client) | 4 |
| 4 | Connector network path into the client's LMS (PrivateLink / peering; private endpoints on Azure) and where its credentials live | 3 |
| 5 | Borrower details are submission fields and appear in plain form in the bureau file. Encryption covers storage only: the engine decrypts in memory. Personal-data vault (silver holds tokens, the formatter rejoins real values) **deferred to the AI/BI phase**, to keep PII away from analytics; adding it later is an expand → contract silver change | 7 |
| 6 | **Rule (agreed): silver is always rebuildable from bronze**; a lost key or a bad build is recovered by rebuilding | 3 |
| 7 | **Key lifecycle:** one key per client; automatic yearly rotation; deletion only after the 30-day waiting period and a client-exit record; alarms on disable/schedule-deletion; key policies in IaC only | 4 |
| 8 | **Network (decided 2026-11-26):** with DynamoDB as the control DB there is no RDS, so Lambdas run outside a VPC and reach S3, KMS, DynamoDB and Step Functions over IAM: no endpoints, no NAT, near-zero idle cost. Keys never go in environment variables (non-exportable; a copied key would defeat the kill switch and CloudTrail). Update the CLAUDE.md network rule in phase 4 | 0 |
| 9 | **Region (agreed): everything in India, AWS `ap-south-1`** (Azure Central India), stated for RBI / DPDP | 0 |
| 10 | AI on client data: approach decided later (in-region models, no retention, contract and borrower consent to cover) | 7 |
| 11 | **Support access:** only engine service roles decrypt; staff see client data only through client-approved, time-limited break-glass access, fully logged. A policy we enforce, and the answer to "who at the vendor can see our borrowers?" | 4 |
| 12 | **Client-side CloudFormation template** for Account 1: buckets, Object Lock, access grant, key permissions | 4 |
| 13 | Operations (backup / DR targets, uptime, alerting, client status page): **TBD** | 4–5 |
| 14 | **Plan enforcement and metering:** Lite limited to 1,000 records per upload (CSV loader); Standard / Complete unlimited. Count records, files and runs per client per cycle in the control DB (negligible cost); used for plan enforcement, pricing and reporting usage to the LMS partner | 5 |
| 15 | **Configurable admin console** (plans, submission types, bureaus, reporting cycles, pack-version pins, users, metering) for the LMS partner / client admins; our operations console TBD | 5 |
| 16 | **Synthetic test data generator** from `model.yaml`: all entities, code lists, edge cases, 1 lakh and 10 lakh sizes, Lite CSVs; no real PII in CI | 0 |
| 17 | Phase 0 success criteria and size estimates: see below | before phase 0 |

### Phase 0 success criteria

1. One consumer cycle of 1 lakh records (silver → snapshot → validation → file) in **under 5 minutes** in Lambda, within its memory limit.
2. **A client principal reads only ciphertext:** signed in as an administrator of the client's account, a downloaded
   silver file is unreadable encrypted bytes (no schema, no values).
3. The engine in Account 2 decrypts and writes with a key from another account (AWS), and from another tenant (Azure).
4. Revoking the access grant or disabling the key stops the engine cleanly.

### Size estimates (rough; one developer with Claude; firmed up after phase 0)

| Phase | Weeks |
|---|---|
| 0 Spike + synthetic data generator | 2–3 |
| 1 Ports, core packaging, basic CI | 1–2 |
| 2 Engine on DuckDB (parity with today) | 3–4 |
| 3 Silver with history, Lite CSV loader, connectors | 4–6 |
| 4 Cross-account, Account 2 handover, key lifecycle, client template, audit packs | 4–5 |
| 5 Pack versioning, CI/CD rings, metering, admin console | 4–5 |
| **First release (AWS, Consumer + Commercial)** | **~18–25 (4½–6 months)** |
| 6 Azure host | 3–4 |
| 7 MFI / NeSL (each), add-ons, AI/BI | 2–3 per pack; rest TBD |
| 8 Final cleanup sweep | ~1 (most removal happens within each phase) |

## Open questions

- Will NBFCs accept an Account 2 in their Organization whose root and admin roles are ours (they keep SCPs, billing, CloudTrail)?
- Do Azure clients' policies allow access from a vendor tenant (multi-tenant app or Lighthouse)?
- Retention period each NBFC's auditors expect for bronze and audit packs (assumed 7+ years).
- Post-exit re-runs for audit: paid service or contract clause?
