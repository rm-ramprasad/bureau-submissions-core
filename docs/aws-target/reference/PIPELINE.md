# Submission Data Pipeline: Design

> How a submission file is produced: source data → canonical model → validation → file.
> The platform around it (requests, exceptions, workers, UI, AWS) is in [ARCHITECTURE.md](ARCHITECTURE.md); open work is
> in [ROADMAP.md](ROADMAP.md). The consumer file spec is [specs/UCRF_Consumer_v3.74_Spec.md](specs/UCRF_Consumer_v3.74_Spec.md).

---

## 1. Context

- Each customer (NBFC) runs its own LMS on MySQL in its own AWS account; one deployment of this app per customer. Column metadata can differ per customer.
- **Goal:** one product for all customers. Per-customer effort is limited to a **mapping** plus configuration.
- **Validation is a first-class stage.** It can run on its own before any file exists, its results are stored in tables and tracked, and it is extensible with client rules beyond UCRF.

## 2. Architecture

```
 NBFC schema ──▶ ADAPTER ─────────▶ CANONICAL SNAPSHOT ──▶ VALIDATION ──────────▶ FORMATTER ──▶ bureau files
 (custom cols)   mapping file       (core model, per run)   SQL + Python rules     UCRF layout,
                 compiled to SQL                            → findings tables      bureau stamping
```

| Layer | Per NBFC? | Owns | Never does |
|---|---|---|---|
| **Adapter** | Yes: one mapping file per submission type + lookups | Joins, filtering, unpivoting, client code → canonical value | Spec rules, rejecting records |
| **Canonical model** | No (core, versioned) | The data contract every later layer uses | — |
| **Snapshot** | No | Freezing the canonical data for one run | — |
| **Validation** | Core rules: no. Custom rules and policy: yes | Rules, outcomes, cascade, findings | Formatting |
| **Formatter** | No | Segments, encoding, header/trailer, canonical value → file code, bureau identity | Validation decisions |

**Dependency rule:** validation and the formatter depend only on the canonical model, never on each other or on adapters. Only the run orchestrator knows all layers.

**Change test:** changes when an NBFC's schema changes → adapter. Changes when the UCRF spec changes → validation/formatter. Changes when the product changes → canonical model.

**Submission types** are self-contained folders, `submissions/types/<type>/` (consumer now; commercial, NeSL later), nothing shared between them:

| File | Holds | Master |
|---|---|---|
| `codes.yaml` | Canonical value → this type's file code, per code list; named groups of values (e.g. `card_account_type`) | Initial data only: seeded into `ref_code_translation` / `ref_code_group`; **the database is the master** (the seed inserts missing rows, never overwrites) |
| `requirements.yaml` | Canonical columns the type needs (generated REQUIRED rules, mapping coverage) | Repo |
| `rules.yaml` | The type's rule pack (rules, categories, record decision) | Repo |
| `layout.yaml` | The file layout, rendered by a generic engine | Repo |
| `pipeline.py`, `file.py` | Wiring: mapping → snapshot → rules → file. The registry discovers each folder with a `pipeline.py` | Repo |

## 3. Canonical model (core)

### 3.1 Single source of truth

`submissions/canonical/model.yaml` defines every entity, column, type, key and code list, for **all** submission types. It holds no type knowledge (what a type requires or how it codes a value lives in the type folder). **Everything is derived from it** so nothing drifts:

| Derived artifact | How | Used by |
|---|---|---|
| SQLAlchemy tables for `c_*` | Built at import (`canonical/schema.py`) | Snapshot, validation, formatter |
| Record dataclasses (`AccountRecord`, …) | Built at import | Python rules, formatter |
| Reference data `ref_code`, `ref_state_pin` | Seeded after migrations (`submissions.reference`, insert-missing only) | Validation (code lists, PIN/state) |
| MySQL DDL | `python -m submissions.canonical.generate` prints it: copy what changed into a new `migrations/NNN_*.sql` (applied migrations are never edited). Parity: `tests/test_migrate.py` against MySQL | Deployment |
| Mapping template + contract check | `clients/_template/`, `submissions.adapters.check` | Adapter onboarding |

Snapshot tables have a surrogate `row_id`, a `snapshot_id` foreign key with `ON DELETE CASCADE` (purging a snapshot purges its rows), indexes led by `snapshot_id`, and **no NOT NULL or unique constraints**. The snapshot holds what the source holds, and validation judges it. "Required" is a validation rule, not a DB constraint.

It is versioned (`canonical_version`). A change is a product release, not a client change.

### 3.2 Entities

Normalised, not tied to any file layout. Code columns hold **canonical values** (`FEMALE`, `HOUSING_LOAN`, `MAHARASHTRA`), one taxonomy per concept for every type: mappings translate client codes in, each layout translates out (`translate:`). Every table carries `snapshot_id`.

| Table | Grain | Key fields |
|---|---|---|
| `c_party` | borrower | party_key, full_name, birth_date, gender, occupation, income, income_basis, income_period |
| `c_party_identifier` | party × ID | id_type (PAN, PASSPORT, …), id_number, issue_date, expiry_date |
| `c_party_phone` | party × phone | number, extension, phone_type, priority |
| `c_party_email` | party × email | email, priority |
| `c_party_address` | party × address | line1–5, state_code, pin_code, address_category, residence_code, priority |
| `c_account` | account | account_key, account_number, account_type, dates (opened, last payment, closed, reported), amounts (sanctioned, balance, overdue, limits, EMI, write-off, settlement, collateral), DPD, asset class, facility status, suit status, ROI, tenure, payment frequency, previous-value fields (TL.16–20) |
| `c_account_holder` | account × party | ownership_indicator (INDIVIDUAL, JOINT, …). **One UCRF record per row.** |
| `c_account_history` | account × month | TH fields (optional) |
| `c_relationship` | borrower × related party | Commercial: director, partner, holding company … (related type, relationship type, identifiers, address, contacts, control %) |
| `c_guarantor` | account × guarantor | Commercial: same related-party columns, plus guarantee invocation date |
| `c_security` | account × security | Commercial: value, currency, security type, charge classification, valuation date |
| `c_cheque_dishonour` | account × cheque | Commercial: date, amount, instrument, times dishonoured, reason |
| `c_extension_attribute` | entity × key | NBFC-specific values for custom rules (`entity, entity_key, attr_name, attr_value`) |

Business borrowers use `c_party` (legal constitution, business category, industry type, ratings …), `c_party_identifier` (CIN, TIN, Udyam … as `id_type` values) and `c_party_address` (location types as `address_category` values); credit facilities use `c_account`. Columns a type does not use stay NULL.

Amounts are `DECIMAL(20,2)` in the model. The formatter rounds to whole rupees per the spec.

## 4. Adapter (per-NBFC mapping)

### 4.1 Mapping file

One YAML per NBFC **and submission type** (`clients/<tenant>/consumer.yaml`; `tenant.mapping_path` names the folder) states **how its schema reaches the canonical model**. A **base mapping** (`clients/_base/<type>.yaml`) covers the standard LMS schema, and each NBFC file only **overrides** what differs. Commercial vs consumer is usually a `where:` filter on the same LMS tables. The format reference is `submissions/adapters/mapping.py`; the sketch below is the original design.

```yaml
tenant: nbfc_017
extends: base_lms_v1          # base mapping for the standard LMS schema
source_schema: nbfc_017
canonical_version: "1.0"
party:
  fields:
    birth_date: c.dob_dt                       # override: renamed column
account:
  from: "{s}.loan l LEFT JOIN {s}.loan_ext x ON x.loan_id = l.loan_id"
  fields:
    days_past_due: "COALESCE(l.dpd_days, x.overdue_days)"
party_phone:
  unpivot:                                     # wide source columns → canonical rows
    - { number: c.mobile_no,  phone_type: "01", priority: 1 }
    - { number: c.alt_mobile, phone_type: "01", priority: 2 }
extensions:
  account: { scheme_code: x.scheme_cd }        # → c_extension_attribute
lookups:                                       # client code → canonical value
  account_type: { HL: HOUSING_LOAN, PL: PERSONAL_LOAN, GL: GOLD_LOAN }
  gender: { M: MALE, F: FEMALE, T: TRANSGENDER }
```

### 4.2 Mapping compiler

- Compiles base + overrides into **plain MySQL**: one `SELECT` per canonical entity (several named SELECTs combine with `UNION ALL`, which is how wide columns become rows). Each statement is readable, reviewed and committed under `clients/<tenant>/generated/`. A hash of the compiled SQL is stored on each snapshot as `mapping_version`.
- **Lookups** compile inline to `CASE <expr> WHEN 'HL' THEN 'HOUSING_LOAN' … ELSE <expr> END`. There's no lookup table, so it works whichever server the source is on. Lookup values must be canonical values of the column's code list (checked when the mapping loads). An unmapped value passes through as the raw source value, so validation reports it (not a valid code) rather than dropping it; a client code that already equals a canonical value needs no lookup.
- **Extract strategy** is configurable:
  - `insert_select`: our schema is on the same MySQL server/cluster, so run `INSERT INTO c_* SELECT …` directly (fastest).
  - `stream`: the source is a separate read replica, so stream the `SELECT` results in batches into our `c_*` tables.

### 4.3 Contract check

`check-mapping` verifies that every canonical field the type requires (`requirements.yaml`) is mapped, the SQL compiles against the schema (`EXPLAIN`), result types are compatible, and every distinct source code has a lookup entry.

## 5. Canonical snapshot

- Each extract creates an `extract_snapshot` (snapshot_id, tenant, submission_type, as_of_date, canonical_version, mapping_version, status, row counts) and fills the `c_*` tables with that `snapshot_id`.
- **One snapshot serves a whole batch for a tenant and type.** It is validated once, then stamped per bureau, so 4 bureau files cost one extract and one validation.
- **Why snapshot rather than validate live views:**
  - **Consistency:** the file always matches what was validated.
  - **Audit:** you can show exactly what was submitted.
  - **Tracking:** findings join to stable rows.
  - **Isolation:** one read from the LMS, then all work happens on our tables.
- **Retention:** snapshots are purged after N days (configurable, PII). Findings summaries can be kept longer.

## 6. Validation

### 6.1 Rule catalogue

**As built:**
- **Built-in rules ship with the code** as a versioned YAML pack, `submissions/types/consumer/rules.yaml`, like the canonical model.
- **REQUIRED and ENUM rules are generated**: REQUIRED from the type's `requirements.yaml`, ENUM for every code column whose list the type translates (a value without an active translation cannot be written). Validation first freezes the type's active rows of `ref_code_translation` into `snapshot_code_translation`; the ENUM rules and the file writer both read that copy, so a file always matches what was validated. The pack sets their outcomes per entity or column. Pack SQL uses canonical values and `ref_code_group` (`:submission_type` is bound by the engine).
- **`validation_rule` holds only per-tenant custom SQL rules.** Their IDs may not start with `UCRF.`.
- **`validation_policy`** overrides the enabled flag and/or outcome of any rule per tenant.
- **Rule kinds:**
  - `sql`: must use `:snapshot_id`, and returns `row_id, account_key, party_key, value`;
  - `python`: a registered function in `python_rules.py`;
  - `cascade`: a record decision.

Original column design, now split between the pack and the table:

| Column | Notes |
|---|---|
| `rule_id` | e.g. `UCRF.TL.CLOSED_WITH_BALANCE`, `NBFC017.HL_NEEDS_COLLATERAL` |
| `rule_pack` | `UCRF-3.74` (core) or `CUSTOM` |
| `tenant_id` | NULL = global; set = that NBFC only |
| `submission_type`, `scope` | CONSUMER…; FIELD / SEGMENT / RECORD / FILE |
| `engine` | `SQL` or `PYTHON` |
| `sql_text` / `python_ref` | The check |
| `segment`, `field` | What the finding points at |
| `default_outcome` | WARN / REJECT_FIELD / REJECT_SEGMENT / REJECT_RECORD / REJECT_FILE |
| `message`, `is_enabled`, `version` | |

`validation_policy (tenant_id, rule_id, is_enabled, outcome)` overrides per NBFC. UCRF's "configurable reject record" items are simply policy rows.

### 6.2 Rule engines

**SQL rules** (the majority, including all custom rules) run on the canonical tables:
```sql
-- UCRF.TL.CLOSED_WITH_BALANCE
SELECT a.account_key, h.party_key, a.closed_date AS value
FROM c_account a JOIN c_account_holder h ON h.snapshot_id = a.snapshot_id AND h.account_key = a.account_key
WHERE a.snapshot_id = :snapshot_id AND a.closed_date IS NOT NULL AND a.current_balance > 0
```
The runner wraps each rule as `INSERT INTO validation_finding … SELECT`. Rules are written once, against canonical tables, for every customer.

**Python rules** cover the few that SQL can't express well: Aadhaar Verhoeff, name rules (tokens, initials, S/O descriptors, salutations), phone pre-edits, and the full email check set.

**Custom rules** use the same mechanism: SQL against canonical tables plus `c_extension_attribute`. A declarative form (required / enum / range / compare) compiles to SQL later.

### 6.3 Outcomes and cascade

After all rules run, the **cascade** (Python, ordered) computes the final status per record (`c_account_holder` row):
1. Field rejects drop the field, or apply its default.
2. Segment rejects drop the segment.
3. Re-check minimums: PN valid, ≥ 1 valid PA, PT or a qualifying ID (accounts opened on/after 01-06-2007), TL valid.
4. Any REJECT_RECORD, or a failed minimum → record rejected.
5. Any REJECT_FILE → the snapshot is not formattable.

The result is written to `record_outcome (snapshot_id, account_key, party_key, outcome, reason_rule_id)`. `WARN` findings never block anything.

### 6.4 Findings (tracked in tables)

- `validation_finding`: snapshot_id, account_key, party_key, rule_id, segment, field, outcome, value, message.
- `validation_summary`: snapshot_id, rule_id, outcome, count. Feeds the UI and trends across runs.
- CSV/XLSX is only an export of these tables.

## 7. Formatter

The file format is **data, not code**. A layout YAML describes it, and a generic TUDF engine renders it:

| Part | Where | What it holds |
|---|---|---|
| Layout | `submissions/types/consumer/layout.yaml` | The whole format: encoding (tag style, record separator, record end, trailer), the 146-byte header, the segments in order with their sequence labels, repeat caps and sort order, and every field (tag, source, `translate`, format, max length, cap, default, conditions) |
| Engine | `submissions/formats/tudf/` | Generic: `layout.py` loads and checks the YAML; `engine.py` renders; `snapshot.py` reads accepted records and validation drops; `parser.py` reads a file back |
| Formatters | `submissions/formats/tudf/formatters.py` | The named algorithms a layout applies: dates, amounts, signed amounts, rate, lookup maps; word fitting for names and address lines; the phone list, ID number and PIN rules |

**How it works:**
- **Input:** a validated snapshot plus a `BureauProfile`. Only records whose `record_outcome` is accepted are written, with REJECT_FIELD and REJECT_SEGMENT drops applied (history rows are written as loaded).
- **Output:** the header, then one record per account × holder (the layout's segments, then the record end), then the trailer. Repeating segments are numbered in the order written; an instance with no value for a `required` field is left out.
- **Codes:** `translate: <code_list>` on a field writes the type's file code for the canonical value (the snapshot's frozen copy, `snapshot_code_translation`; a snapshot without one must be validated again); no active code leaves the field out (validation has already flagged it). `when`, `default` and formatter args use canonical values.
- **Bureau stamping:** member ID, header segment tag and file name come only from the bureau profile.
- **Byte-identical duplicate records** are counted and left out (spec §1.4).

**To change the format** (for example a new field, a length, or a cap):
1. Edit the layout YAML. It is checked when it loads: unknown keys, formats or sources, bad tags, or a header that doesn't add up to its length all fail immediately.
2. Run the tests. `tests/test_ucrf_golden.py` compares the generated files byte for byte with `tests/golden/`.
3. If the change is intended, review the difference, then regenerate the golden files (`REGENERATE_GOLDEN=1`).

A new algorithm is a new named formatter, which any layout can then use.

**Commercial** is pipe-delimited, not TUDF: `types/commercial/layout.yaml` is rendered by `submissions/formats/delimited/` (header; per borrower and branch a BS line with its AS / RS lines; per accepted facility a CR line with its GS / SS / CD lines; TS with the BS / CR counts; every line ends with `|` and CRLF). Fields are positional and use the same keys as TUDF fields, plus `const`, `blank`, `if_present`; golden files: `tests/test_commercial_golden.py`.

## 8. Onboarding an NBFC

1. `introspect --schema nbfc_017`: diff against the base LMS schema and list the differences.
2. Write `clients/nbfc_017/consumer.yaml` (overrides only) and fill in the lookups.
3. `check-mapping`: coverage, compile, types, unmapped codes.
4. `extract --dry-run`: row counts, null rates, unmapped-value report.
5. Validate-only run: review the findings with the NBFC.
6. Tenant config: member IDs per bureau, policy overrides, custom rules → go live.

## 9. Code layout (pipeline part)

```
submissions/
  canonical/                    # model.yaml (source of truth), model loader, generated dataclasses, DDL helper
  adapters/                     # mapping loader (base + override), compiler, lookups, extract runner, check-mapping
  snapshot/                     # snapshot lifecycle, retention
  validation/                   # rule engine, SQL runner, python rules, cascade, policy, summary
  formats/tudf/                 # generic TUDF engine: layout loader, formatters, renderer, parser
  types/consumer/               # codes, requirements, rules, layout (YAML) + pipeline.py, file.py
  reference.py                  # reference data seed (insert-missing) and active translations
clients/
  _base/consumer.yaml           # standard LMS schema mapping, per submission type
  _template/consumer.yaml       # copied for a new NBFC
  <tenant>/consumer.yaml, generated/consumer/*.sql
```

## 10. Open questions

Open pipeline questions (TLV encoding against a CIC sample file, the real base LMS schema, TH history) are tracked in
[ROADMAP.md](ROADMAP.md).
