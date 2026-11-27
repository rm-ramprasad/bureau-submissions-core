# Roadmap

Open work only. Done work is described in [ARCHITECTURE.md](ARCHITECTURE.md) and [PIPELINE.md](PIPELINE.md), and is in
the git history.

## Target architecture (LMS partnership)

Design: [plans/lms-partnership-architecture.md](plans/lms-partnership-architecture.md) · tests:
[plans/test-plan.md](plans/test-plan.md). Not started. Each phase ends by deleting the old path it replaces and
updating the current docs.

- [ ] 0 Spike: DuckDB in Lambda over SSE-KMS Parquet with the cross-account key (client admin sees ciphertext only), 1 lakh records < 5 min, 10 lakh via Step Functions Distributed Map; Parquet modular encryption with Key Vault (Azure); synthetic data generator
- [ ] Before phase 4: confirm with a pilot NBFC the SCP / contract blocking centralized root access on Account 2
- [ ] 1 Ports (dispatch, auth, key service), `boto3` out of core, core wheel, basic GitHub Actions CI
- [ ] 2 Engine on DuckDB with parity to today → remove MySQL engine and SQLite shims
- [ ] 3 Bronze / silver (with history) / gold, Lite CSV loader, LMS connectors; pages: Data upload, Data sources → remove generated adapter SELECTs
- [ ] 4 Account 2 setup and handover, key account, exceptions log, audit packs, client template; pages: exceptions history, audit pack download, connection health, cycle close, rule SQL hidden from client roles, Cognito in Account 2 → remove `submission_adjustment`, single-account stack
- [ ] 5 Pack versioning, CI/CD rings, metering; pages: admin console, usage, plan gating, pack version on requests/files → remove manual `deploy.ps1`
- [ ] 6 Azure host (same pages, Entra ID sign-in)
- [ ] 7 MFI / NeSL packs, Complete add-ons, AI/BI
- [ ] 8 Final cleanup sweep (see "Retiring old code" in the design)

## Inputs needed

| Item | What is needed | Blocks |
|---|---|---|
| Base LMS schema | The real standard LMS schema and one NBFC customisation sample, to write `clients/_base/consumer.yaml` for real and make the mock data realistic | Onboarding real NBFCs |
| Bureau values per NBFC | Real member IDs, header tags and file-name formats (`bureau_profile`; placeholders today) | First live submission |
| CIC sample file | Re-confirm the encoding decision: segment tags without a length prefix (`PNN01`), no separator or line break between records (`encoding:` in `submissions/types/consumer/layout.yaml`). If a bureau differs, add a per-bureau layout override (`extends:`) | First live submission |
| Re-runs and retention | Keep every version of a generated file, or only the latest? Retention period (90 days today) | — |
| Commercial: location type for individuals | Individuals reported commercially (constitution 90) have consumer-style addresses (`PERMANENT`, `RESIDENCE` …) with no commercial location type (8.4); today untranslated, so flagged. Which code should they get? | Commercial for individual borrowers |
| Commercial: weekly / fortnightly / daily repayment | No code in 8.18; reported as 08 Others (`types/commercial/codes.yaml`). Confirm with the CIC | — |
| Commercial: cheque dishonoured on a closed account | §7.8 says report it; catalogue 8.20 has only 01 Insufficient Funds. `ACCOUNT_CLOSED` is untranslated (flagged). Which code? | — |
| Commercial: CR 26 Open / Closed by Payment | Derived from `date_closed` when no special status is set (layout, phase 4). Confirm this matches how the CIC reads it | — |
| Commercial: CR 35 Wilful Default | 1 when `wilful_default_date` is set or the status is a consumer-style wilful status. Confirm with an NBFC how wilful default is held in the LMS | — |
| TH history | Is a one-time historical load of account history needed? If so: should validation findings on history rows (e.g. an invalid asset class) drop TH fields like other segments? Today TH is written as loaded (`drops: false` and `check: not_null` in the layout) | — |

## Product

- **NBFCs & bureaus page:** a read-only view of tenants and bureau profiles (the sidebar shows it as "soon").
- **Findings trends:** trends across runs from the kept `validation_summary`.
- **Commercial pipeline:** needs its spec.
- **NeSL pipeline:** its own spec.
- **Bureau file viewer for large files (parked 2026-11-19):** measured on 1 lakh records (55 MB consumer / 67 MB
  commercial), `tudf-reader.js` takes ~1.5 s but ~0.7–1 GB of browser memory, made worse by Alpine wrapping the result
  in reactive proxies. Plan: read lazily (one scan keeps record positions + name / account / ID numbers for the list and
  search, and the structure problems; a record is split into fields only when opened), keep the parsed file out of
  Alpine (`Alpine.raw` / outside the component), chunked reading with progress. Separately, inline opening
  (`/requests/<id>/file?view=1`, gzipped) stops at 4.2 MB compressed (~50–70k consumer records; Lambda's 6 MB
  response cap); beyond that the page offers download + drop. Raising it needs S3 CORS + CSP `connect-src` (the files
  bucket is a stack parameter) or a Lambda function URL with response streaming.
- **Bureaus page and Admin page** (from bureau-submissions-core): Bureaus needs `/bureaus` endpoints on
  `bureau_profile`; Admin (bureau switches, reporting cycles, config versions; no data source) needs a migration.

## Operations and AWS

- **Users:** add e-mails for any existing app users (`users email`) and send their Cognito invites (RUNBOOK "Users").
- **Deploy from GitHub:** a GitHub Actions workflow with an IAM role through OIDC (no stored keys):
  - `frontend/**` changes run `deploy.ps1 -FrontendOnly`;
  - other changes run `-BackendOnly`.
  This also replaces deploying as the root user.
- **Hardening:**
  - make the RDS instance not publicly accessible;
  - remove the `49.43.251.101/32` rule from its security group;
  - add a monthly budget alert (for example $5).
- **Company subdomain:** an ACM certificate in us-east-1, a CloudFront alias, and the Cognito callback URLs.
- **Company identity provider:** federate it in Cognito (SAML/OIDC) when needed; no code change.
- **More capacity:** if usage grows, request a higher Lambda concurrency quota (10 today).
