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


def scope_for(path: str, length: int | None = None, method: str = "POST"):
    headers = [(b"authorization", b"Bearer " + b"a" * 48)]
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    return {
        "type": "http",
        "http_version": "1.1",
        "scheme": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "headers": headers,
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 1234),
    }


async def _ok_downstream(scope, receive, send):
    while True:
        message = await receive()
        if message["type"] != "http.request" or not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def run_guard(guard, scope, chunks):
    messages = []
    pending = list(chunks)

    async def receive():
        if pending:
            return {"type": "http.request", "body": pending.pop(0), "more_body": bool(pending)}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await guard(scope, receive, send)
    return messages


@pytest.mark.asyncio
async def test_source_upload_uses_large_limit_while_other_paths_keep_small_limit(
    environment,
):
    guard = RequestGuard(_ok_downstream, max_body_bytes=10, source_max_body_bytes=32)
    source = scope_for("/api/v1/categories/waf/source", length=20)
    messages = await run_guard(guard, source, [b"x" * 20])
    assert next(m["status"] for m in messages if m["type"] == "http.response.start") == 200

    other = scope_for("/api/v1/tender/extractions/upload", length=20)
    messages = await run_guard(guard, other, [b"x" * 20])
    assert next(m["status"] for m in messages if m["type"] == "http.response.start") == 413


@pytest.mark.asyncio
async def test_source_upload_content_length_over_limit_fails_before_body(environment):
    guard = RequestGuard(_ok_downstream, max_body_bytes=10, source_max_body_bytes=32)
    scope = scope_for("/api/v1/categories/waf/source", length=33)

    async def receive():
        pytest.fail("超限 Content-Length 不应读取请求体")

    async def downstream(scope, receive, send):
        pytest.fail("超限 Content-Length 不应进入应用")

    messages = []

    async def send(message):
        messages.append(message)

    await guard(scope, receive, send)
    assert next(m["status"] for m in messages if m["type"] == "http.response.start") == 413
    assert b"REQUEST_TOO_LARGE" in messages[-1]["body"]
