"""JWT 认证 —— 自持密钥，部署时通过 JWT_SECRET 环境变量注入"""
import os
from datetime import datetime, timedelta
from hashlib import pbkdf2_hmac
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from .database import get_db
from .models import User

# 生产必须显式注入强密钥：缺失或弱口令直接启动失败（禁止随机回落——重启后已签发令牌会全部失效）
JWT_SECRET = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET or JWT_SECRET.startswith("change-me") or len(JWT_SECRET) < 32:
    raise RuntimeError(
        "JWT_SECRET 未配置或过于弱（要求 ≥32 字符随机值）。"
        "请在部署环境变量（/opt/license/env.file）中设置后重启服务。"
    )
ALGORITHM = "HS256"
EXPIRE_HOURS = 24

security = HTTPBearer(auto_error=False)


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    if salt is None:
        salt = os.urandom(16)
    dk = pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex() + ":" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, _ = stored.split(":", 1)
        return hash_password(password, bytes.fromhex(salt_hex)) == stored
    except Exception:
        return False


def create_token(username: str, role: str = "operator") -> str:
    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=EXPIRE_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效凭证")
    user = db.query(User).filter(User.username == payload["sub"]).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user


def require_roles(*roles: str):
    """角色守卫：P2 权限分角色 —— admin=全部 / operator=运营 / viewer=只读

    用法：Depends(require_roles("admin", "operator"))
    """

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足（需要 {'/'.join(roles)} 角色，当前 {user.role}）",
            )
        return user

    return checker
