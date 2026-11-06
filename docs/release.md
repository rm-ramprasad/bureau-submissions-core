# Releasing and deploying

Step 9 (issue #21). The source lives only in the Open Terrace repository (`rm-ramprasad/bureau-submissions-core`).
Each client has a small **deployment repository** in their own GitHub (rarfincare: `birarfincare/bureau-submissions-deploy`)
with no source code: Open Terrace publishes releases there, and the client's Azure trusts only that repository.

```
Open Terrace: bureau-submissions-core                 Client: <client>/bureau-submissions-deploy
  tag v1.2.0 -> Release workflow builds the zips          Releases: v1.2.0 (function.zip, web.zip, migrations.zip)
  Publish workflow (version, client)  ---- copies --->    .github/workflows/deploy.yml, scripts/, README, install.json
                                                          Deploy workflow -> client's Azure (OIDC, main branch only)
```

- `installs/template/`: what every deployment repository holds (Deploy workflow, scripts, README).
- `installs/<client>/install.json`: that client's repository and Azure names (identifiers, not secrets).
- `deploy/deploy.ps1` stays as the fallback from a workstation.

GitHub Free has no approval step for private repositories: Deploy runs only from `main`, only for people with write
access to the deployment repository, and only when the version is typed twice.

## New client (once)

1. In the client's GitHub, create an empty **private** repository, e.g. `bureau-submissions-deploy`.
2. Add `installs/<client>/install.json` here (copy rarfincare's; set `repo` and the Azure names) and `<client>` to the
   `client` choices in `.github/workflows/publish.yml`, by pull request.
3. **Token for publishing.** Signed in to GitHub as the client account: Settings > Developer settings > Personal
   access tokens > **Fine-grained tokens** > Generate new token. Resource owner: the client account; Repository
   access: **Only select repositories** > the deployment repository; Permissions: **Contents: Read and write**,
   **Workflows: Read and write**; expiry as you prefer (renew it before it ends). Copy the token.
4. In **this** repository: Settings > Secrets and variables > Actions > New repository secret: name
   `INSTALL_TOKEN_<CLIENT>` (e.g. `INSTALL_TOKEN_RARFINCARE`), value the token.
5. Signed in with `az login` (the client's Azure) and `gh auth login` (an account with admin rights on the deployment
   repository), review and run `.\deploy\setup-github-oidc.ps1 -Client <client> -Subscription <subscription>`.

## Each release

1. Merge the pull requests for the release into `main` (CI green).
2. Here: Releases > Draft a new release > tag `v1.4.0` on `main` > Publish. The **Release** workflow checks, builds and
   attaches the zips; its notes list any new migration.
3. Here: Actions > **Publish** > Run workflow: version `v1.4.0`, client. The release appears in the client's
   deployment repository.
4. **New migrations?** Run each file in SSMS against the client's database (admin login) **before** deploying.
5. In the client's deployment repository: Actions > **Deploy** > Run workflow (branch `main`): `version` = the tag,
   `confirm` = the tag again, `mark_applied` = the migrations you ran (comma-separated), or empty.
6. The run publishes the Function App, waits until it answers, records the migrations, checks the Function App runs
   the tagged version, publishes the pages (with this install's sign-in settings) and checks pages and API. It stops
   at the first failure.

## Rolling back

Deploy the previous tag the same way. Migrations only add (tables, columns), so older code runs on a newer database.

## Versions

`vMAJOR.MINOR.PATCH`: PATCH for fixes, MINOR for features, MAJOR when an install needs more than a deploy (e.g. a
manual step). The deployed version is reported by `ops.ps1 migrate` (`"version"`).
