# Test plan: LMS partnership architecture

Goes with [lms-partnership-architecture.md](lms-partnership-architecture.md). Status: proposed, 2026-11-21.

## Principles

- **No real client data in CI.** Synthetic data generated from `model.yaml`, plus the mock tenants `nbfc_001` and `nbfc_002`.
- **Fast tests stay local.** Unit and golden tests need no cloud and no database server (as today).
- **Every port is tested the same way on every implementation.** One test suite per interface (FileStore, job
  dispatch, auth, key service) runs against local, AWS and Azure implementations.
- **The bureau file is the contract.** Byte-identical golden files guard every change to the engine, packs or storage.
- **The IP and exit promises are tested**, not assumed: encryption, isolation, revocation, no PII in Account 2.

## Where each layer runs

| Layer | Runs on | When | Time budget |
|---|---|---|---|
| 1. Unit | Developer machine, CI runner | Every commit / PR | < 3 min |
| 2. Golden and parity | CI runner | Every PR | < 5 min |
| 3. Integration (containers) | CI runner with MySQL, MinIO, Azurite | Every PR | < 15 min |
| 4. Cloud integration | Dev accounts on AWS and Azure | Merge to `main` | < 30 min |
| 5. Security and IP | CI runner + dev accounts | PR (static) / merge (live) | in 3–4 |
| 6. Performance | Dev accounts | Nightly and before release | < 1 h |
| 7. Pipeline / deploy | Each ring | Every release | per ring |
| 8. Client acceptance | Client's environment | Onboarding, major releases | days |

## 1. Unit tests

Existing (34 files: adapters, canonical, validation, formatters, API, worker, storage, golden…) stay. Add:

| Area | Tests |
|---|---|
| Encryption | Parquet write → read round trip with a local key provider; wrong key fails; footer is encrypted (no readable schema) |
| Exceptions log | Reducer: ADD → SUPERSEDE → WITHDRAW gives the right current state; appends after lock are refused; ordering by timestamp is stable |
| Pack versioning | `effective_from` picks the right rule/layout for an as-of date; boundary dates; client pin overrides `stable` |
| Lite CSV loader | Template generated from `model.yaml`; missing/extra columns, bad codes and types are rejected with readable messages |
| Medallion builds | Bronze → silver mapping per entity; tokenisation keeps joins working; PII vault lookup |
| Ports | Each interface's contract suite against the local implementation |
| Core boundary | Importing the core package does not import `boto3` or any Azure SDK |
| Frontend | Existing page tests stay. Add: Data upload (template download, row errors, 1,000-record limit), Data sources, exceptions history and read-only after submit, audit pack download, connection-health banner, cycle close, admin console, usage, plan gating (locked types not reachable via UI or API) |
| Rule visibility | API returns no rule SQL / Python check to client roles (only to our roles); the page never renders it |

## 2. Golden and parity tests

- **Golden files** (`tests/golden/`): per type pack *and* per effective version. A regulatory change adds a new golden
  for the new date; the old golden must still pass for earlier as-of dates.
- **Engine parity during the DuckDB move (phase 2):** the same input through the MySQL engine and the DuckDB engine
  must give byte-identical files and identical findings and record outcomes. Kept until the MySQL engine is removed.
- **Reproducibility:** run a cycle, then re-run it from bronze with the versions in its manifest; the file hash must match.
- **Round trip:** synthetic canonical records → formatter → TUDF / delimited parser → same values (property-based,
  random records).

## 3. Integration tests (containers, every PR)

- **MySQL container:** migrations vs `tables.py` parity (`tests/test_migrate.py`), control DB, atomic claim under
  concurrent workers. Closes the gap noted in CLAUDE.md, where this only runs with `SUBMISSIONS_TEST_MYSQL_URL` set.
- **MinIO (S3) and Azurite (Blob):** FileStore contract suite; DuckDB reading and writing encrypted Parquet through
  each; Object Lock / immutability where the emulator supports it.
- **End-to-end, local:** CSV upload and an LMS-connector extract (MySQL, Postgres, SQL Server containers) → bronze →
  silver → snapshot with exceptions → validation → file → audit pack. Assert file against golden, audit pack manifest
  hashes, nothing written to Account 2 storage except control rows.

## 4. Cloud integration (dev accounts, merge to `main`)

Per cloud, two test environments: an **engine** account/subscription and two **fake client** accounts (A and B).

| Test | Expectation |
|---|---|
| Happy path | Full cycle for client A on real Lambda / Functions, S3 / Blob, KMS / Key Vault |
| Isolation | Engine working for client A cannot read client B's data or use B's key |
| Client cannot read | A principal in client A's account with full storage rights reads silver → gets ciphertext only |
| Client kill switch | Revoke the access grant → next run fails cleanly with a clear status; no partial files |
| Our kill switch | Disable client A's key → silver / gold / working store unreadable; bronze and audit packs still readable by A |
| Write-once | Overwriting a submission file or exceptions file is refused |
| Network | Engine functions have no internet egress; only the allowed endpoints resolve |
| Purge | Closing a cycle deletes the working store; retention job removes expired items only |

## 5. Security and IP tests

- **Static (every PR):** cfn-lint, cfn-guard, Bicep lint; dependency and secret scanning; no wildcard IAM / RBAC.
- **No plaintext derived data:** scan Account 1 test buckets; every silver / gold / working / exceptions object must be
  an encrypted Parquet file (encrypted footer), never plain Parquet.
- **No PII in Account 2:** scan control DB, version ledger and logs for PAN, phone, name and account-number patterns
  from the synthetic data; any hit fails the build.
- **Audit pack content:** contains findings in plain language and exceptions, never rule SQL or mapping logic.
- **No rule logic to client users:** call every API endpoint as a client role; no response contains rule SQL, Python
  check source or mapping expressions.

## 6. Performance

Synthetic datasets of 1 lakh and 10 lakh records per type.

- Budgets per stage: silver build, snapshot, validation, formatting; peak memory per function.
- Fail if a stage gets more than 20% slower or memory crosses 80% of the function's limit.
- Phase 0 spike sets the first baselines.

## 7. CI/CD pipeline tests

Pipeline: test → build once → dev → staging → canary client → all.

| Stage | Gate |
|---|---|
| PR | Layers 1–3 and static security pass |
| Build | Wheel and both cloud packages built from the same commit; versions stamped |
| IaC change preview | CloudFormation change sets and Bicep what-if reviewed; no replacement of data or key resources |
| Dev | Layer 4 passes |
| Staging | Smoke tests on mock tenants: health, sign-in, one full request per type, findings page opens |
| Canary client | **Shadow run:** the new version re-runs the client's last cycle in audit mode; file and findings diff against the live version; every difference must be listed in the release notes (e.g. the regulatory change) |
| All clients | Same smoke tests in every client's Account 2 |
| Any ring | Smoke failure → automatic rollback to the previous version |

## 8. Client acceptance (onboarding and major releases)

- Mapping check (`submissions.adapters.check`) on the client's LMS schema; zero unmapped required fields.
- First cycles in parallel with the client's current process; files and record counts compared before go-live.
- Client sign-off recorded with the version ledger entry.

## Regulatory change checklist

1. Change the type pack with a new `effective_from`; bump the pack version.
2. Add a golden for the new date; confirm earlier goldens still pass.
3. Release notes list expected file differences.
4. Shadow run on canary shows only those differences.
5. Promote to all; ledger records the version per client.

## By phase

| Phase | Tests added |
|---|---|
| 0 Spike | Encryption round trip; cross-account / cross-tenant key access; 1 lakh DuckDB baseline |
| 1 Ports | Contract suites per port; core boundary test |
| 2 DuckDB engine | Engine parity; DuckDB-compatible custom rules |
| 3 Medallion + Lite | CSV loader, silver build, round trip, connector containers |
| 4 Cross-account | Layer 4 suite; no-plaintext and no-PII scans; exceptions log |
| 5 Versioning + CI/CD | Effective-date goldens; pipeline gates; shadow run; rollback |
| 6 Azure | Same suites on Azurite and the Azure dev subscription |
| 7 Products | Goldens and rules per new pack (MFI, NeSL) and per add-on |
