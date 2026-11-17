import pytest

from tests.conftest import make_zip
from tests.integration.test_p12_index_retrieval import indexed, tool


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_encoded_source_search_uses_same_exact_line_read(client, encoding):
    text = "def notify():\n    return '机器人通知'\n"
    cid, sid = indexed(client, data=make_zip([("encoded.py", text.encode(encoding))]))
    result = tool(client, cid, sid, "search_code_batch", query="机器人通知")
    assert result.status_code == 200, result.text
    item = result.json()["results"][0]
    assert item["start_line"] == 2 and item["snippet"] == "    return '机器人通知'"
    symbols = tool(client, cid, sid, "search_symbols", query="notify").json()["results"]
    assert symbols[0]["start_line"] == 1


def test_search_cursor_not_repeating_first_hit(client):
    cid, sid = indexed(client, data=make_zip([("test.py", "alpha\nalpha\nalpha\n")]))
    first = tool(client, cid, sid, "search_code_batch", query="alpha", limit=1).json()
    second = tool(
        client,
        cid,
        sid,
        "search_code_batch",
        query="alpha",
        limit=1,
        cursor=int(first["next_cursor"]),
    ).json()
    assert first["results"][0]["start_line"] == 1
    assert second["results"][0]["start_line"] == 2
