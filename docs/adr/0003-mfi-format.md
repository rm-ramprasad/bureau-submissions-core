# 3. MFI submission format (UCRF for MFIs incl. SHG, v3.3.1)

Date: 2026-11-10 · Status: proposed · Decisions by the owner on 2026-11-10 marked **(owner)**

Spec: [docs/specs/UCRF_MFI_v3.3.1.md](../specs/UCRF_MFI_v3.3.1.md) (UCRF for MFIs including SHG, v3.3.1, April 2026,
effective 01-07-2026). Section numbers below (§) refer to it. Requirements 10.3 asked for this spec before MFI work starts.

## Context

MFI is the third format after consumer and commercial; `bureau_core/types/mfi/type.yaml` exists as a planned
("coming soon") type. MFI differs from both existing formats:

| | Consumer / commercial | MFI |
|---|---|---|
| Files per run | One per bureau | **Two per bureau and date**: SHG (136 columns) and JLG + Individual (100 columns) — never mixed (§0, §1.2) |
| Line | One segment per line | **One line per account**: GRPCRD (SHG only) + CNSCRD + ADRCRD + ACTCRD joined by `\|`; a member with N loans repeats CNSCRD + ADRCRD on N lines |
| Header / trailer | Pipe-delimited | **Fixed width**: HDR 190, TRL 36 characters, space-padded (§3, §7A) |
| Data | Borrower, IDs, phones, addresses, account | Plus SHG group (group loan, bank account, government programme), centre/kendra, branch, loan officer, key person, 4 relatives, nominee, marital status, education, religion, caste, member role/status, family income/expenses, loan category/cycle/purpose, insurance, MFI account status S04–S18 |
| Checks | Consumer / commercial rule packs | ~150 field rules with record or field rejection (§4–§7), name packing (§7.1), Aadhaar Verhoeff check digit, PIN prefix per state, phone sequences, e-mail rules (§7.3–§7.6) |
| Proof | Golden files from the reference app | **None exists**: fixtures built from the spec, then a CIC test submission |

## Same framework as consumer and commercial (owner)

MFI is built **exactly like the other two formats**: a format is a set of configuration files read by the shared
pipeline, engines, data-quality checks and Admin pages; code is added only for a genuinely new kind of rule or a new
engine capability, and then it is generic (usable by any format). Piece by piece:

| Piece | Consumer / commercial today | MFI |
|---|---|---|
| Type descriptor | `bureau_core/types/<type>/type.yaml` | `types/mfi/type.yaml` (exists, `planned: true` until step 7): engine, labels, default file names |
| Code lists and translations | `codes.yaml`: canonical value → file code, seeded into `ref_code_translation`, editable in the database | `types/mfi/codes.yaml`: canonical → MFI codes (S/K/F/X/P/T/… lists, state codes per Appendix C) |
| Required fields | `requirements.yaml` → generated REQUIRED rules | `types/mfi/requirements.yaml` |
| Validation rules | `rules.yaml` (sql / python / cascade rules, **parameters**), outcomes per client via `validation_policy` | `types/mfi/rules.yaml`: the §4–§7 rules; the §10 switches and date gates as **parameters** (Rules page) |
| File layout | `layout.yaml` read by the generic engine | `types/mfi/layout.yaml` (SHG and JLG + Individual) read by the same delimited engine |
| Upload: workbook layout | `canonical/types/<type>/inputs.yaml` (sheets → staging tables) | `canonical/types/mfi/inputs.yaml`, once the client's template is known (step 6) |
| **Data-quality phase** at upload | `canonical/types/<type>/input_checks.yaml` (block / warn), shown on the request | `canonical/types/mfi/input_checks.yaml`: the same two-stage checking — upload checks, then snapshot validation |
| Source mapping | The client's source views (`vw_<type>_*`), then optionally the customer tables (Admin page, data source) | The same: the client's `vw_mfi_*` views; the data-source switch applies to MFI like the others |
| Findings, review Excel, exceptions | Shared pages and outputs | Shared, unchanged |
| Admin | Bureau profiles per type, rules / parameters / custom rules, cycles, data source, config versions and export | Same pages; MFI appears as one more type |

## Decisions

1. **Client and data (owner).** MFI is for a **new client**; their data source (LMS view or workbook) is not known
   yet. Everything that does not need their data (steps 1–5 below) goes ahead now; the upload layout, its checks and
   the source views (step 6) wait for a sample or their system's column list.
2. **Both files (owner).** SHG and JLG + Individual are built together: the hard parts (joined segments, two files)
   are shared; SHG adds the group segment.
3. **Same data paths as the other formats.** Upload with data-quality checks, the client's source views into the
   snapshot, and the Admin page's data-source switch to the customer tables (ADR 0002) — nothing MFI-only.
4. **File routing by loan category** (§8.17): T04–T07 → SHG file, T01–T03 → JLG + Individual file; T08 by a rule
   parameter (default: JLG + Individual).
5. **Engine: generic capabilities, configured in the layout.** The delimited engine gains fixed-width header and
   trailer, joined segments (one line per account) and several files per request split by a field — each switched
   on from `layout.yaml`, available to any future format. Consumer and commercial golden files stay byte identical.
6. **Validation outcomes map one to one.** The spec's *Record* → `REJECT_RECORD` (record excluded), *Field* →
   `REJECT_FIELD` (field blanked), as for the other formats. Each client can change an outcome on the Rules page
   (`validation_policy`), as today.
7. **Spec issues (§10): defaults, configurable the usual way** — a rule parameter or `codes.yaml` row, so a client
   or a CIC answer changes configuration, not code. The owner can overrule any default before step 5.

| §10 | Issue | Default (where it is configured) |
|---|---|---|
| 1 | State codes: Appendix B contradicts Appendix C for CG/CH, HP/HR, JH/JK | **Appendix C (owner)**: CH Chandigarh, CG Chhattisgarh, HP Himachal, HR Haryana, JK J&K, JH Jharkhand (`codes.yaml` translations, editable). **Confirm with the CIC before go-live** |
| 2 | Disbursed amount 0 allowed "for cancelled" but ACTCRD #83 rejects 0 | 0 allowed only with status S15 (cancelled); otherwise record rejected (rule) |
| 3 | GRPCRD #20–22 "Mandatory" vs "for loans disbursed ≥ 01-01-2027" | Always filled; record rejection only for disbursal ≥ 01-01-2027 (date parameter) |
| 4 | GRPCRD #20 rule mentions "Permanent Address" | Minimum length 3 applies to the group address (parameter, as commercial's `min_address_length`) |
| 5 | ACTCRD #89 DPD typed A/N | Always 3 digits, numeric (layout) |
| 6 | ACTCRD #91 typed `D` | Numeric DDMMYYYY (layout) |
| 7 | ACTCRD #94 typed N but holds references | Printable text (layout) |
| 8 | Email, group name, member name typed A/N but contain `@ . -` and spaces | Printable, with each field's own rules |
| 9 | HDR #1 `HDR` or `MFI` | `HDR` (layout constant) |
| 10 | Version field 3 characters, document 3.3.1 | `3.3` (layout constant) |
| 11 | "S07, S09 AND S12" | Status in (S07, S09, S12) |
| 12 | ACTCRD #86 SHG rule says "other than enumeration" for an amount | Read as "invalid value" |
| 13 | Email "not more than two consecutive dots" | Reject any `..` (parameter) |
| 14 | S18 in the code list, not in the change log | Included (`codes.yaml`) |
| 15 | GRPCRD #17 write-off "for loans disbursed ≥ 01-01-2027" | Date parameter |
| 16 | S04 with DPD > 0 | A data-quality warning, not a CIC rejection (rule outcome, changeable per client) |

8. **Amounts in whole rupees, truncated** (§2.3), names packed word by word across Name1/2/3 without truncation
   (§7.1), identical lines removed before writing (§2.7), file names per §1.3 with the file's kind
   (e.g. `MFI-ME-31072026-NB12345678-SHG01`) — layout and engine settings.

## Order of work (one pull request each)

1. This ADR and the spec in `docs/specs/`. No code.
2. **Data model**: canonical fields and code lists for MFI in `bureau_core/model.yaml` (group, member and account
   extras), `types/mfi/codes.yaml` and `requirements.yaml`; the same fields in the snapshot (`c_*`) and customer
   tables (one migration).
3. **Engine**: fixed-width header/trailer, joined segments, several files per request — generic, switched on from a
   layout; consumer and commercial goldens unchanged.
4. **Layout**: `types/mfi/layout.yaml` for SHG and JLG + Individual, column by column; column counts (136 / 100) and
   line lengths checked; synthetic fixtures for every segment.
5. **Validation**: `types/mfi/rules.yaml` — every rule in §4–§7 and §7.1–§7.7, parameters for the §10 switches, a
   test per rule; any new rule kind added generically to the shared engine.
6. **Data in** for the new client: `canonical/types/mfi/inputs.yaml` (upload layout), `input_checks.yaml` (the
   data-quality phase) and the `vw_mfi_*` source views — once their data is known.
   **Owner 2026-11-10: done first as a PROVISIONAL workbook** (Loans + Groups sheets, MFI file codes in coded
   columns, loan figures in the workbook as there is no loan system view yet; sample from `python -m sources.samples
   MFI`), with `planned: false` brought forward from step 7 so MFI is on the New Request page. Adjusted to the
   client's real data when it arrives (an edit of inputs.yaml / mfi.sql plus a migration, as for any layout change).
7. **Switch on**: bureau profiles (placeholders are added on deploy; real CIC member codes on the Admin page), then a
   **CIC test submission**; fixes from the CIC's rejection report before the first live file.

## Open points

- The client and their data source (step 6).
- A **CIC test window and an accepted sample file**: the only external proof, since no reference output exists.
- Appendix B vs C confirmation with the CIC (§10 #1).
- Whether the client's loans need the incremental files (DL, W1–W3) or month-end only.
