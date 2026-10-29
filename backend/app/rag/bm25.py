from __future__ import annotations

import math
import re
from collections import Counter
from typing import Sequence

import numpy as np

from app.rag.types import Chunk

_LATIN = re.compile(r"[a-z0-9][a-z0-9._+-]*")
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_STOPWORDS = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一",
    "吗", "呢", "吧", "啊", "怎么", "什么", "可以", "你们", "请问",
}


def tokenize(text: str) -> list[str]:
    """中英混排的轻量分词：英文按词，中文按二元组（含单字兜底）。"""
    lowered = text.lower()
    tokens: list[str] = [word for word in _LATIN.findall(lowered)]

    for run in _CJK.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
            continue
        for i in range(len(run) - 1):
            bigram = run[i : i + 2]
            if bigram not in _STOPWORDS:
                tokens.append(bigram)

    return tokens


class BM25:
    """内存版 BM25（k1/b 可调），与向量检索做分数融合。"""

    def __init__(
        self,
        chunks: Sequence[Chunk],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.chunks = list(chunks)
        self.k1 = k1
        self.b = b

        self._term_freqs: list[Counter[str]] = []
        self._doc_lengths: list[int] = []
        doc_freq: Counter[str] = Counter()

        for chunk in self.chunks:
            tokens = tokenize(f"{chunk.title} {chunk.section} {chunk.text}")
            freqs = Counter(tokens)
            self._term_freqs.append(freqs)
            self._doc_lengths.append(len(tokens))
            doc_freq.update(set(tokens))

        self._n_docs = len(self.chunks)
        self._avg_length = (
            sum(self._doc_lengths) / self._n_docs if self._n_docs else 0.0
        )
        self._idf = {
            term: math.log(1 + (self._n_docs - freq + 0.5) / (freq + 0.5))
            for term, freq in doc_freq.items()
        }

    def scores(self, query: str) -> np.ndarray:
        if not self._n_docs:
            return np.zeros(0, dtype=np.float32)

        query_terms = tokenize(query)
        result = np.zeros(self._n_docs, dtype=np.float32)

        for index, freqs in enumerate(self._term_freqs):
            length = self._doc_lengths[index] or 1
            score = 0.0
            for term in query_terms:
                tf = freqs.get(term)
                if not tf:
                    continue
                idf = self._idf.get(term, 0.0)
                denominator = tf + self.k1 * (
                    1 - self.b + self.b * length / (self._avg_length or 1)
                )
                score += idf * (tf * (self.k1 + 1)) / denominator
            result[index] = score

        return result
