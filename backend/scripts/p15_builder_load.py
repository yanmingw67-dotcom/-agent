"""在 builder 容器配额内制造 CPU 与 PostgreSQL 只读负载，用于 Probe 压力基线。

这是合成负载，不调用模型，不代表真实 Agent A 语义；仅验证同一 cgroup 资源竞争下
Probe 的可达性与延迟。所有 SQL 均为只读计数。
"""

import argparse
import json
import time

from sqlalchemy import text

from app.db.session import get_engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=45)
    args = parser.parse_args()
    engine = get_engine()
    end = time.monotonic() + args.duration
    queries = 0
    started = time.monotonic()
    while time.monotonic() < end:
        total = 0
        for value in range(20000):
            total += value * value
        with engine.connect() as connection:
            for table in (
                "evidence",
                "knowledge_publications",
                "capability_claims",
                "knowledge_jobs",
            ):
                connection.execute(text(f"select count(*) from {table}"))
        queries += 4
    print(
        json.dumps(
            {
                "queries": queries,
                "duration_s": round(time.monotonic() - started, 3),
                "cpu_burn": total,
            }
        )
    )


if __name__ == "__main__":
    main()
