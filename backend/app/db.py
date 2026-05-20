"""
数据库持久化层
使用 SQLite 存储用户和 Session 数据，服务重启后数据不丢失
"""

import os
import json
import time
from pathlib import Path
from typing import Optional, List, Dict

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy import select, delete, String, Float, Integer


# 数据库文件路径
DB_DIR = Path(__file__).parent.parent / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_URL = f"sqlite+aiosqlite:///{DB_DIR}/ops_agent.db"

Base = declarative_base()


class UserModel(Base):
    """用户表"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class SessionModel(Base):
    """Session 表"""
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    last_active: Mapped[float] = mapped_column(Float, default=time.time)


# 全局引擎和会话工厂
_engine = None
_session_factory = None


async def get_engine():
    """获取或创建数据库引擎"""
    global _engine
    if _engine is None:
        _engine = create_async_engine(DATABASE_URL, echo=False)
    return _engine


async def get_session_factory():
    """获取或创建会话工厂"""
    global _session_factory
    if _session_factory is None:
        engine = await get_engine()
        _session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return _session_factory


async def init_db():
    """初始化数据库（创建表）"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """关闭数据库连接"""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None


# ===== 用户操作 =====

async def db_user_exists(username: str) -> bool:
    """检查用户是否存在"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(select(UserModel).where(UserModel.username == username))
        return result.scalar_one_or_none() is not None


async def db_create_user(username: str, password_hash: str) -> bool:
    """创建用户，返回是否成功"""
    factory = await get_session_factory()
    async with factory() as session:
        # 先检查是否已存在
        result = await session.execute(select(UserModel).where(UserModel.username == username))
        if result.scalar_one_or_none():
            return False
        
        user = UserModel(username=username, password_hash=password_hash)
        session.add(user)
        await session.commit()
        return True


async def db_get_user(username: str) -> Optional[Dict]:
    """获取用户信息"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(select(UserModel).where(UserModel.username == username))
        user = result.scalar_one_or_none()
        if user:
            return {
                "username": user.username,
                "password_hash": user.password_hash,
                "created_at": user.created_at,
            }
        return None


async def db_get_all_users() -> List[Dict]:
    """获取所有用户"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(select(UserModel))
        users = result.scalars().all()
        return [
            {"username": u.username, "password_hash": u.password_hash, "created_at": u.created_at}
            for u in users
        ]


# ===== Session 操作 =====

async def db_create_session(sid: str, username: str) -> None:
    """创建 Session"""
    factory = await get_session_factory()
    async with factory() as session:
        # 删除同用户的旧 session（单点登录）
        await session.execute(delete(SessionModel).where(SessionModel.username == username))
        
        s = SessionModel(sid=sid, username=username)
        session.add(s)
        await session.commit()


async def db_get_session(sid: str) -> Optional[Dict]:
    """获取 Session 信息"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(select(SessionModel).where(SessionModel.sid == sid))
        s = result.scalar_one_or_none()
        if s:
            return {
                "sid": s.sid,
                "username": s.username,
                "created_at": s.created_at,
                "last_active": s.last_active,
            }
        return None


async def db_update_session_last_active(sid: str) -> None:
    """更新 Session 最后活跃时间"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(select(SessionModel).where(SessionModel.sid == sid))
        s = result.scalar_one_or_none()
        if s:
            s.last_active = time.time()
            await session.commit()


async def db_delete_session(sid: str) -> None:
    """删除 Session"""
    factory = await get_session_factory()
    async with factory() as session:
        await session.execute(delete(SessionModel).where(SessionModel.sid == sid))
        await session.commit()


async def db_delete_expired_sessions(ttl: int) -> int:
    """删除过期的 Session，返回删除数量"""
    cutoff = time.time() - ttl
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(
            delete(SessionModel).where(SessionModel.last_active < cutoff)
        )
        await session.commit()
        return result.rowcount
