"""限流与指标的测试：新增的可观测性能力也要能被验证。"""

from __future__ import annotations

import time

from app.core.metrics import Metrics
from app.core.ratelimit import SlidingWindowLimiter


# ── 滑动窗口限流 ────────────────────────────────────────────────────────────
def test_limiter_allows_then_blocks() -> None:
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60)
    assert limiter.allow("a") == (True, 0)
    assert limiter.allow("a") == (True, 0)

    allowed, retry_after = limiter.allow("a")
    assert allowed is False
    assert retry_after >= 1


def test_limiter_window_expires() -> None:
    limiter = SlidingWindowLimiter(limit=1, window_seconds=0.15)
    assert limiter.allow("k")[0] is True
    assert limiter.allow("k")[0] is False
    time.sleep(0.2)
    assert limiter.allow("k")[0] is True


def test_limiter_is_keyed() -> None:
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60)
    assert limiter.allow("alice")[0] is True
    assert limiter.allow("alice")[0] is False
    assert limiter.allow("bob")[0] is True


# ── 登录限流（路由级）──────────────────────────────────────────────────────
def test_login_is_rate_limited(client) -> None:
    from app.api import routes

    original = routes._login_limiter.limit
    routes._login_limiter.limit = 3
    routes._login_limiter.reset()
    try:
        for _ in range(3):
            assert client.post(
                "/api/auth/login", json={"username": "probe", "password": "bad"}
            ).status_code == 401

        blocked = client.post("/api/auth/login", json={"username": "probe", "password": "bad"})
        assert blocked.status_code == 429
        assert "频繁" in blocked.json()["detail"]
        assert int(blocked.headers["Retry-After"]) >= 1
    finally:
        routes._login_limiter.limit = original
        routes._login_limiter.reset()


# ── 指标 ────────────────────────────────────────────────────────────────────
def test_metrics_render_contains_counter_and_quantiles() -> None:
    metrics = Metrics()
    metrics.inc("demo_total", intent="policy")
    for value in (0.1, 0.2, 0.3):
        metrics.observe("demo_seconds", value, path="/x")

    text = metrics.render()
    assert 'demo_total{intent="policy"} 1' in text
    assert 'demo_seconds{path="/x",quantile="p50"}' in text
    assert "demo_seconds_count" in text


def test_metrics_endpoint_requires_admin(client, auth) -> None:
    assert client.get("/api/metrics", headers=auth("agent01")).status_code == 403
    response = client.get("/api/metrics", headers=auth("admin"))
    assert response.status_code == 200
    assert "http_requests_total" in response.text


def test_chat_turn_is_counted(client, auth) -> None:
    client.post("/api/chat", json={"message": "发票怎么开"}, headers=auth("alice"))
    text = client.get("/api/metrics", headers=auth("admin")).text
    assert "chat_turns_total{" in text
    assert 'intent="invoice"' in text


def test_escalation_is_counted(client, auth) -> None:
    client.post(
        "/api/chat",
        json={"message": "订单 SO202610180001 我要退款", "thread_id": "metrics-esc"},
        headers=auth("alice"),
    )
    text = client.get("/api/metrics", headers=auth("admin")).text
    assert "escalations_total{" in text
