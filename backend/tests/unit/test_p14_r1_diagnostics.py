"""数据库异常诊断不得泄漏SQL、参数、正文、凭据或源码。"""

import json
from types import SimpleNamespace

from sqlalchemy.exc import DBAPIError

from app.core.logging import database_diagnostic, log_database_error


def test_database_diagnostic_allowlisted_metadata_only(caplog):
    class DriverError(Exception):
        sqlstate = "P0001"
        diag = SimpleNamespace(
            constraint_name=None,
            table_name=None,
            column_name=None,
            context="PL/pgSQL function guard_active_snapshot() line 6 at RAISE",
            message_detail="敏感SQL参数与源码不能出现",
        )

    exc = DBAPIError("SECRET_SQL", {"password": "SECRET_PASSWORD"}, DriverError("SECRET_BODY"))
    with caplog.at_level("ERROR", logger="knowledge.audit"):
        log_database_error(exc, "test-correlation-id")
    row = json.loads(caplog.records[-1].message)
    assert row["sqlstate"] == "P0001"
    assert row["constraint_name"] is None
    assert row["function_name"] == "guard_active_snapshot"
    assert row["correlation_id"] == "test-correlation-id"
    assert row["driver_exception_type"] == "DriverError"
    assert not any(token in caplog.text for token in ["SECRET", "敏感SQL参数"])


def test_database_diagnostic_rejects_untrusted_identifier_text():
    original = SimpleNamespace(
        sqlstate="P0001\npassword",
        diag=SimpleNamespace(
            constraint_name="bad\nsecret",
            table_name="table; SQL",
            column_name="bad name",
            context="正文包含敏感值",
        ),
    )
    row = database_diagnostic(SimpleNamespace(orig=original), "correlation")
    assert row["sqlstate"] is None
    assert row["constraint_name"] is None
    assert row["table_name"] is None
    assert row["column_name"] is None
    assert row["function_name"] is None
