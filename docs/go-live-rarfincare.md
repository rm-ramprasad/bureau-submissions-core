# Go-live on rarfincare: from the old app to this repository

For the owner, on the Windows machine that deploys today. About 1–2 hours. Nothing here creates or deletes an Azure
resource: the new code is published to the **same** Function App and Static Web App, with the **same** app settings.
The previous app's code stays deployable until the end (step 6 is the way back).

## 0. Once: tools and the new folder

1. **Install uv** (a program, installed once on the machine like `az` or `git`, not inside any project folder).
   In any PowerShell: `winget install --id=astral-sh.uv -e`
   (or `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`), then open a **new**
   PowerShell and check `uv --version`. The other tools are the ones the old deploy already uses: `az`, `func`
   (Core Tools v4), Node / `npx`, ODBC Driver 18.
2. **Get a copy of the code for deploying**, in any folder you like (e.g. `D:\deploy`). It is a clean copy of what
   is merged on GitHub (`main`), used only to check and deploy; `git pull` in it before each deploy. (The folder
   this container works in is not used for deploying: its `.venv` belongs to the container.)

   ```
   cd D:\deploy
   git clone https://github.com/rm-ramprasad/bureau-submissions-core.git
   cd bureau-submissions-core
   uv sync
   ```

   `uv sync` fetches Python 3.14 if needed and installs everything into `.venv` inside this folder only. Run every
   step below from this folder. Your `.env.sql` stays where it is, outside the repository; step 1 only needs its
   path.

## 1. Parity check (before anything changes)

Pick two **finished** requests made by the old app: one consumer with files generated, one commercial with files
generated (their request ids are on the Requests page, e.g. `20261102083455-327be1`). In a terminal in the repo:

```
$env:SUBMISSIONS_SQL_ENV_FILE = "<path to your .env.sql>"     # the same login file as today; never copy it into the repo
$env:SUBMISSIONS_TENANT = "rarfincare"
$env:SUBMISSIONS_STORAGE_ACCOUNT_URL = "https://bsubst73vcgqid4kh3c.blob.core.windows.net"
az login
uv run python -m shell_azure.parity <consumer request id>
uv run python -m shell_azure.parity <commercial request id>
```

- It rebuilds each request's snapshot from the **same upload and date** with the new code (with the same exclusions
  and overrides), compares every row, outcome, finding and bureau file with the old app's, prints a report, and
  deletes its temporary snapshot. It writes nothing the pages show.
- If reading the old files from Blob storage is refused (your account may not have read access), download the
  request's files from the Files page into a folder and add `--old-files <folder>`.
- To keep the report: add `2>&1 | Out-File -Encoding utf8 <file>.txt` to the command.
- **Expected:** `RESULT: IDENTICAL` for both. `RESULT: INCOMPLETE` means no differences, but some old files were not
  in the folder: add them and run again. A "header line differs" note on a TUDF is expected (creation date/time).
- **Anything else:** stop and paste the report into the chat. Do not deploy.

Use a request made **today**, just before the check: the views read the live LMS view, and the view scripts may
have changed since an older request ran, so an older request differs for those reasons alone (seen on go-live day).

## 2. Deploy the new code

```
.\deploy\deploy.ps1 -FunctionOnly
```

The screens did not change, so `-WebOnly` is not needed. The app settings stay as they are
(`SUBMISSIONS_VALIDATION_ENGINE` is simply no longer read).

## 3. Admin job: migrate

```
.\deploy\ops.ps1 migrate
```

Expected: `"applied": []` (no new migrations in this move), reference rows unchanged, `"bureau_profiles_added": 0`.

New migrations (files under `packages/canonical/src/canonical/migrations/azure/`) are run by the owner in SSMS with
the admin login; `migrate` only checks and records them. The app identity has SELECT/INSERT/UPDATE/DELETE/EXECUTE
on schema `automn` and nothing more (by design), so running a new migration through `migrate` answers
`"failed": "<version>"` with "CREATE TABLE permission denied" (or similar). For each new migration:

1. Run `<version>.sql` in SSMS (admin login) against rarfinmisdb.
2. Record it and migrate again: `.\deploy\ops.ps1 migrate --mark-applied <version>`.

The same applies if the error says "already exists": the migration was run by hand earlier without its history row
(this happened with 006 on go-live day). Check in SSMS that its change is there, then step 2.

## 4. Smoke test (10 minutes)

1. Open the site, sign in: the dashboard loads, the Requests list shows the old requests.
2. New request, consumer, a recent month end: upload `Consumer_Test_Coverage.xlsx` → checks shown → submit →
   validated → generate CIBIL → the three files download (bureau Excel, TUDF, review Excel).
3. The same for commercial with its workbook → the bureau file and review Excel download.
4. Open a TUDF in the File viewer.

## 5. Tell Claude

Share the parity reports (step 1), the migrate output (step 3) and anything odd from the smoke test.

## 6. The way back (only if something is wrong after step 2)

Redeploy the previous app's code with its own deploy script, as before (`deploy.ps1 -FunctionOnly`).
Nothing in the database changed format, so the old app works on everything the new one wrote.
