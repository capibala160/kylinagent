"""Session 认证管理模块

使用 SQLite 持久化存储用户和 Session 数据，服务重启后数据不丢失。
生产环境建议迁移到 PostgreSQL/MySQL 以支持多实例横向扩展。
"""

import logging
import os
import secrets
import time
import bcrypt
from typing import Dict, Optional
from fastapi import Request, HTTPException

logger = logging.getLogger(__name__)

from ..db import (
    init_db,
    db_user_exists,
    db_create_user,
    db_get_user,
    db_get_user_by_phone,
    db_get_user_by_email,
    db_get_all_users,
    db_create_session,
    db_get_session,
    db_update_session_last_active,
    db_delete_session,
    db_delete_expired_sessions,
    db_update_user_password,
)


# 开发环境 Token：必须显式设置环境变量 OPS_API_TOKEN 才能启用
# 未设置时 Dev Token 认证完全禁用，防止默认值被利用绕过认证
_DEV_API_TOKEN_RAW = os.environ.get("OPS_API_TOKEN", "")
DEV_API_TOKEN = _DEV_API_TOKEN_RAW if _DEV_API_TOKEN_RAW.strip() else None


class SessionAuth:
    """基于 SQLite 的 Session 管理器"""

    # Session 有效期：24 小时（与 cookie max_age 保持一致）
    SESSION_TTL = 3600 * 24

    def __init__(self):
        # 内存缓存（减少数据库查询）
        self._session_cache: Dict[str, dict] = {}
        self._user_cache: Dict[str, dict] = {}
        self._db_initialized = False

    async def _ensure_db(self):
        """确保数据库已初始化"""
        if not self._db_initialized:
            await init_db()
            self._db_initialized = True
            # 加载所有用户到内存缓存
            users = await db_get_all_users()
            for u in users:
                self._user_cache[u["username"]] = u

    # ------------------------------------------------------------------
    # 密码哈希
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_password(password: str) -> str:
        """使用 bcrypt 对密码进行哈希"""
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')

    @staticmethod
    def _verify_password_hash(password: str, hashed: str) -> bool:
        """使用 bcrypt 验证密码"""
        try:
            return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 公有接口
    # ------------------------------------------------------------------

    async def create(self, username: str) -> str:
        """创建新 Session，返回 Session ID"""
        await self._ensure_db()
        sid = secrets.token_urlsafe(32)
        await db_create_session(sid, username)
        self._session_cache[sid] = {
            "username": username,
            "created_at": time.time(),
            "last_active": time.time(),
        }
        return sid

    async def verify(self, request: Request) -> str:
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
            # 先查内存缓存
            cached = self._session_cache.get(sid)
            if cached and (time.time() - cached["last_active"] <= self.SESSION_TTL):
                cached["last_active"] = time.time()
                await db_update_session_last_active(sid)
                return cached["username"]

            # 缓存未命中，查数据库
            await self._ensure_db()
            session = await db_get_session(sid)
            if session and (time.time() - session["last_active"] <= self.SESSION_TTL):
                self._session_cache[sid] = session
                await db_update_session_last_active(sid)
                return session["username"]

        # 2) 降级：开发 Token（仅当显式配置 OPS_API_TOKEN 环境变量时启用）
        if DEV_API_TOKEN is not None:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth.replace("Bearer ", "").strip()
                if token == DEV_API_TOKEN:
                    return "dev_token"

        raise HTTPException(status_code=401, detail="未登录或会话已过期")

    async def destroy(self, sid: str):
        """销毁指定 Session"""
        await self._ensure_db()
        self._session_cache.pop(sid, None)
        await db_delete_session(sid)

    async def get_session_info(self, sid: str) -> Optional[dict]:
        """获取 Session 信息（仅内部使用）"""
        await self._ensure_db()
        # 先查缓存
        cached = self._session_cache.get(sid)
        if cached:
            return cached
        # 再查数据库
        session = await db_get_session(sid)
        if session:
            self._session_cache[sid] = session
        return session

    async def cleanup_expired(self) -> int:
        """清理过期 Session，返回清理数量"""
        await self._ensure_db()
        count = await db_delete_expired_sessions(self.SESSION_TTL)
        # 同步清理内存缓存
        now = time.time()
        expired = [sid for sid, s in self._session_cache.items()
                   if now - s["last_active"] > self.SESSION_TTL]
        for sid in expired:
            self._session_cache.pop(sid, None)
        return count

    # ------------------------------------------------------------------
    # 用户注册 / 密码校验
    # ------------------------------------------------------------------

    async def user_exists(self, username: str) -> bool:
        """检查用户名是否已存在"""
        await self._ensure_db()
        # 先查缓存
        if username in self._user_cache:
            return True
        # 再查数据库
        return await db_user_exists(username)

    async def register(self, username: str, password: str,
                        phone: Optional[str] = None, email: Optional[str] = None) -> bool:
        """
        注册新用户。
        返回 True 表示注册成功，False 表示用户名/手机/邮箱已存在。
        """
        await self._ensure_db()
        if await db_user_exists(username):
            return False
        password_hash = self._hash_password(password)
        success = await db_create_user(username, password_hash, role="user", phone=phone, email=email)
        if success:
            self._user_cache[username] = {
                "username": username,
                "password_hash": password_hash,
                "role": "user",
                "phone": phone,
                "email": email,
                "created_at": time.time(),
            }
        return success

    async def get_user_by_phone(self, phone: str) -> Optional[dict]:
        """通过手机号查找用户"""
        await self._ensure_db()
        return await db_get_user_by_phone(phone)

    async def get_user_by_email(self, email: str) -> Optional[dict]:
        """通过邮箱查找用户"""
        await self._ensure_db()
        return await db_get_user_by_email(email)

    async def reset_password(self, username: str, new_password: str) -> bool:
        """重置用户密码"""
        await self._ensure_db()
        password_hash = self._hash_password(new_password)
        success = await db_update_user_password(username, password_hash)
        if success and username in self._user_cache:
            self._user_cache[username]["password_hash"] = password_hash
        return success

    async def verify_password(self, username: str, password: str) -> bool:
        """
        验证用户密码。
        支持注册的用户和默认管理员账号。
        """
        await self._ensure_db()
        # 先查缓存
        cached = self._user_cache.get(username)
        if cached:
            return self._verify_password_hash(password, cached["password_hash"])
        # 再查数据库
        user = await db_get_user(username)
        if not user:
            return False
        # 加载到缓存
        self._user_cache[username] = user
        return self._verify_password_hash(password, user["password_hash"])

    async def init_default_user(self):
        """初始化默认管理员账号（如果不存在）

        安全要求：必须通过环境变量 OPS_ADMIN_PASS 设置管理员密码。
        未配置时不会创建默认管理员，避免使用硬编码弱口令被攻击者利用。
        """
        await self._ensure_db()
        env_user = os.environ.get("OPS_ADMIN_USER", "opsadmin")
        env_pass = os.environ.get("OPS_ADMIN_PASS", "")

        if not env_pass:
            logger.warning(
                "未配置 OPS_ADMIN_PASS 环境变量，跳过创建默认管理员账号。"
                "请在首次部署前通过环境变量设置强密码。"
            )
            return

        if not await db_user_exists(env_user):
            password_hash = self._hash_password(env_pass)
            await db_create_user(env_user, password_hash, role="admin")
            self._user_cache[env_user] = {
                "username": env_user,
                "password_hash": password_hash,
                "role": "admin",
                "phone": None,
                "email": None,
                "created_at": time.time(),
            }


# 全局单例工厂（支持测试替换）
_session_auth_instance: Optional[SessionAuth] = None


def get_session_auth() -> SessionAuth:
    """获取 SessionAuth 实例（工厂模式，支持测试替换）"""
    global _session_auth_instance
    if _session_auth_instance is None:
        _session_auth_instance = SessionAuth()
    return _session_auth_instance


def set_session_auth(instance: SessionAuth):
    """设置自定义 SessionAuth 实例（主要用于单元测试）"""
    global _session_auth_instance
    _session_auth_instance = instance


def reset_session_auth():
    """重置 SessionAuth 实例"""
    global _session_auth_instance
    _session_auth_instance = None


# 兼容旧代码：直接导入 session_auth 仍然可用
session_auth = get_session_auth()
