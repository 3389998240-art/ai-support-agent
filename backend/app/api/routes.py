from __future__ import annotations

import json
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.api.deps_auth import get_current_user, require_permission, require_user
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    HistoryResponse,
    LoginRequest,
    ResumeRequest,
    SearchHit,
    SearchRequest,
    TokenResponse,
    UserOut,
)
from app.core.config import get_settings
from app.core.errors import (
    NotWaitingError,
    ThreadBusyError,
    ThreadForbiddenError,
    ThreadNotFoundError,
)
from app.core.logging import get_logger
from app.core.metrics import METRICS
from app.core.ratelimit import SlidingWindowLimiter
from app.deps import get_repository, get_retriever
from app.domain.security import (
    ROLE_PERMISSIONS,
    CurrentUser,
    create_access_token,
    hash_password,
    verify_password,
)
from app.rag.ingest import index_summary, ingest_knowledge_base
from app.rag.types import clean_snippet

logger = get_logger(__name__)
router = APIRouter()

DEFAULT_CUSTOMER = "C1001"

_login_limiter = SlidingWindowLimiter(
    limit=get_settings().login_rate_limit,
    window_seconds=get_settings().login_rate_window_seconds,
)

# 账号不存在时用来"陪跑"一次哈希校验，抹平登录耗时差异
DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing")


def _service(request: Request):
    return request.app.state.service


def _user_out(user: CurrentUser) -> UserOut:
    granted = ROLE_PERMISSIONS.get(user.role, set())
    return UserOut(
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        customer_id=user.customer_id,
        permissions=sorted(granted),
    )


def _assert_thread_access(
    repo,
    thread_id: str | None,
    user: CurrentUser,
    *,
    allow_new: bool,
) -> None:
    """客户只能操作/读取自己的会话；客服与管理员可介入。

    allow_new=True 用于发消息：thread_id 还不存在时是"新建会话"，之后会绑定给当前客户。
    越权一律 404，不暴露"这个会话存在但不属于你"。
    """
    if not thread_id or user.role != "customer":
        return

    owner = repo.get_thread_owner(thread_id)
    if owner is None:
        if allow_new:
            return
        raise HTTPException(status_code=404, detail="会话不存在")
    if owner != user.customer_id:
        raise HTTPException(status_code=404, detail="会话不存在")


def _resolve_customer(user: CurrentUser, requested: str | None) -> str:
    """客户只能以自己的身份发起会话，避免传别人的 customer_id 查别人订单。"""
    if user.role == "customer":
        return user.customer_id or DEFAULT_CUSTOMER
    return requested or DEFAULT_CUSTOMER


# ── 认证 ────────────────────────────────────────────────────────────────────
@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request) -> TokenResponse:
    client_ip = request.client.host if request.client else "-"
    allowed, retry_after = _login_limiter.allow(f"{client_ip}:{payload.username}")
    if not allowed:
        METRICS.inc("login_rate_limited_total")
        raise HTTPException(
            status_code=429,
            detail=f"登录尝试过于频繁，请 {retry_after} 秒后再试",
            headers={"Retry-After": str(retry_after)},
        )

    repo = get_repository()
    record = repo.get_user(payload.username)

    # 用户名不存在时也跑一次哈希校验：否则"账号不存在"会明显更快，形成时序侧信道
    password_hash = record["password_hash"] if record else DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, password_hash)

    # 用户名不存在与密码错误返回同一句话，避免枚举账号
    if record is None or not password_ok:
        logger.info("登录失败 user=%s", payload.username)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token, expires_at = create_access_token(
        subject=record["username"],
        role=record["role"],
        display_name=record["display_name"],
        customer_id=record["customer_id"],
    )
    user = CurrentUser(
        username=record["username"],
        role=record["role"],
        display_name=record["display_name"],
        customer_id=record["customer_id"],
    )
    logger.info("登录成功 user=%s role=%s", user.username, user.role)
    return TokenResponse(access_token=token, expires_at=expires_at, user=_user_out(user))


@router.get("/auth/me", response_model=UserOut)
def me(user: CurrentUser = Depends(require_user)) -> UserOut:
    return _user_out(user)


@router.get("/auth/users")
def list_users(_: CurrentUser = Depends(require_permission("users:read"))) -> dict[str, Any]:
    items = get_repository().list_users()
    return {"items": items, "count": len(items)}


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(_: CurrentUser = Depends(require_permission("metrics:read"))) -> str:
    return METRICS.render()


# ── 健康检查（公开）──────────────────────────────────────────────────────────
@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    settings = get_settings()
    store = get_retriever().store
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
        "llm": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "enabled": settings.llm_enabled,
        },
        "knowledge": store.meta,
        "checkpointer": settings.checkpointer_backend,
        "auth": {
            "scheme": "JWT (HS256)",
            "roles": sorted(ROLE_PERMISSIONS),
            "agent_approval_limit": settings.agent_approval_limit,
        },
    }


# ── 会话 ────────────────────────────────────────────────────────────────────
@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    request: Request,
    user: CurrentUser = Depends(require_user),
) -> ChatResponse:
    service = _service(request)
    _assert_thread_access(get_repository(), payload.thread_id, user, allow_new=True)
    try:
        result = service.chat(
            payload.message,
            thread_id=payload.thread_id,
            customer_id=_resolve_customer(user, payload.customer_id),
            actor_role=user.role,
        )
    except ThreadBusyError as exc:
        raise HTTPException(
            status_code=409,
            detail="该会话正在等待人工审核，请先在审核工作台处理，然后再继续对话。",
        ) from exc
    except ThreadForbiddenError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc

    METRICS.inc("chat_turns_total", intent=str(result.get("intent") or "unknown"))
    if result.get("status") == "waiting_human":
        METRICS.inc("escalations_total", intent=str(result.get("intent") or "unknown"))
    if result.get("degraded"):
        METRICS.inc("degraded_turns_total")
    return ChatResponse(**result)


@router.post("/chat/stream")
def chat_stream(
    payload: ChatRequest,
    request: Request,
    user: CurrentUser = Depends(require_user),
) -> StreamingResponse:
    service = _service(request)
    customer_id = _resolve_customer(user, payload.customer_id)
    _assert_thread_access(get_repository(), payload.thread_id, user, allow_new=True)

    def event_stream() -> Iterator[str]:
        try:
            for event in service.stream_chat(
                payload.message,
                thread_id=payload.thread_id,
                customer_id=customer_id,
                actor_role=user.role,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except ThreadBusyError:
            error = {"type": "error", "detail": "会话正在等待人工审核，请先处理审批"}
            yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"
        except ThreadForbiddenError:
            error = {"type": "error", "detail": "会话不存在"}
            yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            error = {"type": "error", "detail": f"服务异常: {exc}"}
            yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/threads/{thread_id}", response_model=HistoryResponse)
def thread_history(
    thread_id: str,
    request: Request,
    user: CurrentUser = Depends(require_user),
) -> HistoryResponse:
    _assert_thread_access(get_repository(), thread_id, user, allow_new=False)

    service = _service(request)
    try:
        return HistoryResponse(**service.history(thread_id))
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc


@router.post("/threads/{thread_id}/resume", response_model=ChatResponse)
def resume(
    thread_id: str,
    payload: ResumeRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("reviews:decide")),
) -> ChatResponse:
    settings = get_settings()
    repo = get_repository()

    # ABAC：审批员有金额上限，超过要管理员。角色之外再看一眼金额。
    if payload.decision == "approve" and user.role != "admin":
        pending = repo.find_pending_approval(thread_id)
        if pending and float(pending["amount"]) > settings.agent_approval_limit:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"该笔退款 ¥{float(pending['amount']):.2f} 超过审核员上限 "
                    f"¥{settings.agent_approval_limit:.0f}，需要管理员审批"
                ),
            )

    service = _service(request)
    try:
        result = service.resume(
            thread_id,
            decision=payload.decision,
            note=payload.note,
            reviewer=user.username,  # 采信令牌身份，不采信请求体
            message=payload.message,
        )
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    except NotWaitingError as exc:
        raise HTTPException(status_code=409, detail="该会话不在等待人工的状态") from exc
    return ChatResponse(**result)


# ── 审核工作台 ──────────────────────────────────────────────────────────────
@router.get("/reviews")
def list_reviews(
    status: str = "pending",
    limit: int = 50,
    _: CurrentUser = Depends(require_permission("reviews:read")),
) -> dict[str, Any]:
    repo = get_repository()
    items = repo.list_approvals(status=None if status == "all" else status, limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/tickets")
def list_tickets(
    thread_id: str | None = None,
    limit: int = 20,
    _: CurrentUser = Depends(require_permission("tickets:read")),
) -> dict[str, Any]:
    repo = get_repository()
    items = repo.list_tickets(thread_id=thread_id, limit=limit)
    return {"items": items, "count": len(items)}


# ── 知识库 ──────────────────────────────────────────────────────────────────
@router.get("/knowledge")
def knowledge_summary(
    request: Request,
    _: CurrentUser = Depends(require_permission("knowledge:read")),
) -> dict[str, Any]:
    settings = get_settings()
    store = get_retriever().store
    return index_summary(settings, store)


@router.post("/knowledge/ingest")
def knowledge_ingest(
    _: CurrentUser = Depends(require_permission("knowledge:write")),
) -> dict[str, Any]:
    report = ingest_knowledge_base()
    from app import deps

    deps.get_retriever.cache_clear()
    return report.to_dict()


@router.post("/knowledge/search")
def knowledge_search(
    payload: SearchRequest,
    _: CurrentUser = Depends(require_permission("knowledge:search")),
) -> dict[str, Any]:
    retriever = get_retriever()
    items = retriever.search(payload.query, k=payload.k)
    hits = [
        SearchHit(
            id=item.chunk.id,
            title=item.chunk.title,
            section=item.chunk.section,
            source=item.chunk.source,
            doc_id=item.chunk.doc_id,
            snippet=clean_snippet(item.chunk.text)[:400],
            score=round(item.score, 4),
            vector_score=round(item.vector_score, 4),
            keyword_score=round(item.keyword_score, 4),
            rank=item.rank,
        )
        for item in items
    ]
    return {"query": payload.query, "hits": [hit.model_dump() for hit in hits]}


# ── 演示数据 ────────────────────────────────────────────────────────────────
@router.get("/demo/orders")
def demo_orders(
    customer_id: str | None = None,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    if user is None:
        raise HTTPException(status_code=401, detail="需要登录")
    if user.role == "customer":
        target = user.customer_id or DEFAULT_CUSTOMER
    else:
        target = customer_id or DEFAULT_CUSTOMER

    repo = get_repository()
    orders = repo.list_orders(target)
    return {"customer_id": target, "items": [order.to_dict() for order in orders]}
