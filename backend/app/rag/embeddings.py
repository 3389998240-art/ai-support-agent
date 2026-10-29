from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, Sequence

import numpy as np

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_LATIN = re.compile(r"[a-z0-9][a-z0-9._+-]*")
_CJK = re.compile(r"[\u4e00-\u9fff]+")


class Embedder(Protocol):
    dim: int
    name: str

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


class HashingEmbedder:
    """本地确定性向量化：字符 n-gram 特征哈希 + 次线性 TF + L2 归一化。

    为什么默认用它：
    - 零下载、零网络、CPU 上毫秒级，CI 和离线演示都能跑；
    - 结果可复现，pytest 断言稳定；
    - 中文按字/二元组切分，不依赖分词器。
    生产环境把 EMBEDDING_PROVIDER 换成 openai（或任何兼容网关）即可，
    对外接口完全一致。
    """

    def __init__(self, dim: int = 768) -> None:
        self.dim = dim
        self.name = f"hashing-{dim}"

    @staticmethod
    def _tokens(text: str) -> list[tuple[str, float]]:
        lowered = text.lower()
        tokens: list[tuple[str, float]] = []

        for word in _LATIN.findall(lowered):
            tokens.append((f"w:{word}", 1.0))

        for run in _CJK.findall(lowered):
            for char in run:
                tokens.append((f"c:{char}", 0.5))
            for i in range(len(run) - 1):
                tokens.append((f"b:{run[i:i + 2]}", 1.2))

        return tokens

    def _hash(self, token: str) -> int:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.dim

    def _embed_one(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dim, dtype=np.float32)
        for token, weight in self._tokens(text):
            vector[self._hash(token)] += weight

        nonzero = vector > 0
        vector[nonzero] = 1.0 + np.log(vector[nonzero])
        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector /= norm
        return vector

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack([self._embed_one(text) for text in texts]).astype(np.float32)


class OpenAIEmbedder:
    """通过 OpenAI 兼容接口做向量化（千问 DashScope / 硅基流动 / OpenAI 均可）。

    踩过的坑：
    - 千问 text-embedding-v3 单批最多 10 条，超了直接 400；
    - 部分网关返回的 data 顺序不保证，必须按 index 排序；
    - 限流会返回 429，这里做一次退避重试。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.dim = settings.embedding_dim
        self.name = f"openai-{settings.embedding_model}"
        self.batch_size = max(1, settings.embedding_batch_size)

    def _payload(self, batch: list[str]) -> dict:
        payload: dict = {
            "model": self.settings.embedding_model,
            "input": batch,
            "encoding_format": "float",
        }
        if self.settings.embedding_dim > 0:
            payload["dimensions"] = self.settings.embedding_dim
        return payload

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        import time

        import httpx

        api_key = self.settings.embedding_api_key or self.settings.llm_api_key
        if not api_key:
            raise RuntimeError("EMBEDDING_API_KEY 未配置")

        url = f"{self.settings.embedding_base_url.rstrip('/')}/embeddings"
        vectors: list[list[float] | None] = [None] * len(texts)

        with httpx.Client(timeout=90.0) as client:
            for start in range(0, len(texts), self.batch_size):
                batch = list(texts[start : start + self.batch_size])
                response = None

                for attempt in range(2):
                    response = client.post(
                        url,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=self._payload(batch),
                    )
                    if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                        logger.warning("embedding 限流/异常 %s，1.2s 后重试", response.status_code)
                        time.sleep(1.2)
                        continue
                    break

                assert response is not None
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"embedding 接口返回 {response.status_code}: {response.text[:200]}"
                    )

                items = sorted(response.json()["data"], key=lambda item: item.get("index", 0))
                for offset, item in enumerate(items):
                    vectors[start + offset] = item["embedding"]

        matrix = np.asarray([vector for vector in vectors if vector is not None], dtype=np.float32)
        if matrix.size == 0:
            return np.zeros((0, self.dim), dtype=np.float32)

        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix = matrix / norms
        self.dim = matrix.shape[1] if matrix.size else self.dim
        return matrix


def build_embedder(settings: Settings | None = None) -> Embedder:
    settings = settings or get_settings()
    provider = settings.embedding_provider.lower()

    if provider == "hashing":
        return HashingEmbedder(settings.embedding_dim)

    if provider == "openai":
        return OpenAIEmbedder(settings)

    logger.warning("未知的 EMBEDDING_PROVIDER=%s，回退到 hashing", provider)
    return HashingEmbedder(settings.embedding_dim)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0 or math.isnan(denom):
        return 0.0
    return float(np.dot(a, b) / denom)
