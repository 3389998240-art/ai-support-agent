from __future__ import annotations

from langgraph.types import interrupt

from app.agents.common import trace
from app.deps import get_repository
from app.graph.state import SupportState

LEVEL_BY_REASON = {
    "投诉": "P0",
    "人身安全": "P0",
    "监管": "P0",
    "律师": "P1",
    "媒体": "P1",
}


def _level_for(state: SupportState) -> str:
    text = state.get("message", "")
    for keyword, level in LEVEL_BY_REASON.items():
        if keyword in text:
            return level
    intent = state.get("intent", "")
    return "P2" if intent in {"refund", "human"} else "P3"


def _ensure_approval(repo, state: SupportState, payload: dict) -> dict:
    """人工接管按 thread 幂等（一个会话同一时刻只可能有人在处理）。"""
    thread_id = state.get("thread_id", "unknown")
    existing = repo.find_pending_approval(thread_id)
    if existing:
        return existing
    approval = repo.create_approval(
        thread_id=thread_id,
        intent=payload.get("intent", "human"),
        amount=float(payload.get("amount", 0.0)),
        reason=payload.get("reason", ""),
        risk_notes=payload.get("risk_notes", []),
        payload=payload,
        order_no=payload.get("order_no"),
    )
    return approval.to_dict()


def human_handoff(state: SupportState) -> dict:
    """人工受理节点：真正的 interrupt 暂停 + 恢复后落工单。

    恢复时节点会重跑，因此审批单创建必须幂等（见 _ensure_approval）。
    """
    repo = get_repository()
    level = _level_for(state)
    reason = state.get("human_reason") or "用户请求人工 / 自动判定需要人工"

    payload = {
        "type": "human_handoff",
        "intent": state.get("intent", "human"),
        "level": level,
        "reason": reason,
        "message": state.get("message", ""),
        "draft_answer": state.get("answer", ""),
        "citations": state.get("citations", []),
        "order_no": state.get("order_no") or None,
        "thread_id": state.get("thread_id"),
    }
    approval = _ensure_approval(repo, state, payload)

    decision = interrupt({**payload, "approval_id": approval["approval_id"]})

    reply = ""
    if isinstance(decision, dict):
        reply = str(decision.get("message") or decision.get("note") or "").strip()

    from app.agents.common import now_label  # 局部导入避免循环

    # 先原子认领，再建工单：否则抢单失败会留下一条重复工单
    if not repo.claim_approval(
        approval["approval_id"],
        status="approved",
        decided_by=str((decision or {}).get("reviewer") or "human_agent"),
        note=reply or f"{now_label()} 人工接管",
    ):
        return {
            "response": "这条会话已经被其他同事接管了，请在审核台刷新查看。",
            "suggestions": ["转人工"],
            **trace("人工受理", status="warn", detail="APPROVAL_CONFLICT"),
        }

    ticket = repo.create_ticket(
        thread_id=state.get("thread_id", "unknown"),
        intent=state.get("intent", "human"),
        order_no=state.get("order_no") or None,
        level=level,
        status="open",
        summary=state.get("message", "")[:120],
        meta={"approval_id": approval["approval_id"], "draft_answer": state.get("answer", "")},
    )

    response = f"已为你转人工，工单号 {ticket.ticket_no}（{level}），客服会尽快跟进。"
    if reply:
        response += f"\n\n客服回复：{reply}"

    return {
        "response": response,
        "tickets": [ticket.to_dict()],
        "approval": {**payload, "approval_id": approval["approval_id"]},
        "approval_decision": decision if isinstance(decision, dict) else {},
        "requires_human": True,
        **trace(
            "人工受理",
            status="ok",
            detail=f"{ticket.ticket_no} · {level}",
            level=level,
        ),
    }
