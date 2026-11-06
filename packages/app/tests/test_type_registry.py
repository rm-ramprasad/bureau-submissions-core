"""The type registry (step 5): types come from type folders (type.yaml), and a new folder works end to end with no
code change. The sample type is the commercial folder copied under another name into a folder outside the product,
listed in SUBMISSIONS_TYPE_PATH; its source views are the commercial stand-ins under the sample's names."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text
from test_api import worker  # noqa: F401  (fixture: the real pipeline over golden-backed views)

from app.bureaus.seed import seed_missing
from app.enums import BureauCode as B
from app.enums import RequestStatus as S
from app.enums import RunMode as M
from bureau_core import types
from bureau_core.enums import SubmissionType
from bureau_core.types import Descriptor, TypeDefinitionError, descriptor
from canonical.reference import seed as seed_reference

AS_OF = date(2026, 9, 30)
PRODUCT = Path(types.__file__).parent
CANONICAL = Path(__file__).resolve().parents[2] / "canonical" / "src" / "canonical" / "types"


def make_sample(root: Path) -> Path:
    folder = root / "sample"
    folder.mkdir(parents=True)
    for name in ("codes.yaml", "requirements.yaml", "rules.yaml", "layout.yaml"):
        body = (PRODUCT / "commercial" / name).read_text(encoding="utf-8")
        body = body.replace("submission_type: COMMERCIAL", "submission_type: SAMPLE").replace("CUCRF.", "SMPL.")
        (folder / name).write_text(body, encoding="utf-8")
    for name in ("inputs.yaml", "input_checks.yaml"):
        shutil.copy(CANONICAL / "commercial" / name, folder / name)
    (folder / "type.yaml").write_text(
        "submission_type: SAMPLE\nlabel: Sample\norder: 30\nengine: delimited\nfile_label: Sample file\n"
        "record_noun: Things\nheader_tag_required: false\ndefault_file_name: 'Sample-{member_id}-{as_of:%d%m%Y}.txt'\n",
        encoding="utf-8",
    )
    return folder


@pytest.fixture
def sample(tmp_path, monkeypatch):
    make_sample(tmp_path / "extra-types")
    monkeypatch.setenv("SUBMISSIONS_TYPE_PATH", str(tmp_path / "extra-types"))
    types.reload()
    yield SubmissionType("SAMPLE")
    monkeypatch.delenv("SUBMISSIONS_TYPE_PATH")
    types.reload()


def test_the_product_types_come_from_their_folders():
    assert [str(t) for t in SubmissionType] == ["CONSUMER", "COMMERCIAL"]
    assert SubmissionType("CONSUMER") is SubmissionType.CONSUMER and "NESL" not in SubmissionType
    with pytest.raises(ValueError, match="not a valid SubmissionType"):
        SubmissionType("NESL")
    assert (descriptor("CONSUMER").engine, descriptor("COMMERCIAL").engine) == ("tudf", "delimited")
    assert descriptor("CONSUMER").header_tag_required and not descriptor("COMMERCIAL").header_tag_required
    assert repr(SubmissionType.CONSUMER) == "SubmissionType('CONSUMER')" and SubmissionType.CONSUMER.value == "CONSUMER"


def test_descriptors_are_checked(tmp_path):
    bad = tmp_path / "type.yaml"
    bad.write_text("submission_type: X\n", encoding="utf-8")
    with pytest.raises(TypeDefinitionError, match="missing"):
        Descriptor.read(bad)
    body = (PRODUCT / "commercial" / "type.yaml").read_text(encoding="utf-8")
    bad.write_text(body.replace("engine: delimited", "engine: xml"), encoding="utf-8")
    with pytest.raises(TypeDefinitionError, match="engine"):
        Descriptor.read(bad)
    bad.write_text(body + "colour: red\n", encoding="utf-8")
    with pytest.raises(TypeDefinitionError, match="unknown keys colour"):
        Descriptor.read(bad)
    with pytest.raises(TypeDefinitionError, match="No submission type folder"):
        descriptor("NOPE")


def test_two_folders_for_one_type_are_refused(tmp_path, monkeypatch):
    extra = tmp_path / "extra"
    (extra / "copy").mkdir(parents=True)
    shutil.copy(PRODUCT / "consumer" / "type.yaml", extra / "copy" / "type.yaml")
    monkeypatch.setenv("SUBMISSIONS_TYPE_PATH", str(extra))
    types.reload()
    try:
        with pytest.raises(TypeDefinitionError, match="Two folders for CONSUMER"):
            types.descriptors()
    finally:
        monkeypatch.delenv("SUBMISSIONS_TYPE_PATH")
        types.reload()


def test_a_new_type_folder_works_end_to_end(sample, make_api, worker, engine, requests_repo, bureau_repo):  # noqa: F811
    assert [str(t) for t in SubmissionType] == ["CONSUMER", "COMMERCIAL", "SAMPLE"]
    seed_reference(engine)  # its code translations, as `migrate` seeds them
    seed_missing(bureau_repo, "nbfc_001")  # its bureau profiles, from the descriptor
    profile = bureau_repo.get("nbfc_001", B.CIBIL, sample)
    assert (profile.display_name, profile.member_id, profile.header_segment_tag) == (
        "TransUnion CIBIL Sample",
        "CIBS000001",
        "",
    )
    with engine.begin() as conn:  # the client's source views for the new type
        names = conn.execute(text("SELECT name FROM automn.sqlite_master WHERE name LIKE 'vw_commercial_%'")).scalars()
        for name in list(names):
            conn.execute(
                text(f"CREATE VIEW automn.{name.replace('commercial', 'sample')} AS SELECT * FROM automn.{name}")
            )

    api = make_api()
    listed = {t["value"]: t for t in api.get("/types")[1]["types"]}
    assert listed["SAMPLE"]["file_label"] == "Sample file" and listed["SAMPLE"]["label"] == "Sample"
    assert listed["SAMPLE"]["enabled"] and listed["SAMPLE"]["record_noun"] == "Things"

    v = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [sample])
    worker.run_once()
    validated = requests_repo.list_batch(v)[0]
    assert validated.status is S.SUCCEEDED, validated.error_message
    g = requests_repo.create_batch(
        "nbfc_001", AS_OF, M.GENERATE, [sample], [B.CIBIL], snapshot_id=validated.snapshot_id
    )
    worker.run_once()
    generated = requests_repo.list_batch(g)[0]
    assert generated.status is S.SUCCEEDED, generated.error_message
    assert generated.output_file_name.startswith("Sample-CIBS000001-30092026")
