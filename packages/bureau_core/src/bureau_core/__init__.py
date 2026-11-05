"""bureau_core: the portable core of bureau submission files (docs/ROADMAP.md, "Portable core"). The same package runs
under every host (Azure today; AWS, GCP, a command line): what it takes, checks and writes never depends on the host.

No database driver, SQLAlchemy, web framework or cloud SDK, and nothing from a host app (tests/test_core_boundary.py).

- contract.py   CanonicalBatch (the one input every source produces) and ValidationResult, as JSON
- data.py       SnapshotData / Drops: what the format engines render from
- canonical.py  the canonical model (model.yaml): entities, columns, code lists
- types/        per submission type: codes.yaml, requirements.yaml, rules.yaml, layout.yaml, excel_layout.yaml
- formats/      the generic engines: TUDF (consumer), delimited (commercial), Excel (review / Data Submission Form)
- tudf/         the consumer bureau Excel -> TUDF converter and its reconciliation (source kind c)
- bureau.py, enums.py, checks/, text.py   bureau profile, codes, check functions, cell text

A bureau requirement is a change here (usually YAML plus a golden file), released once for every NBFC and host.
"""

__version__ = "0.1.0"
