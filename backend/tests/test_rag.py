from __future__ import annotations

import numpy as np
import pytest

from app.core.config import get_settings
from app.deps import get_retriever
from app.rag.bm25 import tokenize
from app.rag.embeddings import HashingEmbedder


def test_embedder_is_deterministic() -> None:
    embedder = HashingEmbedder(256)
    first = embedder.embed(["退款多久到账"])
    second = embedder.embed(["退款多久到账"])
    assert np.allclose(first, second)
    assert first.shape == (1, 256)
    assert pytest.approx(float(np.linalg.norm(first[0])), rel=1e-5) == 1.0


def test_embedder_related_text_scores_higher() -> None:
    embedder = HashingEmbedder(256)
    base, related, unrelated = embedder.embed(
        ["退款到账时效是多久", "退款多久能到账", "扫地机器人怎么连网"]
    )
    sim_related = float(np.dot(base, related))
    sim_unrelated = float(np.dot(base, unrelated))
    assert sim_related > sim_unrelated


def test_tokenizer_handles_chinese_bigrams() -> None:
    tokens = tokenize("退款时效 Refund 3天")
    assert "退款" in tokens
    assert "refund" in tokens


def test_index_is_built(store) -> None:
    assert store.ready
    assert store.count > 10
    assert store.meta["documents"] == 6
    assert store.backend in {"faiss", "numpy"}


def test_retrieval_hits_refund_timing_doc() -> None:
    retriever = get_retriever()
    items = retriever.search("退款一般多久到账")
    assert items
    assert items[0].chunk.doc_id.startswith("02")
    assert items[0].score > get_settings().retrieval_min_score


def test_retrieval_prefers_table_chunk_for_timing_question() -> None:
    retriever = get_retriever()
    items = retriever.search("退款多久到账")
    table_hits = [item for item in items if "| ---" in item.chunk.text]
    assert table_hits, "时效类问题应召回时效对照表切片"
    assert any(item.chunk.doc_id.startswith("02") for item in table_hits)


def test_unrelated_query_below_threshold() -> None:
    retriever = get_retriever()
    items = retriever.search("帮我写一首关于春天的诗")
    kept = retriever.relevant(items)
    assert kept == []


def test_citations_contain_source_metadata() -> None:
    retriever = get_retriever()
    items = retriever.search("发票怎么开")
    citation = items[0].to_citation()
    for key in ("id", "title", "section", "source", "snippet", "score", "rank"):
        assert key in citation
    assert citation["source"].endswith(".md")
