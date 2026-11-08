# Deploying from GitHub: rarfincare runbook

How Bureau Submissions gets from Open Terrace's source code to rarfincare's Azure: what was set up (and why), the
steps for each release, the permissions involved, and what to do when something goes wrong. The generic process for
any client is in [release.md](release.md); this page is rarfincare's record.

## 1. The picture

```
Open Terrace GitHub                                   rarfincare GitHub
rm-ramprasad/bureau-submissions-core                  birarfincare/bureau-submissions-deploy
(all source code, PRs, tests)                         (no source code)

  1. Tag a version (v1.0.0)
     -> Release workflow: checks, builds once
        function.zip, web.zip, migrations.zip
  2. Publish workflow (version, client) ---- copies -->  Release v1.0.0 + Deploy workflow,
                                                         scripts, README, install.json
                                                      3. Deploy workflow
                                                         -> signs in to Azure (no password)
                                                         -> rarfincare's Function App + pages
```

- **Source code stays with Open Terrace.** rarfincare's repository only receives built packages.
- **One build per version.** Every client gets exactly the same tested files.
- **No stored Azure secrets.** GitHub proves who it is to Azure with a short-lived token (OIDC); Azure trusts only
  `birarfincare/bureau-submissions-deploy`, branch `main`.
- `deploy\deploy.ps1` (from the deploy machine) still works as a fallback.

## 2. What was set up (once, 2026-11-08)

| Where | What | Value |
|---|---|---|
| rarfincare GitHub | Private deployment repository | `birarfincare/bureau-submissions-deploy` |
| rarfincare GitHub | Fine-grained token `open-terrace-publish` (lets Open Terrace publish into the repository above only) | Contents + Workflows: read and write; expires after 1 year: **renew before expiry** |
| Open Terrace GitHub | Actions secret in `rm-ramprasad/bureau-submissions-core` | `INSTALL_TOKEN_RARFINCARE` = the token above |
| rarfincare Azure | Managed identity (the account GitHub signs in as) | `bsub-github-deploy` in `rg-bureau-submissions`; client ID `9e22ef18-ee10-4ef3-852c-12d87ac9d01d` |
| rarfincare Azure | Federated credential on it | `github-main`: trusts `repo:birarfincare/bureau-submissions-deploy:ref:refs/heads/main` only |
| rarfincare Azure | 3 role assignments | see section 3 |
| rarfincare GitHub | Actions variables on the deployment repository | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` (`4592988e-…`), `AZURE_SUBSCRIPTION_ID` (`a8519406-…`) |
| Open Terrace repo | Install record | `installs/rarfincare/install.json`: repository, resource group, Function App, Static Web App, sign-in IDs |

Made with `deploy\setup-github-oidc.ps1 -Client rarfincare -Subscription RarFinance` (run first with `-Preview`).
The logs of both runs are on the deploy machine in `deploy\logs\` (keep them with the audit records). Azure's own
record: portal > `rg-bureau-submissions` > **Activity log** (kept 90 days; download as CSV to keep longer).

Cost: none (managed identities, federated credentials and role assignments are free).

## 3. Permissions (identity `bsub-github-deploy`)

| Role | On | Used for | Cannot |
|---|---|---|---|
| Website Contributor | Function App `bsub-func-73vcgqid4kh3c` (API and jobs) | Upload the app code; read the function key to call the migrate step | Touch the database, storage data, other apps, role assignments |
| Contributor | Static Web App `bsub-web-73vcgqid4kh3c` (the pages) | Read the deployment token; publish the pages | Anything outside this Static Web App |
| Reader | Resource group `rg-bureau-submissions` | Look the resources up (e.g. the site address for the smoke test) | Change anything |

**Not granted:** the SQL database `rarfinmisdb`, the storage account (uploaded and generated files), Entra ID, networking,
other resource groups or subscriptions. Database migrations are still run by a person in SSMS.

Check in the portal: Function App or Static Web App > **Access control (IAM)** > **Role assignments**. Or:
`az role assignment list --assignee 9e22ef18-ee10-4ef3-852c-12d87ac9d01d --all -o table`.

## 4. Each release

1. **Merge** the pull requests for the release into `main` of `rm-ramprasad/bureau-submissions-core` (all checks green;
   the `protect-main` rule blocks merging otherwise).
2. **Tag the version** in `rm-ramprasad/bureau-submissions-core`: **Releases** > **Draft a new release** > **Choose a tag**
   > type the version (`v1.0.0`, then `v1.1.0`, …) > **Create new tag** > target `main` > **Publish release**.
   **Actions** > **Release** runs (about 10 minutes). When green, the release has `function.zip`, `web.zip`,
   `migrations.zip` and notes.
3. **Read the release notes.** They say "No new migrations" or list migration names.
4. **Publish to rarfincare**: same repository, **Actions** > **Publish** > **Run workflow**: version, client
   `rarfincare`. When green, the release is in `birarfincare/bureau-submissions-deploy`.
5. **Only if the notes list migrations:** run each file (from `migrations.zip`, or
   `packages/canonical/src/canonical/migrations/azure/`) in **SSMS** against `rarfinmisdb` with the admin login.
   **Before** step 6: the new code may need them.
6. **Deploy** in `birarfincare/bureau-submissions-deploy`: **Actions** > **Deploy** > **Run workflow** (branch `main`):
   - `version`: the tag, e.g. `v1.1.0`
   - `confirm`: the tag again
   - `mark_applied`: the migrations you ran in step 5, comma-separated (e.g. `010_x,011_y`), or empty
7. The run (about 10 minutes) uploads the Function App code, waits until it answers, records the migrations, checks the
   Function App runs that version, publishes the pages with rarfincare's sign-in settings, and runs a smoke test (pages,
   `config.json`, API). It stops at the first failure. Last line: `Deployed vX.Y.Z: pages, sign-in settings and API OK`.
8. **Check yourself:** open the site, sign in, look at the dashboard.

Versions: `vMAJOR.MINOR.PATCH`: PATCH for fixes, MINOR for features, MAJOR when an install needs more than a deploy.

## 5. Rolling back

Run **Deploy** again with the previous version (e.g. `v1.0.0`), `mark_applied` empty. Migrations only add tables and
columns, so older code runs on a newer database: nothing to undo in the database.

## 6. When something fails

| Symptom | Cause | Fix |
|---|---|---|
| A PR shows no merge button | The `protect-main` rule: a required check is red | Open the PR's **Checks**; if it failed downloading tools (504, timeout), **Re-run failed jobs**; otherwise send the log |
| Publish: `Secret INSTALL_TOKEN_RARFINCARE is not set` | Secret missing in the Open Terrace repository | Add it (section 2) |
| Publish: 401 / 403 / `Bad credentials` | Token expired or lacks access to the repository | birarfincare > Settings > Developer settings > Fine-grained tokens: regenerate or fix **Repository access**; update the secret |
| Deploy: `AADSTS70021` / `No matching federated identity` | Run from another branch, or the repository was renamed | Run from `main`; the repository must be `birarfincare/bureau-submissions-deploy` (or add a federated credential for the new name) |
| Deploy: `AuthorizationFailed` | Roles missing or not yet applied | Wait 5 minutes after setup; check section 3 |
| Deploy: `The confirmation … does not match` | `confirm` differs from `version` | Run again with both the same |
| Deploy: `Migration … failed` | A new migration was not run in SSMS, or `mark_applied` missed it | Run it in SSMS, deploy again with its name in `mark_applied` |
| Deploy: `The Function App runs 'dev', not 'vX'` | The upload did not take effect | Re-run; if it repeats, send the log (fallback: `deploy\deploy.ps1`) |
| Deploy: `The API did not come up in 5 minutes` | App start failed after the upload | Log Analytics `bsub-log-73vcgqid4kh3c` (AppExceptions); roll back (section 5) |

The site keeps running the previous version when a deploy fails before the upload; after the upload, roll back.

Lessons from the first setup (2026-11-08): the setup's GitHub step failed until the deployment repository existed under
exactly the name in `install.json` (it had been created as `bureau-submisisons-core` and was renamed); GitHub's own
download servers timed out for a morning, which CI now avoids (GitHub's Python, `uv` from PyPI).

## 7. Maintenance

- **Token expiry:** renew `open-terrace-publish` before it expires and update `INSTALL_TOKEN_RARFINCARE`.
- **Who can deploy:** anyone with write access to `birarfincare/bureau-submissions-deploy` (GitHub Free has no approval
  step for private repositories). Keep that list short.
- **Undo the setup:** delete the identity `bsub-github-deploy` in `rg-bureau-submissions`, remove its three role
  assignments (Access control (IAM) on each resource), delete the secret and the token.
- **Another client:** see [release.md](release.md), "New client".
