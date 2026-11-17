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
        lock = None
        acquired = False
        try:
            if settings.worker_kind == "index":
                lock = connection().lock(
                    "knowledge-index-single-worker",
                    timeout=settings.index_job_timeout_seconds + 120,
                )
                acquired = lock.acquire(blocking=False)
                if not acquired:
                    continue
            dispatch_pending()
            # RQ fork 前清空连接池；分析 Worker 没有归档或源码写入挂载。
            get_engine().dispose()
            Path("/tmp/knowledge-worker-heartbeat").write_text(str(time.time()))
            worker = Worker(
                [
                    settings.index_queue_name
                    if settings.worker_kind == "index"
                    else settings.queue_name
                ],
                connection=connection(),
            )
            worker.work(
                burst=True,
                max_jobs=1 if settings.worker_kind == "index" else 10,
                logging_level="WARNING",
            )
        except Exception:
            logging.getLogger("knowledge.worker").warning("worker_dependency_unavailable")
        finally:
            if acquired and lock and lock.owned():
                lock.release()
            time.sleep(2)


if __name__ == "__main__":
    main()
