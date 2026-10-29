from __future__ import annotations

from collections import defaultdict

import numpy as np

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.rag.bm25 import BM25
from app.rag.store import KnowledgeStore
from app.rag.types import Retrieved

logger = get_logger(__name__)


class HybridRetriever:
    """向量召回 + BM25 词法召回，分数加权融合后按文档去重。

    为什么做混合检索：
    - 纯向量在离线哈希向量下对专有名词（型号、单号、条款编号）不敏感；
    - 纯 BM25 又抓不到"退款到账要多久" ↔ "款项退回时效" 这类近义改写；
    - 两路融合后，召回率和可解释性都更好（每条命中都带两路分数，
      前端可以直接展示"向量 0.42 / 关键词 0.61 / 融合 0.50"）。
    """

    VECTOR_WEIGHT = 0.6
    KEYWORD_WEIGHT = 0.4
    TABLE_BOOST = 0.06

    # 问"多久/几天/时效"时，带表格的切片（时效对照表）更有价值
    TIMING_QUERY_HINTS = ("多久", "几天", "多长时间", "时效", "到账", "什么时候")

    def __init__(self, store: KnowledgeStore, settings: Settings | None = None) -> None:
        self.store = store
        self.settings = settings or get_settings()
        self._bm25: BM25 | None = None
        self._bm25_version = -1

    # ------------------------------------------------------------------ 索引
    def _ensure_bm25(self) -> BM25:
        if self._bm25 is None or self._bm25_version != self.store.version:
            self._bm25 = BM25(self.store.chunks)
            self._bm25_version = self.store.version
        return self._bm25

    # ------------------------------------------------------------------ 检索
    def search(
        self,
        query: str,
        *,
        k: int | None = None,
        max_per_doc: int | None = None,
    ) -> list[Retrieved]:
        if not self.store.ready:
            return []

        k = k or self.settings.retrieval_top_k
        max_per_doc = max_per_doc or self.settings.retrieval_max_per_doc

        # 向量侧失败（embedding 接口挂了 / 限流）不能让整条对话挂掉，
        # 降级成纯词法召回，答案仍然有原文依据。
        try:
            vector_hits = self.store.search(query, k=k * 3)
        except Exception as exc:  # noqa: BLE001
            logger.warning("向量检索不可用，降级为 BM25: %s", exc)
            vector_hits = []

        bm25 = self._ensure_bm25()
        keyword_scores = bm25.scores(query)
        keyword_max = float(np.max(keyword_scores)) if keyword_scores.size else 0.0
        asks_timing = any(hint in query for hint in self.TIMING_QUERY_HINTS)
        position = {chunk.id: index for index, chunk in enumerate(self.store.chunks)}

        candidates: list[Retrieved] = []

        if vector_hits:
            for chunk, vector_score in vector_hits:
                index = position.get(chunk.id)
                raw_keyword = float(keyword_scores[index]) if index is not None else 0.0
                keyword_norm = raw_keyword / keyword_max if keyword_max > 0 else 0.0
                fused = (
                    self.VECTOR_WEIGHT * max(vector_score, 0.0)
                    + self.KEYWORD_WEIGHT * keyword_norm
                )
                if asks_timing and "| ---" in chunk.text:
                    fused += self.TABLE_BOOST
                candidates.append(
                    Retrieved(
                        chunk=chunk,
                        vector_score=vector_score,
                        keyword_score=raw_keyword,
                        score=fused,
                    )
                )
        elif keyword_max > 0:
            for index in np.argsort(-keyword_scores)[: k * 3]:
                chunk = self.store.chunks[int(index)]
                raw_keyword = float(keyword_scores[int(index)])
                # 只有一路信号可用时，融合分退化为该信号的归一化值，
                # 保证阈值语义与双路融合时一致（不会因为降级就全部丢掉）。
                pseudo = raw_keyword / keyword_max
                candidates.append(
                    Retrieved(
                        chunk=chunk,
                        keyword_score=raw_keyword,
                        score=pseudo,
                        extra={"mode": "bm25_only"},
                    )
                )
        else:
            return []

        # 同一文档最多保留 max_per_doc 个切片，避免单一文档霸榜
        bucket: dict[str, int] = defaultdict(int)
        selected: list[Retrieved] = []
        for item in sorted(candidates, key=lambda r: r.score, reverse=True):
            if bucket[item.chunk.doc_id] >= max_per_doc:
                continue
            bucket[item.chunk.doc_id] += 1
            selected.append(item)
            if len(selected) >= k:
                break

        for rank, item in enumerate(selected, start=1):
            item.rank = rank
        return selected

    # ------------------------------------------------------------------ 过滤
    def relevant(
        self,
        items: list[Retrieved],
        *,
        min_score: float | None = None,
    ) -> list[Retrieved]:
        threshold = (
            min_score if min_score is not None else self.settings.retrieval_min_score
        )
        return [item for item in items if item.score >= threshold]
