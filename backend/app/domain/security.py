"""认证与授权的基础设施：口令哈希、JWT 签发/校验、角色权限表。

签名交给 PyJWT，但**校验策略由我们自己钉死**：
1. `algorithms=[HS256]` 白名单 —— 防 `alg=none` 与算法混淆攻击；
2. `issuer` 强校验 + `require=["exp","iat","sub","iss"]` —— 缺字段直接拒；
3. 过期留 30s 时钟漂移容忍，避免多实例时间不同步导致误杀；
4. 口令用 PBKDF2-HMAC-SHA256 + 每用户随机盐，绝不明文或裸 hash 入库。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from dataclasses import dataclass

import jwt

from app.core.config import Settings, get_settings

PBKDF2_ITERATIONS = 210_000
_CLOCK_SKEW_SECONDS = 30

ROLE_PERMISSIONS: dict[str, set[str]] = {
    # 终端客户：只能和自己相关
    "customer": {
        "chat:own",
        "orders:own",
        "tickets:own",
    },
    # 售后审核员：能看待审、能审批（金额受 ABAC 限制）
    "agent": {
        "chat:own",
        "orders:read",
        "tickets:read",
        "reviews:read",
        "reviews:decide",
        "knowledge:read",
        "knowledge:search",
    },
    # 管理员：全量
    "admin": {"*"},
}


class TokenError(Exception):
    """令牌无效/过期/被篡改。"""


@dataclass(frozen=True)
class CurrentUser:
    username: str
    role: str
    display_name: str
    customer_id: str | None = None
    jti: str = ""

    def has(self, permission: str) -> bool:
        granted = ROLE_PERMISSIONS.get(self.role, set())
        return "*" in granted or permission in granted


# ── 口令 ────────────────────────────────────────────────────────────────────
def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False

    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        rounds = int(iterations)
    except ValueError:
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(actual, expected)


# ── JWT ─────────────────────────────────────────────────────────────────────
def create_access_token(
    *,
    subject: str,
    role: str,
    display_name: str = "",
    customer_id: str | None = None,
    settings: Settings | None = None,
    expires_minutes: int | None = None,
) -> tuple[str, int]:
    """返回 (token, exp 时间戳)。"""
    settings = settings or get_settings()
    now = int(time.time())
    expire = now + (expires_minutes or settings.jwt_expire_minutes) * 60

    payload = {
        "iss": settings.jwt_issuer,
        "sub": subject,
        "role": role,
        "name": display_name,
        "cid": customer_id,
        "iat": now,
        "exp": expire,
        "jti": uuid.uuid4().hex,
    }
    # cid 可能为空，去掉空值避免出现 "cid": null 的噪声
    payload = {key: value for key, value in payload.items() if value is not None}

    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expire


def decode_access_token(token: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],          # 白名单，杜绝 alg 混淆
            issuer=settings.jwt_issuer,
            leeway=_CLOCK_SKEW_SECONDS,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("令牌已过期") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("令牌无效或已被篡改") from exc


def user_from_payload(payload: dict) -> CurrentUser:
    return CurrentUser(
        username=str(payload.get("sub", "")),
        role=str(payload.get("role", "customer")),
        display_name=str(payload.get("name", "")),
        customer_id=payload.get("cid"),
        jti=str(payload.get("jti", "")),
    )
