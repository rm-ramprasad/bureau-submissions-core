# Plan: Commercial UCRF + per-type modules + reference data in the database

Status as of 24-10-2026. Phases 1, 1b, 1c (items 1, 4, 6) and 2 merged into main; migrations 012-014 executed.
Phases 3 and 4 merged. Phase 5 (commercial pipeline registered: COMMERCIAL offered in the app) done on branch
`phase-5-commercial-pipeline` (no DDL). Consumer golden files byte-identical throughout. Commercial is complete
end to end; remaining: the admin page for reference data (1c items 2, 3, 5) and the ROADMAP open questions.
Spec: [docs/specs/Commercial_UCRF_V3_10.md](../specs/Commercial_UCRF_V3_10.md).

## Decisions (agreed)

| # | Topic | Decision |
|---|---|---|
| 1 | Canonical model | **One** model for all submission types (`submissions/canonical/model.yaml`): entities, columns, types, keys only. No type knowledge (no `required`, no file field refs, no file codes). Snapshot **tables**, not views. |
| 2 | Canonical values | Code columns hold **standard values** (e.g. `FEMALE`, `HOUSING_LOAN`, `MAHARASHTRA`). Mappings translate client codes in; each file writer translates out. |
| 3 | Product / status taxonomies | **Option (a)**: one neutral taxonomy per concept (e.g. one product list covering consumer account types *and* commercial credit types); each type translates to its own codes. |
| 4 | Modules per type | Separate, no overlaps: `submissions/types/<type>/` with `codes.yaml`, `requirements.yaml`, `rules.yaml`, `layout.yaml`, `pipeline.py`. A new type (NeSL, MFI) = new folder + reference rows. |
| 5 | Mappings | One file per type per NBFC: `clients/<tenant>/consumer.yaml`, `commercial.yaml` (+ `clients/_base/<type>.yaml`). Commercial vs consumer is usually a `where:` filter on the same LMS tables; a type may also use different source tables. |
| 6 | Reads / writes | One extract per submission type → own snapshot → own rule pack → one file per bureau. |
| 7 | **Master of reference data** | **The database**, editable without a deploy (see below). Logic stays in the repo. |
| 8 | Commercial file | Pipe-delimited, CRLF, **trailing `|` at the end of every line**. LMS has all segments (BS/AS/RS/CR/GS/SS/CD). |
| 9 | Commercial member IDs | Placeholders in `bureau_profile` (table, editable without deploy). |

### Data in the database, logic in the repo

| Kind | Examples | Master |
|---|---|---|
| Reference data | Translations (`FEMALE → 1`), code groups (card types), PIN ranges, bureau member IDs, rule parameters | **Database** |
| Canonical code lists | `ref_code` values | **Database**, guarded: adding is free; renaming/deleting a value used by rules or mappings is blocked |
| Logic | Rule SQL, record decision (cascade), file layouts, canonical tables | **Repo** (tests + golden files, deploy) |

Safeguards to build with DB-master:
1. Seed is **insert-missing only** (YAML = initial data for a new installation / customer account; never overwrites edits).
2. Admin page **Reference data** (like Rules & policies) with checks: translation points at an existing canonical value; output code fits the spec length; canonical values used by rules cannot be deleted.
3. Every change audited (`audit_event`: who, when, old → new).
4. **Freeze per snapshot**: validation records the translations it used; file generation uses that copy, so the file matches what was validated.
5. Export to YAML (review, or copy to another customer account).
6. No reference values hard-coded in Python: PIN ranges from `ref_state_pin`; salutations a rule parameter (`name_salutation_pattern`). **Decided to stay in code:** canonical values that name a meaning the logic depends on (`MOBILE`, `NOT_CLASSIFIED`, `FOREIGN`, `AADHAAR`, `G_RAM_G`, which ID type uses which `id_pattern_*`), and algorithms (Verhoeff, phone edits, e-mail rules): editing them in the database could only break validation. ID patterns were already rule parameters.

## Phases

| Phase | Content | DDL | State |
|---|---|---|---|
| 1 | Generalise engines: record keys in model, pack discovery / prefix / categories / cascade from the pack | No | **Done** (uncommitted), 429 tests passed at that point |
| 1b | Per-type folders, canonical values, reference tables, consumer rewritten — consumer files byte-identical (golden tests) | Yes (012) | **Done** (uncommitted), 434 passed |
| 1c | DB-master safeguards 1–6 above | Yes (snapshot translation copy, maybe audit fields) | **1, 4, 6 done** (seed insert-missing; freeze per snapshot, migration 013; PIN ranges from the DB, salutation parameter). Left: 2, 3, 5 (admin page) after commercial |
| 2 | Commercial model: extend `c_party` / `c_party_address` / `c_account`; new `c_relationship`, `c_guarantor`, `c_security`, `c_cheque_dishonour`; neutral product/status taxonomy (union of both specs); commercial mock data + `clients/_base/commercial.yaml` | Yes (014) | **Done** (branch `phase-2-commercial-model`), see below |
| 3 | `types/commercial/rules.yaml` + cascade (record = facility × borrower via `c_account_holder`; borrower reject rejects all its facilities; RS optional for credit type 9026) | Seed rows | **Done** (branch `phase-3-commercial-rules`), see below |
| 4 | Generic pipe-delimited engine (`formats/delimited/`) + `types/commercial/layout.yaml`; golden tests; TS counts; CRLF + trailing pipe | No | **Done** (branch `phase-4-commercial-file`), see below |
| 5 | Register commercial pipeline, bureau profiles (placeholder member IDs, `Commercial-<cycle>…` names), report categories | Seed rows | **Done** (branch `phase-5-commercial-pipeline`), see below |

## Phase 1b: what was built (24-10-2026)

- `model.yaml`: neutral code values (exact inverses of the consumer codes); no `required` / `ucrf` on columns; `keys:` per entity.
- `submissions/types/`: loader for `types/*/codes.yaml` + `requirements.yaml`; `types/consumer/` holds codes, requirements, rules, layout, `file.py` (was `formats/ucrf_consumer`), `pipeline.py` (`PIPELINE`; `pipeline/__init__.py` discovers type folders).
- Reference tables `ref_code_translation`, `ref_code_group` (migration 012); `submissions/reference.py` seeds **insert-missing only** after migrations and provides `active_translations()`.
- Layout key `translate: <code_list>`; `when`, `default`, formatter args and the history asset-class map use canonical values. Translations are frozen per snapshot at validation (`snapshot_code_translation`, migration 013, 1c item 4).
- Mappings per type: `clients/{_base,_template,<t>}/consumer.yaml` with `submission_type: CONSUMER`; lookups give canonical values, checked against the column's code list; required columns from `requirements.yaml`; `mapping_file()` resolves `tenant.mapping_path` as the client folder (an old `…/mapping.yaml` value still works). New tenants copy every `_template/*.yaml`. Compiled SQL under `clients/<t>/generated/consumer/`.
- `canonical/generate.py` prints DDL only (no INSERTs, no `--check`); the "003 matches the model" tests were removed.
- Validation: REQUIRED rules from `requirements.yaml`; ENUM rules check active `ref_code_translation` rows for the snapshot's type.
- Behaviour note: a client code that equals a canonical value (e.g. nbfc_002 `MALE`) passes through as valid without a lookup.
- Docs updated: PIPELINE, ARCHITECTURE (reference data), README, ONBOARDING, RUNBOOK, CLAUDE.md.

## Phase 2: what was built (24-10-2026)

- **Model 1.1** (`canonical_version` bumped; every mapping file says `"1.1"`):
  - `c_party`: business columns (short name, legal constitution, business category, industry type, incorporation
    date, RBI activity classes 1-3, HSN, sales + year, employees, credit rating + agency + dates). A business borrower
    and an individual reported commercially (constitution `INDIVIDUAL`) are both a party.
  - `c_party_identifier`: new `id_type` values `COMPANY_REGISTRATION`, `CIN`, `TIN`, `SERVICE_TAX`, `UDYAM`, `DIN`.
  - `c_party_address`: `city`, `district`, `country`. Business location types are new `address_category` values
    (`REGISTERED_OFFICE`, `BRANCH_OFFICE`, `WAREHOUSE`, `PLANT_FACTORY`, `OTHERS`; `MORTGAGE_PROPERTY` shared).
  - `c_account`: credit facility columns (branch, sanctioned amount + currency, drawing power, maturity / renewal,
    overdue buckets 1-5, status date, restructuring reason, security / guarantee coverage, remark, wilful default
    date, suit reference / amount / date, dispute ID, other lenders, UFCE). Reused where the meaning is the same:
    `days_past_due`, `asset_classification`, `credit_facility_status`, `suit_filed_status`, `current_balance`,
    `amount_overdue`, `high_credit_amount` (revolving), `emi_amount`, `actual_payment_amount`, `written_off_total`,
    `settlement_amount`, `repayment_tenure`, `date_closed`.
  - New entities `relationship` (per borrower) and `guarantor` (per facility) with the same related-party columns
    (identifiers inline: PAN, voter ID, passport, DL, UID, ration card, CIN, DIN, TIN, service tax, CKYC, Udyam);
    `security`; `cheque_dishonour`.
  - **One taxonomy per concept** (decision 3): commercial credit types, asset classes (SMA 0-2, Doubtful 1-3, NPA,
    ARC), account statuses (invoked, devolved, ARC), suit stages, repayment frequencies were added to the existing
    lists. New lists: legal constitution, business category, industry type, rating agency, related type,
    relationship type, restructuring reason, coverage, security type / classification, dishonour reason, currency
    (169, the codes themselves), country (195, generated from the spec names: `INDIA` = 079).
- Migration `014_commercial_model.sql` (generated from the tables: ADD COLUMN + CREATE TABLE).
- `types/commercial/`: `codes.yaml` (21 lists, 615 translations, groups `revolving_credit_type`,
  `relationship_optional_credit_type`, `individual_related_type`, `entity_related_type`) and `requirements.yaml`.
  No `rules.yaml`, `layout.yaml` or `pipeline.py` yet, so commercial is not offered in the UI.
- Consumer `codes.yaml`: SMA 0-2 → 05, Doubtful 1-3 → 03, suit stages → 01, Mudra variants → 39. Commercial-only
  values (credit types, business locations, CIN ...) are deliberately untranslated for consumer.
- Mock LMS: `mock_data/commercial.py` (own seed; consumer data unchanged): business_borrower / address /
  relationship, business_facility, facility_guarantor / security / cheque_bounce in both schemas; 13 positive and
  4 negative scenarios with expected outcomes in `test_*` columns.
- Mappings: `clients/_base/commercial.yaml` (guarantor reuses the relationship columns by YAML merge; `null` now
  removes a merged column), `clients/{nbfc_001,nbfc_002,_template}/commercial.yaml`; compiled SQL under
  `clients/<t>/generated/commercial/`.
- Commercial phones / e-mails are held per address in the mock LMS but per borrower in the model (`party_phone`,
  `party_email`); the AS writer will report them on the borrower's addresses (phase 4).

### Open questions (verify with the CIC / an NBFC)

1. Location type for addresses of individuals reported commercially (`PERMANENT`, `RESIDENCE` ...): untranslated now.
2. Weekly / fortnightly / daily repayment: reported as 08 Others.
3. Cheque dishonoured on a closed account: the spec says report it, its catalogue has only 01 Insufficient Funds.
4. CR 26 Account Status 01 Open / 02 Closed by Payment: derived from `date_closed` when `credit_facility_status` is
   empty (layout, phase 4).
5. CR 35 Wilful Default: 1 when `wilful_default_date` is set, or the consumer-style status says wilful (rules, phase 3).

## Phase 3: what was built (24-10-2026)

- `types/commercial/rules.yaml` (pack `CUCRF-3.10`, prefix `CUCRF.`): 170 rules with the generated REQUIRED / ENUM
  ones; categories Minimum / Borrower & identifiers / Address & contacts / Relationships & guarantors / Credit
  facility; 10 parameters (address length, salutations, ID patterns for PAN, CIN, TIN, CKYC, Udyam, DIN, passport,
  voter ID with commercial defaults).
- Cascade `facility_borrower_minimums` (validation/cascades.py): record = facility × borrower; rejected by
  REJECT_RECORD findings (a borrower-level finding rejects all its facilities) or when the borrower has no valid
  identifier, address or relationship; relationship not needed for credit types in group
  `relationship_optional_credit_type` (9026).
- Annexure D parts A / B as groups in `types/commercial/codes.yaml` (`relationship_for_<constitution>`,
  `relationship_not_for_<related type>`), plus `derivative_credit_type`, `director_relationship_type`: database-master,
  editable. Seeded by the next `migrate` (insert-missing, no DDL).
- Python rules: `borrower_name` (2 alphanumerics for a business, Annexure C part C for an individual), and per related
  entity (relationship, guarantor): name, identifier formats (PAN, voter ID, passport, CIN, DIN, CKYC, Udyam), PIN
  format / state, meaningless address. `checks/identifiers.py` knows CIN, TIN, Udyam, DIN.
- Interpretations (in rule messages): related parties registered outside India need no Indian identifier; written-off
  / settled amounts, suit details, drawing power, wilful default date are WARN (configurable); Annexure D part A is
  checked only for constitutions it lists (not INDIVIDUAL).
- Tests: `tests/test_commercial_rules.py` runs extract + validation on both mock schemas and checks every scenario's
  expected outcome and rule; clean scenarios have no REJECT findings except the spec's expected handling.

## Phase 4: what was built (24-10-2026)

- `submissions/formats/delimited/`: generic engine. Layout = header line, a group per borrower (and split value, here
  the member branch: BS once per borrower per branch) with its child lines (AS, RS), each accepted record's line (CR)
  with its child lines (GS, SS, CD), trailer. Positional fields 2..n with the TUDF field keys (`source`, `translate`,
  `when`, `alternatives`, `max_len` ...) plus `const`, `blank`, `if_present`; each segment declares the spec's field
  count, checked on load. Sources `id.<TYPE>` (borrower's valid identifier), `contacts.mobile|telephone|email`,
  `count.<TAG>` (TS counts). Delimiter, `~` and `^` stripped from every value.
- New formatters: `signed_amount_leading` (CR 11), `dpd_1nnn` (CR 15: 0 -> 1000, 999+ -> 1999), `hundredths` (RS 25).
- `types/commercial/layout.yaml` (all 9 segments, field by field with spec numbers) and `types/commercial/file.py`
  (`build_file(..., created_date)`: HD 4, never before the as-of date).
- Decisions in the layout: CR 26 = special status, else 02 when `date_closed`, else 01; CR 27 = status date, else date
  closed; CR 35 = 1 when `wilful_default_date` or a wilful status, else 0; CR 42 = 01 (normal submission); AS 12 / 14 =
  the borrower's valid mobiles / other phones on every address; AS 16 e-mail only on the registered office address;
  telephone area code is part of the number (AS 13 empty).
- Tests: `tests/test_commercial_golden.py` (field counts, segment order per Table 2, TS counts, trailing `|` + CRLF,
  rejected / dropped data left out, codes) and golden files `tests/golden/commercial_*.txt`.

## Phase 5: what was built (24-10-2026)

- `submissions/pipeline/mapped.py`: `MappedPipeline` (mapping -> snapshot -> rules; file via the type's writer;
  exceptions CSV). Consumer and commercial `pipeline.py` only supply their writer. Registry in `SubmissionType` order.
- `available_types(tenant)`: a type is offered when it has a pipeline **and** the NBFC has
  `clients/<tenant>/<type>.yaml`. Used by `/meta` (request page, Rules & policies type selector), request creation
  and scheduled runs (an NBFC without `commercial.yaml` gets consumer only; none at all = skipped).
- Bureau profiles: `placeholder_profiles` also seeds COMMERCIAL per bureau (member IDs `CIBC000001` ..., no header
  tag, file name `Commercial-{cycle}-{as_of:%d%m%Y}-{member_id}-{created:%H%M%S}.txt`, spec 4.3). New file-name field
  `{cycle}` (AH when a run has no cycle).
- Tests: `tests/test_commercial_pipeline.py` (validate both types in one batch -> two snapshots; generate commercial
  for two bureaus; per-NBFC availability), plus updated API / schedule / seed tests.

## Next steps

1. ~~Commit 1b~~ done, merged into main; migration 012 executed.
2. ~~1c item 4~~ done 24-10-2026: `snapshot_code_translation` (migration 013, **not yet executed**).
3. ~~1c item 6~~ done 24-10-2026 (branch `reference-values-out-of-checks`, no DDL).
4. ~~Phase 2~~ done 24-10-2026 (branch `phase-2-commercial-model`, migration 014).
5. ~~Phase 3~~ done 24-10-2026 (branch `phase-3-commercial-rules`).
6. ~~Phase 4~~ done 24-10-2026 (branch `phase-4-commercial-file`).
7. ~~Phase 5~~ done 24-10-2026 (branch `phase-5-commercial-pipeline`).
8. **Next:** admin page for reference data (1c items 2, 3, 5); answers to the ROADMAP commercial questions; real
   commercial member IDs per bureau (`bureau_profile`).

## Deploy notes (when 1b ships)

- DDL: migrations 012 and 013 **have been executed (24-10-2026)**: frozen, never edit them. 014 (commercial model,
  ADD COLUMN on c_party / c_party_address / c_account + 4 new tables) is new: flag before deploying.
- Development database: re-load `mock_data/mock_lms.sql` to get the commercial mock tables (consumer rows unchanged).
- Existing snapshots hold consumer file codes, not canonical values: re-validate (new request) before generating files from them.
- RDS `tenant.mapping_path` values point at `…/mapping.yaml`: the code reads that as the folder, so no data update is needed (optionally set them to `clients/<t>`).
- The first `migrate` after deploying 012 fills the new reference tables (seed inserts missing rows).
