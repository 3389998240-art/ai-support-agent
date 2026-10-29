from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.common import is_foreign_order, sources_block, to_citations, trace
from app.deps import get_repository, get_retriever
from app.graph.state import SupportState

STATUS_LABEL = {
    "pending": "待支付",
    "paid": "已支付待发货",
    "shipped": "已发货",
    "delivered": "已签收",
    "cancelled": "已取消",
}


def resolve_order(state: SupportState) -> dict:
    """用工具查真实订单数据——不让模型凭印象说"您的包裹正在派送中"。"""
    repo = get_repository()
    order_no = (state.get("order_no") or "").strip()
    order = None

    if order_no:
        order = repo.get_order(order_no)
    else:
        order = repo.latest_order(state.get("customer_id", "C1001"))

    if order is None or is_foreign_order(order, state):
        detail = (
            f"订单 {order.order_no} 不属于当前客户，已按未找到处理"
            if order is not None
            else f"未找到订单 {order_no or '(未提供)'}"
        )
        return {
            "order_snapshot": {},
            **trace("订单查询", status="warn", detail=detail),
        }

    payments = [payment.to_dict() for payment in repo.get_payments(order.order_no)]
    return {
        "order_no": order.order_no,
        "order_snapshot": order.to_dict(),
        "payments": payments,
        **trace(
            "订单查询",
            detail=f"{order.order_no} · {STATUS_LABEL.get(order.status, order.status)}",
            source="sqlite:orders",
        ),
    }


def route_after_resolve(state: SupportState) -> str:
    return "found" if state.get("order_snapshot") else "missing"


def order_missing(state: SupportState) -> dict:
    return {
        "response": (
            "我没有查到这个订单。请提供以 SO 开头的订单号（例如 SO202610180001），"
            "或者告诉我下单时用的手机号后四位，我再帮你确认。"
        ),
        "suggestions": ["我的订单", "退款政策", "转人工"],
        **trace("订单查询", status="warn", detail="缺少订单号，向用户追问"),
    }


def compose_order_answer(state: SupportState) -> dict:
    snapshot = state.get("order_snapshot", {})
    order_no = snapshot.get("order_no", "")
    status = STATUS_LABEL.get(snapshot.get("status", ""), snapshot.get("status", ""))
    amount = snapshot.get("amount", 0.0)
    payment = (state.get("payments") or [{}])[0]

    lines = [
        f"订单 {order_no} · {snapshot.get('product_name', '')}",
        f"状态：{status}",
        f"金额：¥{amount:.2f}（{payment.get('method', '支付方式未知')}）",
    ]

    if snapshot.get("tracking_no"):
        lines.append(
            f"物流：{snapshot.get('carrier', '')} {snapshot.get('tracking_no', '')}"
        )
    if snapshot.get("delivered_at"):
        lines.append(f"签收时间：{snapshot['delivered_at'][:10]}")

    # 物流类问题补一条政策依据，让用户知道"多久没更新该催单"
    citations: list[dict] = []
    retriever = get_retriever()
    items = [
        item
        for item in retriever.search(f"{state.get('message', '')} 发货时效 物流异常", k=4)
        if item.chunk.doc_id == "03-物流与配送规则"
    ]
    if items:
        citations = to_citations(items[:1])

    body = "\n".join(lines)
    if citations:
        body += "\n\n" + sources_block(citations)

    return {
        "citations": citations,
        "response": body,
        "confidence": 0.9,
        "suggestions": ["申请退款", "查物流异常", "发票怎么开"],
        **trace(
            "业务工具调用",
            detail=f"读取订单与支付记录 ({len(state.get('payments') or [])} 条支付)",
            tools=["orders.get", "payments.list"],
        ),
    }


def build_order_graph():
    builder = StateGraph(SupportState)
    builder.add_node("resolve_order", resolve_order)
    builder.add_node("order_missing", order_missing)
    builder.add_node("compose_order_answer", compose_order_answer)

    builder.add_edge(START, "resolve_order")
    builder.add_conditional_edges(
        "resolve_order",
        route_after_resolve,
        {"found": "compose_order_answer", "missing": "order_missing"},
    )
    builder.add_edge("order_missing", END)
    builder.add_edge("compose_order_answer", END)

    return builder.compile()
