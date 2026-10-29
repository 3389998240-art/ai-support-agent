from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from app.agents.fallback import fallback, receive_query
from app.agents.guard import route_after_safety, safety_check
from app.agents.human import human_handoff
from app.agents.knowledge import build_knowledge_graph
from app.agents.order import build_order_graph
from app.agents.wrappers import make_subgraph_node
from app.agents.refund import (
    evaluate_refund_request,
    execute_refund,
    reject_refund,
    request_refund_approval,
    route_after_approval,
    route_after_evaluate,
)
from app.agents.common import trace
from app.graph.classifier import classify_intent
from app.graph.state import SupportState

DEFAULT_SUGGESTIONS = {
    "policy": ["七天无理由怎么算", "退款多久到账", "转人工"],
    "order": ["查物流异常", "申请退款", "发票怎么开"],
    "refund": ["退款进度", "退换货运费谁承担", "转人工"],
    "invoice": ["电子发票怎么开", "保修多久", "转人工"],
    "human": ["转人工", "退款政策"],
    "unknown": ["查订单物流", "我要退款", "转人工"],
}

INTENT_ROUTES = {
    "policy": "knowledge_workflow",
    "invoice": "invoice_workflow",
    "order": "order_workflow",
    "refund": "evaluate_refund_request",
    "human": "human_support",
    "unknown": "fallback_workflow",
}


def route_by_intent(state: SupportState) -> str:
    """返回 intent 键，由 INTENT_ROUTES 映射到具体节点。"""
    intent = state.get("intent", "unknown")
    return intent if intent in INTENT_ROUTES else "unknown"


def route_after_rag(state: SupportState) -> str:
    """知识库答不上来（依据不足 / 无命中）时，交人工而不是硬答。"""
    return "escalate" if state.get("requires_human") else "finalize"


def finalize_response(state: SupportState) -> dict:
    response = (state.get("response") or "").strip()
    if not response:
        response = "抱歉，这次没有生成有效回复，已记录该问题，可以转人工继续处理。"

    intent = state.get("intent", "unknown")
    suggestions = state.get("suggestions") or DEFAULT_SUGGESTIONS.get(intent, [])

    return {
        "response": response,
        "suggestions": suggestions[:4],
        "messages": [AIMessage(content=response)],
        **trace(
            "回复完成",
            detail=f"intent={intent} · 置信度 {state.get('confidence', 0):.2f}",
        ),
    }


def build_graph(checkpointer=None, service=None):
    builder = StateGraph(SupportState)

    # ── 子图 ─────────────────────────────────────────────
    knowledge_graph = build_knowledge_graph()
    invoice_graph = build_knowledge_graph(("04-发票与保修服务", "02-退款规则与到账时效"))
    order_graph = build_order_graph()

    # ── 节点 ─────────────────────────────────────────────
    builder.add_node("receive_query", receive_query)
    builder.add_node("safety_check", safety_check)
    builder.add_node("classify_intent", classify_intent)

    builder.add_node("knowledge_workflow", make_subgraph_node(knowledge_graph, label="knowledge"))
    builder.add_node("invoice_workflow", make_subgraph_node(invoice_graph, label="invoice"))
    builder.add_node("order_workflow", make_subgraph_node(order_graph, label="order"))
    builder.add_node("fallback_workflow", fallback)

    builder.add_node("evaluate_refund_request", evaluate_refund_request)
    builder.add_node("request_refund_approval", request_refund_approval)
    builder.add_node("execute_refund", execute_refund)
    builder.add_node("reject_refund", reject_refund)

    builder.add_node("human_support", human_handoff)
    builder.add_node("finalize_response", finalize_response)

    # ── 入口与意图路由 ───────────────────────────────────
    builder.add_edge(START, "receive_query")
    builder.add_edge("receive_query", "safety_check")
    builder.add_conditional_edges(
        "safety_check",
        route_after_safety,
        {"blocked": "finalize_response", "ok": "classify_intent"},
    )
    builder.add_conditional_edges("classify_intent", route_by_intent, INTENT_ROUTES)

    # ── 知识类子图 ───────────────────────────────────────
    for node in ("knowledge_workflow", "invoice_workflow"):
        builder.add_conditional_edges(
            node,
            route_after_rag,
            {"escalate": "human_support", "finalize": "finalize_response"},
        )

    builder.add_edge("order_workflow", "finalize_response")
    builder.add_edge("fallback_workflow", "finalize_response")

    # ── 退款：规则判定 → 自助/审批/驳回 ──────────────────
    builder.add_conditional_edges(
        "evaluate_refund_request",
        route_after_evaluate,
        {
            "execute": "execute_refund",
            "approval": "request_refund_approval",
            "reject": "reject_refund",
            "ask": "finalize_response",
        },
    )
    builder.add_conditional_edges(
        "request_refund_approval",
        route_after_approval,
        {"approve": "execute_refund", "reject": "reject_refund"},
    )
    builder.add_edge("execute_refund", "finalize_response")
    builder.add_edge("reject_refund", "finalize_response")

    # ── 人工与收尾 ───────────────────────────────────────
    builder.add_edge("human_support", "finalize_response")
    builder.add_edge("finalize_response", END)

    return builder.compile(checkpointer=checkpointer)
