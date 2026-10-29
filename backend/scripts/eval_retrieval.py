"""检索效果评估：recall@k / MRR / 误召率。

用法：
    cd backend && PYTHONPATH=. python scripts/eval_retrieval.py
    cd backend && PYTHONPATH=. python scripts/eval_retrieval.py --k 5 --json eval/report.json

数据集：eval/retrieval_qa.jsonl，每行
    {"query": "...", "doc": "01-退换货政策", "section": "七天无理由退货", "label": "answerable"}

指标口径：
- recall@k      ：answerable 里，期望文档出现在前 k 条的比例
- section@1    ：Top1 的章节名包含期望章节的比例（更严格，看"引对段落"）
- MRR           ：第一个命中期望文档的排名倒数均值
- 误召率        ：unanswerable 里，过滤阈值后仍有结果的比例（越低越好）

为什么要有这份评估：阈值和权重不能靠"我觉得还行"，要有一组可回归的数字。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.agents.knowledge import _is_on_topic
from app.core.config import get_settings
from app.deps import get_retriever
from app.graph.classifier import classify_text

DATASET = Path(__file__).resolve().parents[1] / "eval" / "retrieval_qa.jsonl"


def load_cases() -> list[dict]:
    cases = []
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--json", dest="json_path", default="")
    args = parser.parse_args()

    settings = get_settings()
    retriever = get_retriever()
    store = retriever.store
    cases = load_cases()

    answerable = [case for case in cases if case["label"] == "answerable"]
    unanswerable = [case for case in cases if case["label"] == "unanswerable"]

    hits_at = {k: 0 for k in (1, 3, args.k)}
    section_at_1 = 0
    reciprocal_ranks: list[float] = []
    misses: list[dict] = []

    for case in answerable:
        items = retriever.search(case["query"], k=args.k)
        docs = [item.chunk.doc_id for item in items]
        if case["doc"] in docs:
            rank = docs.index(case["doc"]) + 1
            reciprocal_ranks.append(1 / rank)
            for k in hits_at:
                if rank <= k:
                    hits_at[k] += 1
            top = items[rank - 1]
            if case["section"] and case["section"] in top.chunk.section:
                section_at_1 += 1
            if rank > 1:
                misses.append(
                    {
                        "query": case["query"],
                        "expected": case["doc"],
                        "rank": rank,
                        "top1": items[0].chunk.doc_id,
                    }
                )
        else:
            reciprocal_ranks.append(0.0)
            misses.append(
                {
                    "query": case["query"],
                    "expected": case["doc"],
                    "rank": None,
                    "top1": docs[0] if docs else None,
                }
            )

    false_positives = []
    gate_blocked = 0
    for case in unanswerable:
        # 端到端还有两层闸门在检索之前：意图分类 + 主题判定
        intent, _, _ = classify_text(case["query"])
        if intent == "unknown" or not _is_on_topic(case["query"]):
            gate_blocked += 1

        items = retriever.search(case["query"], k=args.k)
        kept = retriever.relevant(items)
        if kept:
            false_positives.append(
                {
                    "query": case["query"],
                    "score": round(kept[0].score, 3),
                    "top1": kept[0].chunk.doc_id,
                }
            )

    n = len(answerable)
    m = len(unanswerable)
    report = {
        "index": store.meta,
        "settings": {
            "top_k": settings.retrieval_top_k,
            "min_score": settings.retrieval_min_score,
            "max_per_doc": settings.retrieval_max_per_doc,
        },
        "counts": {"answerable": n, "unanswerable": m},
        "recall@1": round(hits_at[1] / n, 3),
        "recall@3": round(hits_at[3] / n, 3),
        f"recall@{args.k}": round(hits_at[args.k] / n, 3),
        "section@1": round(section_at_1 / n, 3),
        "mrr": round(sum(reciprocal_ranks) / n, 3),
        "false_positive_rate": round(len(false_positives) / m, 3) if m else 0.0,
        "gate_block_rate": round(gate_blocked / m, 3) if m else 0.0,
        "misses": misses,
        "false_positives": false_positives,
    }

    print("=" * 72)
    print(f"索引：{store.meta['chunks']} 切片 / {store.meta['documents']} 文档 · {store.meta['embedder']}")
    print(f"参数：top_k={settings.retrieval_top_k} · min_score={settings.retrieval_min_score} · max_per_doc={settings.retrieval_max_per_doc}")
    print("-" * 72)
    print(f"样本：可回答 {n} 条 / 不可回答 {m} 条")
    print(f"recall@1 = {report['recall@1']:.3f}    recall@3 = {report['recall@3']:.3f}    recall@{args.k} = {report[f'recall@{args.k}']:.3f}")
    print(f"section@1 = {report['section@1']:.3f}   MRR = {report['mrr']:.3f}")
    print(f"误召率 = {report['false_positive_rate']:.3f}（{len(false_positives)}/{m}）—— 只看检索")
    print(f"前置闸门拦截率 = {report['gate_block_rate']:.3f}（{gate_blocked}/{m}）—— 意图分类 + 主题判定")
    print("-" * 72)

    if misses:
        print("未排在第一位的样本：")
        for miss in misses:
            print(f"  · {miss['query']:<18} 期望={miss['expected']:<22} 实际排名={miss['rank']} top1={miss['top1']}")
    if false_positives:
        print("被误召的无关问题：")
        for item in false_positives:
            print(f"  · {item['query']:<18} score={item['score']} top1={item['top1']}")
    print("=" * 72)

    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入 {target}")


if __name__ == "__main__":
    main()
