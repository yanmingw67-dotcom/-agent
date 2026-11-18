"""P16-01 回归：只有源码证据的旧路径保持可用。"""

from tests.conftest import READ
from tests.integration.test_p14_review_publication import (
    accept,
    publish,
    request,
    setup,
)


def probe(client, cid, text):
    return client.post(
        "/api/v1/probes",
        headers=READ,
        json={"category_id": cid, "requirement_text": text},
    )


def test_source_only_candidate_review_publish_and_probe(client, environment):
    object.__setattr__(environment, "service_mode", "retrieval")
    cid, sid, claim_ids, evidence = setup(client)
    revision = accept(client, claim_ids[0], request(evidence["core/i_preview.py"]))
    response = publish(client, sid, [revision["id"]])
    assert response.status_code == 200, response.text
    result = probe(client, cid, "事件列表首项保留")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "candidate_retrieval"
    assert body["candidates"][0]["content"]["name"] == "事件列表首项保留机制"
    assert all(item["kind"] == "source" for item in body["evidence"])
    assert body["coverage"]["coverage_incomplete"] is True
