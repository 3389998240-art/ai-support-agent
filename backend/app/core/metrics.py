"""极简指标注册表：计数器 + 延迟分位数，按 Prometheus 文本格式输出。

为什么不用 prometheus_client：多一个依赖、多一个注册表生命周期要管；
这里的指标只服务四件事——请求量、错误率、延迟、业务转化（转人工率）。
多实例部署时同样应该换成 Prometheus/OTel SDK 或推送到采集端。
"""

from __future__ import annotations

import threading
from collections import deque

_MAX_SAMPLES = 2000


def _fmt_labels(labels: dict[str, str] | None) -> str:
    if not labels:
        return ""
    parts = ",".join(f'{key}="{value}"' for key, value in sorted(labels.items()))
    return "{" + parts + "}"


def _percentile(samples: list[float], ratio: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, int(round(ratio * (len(ordered) - 1)))))
    return ordered[index]


class Metrics:
    def __init__(self) -> None:
        self._counters: dict[tuple[str, tuple], float] = {}
        self._samples: dict[tuple[str, tuple], deque[float]] = {}
        self._lock = threading.Lock()

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            bucket = self._samples.setdefault(key, deque(maxlen=_MAX_SAMPLES))
            bucket.append(float(value))

    def counter_value(self, name: str, **labels: str) -> float:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            return self._counters.get(key, 0.0)

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            counters = dict(self._counters)
            samples = {key: list(value) for key, value in self._samples.items()}

        for (name, label_items), value in sorted(counters.items()):
            labels = dict(label_items)
            lines.append(f"{name}{_fmt_labels(labels)} {value:g}")

        for (name, label_items), values in sorted(samples.items()):
            labels = dict(label_items)
            for suffix, ratio in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99)):
                current = dict(labels, quantile=suffix)
                lines.append(f"{name}{_fmt_labels(current)} {_percentile(values, ratio):.4f}")
            if values:
                lines.append(f"{name}_count{_fmt_labels(labels)} {len(values)}")
                lines.append(f"{name}_sum{_fmt_labels(labels)} {sum(values):.4f}")

        if not lines:
            lines.append("# no metrics recorded yet")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._samples.clear()


METRICS = Metrics()
