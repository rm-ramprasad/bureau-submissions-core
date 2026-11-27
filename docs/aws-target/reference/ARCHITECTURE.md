# Bureau Submission Platform: Architecture

> What the platform does, and how it is built and hosted on AWS.
> The data pipeline (canonical model, adapters, validation, formatter) is in [PIPELINE.md](PIPELINE.md).
> Operations and deployment rules are in [RUNBOOK.md](../RUNBOOK.md); open work is in [ROADMAP.md](ROADMAP.md).
> Target architecture (not yet built): [plans/lms-partnership-architecture.md](plans/lms-partnership-architecture.md).
> This page describes the system as it runs today.
> The consumer file spec is [specs/UCRF_Consumer_v3.74_Spec.md](specs/UCRF_Consumer_v3.74_Spec.md).

---

## 1. Scope

- **Context:** each customer (NBFC) has **their own AWS account** with their own LMS deployment on RDS MySQL. This app
  is deployed into that account alongside the LMS, as the same `bureau-submissions` stack, and serves **that one NBFC**
  (stack parameter `Tenant` → `SUBMISSIONS_TENANT`). The LMS layout (columns) can differ per customer; the mapping
  absorbs it. The data model keeps `tenant_id`; the mock tenants `nbfc_001` / `nbfc_002` are two layouts for testing.
- **Users:** an internal team of about 5, using the app about 10 times a month.
- **What a user does:**
  1. Picks an NBFC, an as-of date and one or more **submission types**. Consumer now; Commercial and NeSL later, with their own specs.
  2. Optionally adds **exceptions**: excluded loans or customers, and value overrides.
  3. Runs **validation**. Results are stored in tables and tracked across runs.
  4. Generates and **downloads one file per bureau**: Experian, CIBIL, CRIF High Mark, Equifax.
- **Bureau files:** they differ only in the member ID, header segment tag and file name. All other content is identical.

## 2. Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Bureau differences | Only member ID, header segment tag and file name (per NBFC × bureau × type, in `bureau_profile`). |
| 2 | Multiple bureaus / types | Allowed in one click. One row per unit of work in `submission_request`, grouped by `batch_id`. |
| 3 | Delivery | Download only. |
| 4 | NeSL / Commercial | Own specs; added later as separate pipeline plugins. |
| 5 | UI vs processing | Independent: the API writes and reads rows; worker Lambdas do the work. |
| 6 | Stack | Python 3.14, Flask (JSON API), SQLAlchemy Core (no ORM) + PyMySQL, plain SQL migrations, pytest. |
| 7 | Database | MySQL for the source (LMS) and the app (the existing RDS instance). |
| 8 | Data model | A core canonical model (one YAML definition) that every later stage uses. |
| 9 | Onboarding | A mapping file per NBFC (base LMS mapping + overrides + lookups), compiled to MySQL. |
| 10 | Snapshot | Each validation extracts into canonical snapshot tables; validation and formatting read the same frozen data. |
| 11 | Validation | A separate stage. SQL-first rules over canonical tables, Python only for algorithmic rules, findings in tables. Custom per-NBFC rules and policy overrides. |
| 12 | Tenancy | One NBFC per deployment (own AWS account per customer). Isolation by schema at extract; everything after carries `tenant_id` / `snapshot_id`. The API applies the deployment's tenant to every call and refuses other tenants' requests and snapshots. |
| 13 | Exceptions | Exclusions and overrides are joined into the extract in SQL (`NOT EXISTS`, `COALESCE(override, source)`), frozen per snapshot, and confirmed before generating. The LMS schema must be on the app's MySQL server: the app tables live on the customer's LMS RDS instance. |
| 14 | Validate first, always | No direct file generation. Every request is raised by a user and is a validation; files come only from a validated snapshot. |
| 15 | Request flow | Request → Exceptions → Validation → Files (§5). Exceptions are locked on submit; more exceptions means a new request. |
| 16 | File storage | Generated files and exception CSVs go to S3 (`s3://<bucket>/<prefix>/<tenant>/<batch>/<file>`), written conditionally (never overwritten), SSE encrypted. Downloads use short-lived presigned links. |
| 17 | Hosting | Serverless on AWS (§3): static frontend, Cognito sign-in, HTTP API + Lambda, worker Lambdas started by S3 job objects, EventBridge Scheduler. No servers to patch. |
| 18 | UI | Plain HTML + Alpine.js, no build step and no CDN; sidebar + top bar layout showing the deployment's NBFC (no selector). |
| 19 | Sign-in | Cognito user pool with admin-created users (TOTP MFA optional). A company identity provider can be federated later as a Cognito setting. The app keeps who may use it (and admins) in `app_user`, by e-mail. |

## 3. AWS architecture

```
 Browser ── https://<id>.cloudfront.net ─┬─ /*      → S3 UI bucket (private, Origin Access Control)
   │                                     └─ /api/*  → API Gateway HTTP API (JWT authorizer) → λ ApiFunction (Flask JSON)
   └── sign-in: Cognito managed login (authorization code + PKCE)                          │ insert row (MySQL)
                                                                                           │ put jobs/<mode>/<id>.json
                                                     jobs bucket ── S3 event (async) ──────┴──┬───────────────────┐
                                                                                              ▼                   ▼
                                                                                      λ ValidateWorker    λ GenerateWorker
                                                                                      extract → validate  format → S3 file
 EventBridge Scheduler ──▶ λ JobsFunction (retention · recovery/re-dispatch · migrate · users)

 RDS VPC subnets (no NAT): all λ ── RDS MySQL (3306, security group to security group)
                                 └─ S3 gateway endpoint (free) → files bucket + jobs bucket
```

| Component | Choice | Why |
|---|---|---|
| Frontend | Static pages in a private S3 bucket behind CloudFront; security headers (CSP, HSTS, frame deny) | No server for the UI and no cold start for pages; deploys independently of the backend |
| One origin | CloudFront sends `/api/*` to the HTTP API and everything else to S3 | No CORS; one URL for users |
| Sign-in | Cognito user pool, managed login; the browser runs the code + PKCE flow with oidc-client-ts (public client, tokens in sessionStorage) | No passwords in the app; MFA and password reset from Cognito |
| API | HTTP API with a JWT authorizer (Cognito issuer, app client audience) on every route → one Lambda running the Flask JSON API (apig-wsgi) | Tokens are verified before Lambda runs, so the in-VPC Lambda needs no internet. HTTP API is the cheaper API Gateway type; REST-only features aren't needed |
| Who may use the app | The ID token's e-mail must be an active `app_user`, else 403 and `LOGIN_DENIED` | Offboarding and roles stay in the app |
| Validate worker | Lambda, 15 min timeout, 2 GB | Validations take under 10 minutes |
| Generate worker | Lambda, 5 min timeout, 1 GB | Short runs with different sizing |
| Starting work | The API writes `jobs/{validate,generate}/<request_id>.json` to the jobs bucket; an S3 event invokes the worker asynchronously (2 retries, then an SQS dead-letter queue) | Uses the free S3 gateway endpoint (no NAT, no interface endpoint). The atomic claim on the request row makes duplicate deliveries harmless |
| Orchestration | None | Validate → Files is gated by a person; request status in MySQL is the source of truth |
| Scheduled jobs | EventBridge Scheduler → `JobsFunction`: recovery every 15 min, retention nightly, ME/W1/W2/W3 runs | Recovery also re-dispatches PENDING rows that waited more than 10 minutes (a lost dispatch) |
| Admin | `JobsFunction` via `aws lambda invoke`: `migrate`, `users add/email/disable/enable/list` | No servers or shell access needed |
| Database | Existing RDS MySQL. Up to 5 connections per Lambda environment; the account allows 10 concurrent Lambdas | No RDS Proxy needed |
| Files | Existing bucket and prefix; audited downloads via 60-second presigned links; CSV exports written to `exports/` in the jobs bucket (expire after 1 day) | Avoids the Lambda (6 MB) and API Gateway (30 s) response limits; buckets stay private |
| Packaging | Zip on the managed `python3.14` runtime (about 39 MB unzipped), built by `deploy/deploy.ps1` | Under the zip limits; no containers or ECR |
| Secrets | Database URL in SSM Parameter Store (SecureString), passed at deploy as a NoEcho parameter into the Lambda environment (encrypted at rest) | Free; no SSM endpoint at runtime |
| Infrastructure as code | `template.yaml` (AWS SAM transform), deployed with `aws cloudformation package/deploy` | No SAM CLI or Docker needed |
| Network | Created outside the stack: Lambda security group, RDS ingress rule, S3 gateway endpoint (RUNBOOK) | The stack creates no network resources |
| Monitoring | Alarm e-mails (SNS) for the dead-letter queue, API errors and job errors; CloudWatch logs kept 30 days | |
| Domain, WAF | Default `*.cloudfront.net` and Cognito prefix domain; no WAF for now | A company subdomain is configuration only |

### Code layout

| Path | Purpose |
|---|---|
| `frontend/` | Static pages (`*.html`), `js/core.js` (sign-in, API client, page shell), one script per page, `vendor/` (Alpine.js, oidc-client-ts) |
| `submissions/api/` | Flask JSON API (`/api/...`), identity from the verified token |
| `submissions/lambda_handlers.py` | Lambda entry points: `api`, `worker`, `jobs` |
| `submissions/dispatch.py` | Starting workers via S3 job objects (no dispatch locally) |
| `submissions/requests/`, `tenants/`, `bureaus/`, `adjustments/`, `users.py` | Platform repositories |
| `submissions/canonical/`, `adapters/`, `snapshot/`, `validation/`, `formats/`, `pipeline/` | Data pipeline ([PIPELINE.md](PIPELINE.md)) |
| `submissions/types/<type>/` | One folder per submission type: codes, requirements, rules, layout, pipeline |
| `submissions/reference.py` | Reference data seed (insert-missing) and the translations files use |
| `submissions/worker/` | Runs one request (Lambda) or polls for PENDING rows (local development) |
| `submissions/ops/` | Audit, recovery, retention |
| `submissions/onboarding/` | `new_tenant`, `introspect`, `dry_run` ([ONBOARDING.md](../ONBOARDING.md)) |
| `clients/` | Base and per-NBFC mappings, generated SQL |
| `migrations/` | Numbered SQL migrations (MySQL) |
| `template.yaml`, `deploy/` | AWS stack and deploy script |
| `mock_data/`, `tests/` | Mock LMS source (two NBFC schemas) and the test suite |

## 4. Platform data model

**`tenant`:** `tenant_id` (e.g. `nbfc_017`), `display_name`, `source_schema`, `mapping_path` (client folder, `clients/nbfc_017`, holding one `<type>.yaml` per submission type; an older value naming `…/mapping.yaml` is read as its folder), `extract_strategy` (insert_select / stream), `is_active`.

**`submission_request`:** one row = one unit of work.

| Column | Notes |
|---|---|
| `request_id`, `batch_id` | Primary key; rows from one click share `batch_id` (the request ID users see) |
| `tenant_id`, `run_mode` | NBFC; `VALIDATE` or `GENERATE` |
| `as_of_date`, `submission_type`, `reporting_cycle` | |
| `bureau_code` | Set for GENERATE; NULL for VALIDATE (validation is bureau-independent) |
| `snapshot_id` | The snapshot used; GENERATE rows reuse a validated snapshot |
| `status`, `active_key`, `worker_id`, `attempt_count`, `started_at`, `finished_at` | DRAFT → PENDING → RUNNING → SUCCEEDED / FAILED / CANCELLED |
| `output_file_name`, `output_file_path`, `exceptions_file_path`, counts, `error_message` | Results |

Rows per click: one per type for a validation, one per type × bureau for file generation. Duplicate guard: one open row per (NBFC, date, type, mode, bureau).

**`bureau_profile`:** member ID, header tag and file-name pattern per NBFC × bureau × type.

**`app_user`:** who may use the app (`email`, `is_active`, `is_admin`, `last_login_at`). Passwords are in Cognito, not here.

**Exceptions (adjustments):**

| Object | Purpose |
|---|---|
| `submission_adjustment` | What users capture, per request: `EXCLUDE_ACCOUNT` (loan number), `EXCLUDE_CUSTOMER` (customer ID: all their loans), `OVERRIDE` of `days_past_due`, `current_balance`, `amount_overdue` or `settlement_amount`, each with an optional reason; soft delete |
| `snapshot_adjustment` | Frozen copy of the adjustments in force when a snapshot was extracted (the audit trail) |
| `snapshot_adjustment_effect` | What each frozen adjustment hit: excluded loans, borrowers left with no loan, overrides with old → new value |
| `v_snapshot_adjustment` (view) | Each frozen adjustment with `loans_affected` (0 = not found → WARN `UCRF.ADJ.NOT_FOUND`) |

In the extract (`submissions/adjustments/sql.py`), the adjustments are frozen, resolved against the NBFC's own SELECTs, and joined into each entity's SELECT. The `c_*` tables hold the final data, so validation and the formatter need no changes.

**Reference data (master: the database).** Data lives in tables, logic in the repo:

| Table | Holds | Initial rows from |
|---|---|---|
| `ref_code` | Canonical code lists (`gender: FEMALE, MALE, …`) | `canonical/model.yaml` |
| `ref_state_pin` | PIN prefix range per canonical state | `canonical/model.yaml` |
| `ref_code_translation` | Per submission type: canonical value → file code, `is_active` (no active row = not reportable in that file) | `types/<type>/codes.yaml` |
| `ref_code_group` | Per submission type: named sets of canonical values used by rules (e.g. `card_account_type`) | `types/<type>/codes.yaml` |

`submissions.reference` runs after the migrations (jobs Lambda `migrate`, `python -m submissions.migrate`) and only inserts missing rows, so changes made in the tables survive deploys. Each validation copies the type's active translations into `snapshot_code_translation`; its code rules and the file read that copy, so an edit after validation never changes a file (re-validate to pick it up). Planned (plan 1c): an admin page with checks and audit, YAML export.

Pipeline tables (`extract_snapshot`, `c_*`, `validation_*`, `record_outcome`) are in [PIPELINE.md](PIPELINE.md). Operations: `audit_event`; `worker_heartbeat` exists but is no longer written.

## 5. Request flow

**Request → Exceptions → Validation → Files**, tied together by one request ID (`batch_id`).

| Step | What happens |
|---|---|
| 1. Request | NBFC, as-of date, submission type(s), cycle → **Create request**. It is a DRAFT, not processed yet. |
| 2. Exceptions | Optional: exclude loans or customers (bulk paste) and override values, each with an optional reason. Audited (`ADJUSTMENT_ADDED` / `REMOVED`). |
| 3. Validation | **Submit for validation** locks the exceptions (DRAFT → PENDING) and starts the worker. The extract leaves excluded records out and applies overrides, so validation runs on the remaining records. Each exception then shows *Applied (n loans)* or *Not found*. |
| 4. Files | From the validated request: confirm "N loans excluded, M overridden" (required when there are any; audited `ADJUSTMENTS_CONFIRMED`), pick bureaus, and one file per bureau is generated from the validated snapshot. Each file's exceptions CSV lists the applied adjustments. |

**Rules:**
- Exceptions are locked once submitted; more exceptions means a new request for the same date.
- One open request per NBFC, date and type; a draft can be discarded.
- Scheduled runs skip step 2.
- The Findings page's **Exclude loan** / **Override…** add to a new draft request for the same NBFC, date and type.

**Request stage** (derived, used by the dashboard and tracker):

| Stage | Rule |
|---|---|
| Awaiting exceptions | Validation rows are DRAFT |
| In progress | Validation, or a file generation from it, is PENDING or RUNNING |
| Validated, files pending | Validation SUCCEEDED; no file generated yet |
| Completed | At least one file generated successfully |
| Failed | Validation FAILED, or every file generation FAILED |
| Cancelled | Discarded draft or cancelled request |

## 6. UI

**Layout:**
- **Sidebar:** Main (Dashboard, New request, Exceptions), Reports (Submission tracker, Files, Audit), Admin (Rules & policies; NBFCs & bureaus later).
- **Sidebar footer:** queue status ("Processing normally" or "Runs waiting …") and the signed-in user.
- **Top bar:** breadcrumb, the deployment's **NBFC name**, **+ New request**.

| Page | Content |
|---|---|
| Dashboard | Period filter. Request KPI cards by stage (each opens the tracker filtered), data KPIs (records validated, acceptance rate, loans excluded / overridden, files, records submitted), needs attention, bureaus, recent requests |
| New request | As-of date, types, cycle (the NBFC is the deployment's) → creates the draft and opens its exceptions step |
| Exceptions | Drafts waiting for exceptions, or one request's step: exclude / override forms, the list with status (*Will apply / Applied / Not found / Removed*, old → new), Submit, Discard |
| Submission tracker | All runs (status, mode, bureau, date filters, paging) or requests by stage |
| Request | Rows and statuses (refreshing while active), retry / cancel, snapshot record KPIs and issues by category (as on Findings) and adjustments, generate files with the confirmation, downloads |
| Findings | Record KPIs (checked, will be reported, not reported, reported with gaps), issues by business category with plain-language effect (rules marked `expected:` in the pack show as "Spec rule (no action)" and are not gaps), filters (category, issue, effect), paging, CSV export, Exclude loan / Override… |
| Files | Generated files with bureau / period filters and downloads |
| Audit | Every action, filterable by action, user and target |
| Rules & policies | Rule catalogue with its SQL / Python check; per NBFC: on/off and outcome (with a reason), rule parameters, custom rules built from a form (preview on the latest validation) |

## 7. Security

- **Sign-in and API:**
  - Cognito sign-in; the JWT authorizer verifies every API call;
  - only active app users get in;
  - every action is audited with the user.
- **Network and data:**
  - no public network path to the Lambdas or the database from the app; the Lambdas have no internet access;
  - buckets are private and encrypted;
  - downloads use 60-second links;
  - files are never overwritten.
- **Secrets:** the database URL is only in SSM and the Lambda environment, never in the repo.
- **PII:** snapshot rows, findings and files are purged after 90 days; validation summaries are kept for trends.
- **Tenant safety:**
  - the extract reads only the NBFC's own schema;
  - member IDs come only from that NBFC's bureau profiles;
  - every query after extract is scoped by tenant or snapshot.

## 8. Cost

At about 10 uses a month, the stack costs **about $1 a month per customer account**, excluding the existing RDS and files bucket:
- Lambda, CloudFront, Scheduler, SQS, SNS, CloudWatch alarms and Cognito (under 10,000 monthly users) are within their always-free allowances.
- The HTTP API and S3 cost fractions of a cent after their first-year free tier.
- There are no hourly charges: no NAT, interface endpoints, load balancer or servers.
