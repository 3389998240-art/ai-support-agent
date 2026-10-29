"""针对"已知问题"修复的守卫测试。

每个用例对应一处加固，注释里写明它防的是什么事故。
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import HumanMessage

from app.agents.guard import safety_check
from app.agents.knowledge import _retrieval_query
from app.core.config import get_settings
from app.core.errors import ApprovalConflictError, DuplicateRefundError, NotWaitingError
from app.domain.models import Order
from app.domain.policy import calculate_refundable, evaluate_refund

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _order(**overrides) -> Order:
    base = Order(
        order_no="SO202610290099",
        customer_id="C1001",
        product_name="星辰运动手环 4",
        category="手环",
        amount=287.0,
        goods_amount=329.0,
        coupon_amount=50.0,
        shipping_fee=8.0,
        status="delivered",
        paid_at=(NOW - timedelta(days=4)).isoformat(),
        delivered_at=(NOW - timedelta(days=2)).isoformat(),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


# ── 并发审批 ────────────────────────────────────────────────────────────────
def test_claim_approval_is_atomic(repo) -> None:
    """8 个线程同时抢同一张审批单，只能有一个成功。"""
    approval = repo.create_approval(
        thread_id="hard-race",
        intent="refund",
        amount=899.0,
        reason="并发测试",
        risk_notes=[],
        payload={},
        order_no="SO202610180001",
    )

    results: list[bool] = []

    def worker() -> None:
        results.append(repo.claim_approval(approval.approval_id, decided_by="tester"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(results) == 1, "并发抢占必须只有一个赢家"
    assert repo.get_approval(approval.approval_id)["status"] == "approved"


def test_commit_refund_rolls_back_when_approval_taken(repo) -> None:
    """审批单被别人先处理：既不能退款，也不能留下工单。"""
    approval = repo.create_approval(
        thread_id="hard-taken",
        intent="refund",
        amount=899.0,
        reason="已被处理",
        risk_notes=[],
        payload={},
        order_no="SO202610180001",
    )
    assert repo.claim_approval(approval.approval_id, decided_by="alice") is True

    with pytest.raises(ApprovalConflictError):
        repo.commit_refund(
            thread_id="hard-taken",
            order_no="SO202610180001",
            amount=899.0,
            reason="重复执行",
            status="approved",
            decided_by="bob",
            note="",
            approval_id=approval.approval_id,
        )

    assert repo.list_refunds("SO202610180001") == []
    assert repo.list_tickets("hard-taken") == []


def test_duplicate_active_refund_blocked_by_unique_index(repo) -> None:
    """同一订单已有进行中退款时，唯一索引兜底拦住第二笔。"""
    repo.create_refund(
        order_no="SO202610120002",
        amount=1299.0,
        reason="已有一笔",
        status="pending",
    )

    with pytest.raises(DuplicateRefundError):
        repo.commit_refund(
            thread_id="hard-dup",
            order_no="SO202610120002",
            amount=1299.0,
            reason="重复申请",
            status="pending",
            decided_by=None,
            note=None,
            approval_id=None,
        )

    assert len(repo.list_refunds("SO202610120002")) == 1


def test_sequential_double_resume_only_refunds_once(service, clean_db) -> None:
    """审核员连点两次"通过"：第二次必须被挡，退款单只有 1 条。"""
    service.chat("订单 SO202610180001 我要退款", thread_id="hard-double")
    first = service.resume("hard-double", decision="approve", note="核实通过", reviewer="alice")
    assert "退款已提交" in first["reply"]

    with pytest.raises(NotWaitingError):
        service.resume("hard-double", decision="approve", note="再点一次", reviewer="bob")

    assert len(clean_db.list_refunds("SO202610180001")) == 1


def test_reject_closes_approval(repo, service, clean_db) -> None:
    """驳回也要关审批单，否则审核队列永远挂着一条已处理的单。"""
    service.chat("订单 SO202610180001 我要退款", thread_id="hard-reject-close")
    pending = [a for a in clean_db.list_approvals(status="pending") if a["thread_id"] == "hard-reject-close"]
    assert len(pending) == 1

    result = service.resume("hard-reject-close", decision="reject", note="缺少故障凭证", reviewer="bob")
    assert "未通过" in result["reply"]

    still_pending = [
        a for a in clean_db.list_approvals(status="pending") if a["thread_id"] == "hard-reject-close"
    ]
    assert still_pending == [], "驳回后审批单必须关闭"
    assert clean_db.get_approval(pending[0]["approval_id"])["status"] == "rejected"


# ── 幂等键 ──────────────────────────────────────────────────────────────────
def test_idempotency_key_scoped_by_order(repo) -> None:
    """同一个会话里两笔订单各自持有审批单，不会互相复用。"""
    repo.create_approval(
        thread_id="hard-keys",
        intent="refund",
        amount=899.0,
        reason="订单 A",
        risk_notes=[],
        payload={},
        order_no="SO202610180001",
    )
    repo.create_approval(
        thread_id="hard-keys",
        intent="refund",
        amount=287.0,
        reason="订单 B",
        risk_notes=[],
        payload={},
        order_no="SO202610270007",
    )

    found = repo.find_pending_approval("hard-keys", "SO202610270007")
    assert found is not None
    assert found["order_no"] == "SO202610270007"


def test_execute_revalidates_order_state(service, clean_db, repo) -> None:
    """审批挂了一段时间后订单被取消：执行前必须复核，不能照退。"""
    service.chat("订单 SO202610180001 我要退款", thread_id="hard-toctou")
    try:
        repo._execute(  # noqa: SLF001 - 模拟其他系统改了订单状态
            "UPDATE orders SET status = 'cancelled' WHERE order_no = 'SO202610180001'"
        )
        result = service.resume("hard-toctou", decision="approve", note="核实通过", reviewer="alice")
        assert "复核未通过" in result["reply"]
        assert clean_db.list_refunds("SO202610180001") == []
    finally:
        repo._execute(  # noqa: SLF001
            "UPDATE orders SET status = 'delivered' WHERE order_no = 'SO202610180001'"
        )


# ── 规则引擎：R9 与金额 ─────────────────────────────────────────────────────
def test_delivery_dispute_forces_manual_review() -> None:
    """声称没收到货但物流显示已签收 → 必须人工，不能自助退款。"""
    decision = evaluate_refund(
        _order(),
        [],
        settings=get_settings(),
        quality_claim=False,
        delivery_dispute=True,
        now=NOW,
    )
    assert decision.eligible is True
    assert decision.action == "manual_review"
    assert decision.rule_code == "R8_MANUAL_REVIEW"
    assert any("签收争议" in note for note in decision.risk_notes)


def test_refund_amount_deducts_shipping_for_change_of_mind() -> None:
    """无理由退货扣运费，质量问题不扣；优惠券不再二次扣减（实付已含）。"""
    change_of_mind = calculate_refundable(_order(), quality_claim=False)
    assert change_of_mind["refundable"] == pytest.approx(279.0)  # 287 - 8
    assert change_of_mind["shipping_deducted"] == pytest.approx(8.0)

    quality = calculate_refundable(_order(), quality_claim=True)
    assert quality["refundable"] == pytest.approx(287.0)
    assert quality["shipping_deducted"] == pytest.approx(0.0)

    # 标价 - 优惠 = 279，与"实付 - 运费"一致，说明分摊口径自洽
    assert quality["goods_amount"] - quality["coupon_deducted"] == pytest.approx(279.0)


def test_reject_reports_all_failed_rules() -> None:
    """同时踩中"定制商品"和"超期"时，要一次说清两条，而不是只报第一条。"""
    order = _order(
        is_custom=True,
        delivered_at=(NOW - timedelta(days=30)).isoformat(),
    )
    decision = evaluate_refund(
        order, [], settings=get_settings(), quality_claim=False, now=NOW
    )
    assert decision.eligible is False
    assert set(decision.failed_rules) == {"R3_CUSTOM_GOODS", "R4_OUT_OF_WINDOW"}
    assert "定制商品" in decision.reason and "时效" in decision.reason


# ── 多轮检索 ────────────────────────────────────────────────────────────────
def test_multi_turn_query_merges_previous_question() -> None:
    state = {
        "message": "那它呢",
        "messages": [HumanMessage(content="退款一般多久到账"), HumanMessage(content="那它呢")],
    }
    query, previous = _retrieval_query(state)
    assert previous == "退款一般多久到账"
    assert "退款" in query and "那它呢" in query


def test_multi_turn_query_keeps_full_sentence_alone() -> None:
    state = {
        "message": "电子发票开票需要多久才能收到",
        "messages": [
            HumanMessage(content="退款多久到账"),
            HumanMessage(content="电子发票开票需要多久才能收到"),
        ],
    }
    query, previous = _retrieval_query(state)
    assert previous is None
    assert query == "电子发票开票需要多久才能收到"


# ── 安全与可观测 ────────────────────────────────────────────────────────────
def test_phone_number_is_masked() -> None:
    result = safety_check({"message": "我手机 13812345678，订单要退款"})
    assert "13812345678" not in result["message"]
    assert "手机号已脱敏" in result["message"]
    assert result["guard"]["issues"]


def test_request_id_is_returned_and_logged(client, auth) -> None:
    response = client.post("/api/chat", json={"message": "发票怎么开"}, headers=auth("alice"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["request_id"].startswith("req-")
    assert response.headers["X-Request-Id"] == payload["request_id"]
