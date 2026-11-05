# Contributing

- **Branches:** short-lived, one per step or sub-step (`step-1/extract-canonical`). `main` is protected.
- **Pull requests:** small (a few hundred lines), CI green, the template filled in. The owner reviews and merges
  (squash). Titles follow Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `ci:`, `docs:`).
- **Tests:** every change comes with tests. Coverage never drops below the gate in `pyproject.toml`.
  Golden files (bureau output, byte for byte) change only with the reason stated in the PR.
- **Data:** no real or workbook data in git (`*.xlsx`, `*.csv` are gitignored and blocked by pre-commit).
  CI uses synthetic fixtures built in code; workbook tests run locally (`-m workbook`).
- **Secrets:** none in the repository; gitleaks runs on every PR.
- **Layers:** a new import across packages must satisfy `lint-imports`; cloud SDKs only in `shell_*`.
- **Decisions:** a significant design choice gets a short record in `docs/adr/`.
