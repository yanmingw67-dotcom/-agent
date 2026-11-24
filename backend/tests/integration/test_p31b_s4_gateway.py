import pytest
from pydantic_ai.models import ModelRequestParameters

from app.agents.runtime import AuditedModel
from app.core.security import AppError
from tests.integration.test_p31b_r01_model_authorization import (
    configure,
    ops_headers,
    payload,
    recording_model,
    request_messages,
    running_deps,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["unconfigured", "disabled", "missing", "model", "endpoint", "revoked", "expired"]
)
async def test_denial_before_transport(case, client, environment, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.knowledge import model_authorization as auth

    configure(environment)
    deps = running_deps(client)
    calls = []
    model = AuditedModel(recording_model(calls), deps)
    if case not in {"unconfigured", "missing"}:
        response = client.post(
            "/api/v1/admin/model-authorizations", headers=ops_headers(), json=payload()
        )
        assert response.status_code == 201
        rid = response.json()["id"]
    if case == "unconfigured":
        object.__setattr__(environment, "builder_model", "")
    elif case == "disabled":
        object.__setattr__(environment, "builder_allow_enterprise", False)
    elif case == "model":
        object.__setattr__(environment, "builder_model", "other")
    elif case == "endpoint":
        object.__setattr__(environment, "builder_base_url", "https://model.example.test/other")
    elif case == "revoked":
        assert (
            client.post(
                f"/api/v1/admin/model-authorizations/{rid}/revoke", headers=ops_headers()
            ).status_code
            == 200
        )
    elif case == "expired":
        monkeypatch.setattr(auth, "now", lambda: datetime.now(UTC) + timedelta(days=2))
    with pytest.raises(AppError):
        await model.request(request_messages(), None, ModelRequestParameters())
    assert calls == []


def test_no_perpetual_enterprise_grant_or_reused_admin_secret(client, environment):
    configure(environment)
    path = "/api/v1/admin/model-authorizations"
    assert (
        client.post(path, headers=ops_headers(), json=payload(expires_at=None)).status_code == 422
    )
    object.__setattr__(environment, "model_ops_token", environment.admin_token)
    headers = {**ops_headers(), "X-Model-Ops-Token": environment.admin_token.get_secret_value()}
    assert client.post(path, headers=headers, json=payload()).status_code == 503
