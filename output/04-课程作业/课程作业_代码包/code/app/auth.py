"""
鉴权模块：JWT 身份认证 + 应用层 RBAC。

【设计取舍】
为什么用 HS256 手写而不是引入 python-jose / python-jose[cryptography]？
  本项目只需要签发/校验自己的 token，没有三方 token 互操作需求。
  纯标准库实现（hmac + base64 + json）零依赖，代码 60 行，审计成本低。
  ⚠️ 若后续要接入统一身份中心（OIDC），必须换成标准库并校验证书链。

【为什么要两层鉴权】
  应用层（本文件）：决定"这个请求能不能进入业务逻辑"，给出友好报错。
  数据库层（GRANT）：即便应用层有 bug 绕过判断，数据库也会拒绝——
    例如传承人角色被授予了 UPDATE asset 的列权限，但**不含 right_level 列**，
    所以即使代码误写 `UPDATE asset SET right_level=...`，数据库也会报
    "permission denied for table asset"。
  两层叠加 = 纵深防御。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import demo
from app.config import settings
from app.db import read_one

bearer_scheme = HTTPBearer(auto_error=False)


# =============================================================================
#  JWT 签发与校验（HS256）
# =============================================================================
def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def create_token(user_id: int, username: str, role: str) -> str:
    header = {"alg": settings.JWT_ALG, "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + settings.JWT_EXPIRE_MINUTES * 60,
    }
    signing_input = (
        _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    )
    sig = hmac.new(settings.JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64url_encode(sig)


def decode_token(token: str) -> dict:
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "令牌格式非法")

    signing_input = f"{head_b64}.{body_b64}"
    expected = hmac.new(settings.JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    # 用 compare_digest 而不是 ==，抵御时序攻击
    if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "令牌签名校验失败")

    try:
        payload = json.loads(_b64url_decode(body_b64))
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "令牌负载解析失败")

    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "令牌已过期，请重新登录")
    return payload


# =============================================================================
#  当前用户
# =============================================================================
@dataclass
class CurrentUser:
    user_id: int
    username: str
    display_name: str
    role: str          # heritor / expert / admin
    org_name: str | None

    @property
    def is_expert(self) -> bool:
        return self.role == "expert"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if cred is None or not cred.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少访问令牌")
    payload = decode_token(cred.credentials)

    # 每次请求回查用户，保证「用户被禁用后旧 token 立即失效」
    uid = int(payload["sub"])
    if demo.is_enabled():
        u = demo.STORE.users.get(uid)
        row = None if u is None else {
            "user_id": u["user_id"], "username": u["username"],
            "display_name": u["display_name"], "role": u["role"],
            "org_name": u["org_name"], "is_active": u["is_active"],
        }
    else:
        row = read_one(
            "SELECT user_id, username, display_name, role, org_name, is_active "
            "FROM app_user WHERE user_id = %s",
            (uid,),
        )
    if row is None or not row["is_active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在或已停用")
    return CurrentUser(
        user_id=row["user_id"],
        username=row["username"],
        display_name=row["display_name"],
        role=row["role"],
        org_name=row["org_name"],
    )


def require_roles(*roles: str):
    """
    角色守卫工厂：
        @router.post(..., dependencies=[Depends(require_roles("expert", "admin"))])
    """
    allowed = set(roles)

    def _guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"当前角色 {user.role} 无权执行该操作，需要角色之一：{sorted(allowed)}",
            )
        return user

    return _guard


def client_hint(request: Request) -> str:
    """记录调用来源，写进审计日志的备注里（演示环境用，生产需处理代理头）。"""
    return request.client.host if request.client else "unknown"
