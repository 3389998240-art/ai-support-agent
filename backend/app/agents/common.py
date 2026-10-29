from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

from app.rag.bm25 import tokenize
from app.rag.types import Retrieved

CITATION_MARK = re.compile(r"\[(\d{1,2})\]")
SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")


def now_label() -> str:
    return datetime.now().strftime("%H:%M:%S")


def event(
    stage: str,
    *,
    status: str = "ok",
    detail: str = "",
    **extra: Any,
) -> dict[str, Any]:
    """构造一条可回溯的执行事件，前端时间线直接渲染。"""
    payload: dict[str, Any] = {
        "stage": stage,
        "status": status,
        "detail": detail,
        "ts": now_label(),
    }
    payload.update(extra)
    return payload


def trace(stage: str, *, status: str = "ok", detail: str = "", **extra: Any) -> dict:
    return {"trace": [event(stage, status=status, detail=detail, **extra)]}


def to_citations(items: list[Retrieved], limit: int = 5) -> list[dict]:
    return [item.to_citation() for item in items[:limit]]


def sources_block(citations: list[dict]) -> str:
    if not citations:
        return ""
    lines = ["资料来源："]
    for index, item in enumerate(citations, start=1):
        heading = f"{item['title']} · {item['section']}" if item.get("section") else item["title"]
        lines.append(f"[{index}] 《{heading}》（{item['source']}，相关度 {item['score']:.2f}）")
    return "\n".join(lines)


def _overlap_score(question_tokens: set[str], text: str) -> float:
    tokens = set(tokenize(text))
    if not tokens:
        return 0.0
    return len(question_tokens & tokens) / len(question_tokens)


def _table_rows(text: str) -> list[str]:
    """抽取 Markdown 表格行（含表头），售后政策里的时效表是最常被问的内容。"""
    rows: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = stripped.strip("|").split("|")
        if all(set(cell.strip()) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append(stripped)
    return rows


def _format_table_rows(rows: list[str]) -> str:
    """把 Markdown 表格行排成用户能读的短句，避免聊天框里出现大量竖线。"""
    lines: list[str] = []
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        cells = [cell for cell in cells if cell]
        if len(cells) == 2:
            lines.append(f"· {cells[0]}：{cells[1]}")
        elif cells:
            lines.append("· " + " / ".join(cells))
    return "\n".join(lines)


def extractive_answer(question: str, citations: list[dict]) -> str:
    """无 LLM 时的降级策略：从命中的切片里抽句子，并保留引用编号。

    这不是"假装模型回答"——它明确是基于检索片段的原文摘录，
    保证在断网/欠费/限流时依然给用户可核对的信息。
    """
    if not citations:
        return ""

    question_tokens = set(tokenize(question))
    top = citations[0]
    heading = f"{top['title']} · {top['section']}" if top.get("section") else top["title"]

    # 命中切片里有时效对照表：直接给表，比抽句子更准确。
    # 表格常被单独切成一块，因此在前三段里找。
    for index, item in enumerate(citations[:3], start=1):
        rows = _table_rows(item["snippet"])
        if len(rows) >= 3:
            item_heading = (
                f"{item['title']} · {item['section']}" if item.get("section") else item["title"]
            )
            return (
                f"根据《{item_heading}》：\n\n"
                + _format_table_rows(rows[:6])
                + f"\n\n以上为平台承诺时效，实际到账以支付渠道处理为准 [{index}]。"
            )

    picked: list[tuple[float, str, int]] = []

    for index, item in enumerate(citations[:3], start=1):
        for sentence in SENTENCE_SPLIT.split(item["snippet"]):
            sentence = sentence.strip(" \n-")
            if len(sentence) < 8 or sentence.startswith("【"):
                continue
            score = _overlap_score(question_tokens, sentence)
            if score > 0:
                picked.append((score, sentence, index))

    if not picked:
        return f"根据《{heading}》中的说明：\n\n{top['snippet'][:220]}… [1]"

    picked.sort(key=lambda item: item[0], reverse=True)
    seen: set[str] = set()
    lines: list[str] = []
    for _, sentence, index in picked:
        if sentence in seen:
            continue
        seen.add(sentence)
        lines.append(f"- {sentence} [{index}]")
        if len(lines) >= 3:
            break

    return "根据售后政策，整理如下：\n\n" + "\n".join(lines)


def verify_citations(answer: str, citations: list[dict]) -> dict[str, Any]:
    """答案校验：引用编号必须真实存在，且至少引用一条依据。"""
    marks = [int(m) for m in CITATION_MARK.findall(answer)]
    valid = [m for m in marks if 1 <= m <= len(citations)]
    invalid = [m for m in marks if m not in valid]

    has_refusal = any(
        phrase in answer
        for phrase in ("无法回答", "暂不清楚", "不知道", "抱歉，我没有", "无法确定")
    )

    coverage = len(set(valid)) / len(citations) if citations else 0.0
    score = 0.0
    if valid:
        score += 0.5
    if not invalid:
        score += 0.2
    if coverage >= 0.2:
        score += 0.2
    if not has_refusal:
        score += 0.1

    return {
        "has_citation": bool(valid),
        "invalid_marks": invalid,
        "coverage": round(coverage, 2),
        "refusal": has_refusal,
        "score": round(score, 2),
    }


def has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def is_foreign_order(order, state) -> bool:
    """订单不属于当前操作者。

    客服/管理员可以代客查询（有 orders:read 权限）；客户只能碰自己的订单。
    越权时调用方应按"查不到"处理，不要暴露"这单存在但不是你的"。
    """
    if order is None:
        return False
    if (state.get("actor_role") or "customer") in {"agent", "admin"}:
        return False
    return order.customer_id != (state.get("customer_id") or "")


class Stopwatch:
    def __init__(self) -> None:
        self._start = time.perf_counter()

    def ms(self) -> int:
        return int((time.perf_counter() - self._start) * 1000)
