"""并发压测：只依赖标准库，不需要 locust/k6。

用法（建议对 mock 模式的实例压，避免烧 LLM 额度和被限流）：
    # 先起一个不调用外部模型的实例
    LLM_PROVIDER=mock EMBEDDING_PROVIDER=hashing python -m uvicorn app.main:app --port 8001
    python scripts/loadtest.py --port 8001 --concurrency 20 --requests 100

输出：成功率、RPS、p50/p95/p99、按阶段拆分的错误。
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, url: str, token: str | None = None, body: dict | None = None, timeout: float = 120):
    payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    request = urllib.request.Request(url, data=payload, method=method)
    if payload:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc).encode()


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(ratio * (len(ordered) - 1)))))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--username", default="alice")
    parser.add_argument("--password", default="demo1234")
    parser.add_argument("--message", default="退货运费谁承担？")
    args = parser.parse_args()

    base = f"http://{args.host}:{args.port}"

    status, payload = call(
        "POST", f"{base}/api/auth/login", body={"username": args.username, "password": args.password}
    )
    if status != 200:
        raise SystemExit(f"登录失败 {status}: {payload[:200]}")
    token = json.loads(payload)["access_token"]

    latencies: list[float] = []
    failures: dict[str, int] = {}
    lock = threading.Lock()
    counter = {"done": 0}

    def one(index: int) -> None:
        started = time.perf_counter()
        status_code, body = call(
            "POST",
            f"{base}/api/chat",
            token,
            {"message": args.message, "thread_id": f"load-{args.concurrency}-{index}"},
        )
        elapsed = time.perf_counter() - started
        with lock:
            latencies.append(elapsed)
            counter["done"] += 1
            if status_code != 200:
                key = f"http_{status_code}"
                failures[key] = failures.get(key, 0) + 1

    started_at = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        list(pool.map(one, range(args.requests)))
    total = time.perf_counter() - started_at

    success = sum(1 for _ in latencies) - sum(failures.values())
    print("=" * 60)
    print(f"目标 {base} · 并发 {args.concurrency} · 请求 {args.requests}")
    print(f"总耗时 {total:.2f}s · RPS {args.requests / total:.2f} · 成功 {success}")
    print(
        "延迟 p50 {:.2f}s · p95 {:.2f}s · p99 {:.2f}s · max {:.2f}s".format(
            percentile(latencies, 0.5),
            percentile(latencies, 0.95),
            percentile(latencies, 0.99),
            max(latencies) if latencies else 0.0,
        )
    )
    if failures:
        print("失败分布:", failures)
    print("=" * 60)


if __name__ == "__main__":
    main()
