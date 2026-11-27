# Commercial Repository – Uniform Credit Reporting Format (UCRF) v3.10

> Source: *Commercial Repository – Uniform Credit Reporting Format Version 3.10, w.e.f. July 2026* (74-page PDF, CIC member guide).
> This Markdown is a faithful conversion of that PDF for use as a build spec. Sections 1–12 reproduce the source content; section 0 below is an added implementation summary and must not be read as part of the official guide. Where section 0 and the source disagree, the source wins.

---

## 0. Implementation notes (added – not part of the source document)

### 0.1 Scope warning
This is the **Commercial** UCRF. It is used to report credit facilities of commercial entities (see 8.1 Legal Constitution) to the CIC Commercial repository. It is **not** the Consumer (individual) UCRF / TUDF format, which uses different segments (e.g. PN/ID/PT/EC/PA/TL) and a different layout. Confirm with the CIC which repository the target file is for before building against this spec.

### 0.2 File structure at a glance
- Encoding: printable ASCII only; no control characters (`~`, `^` etc. cause whole-file rejection — see §6 and Appendix A).
- One segment instance per line; every line terminated by `\r\n` (CRLF), including the last (TS) line.
- Fields are pipe-delimited (`|`). Empty/null field = nothing between delimiters (`||`). Max lengths in the tables are upper bounds; do not pad.
- Dates `DDMMYYYY`; times `HHMMSS`; amounts are whole numbers (rounded, no separators, no decimals). Only CR field 11 (Current Balance / Limit Utilized / MTM) may be negative (leading `-`).
- The source example line ends with a trailing `|`; the guide does not state explicitly whether a trailing delimiter is required. Confirm with the CIC's sample file/validator.
- Fields marked *Reserved for future use* / Filler must be blank.

### 0.3 Segment order and field counts

| Seg | Name | Parent | Occurrence | Fields | May be followed by |
|---|---|---|---|---|---|
| HD | Header | – | exactly 1, first line | 7 | BS |
| BS | Borrower | HD | n per file | 27 | AS |
| AS | Address | BS | ≥1 per BS | 17 | AS / RS |
| RS | Relationship | BS | ≥1 per BS* | 39 | RS / CR |
| CR | Credit Facility | BS | ≥1 per BS | 45 | CR / GS / SS / CD / BS / TS |
| GS | Guarantor | CR | 0..n per CR | 38 | GS / SS / CD / CR / BS / TS |
| SS | Security | CR | 0..n per CR | 7 | SS / CD / CR / BS / TS |
| CD | Dishonour of Cheques | CR | 0..n per CR | 8 | CD / CR / BS / TS |
| TS | File Closure | – | exactly 1, last line | 4 | – |

\*RS is not mandatory for borrowers whose facilities are Credit Type 9026 (Factoring without Recourse – Buyer).

TS field 2 must equal the count of BS lines and TS field 3 the count of CR lines in the file. HD field 4 (creation date) must not be earlier than HD field 5 (reporting/cycle date).

### 0.4 Ambiguities / apparent errors in the source (verify with the CIC)
These are reproduced as-is in the sections below; they are flagged here so code does not silently encode a typo.
1. **GS field 37 Guarantee Invocation Date** – type Date but max length printed as **58**; a DDMMYYYY date is 8.
2. **RS field 39 Udyam Registration Number** – says "Field No. 34 – CKYC"; CKYC is RS field **24** (field 34 is Mobile Number).
3. **BS field 27 / Annexure D Part C** – spelled "CYKC"; means CKYC (15 characters per description, though max length is 30).
4. **BS field 12** – spelled "Udhyam"; means Udyam.
5. **CR field 9 Repayment Frequency** – Optional, yet failure outcome is "Reject Segment".
6. **8.5 State** – code 09 is absent from the catalogue, but notes say codes 8 **and** 9 continue to be accepted (Dadra & Nagar Haveli / Daman & Diu merged into 08).
7. **8.5 vs Annexure D Part E** – state names differ slightly (e.g. "New Delhi" vs "Delhi", "Puducherry" vs "Pondicherry"); codes match. Part E adds code 99 (APO Address) which is not in 8.5.
8. **8.20 Reason for Inward Cheque Dishonour** – only code 01 (Insufficient Funds) is listed, while §7.8 says Closed Account dishonours should also be reported.
9. **Appendix A** – `/` and `\` appear in both Allowed and Disallowed; the note clarifies they are allowed only within S/O, W/O, D/O, H/O patterns.
10. **Annexure C Part C (Name validation)** refers to an "ID type" field, which does not exist in the Commercial format (it is a consumer-format concept); apply as "at least one valid identifier reported".
11. **CR fields 28/29 (Written Off / Settled Amount)** – Required Conditionally only from 1 April 2027.
12. **Section 3 table** still uses "When Available"; v3.10 renamed this option to "Optional" everywhere.
13. Annexure numbering in the source skips 11 (goes 10 → 12 → 13); renumbered sequentially here as 10, 11, 12.
14. **8.9 DPD encoding** – `1nnn` where nnn = days past due; `1000` = 0 DPD; `1999` = 999+ DPD.

---

## 1. General Introduction
• This Guide is for Credit Information Companies (CICs) and its Members only. It must not
be modified, copied, reproduced or used in part or whole for any purpose, other than
specified.
• This Guide describes how Members should report commercial credit related information
to the CICs. This format should be used for submitting data on all credit facilities in the
names of commercial entities as Borrowers/Guarantors. Please refer Catalog 8.2 (Legal
Constitution) for list of entities allowed to be reported using the Commercial UCRF. .
• Individuals may be reported under the Commercial UCRF only when a Commercial Entity
listed under Catalog 8.2 (Legal Constitution) acts as a guarantor on their behalf.
Scenario: Mr. X avails a loan which is guaranteed by Sample Pvt Ltd. Mr. X’s credit line is
reported in the Consumer repository and the corporate guarantor needs to be reported
in the Commercial repository. Given that the Guarantee Segment is a child segment of
the Credit Facility Segment which is a child segment of the Borrower Segment, Sample
Pvt Ltd. cannot be reported in the Commercial repository unless Mr. X is also reported.
To facilitate reporting of this scenario, “Individual” as a Legal Constitution is added. Mr.
X can now be reported as a Borrower and Sample Pvt Ltd. as the Guarantor in the
Commercial repository thereby fulfilling the cross-reporting requirement.
• CICs will accept data from Members, only in the form of electronic data files hereafter
called as “Data Input Files”.
• Members should read this document carefully before using the UCRF for the extraction
and delivery of data. The designated personnel from the Systems/ Information
Technology Department of the Members must refer to this document in order to
develop their data extraction program(s) internally or while using a third party utility.
• This document assumes that the reader is familiar with the basic concepts and terms
related to credit, data and systems.

## 2. Description of the File Layout
The Data Input File has the following hierarchy:
- Field: Refers to a data element
- Segment: Refers to a collection of related fields – for e.g., Credit Facility Segment
- Structure: Refers to an instance of a Credit Facility Segment and all associated Sub-
segments (including Subject and Relationship segment(s), Security segment(s) and
Guarantor segment(s))
The Data Input File layout has the description of the segments that make up the Commercial
Uniform Credit Reporting Format and describes the fields that are contained in each
segment. The column headings are consistent throughout the document. The layout and the
meaning of column headings are described below:
- No.: Sequence number assigned to each field in the segment.
- Field Name: The name assigned to each data element requested in the Data Input File.
- Field Option:
(a) Required: Indicates that the data element for the relevant Field Name is to be
compulsorily furnished. Non-submission of a ‘Required’ data element shall render
the Data Input File or the Segment or the Structure defective. This may cause
rejection of the Data Input File or the Segment or the Structure based on the
assigned severity level.
(b) Required Conditionally: Indicates that the data element for the relevant Field
Name is to be compulsorily furnished based on some condition given in the
remarks column. For Example “Business Entity Name” in the “Relationship
Segment” is a “Required Conditional” field and is required only when the “Related
Type” field in the “Relationship Segment” is reported as “1= Business Entity
Registered in India” or “3= Business Entity Registered Outside India”. Non-
submission of a ‘Required Conditionally’ data element shall render the Data Input
File or the Segment or the Structure defective. This may cause rejection of the Data
Input File or the Segment, or the Structure based on the assigned severity level.
(c) Optional: Information in these fields is optional for submission for CIs. CIs are
encouraged to submit this information to provide more accurate and complete
response.
(d) See Description: Indicates that Member should refer to the Description column for
more instructions. Information shall be furnished in a prescribed manner if certain
conditions are met. For example: Facility / Loan Activation / Sanction Date
For all the fields, the Member should refer to the Description column for more
instructions. For self-explanatory fields, the description is left blank..
- Field Type: Identifies the data type of each field. These are:
(a) Text: fields can contain alpha, numeric, print or alphanumeric characters. (Any
printable characters can be used in this type of field i.e. ASCII).
(b) Numeric: Only whole numbers (without separators and without decimals i.e. all
decimals to be rounded to the nearest whole number) will be accepted
(c) Date: All dates should be in DDMMYYYY format. For e.g. 1st July 2007 should be
denoted as 01072007. (Dates Prior to 1st Jan 1900 & after Reporting cycle date will
lead to rejection of the field/Segment if respective date field is Mandatory). 1st Jan
1900 check is not applicable to Date of Incorporation.
(d) Time: All time should be in HHMMSS format
- Field Length: Indicates the permitted field length applicable for the value of a given
Field Name
- Description: Contains the instructions to be followed with respect to a Field Name.

## 3. Segment Description of the Commercial Uniform Credit Reporting Format
Table 1 below describes the various Segments that make up the entire Data Input File.

| Segment Identifier | Segment Name | Segment Option | Maximum Number of Entries |
|---|---|---|---|
| HD | Header | Required | 1 per File |
| BS | Borrower | Required | n per File |
| AS | Address | Required | n per Borrower |
| RS | Relationship | Required* | n per Borrower |
| CR | Credit Facility | Required | n per Borrower |
| GS | Guarantor | When Available | n per Credit Facility |
| SS | Security | When Available | n per Credit Facility |
| CD | Dishonour of Cheques | When Available | n per Credit Facility |
| TS | File Closure | Required | 1 per File |

Header Segment (HD): Is a Required Segment and contains the general information about
the Data Input File provided by the Member. It occurs only in the BEGINNING of the Data
Input File. It should appear only ONCE in the Data Input File and it will precede the other
Segments.
Borrower Segment (BS): Is a Required Segment and contains general data (identification
and location data) for each Borrower. The BS Segment can appear several times in the input
file, depending on the number of Borrowers.
Address Segment (AS): Is a Required Segment and sub segment of Borrower segment. This
segment contains information on Registered, Communication, Warehouse, and Branch
addresses related to the Borrower (BS). The AS Segment can appear several times for the
same Borrower (BS).
Relationship Segment (RS): Is a Required Segment and a sub segment of the Borrower
Segment. This Segment contains information on Proprietors, Partners, Holding Companies,
Subsidiary Companies and Associated Companies related to the Borrower. It can appear
multiple times for a Borrower Segment. Same related party should not repeat for the same
borrower in the same data input file reported by the same Member.
*Note: In lieu of the RBI Letter DOR.SIG.FIN.No./20.16.042/2024-25 dated 4th November
2024, for Borrowers reported with new Credit Type “9026 - Factoring without Recourse
(Buyer)” trades, the Relationship Segment is not mandatory, and its weightage may not be
considered for computation of DQI (Data Quality Index).
Credit Facility Segment (CR): Is a Required Segment and contains information on Credit
Facility provided to the Borrower. It should appear at least once in the Data Input File but
can appear multiple times for each Borrower in the file depending on the number of Credit
Facilities.
Guarantor Segment (GS): Is a When Available Segment and a sub segment of the Credit
Facility Segment. It contains general information of the guarantor(s), if any, for each Credit
Facility and can appear multiple times per Credit Facility.
Security Segment (SS): Is a When Available Segment and a sub segment of the Credit Facility
Segment. It contains details of tangible collateral security provided by the Subject for
availing the Credit Facility and can appear multiple times per Credit Facility.
Cheque Dishonour Segment (CD): Is a When Available Segment and a sub segment of the
Credit Facility Segment. It contains information on inward cheques dishonoured if any, by a
Subject related to a Credit Facility and can appear multiple times per Credit Facility.
File Closure Segment (TS): Is a Required Segment and contains summary of the number of
Credit Facility Segments reported in the Data Input File. It occurs only in the END of the Data
Input File. It should appear only ONCE in the Data Input File and will be the FINAL segment
in the Data Input File.
Segment hierarchy (from the diagram in the source PDF):

```
HD  Header ........................ 1 per file        (Required)
└── BS  Borrower .................. n per file        (Required)
    ├── AS  Address ............... n per Borrower    (Required)
    ├── RS  Relationship .......... n per Borrower    (Required*)
    └── CR  Credit Facility ....... n per Borrower    (Required)
        ├── GS  Guarantor ......... n per Credit Facility (When Available)
        ├── SS  Security .......... n per Credit Facility (When Available)
        └── CD  Dishonour of Cheques n per Credit Facility (When Available)
TS  File Closure (Trailer) ........ 1 per file        (Required)
```

## 4. Format Details – Uniform Credit Reporting Format

### 4.1. Method for Input File Submission
Members should submit Data Input Files only in electronic format via the SFTP channel. CICs
will not accept data submission by members in hard copies (such as printouts, faxes etc.).

Electronic: SFTP (Secured File Transfer Protocol)

### 4.2. Formatting of the Uniform Credit Reporting Format
In the UCRF, every row corresponds to a Segment and the length of each Segment/ Field can
vary. Every instance of a Segment (for e.g. the Borrower Segment details of a borrower) in
the Input File should end with a carriage return and line feed [\r\n].
Input File is to be reported in the pipe Delimited format only; delimiter being the preferable
and recommended format owing to the ease of preparation and processing files in this
format.
In the Delimited format a predefined character delimiter separates the Fields. ‘|’ (pipe) will
be used as the delimiter.
When data is not available for a Field, it should be left blank and the delimiter should occur
to indicate a null value. In such a case the Input File will contain two consecutive delimiters
as in the following example:

```
RS|999999999|2||60||MRS|ANU SACHDEVA||B606,I|||DELHI||DELHI|110048|INDIA|4143143|
```

### 4.3. File Naming Convention
The recommended file naming convention is
<Commercial> - <DL/W1/W2/W3/ME><Date reported><Any internal reference name or
number for CIs>
There will not be any file rejections on the basis of the file naming convention.
Example – Input file name may be `Commercial-W1-0901026-BP12341234`

Legends:
DL – Daily
W1 – 1st weekly incremental submission as on 9th of every month
W2 – 2nd weekly incremental submission as on 16th of every month
W3 – 3rd weekly incremental submission as on 23rd of every month
ME – Month End
DC-Data correction/Dispute
AH – Any other Adhoc Submissions
RR – Data Rejection Resubmission

## 5. Input File Submission Process
The data / information should be submitted through Secure File Transfer Protocol (SFTP) only.
Submission of data through email or Compact Disks (CDs) are not permitted and shall not be
accepted by the CICs. Only computer readable input files will be accepted. Hardcopy or
printed form data will not be accepted.
SFTP address and SFTP user ID provided at the time of Member registration to be used for
transfer of UCRF. Members are requested to refer to “Guide on Encryption Standards and
automated transfer” for the current encryption standards followed by the industry.
Please note that input files which do not conform to the above-Mentioned Guide will not be
accepted by the CICs. In case of a technical issue if the file is to be resubmitted, members are
guided to submit the data file with different file name.

## 6. Important Guidance while Creating the Data Input File
The following instructions shall be observed strictly and are crucial to properly format data.
- Data Input File should not contain Control Characters such as ~, ^ etc. as this leads to
rejection of the entire file
- Each and every segment should end with a carriage return and line feed [\r\n]
- Certain fields must have specific values cited in the Catalogues included in this
document
- All Date fields must have a valid calendar date and must be in DDMMYYYY format. For
e.g., the date 30th August, 2024 must be reported as 30082024
- All Time fields must have a valid time and must be in HHMMSS format
- Monetary amounts must be expressed in whole numbers (no decimals and without
separators). All the fields except the Account Balance / Limit Utilized /Mark to Market
field (Credit Facility Segment (CR)) must be reported as positive. The Account Balance /
Limit Utilized /Mark to Market field can have negative value, when a loan/ credit facility
is in credit balance. Negative values must be prefixed with a minus symbol at the
starting position of the field
- In case of multiple loans availed by the Borrower from the same Member, then the
information on such loans must be reported SEPARATELY as multiple Credit Facility
structures
- If in the Description column, the legend reserved for future use is displayed, then the
corresponding field must be reported blank
- Segments can appear only in the following sequence: Table 2

| Segment Identifier | Is a Sub Segment of | Immediate following Segment |
|---|---|---|
| HD | - | BS |
| BS | HD | AS |
| AS | BS | AS/RS |
| RS | BS | RS/CR |
| CR | BS | CR/ GS/ SS/ CD/ BS/ TS |
| GS | CR | GS/ SS/CD / CR/ BS/ TS |
| SS | CR | SS/ CD/ CR/ BS/ TS |
| CD | CR | CD/ CR/ BS/ TS |
| TS | - | - |

- The system will reject the field(s), segment(s) or structure(s) or the entire file in case of:
(a) If the date fields are not in DDMMYYYY format.
(b) If the Required segments are not present in the input file. For example: if there is
no Credit Facility Segment present in the Input File for a Borrower.
(c) If the Required fields do not contain data.
(d) If each and every segment in the input file does not end with carriage return and
line feed [\r\n]
(e) If the number of Borrower Segments reported in “Number of Borrower Segments”
and/or number of Credit Facility Segments reported in “Number of Credit Facility
Segments” fields in the File Closure Segment are not equal to the actual number of
Borrower Segments and/or actual number of Credit Facility Segments in the input
file.
(f) If the Date reported in Header segment’s “Date of Creation & Certification of Input
File” is earlier than the “Reporting/Cycle Date”

## 7. Commercial Credit Bureau File Format – Data Segments and Data Fields

### 7.1 Header Segment (HD)
- Is a Required segment
- Contains the general information about the Data Input File provided by the Member
Branch
- It should appear only ONCE in the Data Input File
- Occurs only in the BEGINNING of the Data Input File
- It will precede the other Segments

| No | Field Name | Field Option | Failure Outcome | Field Type | Max Length | Description |
|---|---|---|---|---|---|---|
| 1 | Segment Identifier | Required | Reject File | Text | 2 | Must contain the value HD |
| 2 | Member ID | Required | Reject File | Text | 10 | Must contain the Identification Number assigned by the CIC to the Member. If the Member ID changes and the new Member ID is being reported for the first time, complete this field with the new Member ID. |
| 3 | Previous Member ID | Required Conditionally | Reject File | Text | 10 | To be used in the immediate submission, following a merger or acquisition between 2 existing Members. Previous Member ID may have to be obtained from CIC If the field contains an invalid member ID, it will lead to entire file rejection. |
| 4 | Date of Creation & Certification of Input File | Required | Reject File | Date | 8 | Date when the Data Input File was created/ extracted/ generated & certified from the Member Branch’s system. Must be valid calendar date and in DDMMYYYY format. |
| 5 | Reporting / Cycle Date | Required | Reject File | Date | 8 | Date up to which information is reported. This is not the date on which the Data Input File was created nor the date of extraction. Must be a valid calendar date and in DDMMYYYY format. E.g. ABC Bank reports data as on March 31, 2006, extracts the Data Input File on April 05, 2006 and submits the data to TransUnion CIBIL on April 12, 2006. In this case, the reporting date will be 31032006. |
| 6 | Reporting Cycle | Optional | Reject Field if the value is not as per the prescribed legends | Text | 2 | DL- Daily W1, W2, W3 - Weekly ME- Month end DC-Data correction/Dispute AH – Any other Adhoc Submissions RR – Data Rejection Resubmission No Cross Validation with any other field will be performed in case Reporting cycle is reported by Members |
| 7 | Filler | Optional | - | Text | 225 | Reserved for future use. This field is to be left blank |

### 7.2 Borrower Segment (BS)
- Is a Required segment
- Contains information on the Borrower
- It should appear only once per each borrower reported
- It can appear multiple times per the Data Input File without repeating the same Borrower
per Branch ID

| No | Field Name | Field Option | Failure Outcome | Field Type | Max Length | Description |
|---|---|---|---|---|---|---|
| 1 | Segment Identifier | Required | Reject record | Text | 2 | It must be BS. |
| 2 | Member Branch Code | Optional | Reject Field | Text | 15 | Identification code of the member’s branch where the credit facility has been granted. If the Member Branch code changes and the new Member Branch code is being reported for the first time, complete this field with the new Member Branch code. Provide the old Member Branch code in the Previous Member Branch code. |
| 3 | Previous Member Branch Code | Optional | Reject Field | Text | 15 | Must contain the previous Identification code of the member’s branch where the credit facility had been granted. The Previous Identification Number should be provided in the immediate submission following a change in Member Branch Code due to merger or acquisition or any other reason. Hence, post a merger or acquisition or any such reason prompting a change in the Member Branch Code, the Previous Member Branch Code becomes a Required field in the submission immediately following such a change. |
| 4 | Borrower’s Name | Required | Reject record | Text | 125 | Legal Business Name of Borrower. In case of Commercial Paper “Name of the CP Issuer” Note- The Commercial Entity Name must contain a minimum of 2 characters, which must be alphanumeric. The 2 characters may be: • Both alphabets (e.g., AB) • Alphabet + numeric (e.g., A1) • Numeric + numeric (e.g., 12) Names consisting of only special characters are not permitted. Failure to meet the minimum condition will result in record rejection. Please Refer “Annexure C” Part C for Name Validation for Individuals Reported with Legal Constitution “90” |
| 5 | Borrower Short Name | Optional | Reject Field | Text | 20 | Borrower ‘s Short Name Note- Not to be submitted for Individuals Reported with Legal Constitution “90” |
| 6 | Company Registration Number | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Company Registration Number issued by Registrar of Companies. Required if none of the other IDs mentioned in Field No. 8 – PAN, Field No. 9 – CIN, Field No. 10 – TIN, Field No. 11 – Service Tax, Field No. 12 – Udyam Registration Number, or Field No. 27 – CKYC is provided. |
| 7 | Date of Incorporation | Optional | Reject Field | Date | 8 | The date on which the Business is Registered / Incorporated |
| 8 | PAN | Required Conditionally | Reject Record if no identifier provided | Text | 15 | Permanent Account Number issued by Income Tax Authority Required if none of the other IDs mentioned in Field No. 6 – Company Registration Number, Field No. 9 – CIN, Field No. 10 – TIN, Field No. 11 – Service Tax, Field No. 12 – Udyam Registration Number, or Field No. 27 – CKYC is provided. Or If Legal Constitution (Field, no 13) reported 90 “Individual” and other ID in Field 27 (CKYC) is not provided |
| 9 | CIN | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Corporate Identification Number issued by Ministry of Corporate Affairs (MCA). CIN is applicable for Legal Constitution 11 or 12. Required if none of the other IDs mentioned in Field No. 6 – Company Registration Number, Field No. 8 – PAN, Field No. 10 – TIN, Field No. 11 – Service Tax, Field No. 12 – Udyam Registration Number, or Field No. 27 – CKYC is provided. |
| 10 | TIN | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Tax Identification Number for Non-Individuals issued by Income Tax Authority Required if none of the other IDs mentioned in Field No. 6 – Company Registration Number, Field No. 8 – PAN, Field No. 9 – CIN, Field No. 11 – Service Tax , Field No. 12 – Udyam Registration Number, or Field No. 27 – CKYC is provided. |
| 11 | Service Tax # | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Service Tax Registration Number issued by Central Excise Authority |
| 12 | Udhyam Registration Number | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Udyam Registration Number issued by Ministry of MSME Required if none of the other IDs mentioned in Field No. 6 – Company Registration Number, Field No. 8 – PAN, Field No. 9 – CIN, Field No. 10 – TIN, Field No. 11 – Service Tax , or Field No. 27 – CKYC is provided. |
| 13 | Borrower’s Legal Constitution | Required | Reject record | Text | 2 | Refer to Legal Constitution catalogue for the list of valid values. Blank or wrong value will lead to borrower record rejection. |
| 14 | Business Category | Required | Reject record | Text | 2 | Refer to Business Category catalogue for a list of valid values. Blank or wrong value will lead to borrower record rejection. |
| 15 | Business/ Industry Type | Required | Reject record | Text | 2 | Refer to Business / Industry Type catalogue for a list of valid values. Blank or wrong value will lead to borrower record rejection. |
| 16 | Class of Activity 1 | Required | Reject record | Text | 5 | Classification of Activity/ Occupation as per Reserve Bank of India, Handbook of Instructions, Basic Statistical Returns 1 and 2, Latest Edition. Blank or wrong value will lead to borrower record rejection. |
| 17 | Class of Activity 2 | Optional | Reject Field | Text | 5 | Classification of Activity/Occupation as per Reserve Bank of India, Handbook of Instructions, Basic Statistical Returns 1 and 2, Latest Edition |
| 18 | Class of Activity 3 | Optional | Reject Field | Text | 5 | Classification of Activity/Occupation as per Reserve Bank of India, Handbook of Instructions, Basic Statistical Returns 1 and 2, Latest Edition |
| 19 | HSN | Optional | Reject Field | Text | 20 | 6 -Digit Uniform HSN (Harmonized System of Nomenclature) code issued by GST Network. |
| 20 | Sales Figure | Optional | Reject Field | Numeric | 20 | Sales Figure as of Previous Financial year (year before the current running financial year) is to be reported. |
| 21 | Financial Year | Optional | Reject Field | Date | 8 | Financial year for which Sales information is provided |
| 22 | Number of Employees | Optional | Reject Field | Numeric | 8 | This includes the count of permanent employees |
| 23 | Credit Rating | Optional | Reject Field | Text | 10 | Credit Rating issued by recognized Credit Rating Agencies or Authorities. |
| 24 | Assessment Agency / Authority | Required Conditionally | Reject Field | Text | 2 | Report this field when field no. 23 Credit Rating is reported. Refer to 8.19 Assessment Agency/Authority for a valid list of catalogue values. |
| 25 | Credit Rating As On | Required Conditionally | Reject Field | Date | 8 | Date Credit Rating was issued to be provided when field no. 23 Credit Rating is reported |
| 26 | Credit Rating Expiry Date | Required Conditionally | Reject Field | Date | 8 | Date until which the Credit Rating is valid to be provided when field no. 23 Credit Rating is reported |
| 27 | CYKC | Required Conditionally | Reject Record if no identifier provided | Text | 30 | 15 Character Central KYC (cKYC) managed by Central Registry of Securitization and Asset Reconstruction and Security Interest in India (CERSAI) Required if none of the other IDs mentioned in Field No. 6 – Company Registration Number, Field No. 8 – PAN, Field No. 9 – CIN, Field No. 10 – TIN, Field No. 11 – Service Tax , or Field No. 12 – Udyam Registration Number is provided. |

*Please refer Annexure D- PART C for Identifier Validation
Note: If no identifier is reported for a borrower, then it will lead to that borrower record rejection.

### 7.3 Address Segment (AS)
- Is a Required Segment
- This is a sub segment of the Borrower Segment
- It should appear at least once per Borrower
- It can appear multiple times per Borrower

| No | Field Name | Field Option | Failure Outcome | Field Type | Max Length | Description |
|---|---|---|---|---|---|---|
| 1 | Segment Identifier | Required | Reject Segment | Text | 2 | It must be AS. |
| 2 | Borrower Office Location Type | Required | Reject Segment | Text | 2 | Refer to Location Type catalogue for a list of valid values. Registered Address is Mandatory. |
| 3 | Filler | Optional | Reject Field | Text | 13 | Reserved for future use. This field is to be left blank. |
| 4 | Address Line 1 | Required Conditionally | Reject Field/Segment | Text | 200 | The address is to be parsed logically under the fields Address Line1, Address Line2, Address Line3, City/Town, District, State/ Union Territory. If this is not possible, the entire address can appear in either of Address Line1, Address Line 2, Address Line 3. Do not truncate. Use comma separated values. Do not split whole words. If insufficient, please use Address Line 2 and Address Line 3 Note- Concatenated length of Address Line 1, Address Line 2 and Address Line 3 has to be at least 5 characters. Per field max length is 200 characters. After Concatenation address field (Line 1, Line 2, or Line 3) that contains only NULL, only “NA,” any combination or repetition of these values, solely numeric characters, or only special characters will result in rejection of the entire Address segment. Address Line 1, 2, 3 are all mentioned as ‘Required Conditionally’ because at least one of these need to be reported. |
| 5 | Address Line 2 | Required Conditionally | Reject Field/Segment | Text | 200 |  |
| 6 | Address Line 3 | Required Conditionally | Reject Field/Segment | Text | 200 |  |
| 7 | City/Town | Optional | Reject Field | Text | 40 | City name to be provided. There is no check for city name validation done. |
| 8 | District | Optional | Reject Field | Text | 40 | District name to be provided. There is no check for district name validation done. |
| 9 | State/Union Territory | Required Conditionally | Reject Field/Segment | Text | 2 | To be Reported If country Field ID # 11 = India “77” to be reported for foreign address. Refer to State catalogue for a list of valid values.In the State Code master, Daman and Diu is combined with Dadra and Nagar Haveli. Both the state codes of 8 & 9 will continue to be accepted |
| 10 | Pin Code | Required Conditionally | Reject Field/Segment | Text | 6 | To be Reported If country Field ID # 11 = India State – PIN Code validation will be applicable. For e.g. Maharashtra State cannot be reported with PIN code starting with ‘38’. For State Code 77, PINCode value validation will be only on the max character length |
| 11 | Country | Required | Reject Segment | Text | 3 | If 079 India is reported then State & PIN code fields to be reported mandatorily. Refer to Country Code/ Nationality catalogue for a list of valid values. |
| 12 | Mobile Number(s) | Optional | Reject Field | Text | 55 | Use comma separators for multiple numbers. If country Field ID # 11 = India then mobile no. to be of 10 digit. Mobile Number Validations are Mentioned in Annexure C (Part B) |
| 13 | Telephone Area Code | Optional | Reject Field | Text | 6 |  |
| 14 | Telephone Number(s) | Optional | Reject Field | Text | 55 | Use comma separators for multiple numbers. Telephone Number Validations are Mentioned in Annexure C (Part B) |
| 15 | Filler | Optional | Reject Field | Text | 6 | Reserved for future use. This field is to be left blank. |
| 16 | Email ID | Optional | Reject Field | Text | 55 | Email ID reported with Location Type 01 Registered Office will be considered for data ingestion. Email ID validations are mentioned in Annexure C (Part A). Invalid email ID as per the validations will lead to field level reject. |
| 17 | Filler | Optional | Reject Field | Text | 31 | Reserved for future use. This field is to be left blank. |

Note: Reject Field/Segment means if there is only one record in that segment and the
validation fails only then it will lead to Segment reject.

### 7.4 Relationship Segment (RS)
- Is a Mandatory Segment
- This is a sub segment of the Borrower Segment
- Contains information on Directors, Share Holders, Proprietors, Partners, Trustees,
Holding Companies, Subsidiary Companies and Associated Companies related to the
Subject
- It can appear multiple times for a Borrower
- In lieu of the RBI letter DOR.SIG.FIN.No./20.16.042/2024-25 dated 4th November 2024,
for Borrowers reported with new Credit Type “9026 - Factoring without Recourse (Buyer)”
trades, the Relationship Segment is not mandatory, and its weightage may not be
considered for computation of DQI (Data Quality Index).

| No | Field Name | Field Option | Failure Outcome | Field Type | Max Length | Description |
|---|---|---|---|---|---|---|
| 1 | Segment Identifier | Required | Reject Segment | Text | 2 | It must be RS. |
| 2 | Filler | Optional | Reject Field | Text | 13 | Reserved for future use. This field is to be left blank. |
| 3 | Related Type | Required | Reject Segment | Text | 1 | 1= Business Entity Registered in India 2=Resident Indian Individual 3= Business Entity Registered Outside India 4= Foreign/Non-Resident Indian Individual |
| 4 | Relationship | Required | Reject Segment | Text | 2 | Refer Type of Relationship catalogue for a list of valid values. Refer.Annexure D – Relationship Type Validations |
| 5 | Business Entity Name | Required Conditionally | Reject Segment | Text | 125 | Required when Field No. 3 – Related Type is 1 or 3. |
| 6 | Business Category | Required Conditionally | Reject Segment | Text | 2 | Required when Field No. 3 – Related Type is 1 or 3. Refer to Business Category catalogue for a list of valid values. |
| 7 | Business / Industry Type | Required Conditionally | Reject Segment | Text | 2 | Required when Field No. 3 – Related Type is 1 or 3. Refer to Business / Industry Type catalogue for a list of valid values. |
| 8 | Individual Name Prefix | Optional | Reject Field | Text | 15 | Mr, Ms, Mrs, Dr, etc., Report only when Field No. 3 – Related Type is 2 or 4. |
| 9 | Full Name | Required Conditionally | Reject Segment | Text | 150 | Required when Field No. 3 – Related Type is 2 or 4. Please Refer “Annexure C” Part C for Name Validation for Individuals Reported as Relationship |
| 10 | Gender | Required Conditionally | Reject Segment | Text | 2 | Required when Field No. 3 – Related Type is 2 or 4. 01 Male; 02 Female; 03 Transgender |
| 11 | Company Registration Number | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Report only when RS Field ID # 3 = 1 or 3. Company Registration Number issued by Registrar of Companies for business entity registered in India. Required if none of the other identifiers in Field No. 20 – CIN, Field No. 22 – TIN, Field No. 23 – Service Tax # Field No. 24 – CKYC, or Field No. 39 – Udyam Registration Number is provided. |
| 12 | Date of Incorporation | Optional | Reject Field | Date | 8 | Report only when Field No. 3 – Related Type is 1 or 3. The date on which the Business is Registered / Incorporated |
| 13 | Date of Birth | Required Conditionally | Reject Record | Date | 8 | Report when Field No. 3 – Related Type is 2 or 4. |
| 14 | PAN | Required Conditionally | Reject Record if no identifier provided | Text | 10 | Permanent Account Number issued by Income Tax Authority RS Field ID #3 Related Type = 1 (Business Entity Registered in India) OR RS Field ID #3 Related Type 2 (Resident Indian Individual) and none of the identifiers in Field No. 15 – Voter ID, Field No. 16 – Passport Number, Field No. 17 – Driving Licence ID, Field No. 19 – Ration Card No, or Field No. 24 – CKYC are provided. |
| 15 | Voter ID | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Issued by Election Commission of India Required if RS Field ID #3 Related Type 2 (Resident Indian Individual) and none of the identifiers in Field No. 14 – PAN, Field No. 16 – Passport Number, Field No. 17 – Driving Licence ID, Field No. 19 – Ration Card No, or Field No. 24 – CKYC are provided. |
| 16 | Passport Number | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Required when Field No. 3 – Related Type = 2 or 4 AND none of Field No. 14 – PAN, Field No. 15 – Voter ID, Field No. 17 – Driving Licence ID, Field No. 19 – Ration Card No, or Field No. 24 – CKYC are provided. |
| 17 | Driving Licence ID | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Required when Field No. 3 – Related Type = 2 or 4 AND none of Field No. 14 – PAN, Field No. 15 – Voter ID, Field No. 16 – Passport Number, Field No. 19 – Ration Card No, or Field No. 24 – CKYC are provided. |
| 18 | UID | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Issued by Government of India Required if RS Field ID #3 Related Type 2 (Resident Indian Individual) and none of Field No. 14 – PAN, Field No. 15 – Voter ID, Field No. 16 – Passport Number, Field No. 17 – Driving Licence ID, Field No. 19 – Ration Card No, or Field No. 24 – CKYC are provided. |
| 19 | Ration Card No | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Required if RS Field ID #3 Related Type 2 (Resident Indian Individual) and none of Field No. 14 – PAN, Field No. 15 – Voter ID, Field No. 16 – Passport Number, Field No. 17 – Driving Licence ID, or Field No. 24 – CKYC are provided. |
| 20 | CIN | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Corporate Identification Number applicable for RS Field ID #3 Related Type 1 = Business Entity Registered in India. Required if none of the identifiers in Field No. 11 – Company Registration Number, Field No. 22 – TIN, Field No. 23 – Service Tax # Field No. 24 – CKYC, or Field No. 39 – Udyam Registration Number is provided. |
| 21 | DIN | Required Conditionally | Reject Record if no identifier provided | Text | 12 | Director Identification Number issued by Ministry of Company Affairs. Only when RS Field ID # 3 = 2 or 4 and RS Field ID # 4 – Relationship is any of 51, 52, 53, 54, or 56. Director Identification Number issued by MCA. |
| 22 | TIN | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Tax Identification Number for Non-Individuals issued by Income Tax Authority. Required if none of the identifiers in Field No. 11 – Company Registration Number, Field No. 20 – CIN, Field No. 23 – Service Tax # Field No. 24 – CKYC, or Field No. 39 – Udyam Registration Number is provided. |
| 23 | Service Tax # | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Service Tax Registration Number issued by Central Excise Authority |
| 24 | CKYC | Required Conditionally | Reject Record if no identifier provided | Text | 30 | 15 Character Central KYC (CKYC) managed by Central Registry of Securitization and Asset Reconstruction and Security Interest in India (CERSAI). Applicable for RS Field ID #3 Related Type 1 & 2. Required if none of the identifiers in Field No. 11 – Company Registration Number, Field No. 20 – CIN, Field No.22-TIN Field No. 23 – – Service Tax #, or Field No. 39 – Udyam Registration Number is provided. |
| 25 | Percentage of Control | Optional | Reject Field | Text | 6 | Percentage of shares owned by Borrower in Related Business Entity or Percentage of shares owned by Related Person/ Business Entity in the Borrower, as the case may be. Example: submit 9999 for 99.99% or 10000 for 100.00% |
| 26 | Address Line 1 | Required Conditionally | Reject Field/Segment | Text | 200 | The address is to be parsed logically under the fields Address Line1, Address Line2, Address Line3, City/Town, District, State/ Union Territory. If this is not possible, the entire address Can appear in Address Line1,Address Line 2 or Address Line 3. In such case report City/Town Name appropriately, without truncating or splitting into the next field. Do not truncate. Use comma separated values. Do not split whole words. If insufficient please use Address Line2 and Address Line 3 Note- Concatenated length of Address Line 1, Address Line 2 and Address Line 3 has to be at least 5 characters. Per field max length will be 200 characters. After Concatenation address field (Line 1, Line 2, or Line 3) that contains only NULL, only “NA,” any combination or repetition of these values, solely numeric characters, or only special characters will result in rejection of the entire Address segment. Address Line 1, 2, 3 are all mentioned as ‘Required Conditionally’ because at least one of these need to be reported. |
| 27 | Address Line 2 | Required Conditionally | Reject Field/Segment | Text | 200 |  |
| 28 | Address Line 3 | Required Conditionally | Reject Field/Segment | Text | 200 |  |
| 29 | City/Town | Optional | Reject Field | Text | 40 | City name to be provided. There is no check for city name validation done. |
| 30 | District | Optional | Reject Field | Text | 40 | District name to be provided. There is no check for district name validation done. |
| 31 | State/Union Territory | Required Conditionally | Reject Field/ Segment | Text | 2 | To be reported if country is = India. Report for Related Type 1= Business Entity Registered in India, 2=Resident Indian Individual “77” to be reported for foreign address. Refer to State catalogue In the State Code master, Daman and Diu is combined with Dadra and Nagar Haveli. Both the state codes of 8 & 9 will continue to be accepted |
| 32 | Pin Code | Required Conditionally | Reject Field/ Segment | Text | 6 | To be reported if country is = India. Report for Related Type 1= Business Entity Registered in India, 2=Resident Indian Individual State – PIN Code validation will be applicable. For e.g. Maharashtra State cannot be reported with PIN code starting with ‘38’. For State Code 77, PINCode value validation will be only on the max character length |
| 33 | Country | Required | Reject Segment | Text | 3 | Refer to Country Code/ Nationality for the list of valid values. |
| 34 | Mobile Number(s) | Optional | Reject Field | Text | 55 | Use comma separators for multiple numbers If country Field ID # 33 =India ,then mobile no. to be 10 digit. Mobile Number Validations are Mentioned in Annexure C (Part B) |
| 35 | Telephone Number(s) | Optional | Reject Field | Text | 55 | Use comma separators for multiple numbers. Telephone Number Validations are Mentioned in Annexure C (Part B) |
| 36 | Telephone Area Code | Optional | Reject Field | Text | 6 |  |
| 37 | Fax Number(s) | Optional | Reject Field | Text | 55 | Use comma separators for multiple numbers |
| 38 | Fax Area Code | Optional | Reject Field | Text | 6 |  |
| 39 | Udyam Registration Number | Required Conditionally | Reject Record if no identifier provided | Text | 30 | Udyam Registration Number issued by Ministry of MSME. Report if RS field no.3 is Related Type 2 (Resident Indian Individual) or Related Type = 1 (Business Entity Registered in India). Required if none of the identifiers in Field No. 11 – Company Registration Number, Field No. 20 – CIN, Field No.22-TIN Field No. 23 – – Service Tax #, or Field No. 34 – CKYC is provided. |

*Please refer Annexure D- PART C for Identifier Validation
Note: Reject Field/Segment means
if there is only one record in that segment and the validation fails only then it will lead to
Segment reject.

### 7.5 Credit Facility Segment (CR)
- Is a Required Segment
- This is a sub segment of the Borrower Segment
- It must appear at least once per Borrowers
- It can appear multiple times per Borrower
- In lieu of the RBI Circular RBI/2024-25/81 DoR.FIN.REC.47/20.16.042/2024-25 dated 10th
October 2024, all CIs whose licence or CoR has been cancelled by the RBI shall continue
to report credit information of the borrowers onboarded and reported to CICs prior to
cancellation of their licence or CoR to all the four CICs till the loan lifecycle is completed
or the credit institution is wound up, whichever is earlier.
- If a suppressed account is submitted by the Credit Institution in the UCRF format, the CIC
will accept and update the information; however, the updated details will be available for
rendering only after the account is unsuppressed.

| No | Field Name | Field Option | Failure Outcome | Field Type | Max Length | Description |
|---|---|---|---|---|---|---|
| 1 | Segment Identifier | Required | Reject Segment | Text | 2 | It must be CR |
| 2 | Account Number | Required | Reject Segment | Text | 25 | Account Number assigned by the Member to the Borrower. For account type credit card or similar credit types, CIs shall submit account number which is different from credit card number. |
| 3 | Previous Account Number | Optional | Reject Field | Text | 25 | Previous Credit Facility Number where Credit Facility Number is reassigned/changed |
| 4 | Facility / Loan Activation / Sanction Date | Required | Reject Segment | Date | 8 | Date on which the Credit Facility was first activated by the Member. In case of Renewal, report the renewal date in CF Field ID # 14 In case of Commercial Paper the ‘Issue Date’ is to be given. For CIs whose licence or CoR has been cancelled by the RBI, sanctioned date reported for a Credit Facility must be older than licence or CoR cancelled date of the CI, else it will lead to rejection. Note- No Validation between Sanction Date and Date of Incorporation is done. |
| 5 | Sanctioned Amount/ Notional Amount of Contract | Required | Reject Segment | Numeric | 20 | Sanctioned/ Approved amount of loan/credit as at the end of reporting period. In case of Derivative, the Notional Amount of the contract has to be given. In case of Commercial Paper, the amount of CP to be given. Note : This amount must be positive and can be specified in INR equivalent or in the base currency of the loan provided. • |
| 6 | Currency Code | Required | Reject Segment | Text | 3 | Refer to “Currency Code” catalogue for a list of valid values. Note: The Currency mentioned in this field is linked only to the Sanctioned Amount / Notional Amount of Contract |
| 7 | Credit Type | Required | Reject Segment | Text | 4 | Refer to Credit Type catalogue master. |
| 8 | Tenure / Weighted Average maturity period of Contracts | Optional | Reject Field | Numeric | 3 | Number of months the Credit facility is extended / Weighted Average maturity period of Contracts for Derivatives only. If the number of Months exceeds 999, report 999. This field is to report the original tenure and not the residual tenure. For e.g. Sanctioned date is 2022 and the loan is for a period of 5 years then the tenure will be 60 months. Every month the tenure will not be reported with the revised residual months. |
| 9 | Repayment Frequency | Optional | Reject Segment | Text | 2 | Refer to Repayment Frequency for the list of valid values |
| 10 | Drawing Power | Required Conditionally | Reject Field/Segment | Numeric | 20 | Drawing Power as computed by the Member This field is to be reported only for Revolving Trade facilities where Credit Type is one of the following permitted codes: 0100, 0200, 3100, 9024 |
| 11 | Current Balance / Limit Utilized /Mark to Market | Required | Reject Segment | Numeric | 20 | In case of credit balance, please prefix negative sign. In case of Derivative, Mark to Market has to be reported. Note: This field must have the current balance in INR - Indian Rupees. If the Credit Facility is sanctioned in a foreign currency, the equivalent amount in INR should be reported in this field |
| 12 | Notional Amount of Out-standing Restructured Contracts | Optional | Reject Field | Numeric | 20 | Report Notional Amount of Out-standing Contracts which has been restructured. This field is applicable for Derivatives only. Note: This amount must be positive and must contain the notional amount in INR - Indian Rupees. If the Credit Facility is sanctioned in a foreign currency, the equivalent amount in INR should be reported in this field |
| 13 | Loan or Limit Expiry / Maturity Date | Optional | Reject Field | Date | 8 | Expected date of Maturity of the loan. Should be reported for all the facilities except revolving facilities without expiry date. In case of Commercial Papers, the “Maturity Date: is to be given. |
| 14 | Loan Renewal Date | Optional | Reject Field | Date | 8 | Last Renewed/expected renewal date . This field is to be reported only for Revolving Trade facilities where Credit Type is one of the following permitted codes: 0100, 0200, 3100, 9024 Note- Ensure Loan renewal date is later than Facility / Loan Activation / Sanction Date Field ID # 4 |
| 15 | Asset Classification/Days Past Due (DPD) | Required | Reject Segment | Text | 4 | Refer to the Asset Classification Catalogue master for valid catalogue values. |
| 16 | Asset Classification Date | Optional | Reject Field | Date | 8 | Should be the first effective date when the account moved to other than “Standard” for the first time. |
| 17 | Amount Overdue / Limit Overdue | Required | Reject Segment | Numeric | 20 | The amount that has been overdue in the account. If no overdue amount then "0" needs to be populated Note: This amount must be positive and must contain the amount overdue in INR - Indian Rupees. If the Credit Facility is sanctioned in a foreign currency, the equivalent amount in INR should be reported in this field In case of Commercial Papers, “Amount of Default” to be given. |
| 18 | Overdue Bucket 01 ( 1 – 30 days) | Optional | Reject Field | Numeric | 20 | Report the Amount Overdue between 1 – 30 days Note: This amount must be positive and must contain the amount overdue in INR - Indian Rupees. If the Credit Facility is sanctioned in a foreign currency, the equivalent amount in INR should be reported in this field |
| 19 | Overdue Bucket 02 ( 31 – 60 days) | Optional | Reject Field | Numeric | 20 | Report the Amount Overdue between 31 – 60 days Note: This amount must be positive and must contain the amount overdue in INR - Indian Rupees. If the Credit Facility is sanctioned in a foreign currency, the equivalent amount in INR should be reported in this field |
| 20 | Overdue Bucket 03 ( 61 – 90 days) | Optional | Reject Field | Numeric | 20 | Report the Amount Overdue between 61 – 90 days Note: This amount must be positive and must contain the amount overdue in INR - Indian Rupees. If the Credit Facility is sanctioned in a foreign currency, the equivalent amount in INR should be reported in this field |
| 21 | Overdue Bucket 04 (91 – 180 days) | Optional | Reject Field | Numeric | 20 | Report the Amount Overdue between 91 – 180 days Note: This amount must be positive and must contain the amount overdue in INR - Indian Rupees. If the Credit Facility is sanctioned in a foreign currency, the equivalent amount in INR should be reported in this field |
| 22 | Overdue Bucket 05 (Above 180 days) | Optional | Reject Field | Numeric | 20 | Report the Amount Overdue for more than 180 days Note: This amount must be positive and must contain the amount overdue in INR - Indian Rupees. If the Credit Facility is sanctioned in a foreign currency, the equivalent amount in INR should be reported in this field |
| 23 | High Credit | Optional | Reject Field | Numeric | 20 | Highest amount of credit used by borrower during the reported period This field is to be reported only for Revolving Trade facilities where Credit Type is one of the following permitted codes: 0100, 0200, 3100, 9024 Note: This amount must be positive and must contain the Highest credit in INR - Indian Rupees. If the Credit Facility is sanctioned in a foreign currency, the equivalent amount in INR should be reported in this field |
| 24 | Installment Amount | Optional | Reject Field | Numeric | 20 | Amount paid by the Borrower to the Member for repayment of the Credit Facility Note: This amount must be positive and must contain the Installment amount in INR - Indian Rupees. If the Credit Facility is sanctioned in a foreign currency, the equivalent amount in INR should be reported in this field |
| 25 | Last Repaid Amount | Optional | Reject Field | Numeric | 20 | The latest amount paid by the borrower Note: This amount must be positive and must contain the last repaid amount in INR - Indian Rupees. If the Credit Facility is sanctioned in a foreign currency, the equivalent amount in INR should be reported in this field |
| 26 | Account Status | Required | Reject Segment | Text | 2 | Refer to Account Status catalogue for a list of valid values. |
| 27 | Account Status Date | Optional | Reject Field | Date | 8 | The date on which the account status is effective from. For Account Status 02 - This is the date the member or borrower closed the account/Credit Limit (As applicable). |
| 28 | Written Off Amount | Required Conditionally | Reject Field | Numeric | 20 | If Account Status Field ID #26 = 05 or 06 Amount written off form the overall liability Note-Field to be treated as Required Conditional from 1st April 2027. Note: This amount must be positive and must contain the Written off amount in INR - Indian Rupees. If the Credit Facility is sanctioned in a foreign currency, the equivalent amount in INR should be reported in this field |
| 29 | Settled Amount | Required Conditionally | Reject Field | Numeric | 20 | If Account Status Field ID #26 = 03 or 06 Amount paid in full settlement of overdue loans Note-Field to be treated as Required Conditional from 1st April 2027. Note: This amount must be positive and must contain the Settled amount in INR - Indian Rupees. If the Credit Facility is sanctioned in a foreign currency, the equivalent amount in INR should be reported in this field |
| 30 | Major reasons for Restructuring | Optional | Reject Field | Text | 2 | 01= Restructured due to Non-Performance 02= Restructured due to Natural Calamity 03= Restructured due to COVID-19 99= Others |
| 31 | Amount of Contracts Classified as NPA | Optional | Reject Field | Numeric | 20 | Applicable for Derivatives only Note: This amount must be positive and must contain the amount in INR - Indian Rupees. If the Credit Facility is sanctioned in a foreign currency, the equivalent amount in INR should be reported in this field |
| 32 | Asset based Security coverage | Optional | Reject Field | Text | 2 | Refer to Tangible Security Coverage catalogue for a list of valid values. |
| 33 | Guarantee Coverage | Optional | Reject Field | Text | 2 | Refer to Guarantee Coverage catalogue for a list of valid values. |
| 34 | Bank Remark Code | Optional | Reject Field | Text | 100 | Free text for banks to provide any comments Report “JOINT” in case of Joint Loans against both the borrower & the Co- borrower(s). |
| 35 | Wilful Default Status | Required | Reject Segment | Numeric | 1 | 0 = Not Wilful Defaulter; 1 = Wilful Defaulter |
| 36 | Date Classified as Wilful Default | Required Conditionally | Reject Field/Segment | Date | 8 | Date on which the Member classified the Account as being in wilful default. This field must be reported when Wilful Default Status is reported as “1- Wilful Defaulter” |
| 37 | Suit Filed Status | Optional | Reject Field | Text | 2 | Refer to Suit Filed Status catalogue for a list of valid values. |
| 38 | Suit Reference Number | Required Conditional | Reject Field | Text | 25 | Internal reference number to the suit filed. This field must be reported when there is a Suit Filed (Suit Filed status other than 00 - Not a Suit Filed Case) |
| 39 | Suit Amount in Rupees | Required Conditional | Reject Field | Numeric | 20 | The total amount for which the suit has been filed. This field must be reported when there is a Suit Filed (Suit Filed status other than 00 - Not a Suit Filed Case) Note: This amount must be positive and must contain the Suit amount in INR - Indian Rupees. |
| 40 | Date of Suit | Required Conditional | Reject Field | Date | 8 | Date when suit was admitted by Authority This field must be reported when there is a Suit Filed (Suit Filed status other than 00 - Not a Suit Filed Case) |
| 41 | Dispute ID No. | Optional | Reject Field | Text | 20 | This field should be filled only when the Dispute is raised and is not applicable for regular submission. The Dispute ID would be provided by CICs upon registering a dispute. |
| 42 | Transaction Type Code | Optional | Reject Field | Text | 2 | Refer to Transaction Type Code catalogue for a list of valid values. This field should by default have the value “01 – Normal Submission” |
| 43 | OTHER_BK | Optional | Reject Field | Text | 42 | Name of other Banks/ FIs. The names of other Banks/FIs from which the party has availed credit facility should be indicated. Multiple Banks/FIs may be provided separated by commas and in abbreviated form. |
| 44 | UFCE (Amount) | Optional | Reject Field | Numeric | 20 | Unhedged Foreign Currency Exposure |
| 45 | UFCE Date | Optional | Reject Field | Date | 8 | Unhedged Foreign Currency Exposure Date |

Note: Reject Field/Segment means if there is only one record in that segment and the
validation fails only then it will lead to Segment reject.

### 7.6 Guarantor Segment (GS)
- Is a When Available Segment This is a sub segment of the Credit Facility Segment
- Contains general information of the guarantor(s), if any, for each Credit Facility
- It can appear multiple times per Credit Facility

| No | Field Name | Field Option | Failure Outcome | Field Type | Max Length | Description |
|---|---|---|---|---|---|---|
| 1 | Segment Identifier | Required | Reject Segment | Text | 2 | It must be GS. |
| 2 | Filler | See Description | Reject Field | Text | 13 |  |
| 3 | Guarantor Type | Required | Reject Segment | Text | 1 | 1= Business Entity Registered in India 2=Resident Indian Individual 3= Business Entity Registered Outside India 4= Foreign/ Non-Resident Indian Individual |
| 4 | Business Category | Optional | Reject Field | Text | 2 | Required if GS Field ID # 3– Guarantor Type =1 or 3. Refer to Business Category catalogue for a list of valid values. |
| 5 | Business / Industry Type | Optional | Reject Field | Text | 2 | Required if GS Field ID # 3– Guarantor Type =1 or 3. Refer to Business / Industry Type catalogue for a list of valid values. |
| 6 | Guarantor Entity Name | Required Conditionally | Reject Field/ Segment | Text | 125 | Required if GS Field ID # 3– Guarantor Type = 1 or 3 |
| 7 | Individual Name Prefix | Optional | Reject Fields | Text | 15 | Mr, Ms, Mrs, Dr, etc., Only when GS Field ID # 3– Guarantor Type = 2 or 4 |
| 8 | Full Name | Required Conditionally | Reject Field/ Segment | Text | 150 | Required if GS Field ID # 3– Guarantor Type = 2 or 4 Please Refer “Annexure C” Part C for Name Validation for Individuals Reported as Guarantor |
| 9 | Gender | Optional | Reject Field | Text | 2 | Only When GS Field ID # 3– Guarantor Type =2 or 4 01 Male; 02 Female; 03 Transgender |
| 10 | Company Registration Number | Optional | Reject Field | Text | 30 | Report only when GS Field ID # 3– Guarantor Type = 1 or 3. Company Registration Number issued by Registrar of Companies for business entity registered in India. |
| 11 | Date of Incorporation | Optional | Reject Field | Date | 8 | Report only when GS Field ID # 3– Guarantor Type = 1 or 3. The date on which the Business is Registered / Incorporated |
| 12 | Date of Birth | Optional | Reject Field | Date | 8 | Report only when GS Field ID # 3– Guarantor Type = 2 or 4. |
| 13 | PAN | Optional | Reject Field | Text | 10 | Permanent Account Number issued by Income Tax Authority. |
| 14 | Voter ID | Optional | Reject Field | Text | 30 | Issued by Election Commission of India Only when GS Field ID # 3 Guarantor Type = 2 or 4 |
| 15 | Passport Number | Optional | Reject Field | Text | 30 | Only when GS Field ID # 3 Guarantor Type = 2 or 4 |
| 16 | Driving Licence ID | Optional | Reject Field | Text | 30 | Only when GS Field ID # 3 Guarantor Type = 2 or 4 |
| 17 | UID | Optional | Reject Field | Text | 30 | Issued by Government of India Only when GS Field ID # 3 Guarantor Type = 2 or 4 |
| 18 | Ration Card No | Optional | Reject Field | Text | 30 | Only when GS Field ID # 3 Guarantor Type = 2 or 4 |
| 19 | CIN | Optional | Reject Field | Text | 30 | Corporate Identification Number applicable for GS Field ID #3 Guarantor Type = 1. |
| 20 | DIN | Optional | Reject Field | Text | 12 | Director Identification Number issued by Ministry of Company Affairs. Only when GS Field ID # 3 Guarantor Type = 2 or 4. |
| 21 | TIN | Optional | Reject Field | Text | 30 | Tax Identification Number for Non- Individuals issued by Income Tax Authority. Applicable for GS Field ID #3 Guarantor Type 1 & 2. |
| 22 | Service Tax # | Optional | Reject Field | Text | 30 | Service Tax Registration Number issued by Central Excise Authority Applicable for GS Field ID #3 Guarantor Type 1 & 2. |
| 23 | CKYC | Optional | Reject Field | Text | 30 | 15 Character digit Central KYC (CKYC) managed by Central Registry of Securitization and Asset Reconstruction and Security Interest in India (CERSAI). Applicable for GS Field ID #3 Related Type 1 & 2. |
| 24 | Address Line 1 | Required Conditionally | Reject Field/Segment | Text | 200 | The address is to be parsed logically under the fields Address Line1, Address Line2, Address Line3, City/Town, District, State/ Union Territory. If this is not possible, the entire address can appear in either Address Line1, Address Line2, Address Line3. In such case report City/Town Name appropriately, without truncating or splitting into the next field. Do not truncate. Use comma separated values. Do not split whole words. If insufficient, please use Address Line2 and Address Line 3 Note- Concatenated length of Address Line 1, Address Line 2 and Address Line 3 has to be at least 5 characters. Per field max length will be 200 characters. After Concatenation address field (Line 1, Line 2, or Line 3) that contains only NULL, only “NA,” any combination or repetition of these values, solely numeric characters, or only special characters will result in rejection of the entire Address segment. Address Line 1, 2, 3 are all mentioned as ‘Required Conditionally’ because at least one of these need to be reported. |
| 25 | Address Line 2 | Required Conditionally | Reject Field/Segment | Text | 200 |  |
| 26 | Address Line 3 | Required Conditionally | Reject Field/Segment | Text | 200 |  |
| 27 | City/Town | Optional | Reject Fields | Text | 40 | City name to be provided. There is no check for city name validation done. |
| 28 | District | Optional | Reject Fields | Text | 40 | District name to be provided. There is no check for district name validation done. |
| 29 | State/Union Territory | Required Conditionally | Reject field/ Segment | Text | 2 | To be reported if country is = India. Report for 1= Business Entity Registered in India 2=Resident Indian Individual “77” to be reported for foreign address. Refer to State catalogue for a list of valid values. In the State Code master, Daman and Diu is combined with Dadra and Nagar Haveli. Both the state codes of 8 & 9 will continue to be accepted |
| 30 | Pin Code | Required Conditionally | Reject Field/ Segment | Text | 6 | To be reported if country is = India. Report for 1= Business Entity Registered in India, 2=Resident Indian Individual State – PIN Code validation will be applicable. For e.g. Maharashtra State cannot be reported with PIN code starting with ‘38’. For State Code 77 , PINCode value validation will be only on the max character length |
| 31 | Country | Required | Reject Segment | Text | 3 | Refer to Country Code/ Nationality catalogue for a list of valid values. |
| 32 | Mobile Number(s) | Optional | Reject Field | Text | 55 | Use comma separators for multiple numbers If country Field ID # 31 =India ,then mobile no. to be 10 digit Mobile Number Validations are Mentioned in Annexure C (Part B) |
| 33 | Telephone Area Code | Optional | Reject Field | Text | 6 |  |
| 34 | Telephone Number(s) | Optional | Reject Field | Text | 55 | Use comma separators for multiple numbers Telephone Number Validations are Mentioned in Annexure C (Part B) |
| 35 | Fax Area Code | Optional | Reject Field | Text | 6 |  |
| 36 | Fax Number(s) | Optional | Reject Field | Text | 55 | Use comma separators for multiple numbers |
| 37 | Guarantee Invocation Date | Optional | Reject Field | Date | 58 | Guarantee Invocation date is a date when the guarantee is invoked and not the date of loan account declared as NPA. |
| 38 | Udyam Registration Number | Optional | Reject Field | Text | 30 | Udyam Registration Number issued by Ministry of MSME. Report if GS field no.3 is Guarantor Type 2 (Resident Indian Individual) or Guarantor Type = 1 (Business Entity Registered in India). |

*Please refer Annexure D- PART C for Identifier Validation
Note: Reject Field/Segment means if there is only one record in that segment and the
validation fails only then it will lead to Segment reject.

### 7.7 Security Segment (SS)
- Is a Optional Segment
- This is a sub segment of the Credit Facility Segment
- It can appear multiple times per Credit Facility

| No | Field Name | Field Option | Failure Outcome | Field Type | Max Length | Description |
|---|---|---|---|---|---|---|
| 1 | Segment Identifier | Required | Reject Segment | Text | 2 | It must be SS |
| 2 | Value of Security | Required | Reject Segment | Numeric | 20 | Value of the Tangible Security as on the date of Valuation. |
| 3 | Currency Type | Required | Reject Segment | Text | 3 | Refer to “Currency Code” catalogue for a list of valid values. |
| 4 | Type of Security | Required | Reject Segment | Text | 3 | Refer to Tangible Tangible Security Type catalogue for a list of valid values. |
| 5 | Security Classification | Required | Reject Segment | Text | 2 | Refer to Tangible Security Classification catalogue for a list of valid values. |
| 6 | Date of Valuation | Optional | Reject Field | Date | 8 | The latest date on which the valuation was done. |
| 7 | Filler | Optional | - | Text | 38 | Reserved for future use. This field is to be left blank. |

### 7.8 Dishonour of Cheques Segment (CD)
- Is a Optional Segment
- This is a sub segment of the Credit Facility Segment
- Contains information on cheques dishonoured, if any, by a Subject related to a Credit
Facility
- It can appear multiple times per Credit Facility
- Only Inward Cheques dishonoured due to Insufficient Funds or Closed Accounts need to
be reported

| No | Field Name | Field Option | Failure Outcome | Field Type | Max Length | Description |
|---|---|---|---|---|---|---|
| 1 | Segment Identifier | Required | Reject Segment | Text | 2 | It must be CD |
| 2 | Date of Dishonour | Required | Reject Segment | Date | 8 | Mention the cycle in which the cheque has been dishonoured |
| 3 | Amount | Required | Reject Segment | Numeric | 20 | Amount of the cheque dishonored |
| 4 | Instrument / Cheque Number | Required | Reject Segment | Text | 30 |  |
| 5 | Number of times dishonoured | Required | Reject Segment | Numeric | 10 | Number of times the cheque was dishonoured |
| 6 | Cheque Issue Date | Required Conditionally | Reject Field/ Segment | Date | 8 | To be provided only in case of single cheque dishonoured. |
| 7 | Reason for Dishonour | Required | Reject Segment | Text | 2 | Refer to catalogue Reason for Inward Cheque Dishonour for a valid list of values |
| 8 | Filler | Optional | - | Text | 18 | Reserved for future use. This field is to be left blank. |

Note: Reject Field/Segment means if there is only one record in that segment and the
validation fails only then it will lead to Segment reject.

### 7.9 File Closure Segment (TS)
- Is a Required segment
- Contains summary of the number of Credit Facility Segments reported in the Data Input
File
- Occurs only in the END of the Data Input File
- It should appear only ONCE in the Data Input File
- It will be the FINAL segment in the Data Input File

| No | Field Name | Field Option | Failure Outcome | Field Type | Max Length | Description |
|---|---|---|---|---|---|---|
| 1 | Segment identifier | Required | Reject File | Text | 2 | It must be TS |
| 2 | Number of Borrower Segments | Required | Reject File | Numeric | 10 | Total number of Borrower Segments reported in the file |
| 3 | Number of Credit Facility Segments | Required | Reject File | Numeric | 15 | Total number of Credit Facility Segments reported in the file |
| 4 | Filler | Optional | - | Text | 65 | Reserved for future use. This field is to be left blank. |

## 8. Catalogues

### 8.1 Legal Constitution

| Code | Constitution |
|---|---|
| 11 | Private Limited |
| 12 | Public Limited |
| 20 | Business Entities Created by Statute |
| 30 | Proprietorship |
| 40 | Partnership |
| 50 | Trust |
| 55 | Hindu Undivided Family |
| 60 | Co-operative Society |
| 70 | Association of Persons |
| 80 | Government |
| 85 | Self Help Group |
| 90 | Individual |

### 8.2 Business Category

| Code | Description |
|---|---|
| 03 | Micro |
| 04 | Small |
| 05 | Medium |
| 06 | Large |
| 07 | Others |
| 08 | Retail |
| 09 | Agri |

### 8.3 Business / Industry Type

| Code | Description |
|---|---|
| 01 | Manufacturing |
| 02 | Distribution |
| 03 | Wholesale |
| 04 | Trading |
| 05 | Broking |
| 06 | Service Provider |
| 07 | Importing |
| 08 | Exporting |
| 09 | Agriculture |
| 10 | Dealers |
| 11 | Others |

### 8.4 Location Type

| Code | Description |
|---|---|
| 01 | Registered Office – Required |
| 02 | Branch / Regional Office |
| 03 | Warehouse |
| 04 | Plant / Factory Address |
| 05 | Others |
| 06 | Mortgage Property address |

### 8.5 State

| Code | Description |
|---|---|
| 01 | Andaman and Nicobar Islands |
| 02 | Andhra Pradesh |
| 03 | Arunachal Pradesh |
| 04 | Assam |
| 05 | Bihar |
| 06 | Chandigarh |
| 07 | Chhattisgarh |
| 08 | Dadra and Nagar Haveli and Daman and Diu |
| 10 | Goa |
| 11 | Gujarat |
| 12 | Haryana |
| 13 | Himachal Pradesh |
| 14 | Jammu and Kashmir |
| 15 | Jharkhand |
| 16 | Karnataka |
| 17 | Kerala |
| 18 | Lakshadweep |
| 19 | Madhya Pradesh |
| 20 | Maharashtra |
| 21 | Manipur |
| 22 | Meghalaya |
| 23 | Mizoram |
| 24 | Nagaland |
| 25 | New Delhi |
| 26 | Orissa |
| 27 | Puducherry |
| 28 | Punjab |
| 29 | Rajasthan |
| 30 | Sikkim |
| 31 | Tamil Nadu |
| 32 | Tripura |
| 33 | Uttar Pradesh |
| 34 | Uttarakhand |
| 35 | West Bengal |
| 36 | Telangana |
| 37 | Ladakh |
| 77 | Foreign address |

Note: In the State Code master, Daman and Diu is combined with Dadra and Nagar Haveli. Both the
state codes of 8 & 9 will continue to be accepted.

### 8.6 Type of Relationship

| Code | Relationship |
|---|---|
| 10 | Shareholder |
| 11 | Holding Company |
| 12 | Subsidiary Company |
| 20 | Proprietor |
| 30 | Partner |
| 40 | Trustee |
| 51 | Promoter Director |
| 52 | Nominee Director |
| 53 | Independent Director |
| 54 | Director - Since Resigned |
| 55 | Individual Member of SHG |
| 56 | Other Director |
| 60 | Others |
| 70 | Karta (HUF) |

### 8.7 Currency Code

| Code | Description |
|---|---|
| ADF | Andorran Franc |
| ADP | Andorran Peseta |
| AED | (United) Arab Emirates Dirham |
| AED | United Arab Emirates Dirham |
| AFA | Afghanistan Afghani |
| ALL | Albanian Lek |
| ANG | (Netherlands) Antillian Guilder |
| ANG | Netherlands Antillian Guilder |
| AON | Angolan New Kwanza |
| ARA | Argentine Austral |
| ARS | Argentine Peso |
| ATS | Austrian Schilling |
| AUD | Australian Dollar |
| AWG | Aruban Florin (old guilder) |
| BBD | Barbados Dollar |
| BDT | Bangladeshi Taka |
| BEF | Belgian Franc |
| BGL | Bulgarian Lev |
| BHD | Bahraini Dinar |
| BIF | Burundi Franc |
| BMD | Bermudian Dollar |
| BND | Brunei Dollar |
| BOB | Bolivian Boliviano |
| BRC | Brazilian Cruzeiro |
| BRL | Brazilian Real |
| BSD | Bahamian Dollar |
| BTN | Bhutan Ngultrum |
| BWP | Botswana Pula |
| BZD | Belize Dollar |
| CAD | Canadian Dollar |
| CHF | Swiss Franc |
| CLP | Chilean Peso |
| CNY | Chinese Yuan Renminbi |
| COP | Colombian Peso |
| CRC | Costa Rican Colon |
| CSK | Czech Koruna |
| CUP | Cuban Peso |
| CVE | Cape Verde Escudo |
| CVP | Cyprus Pound |
| DEM | German Mark |
| DJF | Djibouti Franc |
| DKK | Danish Krone |
| DOP | Dominican Peso |
| DZD | Algerian Dinar |
| ECS | Ecuador Sucre |
| EEK | Estonian Kroon |
| EGP | Egyptian Pound |
| ESP | Spanish Peseta |
| ETB | Ethiopian Birr |
| EUR | Euro |
| FIM | Finnish Markka |
| FJD | Fiji Dollar |
| FKP | Falkland Isl.Pound |
| FRF | French Franc |
| GBP | British Pound |
| GBP | Pound Sterling |
| GBP | United Kingdom Pound |
| GHC | Ghanaian Cedi |
| GIP | Gibraltar Pound |
| GMD | Gambian Dalasi |
| GNF | Guinea Franc |
| GRD | Greek Drachma |
| GTQ | Guatemalan Quetzal |
| GWP | Guinea-Bissau Peso |
| GYD | Guyanan Dollar |
| HKD | Hong Kong Dollar |
| HNL | Honduran Lempira |
| HRK | Croatian Kuna |
| HTG | Haitian Gourde |
| HUF | Hungarian Forint |
| IDR | Indonesian Rupiah |
| IEP | Irish Punt |
| ILS | Israeli New Shekel |
| INR | Indian Rupee |
| IQD | Iraqi Dinar |
| IRR | Iranian Rial |
| ISK | Iceland Krona |
| ITL | Italian Lira |
| JMD | Jamaican Dollar |
| JOD | Jordanian Dinar |
| JPY | Japanese Yen |
| KES | Kenyan Schilling |
| KHR | Kampuchean (Cambodian) Riel |
| KHR | Kampuchean (Cambodian) Riel |
| KMF | Comoros Franc |
| KPW | North Korean Won |
| KRW | Korean Won |
| KWD | Kuwaiti Dinar |
| KYD | Cayman Islands Dollar |
| KZT | Kazakhstan Tenge |
| LAK | Lao Kip |
| LBP | Lebanese Pound |
| LKR | Sri Lanka Rupee |
| LRD | Liberian Dollar |
| LSL | Lesotho Loti |
| LTL | Lithuanian Litas |
| LUF | Luxembourg Franc |
| LVL | Latvian Lats |
| LYD | Libyan Dinar |
| MAD | Moroccan Dirham |
| MGF | Malagasy Franc |
| MMK | Myanmar Kyat |
| MNT | Mongolian Tugrik |
| MOP | Macau Pataca |
| MRO | Mauritanian Ouguiya |
| MTL | Maltese Lira |
| MUR | Mauritius Rupee |
| MVR | Maldive Rufiyaa |
| MWK | Malawi Kwacha |
| MXP | Mexican Peso |
| MYR | Malaysian Ringgit |
| MZM | Mozambique Metical |
| NAD | Namibian Dollar |
| NGN | Nigerian Naira |
| NIO | Nicaraguan Cordoba Oro |
| NLG | Netherlands Guilder |
| NLG | Dutch Guilder |
| NOK | Norwegian Kroner |
| NPR | Nepalese Rupee |
| NZD | New Zealand Dollar |
| OMR | Omani Riyal |
| PAB | Panamanian Balboa |
| PEN | Peruvian Neuevo Sol |
| PGK | Papua New Guinea Kina |
| PHP | Philippine Peso |
| PKR | Pakistan Rupee |
| PLZ | Polish Zloty |
| PTE | Portugese Escudo |
| PYG | Paraguay Guarani |
| QAR | Qatari Rial |
| ROL | Romanian Leu |
| RUB | Russian Rouble |
| SAR | Saudi Riyal |
| SBD | Solomon Isl.Dollar |
| SCR | Seychelles Rupee |
| SDD | Sudanese Dinar |
| SDP | Sudanese Pound |
| SEK | Swedish Krona |
| SGD | Singapore Dollar |
| SHP | St. Helena Pound |
| SIT | Slovenian Tolar |
| SKK | Slovak Koruna |
| SLL | Sierra Leone |
| SOS | Somali Schilling |
| SRG | Suriname Guilder |
| STD | Sao Tome and Principe Dobra |
| SVC | El Salvador Colon |
| SYP | Syrian Pound |
| SZL | Swaziland Lilangeni |
| THB | Thai Baht |
| TND | Tunisian Dinar |
| TOP | Tongan Pa'anga |
| TRL | Turkish Lira |
| TTD | Trinidad and Tobago Dollar |
| TWD | Taiwan Dollar |
| TZS | Tanzanian Schilling |
| UAG | Ukraine Hryvnia |
| UAK | Ukraine Karbovanets |
| UGS | Uganda Schilling |
| USD | US Dollar |
| UYP | Uruguayan Peso |
| VEB | Venezuelan Bolivar |
| VND | Vietnamese Dong |
| VUV | Vanuatu Vatu |
| WST | Samoan Tala |
| XAF | CFA Franc BEAC (for Cameroon, the Central African Republic, Chad, Congo, Equatorial Guinea and Gabon) |
| XAF | Cameroon: CFA Franc BEAC |
| XAF | The Central African Republic: CFA Franc BEAC |
| XAF | Chad: CFA Franc BEAC |
| XAF | Congo: CFA Franc BEAC |
| XAF | Equatorial Guinea: CFA Franc BEAC |
| XAF | Gabon: CFA Franc BEAC |
| XAG | Silver (oz.) |
| XAU | Gold (oz.) |
| XOF | Benin: CFA Franc BCEAO |
| XOF | Burkino Faso: CFA Franc BCEAO |
| XOF | CFA Franc BCEAO (for Benin, Burkino Faso, Cote D'Ivoire, Mali, Niger, Senegal and Togo) |
| XOF | Mali: CFA Franc BCEAO |
| XOF | Niger: CFA Franc BCEAO |
| XOF | Cote D'Ivoire: CFA Franc BCEAO |
| XOF | Senegal: CFA Franc BCEAO |
| XOF | Togo: CFA Franc BCEAO |
| XPD | Palladium (oz.) |
| XPT | Platinum (oz.) |
| YUN | Yugoslav Dinar |
| ZAR | South African Rand |
| ZMK | Zambian Kwacha |
| ZWD | Zimbabwe Dollar |

### 8.8 Credit Type

| Code | Description |
|---|---|
| 0100 | Cash credit |
| 0200 | Overdraft |
| 0300 | Demand loan |
| 0310 | Loan extended through credit cards |
| 0410 | Medium term loan (period above 1 year and up to 3 years) |
| 0420 | Long term loan (period above 3 years) |
| 0500 | Packing credit (all export pre-shipment finance) |
| 0610 | Export bills purchased |
| 0620 | Export bills discounted |
| 0630 | Export bills advanced against |
| 0640 | Advances against export cash incentives and duty draw-back claims |
| 0710 | Inland bills purchased |
| 0720 | Inland bills discounted |
| 0800 | Advances against import bills |
| 0900 | Foreign currency cheques TCS/DDS/TTS/MTS purchased |
| 1000 | Lease finance |
| 1100 | Hire purchase |
| 2000 | Bank guarantee |
| 2100 | Deferred payment guarantee |
| 3000 | Letters of credit |
| 3100 | Corporate credit card |
| 4000 | Commercial vehicle loan |
| 4100 | Equipment financing (construction office medical) |
| 5000 | Unsecured business loan |
| 5100 | Short term loan (less than 1 year) |
| 5200 | Aggregation of all fund based facilities |
| 5300 | Aggregation of all non fund based facilities |
| 5400 | Facilities interchange between fund & non fund based |
| 5500 | Derivatives |
| 5600 | Plain vanilla forex forward contracts |
| 5700 | Plain vanila int rate swap(all including INR as coupon) |
| 5800 | Plain vanila foreign currency option (including INR cross currency) |
| 5900 | Complex int rate derv with optionalities |
| 6000 | Any complex derivative loan involving foreign currency with option |
| 6100 | Contracts on past performance – imports |
| 6200 | Contracts on past performance – exports |
| 9000 | Aggregate of all borrowings due to filing of suit |
| 9001 | Auto Loan |
| 9002 | Property Loan |
| 9003 | Gold Loan |
| 9004 | Loan Against Shares/Securities |
| 9005 | HealthCare Finance |
| 9006 | Infrastructure Finance |
| 9007 | Factoring with Recourse (Seller) |
| 9008 | Commercial Paper |
| 9009 | NCD – Non Convertible Debentures |
| 9010 | Unhedged Foreign Currency Exposure |
| 9011 | Payment Account (Point of Sale & Payment Gateways) |
| 9012 | Current Loan |
| 9013 | ARC – Secured Loan (refer Annex. B for validations) |
| 9014 | ARC – Unsecured Loan (refer Annex. B for validations) |
| 9020 | Seller Financing |
| 9021 | GECL Loan |
| 9022 | Mudra Term Loan |
| 9023 | Mudra Working Capital |
| 9024 | Temporary Overdraft |
| 9026 | Factoring without Recourse (Buyer) |
| 9027 | Overdraft against FD |
| 9028 | Overdraft against Shares / Securities |
| 9029 | Overdraft against Collateral |
| 9030 | Merchant Acquiring |
| 9031 | Government Sponsored Loan |
| 9032 | Working Capital Loan |
| 9033 | 3-Wheeler Loan |
| 9036 | Credit Exposures converted to debt/equity securities due to restructuring |
| 9999 | Others |

### 8.9 Asset Classification/ Days Past Due

| Code | Description |
|---|---|
| 0001 | Standard |
| 0002 | Sub-standard |
| 0004 | Loss |
| 0006 | SMA 0 – Principal or interest payment not overdue for more than 30 days but account showing signs of incipient stress. |
| 0007 | SMA 1 – Principal or interest payment overdue between 31-60 days |
| 0008 | SMA 2 – Principal or interest payment overdue between 61 – 90 days, and NA (Not applicable) |
| 0009 | Doubtful -1 |
| 0010 | Doubtful -2 |
| 0011 | Doubtful -3 |
| 0012 | Non-Performing Assets (NPA) |
| 0013 | ARC Loan (refer Annex. B for validations) |
| 1001 | 1 Day Past Due |
| 1002 | 2 Days Past Due |
| 1nnn | Nnn Days Past Due Note: Nnn is the actual number of days. E.g., report 1214, if the the number of days past due is 214 days |
| 1999 | 999 or above Days Past Due |
| 1000 | 0 Day Past Due |

### 8.10 Account Status

| Code | Description |
|---|---|
| 01 | Open |
| 02 | Closed By Payment |
| 03 | Settled & Closed |
| 04 | Restructured |
| 05 | Written Off |
| 06 | Settled Post Write Off |
| 07 | Invoked |
| 08 | Devolved |
| 09 | Restructured Due to Natural Calamity |
| 10 | Sold to ARC (refer Annex. B for validations) |
| 11 | Purchase from Bank |
| 12 | Restructured & Closed |
| 13 | Restructured Loan (Govt. Mandated) |
| 14 | Account Sold |
| 15 | Written off and Account Sold |
| 16 | Account Purchased |
| 17 | Account Purchased and Written Off |
| 18 | Account Purchased and Settled |
| 19 | Account Purchased and Restructured |
| 20 | Restructured due to COVID-19 |
| 21 | Post Write Off Closed |
| 22 | Auctioned & Settled |
| 23 | Repossessed & Settled |
| 24 | Guarantee Invoked |

### 8.11 Suit Filed Status

| Code | Description |
|---|---|
| 00 | Not a Suit Filed Case |
| 01 | Suit Filed |
| 02 | Trial in Progress |
| 03 | Decree issued by court |
| 04 | Execution of Decree |
| 05 | NCLT/NCLAT Suit Filed |

### 8.12 Transaction Type Code

| Code | Description |
|---|---|
| 01 | Normal Submission |
| 02 | Dispute Corrected |
| 03 | Dispute Rejected |

### 8.13 Tangible Security Type

| Code | Description |
|---|---|
| 001 | Cash/ Bullion/ Bank Deposits |
| 002 | Shares/ Bonds/ Securities |
| 003 | Inventory (Raw Material, WIP and Finished Goods) |
| 004 | Accounts Receivable |
| 005 | Other Current Assets |
| 006 | Plant & Machinery and Equipment |
| 007 | Land & Buildings |
| 008 | Other Fixed Assets |
| 009 | Other Assets |
| 010 | Aggregate of all Current Assets |
| 011 | Aggregate of all Fixed Assets |

### 8.14 Tangible Security Classification

| Code | Description |
|---|---|
| 01 | Primary – First Charge |
| 02 | Primary – Second Charge |
| 03 | Primary – Third Charge |
| 04 | Primary – Parri Passu |
| 21 | Collateral – First Charge |
| 22 | Collateral – Second Charge |
| 23 | Collateral – Third Charge |
| 24 | Collateral – Parri Passu |

### 8.15 Country Code/ Nationality

| Code | Description |
|---|---|
| 001 | Afghanistan |
| 002 | Albania |
| 003 | Algeria |
| 004 | Andorra |
| 005 | Angola |
| 006 | Antigua and Barbuda |
| 007 | Argentina |
| 008 | Armenia |
| 009 | Australia |
| 010 | Austria |
| 011 | Azerbaijan |
| 012 | Bahamas |
| 013 | Bahrain |
| 014 | Bangladesh |
| 015 | Barbados |
| 016 | Belarus |
| 017 | Belgium |
| 018 | Belize |
| 019 | Benin |
| 020 | Bhutan |
| 021 | Bolivia |
| 022 | Bosnia and Herzegovina |
| 023 | Botswana |
| 024 | Brazil |
| 025 | Brunei Darussalam |
| 026 | Bulgaria |
| 027 | Burkina Faso |
| 028 | Burundi |
| 029 | Cambodia |
| 030 | Cameroon |
| 031 | Canada |
| 032 | Cape Verde |
| 033 | Central African Republic |
| 034 | Chad |
| 035 | Chile |
| 036 | China |
| 037 | Colombia |
| 038 | Comoros |
| 039 | Congo |
| 040 | Costa Rica |
| 041 | Cote d'Ivoire |
| 042 | Croatia |
| 043 | Cuba |
| 044 | Cyprus |
| 045 | Czech Republic |
| 046 | Democratic People's Republic of Korea |
| 047 | Democratic Republic of the Congo |
| 048 | Denmark |
| 049 | Djibouti |
| 050 | Dominica |
| 051 | Dominican Republic |
| 052 | East Timor |
| 053 | Ecuador |
| 054 | Egypt |
| 055 | El Salvador |
| 056 | Equatorial Guinea |
| 057 | Eritrea |
| 058 | Estonia |
| 059 | Ethiopia |
| 060 | Fiji |
| 061 | Finland |
| 062 | France |
| 063 | Gabon |
| 064 | Gambia |
| 065 | Georgia |
| 066 | Germany |
| 067 | Ghana |
| 068 | Greece |
| 069 | Grenada |
| 070 | Guatemala |
| 071 | Guinea |
| 072 | Guinea-Bissau |
| 073 | Guyana |
| 074 | Haiti |
| 075 | Honduras |
| 076 | Hong Kong |
| 077 | Hungary |
| 078 | Iceland |
| 079 | India |
| 080 | Indonesia |
| 081 | Iran (Islamic Republic of) |
| 082 | Iraq |
| 083 | Ireland |
| 084 | Israel |
| 085 | Italy |
| 086 | Jamaica |
| 087 | Japan |
| 088 | Jordan |
| 089 | Kazakhstan |
| 090 | Kenya |
| 091 | Kiribati |
| 092 | Kuwait |
| 093 | Kyrgyzstan |
| 094 | Lao People's Democratic Republic |
| 095 | Latvia |
| 096 | Lebanon |
| 097 | Lesotho |
| 098 | Liberia |
| 099 | Libyan Arab Jamahiriya |
| 100 | Liechtenstein |
| 101 | Lithuania |
| 102 | Luxembourg |
| 103 | Madagascar |
| 104 | Malawi |
| 105 | Malaysia |
| 106 | Maldives |
| 107 | Mali |
| 108 | Malta |
| 109 | Marshall Islands |
| 110 | Mauritania |
| 111 | Mauritius |
| 112 | Mexico |
| 113 | Micronesia, Federated States of |
| 114 | Monaco |
| 115 | Mongolia |
| 116 | Morocco |
| 117 | Mozambique |
| 118 | Myanmar |
| 119 | Namibia |
| 120 | Nauru |
| 121 | Nepal |
| 122 | Netherlands |
| 123 | New Zealand |
| 124 | Nicaragua |
| 125 | Niger |
| 126 | Nigeria |
| 127 | Norway |
| 128 | Oman |
| 129 | Pakistan |
| 130 | Palau |
| 131 | Palestinian Authority |
| 132 | Panama |
| 133 | Papua New Guinea |
| 134 | Paraguay |
| 135 | Peru |
| 136 | Philippines |
| 137 | Poland |
| 138 | Portugal |
| 139 | Qatar |
| 140 | Republic of Korea |
| 141 | Republic of Moldova |
| 142 | Romania |
| 143 | Russian Federation |
| 144 | Rwanda |
| 145 | Saint Kitts and Nevis |
| 146 | Saint Lucia |
| 147 | Saint Vincent and the Grenadines |
| 148 | Samoa |
| 149 | San Marino |
| 150 | Sao Tome and Principe |
| 151 | Saudi Arabia |
| 152 | Senegal |
| 153 | Serbia and Montenegro |
| 154 | Seychelles |
| 155 | Sierra Leone |
| 156 | Singapore |
| 157 | Slovakia |
| 158 | Slovenia |
| 159 | Solomon Islands |
| 160 | Somalia |
| 161 | South Africa |
| 162 | Spain |
| 163 | Sri Lanka |
| 164 | Sudan |
| 165 | Suriname |
| 166 | Swaziland |
| 167 | Sweden |
| 168 | Switzerland |
| 169 | Syrian Arab Republic |
| 170 | Taiwan |
| 171 | Tajikistan |
| 172 | TFYR Macedonia |
| 173 | Thailand |
| 174 | Togo |
| 175 | Tonga |
| 176 | Trinidad and Tobago |
| 177 | Tunisia |
| 178 | Turkey |
| 179 | Turkmenistan |
| 180 | Tuvalu |
| 181 | Uganda |
| 182 | Ukraine |
| 183 | United Arab Emirates |
| 184 | United Kingdom |
| 185 | United Republic of Tanzania |
| 186 | United States of America |
| 187 | Uruguay |
| 188 | Uzbekistan |
| 189 | Vanuatu |
| 190 | Venezuela |
| 191 | Vietnam |
| 192 | Western Sahara |
| 193 | Yemen |
| 194 | Zambia |
| 195 | Zimbabwe |

### 8.16 Tangible Security Coverage

| Code | Description |
|---|---|
| 01 | Full |
| 02 | Partial |
| 03 | Nil |

### 8.17 Guarantee Coverage

| Code | Description |
|---|---|
| 01 | Full |
| 02 | Partial |
| 03 | Nil |

### 8.18 Repayment Frequency

| Code | Description |
|---|---|
| 01 | Monthly |
| 02 | Quarterly |
| 03 | Half yearly |
| 04 | Annual |
| 05 | On Demand |
| 06 | Bullet |
| 07 | Rolling |
| 08 | Others |

### 8.19 Assessment Agency/Authority

| Code | Description |
|---|---|
| 01 | CARE |
| 02 | CRISIL |
| 03 | ICRA |
| 05 | Acuite Ratings and Research Limited |
| 06 | India Ratings and Research Pvt Ltd |
| 07 | Others |
| 08 | Acer Credit Rating Private Limited |
| 09 | Brickwork Ratings India Private Limited |
| 10 | Infomerics Ratings Private Ltd |

### 8.20 Reason for Inward Cheque Dishonour

| Code | Description |
|---|---|
| 01 | Insufficient Funds |

## 9. Appendix A – Allowed and Disallowed Characters
Following are the allowed and disallowed characters in the Related Individual Name and
Previous Related Individual Name in the Relationship Segment (RS), Guarantor Individual
Name and Previous Individual Name of Guarantor in the Guarantor Segment (GU)

| Allowed | Disallowed |
|---|---|
| . | ~ |
| ` | ! |
|  | @ |
| ( | # |
| ) | $ |
| [ | % |
| ] | ^ |
| { | & |
| } | * |
| / | / |
| \ | \ |
|  | + |
|  | - |
|  | : |
|  | ; |
|  | \| |
|  | < |
|  | > |
|  | ? |
|  | , |
|  | _ |

NOTE: The front slash (/) and the back slash (\) are allowed only when used with SON OF, WIFE OF and DAUGHTER OF in any of the following ways: S/O, S\O, W/O, W\O, D/O,H/0 and D\O. In all other situations, their appearance will lead to a rejection of the data.

## 10. Annexure B – Account Status Validation Rules
Following are the validation when we have ARC data reporting.
a. If Account status=10, then Current balance must be = 0
b. If Account status=10, then Amount Overdue must be = 0
c. If Account status=10, then DPD/AC must be = 1000
d. If Account status=10, then Account Status Date cannot be blank
e. If Account status=11, then Current balance must be > 0
f. If Account status=11, then Amount Overdue must be > 0
g. If Account status=11, then Sanctioned Date cannot be blank

## 11. Annexure C – Name, Email & Telephone No. Validations

#### Part-A Recommended checks on email quality:
1. Only one email ID is allowed which must have only one @ symbol and at least one dot
(.)
2. Before the @ symbol there must be at least 2 Alphanumeric characters present
excluding special characters.
3. After @symbol the domain name begins and must have at least 2 alphanumeric
characters.
4. Between the @ symbol and immediate next (.) Dot there must be 2 characters length
5. Alphanumeric value present. Example abc@gmail.com After the (.) there must be at
least 2-character Alphanumeric character.
6. Example Kumar@gmail.com / kumar@yahoo.co.in
7. The Email address cannot end with @ symbol or (.) symbol.
8. There cannot me more than two (.) symbol after @ symbol
9. Space is not allowed in email field. If Space is found in middle of email address, it would
be rejected
10. Before the @ symbol Special characters such as dots (.), hyphens (-), and underscores
(_) are allowed along with characters [aA-zZ] & [0-9].
11. Email addresses are not case-sensitive

#### Part-B
1. Telephone numbers must not start with “1”.
2. Any Indian mobile telephone number, after stripping ISD prefix, will start with
“5”,“6”,”7”,“8” or “9” and require a minimum of 10 digits.
3. An International Standard Dialing (ISD) number requires a minimum of nine digits.
4. The last no. "012 3" is clearly a truncated no. and this would be edited out from the
input.). Only the edited Telephone number would be used for further field validation.
5. The telephone number identified in this field with straight descending/ascending
sequence (e.g., 876543/234567) or same digits (e.g., 222222) will be edited out from the
input field. Also when multiple telephone numbers are provided, a truncated telephone
number at the end of the field would be edited out as well (e.g., 5436734,645231,0123
The last no. "012 3" is clearly a truncated no. and this would be edited out from the
input.). Only the edited Telephone number would be used for further field validation.

#### PART-C
1. A minimum 1 token with at least 2 alphabet are mandatory in name field.
2. If only a single name is provided, the submission of at least one Valid ID from the catalog values
in the 'ID type' field should be provided.
3. Numbers are strictly prohibited within name strings.
4. Name fields starting with Father/mother/husband/wife name submitted with S/O, W/O, H/O and
D/O (e.g. AMIT S/O SUNIL) shall be accepted but any other values (such as C/O AMIT) shall not be
accepted.
5. Consumer names containing S/O,D/O, H/O or W/O will be accepted only if there is a name token
is reported prior to relationship identifier. Names that begin with S/O,D/O,H/O or W/O will be
rejected. Both forward slashes(/) and backslashed (\) are permitted. Other combination of
whatever off(Like C/O & H/O will not be accepted.
6. Salutations are not considered part of a valid name, even if they appear. If a name consists only
of a salutation, it will be rejected. Similarly, if the Name1 field contains only a salutation, it will
also be rejected. Salutations such as Mr., Mrs., Ms., with or without a dot (.), are acceptable
when provided along with the complete name. For example, "Rakesh Mr." will be accepted.
However, if "Mr. Rakesh" is provided, the salutation (Mr.) will be ignored, and only "Rakesh" will
be accepted with any valid ID.

## 12. Annexure D – Relationship Type Validations

#### PART-A: Legal Constitution and Relationship Type mapping:

|  | Code | 10 | 11 | 12 | 20 | 30 | 40 | 51 | 52 | 53 | 54 | 55 | 56 | 60 | 70 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Code | Related Party Type | Shareholder | Holding Company | Subsidiary Company | Proprietor | Partner | Trustee | Promoter Director | Nominee Director | Independent Director | Director- Since Resigned | Individual Member of SHG | Other Director | Others | Karta (HUF) |
| 11 | Private Limited | ✓ | ✓ | ✓ |  |  |  | ✓ | ✓ | ✓ | ✓ |  | ✓ |  |  |
| 12 | Public Limited | ✓ | ✓ | ✓ |  |  |  | ✓ | ✓ | ✓ | ✓ |  | ✓ |  |  |
| 20 | Business Entities Created by Statute | ✓ | ✓ | ✓ |  |  |  |  | ✓ | ✓ | ✓ |  | ✓ | ✓ |  |
| 30 | Proprietorship |  | ✓ |  | ✓ |  |  |  |  |  |  |  |  |  | ✓ |
| 40 | Partnership |  |  |  |  | ✓ |  |  |  |  |  |  |  |  |  |
| 50 | Trust |  |  |  |  |  | ✓ |  |  |  |  |  |  |  |  |
| 55 | Hindu Undivided Family |  |  |  |  |  |  |  |  |  |  |  |  |  | ✓ |
| 60 | Co‑operative Society | ✓ |  |  |  |  | ✓ | ✓ | ✓ | ✓ |  |  |  | ✓ |  |
| 70 | Association of Persons |  |  |  |  |  | ✓ |  |  |  | ✓ |  |  | ✓ |  |
| 80 | Government |  |  |  |  |  |  |  |  |  |  |  |  | ✓ |  |
| 85 | Self Help Group |  |  |  |  |  |  |  |  |  |  | ✓ |  |  |  |

#### PART- B: Impermissible Relationships
The below table denotes Relationships (Field no.4 RS segment) not applicable to respective
Related type (Field no 3 RS segment)

| Sr. No | Related Type (RS Field 3) | Impermissible Relationships (RS Field 4) |
|---|---|---|
| 1 | BUSINESS ENTITY REGISTERED IN INDIA/GOVERNMENT | PROPRIETOR, NO LONGER ASSOCIATED, PROMOTER DIRECTOR, NOMINEE DIRECTOR, INDEPENDENT DIRECTOR |
| 2 | BUSINESS ENTITY REGISTERED OUTSIDE INDIA/GOVERNMENT | TRUSTEE, PROPRIETOR, NO LONGER ASSOCIATED, PROMOTER DIRECTOR, NOMINEE DIRECTOR, INDEPENDENT DIRECTOR |
| 3 | RESIDENT INDIAN INDIVIDUAL | SUBSIDIARY COMPANY, HOLDING COMPANY |
| 4 | FOREIGN / NON RESIDENT INDIAN INDIVIDUAL | SUBSIDIARY COMPANY, HOLDING COMPANY |

#### Part -C Identifier Validation

| Identifier | Description | Format Validations |
|---|---|---|
| PAN | Permanent Account Number issued by Income Tax Authority | Validate for the standard 10-character alphanumeric PAN structure and ensure the absence of special or non-printable characters |
| CIN | Corporate Identification Number Issued by MCA | Validate for the standard 21-character alphanumeric CIN structure and ensure no special or non-printable characters are present. |
| TIN | Tax Identification Number for Non- Individuals issued by Income Tax Authority | Validate for the standard 11-character alphanumeric TIN format and confirm that it contains no special or non-printable characters. |
| CYKC | Central KYC (CKYC) managed by Central Registry of Securitization and Asset Reconstruction and Security Interest in India (CERSAI) | Validate when available, ensuring it adheres to the prescribed maximum length of 15 characters where first character may be alphanumeric with minimum of 14 digits and contains no non-printable characters. |
| Passport Number | Issued by the Ministry of External Affairs (MEA) through the Passport Seva Kendras (PSKs) / Passport Offices. | If present, must be a minimum of 7 and a maximum of 10 characters (excluding delimiters such as space, hyphen, etc.). The first character must be a letter, second character can be alphanumeric and the remaining characters must be numbers. |
| Voter ID | Elector’s Photo Identity Card (EPIC) number, as issued by the Election Commission. | If present, must be a minimum of 9 characters (excluding delimiters such as space, hyphen, special characters etc.). Old format: For example, MT/08/039/0060725 New format: For example, AAA/999999/0 At least the first two, and no more than the first three characters must be letters, and the remaining must be alpha numeric. |
| Udyam Registration Number | Udyam Registration. This unique number is issued to Micro, Small, and Medium Enterprises (MSMEs) by the Ministry of Micro, Small & Medium Enterprises, Government of India. | Standard Format: UDYAM-XX-00-0000000 UDYAM – Prefix XX – State Code 00 – District Code 0000000 – Unique Business Number |

#### Part -D Account Status
The Account status and their descriptions are listed in the following table:

| Code | Account Status | Explanation |
|---|---|---|
| 01 | Open | The status “Open” indicates that the loan or credit facility is currently active. The borrower is still repaying the outstanding amount as per the agreed terms, and the account has not yet been closed by the lender. |
| 02 | Closed By Payment | The status “Closed by Payment” signifies that the borrower has fully repaid the loan or credit card outstanding amount as per the original terms. After receiving complete payment, the lender has officially closed the account/Credit Limit. |
| 03 | Settled & Closed | The "Settled & Closed" status indicates that a portion of a loan or credit card debt has been paid, and the lender has agreed to accept a reduced payoff amount instead of the full original debt. |
| 04 | Restructured | Basis on borrower’s request to lender modifies the terms of an existing loan to make it more manageable for the borrower. This approach aims to assist borrowers in avoiding default by offering temporary relief and enabling them to manage their debt more effectively. |
| 05 | Written Off | A "written off" status indicates that the borrower has not repaid their outstanding loan or credit card debt for an extended period, based on respective policy by CI. |
| 06 | Settled Post Write Off | “Settled post Write Off” on a consumer credit report indicates that a debt, previously written off (WO) by the lender due to non-payment, has been resolved. This signifies that the borrower and lender have reached an agreement to settle the debt for a reduced amount, which can be viewed as a positive step toward rebuilding creditworthiness. |
| 07 | Invoked | In the context of a credit report, the term “Invoked" refers to a situation in which a guarantee or surety has been activated due to the failure of the obligated party to fulfill their commitment, such as repaying a loan. As a result, the guarantor is now responsible for satisfying the debt. |
| 08 | Devolved | In the context of a credit report, the term “Devolved" refers to a where the bank must make payment from its own funds because the borrower has not provided sufficient funds to meet the liability. |
| 09 | Restructured Due to Natural Calamity | The term "Restructured due to Natural Calamity" indicates that the lender has renegotiated the terms of a loan to facilitate repayment, usually in response to financial difficulties resulting from a natural disaster. |
| 10 | Sold to ARC (refer Annex. B for validations) | In a consumer credit report, the term “Sold to ARC" indicates that the original lender has transferred or sold the debt to ARC. As a result, the account is now reported by the ARC than the original lender. |
| 11 | Purchase from Bank | "Purchased from Bank " generally indicates that a debt has been sold to a collection agency, signifying that the original creditor no longer owns the debt and that a different entity now possesses the rights to collect it. |
| 12 | Restructured & Closed | In the Borrower's credit report, the term "Restructured & Closed" denotes that the lender has altered the original loan terms in response to financial difficulties faced by the borrower or by Government Scheme. Conversely, "Closed" indicates that the loan account has been fully repaid and officially closed by the lender. |
| 13 | Restructured Loan (Govt. Mandated) | Restructured Loan (Government Mandated)" refers to a loan whose terms have been modified as part of a government-led initiative aimed at providing relief to borrowers experiencing financial challenges. This restructuring process involves cooperation between the borrower and lender. |
| 14 | Account Sold | In a consumer credit report, the term "Account Sold" indicates that the original lender has transferred or sold the debt to a third-party collection agency. As a result, the account is now reported by the collection agency rather than the original lender. |
| 15 | Written off and Account Sold | "Written Off and Account Sold" refers to a situation in which a loan or credit account has been recognized by the lender as uncollectible. Consequently, the lender has classified the debt as a loss and may have transferred the account to a collection agency or Asset Reconstruction Company (ARC) for further management. |
| 16 | Account Purchased | "Account Purchased" generally indicates that a debt has been sold to a collection agency, signifying that the original creditor no longer owns the debt and that a different entity now possesses the rights to collect it. |
| 17 | Account Purchased and Written Off | The term "Account Purchased and Written Off" refers to a situation in which a lender has classified an account as uncollectible and recorded it as a loss (written off). Subsequently, the debt associated with this account is sold to another party, which may include a debt collection agency or another financial institution. |
| 18 | Account Purchased and Settled | An 'Account Purchased and Settled' refers to a situation where a lender has ceased efforts to collect an outstanding debt and has sold the account to a third-party entity. The third-party lender has agreed to accept a reduced payment, known as a settlement, to resolve the account. This transaction may have a detrimental effect on the borrower's credit score. |
| 19 | Account Purchased and Restructured | The term "Account Purchased and Restructured" refers to a borrower loan or credit account that has been acquired by a different entity (referred to as a "purchased" account) and for which the terms of the loan have been adjusted (restructured) to better align with the borrower's financial circumstances. |
| 20 | Restructured due to COVID-19 | The phrase "Restructured due to COVID-19" indicates that a loan has been modified by the lender to alleviate the repayment challenges resulting from financial difficulties caused by the COVID-19 pandemic. |
| 21 | Post Write Off Closed | Post Write-Off Closed" refers to the status of a loan that has previously been written off, indicating that the lender has either reached a settlement agreement or received full payment of the outstanding amount. Subsequently, the lender has reported the account as closed. |
| 22 | Auctioned & Settled | In the context of a credit report, the term "auctioned" refers to a scenario in which a lender reclaims and sells a collateralized asset, such as a house or car, in order to recover funds from a defaulted loan. Conversely, "settled" indicates that the borrower and lender have reached an agreement to pay a portion of the loan, rather than the full amount, to close the account. |
| 23 | Repossessed & Settled | In a credit report, the term "repossessed" signifies that a lender has taken ownership of an asset, such as a vehicle or property, due to the borrower's default on a loan. Conversely, "settled" refers to a situation where a borrower has paid less than the total amount owed to fully close the account. |
| 24 | Guarantee Invoked | In the context of a credit report, the term "Guarantee Invoked" refers to a situation in which a guarantee or surety has been activated due to the failure of the obligated party to fulfill their commitment, such as repaying a loan. As a result, the guarantor is now responsible for satisfying the debt. |

#### Part E- State PINcode Mapping.

| State | State Code | First Two-digit PIN prefix (Min) | First Two-digit PIN prefix (Max) |
|---|---|---|---|
| Andaman & Nicobar Islands | 1 | 74 | 74 |
| Andhra Pradesh | 2 | 50 | 56 |
| Arunachal Pradesh | 3 | 78 | 79 |
| Assam | 4 | 78 | 79 |
| Bihar | 5 | 80 | 85 |
| Chandigarh | 6 | 16 | 16 |
| Chhattisgarh | 7 | 46 | 49 |
| Dadra & Nagar Haveli and Daman & Diu | 8 | 36 | 39 |
| Goa | 10 | 40 | 40 |
| Gujarat | 11 | 36 | 39 |
| Haryana | 12 | 12 | 13 |

| Himachal Pradesh | 13 | 17 | 17 |
|---|---|---|---|
| Jammu & Kashmir | 14 | 18 | 19 |
| Jharkhand | 15 | 81 | 83 |
| Karnataka | 16 | 53 | 59 |
| Kerala | 17 | 67 | 69 |
| Lakshadweep | 18 | 67 | 68 |
| Madhya Pradesh | 19 | 45 | 48 |
| Maharashtra | 20 | 40 | 44 |
| Manipur | 21 | 79 | 79 |
| Meghalaya | 22 | 79 | 79 |
| Mizoram | 23 | 79 | 79 |
| Nagaland | 24 | 79 | 79 |
| Delhi | 25 | 11 | 11 |
| Orissa | 26 | 75 | 77 |
| Pondicherry | 27 | 53 | 67 |
| Punjab | 28 | 14 | 16 |
| Rajasthan | 29 | 30 | 34 |
| Sikkim | 30 | 73 | 73 |
| Tamil Nadu | 31 | 53 | 66 |
| Tripura | 32 | 72 | 79 |
| Uttar Pradesh | 33 | 20 | 28 |
| Uttarakhand | 34 | 24 | 26 |
| West Bengal | 35 | 70 | 74 |
| Telangana | 36 | 50 | 56 |
| Ladakh | 37 | 18 | 19 |
| Foreign Address | 77 | - | - |
| APO Address | 99 | 90 | 90 |

---

## Appendix – Revision Control (from source)

| Version | Date | Description |
|---|---|---|
| 3.0 | July 2012 | Version 3.0 released |
| 3.1 | January 2016 | • 7 New credit facility types added (LOAN AGAINST SHARES/SECURITIES (9004), Health Finance (9005), Infrastructure Finance (9006), Factoring (9007), Commercial Papers (9008), NCD – Non Convertible Debentures (9009), Unhedged Foreign Currency Exposure (9010), Payment Account (Point Of Sale & Payment Gateways)(9011) • Field name in Credit Facility segment changed from Asset Classification to Asset Classification / Days Past Due • Note added in Bank Remark Code” of Credit Facility Segment to indicate “Joint” for Joint loans. • New field added “Other_BK” for members to report data related to Consortium lending • Added new Asset Classification – SMA 0,1, 2 & Doubtful 1, 2, 3 under Asset Classification / Days Past Due catalogue • Gender type for Transgender (Relationship segment & Guarantor segment) • Restructured Due to Natural Calamity ◦ Added in Major reasons for Restructuring 02- Restructured due to Natural Calamity ◦ Added on Account status 09-Restructured due to Natural Calamity under Account Status catalogue |
| 3.2 | Feb 2016 | • Added new Asset Classification – NPA Non Performing Assets (0012) • Unhedged Foreign Currency Exposure Amount (UFCE) & UFCE Date added in Credit Facility Segment |
| 3.2 | Mar 2016 | Description Change for the following fields • Name of CP issuer - BS Segment - Field No. 4 Field Name: Borrower's Name" • CP Amount - "CF Segment - Field No. 5 Field Name: Sanctioned Amount/ Notional Amount of Contract" • Issue Date - "CF Segment - Field No. 4 Field Name: Facility / Loan Activation / Sanction Date" • Maturity Date - "CF Segment - Field No. 13 Field Name: Loan Expiry / Maturity Date" • Name of CRA - "BS Segment- Field No. 24 Field Name: Assessment Agency / Authority" • Rating Agency - "BS Segment - Field No. 23 Field Name: Credit Rating" • Amount of Default - "CF Segment - Field No. 17 Field Name: Amount Overdue / Limit Overdue" |
| 3.3 | Dec 2018 | • ARC Changes in Asset Classification, Credit Type & Account status • ARC related validation included in Annexure B |
| 3.4 | June 2020 | • Seller Financing Credit Type added • GECL Loan in Credit type |
| 3.5 | April 2021 | • ‘Major reasons for restructuring’ will have a new catalogue value, viz., ‘Restructured due to COVID-19’ |
| 3.6 | May 2022 | • Under 7.2 Borrower Segment removed catalogue value 99 Not Classified for field no. 13 Borrower’s Legal Constitution. Refer to catalogue master 8.1 Legal Constitution • Under 7.2 Borrower Segment, changed field no. 27 from filler to “when available” field “CKYC”. • Under 7.2 Borrower Segment, changed field no. 12 ”Other ID” to “Udyam Registration Number”. • Under 7.3 Address Segment added catalogue value - 06 Mortgage Property Address for field no. 2 Borrower Office Location type. Refer to catalogue master 8.4 Location Type. • Segment 7.4 Relationship Segment is mandatory ◦ Field no. 10 Gender changed from ‘when available’ to ‘required conditionally’ for RS Field ID #3 Related Type 2 (Resident Indian Individual) or 4 (Foreign/Non-Resident Indian Individual) ◦ Field no. 13 Date of Birth changed from ‘when available’ to ‘required conditionally’ for RS Field ID #3 Related Type 2 (Resident Indian Individual) or 4 (Foreign/Non- Resident Indian Individual) ◦ Field no. 14 PAN changed from ‘when available’ to ‘required conditionally’. Required to be reported for RS Field ID #3 Related Type = 1 (Business Entity Registered in India) OR RS Field ID #3 Related Type 2 (Resident Indian Individual) and none of the other IDs mentioned in Field Nos. 15,16,17,18,19 is provided. ◦ Field no. 15 Voter ID changed from ‘when available’ to ‘required conditionally’ for RS Field ID #3 Related Type 2 (Resident Indian Individual) and none of the other IDs mentioned in Field Nos. 14,16,17,18,19 is provided. ◦ Field no. 16 Passport Number changed from ‘when available’ to ‘required conditionally’ for RS Field ID #3 Related Type 2 (Resident Indian Individual) or 4 (Foreign/Non-Resident Indian Individual) and none of the other IDs mentioned in Field Nos. 14,15,17,18,19 is provided. ◦ Field no. 17 Driving License ID changed from ‘when available’ to ‘required conditionally’ for RS Field ID #3  Related Type 2 (Resident Indian Individual) or 4 (Foreign/Non-Resident Indian Individual) and none of the other IDs mentioned in Field Nos. 14,15,16,18,19 is provided. ◦ Field no. 18 UID changed from ‘when available’ to ‘required conditionally’ for RS Field ID #3 Related Type 2 (Resident Indian Individual) and none of the other IDs mentioned in Field Nos. 14,15,16,17,19 is provided. ◦ Field no. 19 Ration Card No. changed from ‘when available’ to ‘required conditionally’ for RS Field ID #3 Related Type 2 (Resident Indian Individual) and none of the other IDs mentioned in Field Nos. 14,15,16,17,18 is provided. ◦ Field no. 31 State/Union Territory changed from ‘when available’ to ‘required conditionally’ for RS Field ID #3 Related Type 2 (Resident Indian Individual) and Related Type = 1 (Business Entity Registered in India) ◦ Field no. 32 PIN Code changed from ‘when available’ to ‘required conditionally’ for RS Field ID #3 Related Type 2 (Resident Indian Individual) and Related Type = 1 (Business Entity Registered in India) • Under 7.4 Relationship Segment added catalogue value 70 Karta (HUF) for field no. 4 Relationship. Refer catalogue master 8.6 Type of Relationship. • Under 7.4 Relationship Segment, changed field no. 24 “Other ID” to “when available” “CKYC”. • Under 7.4 Relationship Segment, changed field no. 39 from filler field to “when available” for Udyam Registration Number if RS field no.3 is Related Type 2 (Resident Indian Individual) or Related Type = 1 (Business Entity Registered in India). • Under 7.5 Credit Facility Segment added Catalogue value - 12 - Restructured & Closed for field no. 26 Account status. Refer to catalogue master 8.10 Account Status. • Under 7.5 Credit Facility Segment removed Catalogue values Special Mention Accounts (0005), Doubtful (0003) for field no. 15 Asset Classification / Days Past Due (DPD). Refer to catalogue master 8.9 Asset Classification / Days Past Due. • Under 7.5 Credit Facility Segment added Catalogue value Mudra Term Loan (9022), Mudra Working Capital (9023), Temporary Overdraft (9024) for field no. 7 Credit Type. Refer to catalogue master 8.8 Credit Type. • Under 7.5 Credit Facility Segment, field no. 4 Facility/Loan Activation/Sanction Date changed from “When Available”  to “Required”. • Under 7.6 Guarantor Segment, field no. 37 changed from filler field to “when available” Guarantee Invocation Date field. • Under 7.6 Guarantor Segment, field no. 23 changed from “Other ID” to “CKYC”. • Under 7.6 Guarantor Segment, added field no. 38 as “when available” for Udyam Registration Number if GS field no.3 is Guarantor Type 2 (Resident Indian Individual) or Guarantor Type = 1 (Business Entity Registered in India). |
| 3.7 | Feb 2024 | • Added a new value in 8.11 Suit File Status as per circular from RBI RBI/2022-23/154 DoR.FIN.REC.90/20.16.056/2022-23 dated 13th Dec’22. New value added is 05 NCLT/NCLAT Suit Filed • As per RBI/2023-24/73 DoR.FIN.REC.49/20.16.003/2023-24 dated 26th Oct’23, under 7.3 Address Segment, the “Fax Number” field is renamed to “Email ID”. • Fax area code is changed to “Future Use” • Annexure B typo errors rectified • Annexure C added elaborating the suggested email ID validation rules. • Annexure D added mentioning the relationship type that should ideally be reported for which Legal Constitution given RBI’s circular on Data Quality Index for Commercial and Microfinance Segments by Credit Information Companies (RBI/2023-24/62DoR.FIN.REC.39/ 20.16.056/2023-24) dated 20th Sep’23 • Removed CD (Compact Disc) as a manual mode of data submission from section 4.1 Method for Input File Submission. • Removed Annexure A as it pertained to manual data submission in Compact Discs. |
| 3.8 | Nov 2024 | • Updated Section 5 Input File Submission Process, that data / information should be submitted through Secure File Transfer Protocol (SFTP) only. Submission of data through Email, CD, DLT, DAT are not permitted. • As per RBI Letter DOR.SIG.FIN.No./20.16.042/2024 -2025 dated 4th November 2024 made the following changes: ◦ Under 8.9 - Credit type, changed description of catalogue value 9007- “Factoring” to “Factoring with Recourse (Seller)” ◦ Introduction of a new Catalogue Value in 8.9 -Credit Type “9026 – Factoring without Recourse (Buyer)” ◦ Added a comment in the Relationship Segment - For Borrowers with “Factoring without Recourse  (Buyer)” Trades, the Relationship Segment is not mandatory, and its weightage is excluded from the DQI (Data Quality Index) calculation. • As per RBI Circular RBI/2024-25/81 DoR.FIN.REC.47/20.16.042/ 2024-25 dated 10th October 2024 made the following changes: • Added a new note in 7.5 Credit Facility Segment for field no. 4 - Facility / Loan Activation / Sanction Date. |
| 3.9 | Apr 2025 | • Added a new note in 7.5 Credit Facility Segment for field no. 2 – Account Number as per RBI Letter DOR.SIG.No./20.16.042/2025-2026 dated 4th April 2025 |
| 3.10 | Feb 2026 | In light of RBI/DOR/2025-26/119 DoR.FIN.REC. No.326/20.16.056/2025-26 Master Direction and uniform data validations TWG sub-group meetings, following changes are made: • Under 7.1 Header Segment Field No.6 “Information type” renamed to “Reporting cycle” with Catalog values for Reporting Cycle • Under 7.2 Borrower Segment: ◦ Field No.6 “Company Registration Number”, Field No.8 “PAN”, Field No.9 “CIN”, Field No.10 “TIN”, Field No.11 “Service Tax” ,Field No.12 “Udhyam Registration Number” changed from “When Available” to “Required Conditional” ◦ Field No.16 “Class of Activity 1” changed from “Required Conditional” to “Required” ◦ Field No.25 “Credit Rating As on”, Field No 26 “Credit Rating Expiry date”, Field No.27 “CKYC” changed from “When Available” to “Required Conditional”. • Below changes related to Demographics apply to respective fields in 7.3 Address segment,7.4 Relationship Segment and 7.6 Guarantor Segment ◦ “Address Line 1” changed from “Required” to “Required Conditional”. ◦ “Address Line 2”,“Address Line 3”, changed from “When Available” to “Required Conditional”. ◦ “State/Union Territory”, changed from “When Available” to “Required Conditional” ◦ “PIN Code”, changed from “When Available” to “Required Conditional” ◦ “Country”, changed from “When Available” to “Required” ◦ “DUNS Number field” across all segments renamed to “Filler”  • Under 7.4 Relationship Segment following changes are made: ◦ Field No.11 “Company Registration Number”, Field No.21 “DIN”, Field No.22 “TIN” , Field No.23 “Service Tax” , Field No.24 “CKYC”, Field No.37 “Udyam Registration Number” changed from “When Available” to “Required Conditional” • Under 7.5 Credit Facility Segment following changes are made: ◦ Field No. 10 “Drawing Power” changed from “Required “to “Required Conditionally” ◦ Field No.28 “Written Off Amount”, Field No.29 “Settled Amount” changed from “When Available” to “Required Conditional” • Under 7.6 Guarantor Segment following changes are made: ◦ Field No.4 “Business Category” changed from “Required” to Optional ” ◦ Field No.5 “Business/Industry type” changed from “Required” to “Optional” • Field Option “When Available” renamed as “Optional” across all segments • Following Catalogue Masters are updated: ◦ 8.2 Legal Constitution ◦ 8.3 Business Category ◦ 8.5 State code. ◦ 8.8 Credit Type ◦ 8.10 Account Status. ◦ 8.19 Assessment Agency/Authority • Following changes are made in Annexure: ◦ Email Validation annexure changed from “Annexure C” to “Annexure C- PART A) ◦ New Annexure “Annexure C- PART B” added for Mobile/Telephone Validation ◦ New Annexure “Annexure C- PART C” added for Individual Name Validation ◦ Legal Constitution and Relationship Type annexure changed from “Annexure D” to “Annexure D- PART A) ◦ New Annexure “Annexure D- PART B” added for Impermissible Relationships ◦ New Annexure “Annexure D- PART C” added for Identifier ValidationNew Annexure “Annexure D- PART D” added for Account Status description  ◦ New Annexure “Annexure D-Part E” added for State PINcode Mapping. Note: Amendments to the UCRF Guide will be made basis Regulatory directives and/or TWG Sub-Group Recommendations approved by the Regulator. The revised UCRF Guide will be circulated with all the members of the CICs. |
