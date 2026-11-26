# UCRF Consumer Repository — Data Input File Specification (v3.74)

> **Source:** *Uniform Credit Reporting Format – Consumer Repository*, Version 3.74, W.E.F. July 01, 2026 (Equifax / CRIF / Experian / TransUnion CIBIL).
> **Audience:** A coding agent building a generator and/or validator for consumer credit data submission files to Indian Credit Information Companies (CICs).
> **Fidelity:** Rules are transcribed from the source. Where the source contradicts itself, both values are kept and the conflict is listed in §14 *Known Source Inconsistencies*. Do not resolve these silently; make them configurable.

---

## 0. How to Use This Spec

1. §1–§4 define the file structure, encoding and global rules.
2. §5–§13 define each segment, field by field, with validation rules and failure outcomes.
3. Appendices A–F hold the lookup tables. Implement these as enums or constant maps.
4. §14 lists ambiguities to make configurable. §15 gives a suggested implementation outline.

### 0.1 Failure-outcome severity (lowest → highest)

| Outcome | Meaning for implementation |
|---|---|
| **Ignore / Default** | Drop the value or substitute the documented default. Processing continues. |
| **Reject Field** | Discard this field only. The segment and record stay. |
| **Reject Segment** | Discard this segment instance. The record survives if its required segments are still valid. |
| **Reject Record** | Discard the whole consumer record (PN … ES). |
| **Reject File** | Discard the entire submission file. |
| **Configurable to Reject Record** | Default is the lighter outcome shown. A CIC-level switch escalates it to Reject Record. |

General rule (from *Things to Remember*): a rejected **Required** field rejects the entire record. A rejected **Optional** field takes its default (if any) or is ignored.

---

## 1. File Overview

- Plain **text** only. Reports accounts in the names of **individuals only**. Non-individual or commercial entities go in the separate commercial file.
  - Example: a loan to "Mr. Amitabh Kumar" for a commercial vehicle goes in this file. A loan to "Amitabh Kumar & Sons" does not.
- **File name:** `<REPORTING MEMBER ID>-<DDMMYYYY file creation date>-<HHMMSS file creation time>.txt` (or `.gpg` if encrypted).
  - The source shows spaces around the hyphens (`<ID> - <date> - <time>`). See §14.

### 1.1 Segment inventory

| Tag | Name | Length | Type | Min/Record | Max/Record | Max/File | Max Bytes |
|---|---|---|---|---|---|---|---|
| `TUDF` or `CONS` | Header (HD) | Fixed | Required | N/A | N/A | 1 | 146 |
| `PN` | Name | Variable | Required | 1 | 1 | Unlimited | 174 |
| `ID` | Identification | Variable | See Comments | See Comments | 8 | Unlimited | 71 |
| `PT` | Telephone | Variable | See Comments | See Comments | 10 | Unlimited | 28 *(see §14)* |
| `EC` | Email Contact | Variable | Optional | 0 | 10 | Unlimited | 81 |
| `PA` | Address | Variable | Required | 1 | 5 | Unlimited | 256 |
| `TL` | Account | Variable | Required | 1 | 1 | Unlimited | 564 |
| `TH` | Account History | Variable | Optional | 0 | 35 *(see §14)* | Unlimited | 182 |
| `ES` | End of Subject | Fixed | Required | 1 | 1 | Unlimited | 6 |
| `TRLR` | Trailer | Fixed | Required | N/A | N/A | 1 | 4 |

### 1.2 File grammar

```
FILE    := HEADER RECORD+ TRAILER
RECORD  := PN ID{0..8} PT{0..10} EC{0..10} PA{1..5} TL TH{0..n} ES
```

- Exactly **one** HD at the start and **one** TRLR at the end of each update file. If a file spans multiple media, each medium has its own HD and TRLR.
- **Multiple TRLR in one file → Reject File.** Any data after TRLR → Reject File.
- Segment order inside a record is fixed: **PN, ID, PT, EC, PA, TL, TH, ES**.
- Field tags inside a segment must appear **in the order listed**. Some tags are intentionally skipped (e.g. PN has no tag 06).
- Each credit facility is a **separate record**, even for the same borrower.
- **Joint accounts:** one record per holder, each with Ownership Indicator `4`. Authorised users (`2`) and guarantors (`3`) likewise get their own records.

### 1.3 Record-level presence rules

- PN, TL and ES must be present and valid.
- At least one **valid** PA must be present.
- For accounts with TL Date Opened/Disbursed **on/after 01-06-2007**, the record needs at least one valid PT segment **or** at least one valid ID segment whose ID Type is one of `01, 02, 03, 04, 05, 06, 09, 10`. Otherwise → **Reject Record**.

### 1.4 Duplicate records

A duplicate is a byte-identical record (same borrower and tradeline data) submitted more than once. Isolate duplicates. Report the total input count and the duplicate count separately. Compute all acceptance, rejection and data-quality metrics on **unique eligible records only** (total − duplicates).

---

## 2. Encoding Rules

### 2.1 Fixed-length segments (HD, ES, TRLR)

Place each field at its exact byte position. The segment must be exactly its stated total length.

### 2.2 Variable-length segments (all others)

Each field is encoded as **Tag + Length + Value**:

| Part | Size | Content |
|---|---|---|
| Field Tag | 2 bytes | e.g. `01` |
| Length | 2 bytes | Zero-padded byte count of the value, e.g. `11` |
| Value | n bytes | The data |

Example: Consumer Name Field1 = `HAREN PATEL` → `0111HAREN PATEL`.

Segment max size = Σ(max field lengths) + Σ(2-byte tags + 2-byte lengths).
Example for PN: 8 tags (16) + 8 lengths (16) + 142 = **174 bytes**.

> The segment-tag "field" (e.g. `PN` + `N01`) follows the same tag/length/value pattern in the byte-count arithmetic used by the source. Verify against a sample file from the CIC before finalising the encoder. See §14.

### 2.3 Padding and justification (fixed-length fields)

| Char type | Justify | Pad with |
|---|---|---|
| A, A/N, A/N* | Left | Spaces |
| N | Right | Zeros |

Signed numerics put `+` or `-` in the **rightmost** position. No sign means positive.

---

## 3. Data Types

### 3.1 Character types

| Code | Meaning |
|---|---|
| `A` | Alphabetic — letters only |
| `N` | Numeric — whole numbers only (unless stated as signed) |
| `D` | Decimal — `digits . digits` (e.g. `99.27`) |
| `A/N` | Alphanumeric — letters and digits |
| `A/N*` | Alphanumeric with space |
| `P` | Printable — ASCII 32–126 (incl. space, `-`, `~`) |

Unidentified or non-printable characters may reject the field or record. A wrong character type (e.g. a letter in an N field) → field rejected.

### 3.2 Length types

- `F` Fixed — the value must be exactly the stated length.
- `V` Variable — the value must be ≤ the stated maximum.

### 3.3 Field types

- **Required** — must be furnished. Missing or invalid → segment and/or record rejected.
- **Optional** — CIs are encouraged to send. Invalid or badly formatted values are ignored (not loaded) and processing continues.
- **Conditional Mandatory / See Comments / When Applicable** — conditions are given in the field's comments.

### 3.4 Enumerations

When the Comments column lists values, **only those values are valid**. Anything else is rejected.

### 3.5 Monetary amounts

Whole numbers only (e.g. `1`, `51693`, `309820`), in Indian Rupees. All amount fields have a maximum length of **20** (changed in v3.74).

---

## 4. Date Rules (apply to every date field)

- Format `DDMMYYYY`, exactly 8 digits, and a valid calendar date. Example: `09032001` = 9 March 2001.
- Fewer than 8 digits → rejected (at field or record level, per that field's rule).
- Must be **> 01011900** and **≤ current date / file processing date**.

### 4.1 Cross-field date constraints (summary)

`HD.55` is the Header Date Reported and Certified. `TL.11` is the record-level Date Reported and Certified.

| Field | Constraint |
|---|---|
| PN.07 DOB | ≤ HD.55, ≤ TL.09, ≤ TL.10, ≤ TL.11 |
| ID.03 Issue Date | ≤ HD.55; > PN.07; < ID.04 |
| ID.04 Expiration Date | > PN.07; > ID.03 |
| TL.08 Date Opened | ≤ HD.55, ≤ TL.09, ≤ TL.10, ≤ TL.11 |
| TL.09 Last Payment | ≤ HD.55; ≥ TL.08; ≤ TL.10 *only if TL.22 absent*; ≤ TL.11 |
| TL.10 Date Closed | ≤ HD.55; ≥ TL.08; ≥ TL.09 *only if TL.22 absent*; ≤ TL.11 |
| TL.11 Date Reported | ≤ / should match HD.55; ≥ TL.08; ≥ TL.09; at most 1 year old; latest = yesterday |
| TH.01 History Date | Day forced to `01`; month ≤ HD.55 month; month ≤ TL.11 month; ≤ 48 months before TL.11; latest = month before TL.11 |
| TH.08 Last Payment | ≤ HD.55; ≤ TH.01 |

---

## 5. Header Segment (HD) — Fixed, 146 bytes

- Required. One per update file. **All fields must be provided, otherwise the entire file is rejected.**

| Pos | Field | Type | Len | Rules |
|---|---|---|---|---|
| 1 | Segment Tag | A | 4 | `TUDF` or `CONS`. |
| 5 | Version | N | 2 | Must be `12`. |
| 7 | Reporting Member / Processor User ID | P | 30 | ID assigned by the CIC to the member or its data processor. |
| 37 | Reporting Member / Processor Short Name | A/N* | 16 | Optional. Space-fill unused bytes. |
| 53 | Reporting Cycle | A/N | 2 | Optional. Space-fill if unused. Enum: `DL` daily incremental; `W1` 1st weekly (as on 9th); `W2` 2nd weekly (as on 16th); `W3` 3rd weekly (as on 23rd); `ME` month end; `DC` data correction/dispute; `AH` other ad hoc; `RR` data rejection resubmission. Used by CICs for RBI compliance reporting on the Daksh portal on a best-effort basis. |
| 55 | Date Reported and Certified | N | 8 | The **as-of** date of the data, not the extraction or file date. For corrections, use the original submission's as-of date (e.g. a Mar-22 record corrected in Mar-26 → `31032022`). Latest allowed = current date. Used for all regulatory reports. |
| 63 | Future Use | P | 30 | Space-fill. Values are ignored, but junk or non-printable characters → **Reject File**. |
| 93 | Future Use | A | 1 | Same as above. |
| 94 | Future Use | P | 5 | Zero-fill, or space-fill. |
| 99 | Member Data | P | 48 | Free text for member reference only. Space-fill. |
| | **Total** | | **146** | |

> The source states "all fields must be provided" while also marking positions 37 and 53 optional. Treat optional fields as space-filled (present, but blank).

---

## 6. Name Segment (PN) — Variable, max 174 bytes

- Required. Once per record. Tag 06 is reserved (skipped).

| Tag | Field | Field Type | Char | Len Type | Max | Rules | Applicable Rule → Outcome |
|---|---|---|---|---|---|---|---|
| PN | Segment Tag | Required | A/N | F | 3 | Value `N01`. | |
| 01 | Consumer Name Field1 | Required | P | V | 26 | See *Name rules* below. | Only one word and no initials across Fields 1–5, **and** no valid ID or mobile number → **Reject Record** |
| 02 | Consumer Name Field2 | See Comments | P | V | 26 | Same rules. | (as above) |
| 03 | Consumer Name Field3 | See Comments | P | V | 26 | Same rules. | (as above) |
| 04 | Consumer Name Field4 | See Comments | P | V | 26 | Same rules. | (as above) |
| 05 | Consumer Name Field5 | See Comments | P | V | 26 | Same rules. | (as above) |
| 07 | Date of Birth | See Comments | N | F | 8 | Required for accounts opened/disbursed on/after **01-05-2005**. Otherwise required if present in the member's system. Date rules and §4.1 cross-checks apply. | Not required & invalid → **Reject Field**. Required & fails date rules → **Reject Record** (configurable). |
| 08 | Gender | See Comments | N | F | 1 | Same requirement condition as DOB. Enum: `1` Female, `2` Male, `3` Transgender. | Not required & invalid → **Reject Field**. Required & not provided → **Reject Record**. |
| | Σ max lengths | | | | 142 | | |
| | Σ tags + lengths | | | | 32 | | |
| | **Max segment size** | | | | **174** | | |

### 6.1 Name rules (Fields 01–05)

1. Use as many name fields as needed for the **full** name. There must be at least one word, and that word must not be just an initial.
2. At least **one token with ≥ 2 alphabetic characters** is mandatory.
3. A **single-word name** is accepted only if a valid ID (any ID type) **or** a mobile number is provided.
4. Never split a word across fields (e.g. not `PAT` + `EL`). Move the whole word to the next field.
5. CICs join the fields with a single space when displaying the name.
6. **Relationship descriptors:** only `S/O`, `W/O`, `H/O`, `D/O` are allowed, with `/` or `\` (e.g. `S\O`). Any other format (e.g. `C/O AMIT`) is not permitted.
   - A name token **must precede** the descriptor. A name that **begins** with a descriptor → rejected.
7. **Salutations:** a trailing salutation is treated as part of the name (`Rakesh Mr.` = 2 tokens, accepted). A leading salutation is ignored (`Mr. Rakesh` → single name `Rakesh` → needs a valid ID).
8. **Digits are strictly prohibited** in names.
9. Disallowed characters (Appendix A), numbers and commercial names → **Reject Record**.
10. The onus of data accuracy is on the CI.

---

## 7. Identification Segment (ID) — Variable, max 71 bytes

- Optional for accounts opened **before 01-06-2007**.
- For accounts opened on/after 01-06-2007, ID is **required if no valid PT is provided**. If there is neither a valid ID (types `01, 02, 03, 04, 05, 06, 09, 10`) nor a valid PT → **Reject Record**.
- Maximum **8** per record. *(The source also says "any extra ID segments after the 10th will be rejected". See §14.)*
- ID Types must be **unique** within a record. If two or more different ID numbers are given for the same ID Type → **reject all of them**.
- CKYC is required when present in the CI's system, but its absence never rejects the record.

| Tag | Field | Field Type | Char | Len Type | Max | Rules | Applicable Rule → Outcome |
|---|---|---|---|---|---|---|---|
| ID | Segment Tag | Required | A | F | 3 | `I01` … `I08`, sequential across the record's ID segments. | |
| 01 | ID Type | Conditional Mandatory | N | F | 2 | Enum: `01` PAN; `02` Passport; `03` Voter ID; `04` Driver's License; `05` Ration Card; `06` UID (Aadhaar); `07` Additional ID #1 (future); `08` Additional ID #2 (future); `09` CKYC; `10` G RAM G. | Value not in enum → **Reject Segment**. Value `07` or `08` → **Reject Segment**. |
| 02 | ID Number | See Comments | P | V | 30 | Per-type format, see §7.1. | |
| 03 | Issue Date | Optional | N | F | 8 | ≤ HD.55; > PN.07; < ID.04. | **Reject Field** |
| 04 | Expiration Date | Optional | N | F | 8 | > PN.07; > ID.03. | **Reject Field** |
| | Σ max lengths | | | | 51 | | |
| | Σ tags + lengths | | | | 20 | | |
| | **Max segment size** | | | | **71** | | |

### 7.1 ID Number validation by type

Strip delimiters (space, hyphen, etc.) before the length and pattern checks, unless the rule says otherwise. Each type is required when present in the member's system.

| Type | Rule |
|---|---|
| `01` PAN | ≥ 10 chars. Pattern: 5 letters + 4 digits + 1 letter (e.g. `ABCPD1234E`). **4th letter must be `P` or `H`**, otherwise the PAN is ignored. Suggested regex: `^[A-Z]{3}[PH][A-Z][0-9]{4}[A-Z]$`. |
| `02` Passport | 7–10 chars (e.g. `A-9388067`). The first or second character must be a letter; the remaining characters must be digits. |
| `03` Voter ID (EPIC) | ≥ 9 chars after stripping delimiters and special characters. Old format e.g. `MT/08/039/0060725`. New format e.g. `AAA/999999/0`: the first 2–3 characters are letters and the rest is alphanumeric. |
| `06` UID | Exactly 12 digits. The 12th digit is a **check digit** over the first 11 (Aadhaar uses the Verhoeff algorithm). Invalid check digit → ID rejected. |
| `09` CKYC | Up to 15 alphanumeric characters. Non-submission does not reject the record. |
| `10` G RAM G | Format `SS-DD-BBB-GGG-VVV/HHH<Surname>` (e.g. `MH-12-123-123-123/123Patil`). SS = state code, 2 letters; DD = district, 2 digits; BBB = block, 3 digits; GGG = gram panchayat, 3 digits; VVV = village, 3 digits; HHH+Surname = household number, 3 digits + alphanumeric surname. |
| Others (`04`, `05`) | Required if present in the member's system. **No validation performed.** |

---

## 8. Telephone Segment (PT) — Variable, max 28 bytes *(see §14)*

- Required if no valid ID from the ID Type catalogue is present. Up to **10** per record.
- See the §1.3 rule for accounts opened on/after 01-06-2007.

| Tag | Field | Field Type | Char | Len Type | Max | Rules | Applicable Rule → Outcome |
|---|---|---|---|---|---|---|---|
| PT | Segment Tag | Required | N | F | 3 | `T01` … `T10`, sequential. | |
| 01 | Telephone Number | Conditional Mandatory | P | V | 20 | See §8.1. | No valid number after editing → **Reject Segment** |
| 02 | Telephone Extension | Optional | A/N | V | 10 | | |
| 03 | Telephone Type | See Comments | P | F | 2 | Enum: `00` Not Classified, `01` Mobile, `02` Home, `03` Office. | |
| | Σ max lengths | | | | 35 | | |
| | Σ tags + lengths | | | | 16 | | |
| | **Max segment size** | | | | **51** | | |

### 8.1 Telephone Number rules

1. Required when present in the member's system. If present, it must have ≥ **5 digits**.
2. Must **not start with `1`**.
3. **Indian mobile:** after stripping the ISD prefix, must start with `5`, `6`, `7`, `8` or `9` and have ≥ **10 digits**. New series are added periodically by the CICs.
4. **ISD number:** ≥ **9 digits**.
5. **Multiple numbers** are allowed only when Telephone Type = `00`. Separate them with `\` `/` `,` `:` `;` or `&`.
6. **Pre-validation edits.** Remove each of the following, then validate only what is left:
   - Numbers that are a straight ascending or descending sequence (e.g. `876543`, `234567`).
   - Numbers made of a single repeated digit (e.g. `222222`).
   - A truncated trailing number in a multi-number field. Example: in `5436734,645231,012 3`, the last number `012 3` is truncated and is dropped.

> The source text for rule 6 contains a garbled, duplicated sentence fragment ("3. The last no. '012 3' is clearly a truncated no…"). The intent is reconstructed above.

---

## 9. Email Contact Segment (EC) — Variable, max 81 bytes

- Optional. Up to **10** per record.

| Tag | Field | Field Type | Char | Len Type | Max | Rules | Outcome |
|---|---|---|---|---|---|---|---|
| EC | Segment Tag | Required | A | F | 3 | `C01` … `C10`, sequential. | |
| 01 | E-Mail ID | Optional | P | V | 70 | Must pass Appendix E checks. | **Reject Segment** |
| | Σ max lengths | | | | 73 | | |
| | Σ tags + lengths | | | | 8 | | |
| | **Max segment size** | | | | **81** | | |

---

## 10. Address Segment (PA) — Variable, max 256 bytes *(header text says 259; see §14)*

- Required. 1–5 per record. Segments after the 5th → rejected. At least one valid PA is required; all invalid PAs are rejected.
- Address lines 1–5 are free format.
- **Foreign addresses** (State Code `77`) are allowed only for these account types: Credit Card, Loan on Card, Education Loan, Home Loan.

| Tag | Field | Field Type | Char | Len Type | Max | Rules | Applicable Rule → Outcome |
|---|---|---|---|---|---|---|---|
| PA | Segment Tag | Required | A/N | F | 3 | `A01` … `A05`, sequential. | Combined length of Address Lines 01–05 < 3 → **Reject Segment** (reason: invalid address) |
| 01 | Address Line1 | Required | P | V | 40 | See §10.1. | |
| 02 | Address Line2 | See Comments | P | V | 40 | | |
| 03 | Address Line3 | See Comments | P | V | 40 | | |
| 04 | Address Line4 | See Comments | P | V | 40 | | |
| 05 | Address Line5 | See Comments | P | V | 40 | | |
| 06 | State Code | See Comments | N | F | 2 | Required for accounts opened on/after **01-06-2006**; otherwise required if present in the member's system. Use Appendix B. APO address with unknown state → `99`. Foreign → `77`. | Required & invalid → **Reject Segment**. Not required & invalid → **Reject Field**. PIN's first 2 digits outside this state's Appendix C range → **Reject Segment**. |
| 07 | PIN Code | See Comments | P | V | 7 | See §10.2. | Required & invalid → **Reject Segment**. Not required & invalid → **Reject Field**. |
| 08 | Address Category | See Comments | N | F | 2 | Enum: `01` Permanent, `02` Residence, `03` Office, `04` Not Categorized, `05` Mortgage Property. Missing or invalid → **default `04`**. | |
| 09 | Residence Code | — | N | F | 2 | Enum: `01` Owned, `02` Rented. Invalid values are ignored. | **Reject Field** |
| | Σ max lengths | | | | 216 | | |
| | Σ tags + lengths | | | | 40 | | |
| | **Max segment size** | | | | **256** | | |

### 10.1 Address line rules

- The complete address may include location, city/town, district, state and PIN across lines 1–5, and must match the KYC documents submitted to the CI.
- Never split a word across lines (e.g. not `FLA` + `T`). Never truncate addresses.

### 10.2 PIN Code rules

1. Required for accounts opened/disbursed on/after **01-06-2006**.
2. Strip every character that is not `0-9`. The result must be **exactly 6 digits**, and the **last three must not be `000`**.
3. **APO address** with the field required and the PIN unknown → use a value in `900000`–`999999`.
4. The first two digits must fall within the state's min/max range in Appendix C.
5. **Foreign (State 77):** PIN is not validated beyond length and character checks.
6. Valid examples: `604002`, `604-002`, `900000`, `900 000`. Invalid examples: `604`, `6040020000`, `0000604002`.

---

## 11. Account Segment (TL) — Variable, max 564 bytes *(header text says 433; see §14)*

- Required. Once per record. Reserved and skipped tags: **06, 07, 23–25, 27–33**.
- A suppressed account submitted in UCRF is accepted and updated, but the updates are shown only after the account is unsuppressed.

| Tag | Field | Field Type | Char | Len Type | Max | Rules | Applicable Rule → Outcome |
|---|---|---|---|---|---|---|---|
| TL | Segment Tag | Required | A/N | F | 4 | `T001`. | |
| 01 | Current/New Reporting Member Code | Required | A/N | F | 10 | Must match the HD Reporting Member / Processor User ID. If the code changed, put the new code here and the old one in TL.16. | **Reject Record** |
| 02 | Current/New Member Short Name | Optional | A/N* | V | 16 | Short name of the reporting member. If changed, put the new name here and the old one in TL.17. | **Reject Field** |
| 03 | Current/New Account Number | Required | P | V | 25 | Account number assigned by the member. If changed, put the new number here and the old one in TL.18. If numbers are not unique across branches, **prefix the branch/office code**. For credit cards and similar products, send an account number **different from the card number**. | Shorter than the CIC-configured minimum (per account type or system-wide; e.g. type `02` → 4, type `05` → 4) → **Reject Record** |
| 04 | Account Type | Required | N | F | 2 | Appendix D. If changed, put the old type in TL.19. | **Reject Record** |
| 05 | Ownership Indicator | Required | N | F | 1 | Enum: `1` Individual, `2` Authorised User (supplementary card holder), `3` Guarantor, `4` Joint, `5` Deceased. Value `2` requires TL.04 = `10`. If changed, put the old value in TL.20. | **Reject Record** |
| 08 | Date Opened/Disbursed | Required | N | F | 8 | Date of first disbursement. For credit and fleet cards, the card activation date. Cross-checks in §4.1. | **Reject Record** |
| 09 | Date of Last Payment | Optional | N | F | 8 | Most recent payment date. Cross-checks in §4.1. May be later than TL.10 if TL.22 is populated (including `99`). | **Reject Field** |
| 10 | Date Closed | Optional | N | F | 8 | Date the account or credit limit was closed. If present, TL.13 must be `0` or negative. Cross-checks in §4.1. | Date Closed present **and** Current Balance > 0 → **Reject Record** |
| 11 | Date Reported and Certified | See Comments | N | F | 8 | As-of date of this record's data. Defaults to HD.55. Latest = yesterday. At most 1 year old. Cross-checks in §4.1. | Missing → **default to HD.55**. Older than 1 year or later than yesterday → **Reject Record** |
| 12 | High Credit / Sanctioned Amount | Required | N | V | 20 | Account types `10`, `16`, `31`, `35`: highest credit ever used. All others: sanctioned amount. Whole number ≥ 0, INR. | **Reject Record** |
| 13 | Current Balance | Required | N (signed) | V | 20 | Total outstanding (current + overdue) as of TL.11. **Includes interest** for types `10`, `16`, `31`, `35`; **excludes interest** for all others. Up to 19 digits plus an optional trailing sign: `+` = debit balance, `-` = credit balance; no sign = positive. Zero → `0`. Must be `0` for closed accounts with zero balance. | **Reject Record** |
| 14 | Amount Overdue | Optional | N | V | 20 | Overdue amount as of TL.11 (principal + interest + other charges). Whole number, positive, INR. If TL.04 ∉ {`10`, `16`, `31`, `35`} and TL.15 > 0, then TL.14 must be > 0. | TL.04 ∉ {`10`, `16`, `31`, `35`} **and** TL.14 > 0 **and** TL.15 = `000` → **Reject Record** (system treats the error as not reported) |
| 15 | Number of Days Past Due | See Comments | N | V | 3 | DPD as of TL.11 (e.g. `68`). Current → `0`. Max `900`; send `900` for anything higher. If TL.04 ∉ {`10`, `16`, `31`, `35`} and TL.14 > 0, then TL.15 must be > 0. **TL.15 or TL.26 must be present.** | Both TL.15 and TL.26 missing → **Reject Record**. TL.04 ∉ {`10`, `16`, `31`, `35`} **and** TL.15 > 0 **and** TL.14 = 0 → **Reject Record** (treated as not reported). TL.15 > 900 → **set to 900**. |
| 16 | Old Reporting Member Code | Optional / See Comments | A/N | F | 10 | Required when TL.01 is reported for the first time after a change. | This field or TL.17 invalid → **Reject Record** |
| 17 | Old Member Short Name | Optional / See Comments | A/N* | V | 16 | Required when TL.02 changes. If present, TL.16 is required. | This field or TL.16 invalid → **Reject Record** |
| 18 | Old Account Number | Optional / See Comments | P | V | 25 | Required when TL.03 changes. If present, TL.16 should be reported. | **Reject Record** |
| 19 | Old Account Type | Optional / See Comments | N | F | 2 | Required when TL.04 changes. Appendix D. | **Reject Field** |
| 20 | Old Ownership Indicator | Optional / See Comments | N | F | 1 | Required when TL.05 changes. Same enum as TL.05. | **Reject Field** |
| 21 | Suit Filed / Wilful Default | Optional | N | F | 2 | Enum: `00` No suit filed, `01` Suit filed, `02` Wilful default, `03` Suit filed (wilful default). Not reported → stored as blank for that reporting date. | **Reject Field** |
| 22 | Credit Facility Status | When Applicable | N | F | 2 | Appendix F enum (`00`–`17`, `99`). Not reported → the previously reported value **continues to display** in the credit report. | Invalid → **Reject Field**. Status ∈ {`02`, `03`, `04`} and TL.41 **and** TL.42 both missing → **configurable Reject Record**. Status `17` and TL.05 ≠ `3` → **configurable Reject Record**. |
| 26 | Asset Classification | See Comments | N | F | 2 | Enum: `01` Standard, `02` Substandard, `03` Doubtful, `04` Loss, `05` Special Mention Account. **TL.15 or TL.26 must be present.** | Both TL.15 and TL.26 missing → **Reject Record**. Both present → TL.15 takes precedence and TL.26 is **rejected (field)**. |
| 34 | Value of Collateral | Optional | N | V | 20 | INR, whole number, > 0. | TL.35 missing or `00` → **Reject Field** |
| 35 | Type of Collateral | Optional | N | F | 2 | Enum: `00` None, `01` Property, `02` Gold, `03` Shares, `04` Savings Account & FD, `05` Multiple Securities, `06` Others. | **Reject Field** |
| 36 | Credit Limit | Optional | N | V | 20 | Only for TL.04 ∈ {`10`, `16`, `31`, `35`}. Whole number ≥ 0, INR. | Present with any other account type → **Reject Field** |
| 37 | Cash Limit | Optional | N | V | 20 | Only for TL.04 ∈ {`10`, `16`, `31`, `35`}. Whole number ≥ 0, INR. | Present with any other account type → **Reject Field** |
| 38 | Rate of Interest | Optional | D | V | 8 | Numeric, no `%`. Format `d{1,4}.d+`. Keep only the first 3 decimal places and truncate the rest. | More than 4 integer digits, or value `0.0` → **Reject Field** |
| 39 | Repayment Tenure | Optional | N | V | 3 | Number of months. | Value `0` → **Reject Field** |
| 40 | EMI Amount | Optional | N | V | 20 | Report for every account type **except** `10`, `16`, `31`, `35`, `12`. Whole number > 0, INR. | Present with type ∈ {`10`, `16`, `31`, `35`, `12`} → **Reject Field**. Value `0` → **Reject Field**. |
| 41 | Written-off Amount (Total) | See Comments | N | V | 20 | Report only when TL.22 ∈ {`02`, `03`, `04`}. Whole number > 0, INR. | Present while TL.22 ∉ {`02`, `03`, `04`} → **Reject Field**. Value `0` and TL.42 also `0` → **Reject Field**. TL.41 and TL.42 both missing while TL.22 ∈ {`02`, `03`, `04`} → **configurable Reject Record**. |
| 42 | Written-off Amount (Principal) | See Comments | N | V | 20 | Report only when TL.22 ∈ {`02`, `03`, `04`}. Whole number > 0, INR. Must be ≤ TL.41. | Present while TL.22 ∉ {`02`, `03`, `04`} → **Reject Field**. Value `0` and TL.41 also `0` → **Reject Field**. Value > TL.41 → **Reject Field**. Both missing (as in TL.41) → **configurable Reject Record**. |
| 43 | Settlement Amount | See Comments | N | V | 20 | Report only when TL.22 ∈ {`03`, `04`, `15`, `16`}. Whole number > 0, INR. | Present while TL.22 ∉ {`03`, `04`, `15`, `16`} → **Reject Field**. Value `0` → **Reject Field**. |
| 44 | Payment Frequency | Optional | N | F | 2 | Enum: `01` Weekly, `02` Fortnightly, `03` Monthly, `04` Quarterly, `05` Bullet, `06` Daily, `07` Half-yearly, `08` Yearly, `09` On-demand. | **Reject Field** |
| 45 | Actual Payment Amount | Optional | N | V | 20 | Whole number > 0, INR. | Value `0` → **Reject Field** |
| 46 | Occupation Code | Optional | N | F | 2 | Enum: `01` Salaried, `02` Self-Employed Professional, `03` Self-Employed, `04` Others. | **Reject Field** |
| 47 | Income | Optional | N | V | 20 | Whole number > 0. | Value `0` → **Reject Field** |
| 48 | Net/Gross Income Indicator | Optional | A | F | 1 | Enum: `G` Gross, `N` Net. | TL.47 missing or invalid → **Reject Field** |
| 49 | Monthly/Annual Income Indicator | Optional | A | F | 1 | Enum: `M` Monthly, `A` Annual. | TL.47 missing or invalid → **Reject Field** |
| | Σ max lengths | | | | 412 | | |
| | Σ tags + lengths | | | | 152 | | |
| | **Max segment size** | | | | **564** | | |

> **Card account types** (used throughout TL): `10` Credit Card, `16` Fleet Card, `31` Secured Credit Card, `35` Corporate Credit Card. Define these once as a constant set, e.g. `CARD_TYPES = {10, 16, 31, 35}`.

---

## 12. Account History Segment (TH) — Variable, max 182 bytes

- **For initial loading of historical data only.** Optional.
- At most one TH per year/month of Account History Date per record. History dates must be unique within the record.
- Existing history for the same year/month is **overwritten**.
- Covers at most the **last 48 months** before the TL.11 year/month.
- Max occurrences per record: **47** (segment text and tag range `H01`–`H47`) vs **35** (overview table). See §14.

| Tag | Field | Field Type | Char | Len Type | Max | Rules | Applicable Rule → Outcome |
|---|---|---|---|---|---|---|---|
| TH | Segment Tag | Required | A/N | F | 3 | `H01` … `H47`, sequential. | |
| 01 | Account History Date | Required | N | F | 8 | As-of date of the history row. Day is **always reset to `01`** (e.g. March 2008 → `01032008`). Latest = month before TL.11. Cross-checks in §4.1. | History already exists for this date → **Reject Segment** |
| 02 | Asset Classification / NDPD | Required | P | F | 3 | One of `STD`, `SUB`, `DBT`, `LSS`, `SMA`, or a 3-digit DPD `###` (see TL.15). | **Reject Segment** |
| 03 | Amount Overdue | Optional | N | V | 20 | As of the history month. Whole number, positive, INR. See TL.14. | **Reject Segment** |
| 04 | High Credit / Sanctioned Amount | Optional | N | V | 20 | Types `10` and `16`: highest credit used. Others: sanctioned amount as of the month. Whole number ≥ 0. | **Reject Record** *(as stated in source)* |
| 05 | Credit Limit | Optional | N | V | 20 | Only when TL.04 = `10`. Whole number ≥ 0. | Present with TL.04 ≠ `10` → **Reject Field** |
| 06 | Cash Limit | Optional | N | V | 20 | Only when TL.04 = `10`. Whole number ≥ 0. | Present with TL.04 ≠ `10` → **Reject Field** |
| 07 | Current Balance | Required | N (signed) | V | 20 | Outstanding as of the month. Includes interest for type `10` only. Trailing `+`/`-` sign rules as TL.13. `0` for closed accounts with zero balance. *(Source comment says max 10 bytes = 9 digits + sign; see §14.)* | **Reject Segment** |
| 08 | Date of Last Payment | Optional | N | F | 8 | ≤ HD.55; ≤ TH.01. | **Reject Field** |
| 09 | Actual Payment Amount | Optional | N | V | 20 | Whole number > 0, INR. | Value `0` → **Reject Field** |
| | Σ max lengths | | | | 142 | | |
| | Σ tags + lengths | | | | 40 | | |
| | **Max segment size** | | | | **182** | | |

---

## 13. End of Subject (ES) and Trailer (TRLR) — Fixed

### 13.1 ES — 6 bytes, required, once per record

| Pos | Field | Type | Len | Value |
|---|---|---|---|---|
| 1 | End Segment | A/N | 6 | Literal `ES02**` (`ES` = tag, `**` = end characters). |

### 13.2 TRLR — 4 bytes, required, once per file

| Pos | Field | Type | Len | Value |
|---|---|---|---|---|
| 1 | Trailer Segment | A | 4 | Literal `TRLR`. |

- More than one TRLR, or any data after TRLR → **Reject File**.

---

## Appendix A — Allowed / Disallowed Special Characters (PN Fields 01–05)

| Allowed | Disallowed |
|---|---|
| `.` `` ` `` `'` `;` `:` `"` `-` `_` `(` `)` `[` `]` `{` `}` `<` `>` `/` `\` | `~` `!` `#` `$` `%` `^` `&` `*` `=` `\|` `?` `+` `,` `@` `/` `\` |

**Note:** `/` and `\` appear in both columns. They are allowed **only** inside the relationship descriptors `S/O`, `S\O`, `W/O`, `W\O`, `H/O`, `H\O`, `D/O`, `D\O`. Anywhere else they → **Reject Record**.

---

## Appendix B — State / Union Territory Codes (Census 2001 basis)

| Code | State/UT | Code | State/UT | Code | State/UT |
|---|---|---|---|---|---|
| 01 | Jammu & Kashmir | 02 | Himachal Pradesh | 03 | Punjab |
| 04 | Chandigarh | 05 | Uttarakhand | 06 | Haryana |
| 07 | Delhi | 08 | Rajasthan | 09 | Uttar Pradesh |
| 10 | Bihar | 11 | Sikkim | 12 | Arunachal Pradesh |
| 13 | Nagaland | 14 | Manipur | 15 | Mizoram |
| 16 | Tripura | 17 | Meghalaya | 18 | Assam |
| 19 | West Bengal | 20 | Jharkhand | 21 | Orissa |
| 22 | Chhattisgarh | 23 | Madhya Pradesh | 24 | Gujarat |
| 25 | Dadra & Nagar Haveli and Daman & Diu | — | *(26 not used)* | 27 | Maharashtra |
| 28 | Andhra Pradesh | 29 | Karnataka | 30 | Goa |
| 31 | Lakshadweep | 32 | Kerala | 33 | Tamil Nadu |
| 34 | Pondicherry | 35 | Andaman & Nicobar Islands | 36 | Telangana |
| 37 | Ladakh | 77 | Foreign Address | 99 | APO Address |

---

## Appendix C — PIN Code Prefix Validation

The first two digits of the 6-digit PIN must fall within [Min, Max] for the State Code.

| State Code | State | Min | Max |
|---|---|---|---|
| 01 | Jammu & Kashmir | 18 | 19 |
| 02 | Himachal Pradesh | 17 | 17 |
| 03 | Punjab | 14 | 16 |
| 04 | Chandigarh | 14 | 16 |
| 05 | Uttarakhand | 24 | 26 |
| 06 | Haryana | 12 | 13 |
| 07 | Delhi | 11 | 11 |
| 08 | Rajasthan | 30 | 34 |
| 09 | Uttar Pradesh | 20 | 28 |
| 10 | Bihar | 80 | 85 |
| 11 | Sikkim | 73 | 73 |
| 12 | Arunachal Pradesh | 78 | 79 |
| 13 | Nagaland | 79 | 79 |
| 14 | Manipur | 79 | 79 |
| 15 | Mizoram | 79 | 79 |
| 16 | Tripura | 72 | 79 |
| 17 | Meghalaya | 79 | 79 |
| 18 | Assam | 78 | 79 |
| 19 | West Bengal | 70 | 74 |
| 20 | Jharkhand | 81 | 83 |
| 21 | Orissa | 75 | 77 |
| 22 | Chhattisgarh | 46 | 49 |
| 23 | Madhya Pradesh | 45 | 48 |
| 24 | Gujarat | 36 | 39 |
| 25 | Dadra & Nagar Haveli and Daman & Diu | 36 | 39 |
| 27 | Maharashtra | 40 | 44 |
| 28 | Andhra Pradesh | 50 | 56 |
| 29 | Karnataka | 53 | 59 |
| 30 | Goa | 40 | 40 |
| 31 | Lakshadweep | 67 | 68 |
| 32 | Kerala | 67 | 69 |
| 33 | Tamil Nadu | 53 | 67 |
| 34 | Pondicherry | 53 | 67 |
| 35 | Andaman & Nicobar Islands | 74 | 74 |
| 36 | Telangana | 50 | 56 |
| 37 | Ladakh | 18 | 19 |
| 77 | Foreign Address | — (not validated) | — |
| 99 | APO Address | 90 | 99 |

---

## Appendix D — Account Types (TL.04 / TL.19)

`Use` = Current (accepted now) or Future (reserved; treat as not yet accepted unless the CIC confirms).

| Code | Use | Account Type |
|---|---|---|
| 00 | Current | Other |
| 01 | Current | Auto Loan (Personal) |
| 02 | Current | Housing Loan |
| 03 | Current | Property Loan |
| 04 | Current | Loan Against Shares / Securities |
| 05 | Current | Personal Loan |
| 06 | Current | Consumer Loan |
| 07 | Current | Gold Loan |
| 08 | Current | Education Loan |
| 09 | Current | Loan to Professional |
| 10 | Current | Credit Card |
| 11 | Current | Leasing |
| 12 | Current | Overdraft |
| 13 | Current | Two-Wheeler Loan |
| 14 | Current | Non-Funded Credit Facility |
| 15 | Current | Loan Against Bank Deposits |
| 16 | Current | Fleet Card |
| 17 | Current | Commercial Vehicle Loan |
| 18 | Future | Telco – Wireless |
| 19 | Future | Telco – Broadband |
| 20 | Future | Telco – Landline |
| 21 | Current | Seller Financing |
| 23 | Current | GECL Loan Secured |
| 24 | Current | GECL Loan Unsecured |
| 25 | Current | Working Capital Loan |
| 26 | Current | Commercial Equipment Loan |
| 27 | Current | Agriculture Loan |
| 28 | Current | Overdraft against FD |
| 29 | Current | Overdraft against Shares / Securities |
| 30 | Current | Overdraft against Collateral |
| 31 | Current | Secured Credit Card |
| 32 | Current | Used Car Loan |
| 33 | Current | Construction Equipment Loan |
| 34 | Current | Tractor Loan |
| 35 | Current | Corporate Credit Card |
| 36 | Current | Kisan Credit Card |
| 37 | Current | Loan on Credit Card |
| 38 | Current | Prime Minister Jan Dhan Yojana – Overdraft |
| 39 | Current | Mudra Loans – Shishu / Kishor / Tarun |
| 40 | Current | Microfinance – Business Loan |
| 41 | Current | Microfinance – Personal Loan |
| 42 | Current | Microfinance – Housing Loan |
| 43 | Current | Microfinance – Others |
| 44 | Current | Pradhan Mantri Awas Yojana – Credit Link Subsidy Scheme (PMAY CLSS) |
| 45 | Current | P2P Personal Loan |
| 46 | Current | P2P Auto Loan |
| 47 | Current | P2P Education Loan |
| 50 | Current | Business Loan – Secured |
| 51 | Current | Business Loan – General |
| 52 | Current | Business Loan – Priority Sector – Small Business |
| 53 | Current | Business Loan – Priority Sector – Agriculture |
| 54 | Current | Business Loan – Priority Sector – Others |
| 55 | Current | Business Non-Funded Credit Facility – General |
| 56 | Current | Business Non-Funded Credit Facility – Priority Sector – Small Business |
| 57 | Current | Business Non-Funded Credit Facility – Priority Sector – Agriculture |
| 58 | Current | Business Non-Funded Credit Facility – Priority Sector – Others |
| 59 | Current | Business Loan Against Bank Deposits |
| 61 | Current | Business Loan – Unsecured |
| 69 | Current | Short Term Personal Loan |
| 70 | Current | Priority Sector – Gold Loan |
| 71 | Current | Temporary Overdraft |

---

## Appendix E — Email Quality Checks (EC.01)

1. Exactly one `@` and at least one `.`.
2. Local part (before `@`) has ≥ 2 alphanumeric characters, not counting special characters.
3. Domain (after `@`) has ≥ 2 alphanumeric characters.
4. ≥ 2 characters between `@` and the next `.`.
5. ≥ 2 **letters** after the last `.` (e.g. `abc@gmail.com`, `kumar@yahoo.co.in`).
6. Must not end with `@` or `.`.
7. No more than two consecutive `.` after `@`. *(Ambiguous: "more than two consecutive" literally allows `..`. A stricter reading forbids any `..`. Make this configurable.)*
8. No spaces anywhere → reject.
9. Local part may contain `.`, `-`, `_` plus `[A-Za-z0-9]`.
10. Case-insensitive. Normalise to lowercase before comparing or deduplicating.

> Rule 1 in the source reads "10 email ID is allowed…". This most likely refers to the 10-segment maximum per record (§9).

---

## Appendix F — Credit Facility Status (TL.22)

| Code | Status | Meaning |
|---|---|---|
| 00 | Restructured Loan | Terms modified at the borrower's request to help avoid default. |
| 01 | Restructured Loan (Govt. Mandated) | Terms modified under a government-led relief initiative. |
| 02 | Written-off | Unpaid for an extended period; written off per CI policy. |
| 03 | Settled | Lender accepted a reduced payoff instead of the full debt. |
| 04 | Post (WO) Settled | Previously written-off debt later settled for a reduced amount. |
| 05 | Account Sold | Debt sold or transferred to a third party, which now reports it. |
| 06 | Written Off and Account Sold | Recognised as uncollectible, then transferred (e.g. to an ARC). |
| 07 | Account Purchased | Debt bought from another lender; the buyer holds collection rights. |
| 08 | Account Purchased and Written Off | Purchased account classified as a loss. |
| 09 | Account Purchased and Settled | Purchased account resolved for a reduced settlement. |
| 10 | Account Purchased and Restructured | Purchased account whose terms were restructured. |
| 11 | Restructured due to Natural Calamity | Restructured after financial difficulty caused by a natural disaster. |
| 12 | Restructured due to COVID-19 | Restructured after financial difficulty caused by COVID-19. |
| 13 | Post Write Off Closed | Written-off loan later paid in full. Lender may report `13` or `99`. |
| 14 | Restructured & Closed | Restructured loan fully repaid and closed. |
| 15 | Auctioned & Settled | Collateral auctioned; the remainder settled for less than the full amount. |
| 16 | Repossessed & Settled | Asset repossessed; the remainder settled for less than the full amount. |
| 17 | Guarantee Invoked | Guarantor now liable. Requires TL.05 = `3` (configurable Reject Record otherwise). |
| 99 | Clear existing status | Clears any prior status ("No Dues"). |

**Dependent field matrix**

| TL.22 value | TL.41 / TL.42 (Written-off) | TL.43 (Settlement) | Other |
|---|---|---|---|
| `02` | Required (at least one) | Not allowed | |
| `03`, `04` | Required (at least one) | Allowed | |
| `15`, `16` | Not allowed | Allowed | |
| `17` | Not allowed | Not allowed | TL.05 must be `3` |
| Any other | Not allowed | Not allowed | |

---

## 14. Known Source Inconsistencies (make configurable, do not hard-code)

| # | Location | Conflict | Suggested default |
|---|---|---|---|
| 1 | PA | Segment text says max **259** bytes; the field table and overview say **256**. | 256 (it matches the arithmetic). |
| 2 | TL | Segment text says max **433** bytes; the field table and overview say **564**. | 564 (it matches the arithmetic after the v3.74 change to 20-byte amounts). |
| 3 | TH | Segment text says max **117** bytes and **47** occurrences (`H01`–`H47`); the table and overview say **182** bytes, and the overview says **35** occurrences. | 182 bytes. Occurrence cap configurable (47 vs 35), bounded by the 48-month window. |
| 4 | TH.07 | Comment says max **10** bytes (9 digits + sign); the column says **20**. | 20 (v3.74 changed all amounts to 20). |
| 5 | PT | Segment text and overview say max **28** bytes; the field table totals **51**. | 51. |
| 6 | ID | Max **8** per record (and tags `I01`–`I08`), but also "extra segments after the **10th** will be rejected". | Accept up to 8. Reject the 9th onward. |
| 7 | ID tag / PT tag | ID segment tag char type `A` and PT segment tag char type `N`, although the values (`I01`, `T01`) are alphanumeric. | Treat both as A/N. |
| 8 | HD | "All fields must be provided" vs optional positions 37 and 53. | Require the positions to be present; allow space-fill. |
| 9 | TL.11 | Says both "must be on or earlier than" and "should match" HD.55. | Enforce ≤. Warn when not equal. |
| 10 | TH.04 | Failure outcome is **Reject Record** even though the field is Optional. | Follow the source; make it configurable. |
| 11 | TL.26 | When TL.15 and TL.26 are both present, TL.15 wins and TL.26 is **Reject Field**. | Follow the source. |
| 12 | Appendix E #7 | "More than two consecutive dots" wording is ambiguous. | Configurable; strict mode forbids `..`. |
| 13 | File name | Spaces around hyphens in the source template. | No spaces; confirm with the CIC. |
| 14 | Version field | HD.05 must be `12` while the document version is `3.74`. | Use `12`. |
| 15 | PT.01 comment | Garbled duplicated sentence about the truncated number `012 3`. | Intent reconstructed in §8.1. |
| 16 | Segment-tag TLV | Byte-count totals imply segment tags are encoded with tag + length like other fields (e.g. `PN03N01`). | Confirm with a CIC sample file before shipping. |

---

## 15. Suggested Implementation Outline

```
ucrf/
  constants.py      # Enums: Appendix B, C, D, F; CARD_TYPES; all field enums
  schema.py         # Declarative segment and field definitions (tag, char, len_type, max, required)
  encode.py         # TLV and fixed-width writers, padding/justification, signed numerics
  parse.py          # Tokenizer: HD → records (PN…ES) → TRLR
  validate/
    chartype.py     # A, N, D, A/N, A/N*, P checks
    dates.py        # DDMMYYYY plus §4.1 cross-field graph
    name.py         # §6.1 rules, Appendix A
    ids.py          # §7.1 per-type validators (PAN regex, Verhoeff for UID, G RAM G pattern)
    phone.py        # §8.1 edits then validation
    email.py        # Appendix E
    address.py      # §10 PIN/state cross-check
    account.py      # TL inter-field rules (§11, Appendix F matrix)
    history.py      # TH rules
  outcomes.py       # Severity ladder; configurable escalations
  config.py         # Toggles for every §14 item and every "configurable to reject record"
  report.py         # Counts: total, duplicates, accepted, rejected by level and reason
tests/
  fixtures/         # One golden record per segment; one negative case per rule
```

**Validation order (recommended):**

1. File envelope: one HD at the start, one TRLR at the end, nothing after TRLR.
2. Per record: segment order, segment cardinality, field-tag order.
3. Per field: character type, length, enum membership.
4. Per segment: intra-segment rules, applying the outcome ladder.
5. Per record: cross-segment rules (dates, PT/ID presence, TL inter-field logic).
6. Duplicate detection, then reporting.

Every rejection should carry `(record_index, segment, field_tag, rule_id, outcome)` so that downstream reports and resubmissions (Reporting Cycle `RR`) can trace each error to its rule.
