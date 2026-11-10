# Same layers, different shell (design, 2026-11-04)

Status: **agreed design (2026-11-04)**; no code has changed yet. Order of work: section 5. Decisions are in [ROADMAP.md](ROADMAP.md) "Product direction":
a managed install in each client's own cloud (about 30 clients on AWS, rarfincare on Azure, at most 5 users each),
serverless only, app database Azure SQL (rarfincare) / RDS Postgres (AWS).

Why serverless, not Docker on a VM (user, 2026-11-04): there is no one to patch 30 VMs. A VM would cost about
$10–15 a month more per client and avoid the Lambda limits (3.1), but OS patching, TLS and restarts on 30 machines
need an owner. Code is deployed as a zip on the cloud's managed runtime (Functions Python, Lambda Python), so the
cloud patches the OS and runtime; container images are avoided for the same reason (their base image is ours to patch).

## 1. The rule

```
            Azure shell                          AWS shell
   Static Web App, Functions, Storage    S3 + CloudFront, Lambda (Function URL),
   queues, Blob, Entra, Azure SQL        SQS, S3, EventBridge, OIDC, RDS Postgres
                  \                               /
                   ┌───────────────────────────────┐
                   │ Layer 3  bureau_core          │
                   │ contract CanonicalBatch       │  one release,
                   │ Layer 2  staging + views      │  every client
                   │ adapters, workflow, Flask API │
                   └───────────────────────────────┘
```

- **Layers** (`packages/bureau_core/`, `submissions/` outside `submissions/hosts/`) never import a cloud SDK
  (`azure.*`, `boto3`) or a cloud's request objects. A test guards it, as `tests/test_core_boundary.py` does for the core.
- **The shell** is the entry points (triggers) plus one adapter per port (section 2). It holds no business logic.
- A host is chosen by settings at start-up. Local development and the tests use the local adapters, as today.

## 2. The ports

Each port already has a seam in today's code, except where noted.

| Port | What the layers ask for | Seam today | Local / tests | Azure (today) | AWS (new) |
|---|---|---|---|---|---|
| **HTTP API** | Run the Flask app for `/api/*` | `function_app.py` `api` (WSGI middleware) | Flask dev server | Functions HTTP trigger | Lambda Function URL (CloudFront routes `/api/*` to it), WSGI adapter |
| **Jobs** | Send "run request N" now or after a delay; deliver it to a worker once at least | `dispatch.Dispatcher` protocol; `handlers.worker(body)` | `NoDispatch` + polling worker | Storage queues, base64, `<queue>-poison` | SQS (`DelaySeconds`, max 900 s), redrive to a dead-letter queue |
| **Timers** | Recovery every 15 min; retention daily 02:00 IST | `handlers.jobs({"job": ...})` | run by hand | Timer triggers (UTC cron) | EventBridge Scheduler (time zone `Asia/Kolkata`) |
| **Admin jobs** | migrate, users, recovery, retention on demand | `/ops/{job}` + `handlers.jobs` | `python -m ...` | HTTP + function key | Lambda invoked directly with IAM (`aws lambda invoke`); no public route |
| **File store** | Write once, read, delete, list; a short-lived download link | `storage.FileStore` protocol, `make_store` / `make_exports` | `LocalFileStore` | Blob + user-delegation SAS | S3 + presigned GET URL, lifecycle rule on exports |
| **Upload intake** | Receive a workbook (up to 25 MB), stage it, run the checks | `api/inputs.py` `upload_input` (synchronous, in the request) | as today | as today | **Changes:** see 3.1 |
| **Identity** | Who is signed in, and their groups | `api/auth.py` `verify_token` / `identify` (Entra-specific) | `auth = none` | Entra token checked in the app | Generic OIDC: issuer, JWKS, audience, groups claim name from settings (3.2) |
| **Database** | A SQLAlchemy engine for the app schema | `db.make_engine` (Entra token hook) | SQLite with `automn` attached | Azure SQL, managed identity token | RDS Postgres, RDS IAM token (or a Secrets Manager password) through the same hook |
| **Config and secrets** | Settings at start-up | `config.py` (environment variables) | `.env` | App settings | Lambda environment; secrets from Secrets Manager / SSM at start-up |
| **Logs** | Python `logging`, no personal data | `logging_config.py` | console | Application Insights | CloudWatch Logs |
| **Static pages** | Serve `frontend/`; tell it the API URL and sign-in authority | `__API_BASE__` placeholder; `core.js` builds the Entra authority | file server | Static Web App, `staticwebapp.config.json` | S3 + CloudFront; headers from a CloudFront response-headers policy |

## 3. What changes, port by port

### 3.1 Upload intake (the one real behaviour difference)

On AWS a request to a Lambda (Function URL included) is limited to **6 MB**; the upload route accepts 25 MB and
stages and checks in the request. On AWS:

1. The page asks the API for an upload slot; the API returns a presigned S3 PUT URL (and records the pending upload).
2. The browser puts the workbook straight into S3.
3. The page calls "staged" (or an S3 event fires); a job on the queue loads the sheets and runs the checks, as the
   validate job does today. The page polls the upload's status, as it already polls requests.

Recommendation: use the same flow on Azure too (SAS PUT URL, same job), so both clouds behave alike and a large or
slow workbook never hits a request limit. The staging code (`inputs/`, `run_checks`) does not change; only who calls
it and when.

### 3.2 Identity

- Turn `verify_token` into a generic OIDC check driven by settings: `issuer`, `jwks_uri` (or discovery URL),
  `audience`, `groups_claim`, `user_group`, `admin_group`. Entra becomes one configuration of it (the tenant check
  `tid` stays for Entra). The app_user table, the emergency stop, the audit and admin rules are unchanged.
- The page reads `authority` and `client_id` from a per-install `config.json`, not from a URL built for Entra in
  `core.js`. The library (`oidc-client-ts`) is already generic.
- AWS clients bring their own sign-in (Entra, Okta, Google, or Cognito). The Lambda runs outside a VPC (section 7),
  so it fetches the provider's keys itself and checks the token in the app, as on Azure. No API Gateway.

### 3.3 Database

- The app tables are SQLAlchemy Core with `schema="automn"`, and the app's SQL has almost no T-SQL (a search finds it
  only in `ddl_mssql.py`, and one spot in the `azure` validation engine, to be removed with that engine). So the
  Postgres work is the DDL and the migrations, not the queries.
- Migrations per dialect: `migrations/postgres/NNN_*.sql` beside `migrations/azure/NNN_*.sql`, the same numbers, both
  checked in tests. A full DDL generator for Postgres beside `ddl_mssql.py`.
- **A migration runner** in the release (the `migrate` admin job, extended): applies pending scripts in order, records
  them in a table, one transaction each (Postgres allows DDL in a transaction). With 30 installs, nobody runs SQL by
  hand. The views run the same way (generated Postgres copies + `views-overrides/postgres`).
- Checks to add before the first AWS client: the generated Postgres views on a real Postgres with test rows (the CI
  item in the roadmap), case-sensitive text comparisons, the atomic claim of a request under two workers, the
  reference join key as an indexed expression.

### 3.4 Jobs and timers

- `dispatch.Dispatcher` gets an `SqsJobDispatcher`. The message body stays `{"request_id", "run_mode"}`; the base64
  encoding is Azure-only and moves into the Azure adapter.
- Delays above 900 s are not possible on SQS; recovery already re-dispatches, so a longer wait is done by the
  recovery timer, not the queue.
- Retries: Azure `maxDequeueCount` → SQS `maxReceiveCount` on the redrive policy; an alarm on the dead-letter queue
  replaces the poison-queue alert.
- Run time: Lambda stops at 15 minutes. Today's longest run is about 2 minutes (request 81); the batching in
  [LONG-RUNNING-JOBS.md](LONG-RUNNING-JOBS.md) is the answer when volumes grow, on both clouds.

### 3.5 Files

- `S3FileStore` and `S3Exports` beside `BlobFileStore` / `BlobExports`: write once (`If-None-Match: *`), stream,
  delete, presigned GET for downloads. Stored file URIs carry the scheme (`https://...blob...`, `s3://...`), so
  `ReadableStores` can still read older files.

## 4. Code layout

```
submissions/hosts/
  azure/   cloud.py (moved), queues.py, blob.py      Azure adapters (azure.* imports only here)
  aws/     sqs.py, s3.py, rds_auth.py                AWS adapters (boto3 imports only here)
function_app.py                                      Azure entry points (stays at the root: Functions needs it)
aws/lambda_handlers.py                               AWS entry points: api, validate_worker, generate_worker,
                                                     upload_worker, recovery, retention, admin
```

Settings pick the adapters, extending what exists: `SUBMISSIONS_STORAGE` = `local` / `blob` / `s3`,
`SUBMISSIONS_DISPATCH` = `none` / `queue` / `sqs`, `SUBMISSIONS_AUTH` = `none` / `entra` / `oidc`, the database URL's
dialect for the database. Packaging: `deploy.ps1` for Azure as today; a zip (or Lambda layer) for AWS with the same
`submissions/` and `bureau_core/`.

Infrastructure per cloud, written for the client's review and never applied by us: `infra/main.bicep` (Azure) and
`infra/aws/` (CDK in Python, decided 6.1).

## 5. Order of work

Azure (rarfincare) first; each step keeps rarfincare working, and AWS reuses the same core.

| # | Step | Sections | Azure | AWS |
|---|---|---|---|---|
| 1 | **Shell boundary**: a test that nothing outside `submissions/hosts/` and the entry points imports `azure.*` or `boto3`; move `cloud.py` and the Azure parts of `storage.py`, `dispatch.py`, `db.py` behind it. No behaviour change | 1, 2, 4 | ✓ | |
| 2 | **Identity** as generic OIDC; the page's sign-in from `config.json`. Entra keeps working | 3.2 | ✓ | ✓ |
| 3 | **Client configuration store**: versioned settings in the app database, product defaults from the YAML underneath, audit, optional approval; export / import | 8.1, 8.3 | ✓ | ✓ |
| 4 | **Admin page, part 1**: submission types × bureaus, reporting cycles, bureau profiles, retention | 8.1 | ✓ | ✓ |
| 5 | **Data-quality rules on the page**: input checks and validation rules from the catalogue (on / off, severity, parameters, new checks); bureau-mandatory rules locked; preview against the last snapshot | 8.2 | ✓ | ✓ |
| 6 | **Source adapters + mapping on the page**: source kinds (Excel, database view, snapshot files, Athena), column → canonical field and label → code mapping, views generated per dialect, "Test source". rarfincare's `wip.Vw...` views become a configured database-view source | 7, 8.4 | ✓ | ✓ |
| 7 | **Postgres**: DDL, numbered migrations, the migration runner, the generated views on a real Postgres in CI | 3.3 | | ✓ |
| 8 | **AWS adapters**: S3 files and download links, SQS, Athena, RDS IAM sign-in; tests against local stand-ins (moto, a Postgres container) | 2, 3.4, 3.5 | | ✓ |
| 9 | **Upload intake as a job** (6 MB Lambda limit); on Azure later, once proven | 3.1, 6.4 | later | ✓ |
| 10 | **AWS entry points + CDK template** (Function URL, CloudFront, SQS, schedules, RDS Postgres, IAM roles incl. the vendor's cross-account role); first install in the client's account, by them | 6.1, 6.5 | | ✓ |

Needed before step 10: the first AWS client's sign-in provider, and their Glue databases / S3 paths with a sample of
each table (7.5).

## 6. Decisions

**Recorded (user, 2026-11-04):**
- 6.1 CDK in Python; each client gets the generated CloudFormation template.
- 6.2 The client's own sign-in (Entra / Google / Okta); Cognito only as the fallback.
- 6.3 The app never connects to the LMS / LOS. Their data arrives as **snapshot files** in S3 (AWS) / Blob (Azure),
  staged into the app database when a request is placed: the target source design, section 7. The app's Lambdas run
  outside any VPC, like the Azure Function App: no API Gateway, NAT, VPC endpoints or IP allow-lists.
  (Superseded: the app inside the client's VPC reading the LMS database directly.)
- 6.4 The target is the three-layer picture: separate, configurable, modular layers (bureau engine, data contract,
  canonical data / transformation) in a cloud-neutral core, thin per-cloud shells, and source adapters for both
  Excel uploads and snapshot files (section 7), on both clouds. Azure (rarfincare) is built first; AWS reuses the
  same core.
- 6.5 A cross-account role for the vendor team (deploy, migrate, logs; no data access by default).

The options considered are kept below.

Prices are rough list prices for one region, to be checked with the AWS pricing calculator before quoting a client.

### 6.1 How the AWS install is described: CDK or Terraform

The install script creates the S3 buckets, CloudFront, the Lambdas, SQS queues, schedules, RDS
Postgres and the IAM roles in the client's account. It is written once and run 30 times.

| | AWS CDK (Python) | Terraform |
|---|---|---|
| What the client receives | A CloudFormation template (CDK writes it). The client's team can review it and deploy it from the AWS console, with no tools installed | Terraform files; the client (or you) runs Terraform |
| Install state | Kept by AWS inside the client's account (the CloudFormation stack); nothing for you to store | A state file per install, kept in an S3 bucket with locking: 30 state files to look after, and they hold resource details |
| Failed install or upgrade | CloudFormation rolls back automatically | Stops part-way; someone fixes and re-runs |
| Language | Python, same as the app | HCL, a new language for the team |
| Azure | AWS only (Azure keeps `main.bicep`) | Could cover Azure too, but Bicep is already written and working |
| Client security teams | CloudFormation is familiar to AWS-first teams | Also common, especially in larger banks |

**Recommendation: CDK in Python, handing each client the generated CloudFormation template.** No state files to
manage across 30 installs, automatic rollback, and a client can deploy it themselves, which matches "write for
review, the client creates resources". Choose Terraform only if several clients already require it.

### 6.2 How the first AWS client's staff sign in

The app needs a signed-in user and their group (user or admin). Any OIDC provider works once 3.2 is done; what
differs is who manages the users.

| Option | When it fits | Effort and cost |
|---|---|---|
| **Client's Microsoft Entra** (Microsoft 365) | The client uses Microsoft 365 / Outlook. Common among Indian NBFCs | Same as rarfincare today: an app registration and two security groups in their tenant. No cost |
| **Client's Google Workspace** | The client uses Gmail for business | Google does not send groups in the token, so roles come from the app's own user table (admin flag), not from groups. No cost |
| **Client's Okta / other OIDC** | Larger clients with a central sign-in | Same as Entra if they send a groups claim. No cost to you |
| **Amazon Cognito** in the client's account | The client has no company-wide sign-in | Users and groups kept in Cognito (by the client's admin or by you); MFA available. Free for this number of users |

**Recommendation:** use the client's own sign-in where they have one (people leave → access ends with their company
account), Cognito only as the fallback. **Needed from the client:** which of these they use, and whether their IT
will create the app registration and groups.

### 6.3 How the app reaches the client's LMS / LOS data (superseded by section 7)

Kept for the record. This decided whether the Lambdas sit inside a private network (VPC), and so the main running cost besides the
database. A fact that narrows it: **a Lambda outside a VPC has no fixed outbound IP address**, so a database
"reachable over the internet with an IP allow-list" needs the Lambdas in a VPC behind a NAT gateway with a fixed IP.

| Option | How it works | Extra cost / month | Security view |
|---|---|---|---|
| **A. Push files** (Excel / CSV) | The client's team (or a scheduled export in the LMS) puts files into the app's S3 bucket or uploads them on the page. No connection to their systems | None | Best: the app never touches the LMS. Today's workbook flow |
| **B. Database pull, same AWS account / VPC** | The LMS database is in the client's AWS. The Lambdas run in that VPC; free gateway endpoint for S3; interface endpoints for SQS and Secrets Manager | About $15–30 (interface endpoints, two zones) | Good: private traffic only |
| **C. Database pull through a NAT gateway** | The LMS database is elsewhere (another cloud, a vendor, on-premises behind a firewall) and allow-lists one fixed IP | About $35–45 plus data | Good, if the vendor allows it |
| **D. Database open to the internet, no allow-list** | TLS and a strong password only | None | Most client security teams will refuse; not recommended |
| **E. Vendor API** | The LMS vendor's API over the internet | As C if the vendor allow-lists IPs, else none | Depends on the vendor |
| **F. On-premises LMS** | A site-to-site VPN into the client's VPC, then as B | About $36 per VPN connection | Good; more setup with the client's network team |

**Recommendation:** start every client on **A** (works on day one, no network work, nothing to pay); move a client
to B or C only when they want automatic pulls. **Needed from the first AWS client:** where the LMS and LOS run (their
AWS, a vendor's cloud, on-premises), and whether the vendor offers a database replica, scheduled exports or an API.

### 6.4 Upload as a background job on Azure too

AWS needs it (3.1). On Azure the synchronous upload works within limits today (Functions allows larger requests and
longer HTTP calls), so it is a choice, not a need.

| | Same flow on both clouds | Keep the synchronous upload on Azure |
|---|---|---|
| Code | One upload path to test and maintain | Two paths; a fix must be made twice |
| rarfincare | Changes a working screen (the page shows "checking…" and refreshes) | No change |
| Large workbooks | No request limit on either cloud | Azure still bound by its HTTP limits |

**Recommendation:** one flow on both, switched on for rarfincare only after it is proven on AWS.

### 6.5 Who runs the 30 installs

Upgrades, migrations and troubleshooting have to happen in each client's account.

| Option | How it works | Fit for 30 installs |
|---|---|---|
| **A. A cross-account role for your team** | Each client creates an IAM role your team can assume: deploy the stack, run migrations, read logs and alarms; **no access to data** (no database login, no S3 read) unless the client grants it for an incident | Best: one release can be rolled out to all clients by script |
| **B. The client deploys each release** | You publish a versioned template and package; their team applies it | Slow and uneven: 30 clients upgrade at different times, so several versions are live at once |
| **C. Access granted per release** | The client opens access for a window, then closes it | Works, but each upgrade needs coordination with every client |

**Recommendation: A**, with the role's permissions written into the install template so the client's security team
reviews exactly what it can do. Also check the contract terms against the RBI rules on outsourcing of IT services
(managed service with vendor access to a regulated entity's systems); that is a question for the client's compliance
team, not a technical one.

## 7. Target source design (user, 2026-11-04; revised 2026-11-05 for the channels in §18)

The LMS / LOS data reaches the app as **snapshots** (files in object storage, read directly or through Athena), never
as a live connection to the LMS / LOS. The app stages one reporting date's snapshot into its own database when a
request is placed. Excel uploads and database views (where reachable) remain as further source kinds (§8.4).

Who fills the snapshot store depends on the channel:

| Channel (§18.1) | Snapshot store | Filled by | Read by the app through |
|---|---|---|---|
| **LMS partner** (primary) | The per-NBFC **data account** (lake) in the partner's Organization: `landing/` → `raw/` → `curated/` | The partner **pushes** LMS + LOS files to `landing/`; **our ingest** builds `raw/` (Parquet, catalogued) and **our model** builds `curated/` | Athena on `curated/` (or `raw/`), cross-account read-only grant |
| **Direct NBFC** | The NBFC's own S3 / Blob | The NBFC's own ETL / data lake (no extract in the product) | Athena tables (AWS) or the files directly |
| **rarfincare** (Azure) | None today | — | Database views in Azure SQL (7.4) |

```
LMS partner channel                                                  App database (Postgres / Azure SQL)
                     Data account (partner-owned lake)               ┌───────────────────────────────────┐
LMS ─┐  push files   landing/ ─► raw/ (Parquet, by snapshot_date)    │ stg_* staging, tagged load_id     │
LOS ─┴────────────►         our ingest   ─► curated/ (our model) ──► │ Layer 2 views → canonical c_*     │
                                          Athena, read-only grant    │ validation, findings, requests,   │
Direct channel                                                       │ users, audit                      │
NBFC ETL ──────────► NBFC's S3 / Blob (Athena tables or files) ────► │                                   │
Excel (LOS, manual sheets) ─────────► page upload ─────────────────► │                                   │
                                                                     └───────────────────────────────────┘
                                                                                     ▼
                                                                    bureau files → S3 / Blob (exports)
```

### 7.1 The pieces

| Piece | What it is | Who owns it |
|---|---|---|
| **Feed** | LMS partner: pushed files, one per entity per day, with a completion marker (contract: §18.5). Direct: the NBFC's ETL output. **No extract from a database in the product** | Partner / NBFC |
| **Ingest** (LMS partner channel) | Checks each pushed file (entities, columns, row counts, snapshot date), writes Parquet partitioned by `snapshot_date`, registers it in the Glue catalog; alerts on a missing or malformed file | Vendor code (licensed), runs in the partner's data account |
| **Data model** (LMS partner channel) | Athena SQL building `curated/` tables, shared by Intelligence and Submissions | Logic: vendor (licensed). Data: partner |
| **Snapshot store** | The lake (partner channel) or the NBFC's S3 / Blob (direct), encrypted with the owner's key; the long-term history and audit record of what was reported | Partner / NBFC |
| **Source adapter** (in the core, cloud-neutral) | On a request for date D: reads only D's rows for the switched-on submission types (an Athena query or the files) and loads them into `stg_*` with a new `load_id`, then runs the input checks: the same step and screen as an Excel upload ("Upload & check") | Product |
| **App database** | Postgres (AWS) / Azure SQL (Azure): staging for open requests plus the app's records. Retention clears old staging; the snapshots stay | Product, in the partner's / NBFC's account |

### 7.2 Rules

- **Adapters only load.** No mapping or joins in an adapter; the Excel upload, the snapshot / Athena adapter and the
  database-view adapter all write raw rows to staging. Mapping, joins (LOS ↔ LMS on the application / sanction
  number) and label → code translation are Layer 2 views, as today. (The partner channel's `curated/` model serves
  Intelligence too; Submissions may read `curated/` or `raw/`, set in the client's configuration.)
- **Mixed sources in one request:** e.g. LMS from the snapshot and LOS from Excel; both stage under the request and
  join in Layer 2.
- **A missing snapshot blocks**: a switched-on entity with no data for date D (or a day not marked complete) stops the
  request with a clear message, as a missing workbook account does today.
- **Per-client configuration** names the source kind, location (Glue database / tables or paths), entities, columns
  and which submission types use them (§8).
- **Formats:** the partner's pushed files as agreed (CSV or Parquet); `raw/` and `curated/` are Parquet for Athena;
  direct clients' files CSV (UTF-8, header row) or Athena tables.
- **Nothing leaves the owner's Organization / account:** Athena results and staging stay in the per-NBFC account;
  the vendor account holds no client data (§18.4).

### 7.3 Network and cost

- The app never connects to the LMS / LOS, so its Lambdas run **outside any VPC**, like the Azure Function App:
  Function URL, IAM role, TLS, sign-in checked in the app. No API Gateway, NAT, VPC endpoints or IP allow-lists.
  Athena, S3 and Glue are reached over their AWS APIs with IAM, across accounts by grant.
- The app's RDS Postgres is publicly accessible with IAM sign-in and TLS (no password), the same model as Azure SQL
  for rarfincare. The design assumes clients accept this (user, 2026-11-04); the fallback (7.6) is
  documented only, not built until a client needs it.
- Snapshot storage costs cents a month; Athena about $5 per TB scanned (one day's partition: cents); the app database
  stays the smallest size because it holds only open requests.

### 7.4 rarfincare (Azure)

Today it reads `wip.VwConsumerSubmissionAccountSegment` / `wip.VwCommercialSubmissionCR` in Azure SQL directly. It can
keep doing so (a "database view" source adapter), or move to snapshot files in Blob later so both clouds run one
design. No change is planned until the snapshot adapter is proven.

### 7.5 Needed before the first install

- LMS partner channel: the push-file contract (§18.5: format, one file per entity per day, naming, full or changes
  only, delivery time, completion marker) with a sample of each file; Cognito user pool details.
- Direct NBFC: their Glue databases and tables (or S3 folder layout) with a sample of each entity, and their sign-in
  provider.

### 7.6 Fallback: a client will not accept a public database endpoint

Chosen per client by one install setting (`network: public | private`); the app code is the same.

| Option | How | Extra cost / month | Effort |
|---|---|---|---|
| **A. Private RDS, Lambdas in a VPC (recommended)** | RDS has no public address. The Lambdas join the VPC. S3 through the free gateway endpoint. No SQS: a schedule invokes the worker every minute and it claims PENDING rows (today's polling worker), so no queue endpoint. Database sign-in by RDS IAM token (signed locally, no network call). The sign-in token is checked by an API Gateway JWT authorizer, because a Lambda in a VPC cannot fetch the provider's keys | Cents (API Gateway) | Medium: the second worker trigger and the API Gateway route, written once in the template |
| **B. Same as A, plus a NAT gateway** | The Lambdas keep internet access, so everything works exactly as the public setup (SQS, in-app token check) | About $35–45 | Low |
| **C. Aurora Postgres with the RDS Data API** | The database is reached over an HTTPS API with IAM; no network path to it at all, Lambdas stay outside a VPC | Aurora's minimum (or near zero with auto-pause, with a wake-up wait) | High: the app's SQLAlchemy code needs a Data API driver; response size limits. Not recommended |

**Recommendation: A**, B only where the client prefers paying to keeping two setups identical.

## 8. Client self-service configuration (user, 2026-11-04)

The client's own administrators set up and change their install from an admin page: no code, SQL or redeploy per
client. Settings live in the app database, are admin-only, and every change is recorded in the audit trail.

### 8.1 Admin page or install

| Admin page (client) | Install template (changed by a template update) |
|---|---|
| Snapshot folder paths and file names per entity (within the permitted buckets) | **Which buckets** the app's IAM role may read; a page cannot widen it |
| Column mapping: file column → canonical field; label → code (`ref_code_translation`) | Sign-in provider, client ID, groups |
| Submission types × bureaus switched on; reporting cycles | Database, network setting (7.6) |
| Bureau profiles: member IDs, short names, file-name formats (`bureau_profile`) | The release version |
| Data-quality rules (8.2) | |
| Retention days | |

### 8.2 Data-quality rules

Two kinds, both configurable:

| | Input checks (Layer 2: is the source data usable?) | Validation rules (Layer 3: will the bureau accept it?) |
|---|---|---|
| Today | `types/<type>/input_checks.yaml`: a fixed catalogue of check kinds (`required`, `pattern`, `date`, `code`, `label`, `duplicate`, `min_age`, `not_before`, …) with parameters and `block` / `warn` | `rules.yaml` (SQL per rule) with tenant rule-set and parameter overrides already supported by the core (`load_rule_set`, `load_parameters`) |
| Client can | Switch a check on or off; change its severity (block / warn); change its parameters (pattern, age, code list); **add new checks** from the catalogue through a form (pick a kind, columns, parameters, message) | Change severity and parameters (e.g. thresholds); switch off a non-mandatory rule; add a rule from the same catalogue of kinds |
| Client cannot | Write SQL | Weaken or switch off a **bureau-mandatory** rule (one the CIC spec requires: the file would be rejected); these are shown locked. Write SQL |

- **No free SQL from the page.** Rules are built from the catalogue, so they are safe to run and stay valid on
  SQLite, Postgres and T-SQL. A rule that needs SQL is added by the product team to the catalogue or the core, once,
  for every client.
- New check kinds are a product change (a new kind in the catalogue), then usable by every client from the page.

### 8.3 Controls on changes

- **Maker–checker, optional per client** (user, 2026-11-04): a setting on the admin page. On: a change to mapping,
  rules, bureau profiles or switches is saved as a draft and a second admin approves it before it is used. Off: the
  admin's change applies directly. Either way the change is versioned and audited, and the preview is available.
  Switching approval off is itself an audited change (and needs approval while it is on).
- **Versioned:** each approved change makes a new version of the client's configuration. A request records the
  version it used (validation already freezes the rule set and translations in its snapshot), so any past file can be
  explained with the settings of the day.
- **Preview before approval:** "Test source" lists the files for a date and compares headers with the mapping; a rule
  or mapping draft can be run against the last snapshot to show what would change (findings added / removed) before
  it is approved.
- **Export / import** of the whole configuration as one file, to copy a set-up between clients or keep a backup.

### 8.4 Same on Azure

The configuration, the admin page, the rules and approvals are in the core and the app database, so they are the
same on both clouds. The differences are in the shell only:

| | AWS | Azure |
|---|---|---|
| Snapshot location on the page | S3 bucket + folder path | Blob container + folder path |
| What the install permits | IAM role: read on the named buckets | Managed identity: Storage Blob Data Reader on the named containers (Bicep) |
| Generated views | Postgres | T-SQL (the existing portable view build makes both from one definition) |
| Source kinds | Snapshot files in S3 (read through **Athena** tables in the Glue catalog, or the files directly), Excel, and **database views** where the client has them | Snapshot files in Blob, Excel, and **database views** (rarfincare's `wip.Vw...`) |

The page maps every source kind the same way (a table, view or file's columns → canonical fields); the source
adapter for each kind only loads one reporting date's rows into `stg_*`.

AWS source kinds (user, 2026-11-04):
- **Athena:** the adapter runs `SELECT … FROM <glue database>.<table> WHERE snapshot_date = :date` in the install's
  Athena workgroup and stages the result. The page names the Glue database and tables; the install permits the
  workgroup, those Glue databases, read on the data bucket and write on the query-results bucket. Lambda reaches
  Athena over its public API with IAM, no VPC. Cost: about $5 per TB scanned, at least 10 MB per query: cents.
  Partitioning by `snapshot_date` (and Parquet) keeps each query to one day's data.
- **Snapshot files directly:** CSV in a path, when no Athena table exists.
- **Database views:** read with a driver per database (Postgres `psycopg`, MySQL `pymysql`, Oracle `oracledb` thin
  mode; all pure Python, fine on Lambda. SQL Server needs ODBC, a Lambda layer). Works without a VPC only if the
  client's database accepts connections from the app (publicly accessible, TLS, a read-only login). A private
  database needs `network: private` (7.6). Prefer Athena when the client has it.

### 8.5 What it replaces

| Today (rarfincare) | With 8 |
|---|---|
| Mapping in hand-written SQL views, run by the user in SSMS | Views generated from the approved mapping by the release's migration runner |
| `input_checks.yaml`, `rules.yaml` edited and deployed | Product defaults in the YAML; the client's changes layered on top from the database |
| `bureau_profile`, `ref_code_translation` edited with SQL | Admin page (the roadmap's "Reference data page") |

## 9. CI/CD and deployment (2026-11-04)

Today: no git repository; `deploy/deploy.ps1` run by hand for the one Azure install. That does not scale to 30+.

### 9.1 Source control and CI

- **Git repository** (one repo: core, app, frontend, infra, migrations), hosted on **GitHub** (decided 2026-11-04;
  GitHub Actions for CI and releases). Branch protection: changes to `main` through a pull request with CI green.
- **CI on every pull request:** `pytest` (the 684 tests, goldens byte-identical), the Node test of the File viewer,
  the view build check (`tools.build_views` output unchanged), the boundaries (core, shell, schema prefix), and the
  views + migrations run on **SQL Server and Postgres containers** with the same test rows (the roadmap's CI item).

### 9.2 Release

One versioned release (`vX.Y.Z`) built once by CI, the same bytes for every client:

| Artifact | For |
|---|---|
| Function App zip (`submissions/`, `bureau_core/`, packages) | Azure |
| Lambda zip (same code + the AWS entry points) | AWS |
| Frontend bundle (no per-client values; `config.json` written at install) | both |
| Migrations + generated views per dialect | both, applied by the migration runner |
| CloudFormation template (`cdk synth`) and `main.bicep` | install / upgrade |
| Release notes: what changed, migrations included, bureau-format changes | clients' review |

### 9.3 Deployment to the installs

- **Install registry** (vendor side, no client data): client, cloud, account / subscription, region, version, ring.
- **Rings:** a vendor test install (each cloud) → pilot clients → everyone. A release moves on only when the previous
  ring is healthy.
- **One command / pipeline job per ring** deploys a release to each install through the client's cross-account role
  (AWS) or a federated credential (Azure; no stored keys): update the stack, upload the code, run the migration
  runner, then a smoke test (health endpoint, sign-in page, a dry validation). Stops at the first failure.
- **Migrations are backward compatible** (add first, remove a release later), so rolling the code back to the
  previous version never needs a database rollback.
- **Client configuration is never in the release** (it lives in the client's database, section 8); a release can add
  new product defaults that existing configuration keeps or overrides.
- rarfincare: `deploy.ps1` stays until the pipeline exists; then it becomes a ring-1 install like any other.

## 10. Adding a submission type (e.g. MFI)

Goal: a new CIC format (MFI / JLG, later NeSL) is a **folder plus data**, with no change to the pipeline, the API or
the pages, and is switched on per client on the admin page (8.1).

### 10.1 What a new type is

| Piece | Where | What |
|---|---|---|
| File format | `bureau_core/types/<type>/`: `layout.yaml`, `codes.yaml`, `excel_layout.yaml`, `rules.yaml` | Records, fields, codes, validation rules from the CIC spec |
| Canonical entities | `model.yaml` of the type | E.g. for MFI: member, group / centre, account, address, IDs, household |
| Engine | `bureau_core/formats/<engine>/` | Reuse an existing one (TUDF segments, delimited, Excel) when the format fits; a new engine only for a new file family |
| Source mapping and checks | the type's default mapping and input checks; clients adjust on the admin page | As consumer / commercial |
| Goldens | `tests/golden/` | Expected files from a mock batch, byte for byte |

### 10.1a Output files per type (requirement, user 2026-11-04)

| Type | Files for the CIC (uploaded by hand) | For the client's review |
|---|---|---|
| Commercial | Pipe-delimited file | Review Excel |
| Consumer | TUDF, and the bureau Excel (Data Submission Form, 72 columns) | Review Excel |

This is what the app produces today (consumer: bureau Excel → TUDF converted from it → reconciliation → review Excel;
commercial: delimited file + review Excel). In the product each type's descriptor lists its outputs (`outputs:`
kind, label, uploadable to the CIC or review only), and the Files step and "Submitted files" show and label them from
that list, so a new type (MFI) declares its own.

### 10.2 Make types pluggable (work needed)

Found in the code (2026-11-04, read only). Already there: `bureau_core.types.type_definitions()` finds type folders
by their `codes.yaml`, and `submissions.pipeline` registers each folder with a `pipeline.py`. The database stores the
type as plain text (`NVARCHAR(20)`, no check constraint), so a new type needs no migration. What still ties the
product to two types:

| Where | Today | Change |
|---|---|---|
| `bureau_core/enums.py` `SubmissionType` | Fixed enum (CONSUMER, COMMERCIAL, NESL with no folder) | Built from the type folders |
| `submissions/types/<type>/file.py`, `pipeline.py` | Near-identical code per type, choosing the engine | One generic builder; the engine named in the type's descriptor |
| `submissions/tudf/labels.py` (File viewer labels) | Consumer → TUDF, commercial → delimited, by name | By the type's engine |
| `bureaus/seed.py` | Profiles and file names per named type | From the type's descriptor |
| `api/views.py` | Consumer default; "header tag required for consumer" | Default from the registry; "header tag required" from the descriptor |
| Frontend (`batch.html`, `files.html`, `bureaus.*`, `dashboard.js`, `tudf.js`, `rules.js`) | "TUDF" vs "Bureau file", header-tag column, "Loans" vs "Facilities", fixed type order | From a `/api/types` list (label, file label, noun, order, flags) |
| `inputs/loader.py`, `tudf/trace.py`, `tudf/convert.py` | CLI choices and defaults name the two types | From the registry |

Proposed **type descriptor** `bureau_core/types/<type>/type.yaml`: `submission_type`, `label`, `order`, `engine`
(`tudf` / `delimited` / a new one), `file_label`, `record_noun`, `header_tag_required`, `bureau_excel` (consumer's
Excel → converter path), `outputs` (10.1a), default bureau file-name pattern. To do:
- A **type registry** built from the descriptors; enums, bureau lists, the API and the pages read it.
- The app's per-type `pipeline.py` / `file.py` folded into the generic pipeline, driven by the type's YAML; code
  stays only for a genuinely new rule kind or engine.
- A **test that a new type folder works end to end** with no other change (a minimal sample type in the tests).

### 10.3 Needed for MFI

The CIC's MFI data-submission specification (record layout, code lists, validation rules) and a sample file the CIC
has accepted. Then: check which engine fits, write the folder, goldens, release, switch on per client.

**Spec received 2026-11-10:** [specs/UCRF_MFI_v3.3.1.md](specs/UCRF_MFI_v3.3.1.md) (UCRF for MFIs incl. SHG, v3.3.1,
effective 01-07-2026). Design, decisions and order of work: [ADR 0003](adr/0003-mfi-format.md). Still needed: a sample
file the CIC has accepted (or a CIC test window), and the first MFI client's data source.

### Order of work, added

| # | Step | Sections |
|---|---|---|
| 0 | **Git repository and CI** (tests on every change); before step 1 | 9.1 |
| 6a | **Type registry** (pluggable types), with steps 3–6 since the admin page lists types | 10.2 |
| 9a | **Release pipeline, install registry, rings, smoke test**; before the first AWS install | 9.2, 9.3 |
| later | **MFI** once its spec is available | 10.3 |

## 11. Bureau rejections and resubmissions (placeholder, to revisit)

Status: **outline only** (2026-11-04); details to be designed later.

### 11.1 Agreed so far

| What | Where |
|---|---|
| The bureau's response file, as received (evidence; can be re-read if the parser improves) | S3 / Blob, beside the submitted file (`responses/<request>/<bureau>/`) |
| Parsed rejection records: one row per rejected record (bureau, account, member, segment / field, error code, message, status open / fixed / resubmitted / accepted), linked to the request and account | App database; kept until resolved and for the audit period (small volume) |
| The resubmission: a new request with cycle RR, linked to the rejections it fixes | Same path as any request (validate, generate, manual portal upload) |

Flow: the user downloads the response from the bureau portal and uploads it against the original request → stored
and parsed, rejections listed with the request → cause fixed (source data, or an exception / override) → an RR
request for those records → the next response closes them.

### 11.2 Open questions

1. Response / error file formats per bureau (CIBIL, CRIF, Experian, Equifax), consumer and commercial: samples
   needed; each gets a reader defined as data, like the file layouts.
2. RR content: only the rejected records, or the whole file again?
3. A whole file rejected (e.g. bad header): reopen the original request instead of RR?
4. Ownership: rejections assignable to a person with a due date, or one shared list per request?

## 12. Canonical data model coverage (placeholder, to revisit)

Questions to answer:
1. Which canonical fields come from the LOS and which from the LMS (a field-by-field source sheet clients can fill)?
2. When both hold a field (name, address, sanction amount), which wins, and is a disagreement a check finding?
3. Fields the LMS must supply (DPD, asset class, overdue, closure status): the minimum list a client must provide.
4. MFI / NeSL entities (group, centre, household) in the same model or per type?

## 13. Data retention and personal data (placeholder, to revisit)

Questions to answer:
1. Retention per kind: snapshot files (client-owned), staging, canonical snapshots, generated files, response files,
   rejection records, audit trail (today: staging 90 days, exports 1 day).
2. Encryption at rest and in transit; who can read what (client staff, vendor support).
3. Deletion on request or at contract end; backups.
4. Logs and telemetry free of personal data; masking on screens and in exports.
5. Applicable rules: the Digital Personal Data Protection Act, RBI / CIC requirements on credit data.

## 14. Monitoring and support across installs (placeholder, to revisit)

Questions to answer:
1. Health signals per install (failed jobs, dead-letter queue, a request stuck, sign-in errors, migration failures)
   and where the vendor sees them without client data.
2. Alerts: to whom (client admin, vendor support), by what channel.
3. Support access: the cross-account role's read-only diagnostics; access to data only on the client's grant.
4. A vendor dashboard across all installs: version, health, last successful submission.

## 15. Releases and bureau spec changes (placeholder, to revisit)

Questions to answer:
1. How a CIC format change (a new spec version) is tracked, built, tested (goldens) and released to every client.
2. Notice to clients, and the switch-over date when a bureau sets one; both versions side by side meanwhile?
3. Release cadence and versioning; release notes for clients' change control.
4. A client delaying an upgrade: how long, and what is supported.

## 16. DQI / DQR (placeholder, to revisit)

The CICs score a submission's data quality (Data Quality Index) and report on it. Today the app has validation
findings and per-record outcomes, but no score. Questions to answer:
1. Each CIC's DQI method (fields, weights, thresholds) for consumer and commercial; specs needed.
2. Compute the expected score before submission (Layer 3, from the validation result) and show it per request?
3. A DQR (data-quality report) per request and a trend across cycles (with the roadmap's "Findings trends").
4. Read the CIC's own DQI from its response (with §11) and compare with ours?

## 17. IP protection and moat (2026-11-05)

### 17.1 What can and cannot be protected

The product runs in the client's own cloud account, and the account owner can always read what runs there: download
a Lambda / Function package, snapshot a VM's disk, pull a container image. **A VM does not change this** (its disk is
the client's to copy), so serverless stays. Code is fully private only if it runs in the vendor's account (SaaS),
which would move borrower data out of the client's account (ruled out: install model, RBI outsourcing, DPDP Act).
Confidential computing (Nitro Enclaves, confidential VMs) could hide it, but is VM-based, costly and complex: not
justified.

### 17.2 Protection layers (raise the cost of copying)

| Layer | What |
|---|---|
| Contract | Licence: no reverse engineering, no copying or modification, IP stays with the vendor, use limited to the client and install, termination; source escrow only if a client demands it. The main real protection |
| Compiled release | Python core and app compiled (Nuitka or Cython) to native modules for each cloud's runtime; no `.py` shipped. Data files bundled in the package, not loose. Frontend minified. (Check that Lambda and Functions offer the Python version used before relying on it) |
| Signed licence | Per install: client, account / subscription, expiry, enabled types and bureaus; signed with the vendor's private key, checked at start-up; invalid or expired → read-only |
| Licence heartbeat (optional, per client) | Daily call to a vendor endpoint with licence ID and version only, no client data; renew or revoke remotely |
| Release hygiene | Private GitHub repo; clients get built artifacts only; install templates hold infrastructure only |

Stays visible regardless: the generated SQL views in the client's database (from the client's own mapping), the
frontend in the browser, and content that follows the public CIC specs.

### 17.3 The moat (what a copy of the code does not carry)

1. **Proof of correctness with the CICs:** files accepted by all four CICs every cycle across clients; byte-for-byte
   goldens, coverage workbook, reconciliation, a per-release evidence pack.
2. **Rejection knowledge beyond the spec:** how each CIC actually rejects, learned from response files across all
   clients (§11, §16); each new pattern becomes a rule for every client in the next release (a network effect).
3. **Keeping up with regulation:** CIC spec versions and RBI reporting changes delivered to every client on time
   (§15); a copy goes stale.
4. **Onboarding speed:** mapping templates for common LMS / LOS products and Excel layouts; each client makes the
   next faster.
5. **Trust and compliance:** passed security reviews, audit trail, certifications (e.g. ISO 27001), references.
6. **Switching cost:** history, audit, configured mappings and rules, trained staff.

### 17.4 Design changes

1. **Engine and content released separately**, each versioned and signed: the engine (code) and the content (rule
   packs, layouts, code lists, mapping templates). Content, gated by the licence, can ship more often than the engine.
   Affects §9.2 (release artifacts) and §10 (type folders become content).
2. **Anonymous rejection and rule-hit feed** to the vendor (codes and counts, no personal data), opt-in by contract;
   with §14 monitoring.
3. **Mapping template library** for LMS / LOS vendors and common workbooks, offered on the admin page (§8).
4. **Per-release evidence pack:** golden results and the CIC acceptance record, for clients' change control.
   Signing makes content tamper-evident, not unreadable. Every snapshot records the engine and content versions
   (and the content hash) it ran with, so support reproduces a run with the exact same rules (the replay of
   captured batches already exists). Urgent fixes go out as a new signed content version (minutes, without an engine
   release) or as a client override on the admin page; content is never edited in place. Development builds accept
   a development key; production accepts only the vendor's. Findings carry rule ID and message, so clients
   understand them without seeing the rule's SQL.
5. **Release pipeline additions (§9):** compile per runtime, bundle data, sign licence and content, licence check at
   start-up.

## 18. Delivery model and data boundary (2026-11-05)

### 18.1 Channels

| Channel | Where it runs | Status |
|---|---|---|
| **LMS partner (primary)** | Accounts inside the LMS partner's AWS Organization, one set per NBFC; a plug-in add-on to the LMS, decoupled from it | Planned first |
| Direct NBFC | The NBFC's own AWS / Azure account (sections 1–17 as written) | Supported; rarfincare today |
| Hosted SaaS tier | Vendor account, multi-tenant (tables already carry `tenant_id`) | Possible later, for NBFCs without a cloud account |

Products on the same boundary: **Submissions** and the **Intelligence platform** (data model in Athena, dashboards in
QuickSight).

### 18.2 Decisions (user, 2026-11-05)

- The LMS partner runs **one AWS account per NBFC**.
- Per NBFC we add, inside the partner's Organization: a **data account** (the lake) and a **Submissions +
  Intelligence account**.
- The partner **pushes** LMS and LOS data as **files** (both from the LMS platform) into the data account's landing
  area; we do not extract from their database. (A read replica can be added by the partner if they ever need one
  for their exports.)
- The partner **owns all data**, raw and transformed. Sign-in: the LMS's **Cognito** user pool (one login).
- Support: the partner supports the LMS; we support Submissions and Intelligence.

### 18.3 Layout per NBFC

```
Partner's AWS Organization (they own every account, key and byte of data)
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│ LMS account              Data account (lake)                    Submissions + Intelligence   │
│ ┌──────────────┐  push   ┌─────────────────────────────────┐   ┌──────────────────────────┐  │
│ │ LMS (+ LOS)  │───────► │ landing/  files as pushed       │   │ Submissions app + its DB │  │
│ └──────────────┘  files  │   ▼ ingest (our code)           │   │ Athena (queries in place)│  │
│                          │ raw/      daily snapshots,      │◄──┤ QuickSight dashboards    │  │
│                          │           Parquet, documented   │   │                          │  │
│                          │   ▼ model (our code)            │   └──────────────────────────┘  │
│                          │ curated/  our data model output │    read-only, revocable grant   │
│                          │ Glue catalog; S3 encrypted with │                                 │
│                          │ the PARTNER's KMS key           │                                 │
│                          └─────────────────────────────────┘                                 │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
      ▲ vendor operator role: deploy and run pipelines; no data download by default
Vendor account: code, signed content (incl. the data model), licences, health, opt-in anonymous counts. No client data.
```

- **Landing → raw:** our ingest checks each pushed file (expected entities, columns, row counts, the snapshot date)
  and writes it as Parquet partitioned by `snapshot_date`, registered in the Glue catalog. A missing or malformed
  file raises an alert to the partner (§14).
- **Raw → curated:** our data model (Athena SQL / views) builds the curated tables used by Intelligence and by the
  Submissions source adapter (§7, §8.4).
- **Submissions** reads the reporting date through Athena, as already designed; nothing is copied out of the
  partner's Organization.
- **QuickSight** datasets (incl. its SPICE cache) and Athena query results stay in the per-NBFC account; results
  bucket on a short lifecycle.

### 18.4 Built by us, owned by them

| Item | Owner | At the end of the partnership |
|---|---|---|
| Landing and raw snapshots (open format, documented schema) | Partner | Kept; usable with any tool |
| Curated data (our model's output) | Partner (it is their data, transformed) | Last copy kept; no longer refreshed |
| Buckets, KMS key, accounts | Partner | Theirs throughout; revoking our key / role access cuts us off at once |
| Ingest and model code, data model, dashboards, Submissions | Vendor (licensed IP, §17) | Licence ends; pipelines and apps stop; contract requires removal |

The contract separates **data (always the partner's)** from **logic (always the vendor's, licensed)**. Cross-NBFC
benchmarks only from opt-in aggregates with no borrower data.

### 18.5 Open questions

1. **Push file contract:** format (CSV / Parquet), one file per entity per day, naming, full snapshot or changes
   only, delivery time, and a completion marker so ingest knows the day is complete.
2. **QuickSight:** in each per-NBFC account (recommended, simplest boundary) or one partner-wide account; who pays
   per-user pricing (on the partner's bill).
3. **Account creation:** the partner creates the two accounts per NBFC from our CDK templates (with our operator
   role), or grants us rights to create them in their Organization.
4. **Commercials:** licence and revenue share per NBFC; AWS costs on the partner's consolidated bill.
