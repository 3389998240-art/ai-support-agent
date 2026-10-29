from __future__ import annotations

import pytest

from app.graph.classifier import (
    classify_text,
    extract_entities,
    llm_classify,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("我的订单到哪了", "order"),
        ("快递三天没动了", "order"),
        ("我要退款", "refund"),
        ("这个耳机坏了能退货吗", "policy"),
        ("七天无理由怎么算时间", "policy"),
        ("退款一般多久到账", "policy"),
        ("电子发票怎么开", "invoice"),
        ("保修期是多久", "invoice"),
        ("我要投诉，转人工", "human"),
    ],
)
def test_intent_classification(message: str, expected: str) -> None:
    intent, confidence, source = classify_text(message)
    assert intent == expected
    assert confidence > 0
    assert source in {"deterministic", "fallback"}


def test_unknown_intent_is_flagged() -> None:
    intent, confidence, source = classify_text("今天天气不错")
    assert intent == "unknown"
    assert confidence < 0.5
    assert source == "fallback"


def test_entity_extraction_order_no() -> None:
    entities = extract_entities("订单号 SO202610180001 什么时候到")
    assert entities["order_no"] == "SO202610180001"


def test_entity_extraction_quality_claim() -> None:
    entities = extract_entities("耳机开不了机，这是质量问题吧")
    assert entities["quality_claim"] is True
    assert entities["order_no"] == ""


def test_llm_classifier_disabled_under_mock() -> None:
    """默认 mock 模式下不允许外呼模型，必须返回 None 走确定性链路。"""
    assert llm_classify("随便问一句") is None

def test_rule_question_about_human_goes_to_policy() -> None:
    """"什么情况必须转人工"是在问规则，不能直接进人工队列。"""
    assert classify_text("什么情况必须转人工")[0] == "policy"


def test_consultation_without_domain_noun_still_routes_to_policy() -> None:
    """用户不会说"退货政策"，会说"七天无理由从什么时候开始算"。"""
    assert classify_text("七天无理由从什么时候开始算")[0] == "policy"
    assert classify_text("付款后多久发货")[0] == "policy"


def test_short_product_terms_are_on_topic() -> None:
    """主题闸门不能把"专票/寄修/无理由"这类真实说法判成越界。"""
    from app.agents.knowledge import _is_on_topic

    for query in ("七天无理由从什么时候开始算", "开增值税专票需要什么资料", "寄修要多久能修好"):
        assert _is_on_topic(query) is True


def test_off_topic_still_rejected() -> None:
    from app.agents.knowledge import _is_on_topic

    for query in ("今天天气怎么样", "帮我写一首诗", "红烧肉怎么做"):
        assert _is_on_topic(query) is False
