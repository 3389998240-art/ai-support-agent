"""请求级上下文。

用 ContextVar 传 request_id，好处是不用把它顺着 service → graph → node
一层层加参数，日志过滤器也能直接读到。
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    return f"req-{uuid.uuid4().hex[:10]}"


def set_request_id(value: str | None = None) -> str:
    request_id = value or new_request_id()
    _request_id.set(request_id)
    return request_id


def get_request_id() -> str:
    return _request_id.get()
