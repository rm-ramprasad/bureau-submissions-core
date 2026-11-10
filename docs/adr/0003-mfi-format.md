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

## Decisions

1. **Client and data (owner).** MFI is for a **new client**; their data source (LMS view or workbook) is not known
   yet. Everything that does not need their data (steps 1–5 below) goes ahead now; getting their data in (step 6)
   waits for a sample or their system's column list.
2. **Both files (owner).** SHG and JLG + Individual are built together: the hard parts (joined segments, two files)
   are shared; SHG adds the group segment.
3. **New data path only.** MFI is built on ADR 0002's layers: the client's data goes into the customer tables (L2a)
   and the account position (L2b), and the MFI snapshot is extracted from them. There is no MFI source-view path.
4. **File routing by loan category** (§8.17): T04–T07 → SHG file, T01–T03 → JLG + Individual file; T08 per client
   setting (default: JLG + Individual).
5. **Generic engine, extended.** The delimited engine gains three things, used by MFI only: fixed-width header and
   trailer, joined segments (one line per account), and several files per request split by a field. Consumer and
   commercial golden files must stay byte identical.
6. **Validation outcomes map one to one.** The spec's *Record* → `REJECT_RECORD` (record excluded, as today);
   *Field* → `REJECT_FIELD` (field blanked, as today). Rejections show on the findings pages and in the review Excel,
   like the other formats. Enumerations, state codes and PIN ranges are data (code lists / config), not code (§0.5).
7. **Spec issues (§10): defaults, each switchable where marked.** The owner can overrule any line before step 5.

| §10 | Issue | Default |
|---|---|---|
| 1 | State codes: Appendix B contradicts Appendix C for CG/CH, HP/HR, JH/JK | **Appendix C (owner)**: CH Chandigarh, CG Chhattisgarh, HP Himachal, HR Haryana, JK J&K, JH Jharkhand. Switch to B kept. **Confirm with the CIC before go-live** |
| 2 | Disbursed amount 0 allowed "for cancelled" but ACTCRD #83 rejects 0 | 0 allowed only with status S15 (cancelled); otherwise record rejected |
| 3 | GRPCRD #20–22 "Mandatory" vs "for loans disbursed ≥ 01-01-2027" | Always filled; record rejection only for disbursal ≥ 01-01-2027 (date switchable) |
| 4 | GRPCRD #20 rule mentions "Permanent Address" | Minimum length 3 applies to the group address |
| 5 | ACTCRD #89 DPD typed A/N | Always 3 digits, numeric |
| 6 | ACTCRD #91 typed `D` | Numeric DDMMYYYY |
| 7 | ACTCRD #94 typed N but holds references | Printable text |
| 8 | Email, group name, member name typed A/N but contain `@ . -` and spaces | Printable, with each field's own rules |
| 9 | HDR #1 `HDR` or `MFI` | `HDR` (switchable) |
| 10 | Version field 3 characters, document 3.3.1 | `3.3` |
| 11 | "S07, S09 AND S12" | Status in (S07, S09, S12) |
| 12 | ACTCRD #86 SHG rule says "other than enumeration" for an amount | Read as "invalid value" |
| 13 | Email "not more than two consecutive dots" | Reject any `..` (switchable) |
| 14 | S18 in the code list, not in the change log | Included |
| 15 | GRPCRD #17 write-off "for loans disbursed ≥ 01-01-2027" | Date gate switchable |
| 16 | S04 with DPD > 0 | A data-quality warning (not a CIC rejection) |

8. **Amounts in whole rupees, truncated** (§2.3), names packed word by word across Name1/2/3 without truncation
   (§7.1), identical lines removed before writing (§2.7), file names per §1.3 with the file's kind
   (e.g. `MFI-ME-31072026-NB12345678-SHG01`).

## Order of work (one pull request each)

1. This ADR and the spec in `docs/specs/`. No code.
2. **Data model**: canonical fields and code lists for MFI (group, member and account extras; S/K/F/X/P/T/… code
   lists; state codes and PIN ranges as data) and the same fields in the customer tables (one migration).
3. **Engine**: fixed-width header/trailer, joined segments, several files per request; consumer and commercial
   goldens unchanged.
4. **Layouts**: SHG and JLG + Individual, column by column; column counts (136 / 100) and line lengths checked;
   synthetic fixtures for every segment.
5. **Validation**: every rule in §4–§7 and §7.1–§7.7, the §10 switches, a test per rule.
6. **Data source** for the new client (view mapping or upload template), once their data is known.
7. **Switch on**: MFI no longer "coming soon", bureau profiles, then a **CIC test submission**; fixes from the CIC's
   rejection report before the first live file.

## Open points

- The client and their data source (step 6).
- A **CIC test window and an accepted sample file**: the only external proof, since no reference output exists.
- Appendix B vs C confirmation with the CIC (§10 #1).
- Whether the client's loans need the incremental files (DL, W1–W3) or month-end only.
