# Onboarding a new NBFC

From "we have access to the NBFC's schema" to "validated and ready to generate files".
Run every command from the project root with `SUBMISSIONS_DATABASE_URL` set
(and `SUBMISSIONS_SOURCE_DATABASE_URL` if the LMS schemas are on another server).

## 1. Register the tenant

```powershell
.\.venv\Scripts\python -m submissions.onboarding.new_tenant --tenant nbfc_017 --name "Acme Finance" --schema acme_lms
```

- Creates the `tenant` row and one mapping file per submission type (`clients/nbfc_017/consumer.yaml`, `commercial.yaml`) from `clients/_template/`.
  A type is offered for the NBFC only while its mapping file exists: delete `commercial.yaml` for an NBFC without a commercial portfolio
  (the same steps below apply to `commercial.yaml`, against the NBFC's business tables).
- It never overwrites an existing mapping file.
- Add `--placeholder-bureaus` to get dummy bureau profiles for testing. Real member IDs come in step 6.
- Use `--strategy insert_select` only if our app tables are on the same MySQL server as the NBFC schema.

## 2. See how the schema differs from the base LMS layout

```powershell
.\.venv\Scripts\python -m submissions.onboarding.introspect --schema acme_lms
```

The report lists:
- missing and extra tables and columns compared with the reference schema;
- **every column the base mapping uses that this schema does not have**. Each of these needs an override in the tenant mapping.

## 3. Write the overrides

Edit `clients/nbfc_017/consumer.yaml` and list only what differs. The file has commented examples, and the full format is in `submissions/adapters/mapping.py`. Typical overrides:

- **Renamed columns:** `party: {columns: {birth_date: b.dob}}`
- **Extra joins:** `account: {from: "{schema}.account_details a LEFT JOIN {schema}.loan_ext x ON ..."}`
- **Different code values:** `lookups: {account_type: {HOME: HOUSING_LOAN}}` (client code → canonical value from `submissions/canonical/model.yaml`; merged into the base lookups)
- **NBFC-specific fields for custom rules:** an `extension_attribute` entity

Run `introspect` again with `--mapping clients/nbfc_017/consumer.yaml`. It should report that the mapping resolves fully.

## 4. Check the mapping against the live schema

```powershell
.\.venv\Scripts\python -m submissions.adapters.check clients/nbfc_017/consumer.yaml --schema acme_lms --as-of 2026-09-30
```

The check confirms:
- every compiled SELECT runs;
- the row count for each canonical entity;
- **source codes with no lookup entry** (add them to `lookups`).

It must print `OK`.

## 5. Dry run (extract + validate, no request)

```powershell
.\.venv\Scripts\python -m submissions.onboarding.dry_run --tenant nbfc_017 --as-of 2026-09-30
```

The report covers:
- rows extracted;
- records accepted and rejected;
- code values that still aren't translated;
- the share of empty values per canonical column;
- the top findings.

Add `--keep` to keep the trial snapshot and browse its findings in the UI (`/snapshots/<id>/findings`). Review the findings with the NBFC. Then fix the data at source, adjust the mapping, or agree on policy overrides:

```python
from submissions.validation.rules import set_policy
set_policy(engine, "nbfc_017", "UCRF.TL.WRITTEN_OFF_AMOUNTS_MISSING", outcome="REJECT_RECORD")
```

## 6. Bureau profiles

Load the member ID, short name, header tag and file-name pattern each bureau issued to this NBFC into `bureau_profile` (one row per bureau × submission type), using `BureauRepository.upsert`. Placeholder IDs must never reach a bureau.

## 7. Go live

Commit `clients/nbfc_017/consumer.yaml` and regenerate the reviewed SQL:

```powershell
.\.venv\Scripts\python -m submissions.adapters.compiler clients/nbfc_017/consumer.yaml --schema acme_lms --out clients/nbfc_017/generated/consumer
```

Then raise a **Validate** run in the UI, review it, and use **Generate files from this validation**.
