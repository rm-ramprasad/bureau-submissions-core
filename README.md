# bureau-submissions-core

Credit bureau submission files (CIBIL, CRIF High Mark, Experian, Equifax; consumer TUDF and commercial delimited)
from an NBFC's data: cloud-neutral layers in thin per-cloud shells. Design: [docs/requirements.md](docs/requirements.md).

## Packages

| Package | Layer | May import |
|---|---|---|
| `bureau_core` | 3: contract, types, validation, file engines | stdlib, PyYAML, openpyxl (SQLite via SQLAlchemy for validation) |
| `canonical` | 2: staging, input checks, mapping, generated views, snapshot | `bureau_core` |
| `sources` | source adapters (Excel/CSV, database views) | `canonical` |
| `clientconfig` | versioned client settings, audit, approval | none of the layers |
| `app` | workflow, jobs, Flask API, ports | the above; **no cloud SDK** |
| `shell_azure` | Functions entry points, Azure adapters | `app`, `azure.*` |

`shell_aws` is added the same way when AWS starts. The rules are enforced by `lint-imports` in CI.

## Develop

```
uv sync                      # Python 3.14 + all packages (editable) + dev tools
uv run pytest                # unit + regression tests
uv run pytest --cov          # with the coverage gate
uv run pre-commit install    # checks before each commit
```

Tests that need the input workbooks are marked `workbook`. The workbooks are never committed: set
`BSC_WORKBOOK_DIR` to the local folder holding them to run those tests.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the branch and pull-request rules.
