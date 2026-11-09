# 2. Load once, extract many: data load, source adapters, per-format extracts

Date: 2026-11-08 · Status: proposed (issue #20) · Decisions by the owner on 2026-11-08 marked **(owner)**

## Context

Today each submission type has its own upload, staging tables and hand-written mapping views
(`vw_consumer_*`, `vw_commercial_*`), and each request copies its type's rows into a snapshot. Three problems:

1. **One upload per type.** NeSL and MFI draw on the same borrowers and loans as consumer and commercial; they would
   need the data uploaded again.
2. **Hand-written mapping SQL per type and per client.** The views are near copies of each other; every new client or
   source means writing SQL.
3. **No party identity.** A borrower is `<account number>-<Excel row>`: the same person on two accounts, or as borrower
   and guarantor, is several parties. Formats that think in customers (NeSL, MFI) need one party per person.

What already works and stays: the canonical model (`bureau_core/model.yaml`, the `c_*` tables, one set for every
type), snapshots (one format's records, frozen), rule packs and layouts per format, the file engines, golden files.

## Decision

### The layers

```
SOURCES            STAGING                 CANONICAL                                  EXTRACT              OUTPUT
(per client)       (append only)           current master + facts                     (per format)         (per format)

workbooks,    ─►   stg_* rows, one    ─►   party        by customer ID       ◄─┐      selection from  ─►   snapshot (frozen
LMS views,         table per source        account      by account no. +       │      type.yaml           copy) → rules →
files              template, tagged        (sanction no.)                      ├─     + that date's       layout → file
                   with load_id            account ↔ party + role              │      facts
                                           identifiers, phones, emails,        │
                                           addresses (per party)               │
                                           ─────────────────────────────────── │
                                           account facts per as-of date ───────┘
                                           (balances, DPD, status: LMS view)
```

1. **Staging stays append only** and business-shaped: one table per source template, every upload's rows kept with
   their `load_id`. Staging is the history.
2. **Canonical holds the current master and the facts:**
   - **Demographics are a current master, upserted by key (owner):** a new key is inserted, an existing key is
     overwritten. No SCD 2 in canonical **(owner)**: staging already holds every version with its upload and time; a
     history view can be built from staging if ever needed. Revisit only if a retention policy ever removes old
     staging rows.
   - **Account facts are per as-of date** (balances, overdue, days past due, status), from the LMS view or upload,
     linked to the master by account / sanction number.
3. **An extract is a format's selection** from the master and one date's facts, copied into a **snapshot** (the
   snapshot keeps its own copy: option A). Everything from the snapshot on is unchanged: rules filter on
   `:snapshot_id`, the file is rendered from one snapshot, translations and findings are frozen with it.

### Keys

| Entity | Key | Notes |
|---|---|---|
| Party (person or company) | **customer ID** | from the client's demographics / LMS (owner: present in the data) |
| Account | **account number**, plus **sanction number** where one account has several | links demographics to the LMS facts |
| Account ↔ party | account key + customer ID + **role** | borrower, co-borrower, guarantor, director, partner, … |
| Account facts | account key + as-of date | one row per account per date |

A source without these keys cannot feed the master: "Test source" refuses it with a message.

### Applying an upload to the master (owner decisions)

- **Newest wins, completely:** a customer in the upload gets all fields from the upload. A blank cell **clears** the
  value **(owner)**. The client uploads complete records for the customers in a file.
- **Lists are replaced per customer (owner):** the upload's phones, addresses and identifiers replace that customer's
  whole list (they are corrections; the latest is right).
- **Customers and accounts not in the upload are untouched.** Nothing is deleted; a party or account can be marked
  inactive.
- **Change log:** for each overwritten value, the key, field, old and new value and the upload (`load_id`). Small,
  and it answers "what changed for this customer" in one query.
- **Warning, not a block:** an upload that would clear many previously filled values (a truncated file) is flagged in
  the upload's checks.
- Duplicates inside one upload: an exact repeat is loaded once (as today); the same key with conflicting values is a
  blocking input check.

### Regenerating

Regenerating an older month uses **today's demographics** with that month's facts **(owner)**. Files already generated
keep what was sent: their snapshots are frozen copies.

### Per-format selection

Each format's `type.yaml` gets a `selection:` block: which accounts and which roles the format takes from the master,
for example (illustrative):

```yaml
selection:
  accounts: {borrower_kind: individual}            # consumer: accounts whose primary borrower is a person
  roles: [borrower, co_borrower, guarantor]
```

The extract applies it to the master plus the date's facts. Selection is configuration, like the layout. A format
whose needs exceed the selection keys gets a new key in the product, once.

### Party + role consolidation

`c_guarantor` and `c_relationship` fold into `c_party` with roles on the account ↔ party link. The layouts read
guarantors and related parties through their role. This changes the inside of the model only: **golden files must
stay byte identical**, which proves no bureau file changed.

### Source adapters

1. **Configured adapter (the norm):** a mapping from the client's source columns to canonical fields and from their
   values to canonical codes, set on the Admin page, stored in the client's database, versioned and exportable like
   the other settings (the step 4 configuration store). The app **generates** the staging → canonical SQL
   per database (SQL Server, Postgres) from the mapping. **"Test source"** reads a sample (a file, or the view for a
   date), compares headers and keys with the mapping, and shows what would load.
2. **Plugin adapter (rare):** for a source configuration cannot describe (for example an LMS API). A small package
   written by Open Terrace for that client under `installs/<client>/adapters/`, implementing a fixed, versioned
   contract (return rows in the staging shape for a date). Published only to that client with the release; the core
   loads it by name at start-up and refuses a plugin built for another contract version.

### Rules that keep maintenance low

- **Configure first, plugin rarely, never fork.** When two clients need the same plugin behaviour, it becomes a core,
  configurable feature.
- Every client's mapping is checked when saved; every plugin's tests run against each new core release before it is
  published.
- Each request records the app version, the configuration version and the mapping / adapter version it ran with.

## rarfincare's move

1. Their consumer workbook and commercial sheets become **sources of one load**; the LMS view supplies the facts.
   Their current views become their **first configured mapping**.
2. Customer ID, account number and sanction number are taken from their columns (to confirm per sheet when the mapping
   is written).
3. **Parity:** the new path must produce byte-identical consumer and commercial files for the same inputs before it
   replaces the views (the parity report, as at go-live). Their users see no change in files; the upload page changes
   from "upload per type" to "upload, then choose the formats to validate".

## What does not change

Rule packs and their SQL contract (`:snapshot_id`), layouts, codes, file engines, golden files, snapshots and findings,
the request lifecycle after validation, the deployment (GitHub release, Publish, Deploy).

## Order of work (each a pull request, released through GitHub)

1. Canonical master tables (party, account, link + role, contact lists, facts per date, change log) and migration;
   golden files unchanged (nothing reads them yet).
2. Apply-upload logic (upsert, replace lists, blanks clear, change log, mass-clearing warning), with tests.
3. Extract from master + facts with `selection:` per format; consumer and commercial produce byte-identical goldens.
4. Party + role consolidation in the layouts; goldens unchanged.
5. Configured mapping: storage, generated SQL per dialect, "Test source", Admin page; rarfincare's mapping written;
   parity report identical; switch over.
6. Upload page: one load, then pick formats.
7. Plugin contract and loading (when the first client needs one).

## Open points

- The exact selection keys for NeSL and MFI: when their specifications are in hand.
- Whether facts can also come from an upload (no LMS view): expected yes, same mapping mechanism.
