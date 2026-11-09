# 2. Load once, extract many: layers L0–L4, source adapters, per-format extracts

Date: 2026-11-08 · Status: proposed (issue #20) · Decisions by the owner on 2026-11-08 marked **(owner)**

## Context

Today each submission type has its own upload, staging tables and hand-written mapping views
(`vw_consumer_*`, `vw_commercial_*`), and each validation copies its type's rows straight from those views into the
`c_*` tables. Three problems:

1. **One upload per type.** NeSL and MFI draw on the same borrowers and loans as consumer and commercial; they would
   need the data uploaded again.
2. **Hand-written mapping SQL per type and per client.** The views are near copies of each other; every new client or
   source means writing SQL.
3. **No party identity.** A borrower is `<account number>-<Excel row>`: the same person on two accounts, or as borrower
   and guarantor, is several parties. Formats that think in customers (NeSL, MFI) need one party per person.

What already works and stays: the canonical model (`bureau_core/model.yaml`: the standard shape of the data), the
snapshot tables (`c_*`), rule packs and layouts per format, the file engines, golden files.

## The two kinds of data (owner)

Every submission file is made of:

| | **Demographics** | **Account position** |
|---|---|---|
| What | Customer details, identifiers, contacts, addresses; the **sanction** (amount, date, type, borrower, co-borrowers, **guarantors, directors, partners**); **securities** | Principal outstanding, overdue amount, days past due, status, last payment, closure |
| Changes | Rarely: corrections and updates | Every day |
| Source | Demographics uploads (workbooks) | The LMS: a view in the client's database, **or** Excel uploads |

## Decision: the layers

| Label | Name | What it is | Kept how |
|---|---|---|---|
| **L0** | **Sources** | The client's data: workbooks, LMS view, files | The client's |
| **L1** | **Staging** | Uploads as received, column for column, one table per source template | **Append only**: every upload kept with its `load_id` (the history) |
| **L2a** | **Demographics master** (canonical) | Current customers, sanctions, roles, securities, contacts | **Overwrite**: newest upload wins |
| **L2b** | **Account position** (canonical) | Each account's position per as-of date | **A view, no copy**: over the client's LMS view, or over L1 staging |
| **L3** | **Submission snapshot** | One format × one as-of date × one validation run: the records the format selects, frozen | **Frozen**: never changes once made |
| **L4** | **Output files** | The files generated from one L3 snapshot, one per bureau | **Append only** in storage, with a fingerprint |

```
L0 workbooks ──► L1 Staging ──────────► L2a Demographics master ──┐
                                          (current, overwrite)     │
L0 LMS Excel ──► L1 Staging ─┐                                     ├─► EXTRACT ──► L3 Submission snapshot ──► L4 Output files
                             ├─► L2b Account position (view) ──────┘  (format X,     (frozen copy; rules,      (one per bureau,
L0 LMS view ─────────────────┘     (per as-of date)                    date D)         findings, adjustments)   kept forever)
```

### L1 Staging

Unchanged in spirit: business-shaped, one table per source template, append only. Demographics uploads and (for
clients without an LMS view) LMS Excel uploads both land here. Staging is the history: no layer above keeps its own.

### L2a Demographics master

**Keys:**

| Entity | Key |
|---|---|
| Party (person or company) | **customer ID** (owner: present in the client's data) |
| Sanction / account | **account number**, plus **sanction number** where one account has several |
| Account ↔ party | account key + customer ID + **role** (borrower, co-borrower, guarantor, director, partner, …) |
| Security | account key + security identifier |

A source without these keys cannot feed L2a: "Test source" refuses it with a message.

**Applying an upload (owner):**

- **Overwrite, no SCD 2.** Staging (L1) already holds every version with its upload and time; a history view can be
  built from it if ever needed. Revisit only if a retention policy ever removes old staging rows.
- **Newest wins, completely:** a customer in the upload gets all fields from it; a blank cell **clears** the value.
  The client uploads complete records for the customers in a file.
- **Lists are replaced per customer:** the upload's phones, addresses and identifiers replace that customer's whole
  list (they are corrections; the latest is right).
- Customers and sanctions not in the upload are untouched. Nothing is deleted; a party or account can be marked
  inactive.
- **Change log:** per overwritten value, the key, field, old and new value and the upload (`load_id`).
- **Warning, not a block:** an upload that would clear many previously filled values (a truncated file).
- Inside one upload: an exact repeat is loaded once; the same key with conflicting values blocks the upload.

**Party + role:** guarantors, directors and partners are parties with a role on the account ↔ party link; the
separate `c_guarantor` and `c_relationship` shapes fold into this. The layouts read them by role. Golden files must
stay byte identical (proof that no bureau file changed).

### L2b Account position

One canonical contract, `automn.vw_account_position` (account key, as-of date, principal outstanding, overdue amount,
days past due, status, …), filled in one of two ways per client:

| Client has | L2b is | Data load |
|---|---|---|
| **An LMS view** (history by date in their database) | A view over their view, with the mapping applied | **None**: their view is the history (rarfincare today) |
| **LMS Excel uploads** | A view over the L1 staging table, with the mapping applied: **the newest successful upload per account and date wins** (a re-upload for a date is a correction) | Upload to L1 only |

No second copy of positions is stored in canonical: in both cases L2b is a view, so one code path serves both. If
reading through the view ever proves too slow, L2b can be **materialised** (mapped rows written to a table at upload)
without the extract noticing; start with the view, measure, switch only if needed.

**The LMS wins (owner):** for any field both the demographics and the account position hold, the account position's
value is used.

### Extract → L3 Submission snapshot

For format X and as-of date D, the extract:

1. takes **L2a** as it is now;
2. keeps what format X selects (`selection:` in the format's `type.yaml`, e.g. consumer: individual borrowers and their
   accounts, guarantors, contacts);
3. joins each selected account with its **L2b** position for date D (the LMS wins);
4. **copies** the result into the snapshot tables (`c_*`) under a new `snapshot_id`.

From then on the **L3 submission snapshot** is what everything works on: rules (`:snapshot_id`), findings, the user's
exclusions and overrides, every bureau's file (identical data even when generated days apart), the findings pages and
the review Excel. A new upload changes L2a, never an existing snapshot.

**Regenerating** an older month uses **today's** demographics with that month's positions **(owner)**. Files already
generated keep what was sent (L3 and L4 are frozen).

Selection example (illustrative):

```yaml
selection:
  accounts: {borrower_kind: individual}
  roles: [borrower, co_borrower, guarantor]
```

### L4 Output files

Kept forever in storage (owner, 2026-11-06). Each file also gets a fingerprint (SHA-256, size, record count) recorded
with its request, and the request a "submitted" status (who, when, bureau, acknowledgement reference): separate small
step.

## Source adapters

1. **Configured adapter (the norm):** per client and per kind of data (demographics; account position), the source
   kind (Excel upload or view) and the mapping from their columns to canonical fields and from their values to
   canonical codes. Set on the Admin page, stored in the client's database, versioned and exportable like the other
   settings (the step 4 configuration store). The app **generates** the L1 → L2 SQL per database (SQL Server,
   Postgres). **"Test source"** reads a sample (an uploaded file, or the view for a date), checks headers and keys,
   and shows what would load.
2. **Plugin adapter (rare):** for a source configuration cannot describe (for example an LMS API). A small package
   written by Open Terrace for that client under `installs/<client>/adapters/`, implementing a fixed, versioned
   contract (rows in the L1 shape for a date). Published only to that client; the core loads it by name and refuses a
   plugin built for another contract version.

**Rules that keep maintenance low:** configure first, plugin rarely, never fork (two clients needing the same plugin
behaviour → a core, configurable feature). Every mapping is checked when saved; every plugin's tests run against each
new core release before it is published. Each request records the app version, the configuration version and the
mapping / adapter version it ran with.

## rarfincare's move

1. Their consumer workbook and commercial sheets become **demographics sources** (L1 → L2a); their LMS view
   `wip.VwConsumerSubmissionAccountSegment` becomes their **L2b account position** (no load). Their current views
   become their first configured mapping.
2. Customer ID, account number and sanction number come from their columns (confirmed per sheet when the mapping is
   written).
3. **Parity:** the new path must produce byte-identical consumer and commercial files for the same inputs before it
   replaces the views (the parity report, as at go-live). Users see no change in files; the upload page changes from
   "upload per type" to "upload, then choose the formats to validate".

## What does not change

Rule packs and their contract (`:snapshot_id`), layouts, codes, file engines, golden files, the snapshot tables and
findings (L3), the request lifecycle after validation, the deployment (GitHub release, Publish, Deploy).

## Order of work (each a pull request, released through GitHub)

1. L2a demographics master tables (party, sanction / account, link + role, securities, contact lists, change log) and
   migration; golden files unchanged (nothing reads them yet).
2. Applying an upload to L2a (upsert, replace lists, blanks clear, change log, mass-clearing warning), with tests.
3. L2b account position view contract, from a view and from L1 staging.
4. Extract from L2a + L2b with `selection:` per format into L3; consumer and commercial produce byte-identical goldens.
5. Party + role in the layouts; goldens unchanged.
6. Configured mapping: storage, generated SQL per dialect, "Test source", Admin page; rarfincare's mapping written;
   parity report identical; switch over.
7. Upload page: one load, then pick formats.
8. Plugin contract and loading (when the first client needs one).

## Open points

- The exact selection keys for NeSL and MFI: when their specifications are in hand.
- rarfincare's customer ID / sanction number columns per sheet.
