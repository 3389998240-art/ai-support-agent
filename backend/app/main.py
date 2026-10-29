from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings
from app.core.context import set_request_id
from app.core.logging import configure_logging, get_logger
from app.core.metrics import METRICS
from app.deps import get_repository, get_retriever
from app.graph.builder import build_graph
from app.graph.checkpointer import create_checkpointer
from app.graph.service import SupportService

configure_logging()
logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("启动 %s (%s)", settings.app_name, settings.environment)

    repo = get_repository()
    if repo.seed_demo_data():
        logger.info("已写入演示订单数据")

    if repo.ensure_demo_users(settings.demo_password):
        logger.info("已初始化演示账号（alice / bob / agent01 / admin）")

    store = get_retriever().store  # 首次调用会构建/加载 FAISS 索引
    logger.info("知识库就绪: %s 切片 / %s 文档", store.count, store.meta["documents"])

    checkpointer, close_checkpointer = create_checkpointer(settings)
    app.state.service = SupportService(build_graph(checkpointer=checkpointer))
    app.state.close_checkpointer = close_checkpointer

    logger.info(
        "LLM provider=%s enabled=%s",
        settings.llm_provider,
        settings.llm_enabled,
    )
    if settings.jwt_secret.startswith(("dev-only", "change-me")):
        if settings.environment not in {"dev", "test"}:
            raise RuntimeError(
                "JWT_SECRET 仍是默认值，拒绝在非 dev 环境启动；"
                "请注入 32 字节以上随机串（python -c \"import secrets;print(secrets.token_urlsafe(48))\"）"
            )
        logger.warning("JWT_SECRET 仍是默认值，仅限本地开发使用")
    yield

    close_checkpointer()
    logger.info("服务已关闭")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="售后支持 Agent API",
        version="1.0.0",
        description=(
            "企业售后支持 Agent：混合意图路由 + 多业务子图 + FAISS 知识库 RAG "
            "+ 人工审批中断/恢复。"
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        """给每个请求一个 request_id，并记录基础指标。"""
        import time

        request_id = set_request_id(request.headers.get("X-Request-Id"))
        request.state.request_id = request_id
        started = time.perf_counter()

        response = await call_next(request)

        elapsed = time.perf_counter() - started
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        METRICS.inc("http_requests_total", method=request.method, path=path, status=str(response.status_code))
        METRICS.observe("http_request_duration_seconds", elapsed, method=request.method, path=path)

        response.headers["X-Request-Id"] = request_id
        return response

    app.include_router(router, prefix=settings.api_prefix)

    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "docs": "/docs",
            "health": f"{settings.api_prefix}/health",
        }

    return app


app = create_app()
