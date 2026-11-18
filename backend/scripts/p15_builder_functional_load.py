"""用 FunctionModel 驱动真实 Agent A 运行时，制造可并发的 Builder 负载。

不调用真实模型、不产生 Token 费用；结果标记 TEST_FUNCTION_MODEL，仅用于验证
Probe 在 Builder 后台任务运行时的可用性与延迟，不作为模型能力验收。
"""

import argparse
import asyncio
import json
import os

from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from app.agents.runtime import run_builder
from app.db.models import KnowledgeJob
from app.db.session import session_factory


def prompt(messages):
    return json.loads(
        next(p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart))
    )


def model():
    calls = []

    async def respond(messages, info):
        payload = prompt(messages)
        name = info.output_tools[0].name
        calls.append(name)
        # 让测试任务持续到 Probe 压测窗口结束；不代表真实模型延迟。
        await asyncio.sleep(0.4)
        if name == "plan_investigation":
            chosen = next(
                (task for task in payload["tasks"] if task["symbol"] == "handle_events"),
                payload["tasks"][0],
            )
            data = {
                "proposal": {
                    "task_ids": [chosen["id"]],
                    "search_queries": [],
                    "reason": "先入口，再由真实引用扩展",
                }
            }
        elif name == "finish_fragment":
            source = payload["source"]
            follows = (
                ["process_batch", "preview_unique"] if source["path"].startswith("api/") else []
            )
            data = {
                "result": {
                    "candidate": {
                        "name": source["path"],
                        "action": "记录直接源码机制",
                        "target": source["path"],
                        "business_goal": "调查事件处理",
                        "mechanism": source["snippet"][:400],
                        "start_line": source["start_line"],
                        "end_line": source["end_line"],
                        "limitations": ["仅源码机制，持久性和调用可达性未核验"],
                    },
                    "observation": "当前片段已分析",
                    "follow_symbols": follows,
                    "search_queries": [],
                }
            }
        else:
            keys = [candidate["key"] for candidate in payload["candidates"]]
            data = {
                "comparison": {
                    "source_key": keys[0],
                    "target_key": keys[1],
                    "verdict": "not_equivalent",
                    "input_contract": "tenant与id，另一侧仅id",
                    "output_contract": "提交过滤和本地预览",
                    "conditions_and_errors": "存储和配置前提不同",
                    "rationale": "不同作用范围不能互换",
                    "gaps": ["持久store实现未提供"],
                }
            }
        return ModelResponse(parts=[ToolCallPart(name, data)])

    return FunctionModel(respond), calls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    token = os.environ["P15_JOB_TOKEN"]
    injected, calls = model()
    run_builder(args.job_id, token, injected)
    with session_factory()() as db:
        job = db.get(KnowledgeJob, args.job_id)
        summary = {
            "job_id": job.id,
            "status": job.status,
            "stage": job.stage,
            "model_mode": job.metrics_json.get("model_mode"),
            "model_requests": job.metrics_json.get("model_requests"),
            "tool_calls": job.metrics_json.get("tool_calls"),
            "function_model_calls": len(calls),
            "duration_ms": job.metrics_json.get("duration_ms"),
            "peak_cpu_percent": job.metrics_json.get("sampled_peak_cpu_percent"),
            "peak_rss_bytes": job.metrics_json.get("sampled_peak_rss_bytes"),
        }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
