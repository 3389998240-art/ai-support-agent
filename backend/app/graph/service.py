from __future__ import annotations

import uuid
from typing import Any, Iterator

from langgraph.types import Command

from app.core.context import get_request_id
from app.core.errors import (
    NotWaitingError,
    ThreadBusyError,
    ThreadForbiddenError,
    ThreadNotFoundError,
)
from app.deps import get_repository
from app.graph.state import SupportState

DEFAULT_CUSTOMER = "C1001"


class SupportService:
    """把 LangGraph 的执行细节包起来，对外只暴露"会话"语义。

    API 层因此不需要知道 checkpointer、interrupt、state 快照这些概念。
    """

    def __init__(self, graph) -> None:
        self.graph = graph
        self.repo = get_repository()

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def _snapshot(self, thread_id: str):
        return self.graph.get_state(self._config(thread_id))

    def _assert_not_orphan_takeover(self, thread_id: str, snapshot, actor_role: str) -> None:
        """有状态但没有归属记录的会话（认证上线前遗留），不允许客户接管。

        否则：知道一个旧 thread_id 就能往里面发消息，然后把整段历史读走。
        客服/管理员不受限（他们本来就能介入）。
        """
        if actor_role != "customer":
            return
        if not snapshot.values:
            return
        if self.repo.get_thread_owner(thread_id) is None:
            raise ThreadForbiddenError(thread_id)

    def _payload(self, thread_id: str) -> dict[str, Any]:
        snapshot = self._snapshot(thread_id)
        values: SupportState = snapshot.values or {}
        pending = snapshot.interrupts[0].value if snapshot.interrupts else None

        return {
            "thread_id": thread_id,
            "request_id": get_request_id(),
            "status": "waiting_human" if pending else "completed",
            "reply": values.get("response", ""),
            "intent": values.get("intent", "unknown"),
            "intent_confidence": values.get("intent_confidence", 0.0),
            "routing_source": values.get("routing_source", ""),
            "confidence": values.get("confidence", 0.0),
            "citations": values.get("citations", []),
            "policy": values.get("policy"),
            "order": values.get("order_snapshot") or None,
            "trace": values.get("trace", []),
            "suggestions": values.get("suggestions", []),
            "pending_review": pending,
            "degraded": bool(values.get("degraded")),
        }

    # ------------------------------------------------------------------ 会话
    def chat(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        customer_id: str = DEFAULT_CUSTOMER,
        actor_role: str = "customer",
    ) -> dict[str, Any]:
        thread_id = thread_id or f"t-{uuid.uuid4().hex[:12]}"
        snapshot = self._snapshot(thread_id)
        self._assert_not_orphan_takeover(thread_id, snapshot, actor_role)
        self.repo.bind_thread_owner(thread_id, customer_id)

        if snapshot.interrupts:
            raise ThreadBusyError(thread_id)

        self.graph.invoke(
            {
                "message": message,
                "thread_id": thread_id,
                "customer_id": customer_id,
                "actor_role": actor_role,
            },
            config=self._config(thread_id),
        )
        return self._payload(thread_id)

    def stream_chat(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        customer_id: str = DEFAULT_CUSTOMER,
        actor_role: str = "customer",
    ) -> Iterator[dict[str, Any]]:
        """逐节点推送执行事件，最后给出完整结果（供 SSE 使用）。"""
        thread_id = thread_id or f"t-{uuid.uuid4().hex[:12]}"
        snapshot = self._snapshot(thread_id)
        self._assert_not_orphan_takeover(thread_id, snapshot, actor_role)
        self.repo.bind_thread_owner(thread_id, customer_id)

        if snapshot.interrupts:
            raise ThreadBusyError(thread_id)

        yield {"type": "start", "thread_id": thread_id}

        for update in self.graph.stream(
            {
                "message": message,
                "thread_id": thread_id,
                "customer_id": customer_id,
                "actor_role": actor_role,
            },
            config=self._config(thread_id),
            stream_mode="updates",
        ):
            for node, payload in update.items():
                if not isinstance(payload, dict):
                    continue
                for item in payload.get("trace", []) or []:
                    yield {"type": "trace", "node": node, "data": item}

        yield {"type": "result", "data": self._payload(thread_id)}

    def resume(
        self,
        thread_id: str,
        *,
        decision: str,
        note: str = "",
        reviewer: str = "reviewer",
        message: str = "",
    ) -> dict[str, Any]:
        snapshot = self._snapshot(thread_id)
        if not snapshot.values:
            raise ThreadNotFoundError(thread_id)
        if not snapshot.interrupts:
            raise NotWaitingError(thread_id)

        payload = {
            "decision": decision,
            "note": note,
            "reviewer": reviewer,
            "message": message or note,
        }

        self.graph.invoke(Command(resume=payload), config=self._config(thread_id))
        return self._payload(thread_id)

    def history(self, thread_id: str) -> dict[str, Any]:
        snapshot = self._snapshot(thread_id)
        values: SupportState = snapshot.values or {}

        messages = []
        for item in values.get("messages", []) or []:
            role = getattr(item, "type", None)
            content = getattr(item, "content", "")
            if role is None and isinstance(item, dict):
                role = item.get("role") or item.get("type")
                content = item.get("content", "")
            messages.append(
                {
                    "role": "user" if role in {"human", "user"} else "assistant",
                    "content": str(content),
                }
            )

        return {
            "thread_id": thread_id,
            "messages": messages,
            "intent": values.get("intent", "unknown"),
            "citations": values.get("citations", []),
            "trace": values.get("trace", []),
            "policy": values.get("policy"),
            "status": "waiting_human" if snapshot.interrupts else "completed",
            "pending_review": snapshot.interrupts[0].value if snapshot.interrupts else None,
        }
