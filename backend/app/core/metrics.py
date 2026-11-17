import threading
from collections import defaultdict, deque

_lock = threading.Lock()
_values = defaultdict(
    lambda: {"count": 0, "total_ms": 0.0, "max_ms": 0.0, "recent_ms": deque(maxlen=512)}
)


def observe(name, duration_ms):
    with _lock:
        value = _values[name]
        value["count"] += 1
        value["total_ms"] += duration_ms
        value["max_ms"] = max(value["max_ms"], duration_ms)
        value["recent_ms"].append(duration_ms)


def snapshot():
    with _lock:
        output = {}
        for key, value in _values.items():
            recent = sorted(value["recent_ms"])
            output[key] = {k: v for k, v in value.items() if k != "recent_ms"}
            output[key].update(
                p50_ms=recent[len(recent) // 2] if recent else 0,
                p95_ms=recent[min(len(recent) - 1, int(len(recent) * 0.95))] if recent else 0,
            )
        return output
