# Bureau Submissions: deployment repository

This repository deploys **Bureau Submissions** releases to this organisation's Azure. It holds no source code: Open
Terrace publishes each release here (Releases tab: `function.zip`, `web.zip`, `migrations.zip` and notes) together with
this README, the Deploy workflow and `install.json` (this install's Azure names; identifiers, not secrets).

Do not edit these files here: the next publish replaces them. Changes go through Open Terrace.

## Deploying a release

1. Releases tab: open the release (e.g. `v1.2.0`) and read its notes.
2. **If the notes list new migrations**, run each file from `migrations.zip` in SSMS against the app database (admin
   login) first.
3. Actions > **Deploy** > Run workflow (branch `main`): `version` = the release, `confirm` = the release again,
   `mark_applied` = the migrations you ran (comma-separated, as in the notes), or empty.
4. The run publishes the Function App, waits until it answers, records the migrations, checks the Function App runs
   that version, publishes the pages and runs a smoke test. It stops at the first failure.

Rolling back: deploy the previous release the same way (migrations only add, so older code runs on a newer database).

## Access

Azure trusts only this repository's `main` branch (a federated credential on the managed identity
`bsub-github-deploy`: no stored Azure secrets). Anyone with write access here can deploy: keep it to the people who
should.
