"""生成 P3.1-B 验收环境变量；不覆盖已有文件，不把令牌写入仓库。"""

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".env.p31b"


def main():
    if TARGET.exists():
        print(f"已存在 {TARGET.name}，未覆盖")
        return
    values = {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(36),
        "ADMIN_TOKEN": secrets.token_urlsafe(48),
        "READ_TOKEN": secrets.token_urlsafe(48),
        "MODEL_OPS_TOKEN": secrets.token_urlsafe(48),
        "P31B_UI_PORT": "18180",
        "P31B_API_PORT": "18190",
        "P31B_RETRIEVAL_PORT": "18191",
    }
    lines = [f"{key}={value}" for key, value in values.items()]
    lines.extend(
        [
            "",
            "# 企业真实资料模型授权完成后再填写以下变量；默认留空并由 Compose 关闭。",
            "# BUILDER_MODEL=",
            "# BUILDER_BASE_URL=",
            "# BUILDER_API_KEY=",
        ]
    )
    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    TARGET.chmod(0o600)
    print(f"已生成 {TARGET.name}（权限 600，未输出令牌）")


if __name__ == "__main__":
    main()
