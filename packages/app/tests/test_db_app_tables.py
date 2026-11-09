"""The app's tables (requests, bureau profiles, users, audit, validation, adjustments) agree with migrations 001-006 on SQL Server (marked
`db`; CI job "Database")."""

import os

import pytest

from app.adjustments.tables import snapshot_adjustment, snapshot_adjustment_effect, submission_adjustment
from app.audit import audit_event
from app.bureaus.tables import bureau_profile
from app.configstore import config_version
from app.configstore.settings import client_setting
from app.outputfiles import out_file, out_submission
from app.reconciliation import (
    reconciliation_check,
    reconciliation_column,
    reconciliation_mismatch,
    reconciliation_summary,
)
from app.requests.tables import submission_request
from app.users import app_user
from app.validation.tables import (
    record_outcome,
    validation_finding,
    validation_parameter,
    validation_policy,
    validation_rule,
    validation_summary,
)
from canonical.db import make_engine
from canonical.testing_db import prepare_mssql, schema_drift

pytestmark = pytest.mark.db


def test_app_tables_match_the_migrated_schema():
    url = os.environ.get("BSC_MSSQL_URL")
    if not url:
        pytest.skip("BSC_MSSQL_URL not set")
    engine = make_engine(url)
    prepare_mssql(engine)
    problems = schema_drift(
        engine,
        [
            submission_request, bureau_profile, app_user, audit_event, out_file, out_submission,
            validation_rule, validation_policy, validation_parameter, validation_finding, validation_summary,
            record_outcome, submission_adjustment, snapshot_adjustment, snapshot_adjustment_effect,
            reconciliation_summary, reconciliation_check, reconciliation_column, reconciliation_mismatch,
            config_version, client_setting,
        ],
    )  # fmt: skip
    engine.dispose()
    assert not problems, "\n".join(problems)
