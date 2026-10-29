from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Order:
    order_no: str
    customer_id: str
    product_name: str
    category: str
    amount: float  # 实付 = 商品标价 − 优惠分摊 + 运费
    status: str  # pending | paid | shipped | delivered | cancelled
    paid_at: str | None = None
    delivered_at: str | None = None
    carrier: str | None = None
    tracking_no: str | None = None
    is_virtual: bool = False
    is_custom: bool = False
    # 金额明细：退款要对账，就必须能拆开算
    goods_amount: float = 0.0
    coupon_amount: float = 0.0
    shipping_fee: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Payment:
    payment_no: str
    order_no: str
    amount: float
    method: str
    status: str
    paid_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Refund:
    refund_no: str
    order_no: str
    amount: float
    status: str  # pending | approved | rejected | done
    reason: str
    created_at: str
    decided_by: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Ticket:
    ticket_no: str
    thread_id: str
    order_no: str | None
    intent: str
    level: str
    status: str
    summary: str
    created_at: str
    updated_at: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "meta": self.meta}


@dataclass
class Approval:
    approval_id: str
    thread_id: str
    order_no: str | None
    intent: str
    amount: float
    reason: str
    risk_notes: list[str]
    payload: dict[str, Any]
    status: str  # pending | approved | rejected
    created_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    decision_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
