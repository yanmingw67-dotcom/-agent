"""P3.1-B 大容量源码 ZIP 容量预检与受控样本验证。

只使用临时合成文件，不接触真实企业源码。可在宿主机或 api 容器内执行：

    python scripts/p31b_large_upload_check.py --capacity
    python scripts/p31b_large_upload_check.py --generate /tmp/waf-747m.zip --size-mib 747 --files 8
    python scripts/p31b_large_upload_check.py --check /tmp/waf-747m.zip

--check 会走真实的 preflight_directory / extract_checked 路径，并在结束后清理临时解压目录。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.security import ArchiveRejected
from app.ingestion.archive_guard import extract_checked, preflight_directory
from app.ingestion.capacity import ledger, tree_bytes
from app.ingestion.content_store import hash_file, remove_tree

CHUNK = 8 * 1024 * 1024


def _roots(settings: Settings) -> dict[str, Path]:
    return {
        "archive_root": settings.archive_root,
        "ingestion_root": settings.ingestion_root,
        "source_root": settings.source_root,
        "document_store": settings.document_store,
        "control_store": settings.control_store,
    }


def _usage(settings: Settings) -> dict[str, dict[str, int | str | bool]]:
    result = {}
    for name, root in _roots(settings).items():
        existing = root
        while not existing.exists() and existing.parent != existing:
            existing = existing.parent
        try:
            usage = shutil.disk_usage(existing)
            result[name] = {
                "path": str(root),
                "exists": root.exists(),
                "probe": str(existing),
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
            }
        except OSError as exc:
            result[name] = {
                "path": str(root),
                "exists": root.exists(),
                "error": type(exc).__name__,
            }
    return result


def capacity_report(settings: Settings) -> dict:
    roots = [settings.archive_root, settings.source_root, settings.document_store]
    current_usage = sum(tree_bytes(root) for root in roots)
    with ledger(settings) as state:
        reserved = sum(int(value.get("bytes", 0)) for value in state.values())
        reservations = dict(state)
    projected_peak = (
        settings.max_upload_bytes + settings.max_total_bytes + settings.max_entries * 2048
    )
    quota_headroom = settings.storage_quota_bytes - current_usage - reserved
    disk = _usage(settings)
    free_values = [
        int(item["free_bytes"]) for item in disk.values() if isinstance(item.get("free_bytes"), int)
    ]
    minimum_free = min(free_values) if free_values else 0
    checks = [
        {
            "name": "quota_covers_projected_peak",
            "ok": projected_peak <= quota_headroom,
            "detail": {
                "projected_peak_bytes": projected_peak,
                "quota_headroom_bytes": quota_headroom,
                "storage_quota_bytes": settings.storage_quota_bytes,
            },
        },
        {
            "name": "disk_free_covers_projected_peak_and_reserve",
            "ok": minimum_free >= projected_peak + settings.storage_min_free_bytes,
            "detail": {
                "minimum_free_bytes": minimum_free,
                "projected_peak_bytes": projected_peak,
                "storage_min_free_bytes": settings.storage_min_free_bytes,
            },
        },
        {
            "name": "single_file_budget_is_bounded",
            "ok": 0 < settings.max_file_bytes <= settings.max_total_bytes,
            "detail": {
                "max_file_bytes": settings.max_file_bytes,
                "max_total_bytes": settings.max_total_bytes,
            },
        },
        {
            "name": "entry_and_compression_limits_are_bounded",
            "ok": settings.max_entries > 0 and settings.max_compression_ratio > 0,
            "detail": {
                "max_entries": settings.max_entries,
                "max_compression_ratio": settings.max_compression_ratio,
            },
        },
    ]
    return {
        "limits": {
            "max_upload_bytes": settings.max_upload_bytes,
            "max_total_bytes": settings.max_total_bytes,
            "max_file_bytes": settings.max_file_bytes,
            "max_entries": settings.max_entries,
            "max_compression_ratio": settings.max_compression_ratio,
            "max_depth": settings.max_depth,
            "storage_quota_bytes": settings.storage_quota_bytes,
            "storage_min_free_bytes": settings.storage_min_free_bytes,
            "request_max_body_bytes": settings.request_max_body_bytes,
            "multipart_overhead_bytes": settings.multipart_overhead_bytes,
            "job_timeout_seconds": settings.job_timeout_seconds,
        },
        "disk": disk,
        "current_usage_bytes": current_usage,
        "reserved_bytes": reserved,
        "reservation_count": len(reservations),
        "projected_peak_bytes": projected_peak,
        "checks": checks,
        "capacity_status": "PASS" if all(check["ok"] for check in checks) else "FAIL",
    }


def generate_zip(output: Path, size_mib: int, files: int, settings: Settings | None = None) -> dict:
    if files < 1 or size_mib < 1:
        raise ValueError("files 和 size-mib 必须为正数")
    total = size_mib * 1024 * 1024
    if settings is not None and total > settings.max_total_bytes:
        raise ValueError("合成 ZIP 解压总量超过 max_total_bytes")
    per_file = max(1, total // files)
    if settings is not None and per_file > settings.max_file_bytes:
        raise ValueError(
            f"单个合成文件 {per_file} 字节超过 max_file_bytes={settings.max_file_bytes}，请增加 --files"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    main_content = b"def main():\n    return 'p31b large upload fixture'\n"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        # 至少保留一个可索引的文本源码文件，避免“只有随机二进制”导致索引结果为空。
        with archive.open("src/main.py", "w") as handle:
            handle.write(main_content)
        remaining = total
        for index in range(files):
            length = per_file if index < files - 1 else remaining
            remaining -= length
            with archive.open(f"src/module_{index:04d}.bin", "w") as handle:
                written = 0
                while written < length:
                    block = os.urandom(min(CHUNK, length - written))
                    handle.write(block)
                    written += len(block)
    return {
        "path": str(output),
        "bytes": output.stat().st_size,
        "files": files,
        "uncompressed_bytes": total + len(main_content),
        "duration_seconds": round(time.monotonic() - started, 2),
    }


def check_zip(path: Path, settings: Settings) -> dict:
    started = time.monotonic()
    preflight_directory(path, settings)
    temp_root = settings.ingestion_root
    temp_root.mkdir(parents=True, exist_ok=True, mode=0o750)
    temp = Path(tempfile.mkdtemp(prefix=".p31b-large-check-", dir=temp_root))
    try:
        source = temp / "source"
        source.mkdir(mode=0o750)
        manifest = extract_checked(path, source, settings)
        return {
            "path": str(path),
            "archive_bytes": path.stat().st_size,
            "sha256": hash_file(path),
            "entry_count": manifest["entry_count"],
            "file_count": len(manifest["files"]),
            "excluded_count": len(manifest["excluded"]),
            "total_bytes": manifest["total_bytes"],
            "sensitive_files": manifest["sensitive_files"],
            "duration_seconds": round(time.monotonic() - started, 2),
            "temp_cleaned": True,
        }
    finally:
        if temp.exists():
            remove_tree(temp)


def _settings() -> Settings:
    try:
        return get_settings()
    except Exception:
        # 本地快速预检允许使用占位令牌；容器内始终使用真实环境变量。
        return Settings(admin_token="a" * 48, read_token="r" * 48)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capacity", action="store_true", help="输出存储容量预检")
    parser.add_argument("--generate", type=Path, help="生成受控合成大 ZIP")
    parser.add_argument("--size-mib", type=int, default=747)
    parser.add_argument("--files", type=int, default=8)
    parser.add_argument("--check", type=Path, help="执行真实 ZIP 预检与解压")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args()
    settings = _settings()
    result: dict = {}
    if args.capacity or (not args.generate and not args.check):
        result["capacity"] = capacity_report(settings)
    if args.generate:
        result["generated"] = generate_zip(args.generate, args.size_mib, args.files, settings)
    if args.check:
        try:
            result["checked"] = check_zip(args.check, settings)
        except ArchiveRejected as exc:
            result["checked"] = {"path": str(args.check), "rejected": exc.code}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    failed = [check for check in result.get("capacity", {}).get("checks", []) if not check["ok"]]
    if failed or "rejected" in result.get("checked", {}):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
