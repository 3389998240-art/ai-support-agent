from __future__ import annotations

import re
from typing import Any

from app.agents.common import trace
from app.deps import get_llm
from app.graph.state import SupportState

# 领域词表：命中越多分越高；强特征词给高权重，避免"订单"这种泛词误判
INTENT_LEXICON: dict[str, dict[str, float]] = {
    "human": {
        "转人工": 4.0,
        "人工客服": 4.0,
        "找客服": 3.0,
        "投诉": 3.0,
        "举报": 3.5,
        "起诉": 4.0,
        "律师": 4.0,
        "曝光": 3.5,
        "12315": 4.0,
        "太差": 2.0,
        "气死": 2.5,
    },
    "refund": {
        "退款": 3.0,
        "退货": 3.0,
        "退钱": 3.0,
        "我要退": 3.5,
        "申请退款": 3.5,
        "帮我退": 3.0,
        "不要了": 2.5,
        "退了吧": 3.0,
        "退掉": 2.5,
        "换了": 2.0,
        "换货": 2.5,
        "赔偿": 2.0,
    },
    "order": {
        "订单": 2.0,
        "物流": 2.5,
        "快递": 2.5,
        "发货": 2.5,
        "到哪": 2.5,
        "签收": 2.0,
        "运单": 2.5,
        "单号": 1.5,
        "配送": 2.0,
        "催单": 2.5,
    },
    "invoice": {
        "发票": 3.0,
        "开票": 3.0,
        "专票": 3.0,
        "保修": 3.0,
        "维修": 2.5,
        "延保": 3.0,
        "维修点": 3.0,
        "换新": 2.0,
    },
    "policy": {
        "政策": 1.5,
        "规定": 1.5,
        "规则": 1.5,
        "时效": 2.0,
        "几天": 1.5,
        "多久": 1.5,
        "到账": 2.5,
        "怎么退": 2.5,
        "能退吗": 2.0,
        "退款政策": 3.0,
        "能不能": 1.0,
        "可以吗": 1.0,
        "七天": 2.5,
        "无理由": 2.5,
        "会员": 2.0,
        "优惠券": 2.5,
        "积分": 2.0,
        "价保": 2.5,
        "运费": 2.0,
    },
}

ORDER_NO_PATTERN = re.compile(r"(SO\d{8,}|RF\d{8,}|\d{12,})", re.IGNORECASE)
ORDER_NO_LABELED = re.compile(r"订单号?[:：\s]*([A-Za-z0-9\-]{6,})")

QUALITY_HINTS = ("坏", "故障", "破损", "无法开机", "不能用", "质量问题", "异响", "不亮", "划痕")

# 执行类动作词：出现即视为"要办退款"，而不是"问退款政策"
ACTION_WORDS = (
    "我要退",
    "申请退款",
    "帮我退",
    "退了吧",
    "退掉",
    "不要了",
    "我要换",
    "现在就退",
)
# 咨询类问句特征
QUESTION_WORDS = (
    "吗", "?", "？", "怎么", "多久", "几天", "多少", "谁", "如何", "能不能",
    "可以", "什么", "什么时候", "哪些", "需要什么", "多久能", "算",
)
DOMAIN_WORDS = (
    "退", "换", "运费", "时效", "政策", "发票", "保修", "会员", "优惠券",
    "价保", "物流", "到账", "发货", "无理由", "积分", "维修", "上门", "包邮",
)

# "什么情况必须转人工"是在问规则，不是在要求转人工
HUMAN_RULE_QUESTION = re.compile(r"(什么|哪些|哪种|什么时候).{0,10}(转人工|人工|升级)")


def extract_entities(message: str) -> dict[str, Any]:
    """抽取订单号等实体。确定性正则，模型不参与，避免"编造单号"。"""
    entities: dict[str, Any] = {"order_no": ""}

    match = ORDER_NO_PATTERN.search(message.upper())
    if match:
        entities["order_no"] = match.group(1).upper()
    else:
        labeled = ORDER_NO_LABELED.search(message)
        if labeled:
            entities["order_no"] = labeled.group(1).upper()

    entities["quality_claim"] = any(hint in message for hint in QUALITY_HINTS)
    entities["wants_human"] = any(
        word in message for word in ("转人工", "人工客服", "找客服", "投诉")
    )
    return entities


def is_consultation(message: str) -> bool:
    """判断是不是在"问规则"，而不是在"提申请"。"""
    return any(word in message for word in QUESTION_WORDS) and any(
        word in message for word in DOMAIN_WORDS
    )


def has_action_word(message: str) -> bool:
    return any(word in message for word in ACTION_WORDS)


def deterministic_scores(message: str) -> dict[str, float]:
    scores: dict[str, float] = {}
    for intent, lexicon in INTENT_LEXICON.items():
        score = 0.0
        for keyword, weight in lexicon.items():
            if keyword in message:
                score += weight
        scores[intent] = score
    return scores


def classify_text(message: str) -> tuple[str, float, str]:
    """返回 (意图, 置信度, 路由来源)。

    两层结构：词表打分给出候选，再用确定性规则纠正三类易混场景：
    执行类（要退款）、咨询类（问退款政策）、投诉类（要人工）。
    """
    normalized = message.strip().lower()
    entities = extract_entities(message)

    # 先排掉"问规则"的问句，否则"什么情况必须转人工"会被当成要转人工
    if HUMAN_RULE_QUESTION.search(message):
        return "policy", 0.86, "deterministic"

    if entities.get("wants_human"):
        return "human", 0.9, "deterministic"

    if has_action_word(message):
        return "refund", 0.88, "deterministic"

    if entities.get("order_no") and any(word in message for word in ("退", "不要", "换")):
        return "refund", 0.86, "deterministic"

    scores = deterministic_scores(normalized)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_intent, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0

    if top_score <= 0:
        return "unknown", 0.25, "fallback"

    # 问规则而不是提申请："能退货吗 / 运费谁承担" 交给知识库
    if is_consultation(message) and top_intent in {"refund", "order"}:
        return "policy", 0.84, "deterministic"

    # 两个意图贴得太近说明信息不足，交给大模型判（若可用）
    if top_score - second_score < 1.0:
        return "unknown", 0.4, "fallback"

    confidence = min(0.95, 0.55 + top_score / 10)
    return top_intent, round(confidence, 2), "deterministic"


LLM_CLASSIFY_SYSTEM = """你是企业售后支持的意图分类器。
只能从以下意图中选择一个：policy（政策/规则咨询）、order（订单与物流）、refund（退款退货）、
invoice（发票与保修）、human（要求人工/投诉）、unknown（无法判断）。
只输出 JSON：{"intent": "...", "confidence": 0.0-1.0, "reason": "一句话理由"}"""


def llm_classify(message: str) -> tuple[str, float, str] | None:
    llm = get_llm()
    if not llm.enabled:
        return None

    payload = llm.complete_json(LLM_CLASSIFY_SYSTEM, f"用户消息：{message}")
    if not payload:
        return None

    intent = str(payload.get("intent", "unknown")).strip().lower()
    if intent not in {"policy", "order", "refund", "invoice", "human", "unknown"}:
        return None

    try:
        confidence = float(payload.get("confidence", 0.6))
    except (TypeError, ValueError):
        confidence = 0.6

    return intent, round(min(max(confidence, 0.0), 1.0), 2), "llm"


def classify_intent(state: SupportState) -> dict:
    message = (state.get("message") or "").strip()
    intent, confidence, source = classify_text(message)
    entities = extract_entities(message)

    # 词表 + 规则判不出来才外呼模型，省 token 也省延迟
    if intent == "unknown" and confidence < 0.5:
        routed = llm_classify(message)
        if routed:
            intent, confidence, source = routed

    return {
        "intent": intent,
        "intent_confidence": confidence,
        "routing_source": source,
        "entities": entities,
        "order_no": entities.get("order_no", ""),
        **trace(
            "意图分类",
            detail=f"{intent} · 置信度 {confidence:.2f} · 来源 {source}",
            intent=intent,
            confidence=confidence,
            source=source,
        ),
    }
