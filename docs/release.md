# Releasing and deploying

Step 9 (issue #21). One release is built once on GitHub and deployed to each install from GitHub, with the owner's
approval and a smoke test. `deploy/deploy.ps1` stays as the fallback from a workstation.

## One-time setup (owner)

Review, then run `.\deploy\setup-github-oidc.ps1 -Reviewer <your GitHub login>` (signed in with `az login` and
`gh auth login`). It creates a managed identity that GitHub signs in as (no stored secrets; it is trusted only for this
repository's `rarfincare` environment), gives it publish rights on the Function App and the Static Web App only, and
creates the `rarfincare` environment on GitHub with you as the required reviewer.

## Each release

1. Merge the pull requests for the release into `main` (CI green).
2. Tag `main` with the next version and push the tag, e.g. on GitHub: Releases > Draft a new release > choose a tag
   `v1.4.0` on `main` > Publish (or `git tag v1.4.0 && git push origin v1.4.0`). The **Release** workflow checks,
   builds and attaches `function.zip`, `web.zip` and `migrations.zip` to the release, with notes that list any new
   migration.
3. **New migrations?** Run each file in SSMS (rarfinmisdb, admin login) **before** deploying: the new code may need
   them. The release notes list them.
4. Actions > **Deploy** > Run workflow: `version` = the tag; `mark_applied` = the migrations you ran (comma-separated,
   as in the notes), or empty. Approve the run when GitHub asks (environment `rarfincare`).
5. The run publishes the Function App, waits until it answers, records the migrations, checks that the Function App
   runs the tagged version, publishes the pages (with this install's sign-in settings) and checks the pages and API.
   It stops at the first failure.

## Rolling back

Deploy the previous tag the same way. Migrations only add (tables, columns), so older code runs on a newer database.

## Versions

`vMAJOR.MINOR.PATCH`: PATCH for fixes, MINOR for features, MAJOR when an install needs more than a deploy (e.g. a
manual step). The deployed version is reported by `ops.ps1 migrate` (`"version"`).
