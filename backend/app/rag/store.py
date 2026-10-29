from __future__ import annotations

import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np

from app.core.logging import get_logger
from app.rag.embeddings import Embedder
from app.rag.types import Chunk

logger = get_logger(__name__)

try:  # faiss 是可选加速项，缺失时退化为 numpy 精确检索
    import faiss  # type: ignore

    _HAS_FAISS = True
except Exception:  # noqa: BLE001
    faiss = None  # type: ignore
    _HAS_FAISS = False


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """先写同目录临时文件再 os.replace：避免进程崩溃时留下半截索引。"""
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


class KnowledgeStore:
    """FAISS 向量索引 + 切片元数据的本地持久化。

    落盘三个文件，故意不使用 pickle：
      vectors.npy  —— 归一化后的向量矩阵
      chunks.json  —— 切片原文与来源元数据
      meta.json    —— 向量化模型签名 / 维度 / 构建时间
    这样加载索引不会触发任意反序列化（FAISS 常见的
    `allow_dangerous_deserialization=True` 安全隐患）。
    """

    def __init__(self, index_dir: Path, embedder: Embedder) -> None:
        self.index_dir = Path(index_dir)
        self.embedder = embedder
        self._chunks: list[Chunk] = []
        self._matrix: np.ndarray | None = None
        self._faiss_index = None
        self.version = 0

    # ------------------------------------------------------------------ 属性
    @property
    def ready(self) -> bool:
        return bool(self._chunks) and self._matrix is not None

    @property
    def chunks(self) -> list[Chunk]:
        return self._chunks

    @property
    def count(self) -> int:
        return len(self._chunks)

    @property
    def backend(self) -> str:
        return "faiss" if _HAS_FAISS else "numpy"

    @property
    def meta(self) -> dict:
        documents = {chunk.doc_id for chunk in self._chunks}
        return {
            "chunks": self.count,
            "documents": len(documents),
            "dim": int(self._matrix.shape[1]) if self._matrix is not None else 0,
            "embedder": self.embedder.name,
            "backend": self.backend,
            "built_at": self._read_meta().get("built_at"),
        }

    # ------------------------------------------------------------------ 构建
    def build(self, chunks: Sequence[Chunk]) -> None:
        self._chunks = list(chunks)
        self._matrix = self.embedder.embed([self._text_for_embed(c) for c in self._chunks])
        self._invalidate()
        self._save()
        logger.info("知识库索引构建完成: %s 个切片 (%s)", self.count, self.backend)

    def add(self, chunks: Sequence[Chunk]) -> int:
        if not chunks:
            return 0

        existing = {chunk.id for chunk in self._chunks}
        fresh = [chunk for chunk in chunks if chunk.id not in existing]
        if not fresh:
            return 0

        new_matrix = self.embedder.embed([self._text_for_embed(c) for c in fresh])
        self._chunks.extend(fresh)
        self._matrix = (
            new_matrix
            if self._matrix is None
            else np.vstack([self._matrix, new_matrix]).astype(np.float32)
        )
        self._invalidate()
        self._save()
        return len(fresh)

    @staticmethod
    def _text_for_embed(chunk: Chunk) -> str:
        # 把标题与章节拼进正文，提升短问题召回
        return f"{chunk.title} {chunk.section} {chunk.text}"

    # ------------------------------------------------------------------ 检索
    def _ensure_faiss(self) -> None:
        if not _HAS_FAISS or self._matrix is None:
            return
        if self._faiss_index is not None:
            return
        index = faiss.IndexFlatIP(self._matrix.shape[1])
        index.add(np.ascontiguousarray(self._matrix))
        self._faiss_index = index

    def _invalidate(self) -> None:
        self._faiss_index = None
        self.version += 1

    def search(self, query: str, k: int = 5) -> list[tuple[Chunk, float]]:
        if not self.ready or self._matrix is None:
            return []

        query_vector = self.embedder.embed([query])
        top_k = min(k, self.count)

        if _HAS_FAISS:
            self._ensure_faiss()
            scores, indices = self._faiss_index.search(query_vector, top_k)  # type: ignore[union-attr]
            pairs = list(zip(indices[0].tolist(), scores[0].tolist()))
        else:
            raw = self._matrix @ query_vector[0]
            order = np.argsort(-raw)[:top_k]
            pairs = [(int(i), float(raw[i])) for i in order]

        results: list[tuple[Chunk, float]] = []
        for index, score in pairs:
            if index < 0 or index >= self.count:
                continue
            results.append((self._chunks[index], float(score)))
        return results

    # ------------------------------------------------------------------ 持久化
    def _paths(self) -> tuple[Path, Path, Path]:
        return (
            self.index_dir / "vectors.npy",
            self.index_dir / "chunks.json",
            self.index_dir / "meta.json",
        )

    def _read_meta(self) -> dict:
        _, _, meta_path = self._paths()
        if not meta_path.exists():
            return {}
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save(self) -> None:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        vectors_path, chunks_path, meta_path = self._paths()

        if self._matrix is not None:
            buffer = io.BytesIO()
            np.save(buffer, self._matrix)
            _atomic_write_bytes(vectors_path, buffer.getvalue())

        _atomic_write_bytes(
            chunks_path,
            json.dumps(
                [chunk.to_dict() for chunk in self._chunks], ensure_ascii=False, indent=2
            ).encode("utf-8"),
        )
        _atomic_write_bytes(
            meta_path,
            json.dumps(
                {
                    "embedder": self.embedder.name,
                    "dim": int(self._matrix.shape[1]) if self._matrix is not None else 0,
                    "count": self.count,
                    "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )

    def load(self) -> bool:
        """加载已有索引；模型签名不一致或文件缺失时返回 False，由上层重建。"""
        vectors_path, chunks_path, meta_path = self._paths()
        if not (vectors_path.exists() and chunks_path.exists()):
            return False

        meta = self._read_meta()
        if meta.get("embedder") and meta["embedder"] != self.embedder.name:
            logger.warning(
                "索引向量化模型不匹配 (索引=%s, 当前=%s)，将重建索引",
                meta["embedder"],
                self.embedder.name,
            )
            return False

        try:
            matrix = np.load(vectors_path).astype(np.float32)
            chunks = [
                Chunk.from_dict(item)
                for item in json.loads(chunks_path.read_text(encoding="utf-8"))
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("索引加载失败，将重建: %s", exc)
            return False

        if matrix.ndim != 2 or matrix.shape[0] != len(chunks):
            logger.warning("索引文件不一致 (vectors=%s, chunks=%s)，将重建", matrix.shape, len(chunks))
            return False

        self._chunks = chunks
        self._matrix = matrix
        self._invalidate()
        return True
