"""FastAPI 鉴权依赖：从 Authorization: Bearer 解析当前用户，并按权限校验。"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.domain.security import CurrentUser, TokenError, decode_access_token, user_from_payload

bearer = HTTPBearer(auto_error=False, description="Bearer <JWT>，从 /api/auth/login 获取")

_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> CurrentUser | None:
    """有令牌就解析，没有就返回 None（用于"可选登录"的接口）。"""
    if credentials is None:
        return None
    try:
        payload = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers=_UNAUTHORIZED,
        ) from exc
    return user_from_payload(payload)


def require_user(user: CurrentUser | None = Depends(get_current_user)) -> CurrentUser:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要登录",
            headers=_UNAUTHORIZED,
        )
    return user


def require_permission(permission: str):
    """按权限点校验，例如 reviews:decide。"""

    def checker(user: CurrentUser = Depends(require_user)) -> CurrentUser:
        if not user.has(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：需要 {permission}（当前角色 {user.role}）",
            )
        return user

    return checker
