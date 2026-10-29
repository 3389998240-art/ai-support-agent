from __future__ import annotations


class ThreadNotFoundError(Exception):
    """线程不存在。"""


class ThreadForbiddenError(Exception):
    """会话存在但不属于当前客户（或历史会话无归属，不允许被接管）。"""


class ThreadBusyError(Exception):
    """线程正在等待人工处理，必须先恢复再继续。"""


class NotWaitingError(Exception):
    """线程并不处于等待人工的状态。"""


class GuardRejectedError(Exception):
    """输入被安全策略拦截。"""


class ApprovalConflictError(Exception):
    """审批单已被别人处理（并发抢占失败）。"""

    def __init__(self, approval_id: str) -> None:
        self.approval_id = approval_id
        super().__init__(f"审批单 {approval_id} 已被处理，本次操作已取消")


class DuplicateRefundError(Exception):
    """同一订单已有进行中的退款（唯一索引兜底）。"""

    def __init__(self, order_no: str) -> None:
        self.order_no = order_no
        super().__init__(f"订单 {order_no} 已有进行中的退款单")
