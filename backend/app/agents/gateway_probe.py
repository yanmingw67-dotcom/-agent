"""固定合成输入的容器诊断；不接收业务输入，不读取业务表。"""

import json
import sys

import httpx2 as httpx

from app.core.config import get_settings
from app.core.security import AppError
from app.db.session import session_factory
from app.knowledge.model_authorization import normalize_endpoint, require_model_authorization


def run():
    s = get_settings()
    name = "report_number"
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
        "additionalProperties": False,
    }
    tool = {"type": "function", "function": {"name": name, "parameters": schema}}
    cases = [
        ("text", "只回答数字 7", {}),
        (
            "json_schema",
            "返回 n 等于 7 的 JSON 对象",
            {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "number", "strict": True, "schema": schema},
                }
            },
        ),
        (
            "tool",
            "调用 report_number 并传入整数 7",
            {"tools": [tool], "tool_choice": "required", "parallel_tool_calls": False},
        ),
    ]
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        for stage, prompt, extra in cases:
            try:
                with session_factory()() as db:
                    require_model_authorization(db, "synthetic_public")
                base = normalize_endpoint(s.builder_base_url)
                if not base.startswith("https://"):
                    raise ValueError("TLS_REQUIRED")
                print(f"{stage}: model_request synthetic_public", flush=True)
                response = client.post(
                    base + "/chat/completions",
                    headers={"Authorization": "Bearer " + s.builder_api_key.get_secret_value()},
                    json={
                        "model": s.builder_model,
                        "messages": [{"role": "user", "content": prompt}],
                        **extra,
                    },
                )
                response.raise_for_status()
                body = response.json()
                msg = body["choices"][0]["message"]
                usage = body["usage"]
                if stage == "tool":
                    call = msg["tool_calls"][0]["function"]
                    ok = call["name"] == name and json.loads(call["arguments"]) == {"n": 7}
                elif stage == "json_schema":
                    ok = json.loads(msg["content"]) == {"n": 7}
                else:
                    ok = msg["content"].strip() == "7"
                counts = [usage[key] for key in ("prompt_tokens", "completion_tokens")]
                if not ok or not all(type(n) is int and n > 0 for n in counts):
                    raise ValueError("INVALID_RESPONSE")
                print(f"{stage}: PASS input_tokens={counts[0]} output_tokens={counts[1]}")
            except Exception as exc:
                code = exc.error_code if isinstance(exc, AppError) else type(exc).__name__
                if isinstance(exc, httpx.HTTPStatusError):
                    code = f"HTTP_{exc.response.status_code}"
                print(f"{stage}: BLOCKED {code}")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
