from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.domain.models import Approval, Order, Payment, Refund, Ticket

logger = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_no      TEXT PRIMARY KEY,
    customer_id   TEXT NOT NULL,
    product_name  TEXT NOT NULL,
    category      TEXT NOT NULL,
    amount        REAL NOT NULL,
    status        TEXT NOT NULL,
    paid_at       TEXT,
    delivered_at  TEXT,
    carrier       TEXT,
    tracking_no   TEXT,
    is_virtual    INTEGER NOT NULL DEFAULT 0,
    is_custom     INTEGER NOT NULL DEFAULT 0,
    goods_amount  REAL NOT NULL DEFAULT 0,
    coupon_amount REAL NOT NULL DEFAULT 0,
    shipping_fee  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS payments (
    payment_no  TEXT PRIMARY KEY,
    order_no    TEXT NOT NULL,
    amount      REAL NOT NULL,
    method      TEXT NOT NULL,
    status      TEXT NOT NULL,
    paid_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS thread_owners (
    thread_id   TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL,
    customer_id   TEXT,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS refunds (
    refund_no   TEXT PRIMARY KEY,
    order_no    TEXT NOT NULL,
    amount      REAL NOT NULL,
    status      TEXT NOT NULL,
    reason      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    decided_by  TEXT,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_no   TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL,
    order_no    TEXT,
    intent      TEXT NOT NULL,
    level       TEXT NOT NULL,
    status      TEXT NOT NULL,
    summary     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id   TEXT PRIMARY KEY,
    thread_id     TEXT NOT NULL,
    order_no      TEXT,
    intent        TEXT NOT NULL,
    amount        REAL NOT NULL,
    reason        TEXT NOT NULL,
    risk_notes    TEXT NOT NULL,
    payload       TEXT NOT NULL,
    status        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    decided_at    TEXT,
    decided_by    TEXT,
    decision_note TEXT
);

CREATE INDEX IF NOT EXISTS idx_refunds_order ON refunds(order_no);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_approvals_thread ON approvals(thread_id, order_no);
CREATE INDEX IF NOT EXISTS idx_tickets_thread ON tickets(thread_id);
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="seconds") if moment else None


class SupportRepository:
    """SQLite 仓储层。

    业务数据访问全部收在这里，LangGraph 节点里不允许出现裸 SQL，
    方便后面换 PostgreSQL 时只改这一个文件。
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # 用 RLock：事务里的代码如果又调用了仓储的普通方法，不会自己把自己锁死
        self._lock = threading.RLock()
        # isolation_level=None → 自动提交，这样 transaction() 里可以显式 BEGIN IMMEDIATE
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate_locked()

    def _migrate_locked(self) -> None:
        """幂等迁移：补列、回填、建「同一订单只能有一笔进行中退款」的唯一索引。"""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(orders)")}
        for column, ddl in (
            ("goods_amount", "REAL NOT NULL DEFAULT 0"),
            ("coupon_amount", "REAL NOT NULL DEFAULT 0"),
            ("shipping_fee", "REAL NOT NULL DEFAULT 0"),
        ):
            if column not in columns:
                logger.info("迁移：orders 增加列 %s", column)
                self._conn.execute(f"ALTER TABLE orders ADD COLUMN {column} {ddl}")

        self._conn.execute("UPDATE orders SET goods_amount = amount WHERE goods_amount = 0")

        try:
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_refunds_active "
                "ON refunds(order_no) WHERE status IN ('pending', 'approved')"
            )
        except sqlite3.IntegrityError:
            logger.warning(
                "已存在同一订单多笔进行中退款，唯一索引未建立；请先清理脏数据再启动"
            )

    @contextmanager
    def transaction(self):
        """显式事务：BEGIN IMMEDIATE 拿写锁，异常整体回滚。

        用于「抢审批单 + 建退款单 + 建工单」这种必须同生共死的操作。
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    # ------------------------------------------------------------------ 基础
    def close(self) -> None:
        self._conn.close()

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    # ------------------------------------------------------------------ 订单
    def get_order(self, order_no: str) -> Order | None:
        rows = self._query("SELECT * FROM orders WHERE order_no = ?", (order_no,))
        return self._row_to_order(rows[0]) if rows else None

    def list_orders(self, customer_id: str) -> list[Order]:
        rows = self._query(
            "SELECT * FROM orders WHERE customer_id = ? ORDER BY paid_at DESC",
            (customer_id,),
        )
        return [self._row_to_order(row) for row in rows]

    def latest_order(self, customer_id: str) -> Order | None:
        rows = self._query(
            "SELECT * FROM orders WHERE customer_id = ? ORDER BY paid_at DESC LIMIT 1",
            (customer_id,),
        )
        return self._row_to_order(rows[0]) if rows else None

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> Order:
        return Order(
            order_no=row["order_no"],
            customer_id=row["customer_id"],
            product_name=row["product_name"],
            category=row["category"],
            amount=float(row["amount"]),
            status=row["status"],
            paid_at=row["paid_at"],
            delivered_at=row["delivered_at"],
            carrier=row["carrier"],
            tracking_no=row["tracking_no"],
            is_virtual=bool(row["is_virtual"]),
            is_custom=bool(row["is_custom"]),
            goods_amount=float(row["goods_amount"] or row["amount"]),
            coupon_amount=float(row["coupon_amount"] or 0.0),
            shipping_fee=float(row["shipping_fee"] or 0.0),
        )

    # ------------------------------------------------------------------ 支付
    def get_payments(self, order_no: str) -> list[Payment]:
        rows = self._query(
            "SELECT * FROM payments WHERE order_no = ? ORDER BY paid_at",
            (order_no,),
        )
        return [
            Payment(
                payment_no=row["payment_no"],
                order_no=row["order_no"],
                amount=float(row["amount"]),
                method=row["method"],
                status=row["status"],
                paid_at=row["paid_at"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------ 退款
    def list_refunds(self, order_no: str) -> list[Refund]:
        rows = self._query(
            "SELECT * FROM refunds WHERE order_no = ? ORDER BY created_at DESC",
            (order_no,),
        )
        return [self._row_to_refund(row) for row in rows]

    def count_customer_refunds(self, customer_id: str, days: int = 30) -> int:
        since = iso(utc_now() - timedelta(days=days))
        rows = self._query(
            """
            SELECT COUNT(*) AS total FROM refunds r
            JOIN orders o ON o.order_no = r.order_no
            WHERE o.customer_id = ? AND r.created_at >= ?
            """,
            (customer_id, since),
        )
        return int(rows[0]["total"]) if rows else 0

    def create_refund(
        self,
        *,
        order_no: str,
        amount: float,
        reason: str,
        status: str = "pending",
        decided_by: str | None = None,
        note: str | None = None,
    ) -> Refund:
        refund = Refund(
            refund_no=f"RF{utc_now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}",
            order_no=order_no,
            amount=round(amount, 2),
            status=status,
            reason=reason,
            created_at=iso(utc_now()) or "",
            decided_by=decided_by,
            note=note,
        )
        self._execute(
            """
            INSERT INTO refunds (refund_no, order_no, amount, status, reason, created_at, decided_by, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                refund.refund_no,
                refund.order_no,
                refund.amount,
                refund.status,
                refund.reason,
                refund.created_at,
                refund.decided_by,
                refund.note,
            ),
        )
        return refund

    @staticmethod
    def _row_to_refund(row: sqlite3.Row) -> Refund:
        return Refund(
            refund_no=row["refund_no"],
            order_no=row["order_no"],
            amount=float(row["amount"]),
            status=row["status"],
            reason=row["reason"],
            created_at=row["created_at"],
            decided_by=row["decided_by"],
            note=row["note"],
        )

    # ------------------------------------------------------------------ 工单
    def create_ticket(
        self,
        *,
        thread_id: str,
        intent: str,
        summary: str,
        order_no: str | None = None,
        level: str = "P3",
        status: str = "open",
        meta: dict[str, Any] | None = None,
    ) -> Ticket:
        now = iso(utc_now()) or ""
        ticket = Ticket(
            ticket_no=f"TK{utc_now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:3].upper()}",
            thread_id=thread_id,
            order_no=order_no,
            intent=intent,
            level=level,
            status=status,
            summary=summary,
            created_at=now,
            updated_at=now,
            meta=meta or {},
        )
        import json

        self._execute(
            """
            INSERT INTO tickets (ticket_no, thread_id, order_no, intent, level, status, summary, created_at, updated_at, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket.ticket_no,
                ticket.thread_id,
                ticket.order_no,
                ticket.intent,
                ticket.level,
                ticket.status,
                ticket.summary,
                ticket.created_at,
                ticket.updated_at,
                json.dumps(ticket.meta, ensure_ascii=False),
            ),
        )
        return ticket

    def list_tickets(self, thread_id: str | None = None, limit: int = 20) -> list[dict]:
        import json

        if thread_id:
            rows = self._query(
                "SELECT * FROM tickets WHERE thread_id = ? ORDER BY created_at DESC LIMIT ?",
                (thread_id, limit),
            )
        else:
            rows = self._query(
                "SELECT * FROM tickets ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [
            {
                "ticket_no": row["ticket_no"],
                "thread_id": row["thread_id"],
                "order_no": row["order_no"],
                "intent": row["intent"],
                "level": row["level"],
                "status": row["status"],
                "summary": row["summary"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "meta": json.loads(row["meta"] or "{}"),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------ 会话归属
    def bind_thread_owner(self, thread_id: str, customer_id: str) -> None:
        """首次写入即绑定；已有绑定不覆盖（避免会话被转移到别人名下）。"""
        rows = self._query("SELECT 1 FROM thread_owners WHERE thread_id = ?", (thread_id,))
        if rows:
            return
        self._execute(
            "INSERT OR IGNORE INTO thread_owners (thread_id, customer_id, created_at) VALUES (?, ?, ?)",
            (thread_id, customer_id, iso(utc_now())),
        )

    def get_thread_owner(self, thread_id: str) -> str | None:
        rows = self._query(
            "SELECT customer_id FROM thread_owners WHERE thread_id = ?", (thread_id,)
        )
        return rows[0]["customer_id"] if rows else None

    # ------------------------------------------------------------------ 用户
    def get_user(self, username: str) -> dict | None:
        rows = self._query(
            "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "customer_id": row["customer_id"],
            "password_hash": row["password_hash"],
            "is_active": bool(row["is_active"]),
        }

    def list_users(self) -> list[dict]:
        rows = self._query("SELECT username, display_name, role FROM users ORDER BY username")
        return [
            {"username": row["username"], "display_name": row["display_name"], "role": row["role"]}
            for row in rows
        ]

    def upsert_user(
        self,
        *,
        username: str,
        display_name: str,
        role: str,
        password_hash: str,
        customer_id: str | None = None,
    ) -> None:
        self._execute(
            """
            INSERT INTO users (username, display_name, role, customer_id, password_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                display_name = excluded.display_name,
                role = excluded.role,
                customer_id = excluded.customer_id,
                password_hash = excluded.password_hash
            """,
            (username, display_name, role, customer_id, password_hash, iso(utc_now())),
        )

    def ensure_demo_users(self, password: str) -> bool:
        """首次初始化演示账号；已有用户则跳过。

        密码不明文入库：统一走 PBKDF2-HMAC-SHA256 加盐哈希。
        """
        from app.domain.security import hash_password

        if self._query("SELECT 1 FROM users LIMIT 1"):
            return False

        demo = [
            ("alice", "Alice（客户 C1001）", "customer", "C1001"),
            ("bob", "Bob（客户 C1002）", "customer", "C1002"),
            ("agent01", "小艾（售后审核员）", "agent", None),
            ("admin", "管理员", "admin", None),
        ]
        for username, display_name, role, customer_id in demo:
            self.upsert_user(
                username=username,
                display_name=display_name,
                role=role,
                customer_id=customer_id,
                password_hash=hash_password(password),
            )

        logger.info("演示账号已创建: %s（初始密码见 DEMO_PASSWORD）", ", ".join(item[0] for item in demo))
        return True

    # ------------------------------------------------------------------ 审批
    def create_approval(
        self,
        *,
        thread_id: str,
        intent: str,
        amount: float,
        reason: str,
        risk_notes: list[str],
        payload: dict[str, Any],
        order_no: str | None = None,
    ) -> Approval:
        import json

        approval = Approval(
            approval_id=f"AP{utc_now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}",
            thread_id=thread_id,
            order_no=order_no,
            intent=intent,
            amount=round(amount, 2),
            reason=reason,
            risk_notes=risk_notes,
            payload=payload,
            status="pending",
            created_at=iso(utc_now()) or "",
        )
        self._execute(
            """
            INSERT INTO approvals (approval_id, thread_id, order_no, intent, amount, reason, risk_notes, payload, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval.approval_id,
                approval.thread_id,
                approval.order_no,
                approval.intent,
                approval.amount,
                approval.reason,
                json.dumps(risk_notes, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
                approval.status,
                approval.created_at,
            ),
        )
        return approval

    def get_approval(self, approval_id: str) -> dict | None:
        import json

        rows = self._query("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,))
        if not rows:
            return None
        row = rows[0]
        return {
            "approval_id": row["approval_id"],
            "thread_id": row["thread_id"],
            "order_no": row["order_no"],
            "intent": row["intent"],
            "amount": float(row["amount"]),
            "reason": row["reason"],
            "risk_notes": json.loads(row["risk_notes"] or "[]"),
            "payload": json.loads(row["payload"] or "{}"),
            "status": row["status"],
            "created_at": row["created_at"],
            "decided_at": row["decided_at"],
            "decided_by": row["decided_by"],
            "decision_note": row["decision_note"],
        }

    def list_approvals(self, status: str | None = "pending", limit: int = 50) -> list[dict]:
        rows = (
            self._query(
                "SELECT approval_id FROM approvals WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
            if status
            else self._query(
                "SELECT approval_id FROM approvals ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        )
        result = []
        for row in rows:
            item = self.get_approval(row["approval_id"])
            if item:
                result.append(item)
        return result

    def resolve_approval(
        self,
        approval_id: str,
        *,
        status: str,
        decided_by: str,
        note: str | None = None,
    ) -> None:
        self._execute(
            """
            UPDATE approvals
            SET status = ?, decided_by = ?, decision_note = ?, decided_at = ?
            WHERE approval_id = ?
            """,
            (status, decided_by, note, iso(utc_now()), approval_id),
        )

    def claim_approval(
        self,
        approval_id: str,
        *,
        status: str = "approved",
        decided_by: str = "system",
        note: str | None = None,
    ) -> bool:
        """原子抢占审批单：只有 pending → 目标状态成功时才返回 True。

        这是并发审批的第一道闸：两个请求同时点"通过"，只有一行被 update，
        后到的那个拿到 0 行影响，直接放弃执行，不会重复退款。
        """
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE approvals
                SET status = ?, decided_by = ?, decision_note = ?, decided_at = ?
                WHERE approval_id = ? AND status = 'pending'
                """,
                (status, decided_by, note, iso(utc_now()), approval_id),
            )
            return cursor.rowcount == 1

    def find_pending_approval(self, thread_id: str, order_no: str | None = None) -> dict | None:
        """幂等键：退款按 (thread_id, order_no)，人工接管按 thread_id。"""
        if order_no:
            rows = self._query(
                """
                SELECT approval_id FROM approvals
                WHERE thread_id = ? AND order_no = ? AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1
                """,
                (thread_id, order_no),
            )
        else:
            rows = self._query(
                """
                SELECT approval_id FROM approvals
                WHERE thread_id = ? AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1
                """,
                (thread_id,),
            )
        return self.get_approval(rows[0]["approval_id"]) if rows else None

    def commit_refund(
        self,
        *,
        thread_id: str,
        order_no: str,
        amount: float,
        reason: str,
        status: str,
        decided_by: str | None,
        note: str | None,
        approval_id: str | None,
        level: str = "P2",
        summary: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> tuple[Refund, Ticket]:
        """一个事务里完成：抢审批单 → 建退款单 → 建工单。

        任何一步失败整体回滚，不会留下"有退款单没工单"或"工单建了审批没关"。
        """
        import json

        now = iso(utc_now()) or ""
        refund = Refund(
            refund_no=f"RF{utc_now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}",
            order_no=order_no,
            amount=round(amount, 2),
            status=status,
            reason=reason,
            created_at=now,
            decided_by=decided_by,
            note=note,
        )
        ticket = Ticket(
            ticket_no=f"TK{utc_now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:3].upper()}",
            thread_id=thread_id,
            order_no=order_no,
            intent="refund",
            level=level,
            status="processing",
            summary=summary or f"退款 ¥{refund.amount:.2f}",
            created_at=now,
            updated_at=now,
            meta=meta or {},
        )

        try:
            with self.transaction() as conn:
                if approval_id:
                    cursor = conn.execute(
                        """
                        UPDATE approvals
                        SET status = ?, decided_by = ?, decision_note = ?, decided_at = ?
                        WHERE approval_id = ? AND status = 'pending'
                        """,
                        (status, decided_by, note, now, approval_id),
                    )
                    if cursor.rowcount != 1:
                        from app.core.errors import ApprovalConflictError

                        raise ApprovalConflictError(approval_id)

                conn.execute(
                    """
                    INSERT INTO refunds (refund_no, order_no, amount, status, reason, created_at, decided_by, note)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        refund.refund_no,
                        refund.order_no,
                        refund.amount,
                        refund.status,
                        refund.reason,
                        refund.created_at,
                        refund.decided_by,
                        refund.note,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO tickets (ticket_no, thread_id, order_no, intent, level, status, summary, created_at, updated_at, meta)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticket.ticket_no,
                        ticket.thread_id,
                        ticket.order_no,
                        ticket.intent,
                        ticket.level,
                        ticket.status,
                        ticket.summary,
                        ticket.created_at,
                        ticket.updated_at,
                        json.dumps(ticket.meta, ensure_ascii=False),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            from app.core.errors import DuplicateRefundError

            raise DuplicateRefundError(order_no) from exc

        return refund, ticket

    # ------------------------------------------------------------------ 种子
    def seed_demo_data(self) -> bool:
        """幂等地写入演示数据；已有订单则跳过。"""
        if self._query("SELECT 1 FROM orders LIMIT 1"):
            return False

        now = utc_now()
        demo = [
            {
                "order_no": "SO202610180001",
                "customer_id": "C1001",
                "product_name": "星曜降噪耳机 Pro",
                "category": "耳机",
                "amount": 899.0,
                "status": "delivered",
                "paid_at": now - timedelta(days=6),
                "delivered_at": now - timedelta(days=3),
                "carrier": "顺丰速运",
                "tracking_no": "SF1029304756",
            },
            {
                "order_no": "SO202610120002",
                "customer_id": "C1001",
                "product_name": "星辰智能手表 S2",
                "category": "手表",
                "amount": 1299.0,
                "status": "delivered",
                "paid_at": now - timedelta(days=23),
                "delivered_at": now - timedelta(days=20),
                "carrier": "京东物流",
                "tracking_no": "JD0099283746",
            },
            {
                "order_no": "SO202610240003",
                "customer_id": "C1002",
                "product_name": "便携充电宝 20000mAh",
                "category": "配件",
                "amount": 199.0,
                "status": "shipped",
                "paid_at": now - timedelta(hours=30),
                "delivered_at": None,
                "carrier": "中通快递",
                "tracking_no": "ZT8827364910",
            },
            {
                "order_no": "SO202610050004",
                "customer_id": "C1002",
                "product_name": "智能扫地机器人 X1",
                "category": "清洁电器",
                "amount": 2499.0,
                "status": "delivered",
                "paid_at": now - timedelta(days=48),
                "delivered_at": now - timedelta(days=45),
                "carrier": "顺丰速运",
                "tracking_no": "SF9988776655",
            },
            {
                "order_no": "SO202610260005",
                "customer_id": "C1001",
                "product_name": "定制刻字运动手环",
                "category": "手环",
                "amount": 299.0,
                "status": "delivered",
                "paid_at": now - timedelta(days=2),
                "delivered_at": now - timedelta(days=1),
                "carrier": "中通快递",
                "tracking_no": "ZT1122334455",
                "is_custom": True,
            },
            {
                "order_no": "SO202610200006",
                "customer_id": "C1002",
                "product_name": "星辰云会员年卡",
                "category": "虚拟服务",
                "amount": 168.0,
                "status": "delivered",
                "paid_at": now - timedelta(days=5),
                "delivered_at": now - timedelta(days=5),
                "is_virtual": True,
            },
            {
                # 标价 329 − 优惠 50 + 运费 8 = 实付 287；无理由退 279，质量退 287
                "order_no": "SO202610270007",
                "customer_id": "C1001",
                "product_name": "星辰运动手环 4",
                "category": "手环",
                "amount": 287.0,
                "goods_amount": 329.0,
                "coupon_amount": 50.0,
                "shipping_fee": 8.0,
                "status": "delivered",
                "paid_at": now - timedelta(days=4),
                "delivered_at": now - timedelta(days=2),
                "carrier": "中通快递",
                "tracking_no": "ZT5566778899",
            },
        ]

        methods = ["微信支付", "支付宝", "招商银行信用卡", "花呗分期"]
        for index, item in enumerate(demo):
            self._execute(
                """
                INSERT INTO orders (order_no, customer_id, product_name, category, amount, status,
                                    paid_at, delivered_at, carrier, tracking_no, is_virtual, is_custom,
                                    goods_amount, coupon_amount, shipping_fee)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["order_no"],
                    item["customer_id"],
                    item["product_name"],
                    item["category"],
                    item["amount"],
                    item["status"],
                    iso(item["paid_at"]),
                    iso(item["delivered_at"]),
                    item.get("carrier"),
                    item.get("tracking_no"),
                    int(item.get("is_virtual", False)),
                    int(item.get("is_custom", False)),
                    float(item.get("goods_amount", item["amount"])),
                    float(item.get("coupon_amount", 0.0)),
                    float(item.get("shipping_fee", 0.0)),
                ),
            )
            self._execute(
                """
                INSERT INTO payments (payment_no, order_no, amount, method, status, paid_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"PAY{100000 + index}",
                    item["order_no"],
                    item["amount"],
                    methods[index % len(methods)],
                    "success",
                    iso(item["paid_at"]),
                ),
            )

        logger.info("演示数据已写入: %s 笔订单", len(demo))
        return True
