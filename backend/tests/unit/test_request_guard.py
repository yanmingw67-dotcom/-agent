import pytest

from app.core.logging import RequestGuard


@pytest.mark.asyncio
async def test_unauthorized_upload_does_not_read_body(environment):
    messages = []

    async def receive():
        pytest.fail("未授权上传不应读取请求体")

    async def downstream(scope, receive, send):
        pytest.fail("未授权上传不应进入 multipart 解析")

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/categories/123/source",
        "headers": [],
        "query_string": b"",
    }
    await RequestGuard(downstream, 10)(scope, receive, send)
    assert messages[0]["status"] == 401


@pytest.mark.asyncio
async def test_chunked_body_limit_has_uniform_error(environment):
    from app.main import create_app

    messages = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b"x" * 32, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    app = RequestGuard(create_app(), max_body_bytes=10)
    scope = {
        "type": "http",
        "http_version": "1.1",
        "scheme": "http",
        "method": "POST",
        "path": "/api/v1/categories/123/source",
        "raw_path": b"/api/v1/categories/123/source",
        "root_path": "",
        "headers": [
            (b"authorization", b"Bearer " + b"a" * 48),
            (b"content-type", b"multipart/form-data; boundary=test"),
        ],
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 1234),
    }
    await app(scope, receive, send)
    assert next(m["status"] for m in messages if m["type"] == "http.response.start") == 413
    assert b"REQUEST_TOO_LARGE" in messages[-1]["body"]
