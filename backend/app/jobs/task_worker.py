"""P2.1/P2.2/P2.3 后台队列 worker。

知识导入、索引与能力构建仍由 app.jobs.worker 按 WORKER_KIND 处理；本模块只处理
招标提取、符合性评估与受控导出。队列名称来自配置，不提供任何模型授权开关。
"""

import logging
import os
import threading
import time
from pathlib import Path

from rq import Worker

from app.core.config import get_settings
from app.jobs.queue import connection

HEARTBEAT = Path("/tmp/knowledge-taskworker-heartbeat")


def heartbeat():
    while True:
        try:
            HEARTBEAT.write_text(str(time.time()))
        except OSError:
            pass
        time.sleep(5)


def main():
    os.umask(0o027)
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    queues = [
        settings.tender_queue_name,
        settings.evaluation_queue_name,
        settings.export_queue_name,
    ]
    threading.Thread(target=heartbeat, daemon=True).start()
    Worker(queues, connection=connection()).work(logging_level="WARNING")


if __name__ == "__main__":
    main()
