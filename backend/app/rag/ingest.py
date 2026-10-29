from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.rag.chunking import chunk_file
from app.rag.embeddings import build_embedder
from app.rag.store import KnowledgeStore

logger = get_logger(__name__)

SUPPORTED_SUFFIXES = {".md", ".txt"}


@dataclass
class IngestReport:
    documents: int
    chunks: int
    added: int
    embedder: str
    backend: str
    index_dir: str
    elapsed_ms: int

    def to_dict(self) -> dict:
        return asdict(self)


def collect_chunks(settings: Settings) -> list:
    paths = sorted(
        path
        for path in settings.knowledge_dir.rglob("*")
        if path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    chunks = []
    for path in paths:
        chunks.extend(
            chunk_file(
                path,
                chunk_size=settings.chunk_size,
                overlap=settings.chunk_overlap,
            )
        )
    return chunks


def ingest_knowledge_base(
    settings: Settings | None = None,
    store: KnowledgeStore | None = None,
) -> IngestReport:
    """全量重建知识库索引（幂等，可重复执行）。"""
    settings = settings or get_settings()
    started = time.perf_counter()

    store = store or KnowledgeStore(settings.index_dir, build_embedder(settings))
    chunks = collect_chunks(settings)
    if not chunks:
        raise FileNotFoundError(f"知识库目录为空: {settings.knowledge_dir}")

    store.build(chunks)
    documents = len({chunk.doc_id for chunk in chunks})

    return IngestReport(
        documents=documents,
        chunks=len(chunks),
        added=len(chunks),
        embedder=store.embedder.name,
        backend=store.backend,
        index_dir=str(settings.index_dir),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def ensure_store(settings: Settings | None = None) -> KnowledgeStore:
    """优先加载已有索引；缺失或模型变更时自动重建，保证服务能冷启动。"""
    settings = settings or get_settings()
    store = KnowledgeStore(settings.index_dir, build_embedder(settings))

    if store.load():
        logger.info("知识库索引已加载: %s 个切片 (%s)", store.count, store.backend)
        return store

    logger.info("未找到可用索引，正在从 %s 重建…", settings.knowledge_dir)
    ingest_knowledge_base(settings, store)
    return store


def index_summary(settings: Settings, store: KnowledgeStore) -> dict:
    meta = store.meta
    docs: dict[str, dict] = {}
    for chunk in store.chunks:
        entry = docs.setdefault(
            chunk.doc_id,
            {"doc_id": chunk.doc_id, "title": chunk.title, "source": chunk.source, "chunks": 0},
        )
        entry["chunks"] += 1

    return {
        "index": meta,
        "knowledge_dir": str(Path(settings.knowledge_dir)),
        "documents": sorted(docs.values(), key=lambda item: item["doc_id"]),
        "retrieval": {
            "top_k": settings.retrieval_top_k,
            "min_score": settings.retrieval_min_score,
            "hybrid": "vector(faiss) + bm25",
        },
    }
