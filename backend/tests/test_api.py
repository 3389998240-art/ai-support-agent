from __future__ import annotations

from app.core.config import get_settings


# ── 公开接口 ────────────────────────────────────────────────────────────────
def test_health_is_public(client) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["knowledge"]["chunks"] > 0
    assert payload["auth"]["scheme"].startswith("JWT")


def test_chat_requires_login(client) -> None:
    assert client.post("/api/chat", json={"message": "你好"}).status_code == 401
    assert client.post("/api/chat/stream", json={"message": "你好"}).status_code == 401


# ── 登录 ────────────────────────────────────────────────────────────────────
def test_login_success(client) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "agent01", "password": get_settings().demo_password},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["user"]["role"] == "agent"
    assert "reviews:decide" in payload["user"]["permissions"]
    assert payload["expires_at"] > 0


def test_login_wrong_password_does_not_leak_user_existence(client) -> None:
    wrong = client.post("/api/auth/login", json={"username": "agent01", "password": "nope"})
    unknown = client.post("/api/auth/login", json={"username": "ghost", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"] == "用户名或密码错误"


def test_me_returns_identity(client, auth) -> None:
    response = client.get("/api/auth/me", headers=auth("alice"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["username"] == "alice"
    assert payload["role"] == "customer"
    assert payload["customer_id"] == "C1001"


def test_invalid_token_is_rejected(client) -> None:
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 401


# ── RBAC ────────────────────────────────────────────────────────────────────
def test_reviews_requires_agent_role(client, auth) -> None:
    assert client.get("/api/reviews").status_code == 401
    assert client.get("/api/reviews", headers=auth("alice")).status_code == 403
    assert client.get("/api/reviews", headers=auth("agent01")).status_code == 200


def test_knowledge_search_requires_agent(client, auth) -> None:
    body = {"query": "保修多久", "k": 3}
    assert client.post("/api/knowledge/search", json=body, headers=auth("alice")).status_code == 403
    response = client.post("/api/knowledge/search", json=body, headers=auth("agent01"))
    assert response.status_code == 200
    assert response.json()["hits"]


def test_ingest_requires_admin(client, auth) -> None:
    assert client.post("/api/knowledge/ingest", headers=auth("agent01")).status_code == 403
    response = client.post("/api/knowledge/ingest", headers=auth("admin"))
    assert response.status_code == 200
    assert response.json()["chunks"] > 0


def test_knowledge_summary_is_role_guarded(client, auth) -> None:
    assert client.get("/api/knowledge", headers=auth("alice")).status_code == 403
    payload = client.get("/api/knowledge", headers=auth("agent01")).json()
    assert payload["index"]["documents"] == 6


def test_agent_approval_limit_enforced(client, auth, clean_db) -> None:
    """超过审核员上限的金额：agent 403，admin 放行（走到 404 说明越过了金额闸门）。"""
    clean_db.create_approval(
        thread_id="limit-check",
        intent="refund",
        amount=get_settings().agent_approval_limit + 1000,
        reason="超额退款",
        risk_notes=[],
        payload={},
        order_no="SO202610180001",
    )

    agent = client.post(
        "/api/threads/limit-check/resume",
        json={"decision": "approve", "note": "同意"},
        headers=auth("agent01"),
    )
    assert agent.status_code == 403
    assert "超过审核员上限" in agent.json()["detail"]

    admin = client.post(
        "/api/threads/limit-check/resume",
        json={"decision": "approve", "note": "同意"},
        headers=auth("admin"),
    )
    # 没有对应的图状态，所以是 404；关键是它不是 403——金额闸门靠角色放行了
    assert admin.status_code == 404


def test_customer_only_sees_own_orders(client, auth) -> None:
    own = client.get("/api/demo/orders?customer_id=C1002", headers=auth("alice"))
    assert own.json()["customer_id"] == "C1001"

    any_customer = client.get("/api/demo/orders?customer_id=C1002", headers=auth("agent01"))
    assert any_customer.json()["customer_id"] == "C1002"


# ── 会话归属 ────────────────────────────────────────────────────────────────
def test_thread_is_only_visible_to_owner_and_staff(client, auth) -> None:
    chat = client.post(
        "/api/chat", json={"message": "电子发票怎么开"}, headers=auth("alice")
    ).json()
    thread_id = chat["thread_id"]

    assert client.get(f"/api/threads/{thread_id}", headers=auth("alice")).status_code == 200
    assert client.get(f"/api/threads/{thread_id}", headers=auth("bob")).status_code == 404
    assert client.get(f"/api/threads/{thread_id}", headers=auth("agent01")).status_code == 200


# ── 既有行为回归 ────────────────────────────────────────────────────────────
def test_chat_with_token_returns_citations(client, auth) -> None:
    response = client.post(
        "/api/chat", json={"message": "退货运费谁承担？"}, headers=auth("alice")
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["citations"]
    assert payload["thread_id"].startswith("t-")


def test_chat_stream_emits_events(client, auth) -> None:
    with client.stream(
        "POST", "/api/chat/stream", json={"message": "发票怎么开"}, headers=auth("alice")
    ) as response:
        body = "".join(chunk for chunk in response.iter_text())

    assert '"type": "trace"' in body
    assert '"type": "result"' in body


def test_resume_unknown_thread_returns_404(client, auth) -> None:
    response = client.post(
        "/api/threads/not-exist/resume",
        json={"decision": "approve"},
        headers=auth("agent01"),
    )
    assert response.status_code == 404


def test_resume_requires_review_permission(client, auth) -> None:
    response = client.post(
        "/api/threads/whatever/resume",
        json={"decision": "approve"},
        headers=auth("alice"),
    )
    assert response.status_code == 403


def test_busy_thread_returns_409(client, auth) -> None:
    headers = auth("alice")
    first = client.post(
        "/api/chat",
        json={"message": "订单 SO202610180001 我要退款", "thread_id": "api-busy"},
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["status"] == "waiting_human"

    second = client.post(
        "/api/chat", json={"message": "还在吗", "thread_id": "api-busy"}, headers=headers
    )
    assert second.status_code == 409


def test_request_id_on_chat(client, auth) -> None:
    response = client.post("/api/chat", json={"message": "发票怎么开"}, headers=auth("alice"))
    payload = response.json()
    assert payload["request_id"].startswith("req-")
    assert response.headers["X-Request-Id"] == payload["request_id"]


def test_reviewer_identity_comes_from_token(client, auth, clean_db) -> None:
    headers = auth("alice")
    chat = client.post(
        "/api/chat",
        json={"message": "订单 SO202610180001 我要退款，耳机有杂音", "thread_id": "api-decide"},
        headers=headers,
    ).json()
    assert chat["status"] == "waiting_human"

    decided = client.post(
        "/api/threads/api-decide/resume",
        json={"decision": "approve", "note": "核实通过", "reviewer": "伪造的名字"},
        headers=auth("agent01"),
    )
    assert decided.status_code == 200

    refunds = clean_db.list_refunds("SO202610180001")
    assert refunds and refunds[0].decided_by == "agent01", "审核人必须来自令牌，不能采信请求体"
