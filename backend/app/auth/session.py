"""Session 认证管理模块

生产环境建议使用 Redis 替代内存字典，以支持多实例横向扩展。
"""

import os
import secrets
import time
import hashlib
from typing import Dict, Optional
from fastapi import Request, HTTPException


# 保留开发环境 Token 作为降级兼容
DEV_API_TOKEN = os.environ.get(
    "OPS_API_TOKEN", "kylin-ops-dev-token-CHANGE-IN-PROD"
)


class SessionAuth:
    """基于内存的 Session 管理器"""

    # Session 有效期：8 小时
    SESSION_TTL = 3600 * 8

    def __init__(self):
        # sid -> {username, created_at, last_active}
        self._sessions: Dict[str, dict] = {}
        # username -> {password_hash, created_at}
        self._users: Dict[str, dict] = {}
        # 初始化默认管理员账号
        self._init_default_user()

    def _init_default_user(self):
        """初始化默认管理员账号"""
        env_user = os.environ.get("OPS_ADMIN_USER", "opsadmin")
        env_pass = os.environ.get("OPS_ADMIN_PASS", "KylinOps@2024")
        self._users[env_user] = {
            "password_hash": hashlib.sha256(env_pass.encode()).hexdigest(),
            "created_at": time.time(),
        }

    # ------------------------------------------------------------------
    # 公有接口
    # ------------------------------------------------------------------

    def create(self, username: str) -> str:
        """创建新 Session，返回 Session ID"""
        sid = secrets.token_urlsafe(32)
        now = time.time()
        self._sessions[sid] = {
            "username": username,
            "created_at": now,
            "last_active": now,
        }
        return sid

    def verify(self, request: Request) -> str:
        """
        校验请求身份。
        优先级：
          1. Cookie 中的 ops_session
          2. Header 中的 Dev Token（仅开发/测试环境）
        返回：用户名 或 "dev_token"
        """
        # 1) Cookie 优先
        sid = request.cookies.get("ops_session", "")
        if sid:
            session = self._sessions.get(sid)
            if session and (time.time() - session["last_active"] <= self.SESSION_TTL):
                session["last_active"] = time.time()
                return session["username"]

        # 2) 降级：开发 Token（避免完全锁死无 Cookie 的调用方）
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth.replace("Bearer ", "").strip()
            if token == DEV_API_TOKEN:
                return "dev_token"

        raise HTTPException(status_code=401, detail="未登录或会话已过期")

    def destroy(self, sid: str):
        """销毁指定 Session"""
        self._sessions.pop(sid, None)

    def get_session_info(self, sid: str) -> Optional[dict]:
        """获取 Session 信息（仅内部使用）"""
        return self._sessions.get(sid)

    # ------------------------------------------------------------------
    # 用户注册 / 密码校验（简易本地账号，生产环境请对接 LDAP/统一认证）
    # ------------------------------------------------------------------

    def user_exists(self, username: str) -> bool:
        """检查用户名是否已存在"""
        return username in self._users

    def register(self, username: str, password: str) -> bool:
        """
        注册新用户。
        返回 True 表示注册成功，False 表示用户名已存在。
        """
        if self.user_exists(username):
            return False
        self._users[username] = {
            "password_hash": hashlib.sha256(password.encode()).hexdigest(),
            "created_at": time.time(),
        }
        return True

    def verify_password(self, username: str, password: str) -> bool:
        """
        验证用户密码。
        支持注册的用户和默认管理员账号。
        """
        user = self._users.get(username)
        if not user:
            return False
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        return secrets.compare_digest(password_hash, user["password_hash"])


# 全局单例
session_auth = SessionAuth()
