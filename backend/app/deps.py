from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.domain.repository import SupportRepository
from app.llm.client import LLMClient
from app.rag.ingest import ensure_store
from app.rag.retriever import HybridRetriever

# 进程级单例：索引、BM25、DB 连接都只建一次
@lru_cache
def get_repository() -> SupportRepository:
    return SupportRepository(get_settings().sqlite_path)


@lru_cache
def get_retriever() -> HybridRetriever:
    store = ensure_store(get_settings())
    return HybridRetriever(store, get_settings())


@lru_cache
def get_llm() -> LLMClient:
    return LLMClient(get_settings())
