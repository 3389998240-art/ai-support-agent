from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# 必须在导入 app 之前把配置指向临时目录，避免污染开发数据
_TMP = Path(tempfile.mkdtemp(prefix="support-test-"))
os.environ.update(
    {
        "LLM_PROVIDER": "mock",
        "LLM_API_KEY": "",
        "EMBEDDING_PROVIDER": "hashing",
        "CHECKPOINTER_BACKEND": "memory",
        "SQLITE_PATH": str(_TMP / "support.db"),
        "SQLITE_CHECKPOINT_PATH": str(_TMP / "checkpoints.db"),
        "INDEX_DIR": str(_TMP / "index"),
        "KNOWLEDGE_DIR": str(BACKEND_DIR / "knowledge_base"),
        "DEMO_PASSWORD": "test-pass-123",
        "JWT_SECRET": "test-secret-for-pytest-only-0123456789",
        "AGENT_APPROVAL_LIMIT": "5000",
        # 测试里会反复登录，限流阈值调高；限流本身用专门的用例验证
        "LOGIN_RATE_LIMIT": "1000",
        "LOGIN_RATE_WINDOW_SECONDS": "300",
        "LOG_LEVEL": "WARNING",
    }
)

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from app.deps import get_repository, get_retriever  # noqa: E402
from app.graph.builder import build_graph  # noqa: E402
from app.graph.service import SupportService  # noqa: E402


@pytest.fixture(scope="session")
def repo():
    repository = get_repository()
    repository.seed_demo_data()
    return repository


@pytest.fixture(scope="session")
def store():
    return get_retriever().store


@pytest.fixture()
def clean_db(repo):
    for table in ("refunds", "tickets", "approvals"):
        repo._execute(f"DELETE FROM {table}")  # noqa: SLF001 - 测试里直接清表
    repo.seed_demo_data()
    return repo


@pytest.fixture()
def service(clean_db) -> SupportService:
    return SupportService(build_graph(checkpointer=MemorySaver()))


def login(client, username: str = "agent01", password: str | None = None) -> dict[str, str]:
    """登录取 token，返回可直接用的 Authorization 头。"""
    from app.core.config import get_settings

    payload = {"username": username, "password": password or get_settings().demo_password}
    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def auth(client):
    """返回一个函数：auth("alice") -> Authorization 头。"""

    def _login(username: str = "agent01", password: str | None = None) -> dict[str, str]:
        return login(client, username, password)

    return _login


@pytest.fixture(autouse=True)
def _reset_runtime_state():
    """每个用例前清空限流计数与指标，避免用例之间互相污染。"""
    from app.api.routes import _login_limiter
    from app.core.metrics import METRICS

    _login_limiter.reset()
    METRICS.reset()
    yield


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
