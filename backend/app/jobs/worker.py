import logging
import os
import time
from pathlib import Path

from rq import Worker

from app.core.config import get_settings
from app.db.session import get_engine
from app.jobs.queue import connection, dispatch_pending


def main():
    os.umask(0o027)
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    while True:
        try:
            dispatch_pending()
            # RQ fork 前关闭父进程连接，避免子进程复用 PostgreSQL socket。
            get_engine().dispose()
            Path("/tmp/knowledge-worker-heartbeat").write_text(str(time.time()))
            worker = Worker([settings.queue_name], connection=connection())
            worker.work(burst=True, max_jobs=10, logging_level="WARNING")
        except Exception:
            logging.getLogger("knowledge.worker").warning("worker_dependency_unavailable")
        time.sleep(2)


if __name__ == "__main__":
    main()
