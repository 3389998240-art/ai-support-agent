from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """全局配置：全部来自环境变量，禁止在代码里硬编码任何密钥。"""

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 应用 ────────────────────────────────────────────────
    app_name: str = "AfterSales Support Agent"
    environment: str = "dev"
    api_prefix: str = "/api"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ── 大模型 ──────────────────────────────────────────────
    # mock = 不调用任何外部模型，走确定性降级链路（默认，保证离线可跑）
    llm_provider: str = "mock"  # mock | deepseek | openai | ollama
    llm_model: str = "deepseek-chat"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_temperature: float = 0.0
    llm_timeout: float = 40.0
    llm_max_tokens: int = 900

    # ── 向量化 ──────────────────────────────────────────────
    # hashing = 本地确定性字符 n-gram 哈希向量，零下载、可离线、可复现
    embedding_provider: str = "hashing"  # hashing | openai
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 768
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    # 千问 text-embedding-v3 单次最多 10 条，超过会 400
    embedding_batch_size: int = 10

    # ── 检索 ────────────────────────────────────────────────
    retrieval_top_k: int = 6
    retrieval_min_score: float = 0.24
    retrieval_max_per_doc: int = 3
    chunk_size: int = 420
    chunk_overlap: int = 80

    # ── 业务规则（确定性，不交给模型判断） ──────────────────
    refund_auto_limit: float = 300.0
    refund_window_days: int = 7
    quality_refund_window_days: int = 15

    # ── 存储 ────────────────────────────────────────────────
    data_dir: Path = BACKEND_DIR / "data"
    knowledge_dir: Path = BACKEND_DIR / "knowledge_base"
    sqlite_path: Path = BACKEND_DIR / "data" / "support.db"
    index_dir: Path = BACKEND_DIR / "data" / "index"
    checkpointer_backend: str = "sqlite"  # sqlite | memory | postgres
    postgres_url: str = ""
    sqlite_checkpoint_path: Path = BACKEND_DIR / "data" / "checkpoints.db"

    # ── 可观测性 ────────────────────────────────────────────
    log_level: str = "INFO"

    # ── 认证与授权（JWT + RBAC）──────────────────────────────
    # 生产必须用环境变量注入一个 32 字节以上的随机串
    jwt_secret: str = "dev-only-secret-change-me-please-32bytes"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "after-sales-agent"
    jwt_expire_minutes: int = 480
    # 演示账号的初始密码（首次初始化用户表时使用）
    demo_password: str = "demo1234"
    # 审核员单笔可批上限；超过需要 admin（ABAC：角色 + 金额双条件）
    agent_approval_limit: float = 5000.0
    # 登录限流：同一 IP + 账号在窗口内的最大尝试次数
    login_rate_limit: int = 10
    login_rate_window_seconds: int = 300

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider != "mock" and bool(self.llm_api_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.index_dir, self.knowledge_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
