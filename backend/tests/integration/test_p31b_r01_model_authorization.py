"""R01：环境变量不能代替模型外发授权，运维入口与执行门禁必须闭合。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.agents.runtime import configured_model
from app.core.security import AppError
from app.db.governance_models import ModelAuthorization
from app.db.models import AuditEvent
from app.db.session import session_factory
from app.knowledge.model_authorization import authorization_state
from tests.conftest import ADMIN, READ


def configure(environment, *, allow_enterprise=True, allow_synthetic=False):
    object.__setattr__(environment, "builder_model", "approved-model")
    object.__setattr__(environment, "builder_base_url", "https://model.example.test/v1")
    object.__setattr__(environment, "builder_api_key", SecretStr("test-only-not-a-real-key"))
    object.__setattr__(environment, "builder_allow_enterprise", allow_enterprise)
    object.__setattr__(environment, "builder_allow_synthetic", allow_synthetic)
    object.__setattr__(environment, "model_ops_token", SecretStr("ops-" + "x" * 40))


def ops_headers():
    return {"Authorization": ADMIN["Authorization"], "X-Model-Ops-Token": "ops-" + "x" * 40}


def payload(**overrides):
    body = {
        "data_classification": "enterprise",
        "approval_reference": "APPROVAL-2026-" + uuid4().hex[:8],
        "approved_scope": "仅用于 P3.1-B 真实产品知识构建，不用于对外服务",
    }
    body.update(overrides)
    return body


def test_env_flags_alone_do_not_authorize_model_egress(client, environment):
    configure(environment)
    with session_factory()() as db:
        state = authorization_state(db, "enterprise")
    assert state["status"] == "NOT_AUTHORIZED"
    assert state["active_authorization"] is None
    with pytest.raises(AppError) as exc:
        configured_model("enterprise")
    assert exc.value.error_code == "MODEL_AUTHORIZATION_RECORD_REQUIRED"
    body = client.get("/api/v1/admin/overview", headers=ADMIN).json()["model_authorization"]
    assert body["env_flags_are_approval"] is False
    assert body["authorization_source"] == "database_record"
    assert body["builder"]["allow_enterprise"] is True
    assert body["builder"]["status"] == "NOT_AUTHORIZED"
    assert body["builder"]["active_authorization"] is None


def test_ops_entry_requires_separate_token_and_records_are_bound_to_current_endpoint(
    client, environment
):
    configure(environment)
    no_token = client.post("/api/v1/admin/model-authorizations", headers=ADMIN, json=payload())
    assert no_token.status_code == 403
    assert no_token.json()["error_code"] == "MODEL_OPS_FORBIDDEN"
    wrong_token = client.post(
        "/api/v1/admin/model-authorizations",
        headers={**ADMIN, "X-Model-Ops-Token": "wrong"},
        json=payload(),
    )
    assert wrong_token.status_code == 403
    read_cannot = client.post(
        "/api/v1/admin/model-authorizations",
        headers={**READ, "X-Model-Ops-Token": "ops-" + "x" * 40},
        json=payload(),
    )
    assert read_cannot.status_code == 403

    expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    created = client.post(
        "/api/v1/admin/model-authorizations",
        headers=ops_headers(),
        json=payload(expires_at=expires),
    )
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["status"] == "active"
    assert record["approved_by"] == "admin"
    assert record["model_name"] == "approved-model"
    assert record["endpoint"] == "https://model.example.test/v1"

    with session_factory()() as db:
        state = authorization_state(db, "enterprise")
        assert state["status"] == "AUTHORIZED"
        assert state["active_authorization"]["id"] == record["id"]
        audit = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "model_authorization.create")
        )
        assert audit is not None
    assert configured_model("enterprise") is not None

    # 端点或模型改变后旧授权立即失配，必须重新审批。
    object.__setattr__(environment, "builder_base_url", "https://other.example.test/v1")
    with pytest.raises(AppError) as exc:
        configured_model("enterprise")
    assert exc.value.error_code == "MODEL_AUTHORIZATION_RECORD_REQUIRED"


def test_record_does_not_flip_runtime_switch_and_revoke_blocks_real_calls(client, environment):
    configure(environment, allow_enterprise=False)
    created = client.post(
        "/api/v1/admin/model-authorizations", headers=ops_headers(), json=payload()
    )
    assert created.status_code == 201, created.text
    with session_factory()() as db:
        state = authorization_state(db, "enterprise")
    assert state["status"] == "ENV_DISABLED"
    with pytest.raises(AppError) as exc:
        configured_model("enterprise")
    assert exc.value.error_code == "MODEL_DATA_NOT_AUTHORIZED"

    object.__setattr__(environment, "builder_allow_enterprise", True)
    assert configured_model("enterprise") is not None
    revoked = client.post(
        f"/api/v1/admin/model-authorizations/{created.json()['id']}/revoke",
        headers=ops_headers(),
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"
    with pytest.raises(AppError) as exc:
        configured_model("enterprise")
    assert exc.value.error_code == "MODEL_AUTHORIZATION_RECORD_REQUIRED"


def test_expired_or_unconfigured_ops_entry_fails_closed(client, environment):
    configure(environment)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    response = client.post(
        "/api/v1/admin/model-authorizations",
        headers=ops_headers(),
        json=payload(expires_at=past),
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "MODEL_AUTHORIZATION_EXPIRY_INVALID"

    with session_factory().begin() as db:
        db.add(
            ModelAuthorization(
                data_classification="enterprise",
                model_name="approved-model",
                endpoint="https://model.example.test/v1",
                approval_reference="APPROVAL-EXPIRED",
                approved_scope="过期授权不得使用",
                approved_by="admin",
                status="active",
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
                created_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
    with session_factory()() as db:
        assert authorization_state(db, "enterprise")["status"] == "NOT_AUTHORIZED"

    object.__setattr__(environment, "model_ops_token", SecretStr(""))
    unconfigured = client.post(
        "/api/v1/admin/model-authorizations",
        headers={**ADMIN, "X-Model-Ops-Token": "anything"},
        json=payload(),
    )
    assert unconfigured.status_code == 503
    assert unconfigured.json()["error_code"] == "MODEL_OPS_NOT_CONFIGURED"


def test_duplicate_active_authorization_is_rejected(client, environment):
    configure(environment)
    first = client.post("/api/v1/admin/model-authorizations", headers=ops_headers(), json=payload())
    assert first.status_code == 201
    second = client.post(
        "/api/v1/admin/model-authorizations", headers=ops_headers(), json=payload()
    )
    assert second.status_code == 409
    assert second.json()["error_code"] == "MODEL_AUTHORIZATION_EXISTS"
