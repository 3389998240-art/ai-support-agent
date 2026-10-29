from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from app.core.config import Settings
from app.domain.models import Order, Refund

# 用户主张"没收到货"的话术（注意排除"退款没到账"这类，避免误判）
# 拒绝时给用户看的人话标签（规则标题是给审核员看的）
REJECT_LABELS = {
    "R2_ORDER_STATUS": "订单状态不支持退款",
    "R3_VIRTUAL_GOODS": "虚拟服务激活后不支持退款",
    "R3_CUSTOM_GOODS": "定制商品不支持无理由退货",
    "R4_OUT_OF_WINDOW": "已超过售后申请时效",
    "R5_DUPLICATE": "该订单已有进行中的退款单",
}

DELIVERY_DISPUTE_HINTS = (
    "没收到货",
    "没收到东西",
    "没有收到货",
    "未收到货",
    "没收到快递",
    "没拿到",
    "货没到",
    "没送货",
    "物流显示签收但我没收到",
)


@dataclass
class RuleCheck:
    rule_id: str
    title: str
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PolicyDecision:
    """退款资格判定结果。

    关键点：结论完全由代码算出，模型只负责把结论翻译成人话。
    这样同一笔订单无论问多少次、换哪个模型，结论都一致（可测、可审计）。
    """

    eligible: bool
    action: str  # auto_refund | manual_review | reject
    rule_code: str
    reason: str
    requires_approval: bool
    refundable_amount: float
    checks: list[RuleCheck] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    # 金额明细：钱怎么算出来的，必须能对账
    breakdown: dict = field(default_factory=dict)
    # 所有未通过的规则，而不只是触发拒绝的那一条
    failed_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "action": self.action,
            "rule_code": self.rule_code,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "refundable_amount": round(self.refundable_amount, 2),
            "checks": [check.to_dict() for check in self.checks],
            "risk_notes": self.risk_notes,
            "breakdown": self.breakdown,
            "failed_rules": self.failed_rules,
        }


def _days_since(timestamp: str | None, now: datetime) -> int | None:
    if not timestamp:
        return None
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max((now - moment).days, 0)


def has_delivery_dispute(message: str) -> bool:
    return any(hint in message for hint in DELIVERY_DISPUTE_HINTS)


def calculate_refundable(order: Order, *, quality_claim: bool) -> dict:
    """退款金额 = 实付 − 应承担运费。

    模型里 amount 是**实付**（= 商品标价 − 优惠分摊 + 运费），所以：
    - 质量问题：运费由平台承担，全退实付；
    - 无理由：运费由用户承担，退「实付 − 运费」，等价于「标价 − 优惠」。
    把标价/优惠/运费一并返回，前端和工单都能对账。
    """
    goods = float(order.goods_amount or order.amount)
    coupon = float(order.coupon_amount or 0.0)
    shipping = float(order.shipping_fee or 0.0)
    paid = float(order.amount)

    deducted_shipping = 0.0 if quality_claim else shipping
    refundable = max(0.0, round(paid - deducted_shipping, 2))

    return {
        "goods_amount": round(goods, 2),
        "coupon_deducted": round(coupon, 2),
        "shipping_fee": round(shipping, 2),
        "paid_amount": round(paid, 2),
        "shipping_deducted": round(deducted_shipping, 2),
        "refundable": refundable,
    }


def evaluate_refund(
    order: Order | None,
    refunds: list[Refund],
    *,
    settings: Settings,
    quality_claim: bool,
    prior_refund_count: int = 0,
    delivery_dispute: bool = False,
    now: datetime | None = None,
) -> PolicyDecision:
    """按 R1→R9 全量求值，再决定结论。

    与上一版的区别：**不再短路**。所有规则都算一遍，拒绝时能一次说清
    "你既超期、又是定制商品"，审批单里也能看到完整规则画像。
    """
    now = now or datetime.now(timezone.utc)
    checks: list[RuleCheck] = []
    risk_notes: list[str] = []

    # ── R1 订单存在性（唯一必须短路的一条，没订单后面都无从谈起）──────
    if order is None:
        checks.append(RuleCheck("R1", "订单是否存在", False, "未查到该订单号"))
        return PolicyDecision(
            eligible=False,
            action="reject",
            rule_code="R1_ORDER_NOT_FOUND",
            reason="系统里查不到这个订单号，请提供订单号后重新发起退款申请。",
            requires_approval=False,
            refundable_amount=0.0,
            checks=checks,
            failed_rules=["R1_ORDER_NOT_FOUND"],
        )
    checks.append(RuleCheck("R1", "订单是否存在", True, f"已找到订单 {order.order_no}"))

    # ── R2 订单状态 ──────────────────────────────────────────────
    status_ok = order.status in {"paid", "shipped", "delivered"}
    checks.append(RuleCheck("R2", "订单状态是否可退", status_ok, f"当前状态：{order.status}"))

    # ── R3 商品类型 ──────────────────────────────────────────────
    if order.is_virtual:
        checks.append(RuleCheck("R3", "商品类型", False, "虚拟服务已激活，不支持自助退款"))
    elif order.is_custom and not quality_claim:
        checks.append(RuleCheck("R3", "商品类型", False, "定制商品不支持七天无理由退货"))
    else:
        checks.append(RuleCheck("R3", "商品类型", True, "属于可退商品"))

    # ── R4 售后时效 ──────────────────────────────────────────────
    window = settings.quality_refund_window_days if quality_claim else settings.refund_window_days
    anchor = order.delivered_at or order.paid_at
    elapsed = _days_since(anchor, now)
    within_window = elapsed is not None and elapsed <= window
    checks.append(
        RuleCheck(
            "R4",
            "售后时效",
            within_window,
            f"签收后第 {elapsed} 天，{'质量问题' if quality_claim else '无理由'}窗口 {window} 天",
        )
    )

    # ── R5 重复申请（本单已有进行中的退款）────────────────────────
    active = [r for r in refunds if r.status in {"pending", "approved"}]
    checks.append(
        RuleCheck(
            "R5",
            "是否重复申请",
            not active,
            "无进行中的退款" if not active else f"已存在退款单 {active[0].refund_no}",
        )
    )

    # ── R9 签收争议：声称没收到，但系统显示已签收 ──────────────────
    dispute_hit = delivery_dispute and order.status == "delivered"
    checks.append(
        RuleCheck(
            "R9",
            "签收争议",
            not dispute_hit,
            "无签收争议" if not dispute_hit else "用户主张未收到货，但物流显示已签收，需人工核实签收底单",
        )
    )
    if dispute_hit:
        risk_notes.append("签收争议：需人工调取签收底单/监控，不能直接退款")

    # ── R6 金额阈值 ──────────────────────────────────────────────
    breakdown = calculate_refundable(order, quality_claim=quality_claim)
    amount = breakdown["refundable"]
    amount_ok = amount <= settings.refund_auto_limit
    checks.append(
        RuleCheck(
            "R6",
            "金额阈值",
            amount_ok,
            f"退款金额 ¥{amount:.2f}，自助上限 ¥{settings.refund_auto_limit:.2f}",
        )
    )
    if not amount_ok:
        risk_notes.append(f"退款金额 ¥{amount:.2f} 超过自助审批上限")

    # ── R7 退款频次 ──────────────────────────────────────────────
    frequency_ok = prior_refund_count < 3
    checks.append(RuleCheck("R7", "退款频次", frequency_ok, f"近 30 天退款 {prior_refund_count} 次"))
    if not frequency_ok:
        risk_notes.append("近 30 天退款次数达到 3 次，需人工核实是否存在异常退款行为")

    if quality_claim:
        risk_notes.append("用户主张质量问题，建议人工确认故障描述与凭证")

    # ── 结论 ────────────────────────────────────────────────────
    # 硬性红线（会导致拒绝）：R2/R3/R4/R5 全部求值，一次说清所有不通过项
    hard_candidates: list[tuple[str, RuleCheck]] = [("R2_ORDER_STATUS", checks[1])]
    if order.is_virtual:
        hard_candidates.append(("R3_VIRTUAL_GOODS", checks[2]))
    elif order.is_custom and not quality_claim:
        hard_candidates.append(("R3_CUSTOM_GOODS", checks[2]))
    hard_candidates.append(("R4_OUT_OF_WINDOW", checks[3]))
    hard_candidates.append(("R5_DUPLICATE", checks[4]))

    failed_hard = [(code, check) for code, check in hard_candidates if not check.passed]

    if failed_hard:
        rule_code = failed_hard[0][0]
        details = "；".join(
            f"{REJECT_LABELS.get(code, check.title)}（{check.detail}）"
            for code, check in failed_hard
        )
        return PolicyDecision(
            eligible=False,
            action="reject",
            rule_code=rule_code,
            reason=f"本次退款不通过：{details}。",
            requires_approval=False,
            refundable_amount=0.0,
            checks=checks,
            risk_notes=risk_notes,
            breakdown=breakdown,
            failed_rules=[code for code, _ in failed_hard],
        )

    requires_approval = dispute_hit or not amount_ok or not frequency_ok or quality_claim
    if requires_approval:
        reasons = []
        if dispute_hit:
            reasons.append("签收争议需人工核实")
        if not amount_ok:
            reasons.append("金额超过自助上限")
        if not frequency_ok:
            reasons.append("退款频次过高")
        if quality_claim:
            reasons.append("质量问题需人工确认凭证")
        return PolicyDecision(
            eligible=True,
            action="manual_review",
            rule_code="R8_MANUAL_REVIEW",
            reason=f"符合退款条件，但命中人工审批规则（{'；'.join(reasons)}），已提交审核。",
            requires_approval=True,
            refundable_amount=amount,
            checks=checks,
            risk_notes=risk_notes,
            breakdown=breakdown,
            failed_rules=[],
        )

    return PolicyDecision(
        eligible=True,
        action="auto_refund",
        rule_code="R0_AUTO_APPROVED",
        reason="符合退款条件，可自助退款，已提交退款单。",
        requires_approval=False,
        refundable_amount=amount,
        checks=checks,
        risk_notes=risk_notes,
        breakdown=breakdown,
        failed_rules=[],
    )
