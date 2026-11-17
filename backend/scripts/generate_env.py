"""生成本地随机凭据，不覆盖已有配置，也不输出凭据。"""

import os
import secrets
from pathlib import Path


def main():
    path = Path(".env")
    password = secrets.token_hex(24)
    content = (
        f"ADMIN_TOKEN={secrets.token_urlsafe(48)}\n"
        f"READ_TOKEN={secrets.token_urlsafe(48)}\n"
        f"POSTGRES_PASSWORD={password}\n"
        f"DATABASE_URL=postgresql+psycopg://knowledge:{password}@localhost:5432/knowledge\n"
        "REDIS_URL=redis://localhost:6379/0\n"
        "ARCHIVE_ROOT=./storage/archives\nINGESTION_ROOT=./storage/ingestion\n"
        "SOURCE_ROOT=./storage/ingestion\n"
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(content)
    print("已生成 .env，权限 0600；凭据未输出。")


if __name__ == "__main__":
    main()
