from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.domain.models import Order, Refund
from app.domain.policy import evaluate_refund

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def make_order(**overrides) -> Order:
    base = Order(
        order_no="SO202610200001",
        customer_id="C1001",
        product_name="星曜降噪耳机 Pro",
        category="耳机",
        amount=299.0,
        status="delivered",
        paid_at=(NOW - timedelta(days=5)).isoformat(),
        delivered_at=(NOW - timedelta(days=3)).isoformat(),
        carrier="顺丰速运",
        tracking_no="SF123",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


@pytest.fixture()
def settings():
    return get_settings()


def test_auto_refund_within_window(settings):
    decision = evaluate_refund(
        make_order(), [], settings=settings, quality_claim=False, now=NOW
    )
    assert decision.eligible is True
    assert decision.action == "auto_refund"
    assert decision.requires_approval is False
    assert decision.refundable_amount == pytest.approx(299.0)


def test_high_amount_requires_approval(settings):
    decision = evaluate_refund(
        make_order(amount=899.0), [], settings=settings, quality_claim=False, now=NOW
    )
    assert decision.action == "manual_review"
    assert decision.requires_approval is True
    assert decision.rule_code == "R8_MANUAL_REVIEW"
    assert decision.risk_notes


def test_out_of_window_rejected(settings):
    order = make_order(delivered_at=(NOW - timedelta(days=30)).isoformat())
    decision = evaluate_refund(order, [], settings=settings, quality_claim=False, now=NOW)
    assert decision.eligible is False
    assert decision.rule_code == "R4_OUT_OF_WINDOW"


def test_quality_claim_extends_window(settings):
    order = make_order(delivered_at=(NOW - timedelta(days=12)).isoformat(), amount=199.0)
    no_claim = evaluate_refund(order, [], settings=settings, quality_claim=False, now=NOW)
    assert no_claim.eligible is False

    with_claim = evaluate_refund(order, [], settings=settings, quality_claim=True, now=NOW)
    assert with_claim.eligible is True
    # 质量问题即便金额不高也要人工确认凭证
    assert with_claim.requires_approval is True


def test_custom_goods_rejected_without_quality_claim(settings):
    order = make_order(is_custom=True, amount=199.0)
    decision = evaluate_refund(order, [], settings=settings, quality_claim=False, now=NOW)
    assert decision.eligible is False
    assert decision.rule_code == "R3_CUSTOM_GOODS"


def test_virtual_goods_rejected(settings):
    order = make_order(is_virtual=True, amount=168.0)
    decision = evaluate_refund(order, [], settings=settings, quality_claim=False, now=NOW)
    assert decision.rule_code == "R3_VIRTUAL_GOODS"


def test_duplicate_refund_rejected(settings):
    existing = Refund(
        refund_no="RF001",
        order_no="SO202610200001",
        amount=299.0,
        status="pending",
        reason="test",
        created_at=NOW.isoformat(),
    )
    decision = evaluate_refund(
        make_order(), [existing], settings=settings, quality_claim=False, now=NOW
    )
    assert decision.rule_code == "R5_DUPLICATE"


def test_missing_order_rejected(settings):
    decision = evaluate_refund(None, [], settings=settings, quality_claim=False, now=NOW)
    assert decision.rule_code == "R1_ORDER_NOT_FOUND"
    assert decision.refundable_amount == 0.0


def test_frequent_refunder_requires_approval(settings):
    decision = evaluate_refund(
        make_order(amount=99.0),
        [],
        settings=settings,
        quality_claim=False,
        prior_refund_count=3,
        now=NOW,
    )
    assert decision.eligible is True
    assert decision.requires_approval is True
    assert any("退款次数" in note for note in decision.risk_notes)
