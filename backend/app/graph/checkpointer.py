from __future__ import annotations

import sqlite3
from typing import Any, Callable

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def create_checkpointer(settings: Settings | None = None) -> tuple[Any, Callable[[], None]]:
    """按配置创建 checkpointer，返回 (checkpointer, 关闭函数)。

    - memory：单元测试用，进程内保存
    - sqlite：本地开发默认，重启后可恢复未完成审批
    - postgres：生产用，PostgresSaver 建表并持久化
    """
    settings = settings or get_settings()
    backend = settings.checkpointer_backend.lower()

    if backend == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        logger.info("Checkpointer: memory（仅进程内有效）")
        return MemorySaver(), lambda: None

    if backend == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver

        settings.sqlite_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(settings.sqlite_checkpoint_path),
            check_same_thread=False,
        )
        logger.info("Checkpointer: sqlite (%s)", settings.sqlite_checkpoint_path)
        return SqliteSaver(connection), connection.close

    if backend == "postgres":
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        if not settings.postgres_url:
            raise ValueError("CHECKPOINTER_BACKEND=postgres 时必须提供 POSTGRES_URL")

        pool = ConnectionPool(
            conninfo=settings.postgres_url,
            min_size=1,
            max_size=5,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            open=True,
        )
        pool.wait()
        saver = PostgresSaver(pool)
        saver.setup()
        logger.info("Checkpointer: postgres")
        return saver, pool.close

    raise ValueError(f"不支持的 CHECKPOINTER_BACKEND: {backend}")
