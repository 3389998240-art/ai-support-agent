from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents.common import trace
from app.graph.state import SupportState


def receive_query(state: SupportState) -> dict:
    message = (state.get("message") or "").strip()
    return {
        "message": message,
        "messages": [("human", message)],
        **trace("接收消息", detail=f"{len(message)} 字"),
    }


GREETINGS = ("你好", "您好", "在吗", "hi", "hello", "有没有人", "早上好", "下午好", "晚上好")


def fallback(state: SupportState) -> dict:
    """意图不明时不要硬猜，给用户可以点的下一步。"""
    message = (state.get("message") or "").strip().lower()

    if any(word in message for word in GREETINGS) and len(message) <= 12:
        return {
            "response": (
                "你好，我是星辰科技售后助手。可以帮你查订单物流、申请退款、"
                "解答退换货政策、发票与保修问题。你想先处理哪一件？"
            ),
            "suggestions": ["查订单物流", "七天无理由怎么算", "发票怎么开", "转人工"],
            "confidence": 0.5,
            **trace("问候回复", detail="识别为打招呼"),
        }

    return {
        "response": (
            "我是售后助手，这个问题我还不太确定该走哪条流程。你可以直接说："
            "「查订单物流」「我要退款」「发票怎么开」「转人工」，我按对应流程帮你办。"
        ),
        "suggestions": ["查订单物流", "我要退款", "发票怎么开", "转人工"],
        "confidence": 0.2,
        **trace("兜底回复", status="warn", detail="意图不明确，给出引导选项"),
    }


def build_fallback_graph():
    builder = StateGraph(SupportState)
    builder.add_node("fallback", fallback)
    builder.add_edge(START, "fallback")
    builder.add_edge("fallback", END)
    return builder.compile()
