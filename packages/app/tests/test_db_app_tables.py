"""The app's tables (requests, bureau profiles, users, audit) agree with migrations 001-006 on SQL Server (marked
`db`; CI job "Database")."""

import os

import pytest

from app.audit import audit_event
from app.bureaus.tables import bureau_profile
from app.requests.tables import submission_request
from app.users import app_user
from canonical.db import make_engine
from canonical.testing_db import prepare_mssql, schema_drift

pytestmark = pytest.mark.db


def test_app_tables_match_the_migrated_schema():
    url = os.environ.get("BSC_MSSQL_URL")
    if not url:
        pytest.skip("BSC_MSSQL_URL not set")
    engine = make_engine(url)
    prepare_mssql(engine)
    problems = schema_drift(engine, [submission_request, bureau_profile, app_user, audit_event])
    engine.dispose()
    assert not problems, "\n".join(problems)
