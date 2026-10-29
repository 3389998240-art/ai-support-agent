"""数据归属（IDOR）守卫测试。

背景：审计发现"只校验了你是谁，没校验数据是不是你的"——
客户 B 能用订单号操作客户 A 的订单，还能往别人的会话里写消息。这里逐条锁死。
"""

from __future__ import annotations

ALICE_ORDER = "SO202610180001"   # C1001
BOB_ORDER = "SO202610240003"     # C1002
ALICE_CHEAP_ORDER = "SO202610270007"  # C1001，¥287 → 可自助退款，最危险的一条


def test_customer_cannot_refund_other_customers_order(client, auth, clean_db) -> None:
    """用别人的订单号发起退款：按"查不到"处理，不能产生退款单。"""
    response = client.post(
        "/api/chat",
        json={"message": f"订单 {ALICE_CHEAP_ORDER} 不要了，退了吧", "thread_id": "idor-refund"},
        headers=auth("bob"),
    )
    assert response.status_code == 200
    payload = response.json()
    assert "没有查到这笔订单" in payload["reply"]
    assert payload["policy"] is None
    assert clean_db.list_refunds(ALICE_CHEAP_ORDER) == []


def test_customer_cannot_read_other_customers_order(client, auth) -> None:
    response = client.post(
        "/api/chat",
        json={"message": f"我的订单 {ALICE_ORDER} 到哪了", "thread_id": "idor-order"},
        headers=auth("bob"),
    )
    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "星曜降噪耳机" not in reply
    assert "SF1029304756" not in reply
    assert "订单号" in reply  # 退化成"请提供订单号"


def test_staff_can_query_any_order(client, auth) -> None:
    response = client.post(
        "/api/chat",
        json={"message": f"我的订单 {BOB_ORDER} 到哪了", "thread_id": "idor-staff"},
        headers=auth("agent01"),
    )
    assert response.status_code == 200
    assert BOB_ORDER in response.json()["reply"]


def test_customer_cannot_write_into_other_thread(client, auth) -> None:
    own = client.post(
        "/api/chat", json={"message": "电子发票怎么开", "thread_id": "idor-thread"}, headers=auth("alice")
    )
    assert own.status_code == 200
    assert len(client.get("/api/threads/idor-thread", headers=auth("alice")).json()["messages"]) == 2

    intruder = client.post(
        "/api/chat",
        json={"message": f"订单 {ALICE_ORDER} 我要退款", "thread_id": "idor-thread"},
        headers=auth("bob"),
    )
    assert intruder.status_code == 404

    # 会话没有被写入
    assert len(client.get("/api/threads/idor-thread", headers=auth("alice")).json()["messages"]) == 2
    assert client.get("/api/threads/idor-thread", headers=auth("agent01")).status_code == 200


def test_customer_cannot_take_over_orphan_thread(client, auth, repo) -> None:
    """有历史但没有归属的会话：客户不能"发一条消息就把主人变成自己"。"""
    client.post("/api/chat", json={"message": "保修多久", "thread_id": "idor-orphan"}, headers=auth("alice"))
    repo._execute("DELETE FROM thread_owners WHERE thread_id = ?", ("idor-orphan",))  # noqa: SLF001

    intruder = client.post(
        "/api/chat", json={"message": "我是新主人", "thread_id": "idor-orphan"}, headers=auth("bob")
    )
    assert intruder.status_code == 404
    assert repo.get_thread_owner("idor-orphan") is None

    # 客服可以介入（审核场景需要）
    staff = client.post(
        "/api/chat", json={"message": "客服跟进", "thread_id": "idor-orphan"}, headers=auth("agent01")
    )
    assert staff.status_code == 200


def test_unowned_thread_is_invisible_to_customers(client, auth, repo) -> None:
    """认证上线前创建的会话没有归属记录，不能被客户"认领"读取。"""
    client.post("/api/chat", json={"message": "保修多久", "thread_id": "idor-legacy"}, headers=auth("alice"))
    repo._execute("DELETE FROM thread_owners WHERE thread_id = ?", ("idor-legacy",))  # noqa: SLF001

    assert client.get("/api/threads/idor-legacy", headers=auth("alice")).status_code == 404
    assert client.get("/api/threads/idor-legacy", headers=auth("bob")).status_code == 404
    assert client.get("/api/threads/idor-legacy", headers=auth("agent01")).status_code == 200
