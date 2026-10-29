from __future__ import annotations

from typing import Any, Callable

from app.graph.state import SupportState


def make_subgraph_node(graph, *, label: str) -> Callable[[SupportState], dict[str, Any]]:
    """把编译好的子图包成一个父图节点。

    为什么需要这层包装：子图和父图共用同一个 state schema 时，
    子图的输出会包含"从父图继承来的 trace"，父图的 add reducer 会
    再追加一次，导致时间线重复。这里在进入子图前摘掉 trace，
    在返回时只把子图自己产生的事件交回父图。
    """

    def node(state: SupportState) -> dict[str, Any]:
        # 只摘掉 trace（父图 reducer 会二次追加）；
        # messages 要带进去，知识子图靠它做"上一轮问题"的指代消解。
        sub_input = {key: value for key, value in state.items() if key != "trace"}
        result = graph.invoke(sub_input)

        steps = result.pop("trace", []) or []
        result.pop("messages", None)  # 消息通道由父图的 finalize 统一写，避免重复
        result.pop("message", None)

        if label:
            result.setdefault("trace", [])
        return {**result, "trace": steps}

    node.__name__ = f"{label}_node"
    return node
