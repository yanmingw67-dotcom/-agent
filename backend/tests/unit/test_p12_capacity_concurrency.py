from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.security import AppError
from app.ingestion.capacity import ledger, reserve


def test_concurrent_reservations_cannot_exceed_quota(environment):
    object.__setattr__(environment, "storage_quota_bytes", 10)

    def attempt(key):
        try:
            reserve(environment, key, 7)
            return True
        except AppError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ["one", "two"]))
    assert sorted(results) == [False, True]
    with ledger(environment) as state:
        assert sum(v["bytes"] for v in state.values()) == 7


def test_low_free_space_rejects_and_does_not_leak_reservation(environment, monkeypatch):
    from collections import namedtuple

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr("app.ingestion.capacity.shutil.disk_usage", lambda _: Usage(100, 99, 1))
    with pytest.raises(AppError) as exc:
        reserve(environment, "test", 7)
    assert exc.value.error_code == "STORAGE_LOW_SPACE"
    with ledger(environment) as state:
        assert not state


def test_existing_usage_counts_toward_quota(environment):
    object.__setattr__(environment, "storage_quota_bytes", 10)
    environment.archive_root.mkdir()
    (environment.archive_root / "existing").write_bytes(b"x" * 8)
    with pytest.raises(AppError) as exc:
        reserve(environment, "new", 3)
    assert exc.value.error_code == "STORAGE_QUOTA_EXCEEDED"
