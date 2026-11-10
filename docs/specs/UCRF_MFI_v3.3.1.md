# UCRF – Uniform Credit Reporting Format for MFIs (incl. SHG) — v3.3.1

> **Purpose of this file:** machine-oriented specification for Claude Code to generate, validate and test CIC (CRIF High Mark / Equifax / Experian / TransUnion CIBIL) microfinance submission extracts.
> **Source:** UCRF for MFIs (Including SHG), Version 3.3.1, April 2026, **effective 01-Jul-2026**.
> **Conventions in this file:** field `#` = column position used by the CIC spec. Rules are written as implementable predicates. Anything marked **⚠ SPEC ISSUE** is an inconsistency in the source document — do not silently "fix" it; implement the configurable behaviour described and surface it to the user.

---

## 0. Instructions for Claude Code

1. Treat this file as the single source of truth for layout, lengths, enumerations and validation rules.
2. Generate **two separate files** per reporting date: one **SHG** file (136 columns per data line) and one **JLG + Individual** file (100 columns per data line). Never mix them.
3. Implement validation as a pre-submission step that classifies each issue as `REJECT_RECORD` or `REJECT_FIELD` (mirroring CIC behaviour) and produces a rejection report. Records with `REJECT_RECORD` issues must be excluded from (or flagged in) the output per the caller's config; `REJECT_FIELD` issues should blank the field (configurable) and be logged.
4. All rule predicates below are evaluated **after trimming leading/trailing spaces** (length is computed on trimmed values).
5. Keep enumerations, state codes and PIN prefixes in data/config files (e.g. `enums.json`), not hard-coded across the codebase.
6. Write unit tests for every rule in §7 and every **⚠ SPEC ISSUE** in §10.

---

## 1. File structure

### 1.1 Segments

| # | Segment | Tag | Option | Occurrence | Separator |
|---|---|---|---|---|---|
| 1 | Header | `HDR` | Mandatory | Once per file, first line | **Fixed length** (no pipes) |
| 2 | Group | `GRPCRD` | Conditionally Mandatory (SHG only) | Once per group per line | Pipe `\|` |
| 3 | Member/Consumer | `CNSCRD` | Mandatory | Per customer | Pipe `\|` |
| 4 | Address | `ADRCRD` | Mandatory | One or more per consumer | Pipe `\|` |
| 5 | Account | `ACTCRD` | Mandatory | One or more per account | Pipe `\|` |
| 6 | Trailer | `TRL` | Mandatory | Once per file, last line | **Fixed length** |

Missing HDR or TRL ⇒ **entire file rejected**.

### 1.2 Line layout

**SHG file (136 columns per data line):**
```
HDR (fixed length)
GRPCRD(1..36) | CNSCRD(1..55) | ADRCRD(56..63) | ACTCRD(64..100)
GRPCRD (Group 1) | CNSCRD (Member 1) | ADRCRD | ACTCRD (Account 1)
GRPCRD (Group 1) | CNSCRD (Member 2) | ADRCRD | ACTCRD (Account 1)
GRPCRD (Group 2) | CNSCRD (Member 1) | ADRCRD | ACTCRD (Account 1)
TRL (fixed length)
```

**JLG + Individual file (100 columns per data line):**
```
HDR (fixed length)
CNSCRD (Member 1) | ADRCRD | ACTCRD (Account 1)
CNSCRD (Member 1) | ADRCRD | ACTCRD (Account 2)
CNSCRD (Member 2) | ADRCRD | ACTCRD (Account 1)
TRL (fixed length)
```

- One data line = one account. A member with N accounts repeats CNSCRD + ADRCRD on N lines.
- Each segment starts with its segment identifier value as the first column (`GRPCRD`, `CNSCRD`, `ADRCRD`, `ACTCRD`) — these count within the column numbering.
- Column count is fixed: blank fields are emitted as empty values between pipes (`||`). Verify exact count = 136 (SHG) / 100 (JLG) per line → for N columns there are N−1 pipes.
- Field numbering: GRPCRD uses its own 1–36; CNSCRD/ADRCRD/ACTCRD use a continuous 1–100 sequence.

### 1.3 File naming (recommended by CICs; must be unique)

```
<MFI>-<FREQ>-<DDMMYYYY reported date>-<CIC member code>-<internal ref>
e.g. MFI-ME-31072026-NB12345678-SHG01
```

| Code | Meaning |
|---|---|
| DL | Daily incremental |
| W1 | 1st weekly incremental, as on 9th |
| W2 | 2nd weekly incremental, as on 16th |
| W3 | 3rd weekly incremental, as on 23rd |
| ME | Month end |
| DC | Data correction / dispute |
| AH | Any other ad hoc |
| RR | Data rejection resubmission |

---

## 2. Global rules

### 2.1 Character types

| Type | Allowed |
|---|---|
| `A` | Letters only |
| `N` | Digits only (signed only where explicitly allowed) |
| `A/N` / `AN` | Letters and digits |
| `P` | Printable ASCII 32–126 (incl. space, `-`, `~`) |

> Since the field separator is `|` (ASCII 124, which is inside the P range), **strip or replace `|` in all field values** before writing. Also strip CR/LF/tab and non-ASCII characters.

### 2.2 Option semantics

| Option | Behaviour when missing/invalid |
|---|---|
| Mandatory | Segment and/or whole record rejected |
| Optional | Field-level rejection only |
| Conditionally Mandatory | Required when the stated condition holds; outcome per rule |

### 2.3 Amounts
- Report in whole rupees. If a decimal is sent, CIC truncates at the decimal point → **generator should emit integers (truncate, do not round)** to match CIC behaviour.
- Negative values allowed **only** in: GRPCRD #6 Outstanding Balance/Current Balance, ACTCRD #87 Current Balance. All other amount fields must be ≥ 0.
- Max length 9 ⇒ max value 999,999,999 (a leading `-` counts toward length).

### 2.4 Dates
- Format `DDMMYYYY`, numeric, valid calendar date.
- No date may be in the future.
- No date may be earlier than `01011900`.

### 2.5 Field length
- Length measured **after trimming** leading/trailing spaces.
- Values longer than the max are **not truncated by CIC** → rejected (field or record per field option). Generator must validate, not silently truncate (except where this spec explicitly says to move overflow, e.g. names §5 #6–8).

### 2.6 Enumerations
Values must match the enumeration code exactly (e.g. `F01`, not `f01` or `Weekly`), else rejected per field type.

### 2.7 Other processing rules
- An account is reported **only after disbursement**.
- Rejection of a GRPCRD segment rejects the respective records across all segments.
- SHG: if CNSCRD #55 Member Status is blank or anything other than `W02`, ACTCRD is required. If `W02`, ACTCRD is not required (skipped if sent).
- Address: if either Permanent (#57–59) or Current (#60–62) address set is fully valid, the other incorrect set gets field rejection only.
- Duplicates (identical consumer + tradeline strings) are isolated by CIC; metrics are computed on unique records. **Generator should de-duplicate before writing.**
- Suppressed accounts are accepted and updated but displayed only after unsuppression.
- Latest **Reported Date** prevails, not latest upload date (a late resubmission of an older reported date does not overwrite newer data).

---

## 3. Header segment (HDR) — fixed length, no separators

Blank fields must be space-padded to full length. Text fields: left-justify, pad right with spaces.

| # | Field | Option | Type | Len | Start | End | Value / Rule |
|---|---|---|---|---|---|---|---|
| 1 | Segment Identifier | M | A | 3 | 1 | 3 | `HDR` or `MFI` |
| 2 | Name of Submission File | M | A/N | 5 | 4 | 8 | `HMMFI`; may be 5 spaces |
| 3 | Layout Version Number | M | P | 3 | 9 | 11 | `3.3` (spec allows `3.2`/`3.3`) |
| 4 | Submitting Member Id | M | A/N | 10 | 12 | 21 | CIC-assigned member ID |
| 5 | Submitting Member Name | M | A/N | 30 | 22 | 51 | May be 30 spaces |
| 6 | Submitting Branch Id | M | P | 30 | 52 | 81 | May be 30 spaces |
| 7 | Reported Date | M | N | 8 | 82 | 89 | "As of" date, DDMMYYYY (not extraction date) |
| 8 | File Creation / Extraction Date | M | N | 8 | 90 | 97 | DDMMYYYY; must be ≥ Reported Date |
| 9 | Organization Data Structure Indicator | O | A | 3 | 98 | 100 | `SHG` or `JLG`; may be spaces |
| 10 | Old Member Id | O | P | 30 | 101 | 130 | Mandatory for migrated-cases file; else spaces |
| 11 | Reserved for Future Use | O | P | 30 | 131 | 160 | Spaces |
| 12 | File Frequency | O | P | 10 | 161 | 170 | `DL`,`W1`,`W2`,`W3`,`ME`,`DC`,`AH`,`RR` |
| 13 | Reserved for Future Use | – | A/N | 20 | 171 | 190 | Spaces |

**HDR total length = 190.**

Example (Reported 31-Jul-2026, extracted 02-Aug-2026): header date semantics — Reported Date = `31072026`, File Creation Date = `02082026`.

> Note on #12: CICs report compliance info to RBI's Daksh portal using this flag on a best-effort basis; populating it is recommended.

---

## 4. Group segment (GRPCRD) — SHG file only, 36 columns

| # | Field | Type | Len | Option | Description | Rule → Outcome |
|---|---|---|---|---|---|---|
| 1 | Segment Identifier | A/N | 6 | M | `GRPCRD` | wrong value → **Record** |
| 2 | Group Identifier | P | 50 | M | Unique group ID | blank or only special chars → **Record** |
| 3 | Group Name | A/N | 200 | M | SHG name | needs ≥1 token with ≥2 alphabets → **Record** |
| 4 | Group Loan Account Number | P | 35 | M | Unique group loan a/c no | blank or only special chars → **Record** |
| 5 | Disbursed Amount (Rs) | N | 9 | M | Total disbursed; 0 allowed for cancelled | must be ≥ 0 → **Record** |
| 6 | Outstanding Balance / Current Balance (Rs) | N | 9 | M | Principal o/s; may be 0 for C02; negative allowed | > 0 AND #8 not blank → **Record** |
| 7 | Date Opened/Disbursed | N | 8 | M | DDMMYYYY | > #8 → **Record**; > HDR Reported Date → **Record** |
| 8 | Date Closed (if closed) | N | 8 | CM | DDMMYYYY | > HDR Reported Date → **Record** |
| 9 | Date of Last Payment | N | 8 | O | DDMMYYYY | < #7 → **Field**; > HDR Reported Date → **Field** |
| 10 | Number of Installments | N | 3 | CM | Original count; mandatory when #16 = C01 | C01 & (blank/0/neg) → **Record**; C02 & neg → **Field** |
| 11 | Repayment Frequency | A/N | 3 | CM | Enum §8.1 | C01 & (blank/not enum) → **Record**; C02 & not enum → **Field** |
| 12 | Instalment Amount | N | 9 | CM | Mandatory when C01 | C01 & (blank/0/neg) → **Record**; C02 & neg → **Field** |
| 13 | Amount Overdue (Rs) | N | 9 | CM | May be blank if #14 = 0 | (blank/0) & DPD>0 → **Record**; >0 & DPD=0 → **Record**; #8 set & >0 → **Record** |
| 14 | DPD (Days Past Due) | N | 3 | M | `000`–`999` (cap at 999) | invalid/blank → **Record**; #8 set & DPD>0 (except written-off) → **Record** |
| 15 | Loan Cycle ID | N | 3 | O | Sequence of group loans | invalid → **Field** |
| 16 | Loan Type | A/N | 3 | M | `C01` Term Loan, `C02` Cash Credit | not enum → **Record** |
| 17 | Write Off Amount (Rs) | N | 9 | CM | Required if #18 set; applicable for loans disbursed ≥ 01-Jan-2027 | #18 set & (0/blank) → **Record** |
| 18 | Date Write-Off | N | 8 | CM | Required if #17 > 0 | #17 > 0 & blank → **Record** |
| 19 | Write-off Reason | A/N | 3 | O | Enum §8.4 | not enum → **Field** |
| 20 | Groups' Communication Address | P | 200 | M* | As per address proof | len < 3 → **Record**; only special chars → **Record** (special chars with alphanumerics OK) |
| 21 | State Code (Group address) | A | 2 | M* | Appendix B (§9.1) | invalid → **Record** |
| 22 | PIN Code (Group address) | P | 7 | M* | 6-digit PIN, see §7.4 | invalid → **Record**; prefix ≠ state (§9.2) → **Record** |
| 23 | Telephone Number 1 Type Indicator | A/N | 3 | O | Enum §8.6; default `P08` if #24 present and this blank | not enum / blank → **Field** |
| 24 | Group Telephone Number 1 | P | 15 | CM | See §7.5 | disbursed ≥ 01-Jan-2027: invalid/blank → **Record**; disbursed < 01-Jan-2027: invalid → **Field** |
| 25 | Group's Bank Account – Bank Name | P | 50 | O | | → **Field** |
| 26 | Group's Bank Account – Branch Name | P | 50 | O | IFSC preferred | → **Field** |
| 27 | Group's Bank Account – Account Number | P | 35 | O | | → **Field** |
| 28 | Group's Bank Account – Account Balance | N | 9 | O | Rupees | → **Field** |
| 29 | First Created Date | N | 8 | O | Group formation date | → **Field** |
| 30 | First Linkage Date | N | 8 | O | First credit linkage | → **Field** |
| 31 | Group Identifier (Govt) | P | 50 | O | Report if #32 reported | → **Field** |
| 32 | Linked to a Govt Program | A/N | 3 | O | Enum §8.9; default `U99` if #31 set and this blank | → **Field** |
| 33 | Reserved for Future Use | A/N | 3 | O | blank | → **Field** |
| 34 | Reserved for Future Use | A/N | 100 | O | blank | → **Field** |
| 35 | Reserved for Future Use | A/N | 100 | O | blank | → **Field** |
| 36 | Reserved for Future Use | A/N | 100 | O | blank | → **Field** |

\* #20–22 are labelled Mandatory, but the description says "mandatory for all loans disbursed on or after 1st Jan 2027" — see §10.

---

## 5. Member / Consumer segment (CNSCRD) — columns 1–55

| # | Field | Type | Len | Option | Description | Rule → Outcome |
|---|---|---|---|---|---|---|
| 1 | Segment Identifier | A/N | 6 | M | `CNSCRD` | → **Record** |
| 2 | Member Identifier | P | 35 | M | Unique customer ID | blank / only special chars → **Record** |
| 3 | Branch Identifier | P | 30 | O | Branch where enrolled | → **Field** |
| 4 | Kendra/Centre Identifier | P | 30 | O | Centre where enrolled | → **Field** |
| 5 | Group Identifier | P | 50 | O | `GroupID` or `GroupID^GroupName` (validated up to `^`) | → **Field** |
| 6 | Member Name 1 | P | 100 | M | See §7.1 | → **Record** |
| 7 | Member Name 2 | P | 50 | M (group) | See §7.1 | → **Record** |
| 8 | Member Name 3 | P | 50 | M (group) | See §7.1 | → **Record** |
| 9 | Old Member Identifier | P | 35 | O | Report when #2 changed (first submission with new ID) | same old ID against multiple #2 → **Record** |
| 10 | Member Birth Date | N | 8 | M | DDMMYYYY | blank or age < 16 at HDR Reported Date → **Record**; > HDR Reported Date → **Record**; > #78 Disbursed → **Record**; > #70 Date of Account Info → **Record** |
| 11 | Member Age | N | 3 | O | Age at membership (if DOB not captured) | < 16 → **Field** |
| 12 | Member's Age As On Date | N | 8 | O | Date age recorded / KYC doc date | → **Field** |
| 13 | Member Gender Type | A | 1 | M | `F`, `M`, `T` | → **Record** |
| 14 | Marital Status Type | A/N | 3 | O | Enum §8.10 | → **Field** |
| 15 | Key Person's Name | P | 100 | O | Key person / relationship / nominee — at least one set should be filled | blank → **Field**; name set but #16 blank/invalid → **Field** |
| 16 | Key Person's Relationship | A/N | 3 | O | Enum §8.7 | blank/invalid → **Field**; set but #15 blank → **Field** |
| 17 | Member Relationship Name 1 | P | 100 | O | Relative name | blank → **Field**; set but #18 blank/invalid → **Field** |
| 18 | Member Relationship Type 1 | A/N | 3 | O | Enum §8.7 | blank/invalid → **Field**; set but #17 blank → **Field** |
| 19 | Member Relationship Name 2 | P | 100 | O | | as #17 vs #20 |
| 20 | Member Relationship Type 2 | A/N | 3 | O | Enum §8.7 | as #18 vs #19 |
| 21 | Member Relationship Name 3 | P | 100 | O | | as #17 vs #22 |
| 22 | Member Relationship Type 3 | A/N | 3 | O | Enum §8.7 | as #18 vs #21 |
| 23 | Member Relationship Name 4 | P | 100 | O | | as #17 vs #24 |
| 24 | Member Relationship Type 4 | A/N | 3 | O | Enum §8.7 | as #18 vs #23 |
| 25 | Nominee Name | P | 100 | O | Insurance nominee | blank → **Field**; set but #26 blank/invalid → **Field** |
| 26 | Nominee Relationship | A/N | 3 | O | Enum §8.7 | blank/invalid → **Field**; set but #25 blank → **Field** |
| 27 | Reserved for Future Use | N | 3 | O | blank (was Nominee Age) | → **Field** |
| 28 | Voter's ID | A/N | 20 | CM | §7.3 | ID/phone group rule → **Record** |
| 29 | UID (Aadhaar) | A/N | 12 | CM | §7.3 | ID/phone group rule → **Record** |
| 30 | PAN | A/N | 15 | CM | §7.3 | ID/phone group rule → **Record** |
| 31 | Ration Card | A/N | 20 | CM | §7.3 | ID/phone group rule → **Record** |
| 32 | G-RAM-G | P | 35 | CM | §7.3 | no valid ID → **Field** |
| 33 | CKYC | P | 15 | CM | §7.3 | ID/phone group rule |
| 34 | Driver's License Number | P | 30 | CM | §7.3 | ID/phone group rule |
| 35 | Passport Number | P | 10 | CM | §7.3 | ID/phone group rule |
| 36 | Member Other ID 3 Type Description | A/N | 20 | O | | → **Field** |
| 37 | Member Other ID 3 | A/N | 30 | O | Special chars stripped by CIC | → **Field** |
| 38 | Telephone Number 1 Type Indicator | A/N | 3 | O | Enum §8.6; default `P08` if #39 present | not enum → **Field** |
| 39 | Member Telephone Number 1 | A/N | 15 | CM | §7.5 | no valid ID AND no valid phone → **Record**; invalid → **Field** |
| 40 | Telephone Number 2 Type Indicator | A/N | 3 | O | Enum §8.6; default `P08` if #41 present | not enum → **Field** |
| 41 | Member Telephone Number 2 | A/N | 15 | CM | §7.5 | no valid ID AND no valid phone → **Record**; invalid → **Field** |
| 42 | Member's Educational Qualification | A/N | 3 | O | Enum §8.11 | → **Field** |
| 43 | Reserved for Future Use | A/N | 3 | O | blank | → **Field** |
| 44 | Reserved for Future Use | N | 2 | O | blank | → **Field** |
| 45 | Bank Account – Bank Name | P | 50 | O | | → **Field** |
| 46 | Bank Account – Branch Name | P | 50 | O | IFSC preferred | → **Field** |
| 47 | Bank Account – Account Number | P | 35 | O | | → **Field** |
| 48 | Occupation | A/N | 3 | O | Enum §8.12 | → **Field** |
| 49 | Total Monthly Family Income | N | 9 | CM | Required for disbursals ≥ 01-Apr-2022; when present for earlier | disbursed ≥ 01-Apr-2022 & (blank/0) → **Record** |
| 50 | Monthly Family Expenses | N | 9 | O | Rupees, no decimal | → **Field** |
| 51 | Member's Religion | A/N | 3 | O | Enum §8.13 | → **Field** |
| 52 | Member's Caste or Social Strata | A/N | 3 | O | Enum §8.14 | → **Field** |
| 53 | Member's Role | A/N | 3 | O | Enum §8.15 | → **Field** |
| 54 | Reserved for Future Use | A/N | 1 | O | blank | → **Field** |
| 55 | Member's Status | A/N | 3 | O | Enum §8.16 — validated **only for SHG data** | → **Field** |

---

## 6. Address segment (ADRCRD) — columns 56–63

At least one of {Permanent (#57,#58,#59), Current (#60,#61,#62)} must be fully valid, else **Record** reject. If one set is valid, errors in the other set → **Field** reject.

| # | Field | Type | Len | Option | Description | Rule → Outcome |
|---|---|---|---|---|---|---|
| 56 | Segment Identifier | A/N | 6 | M | `ADRCRD` | → **Record** |
| 57 | Member's Permanent Address | P | 200 | CM | | len < 3 → **Record***; only special chars → **Record*** |
| 58 | State Code (Permanent) | A | 2 | CM | §9.1 | invalid → **Record*** |
| 59 | PIN Code (Permanent) | P | 7 | CM | §7.4 | invalid → **Record***; prefix ≠ state → **Record*** |
| 60 | Member's Current Address | P | 200 | CM | If only one address, send same as current | as #57 |
| 61 | State Code (Current) | A | 2 | CM | §9.1 | as #58 |
| 62 | PIN Code (Current) | P | 7 | CM | §7.4 | as #59 |
| 63 | Email ID | A/N (treat as P) | 70 | O | §7.6 | invalid → **Field** |

\* Record-level only when the other address set is also invalid (§2.7).
Note from spec: capture address exactly from one of the KYC documents.

---

## 7. Account segment (ACTCRD) — columns 64–100

| # | Field | Type | Len | Option | Description | Rule → Outcome |
|---|---|---|---|---|---|---|
| 64 | Segment Identifier | A/N | 6 | M | `ACTCRD` | → **Record** |
| 65 | Unique Account Reference Number | P | 35 | M | Stable across submissions; original a/c no if branch split/merge | blank / only special → **Record** |
| 66 | Account Number | P | 35 | M | Current a/c no (= #65 unless changed) | blank / only special → **Record** |
| 67 | Branch Identifier | P | 30 | O | Current branch | → **Field** |
| 68 | Kendra/Centre Identifier | P | 30 | O | Current centre | → **Field** |
| 69 | Loan Officer Originating the Loan | A/N | 30 | O | Employee code | → **Field** |
| 70 | Date of Account Information | N | 8 | M | As-of date of the data (not extraction) | blank → **Record**; > HDR Reported Date → **Record** |
| 71 | Loan Category | A/N | 3 | M | Enum §8.17 | not enum → **Record** |
| 72 | Group Identifier | P | 50 | CM | Required if #71 ≠ `T03`. `GroupID` or `GroupID^GroupName` (validate up to `^`) | #71≠T03 & (blank or len>50) → **Record**; #71=T03 & len>50 → **Field** |
| 73 | Loan Cycle-id | N | 3 | O | Borrower loan sequence | → **Field** |
| 74 | Loan Purpose | P | 20 | O | | → **Field** |
| 75 | Account Status | A/N | 3 | M | Enum §8.18 | blank/not enum → **Record**; status ∈ {S07,S09,S12} & #79 blank → **Record**; status ∈ {S04,S05,S08} & #79 set → **Record** |
| 76 | Reserved for Future Use | N | 8 | O | blank (was Application Date) | → **Field** |
| 77 | Sanctioned Date | N | 8 | O | DDMMYYYY | → **Field** |
| 78 | Date Opened/Disbursed | N | 8 | M | DDMMYYYY | > HDR Reported Date → **Record**; > #79 → **Record**; > #70 → **Record** |
| 79 | Date Closed (if closed) | N | 8 | CM | Required for S07/S09/S12. Settled: settlement date. Written-off a/c no longer reported: write-off date (else shown as hanging). Others: full-repayment date | status ∈ {S07,S09,S12} & blank → **Record**; > HDR Reported Date → **Record**; > #70 → **Record** |
| 80 | Date of Last Payment | N | 8 | O | | > HDR Reported Date → **Field**; > #70 → **Field**; < #78 → **Field**; < CNSCRD #10 → **Field** |
| 81 | Applied For Amount | N | 9 | O | | → **Field** |
| 82 | Loan Amount Sanctioned | N | 9 | O | May equal #83 | → **Field** |
| 83 | Total Amount Disbursed (Rs) | N | 9 | M | Integer part; 0 allowed for cancelled | zero/negative → **Record** (⚠ conflicts with "0 for cancelled", §10) |
| 84 | Number of Installments | N | 3 | CM | Original count; required for disbursals ≥ 01-Apr-2022; for SHG mandatory if #93 = C01 | JLG: blank/0/neg → **Record**; SHG: C01 & blank/0/neg → **Record**; SHG: C02 & neg → **Field** |
| 85 | Repayment Frequency | A/N | 3 | CM | Enum §8.1 | JLG: blank/not enum → **Record**; SHG: C01 & blank/not enum → **Record**; SHG: C02 & not enum → **Field** |
| 86 | Minimum Amount Due / Instalment Amount | N | 9 | CM | Single instalment amount; required for disbursals ≥ 01-Apr-2022 | JLG: disb ≥ 01-Apr-22 & blank/0/neg → **Record**; disb < 01-Apr-22 & blank/invalid → **Field**. SHG: C01 & disb ≥ 01-Apr-22 & blank/0/neg → **Record**; disb < 01-Apr-22 & blank/invalid → **Field**; C02 & neg → **Field** |
| 87 | Current Balance (Rs) | N | 9 | M | Principal outstanding only; negative allowed | > 0 & status ∈ {S07,S09,S12} → **Record** |
| 88 | Amount Overdue (Rs) | N | 9 | CM | May be blank if #89 = 0 | invalid → **Record**; (blank/0) & DPD>0 → **Record**; >0 & DPD=0 → **Record**; status S05 & (DPD=0 or overdue=0) → **Record** |
| 89 | DPD (Days Past Due) | A/N (emit N) | 3 | M | `000`–`999`, cap at 999 | invalid/blank → **Record**; status S05 & (DPD=0 or overdue=0) → **Record** |
| 90 | Write Off Amount (Rs) | N | 9 | CM | Required if status S06 or #91 set | (blank/0) & status ∈ {S06,S10,S11} → **Record**; #91 set & (blank/0) → **Record**; >0 & status ∈ {S04,S05,S07,S08,S09} → **Record** |
| 91 | Date Write-Off | N (spec says D) | 8 | CM | Required if status S06 or #90 > 0 | #90>0 & blank → **Record**; set & status ∈ {S04,S05,S07,S08,S09} → **Record** |
| 92 | Write-off Reason | A/N | 3 | O | Enum §8.4; expected when S06 | not enum → **Field**; set & status ∈ {S04,S05,S07,S08,S09} → **Field** |
| 93 | Loan Type | A/N | 3 | O (M for SHG) | `C01` / `C02` | not enum → **Field** |
| 94 | Old Unique Account Reference Number | N (treat as P) | 35 | O | Report when #65 changes (first submission with new value) | same old value against multiple #65 → **Record** |
| 95 | Insurance Indicator | A/N | 1 | O | `Y` / `N` | → **Field** |
| 96 | Type of Insurance | A/N | 3 | O | Enum §8.19 | → **Field** |
| 97 | Sum Assured / Coverage | N | 9 | O | | → **Field** |
| 98 | Old Account Number | P | 35 | O | Report when #66 changes | same old value against multiple #66 → **Record** |
| 99 | Settlement Amount | N | 9 | O | For settled accounts | status ∈ {S10,S11} & (0/blank) → **Field** |
| 100 | Dummy (Reserved for Future Use) | A/N | 30 | O | blank | → **Field** |

---

## 7A. Trailer segment (TRL) — fixed length

| # | Field | Option | Type | Len | Start | End | Value |
|---|---|---|---|---|---|---|---|
| 1 | Segment Identifier | M | A/N | 3 | 1 | 3 | `TRL` |
| 2 | Layout Version Number | O | P | 3 | 4 | 6 | `3.3` |
| 3 | Submitting Member ID | O | A/N | 10 | 7 | 16 | Same as HDR #4 |
| 4 | Reserved for Future Use | O | A/N | 20 | 17 | 36 | Spaces |

**TRL total length = 36.** (v3.3 removed "Name of Submission File" from the trailer.)

---

## 7B. Detailed validation rules

### 7.1 Member names (CNSCRD #6–#8)
- Combined Name1+Name2+Name3 must contain at least one token with ≥ 2 alphabetic characters (a single initial is not enough).
- Do not split a word across fields. If a word doesn't fit in the current field, move the **whole word** to the next field; never truncate. (Implement a word-wrap packer: Name1 ≤100, Name2 ≤50, Name3 ≤50.)
- **Numbers prohibited** anywhere in names → **Record**.
- Only numbers / special chars → **Record**.
- Total < 2 characters → **Record**.
- Relationship descriptors allowed only as `S/O`, `W/O`, `H/O`, `D/O` (also with `\`: `S\O`, etc.), and only if a name token precedes them. Names starting with a descriptor, or containing only a relation (e.g. `S/O Heena`) → **Record**. Other formats (`C/O`, etc.) not permitted.
- `/` and `\` allowed **only** inside those descriptors; anywhere else → **Record**.
- Salutations (`Mr`, `Mrs`, `Ms`, with/without `.`) are not counted as name tokens. Salutation-only → **Record**. Salutation after name (e.g. `Rakesh Mr.`) is treated as part of the name and accepted.
- Single-token name accepted only if a valid ID **or** valid mobile number is present, else → **Record**.
- Allowed / disallowed special characters: §9.4 (Appendix A).

### 7.2 Date checks summary
- All dates: valid calendar date, not future, ≥ 01-01-1900.
- HDR #8 ≥ HDR #7.
- ACTCRD #70 ≤ HDR #7.
- #78 ≤ HDR #7, ≤ #70, ≤ #79 (if set).
- #79 ≤ HDR #7, ≤ #70.
- DOB: age ≥ 16 at HDR #7; DOB ≤ #78, ≤ #70, ≤ HDR #7.

### 7.3 Identity documents (strip special characters first, then validate)
Record is rejected if **no valid ID** (Voter / UID / PAN / DL / CKYC / Passport / G-RAM-G) **and no valid telephone** (#39 or #41).

| ID | Rule |
|---|---|
| Voter ID (#28) | Length 8–16; first 2 or 3 chars alphabetic; remainder alphanumeric |
| UID (#29) | Exactly 12 digits; 12th digit is a check digit over first 11 (Aadhaar uses **Verhoeff**) |
| PAN (#30) | Exactly 10; chars 1–5 alpha; char 4 ∈ {`P`,`H`}; chars 6–9 numeric; char 10 alpha. Regex: `^[A-Z]{3}[PH][A-Z][0-9]{4}[A-Z]$` |
| Ration Card (#31) | Special chars stripped; no further rule |
| G-RAM-G (#32) | `SS-DD-BBB-GGG-VVV/HHHSurname` → State (2 alpha), District (2 num), Block (3 num), Gram Panchayat (3 num), Village (3 num), Household (3 num + surname). e.g. `MH-12-123-123-123/123Patil` |
| CKYC (#33) | Up to 15, alphanumeric (CKYC numbers are 14 digits in practice) |
| Driving Licence (#34) | Special chars stripped; no further rule |
| Passport (#35) | Length 7–10; char 1 alpha; char 2 alpha or digit; rest digits. Regex: `^[A-Z][A-Z0-9][0-9]{5,8}$` |

Note: PAN rule c) restricts to individuals (`P`) and HUF (`H`).

### 7.4 PIN code (GRPCRD #22, ADRCRD #59, #62)
1. Strip all non-digit characters.
2. Must be exactly 6 digits.
3. Reject if last 3 digits are `000`.
4. First two digits must fall in the state's range (§9.2).
- Valid: `604002`, `604-002`. Invalid: `604`, `6040020000`, `0000604002`.
- Field length limit is 7 (allows one separator, e.g. `604-002`). Recommended: emit clean 6 digits.

### 7.5 Telephone numbers (GRPCRD #24, CNSCRD #39, #41)
1. Strip ISD prefix (`+91`, `0091`, leading `91` when length 12) and non-digits.
2. Must not start with `1`.
3. Mobile: starts with 5/6/7/8/9 and ≥ 10 digits.
4. Landline: include STD code.
5. Reject straight ascending/descending sequences (e.g. `876543`, `234567`) and all-same digits (e.g. `222222`) — implement as checks on the full number.

### 7.6 Email (ADRCRD #63)
- Exactly one `@`, at least one `.`.
- ≥ 2 alphanumeric chars before `@`; before `@` allowed: `[A-Za-z0-9._-]`.
- Domain after `@`: ≥ 2 alphanumeric chars; ≥ 2 chars between `@` and next `.`.
- After last `.`: ≥ 2 alphabetic chars.
- Must not end with `@` or `.`; no spaces; no `..` (spec: "not more than two consecutive dots" — implement as reject on `..` to be safe, configurable).
- Case-insensitive (emit lowercase).

### 7.7 Address text (GRPCRD #20, ADRCRD #57, #60)
- Trimmed length ≥ 3.
- Reject if only special characters; special chars mixed with letters/digits are fine.
- Remove `|`, CR, LF.

---

## 8. Enumerations

### 8.1 Repayment Frequency (GRPCRD #11, ACTCRD #85)
| Code | Meaning |
|---|---|
| F01 | Weekly |
| F02 | Fortnightly |
| F03 | Monthly |
| F04 | Every two months |
| F05 | Quarterly |
| F06 | Semi-annually |
| F07 | Annually |
| F08 | Single payment loan (bullet/balloon) |
| F10 | Other |
| F11 | Daily |
| F12 | On demand |

(No F09.)

### 8.2 Loan Type (GRPCRD #16, ACTCRD #93)
`C01` Term Loan · `C02` Cash Credit

### 8.3 DPD
`000` = current with positive balance; `001`–`999` = days past due (cap 999). Always zero-padded 3 digits.

### 8.4 Write-off Reason (GRPCRD #19, ACTCRD #92)
| Code | Meaning |
|---|---|
| X01 | First payment default |
| X02 | Death |
| X03 | Willful default status |
| X04 | Suit filed, willful default status |
| X09 | Untagged |
| X10 | Not applicable |

### 8.5 Gender (CNSCRD #13)
`F` Female · `M` Male · `T` Third/Transgender

### 8.6 Telephone Type Indicator (GRPCRD #23, CNSCRD #38, #40)
| Code | Meaning |
|---|---|
| P01 | Residence |
| P02 | Company |
| P03 | Mobile |
| P04 | Permanent |
| P07 | Other |
| P08 | Untagged (default when number present, type absent) |

### 8.7 Relationship (CNSCRD #16, #18, #20, #22, #24, #26)
| Code | Meaning | Code | Meaning |
|---|---|---|---|
| K01 | Father | K09 | Father-in-law |
| K02 | Husband | K10 | Daughter-in-law |
| K03 | Mother | K11 | Sister-in-law |
| K04 | Son | K12 | Son-in-law |
| K05 | Daughter | K13 | Brother-in-law |
| K06 | Wife | K15 | Other |
| K07 | Brother | K16 | Sister |
| K08 | Mother-in-law | | |

(No K14.)

### 8.8 Header File Frequency — see §1.3.

### 8.9 Linked to Govt Program (GRPCRD #32)
`U01` NRLM · `U02` NULM · `U03` SRLM · `U04` MEPMA · `U99` Others (default if #31 set and #32 blank)

### 8.10 Marital Status (CNSCRD #14)
`M01` Married · `M02` Separated · `M03` Divorced · `M04` Widowed · `M05` Unmarried · `M06` Untagged

### 8.11 Educational Qualification (CNSCRD #42)
`Y01` Illiterate · `Y02` Passed 5th · `Y03` Passed 8th · `Y04` Passed 10th · `Y05` Above 10th

### 8.12 Occupation (CNSCRD #48)
`Z01` Home maker · `Z02` Landless laborer · `Z03` Marginal farmer · `Z04` Small farmer · `Z05` Non-farming · `Z06` Others

### 8.13 Religion (CNSCRD #51)
`R01` Hindu · `R02` Muslim · `R03` Christian · `R04` Sikh · `R05` Buddhist · `R06` Jain · `R07` Bahai · `R08` Others · `R09` Not stated

### 8.14 Caste / Social Strata (CNSCRD #52)
`V01` SC · `V02` ST · `V03` BC · `V04` NT/VJ · `V05` General · `V08` Others · `V09` Untagged

### 8.15 Member's Role (CNSCRD #53)
`L01` Leader/President/Office-bearer · `L02` Associate/Secretary · `L03` Member · `L04` Untagged

### 8.16 Member's Status (CNSCRD #55, SHG only)
`W01` Active (loanee) · `W02` Inactive/Dormant (non-loanee; ACTCRD not required/skipped) · `W03` Exited · `W04` Untagged

### 8.17 Loan Category (ACTCRD #71) — Appendix D
| Code | Name | Description |
|---|---|---|
| T01 | JLG Group | Loan to JLG; amounts/DPD tagged to each member's account, performance tracked at group level |
| T02 | JLG Individual | Loan to individual within JLG; tracked at individual account level |
| T03 | Individual | Collateral-free loan to individual, household income up to ₹3 lakh (Group ID not required) |
| T04 | SHG Group | Loan to SHG; tagged to member accounts, tracked at group level |
| T05 | SHG Individual | Loan to individual within SHG; tracked at individual level |
| T06 | SHG Group – Govt | Government-formed SHG |
| T07 | SHG Intra-Group | Lending among members within an SHG |
| T08 | Others | Anything else |

File routing suggestion: T04–T07 → SHG file; T01–T03 → JLG+Individual file; T08 → per business mapping (configurable).

### 8.18 Account Status (ACTCRD #75)
| Code | Meaning | #79 Date Closed | #90/#91 Write-off allowed |
|---|---|---|---|
| S04 | Current | must be blank | no |
| S05 | Delinquent (DPD > 0) | must be blank | no |
| S06 | Written off | – | required |
| S07 | Account closed | required | no |
| S08 | Restructured due to COVID-19 | must be blank | no |
| S09 | Restructured & closed | required | no |
| S10 | Settled | – | write-off amount required |
| S11 | Post write-off settled | – | write-off amount required |
| S12 | Post write-off closed | required | – |
| S13 | Account sold | – | – |
| S14 | Account purchased | – | – |
| S15 | Cancelled | – | – |
| S16 | Restructured due to natural calamity | – | – |
| S17 | Restructured loan | – | – |
| S18 | Restructured loan (Govt. mandated) | – | – |

S01–S03 (application-stage statuses) were **removed** in v3.3 — never emit them.

### 8.19 Type of Insurance (ACTCRD #96)
`L01` Life · `L02` Credit · `L03` Health/Medical · `L04` Property · `L05` Liability · `L10` Other

### 8.20 Insurance Indicator (ACTCRD #95)
`Y` / `N`

---

## 9. Appendices

### 9.1 Appendix B — State codes (as printed)

| Code | State | Code | State |
|---|---|---|---|
| AN | Andaman and Nicobar Islands | LD | Lakshadweep |
| AP | Andhra Pradesh | MH | Maharashtra |
| AR | Arunachal Pradesh | ML | Meghalaya |
| AS | Assam | MN | Manipur |
| BR | Bihar | MP | Madhya Pradesh |
| CG | Chandigarh ⚠ | MZ | Mizoram |
| CH | Chhattisgarh ⚠ | NL | Nagaland |
| DD | Dadra & Nagar Haveli and Daman & Diu | OR | Orissa |
| DL | Delhi | PB | Punjab |
| LA | Ladakh | PY | Puducherry |
| GA | Goa | RJ | Rajasthan |
| GJ | Gujarat | SK | Sikkim |
| HP | Haryana ⚠ | TN | Tamil Nadu |
| HR | Himachal Pradesh ⚠ | TR | Tripura |
| JH | Jammu and Kashmir ⚠ | TS | Telangana |
| JK | Jharkhand ⚠ | UK | Uttarakhand (formerly UA) |
| KA | Karnataka | UP | Uttar Pradesh |
| KL | Kerala | WB | West Bengal |

⚠ These six mappings **contradict Appendix C** — see §10 #1.

### 9.2 Appendix C — PIN prefix ranges (first two digits, inclusive)

| Code | State | Min | Max |
|---|---|---|---|
| JK | Jammu & Kashmir | 18 | 19 |
| LA | Ladakh | 18 | 19 |
| HP | Himachal Pradesh | 17 | 17 |
| PB | Punjab | 14 | 16 |
| CH | Chandigarh | 14 | 16 |
| HR | Haryana | 12 | 13 |
| DL | Delhi | 11 | 11 |
| UK | Uttarakhand | 24 | 26 |
| UP | Uttar Pradesh | 20 | 28 |
| RJ | Rajasthan | 30 | 34 |
| GJ | Gujarat | 36 | 39 |
| DD | Dadra & Nagar Haveli and Daman & Diu | 36 | 39 |
| MH | Maharashtra | 40 | 44 |
| GA | Goa | 40 | 40 |
| MP | Madhya Pradesh | 45 | 48 |
| CG | Chhattisgarh | 46 | 49 |
| TS | Telangana | 50 | 56 |
| AP | Andhra Pradesh | 50 | 56 |
| KA | Karnataka | 53 | 59 |
| TN | Tamil Nadu | 53 | 67 |
| PY | Puducherry | 53 | 67 |
| KL | Kerala | 67 | 69 |
| LD | Lakshadweep | 67 | 68 |
| WB | West Bengal | 70 | 74 |
| SK | Sikkim | 73 | 73 |
| AN | Andaman & Nicobar | 74 | 74 |
| TR | Tripura | 72 | 79 |
| OR | Orissa | 75 | 77 |
| AR | Arunachal Pradesh | 78 | 79 |
| AS | Assam | 78 | 79 |
| NL | Nagaland | 79 | 79 |
| MN | Manipur | 79 | 79 |
| MZ | Mizoram | 79 | 79 |
| ML | Meghalaya | 79 | 79 |
| BR | Bihar | 80 | 85 |
| JH | Jharkhand | 81 | 83 |

Ranges overlap (e.g. AP/TS 50–56, KA/TN/PY 53–59); validation is "prefix within the declared state's range", not reverse lookup.

### 9.3 Machine-readable configs (suggested)

```json
{
  "pin_prefix_ranges": {
    "JK":[18,19],"LA":[18,19],"HP":[17,17],"PB":[14,16],"CH":[14,16],"HR":[12,13],
    "DL":[11,11],"UK":[24,26],"UP":[20,28],"RJ":[30,34],"GJ":[36,39],"DD":[36,39],
    "MH":[40,44],"GA":[40,40],"MP":[45,48],"CG":[46,49],"TS":[50,56],"AP":[50,56],
    "KA":[53,59],"TN":[53,67],"PY":[53,67],"KL":[67,69],"LD":[67,68],"WB":[70,74],
    "SK":[73,73],"AN":[74,74],"TR":[72,79],"OR":[75,77],"AR":[78,79],"AS":[78,79],
    "NL":[79,79],"MN":[79,79],"MZ":[79,79],"ML":[79,79],"BR":[80,85],"JH":[81,83]
  },
  "account_status_requires_close_date": ["S07","S09","S12"],
  "account_status_forbids_close_date": ["S04","S05","S08"],
  "account_status_forbids_writeoff": ["S04","S05","S07","S08","S09"],
  "account_status_requires_writeoff_amount": ["S06","S10","S11"],
  "account_status_requires_settlement_amount": ["S10","S11"],
  "account_status_requires_positive_overdue_and_dpd": ["S05"],
  "account_status_requires_zero_balance": ["S07","S09","S12"]
}
```
(The `pin_prefix_ranges` keys follow **Appendix C** naming — see §10 #1.)

### 9.4 Appendix A — Special characters in consumer names

| Allowed | Disallowed |
|---|---|
| `.` `` ` `` `'` `;` `:` `"` `-` `_` `(` `)` `[` `]` `{` `}` `<` `>` | `~` `!` `#` `$` `%` `^` `&` `*` `=` `\|` `?` `+` `,` `@` |
| `/` `\` — **only** within `S/O`, `S\O`, `W/O`, `W\O`, `H/O`, `H\O`, `D/O`, `D\O` | `/` `\` anywhere else |

Disallowed character in any name field → **Record**.

---

## 10. ⚠ Spec issues to handle explicitly (implement as config + surface to user)

1. **State code conflicts between Appendix B and Appendix C.** B maps `CG`=Chandigarh, `CH`=Chhattisgarh, `HP`=Haryana, `HR`=Himachal, `JH`=J&K, `JK`=Jharkhand; C (and the PIN ranges) maps the opposite (`CH`=Chandigarh 14–16, `CG`=Chhattisgarh 46–49, `HP`=Himachal 17, `HR`=Haryana 12–13, `JK`=J&K 18–19, `JH`=Jharkhand 81–83). Appendix C matches standard usage and the PIN data. **Default: use Appendix C mapping**, keep a config switch, and confirm with the CIC before go-live.
2. **Disbursed amount = 0:** GRPCRD #5 and ACTCRD #83 say zero is allowed for cancelled loans, but ACTCRD #83 rule rejects zero. Default: allow 0 only when #75 = `S15`; flag otherwise.
3. **GRPCRD #20–22 option:** marked Mandatory but description says mandatory for loans disbursed ≥ 01-Jan-2027. Default: always populate; enforce record-reject only for disbursal ≥ 01-Jan-2027 (configurable).
4. **GRPCRD #20 rule** references "Permanent Address" (copy-paste); apply min-length 3 to the group address.
5. **ACTCRD #89 DPD** type A/N here vs N in GRPCRD #14 / changelog → always emit 3-digit numeric.
6. **ACTCRD #91** type `D` (stale) → treat as N, DDMMYYYY.
7. **ACTCRD #94** type `N` but holds account references; changelog says P → treat as P.
8. **CNSCRD #63 Email / GRPCRD #3 Group Name / HDR #5** typed A/N but legitimately contain `@ . -` or spaces → validate as P with field-specific rules.
9. **Header #1** allows `HDR` or `MFI`; changelog says "HDR/MFI". Default `HDR`, configurable.
10. **Header #3 / Trailer #2** version: spec allows `3.2`/`3.3`; document version is 3.3.1 but field is 3 chars → emit `3.3`.
11. **"S07, S09 AND S12"** in rules means *status IN (…)*.
12. **ACTCRD #86 SHG rule** mentions "other than enumeration" for an amount field (copy-paste) → treat as "invalid value".
13. **Email rule h)** "not more than two consecutive dots" — ambiguous; default reject any `..`.
14. **Account status S18** appears in the enumeration but not in the change log — include it.
15. **GRPCRD #17** write-off applicability text "loans disbursed on or after 1st Jan 2027" — implement the date gate as config.
16. **ACTCRD #75 status vs #88/#89**: S05 requires DPD > 0 and overdue > 0; consider also flagging S04 with DPD > 0 as a data-quality warning (not a CIC rule).

---

## 11. Implementation checklist

- [ ] Source-to-field mapping document (DB view column → UCRF field #) for SHG and JLG files
- [ ] Sanitiser: trim, strip `|`/CR/LF/non-ASCII, uppercase IDs, date → DDMMYYYY, amounts → integer
- [ ] Name packer (word-wrap across Name1/2/3, no truncation)
- [ ] Defaulting rules: P08 for phone type, U99 for govt program
- [ ] Validators per §2, §7.1–7.7, and per-field rules in §4–§7 with `REJECT_RECORD` / `REJECT_FIELD`
- [ ] Cross-segment checks (DOB vs disbursal, dates vs HDR Reported Date, status vs close/write-off)
- [ ] De-duplication of identical lines
- [ ] Header (190 chars) and Trailer (36 chars) fixed-width builders with length assertions
- [ ] Column-count assertion per data line (136 SHG / 100 JLG)
- [ ] File name builder per §1.3
- [ ] Rejection report (record key, field #, field name, rule, outcome, value)
- [ ] Unit tests incl. §10 edge cases
