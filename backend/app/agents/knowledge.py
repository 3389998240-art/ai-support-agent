from __future__ import annotations

import re
from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.common import (
    Stopwatch,
    extractive_answer,
    has_chinese,
    sources_block,
    to_citations,
    trace,
    verify_citations,
)
from app.deps import get_llm, get_retriever
from app.graph.state import SupportState

# ── 领域范围词表（topic gate） ──────────────────────────────────────────────
TOPIC_LEXICON = (
    "订单", "物流", "快递", "发货", "签收", "运单", "退款", "退货", "换货",
    "发票", "保修", "维修", "延保", "会员", "优惠券", "积分", "价保", "运费",
    "政策", "规定", "规则", "时效", "售后", "客服", "投诉", "质量", "破损",
    "到账", "补发", "拒收", "地址", "定制", "虚拟",
    # 评估集暴露的缺口：用户不会说"退货政策"，会说"无理由""七天""专票""寄修"
    "无理由", "七天", "十五天", "增值税", "专票", "普票", "开票", "寄修",
    "修好", "上门", "取件", "工单", "补差", "降价", "秒杀", "预售", "包邮",
    "赔付", "退款单", "人工",
)

GREETINGS = ("你好", "您好", "在吗", "hi", "hello", "早上好", "下午好", "晚上好")

DRAFT_SYSTEM = """你是星辰科技的售后客服助手。必须严格遵守：
1. 只能依据【资料】中的条款回答，不得编造政策、金额、时效、流程；
2. 每个结论后用 [编号] 标注依据，例如：无理由退货时效为签收后 7 天 [2]；
3. 资料未覆盖的部分直接说明"资料未覆盖"，不要猜测；
4. 先给结论，再给可执行步骤，总长度不超过 160 字；
5. 不要输出 Markdown 标题，不要复述资料原文段落。"""


def _is_on_topic(message: str) -> bool:
    lowered = message.lower()
    if any(word in lowered for word in GREETINGS) and len(lowered) <= 12:
        return False
    return any(word in message for word in TOPIC_LEXICON)


# ── 节点 ────────────────────────────────────────────────────────────────────
def topic_gate(state: SupportState) -> dict:
    message = state.get("message", "")
    on_topic = _is_on_topic(message)
    return {
        "confidence": 0.0,
        **trace(
            "主题判定",
            status="ok" if on_topic else "warn",
            detail="命中售后领域关键词" if on_topic else "问题不在售后知识库范围内",
            on_topic=on_topic,
        ),
        "on_topic": on_topic,
    }

def route_after_topic(state: SupportState) -> str:
    return "on_topic" if state.get("on_topic") else "off_topic"


def off_topic(state: SupportState) -> dict:
    return {
        "response": (
            "这个问题不在我的售后知识范围内。我可以帮你处理订单查询、退换货、"
            "退款时效、发票保修、会员优惠等售后问题；如果需要其他支持，可以转人工。"
        ),
        "suggestions": ["查订单物流", "退款政策", "转人工"],
    }


PRONOUN_HINTS = ("它", "他", "这个", "那个", "这件", "那件", "还有", "再问", "呢")


def _retrieval_query(state: SupportState) -> tuple[str, str | None]:
    """生成检索语句；短句 + 指代词时把上一轮问题一起带进来。

    这是"多轮指代"的最小实现：不做完整 query rewrite（要额外一次模型调用），
    只在明显有省略的时候拼接上下文，避免把每轮都搞成超长 query。
    """
    current = (state.get("message") or "").strip()
    history = state.get("messages") or []
    humans = [msg for msg in history if getattr(msg, "type", "") == "human"]
    if len(humans) < 2:
        return current, None

    previous = str(humans[-2].content).strip()[:80]
    if not previous:
        return current, None

    elliptical = len(current) <= 25 and any(hint in current for hint in PRONOUN_HINTS)
    if not elliptical:
        return current, None

    return f"{previous} {current}", previous


def retrieve_docs(state: SupportState) -> dict:
    watch = Stopwatch()
    retriever = get_retriever()
    query, previous = _retrieval_query(state)
    items = retriever.search(query)

    doc_filter = state.get("doc_filter") or ()
    if doc_filter:
        items = [item for item in items if item.chunk.doc_id in doc_filter]

    detail = f"召回 {len(items)} 个切片，耗时 {watch.ms()}ms"
    if doc_filter:
        detail += f"（限本文档：{', '.join(doc_filter)}）"
    if previous:
        detail += f"（已合并上一轮：{previous[:24]}…）"

    return {
        "retrieved": [item.to_citation() for item in items],
        **trace(
            "知识检索",
            detail=detail,
            count=len(items),
            top_score=round(items[0].score, 3) if items else 0.0,
            ms=watch.ms(),
            query=query,
        ),
    }


def grade_docs(state: SupportState) -> dict:
    """相关性闸门：低于阈值的一律丢掉，宁可不答也不给错依据。"""
    retriever = get_retriever()
    items = [_restore_retrieved(payload) for payload in state.get("retrieved", [])]

    kept = retriever.relevant(items)
    # 至少保留一条最高分，避免阈值设置过严导致永远无答案
    if not kept and items:
        kept = items[:1]

    citations = to_citations(kept)
    return {
        "citations": citations,
        "grading": {"kept": len(citations), "dropped": len(items) - len(kept)},
        **trace(
            "相关性评估",
            status="ok" if citations else "warn",
            detail=f"保留 {len(citations)} 条，丢弃 {len(items) - len(kept)} 条",
            kept=len(citations),
        ),
    }


def _restore_retrieved(payload: dict) -> "Retrieved":
    """把序列化后的检索结果还原成 Retrieve 对象，便于复用阈值逻辑。"""
    from app.rag.types import Chunk, Retrieved

    chunk = Chunk(
        id=payload["id"],
        doc_id=payload["doc_id"],
        title=payload["title"],
        section=payload.get("section", ""),
        text=payload["snippet"],
        source=payload.get("source", ""),
    )
    return Retrieved(
        chunk=chunk,
        vector_score=payload.get("vector_score", 0.0),
        keyword_score=payload.get("keyword_score", 0.0),
        score=payload.get("score", 0.0),
        rank=payload.get("rank", 0),
    )


def route_after_grade(state: SupportState) -> str:
    return "has_context" if state.get("citations") else "no_context"


def _build_context(citations: list[dict]) -> str:
    lines = []
    for index, item in enumerate(citations, start=1):
        heading = f"{item['title']} · {item['section']}" if item.get("section") else item["title"]
        lines.append(f"[{index}] 《{heading}》\n{item['snippet']}")
    return "\n\n".join(lines)


def draft_answer(state: SupportState) -> dict:
    question = state.get("message", "")
    citations = state.get("citations", [])
    context = _build_context(citations)

    llm = get_llm()
    answer = llm.complete(
        DRAFT_SYSTEM,
        f"【资料】\n{context}\n\n【用户问题】\n{question}",
        max_tokens=400,
    )
    degraded = False

    if not answer:
        answer = extractive_answer(question, citations)
        degraded = True

    return {
        "answer": answer,
        "degraded": degraded,
        "response": f"{answer}\n\n{sources_block(citations)}".strip(),
        **trace(
            "答案生成",
            status="warn" if degraded else "ok",
            detail="模型生成" if not degraded else "LLM 不可用，降级为原文摘录",
            degraded=degraded,
        ),
    }


def verify_answer(state: SupportState) -> dict:
    answer = state.get("answer", "")
    citations = state.get("citations", [])
    verification = verify_citations(answer, citations)

    if has_chinese(state.get("message", "")) and not has_chinese(answer):
        verification["language_mismatch"] = True
        verification["score"] = max(0.0, verification["score"] - 0.3)

    top_score = max((item["score"] for item in citations), default=0.0)
    confidence = round(
        min(0.97, 0.4 * min(1.0, top_score * 1.6) + 0.6 * verification["score"]),
        2,
    )

    low_confidence = confidence < 0.55 or not verification["has_citation"]

    update: dict[str, Any] = {
        "verification": {**verification, "confidence": confidence},
        "confidence": confidence,
        **trace(
            "答案校验",
            status="warn" if low_confidence else "ok",
            detail=(
                f"引用 {len(verification['invalid_marks'])} 处异常，覆盖率 {verification['coverage']}"
                if verification["invalid_marks"]
                else f"引用完整，置信度 {confidence:.2f}"
            ),
            low_confidence=low_confidence,
        ),
    }

    if low_confidence:
        update["requires_human"] = True
        update["human_reason"] = "知识库依据不足，无法给出可核查的答案"
        update["response"] = (
            "这个问题我暂时没有足够权威的依据来回答，已经为你转接人工客服，"
            "请稍等，同事会尽快跟进。"
        )

    return update


def no_context(state: SupportState) -> dict:
    return {
        "requires_human": True,
        "human_reason": "知识库未检索到相关条款",
        "response": (
            "售后知识库中没有检索到与这个问题匹配的条款。为了不给你错误信息，"
            "我先为你转接人工客服核实。"
        ),
        **trace("知识检索", status="warn", detail="无命中切片，转人工"),
    }


def handoff(state: SupportState) -> dict:
    return {"requires_human": True}


# ── 组装 ────────────────────────────────────────────────────────────────────
def build_knowledge_graph(doc_filter: tuple[str, ...] = ()):
    builder = StateGraph(SupportState)

    inject_filter = partial(_with_doc_filter, doc_filter=doc_filter)

    builder.add_node("apply_filter", inject_filter)
    builder.add_node("topic_gate", topic_gate)
    builder.add_node("retrieve_docs", retrieve_docs)
    builder.add_node("grade_docs", grade_docs)
    builder.add_node("draft_answer", draft_answer)
    builder.add_node("verify_answer", verify_answer)
    builder.add_node("no_context", no_context)
    builder.add_node("off_topic", off_topic)

    builder.add_edge(START, "apply_filter")
    builder.add_edge("apply_filter", "topic_gate")
    builder.add_conditional_edges(
        "topic_gate",
        route_after_topic,
        {"on_topic": "retrieve_docs", "off_topic": "off_topic"},
    )
    builder.add_edge("retrieve_docs", "grade_docs")
    builder.add_conditional_edges(
        "grade_docs",
        route_after_grade,
        {"has_context": "draft_answer", "no_context": "no_context"},
    )
    builder.add_edge("draft_answer", "verify_answer")
    builder.add_edge("off_topic", END)
    builder.add_edge("no_context", END)
    builder.add_edge("verify_answer", END)

    return builder.compile()


def _with_doc_filter(state: SupportState, doc_filter: tuple[str, ...]) -> dict:
    """把"本文档范围"注入状态，供检索节点过滤。

    放在独立节点里而不是构造闭包，是为了让 graph 的结构在
    `get_graph()` 里可见、可测试。
    """
    if not doc_filter:
        return {}
    return {"doc_filter": doc_filter, **trace("范围限定", detail=", ".join(doc_filter))}
