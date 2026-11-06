"""Performance regression (marked `perf`; the nightly workflow runs it). A golden batch scaled to PERF_RECORDS
records goes through the real stages; each stage must stay within its budget: the measured time / memory at that
size plus 20 % (issue #14). Measured at 1 lakh records on 2026-11-06 (clean process per stage): consumer validation
0.66 GB / 43 s, generation 1.12 GB / 6.4 min; commercial 0.77 GB / 45 s and 1.18 GB / 1.3 min.

    uv run pytest -m perf packages/app/tests/test_perf.py -s
"""

import os
import tempfile
from pathlib import Path

import pytest
from app_perf import run_stages

pytestmark = pytest.mark.perf
RECORDS = int(os.environ.get("PERF_RECORDS", "10000"))
# stage -> (seconds, MB) at 10,000 records. Measured here 2026-11-06: consumer 6.5 s / 43 s, commercial 7 s / 6 s,
# all under 250 MB. Set with headroom until the first nightly runs show the CI runner's speed, then tightened to
# measured + 20 % (a reviewed PR; also when a deliberate change costs more: state why).
BUDGETS = {
    "consumer_nbfc_001": {"validate (core)": (15, 400), "generate files (CIBIL)": (90, 400)},
    "commercial_nbfc_001": {"validate (core)": (15, 400), "generate files (CIBIL)": (15, 400)},
}


@pytest.mark.parametrize("name", list(BUDGETS))
def test_stages_stay_within_budget(name):
    with tempfile.TemporaryDirectory() as d:
        timings = run_stages(name, RECORDS, Path(d), progress=lambda s: None)
    print(f"\n{name} at {RECORDS} records\n{timings.text()}")
    if RECORDS != 10000:
        pytest.skip("budgets are for 10,000 records; other sizes only print the timings")
    over = [
        f"{stage}: {timings.stages[stage]:.0f} s (budget {secs} s), peak {timings.peaks[stage]} MB (budget {mb} MB)"
        for stage, (secs, mb) in BUDGETS[name].items()
        if timings.stages[stage] > secs or timings.peaks[stage] > mb
    ]
    assert not over, "slower or bigger than the budget:\n" + "\n".join(over)
