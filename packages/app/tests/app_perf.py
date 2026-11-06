"""Synthetic large batches for performance work: a golden batch (captures of the reference's mock data) copied N times,
each copy with its own account, borrower and reference keys, so a 100,000-record file can be built without real data.

    uv run python packages/app/tests/app_perf.py consumer 10000      # time each stage for ~10,000 records
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from app_helpers import golden

from bureau_core.contract import CanonicalBatch

KEY_COLUMNS = ("account_key", "party_key", "related_key", "account_number", "entity_key")


def scaled_batch(name: str, records: int) -> CanonicalBatch:
    """The golden batch `name` copied until it has at least `records` records (account x borrower)."""
    batch, result = golden(name)
    per_copy = len(result.outcomes)
    copies = max(1, -(-records // per_copy))
    entities: dict[str, list[dict[str, Any]]] = {}
    for entity, rows in batch.entities.items():
        out = []
        for i in range(copies):
            for r in rows:
                row = dict(r)
                row["row_id"] = r["row_id"] * copies + i + 1 if "row_id" in r else None
                for k in KEY_COLUMNS:
                    if row.get(k):
                        row[k] = f"{row[k]}{i:05d}"
                out.append(row)
        entities[entity] = out
    return CanonicalBatch(
        batch.submission_type, batch.as_of_date, batch.canonical_version, entities, batch.source, batch.exceptions
    )


def peak_mb() -> int:
    """The process's peak resident memory so far (Linux), in MB."""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


@dataclass
class Timings:
    stages: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        yield
        self.stages[name] = time.perf_counter() - start
        self.peaks[name] = peak_mb()

    peaks: dict[str, int] = field(default_factory=dict)

    def text(self) -> str:
        return "\n".join(
            f"  {k:28} {v:8.1f} s   peak memory so far {self.peaks.get(k, 0):6d} MB" for k, v in self.stages.items()
        )


def run_stages(name: str, records: int, out_dir: Path, *, progress: Callable[[str], None] = print) -> Timings:
    """Load a scaled batch into a fresh SQLite app database, validate it and generate one bureau's files, timing
    each stage."""
    from app_helpers import load_snapshot

    from app.bureaus import BureauRepository
    from app.bureaus.seed import seed_missing
    from app.enums import BureauCode, ReportingCycle, RunMode
    from app.pipeline import Services, get_pipeline
    from app.ports.files import LocalFileStore
    from app.requests import RequestRepository
    from app.validation.run import validate_snapshot
    from app.validation.settings import load_rule_set
    from canonical.db import metadata
    from canonical.reference import seed as seed_reference
    from canonical.snapshot import SnapshotRepository
    from canonical.tenants import TenantRepository
    from canonical.testing import TENANT

    t = Timings()
    with t.stage("make synthetic batch"):
        batch = scaled_batch(name, records)
    progress(f"{name}: {sum(len(v) for v in batch.entities.values())} rows")
    submission_type = batch.submission_type
    from canonical.db import make_engine

    engine = make_engine(f"sqlite:///{out_dir / 'perf.db'}")  # on disk, as the real database is not in the app
    import app.adjustments.tables
    import app.validation.tables  # noqa: F401

    metadata.create_all(engine)
    TenantRepository(engine).upsert(TENANT)
    seed_reference(engine)
    bureaus = BureauRepository(engine)
    seed_missing(bureaus, TENANT.tenant_id)
    with t.stage("load snapshot (c_* rows)"):
        sid = load_snapshot(engine, batch)
    submission_type, as_of = batch.submission_type, batch.as_of_date
    del batch  # the real worker never holds the source rows: they go from the views into the database
    import gc

    gc.collect()
    snapshots = SnapshotRepository(engine)
    with t.stage("validate (core)"):
        summary = validate_snapshot(engine, snapshots, sid, load_rule_set(engine, TENANT.tenant_id, submission_type))
    progress(f"  records {summary.records}, accepted {summary.accepted}")
    requests = RequestRepository(engine)
    gen = requests.create_batch(
        TENANT.tenant_id, as_of, RunMode.GENERATE, [submission_type], [BureauCode.CIBIL],
        reporting_cycle=ReportingCycle.MONTH_END, snapshot_id=sid,
    )  # fmt: skip
    (row,) = requests.list_batch(gen)
    services = Services(engine, engine, snapshots, LocalFileStore(out_dir))
    pipeline = get_pipeline(submission_type)
    assert pipeline is not None
    with t.stage("generate files (CIBIL)"):
        pipeline.generate_file(
            services, TENANT, row, snapshots.get(sid), bureaus.get(TENANT.tenant_id, "CIBIL", submission_type)
        )
    return t


if __name__ == "__main__":
    import tempfile

    kind, n = sys.argv[1], int(sys.argv[2])
    with tempfile.TemporaryDirectory() as d:
        timings = run_stages(f"{kind}_nbfc_001", n, Path(d))
    print(timings.text())
