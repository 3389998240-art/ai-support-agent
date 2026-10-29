from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from app.agents.common import is_foreign_order, sources_block, to_citations, trace
from app.core.config import get_settings
from app.core.errors import ApprovalConflictError, DuplicateRefundError
from app.deps import get_repository, get_retriever
from app.domain.policy import evaluate_refund, has_delivery_dispute
from app.graph.state import SupportState

POLICY_DOCS = ("01-退换货政策", "02-退款规则与到账时效", "06-售后升级与人工介入规则")

# 每条规则对应最该引用的政策文档，避免"超期拒绝"却引用《审批流》这种错配
RULE_DOC_HINTS: dict[str, tuple[str, ...]] = {
    "R0_AUTO_APPROVED": ("01-退换货政策", "02-退款规则与到账时效"),
    "R1_ORDER_NOT_FOUND": ("01-退换货政策",),
    "R2_ORDER_STATUS": ("02-退款规则与到账时效",),
    "R3_VIRTUAL_GOODS": ("01-退换货政策",),
    "R3_CUSTOM_GOODS": ("01-退换货政策",),
    "R4_OUT_OF_WINDOW": ("01-退换货政策",),
    "R5_DUPLICATE": ("02-退款规则与到账时效",),
    "R8_MANUAL_REVIEW": ("06-售后升级与人工介入规则", "01-退换货政策"),
}

# 规则专属的检索问句：直接用用户原话会把不相干的切片拉到前面
RULE_QUERIES: dict[str, str] = {
    "R0_AUTO_APPROVED": "退换货流程 审核通过 寄回 质检 退款",
    "R1_ORDER_NOT_FOUND": "退换货流程 申请 订单",
    "R2_ORDER_STATUS": "退款发起条件 订单取消 缺货 超时未发货",
    "R3_VIRTUAL_GOODS": "虚拟服务 会员权益 激活 不支持退款",
    "R3_CUSTOM_GOODS": "定制商品 不支持七天无理由退货 品类",
    "R4_OUT_OF_WINDOW": "七天无理由退货 签收次日起算 7 个自然日 质量问题 15 天",
    "R5_DUPLICATE": "退款处理中 重复申请 退款单",
    "R8_MANUAL_REVIEW": "人工审批 高额退款 工单等级 响应时效",
}


# ── 审批单（幂等） ──────────────────────────────────────────────────────────
def _ensure_approval(repo, state: SupportState, payload: dict[str, Any]) -> dict:
    """同一 (thread_id, order_no) 只保留一张待审单。

    为什么必须幂等：LangGraph 恢复执行时，调用 interrupt() 的节点会从头重跑，
    任何写在 interrupt 之前的副作用都会执行两次。这里用"先查后建"保证
    恢复后不会生成重复审批单。

    幂等键从 thread_id 收紧到 (thread_id, order_no)：一个会话里可能先后
    处理两笔不同订单，只按 thread 去重会把第二笔误判成第一笔。
    """
    thread_id = state.get("thread_id", "unknown")
    order_no = payload.get("order_no")
    if isinstance(order_no, str) and order_no:
        existing = repo.find_pending_approval(thread_id, order_no)
    else:
        existing = repo.find_pending_approval(thread_id)
    if existing:
        return existing

    approval = repo.create_approval(
        thread_id=thread_id,
        intent=payload.get("intent", "refund"),
        amount=float(payload.get("amount", 0.0)),
        reason=payload.get("reason", ""),
        risk_notes=payload.get("risk_notes", []),
        payload=payload,
        order_no=payload.get("order_no"),
    )
    return approval.to_dict()


# ── 评估 ────────────────────────────────────────────────────────────────────
def _policy_citations(decision, product_name: str = "") -> list[dict]:
    """给退款结论配政策依据：按命中规则锁定文档，同一文档只留一条。

    不能让用户看到"超期拒绝"却引用《审批流》——依据错配比没有依据更糟。
    """
    retriever = get_retriever()
    allowed = RULE_DOC_HINTS.get(decision.rule_code, POLICY_DOCS)
    query = RULE_QUERIES.get(
        decision.rule_code,
        f"退换货申请条件 七天无理由 时效 流程 {product_name}",
    )

    items = [item for item in retriever.search(query, k=10) if item.chunk.doc_id in allowed]

    picked, seen_docs = [], set()
    for item in items:
        if item.chunk.doc_id in seen_docs:
            continue
        seen_docs.add(item.chunk.doc_id)
        picked.append(item)
        if len(picked) >= 2:
            break

    return to_citations(picked)


def _decision_text(decision, citations: list[dict]) -> str:
    lines = [decision.reason]

    passed = [check for check in decision.checks if check.passed]
    failed = [check for check in decision.checks if not check.passed]
    if decision.eligible:
        lines.append("")
        lines.append("命中规则：" + "、".join(f"{c.rule_id} {c.title}" for c in passed))
    elif failed:
        lines.append("")
        lines.append("未通过：" + "；".join(f"{c.rule_id} {c.title}（{c.detail}）" for c in failed))

    if decision.eligible and decision.breakdown:
        b = decision.breakdown
        bearer = "平台承担" if b["shipping_deducted"] == 0 else "用户承担"
        lines.append("")
        lines.append(
            f"金额核算：标价 ¥{b['goods_amount']:.2f}，优惠 −¥{b['coupon_deducted']:.2f}，"
            f"运费 ¥{b['shipping_fee']:.2f}（{bearer}）→ 应退 ¥{b['refundable']:.2f}"
        )

    body = "\n".join(lines)
    if citations:
        body += "\n\n" + sources_block(citations)
    return body


def evaluate_refund_request(state: SupportState) -> dict:
    repo = get_repository()
    settings = get_settings()
    message = state.get("message", "")
    order_no = (state.get("order_no") or "").strip()

    order = repo.get_order(order_no) if order_no else repo.latest_order(state.get("customer_id", "C1001"))

    # 越权：订单号是别人的，一律按"查不到"处理，不给任何订单信息
    if order is not None and is_foreign_order(order, state):
        return {
            "response": (
                "没有查到这笔订单，请确认订单号是否属于当前账号；"
                "如果是代他人咨询，请让本人登录后操作。"
            ),
            "suggestions": ["我的订单", "转人工"],
            **trace("退款评估", status="warn", detail=f"越权访问订单 {order.order_no}，已拒绝"),
        }

    if order is None:
        return {
            "response": (
                "退款需要先定位订单。请提供以 SO 开头的订单号，例如 SO202610180001，"
                "我马上帮你核对是否符合退款条件。"
            ),
            "suggestions": ["我的订单", "转人工"],
            **trace("退款评估", status="warn", detail="缺少可定位的订单"),
        }

    refunds = repo.list_refunds(order.order_no)
    prior_count = repo.count_customer_refunds(order.customer_id)
    quality_claim = bool(state.get("entities", {}).get("quality_claim"))

    decision = evaluate_refund(
        order,
        refunds,
        settings=settings,
        quality_claim=quality_claim,
        prior_refund_count=prior_count,
        delivery_dispute=has_delivery_dispute(message),
    )
    citations = _policy_citations(decision, order.product_name)

    return {
        "order_no": order.order_no,
        "order_snapshot": order.to_dict(),
        "payments": [payment.to_dict() for payment in repo.get_payments(order.order_no)],
        "policy": decision.to_dict(),
        "citations": citations,
        "confidence": 0.92,
        "response": _decision_text(decision, citations),
        "suggestions": ["转人工"] if decision.requires_approval else [],
        **trace(
            "政策判定",
            status="ok" if decision.eligible else "warn",
            detail=f"{decision.rule_code} · {decision.action}",
            checks=[check.to_dict() for check in decision.checks],
        ),
    }


def route_after_evaluate(state: SupportState) -> str:
    policy = state.get("policy") or {}
    if not policy:
        return "ask"
    action = policy.get("action")
    if action == "auto_refund":
        return "execute"
    if action == "manual_review":
        return "approval"
    return "reject"


# ── 人工审批（interrupt） ───────────────────────────────────────────────────
def request_refund_approval(state: SupportState) -> dict:
    repo = get_repository()
    policy = state.get("policy", {})
    snapshot = state.get("order_snapshot", {})

    payload = {
        "type": "refund_approval",
        "intent": "refund",
        "order_no": snapshot.get("order_no"),
        "product_name": snapshot.get("product_name"),
        "amount": policy.get("refundable_amount", 0.0),
        "reason": policy.get("reason", ""),
        "risk_notes": policy.get("risk_notes", []),
        "checks": policy.get("checks", []),
        "breakdown": policy.get("breakdown", {}),
        "citations": state.get("citations", []),
        "thread_id": state.get("thread_id"),
    }

    approval = _ensure_approval(repo, state, payload)

    decision = interrupt(
        {
            **payload,
            "approval_id": approval["approval_id"],
            "created_at": approval["created_at"],
        }
    )

    return {
        "approval": {**payload, "approval_id": approval["approval_id"]},
        "approval_decision": decision or {},
        **trace(
            "人工审批",
            status="ok",
            detail=f"{approval['approval_id']} · {(decision or {}).get('decision', 'unknown')}",
        ),
    }


def route_after_approval(state: SupportState) -> str:
    decision = (state.get("approval_decision") or {}).get("decision", "")
    return "approve" if decision == "approve" else "reject"


# ── 执行 / 驳回 ─────────────────────────────────────────────────────────────
def _abort(message: str, detail: str, *, status: str = "warn") -> dict:
    """执行前复核不通过：不写任何数据，给出可理解的结论。"""
    return {
        "response": message,
        "suggestions": ["转人工", "我的订单"],
        **trace("退款执行", status=status, detail=detail),
    }


def execute_refund(state: SupportState) -> dict:
    """执行退款：先复核，再在一个事务里落账。

    三道防线：
    1. TOCTOU —— 审批可能挂了几小时，订单状态/金额可能已变，必须重读重判；
    2. CAS —— 审批单只有仍然 pending 才能抢到，杜绝并发重复执行；
    3. 事务 + 唯一索引 —— 退款单与工单同生共死，同一订单不会有两笔进行中退款。
    """
    repo = get_repository()
    settings = get_settings()
    policy = state.get("policy", {})
    snapshot = state.get("order_snapshot", {})
    decision = state.get("approval_decision") or {}
    approval = state.get("approval") or {}
    order_no = snapshot.get("order_no", "")
    manual = bool(approval)
    approval_id = approval.get("approval_id")

    reviewer = decision.get("reviewer") or ("system" if not manual else "reviewer")
    note = decision.get("note") or ""

    # ① 复核
    order = repo.get_order(order_no) if order_no else None
    if order is None:
        return _abort("订单已不存在，无法退款，已转人工核实。", "ORDER_GONE")

    recheck = evaluate_refund(
        order,
        repo.list_refunds(order.order_no),
        settings=settings,
        quality_claim=bool(state.get("entities", {}).get("quality_claim")),
        prior_refund_count=repo.count_customer_refunds(order.customer_id),
        delivery_dispute=has_delivery_dispute(state.get("message", "")),
    )
    if not recheck.eligible:
        return _abort(f"执行前复核未通过：{recheck.reason}", recheck.rule_code)

    expected = float(policy.get("refundable_amount", 0.0))
    if abs(recheck.refundable_amount - expected) > 0.01:
        return _abort(
            f"订单金额发生变化（审批时 ¥{expected:.2f}，现在 ¥{recheck.refundable_amount:.2f}），"
            "为避免退错金额，请重新发起申请。",
            "AMOUNT_CHANGED",
        )

    # ②③ 事务内：抢审批单 → 建退款单 → 建工单
    try:
        refund, ticket = repo.commit_refund(
            thread_id=state.get("thread_id", "unknown"),
            order_no=order.order_no,
            amount=recheck.refundable_amount,
            reason=state.get("human_reason") or "用户申请退款",
            status="approved" if manual else "pending",
            decided_by=reviewer,
            note=note,
            approval_id=approval_id,
            summary=f"退款 ¥{recheck.refundable_amount:.2f} · {policy.get('rule_code', '')}",
            meta={
                "rule_code": policy.get("rule_code"),
                "breakdown": recheck.breakdown,
            },
        )
    except ApprovalConflictError:
        return _abort("这笔审批已经被其他同事处理过了，本次不会重复退款。", "APPROVAL_CONFLICT")
    except DuplicateRefundError:
        return _abort("该订单已有进行中的退款单，本次不会重复创建。", "DUPLICATE_REFUND")

    b = recheck.breakdown
    lines = [
        f"退款已提交：{refund.refund_no}",
        (
            f"应退金额：¥{refund.amount:.2f}"
            f"（标价 ¥{b['goods_amount']:.2f}，优惠 −¥{b['coupon_deducted']:.2f}，"
            f"运费 ¥{b['shipping_fee']:.2f}）"
        ),
        f"当前状态：{'人工已通过，待仓库质检后打款' if manual else '待仓库质检后打款'}",
        "预计到账：1-3 个工作日（原路退回）",
        f"售后工单：{ticket.ticket_no}",
    ]
    if note:
        lines.append(f"审核备注：{note}")

    return {
        "response": "\n".join(lines),
        "tickets": [ticket.to_dict()],
        "suggestions": ["退款进度", "转人工"],
        **trace("退款执行", detail=f"{refund.refund_no} · ¥{refund.amount:.2f}"),
    }


def reject_refund(state: SupportState) -> dict:
    repo = get_repository()
    policy = state.get("policy", {})
    snapshot = state.get("order_snapshot", {})
    decision = state.get("approval_decision") or {}
    approval = state.get("approval") or {}
    approval_id = approval.get("approval_id")
    note = decision.get("note") or ""
    reviewer = decision.get("reviewer") or "reviewer"

    if decision.get("decision") == "reject":
        # 先把审批单原子关掉：否则审核队列会永远挂着一条已经处理过的单
        if approval_id and not repo.claim_approval(
            approval_id,
            status="rejected",
            decided_by=reviewer,
            note=note,
        ):
            return {
                "response": "这笔审批已经被其他同事处理过了，请刷新审核队列。",
                "suggestions": ["转人工"],
                **trace("退款驳回", status="warn", detail="APPROVAL_CONFLICT"),
            }

        response = (
            f"审核结果：本次退款未通过。\n原因：{note or '不符合当前退款政策'}\n"
            "如果你有新的凭证（如故障视频、检测报告），可以补充后重新申请。"
        )
        repo.create_ticket(
            thread_id=state.get("thread_id", "unknown"),
            intent="refund",
            order_no=snapshot.get("order_no"),
            level="P2",
            status="rejected",
            summary=f"退款被驳回 · {note or policy.get('rule_code', '')}",
            meta={"approval_id": approval_id},
        )
    else:
        response = policy.get(
            "reason",
            "本次申请不符合退款条件，如果需要进一步核实可以转人工。",
        )
        block = sources_block(state.get("citations", []))
        if block:
            response += "\n\n" + block

    return {
        "response": response,
        "suggestions": ["转人工", "查看退换货政策"],
        **trace("退款驳回", status="warn", detail=note or policy.get("rule_code", "")),
    }
