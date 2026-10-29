from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    username: str
    display_name: str
    role: str
    customer_id: str | None = None
    permissions: list[str] = Field(default_factory=list)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: int
    user: UserOut


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = None
    customer_id: str = "C1001"


class ResumeRequest(BaseModel):
    decision: Literal["approve", "reject"] = "approve"
    note: str = ""
    # reviewer 由 JWT 决定，这里保留字段仅为兼容旧前端，服务端不采信
    reviewer: str | None = None
    message: str = ""


class ChatResponse(BaseModel):
    thread_id: str
    request_id: str = ""
    status: Literal["completed", "waiting_human"]
    reply: str = ""
    intent: str = "unknown"
    intent_confidence: float = 0.0
    routing_source: str = ""
    confidence: float = 0.0
    citations: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    policy: dict[str, Any] | None = None
    order: dict[str, Any] | None = None
    pending_review: dict[str, Any] | None = None
    degraded: bool = False


class HistoryMessage(BaseModel):
    role: str
    content: str


class HistoryResponse(BaseModel):
    thread_id: str
    messages: list[HistoryMessage] = Field(default_factory=list)
    intent: str = "unknown"
    status: str = "completed"
    citations: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    policy: dict[str, Any] | None = None
    pending_review: dict[str, Any] | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    k: int = 5


class SearchHit(BaseModel):
    id: str
    title: str
    section: str
    source: str
    doc_id: str
    snippet: str
    score: float
    vector_score: float
    keyword_score: float
    rank: int
