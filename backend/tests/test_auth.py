"""JWT 与 RBAC 的单元测试：不光测"能登录"，更要测"伪造/过期/越权都不行"。"""

from __future__ import annotations

import base64
import json
import time

import pytest

from app.core.config import Settings, get_settings
from app.domain.security import (
    ROLE_PERMISSIONS,
    CurrentUser,
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


# ── 口令 ────────────────────────────────────────────────────────────────────
def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("demo1234")
    second = hash_password("demo1234")

    assert first != second, "每次都要用新盐，不能出现相同哈希"
    assert first.startswith("pbkdf2_sha256$")
    assert "demo1234" not in first
    assert verify_password("demo1234", first) is True
    assert verify_password("wrong", first) is False


@pytest.mark.parametrize("broken", ["", "plaintext", "pbkdf2_sha256$abc$def$ghi", "md5$1$aa$bb"])
def test_malformed_hash_does_not_crash(broken: str) -> None:
    assert verify_password("demo1234", broken) is False


# ── 令牌 ────────────────────────────────────────────────────────────────────
def test_token_roundtrip_carries_claims() -> None:
    token, expires_at = create_access_token(
        subject="alice", role="customer", display_name="Alice", customer_id="C1001"
    )
    payload = decode_access_token(token)

    assert payload["sub"] == "alice"
    assert payload["role"] == "customer"
    assert payload["cid"] == "C1001"
    assert payload["iss"] == get_settings().jwt_issuer
    assert payload["exp"] == expires_at
    assert payload["jti"], "每个令牌都要有唯一 jti，便于审计和吊销"


def test_expired_token_is_rejected() -> None:
    token, _ = create_access_token(subject="alice", role="customer", expires_minutes=-2)
    with pytest.raises(TokenError, match="过期"):
        decode_access_token(token)


def test_tampered_payload_is_rejected() -> None:
    token, _ = create_access_token(subject="alice", role="customer")
    header, payload, signature = token.split(".")

    # 把 role 改成 admin，签名不变 → 必须被拒
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    decoded["role"] = "admin"
    forged_payload = base64.urlsafe_b64encode(
        json.dumps(decoded, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()

    with pytest.raises(TokenError):
        decode_access_token(f"{header}.{forged_payload}.{signature}")


def test_alg_none_token_is_rejected() -> None:
    settings = get_settings()
    now = int(time.time())

    def b64(raw: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(raw).encode()).rstrip(b"=").decode()

    header = b64({"alg": "none", "typ": "JWT"})
    body = b64({"iss": settings.jwt_issuer, "sub": "admin", "role": "admin", "iat": now, "exp": now + 600})
    with pytest.raises(TokenError):
        decode_access_token(f"{header}.{body}.")


def test_token_from_other_issuer_is_rejected() -> None:
    other = Settings(jwt_secret=get_settings().jwt_secret, jwt_issuer="another-service")
    token, _ = create_access_token(subject="alice", role="customer", settings=other)
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_token_signed_with_other_secret_is_rejected() -> None:
    other = Settings(jwt_secret="a-completely-different-secret-32bytes", jwt_issuer=get_settings().jwt_issuer)
    token, _ = create_access_token(subject="alice", role="customer", settings=other)
    with pytest.raises(TokenError):
        decode_access_token(token)


# ── 权限矩阵 ────────────────────────────────────────────────────────────────
def test_permission_matrix() -> None:
    customer = CurrentUser(username="alice", role="customer", display_name="Alice", customer_id="C1001")
    agent = CurrentUser(username="agent01", role="agent", display_name="小艾")
    admin = CurrentUser(username="admin", role="admin", display_name="管理员")

    assert customer.has("chat:own") is True
    assert customer.has("reviews:read") is False
    assert customer.has("reviews:decide") is False

    assert agent.has("reviews:decide") is True
    assert agent.has("knowledge:search") is True
    assert agent.has("knowledge:write") is False
    assert agent.has("users:read") is False

    assert admin.has("knowledge:write") is True
    assert admin.has("users:read") is True


def test_unknown_role_has_no_permission() -> None:
    stranger = CurrentUser(username="x", role="guest", display_name="")
    assert stranger.has("chat:own") is False


def test_all_roles_are_declared() -> None:
    assert set(ROLE_PERMISSIONS) == {"customer", "agent", "admin"}
