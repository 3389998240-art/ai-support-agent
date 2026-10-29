from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class SupportState(TypedDict, total=False):
    """贯穿父子图的共享状态。

    只放"跨节点需要传递"的数据；节点内部的临时变量一律留在函数里，
    避免状态膨胀到无法审计。
    """

    # 会话标识
    thread_id: str
    customer_id: str
    # 谁在操作：customer / agent / admin。客服可代客查询，客户只能查自己的
    actor_role: str

    # 输入
    message: str
    normalized_message: str
    guard: dict[str, Any]

    # 路由
    intent: str
    intent_confidence: float
    routing_source: str  # deterministic | llm | fallback
    entities: dict[str, Any]

    # 业务事实
    order_no: str
    order_snapshot: dict[str, Any]
    payments: list[dict[str, Any]]
    policy: dict[str, Any]
    tickets: list[dict[str, Any]]

    # RAG
    retrieved: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    answer: str
    confidence: float
    verification: dict[str, Any]
    grading: dict[str, Any]
    on_topic: bool
    doc_filter: tuple[str, ...]

    # 人工介入
    requires_human: bool
    human_reason: str
    approval: dict[str, Any]
    approval_decision: dict[str, Any]

    # 输出
    response: str
    suggestions: list[str]
    degraded: bool
    trace: Annotated[list[dict[str, Any]], lambda left, right: (left or []) + (right or [])]
    messages: Annotated[list[AnyMessage], add_messages]


SupportIntent = str
