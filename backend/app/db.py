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
from sqlalchemy import exc as sa_exc


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
    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)  # user / admin
    phone: Mapped[Optional[str]] = mapped_column(String(20), unique=True, nullable=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True, index=True)
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
    """初始化数据库（创建表，并处理列迁移）"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # 迁移：为已存在的 users 表添加新列（SQLite 兼容）
    from sqlalchemy import text
    async with engine.begin() as conn:
        for col_def in [
            "ALTER TABLE users ADD COLUMN role VARCHAR(16) DEFAULT 'user' NOT NULL",
            "ALTER TABLE users ADD COLUMN phone VARCHAR(20)",
            "ALTER TABLE users ADD COLUMN email VARCHAR(128)",
        ]:
            try:
                await conn.execute(text(col_def))
            except Exception:
                # 列已存在或其他错误，忽略
                pass


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


async def db_create_user(username: str, password_hash: str, role: str = "user",
                           phone: Optional[str] = None, email: Optional[str] = None) -> bool:
    """创建用户，返回是否成功"""
    factory = await get_session_factory()
    async with factory() as session:
        # 先检查是否已存在
        result = await session.execute(select(UserModel).where(UserModel.username == username))
        if result.scalar_one_or_none():
            return False
        
        # 检查 phone/email 是否已被占用
        if phone:
            result = await session.execute(select(UserModel).where(UserModel.phone == phone))
            if result.scalar_one_or_none():
                return False
        if email:
            result = await session.execute(select(UserModel).where(UserModel.email == email))
            if result.scalar_one_or_none():
                return False
        
        user = UserModel(username=username, password_hash=password_hash, role=role, phone=phone, email=email)
        session.add(user)
        try:
            await session.commit()
        except sa_exc.IntegrityError:
            # 并发创建或 UNIQUE 约束冲突（TOCTOU 兜底）
            await session.rollback()
            return False
        return True


async def db_get_user(username: str) -> Optional[Dict]:
    """获取用户信息（通过用户名）"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(select(UserModel).where(UserModel.username == username))
        user = result.scalar_one_or_none()
        if user:
            return {
                "username": user.username,
                "password_hash": user.password_hash,
                "role": user.role,
                "phone": user.phone,
                "email": user.email,
                "created_at": user.created_at,
            }
        return None


async def db_get_user_by_phone(phone: str) -> Optional[Dict]:
    """通过手机号获取用户"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(select(UserModel).where(UserModel.phone == phone))
        user = result.scalar_one_or_none()
        if user:
            return {
                "username": user.username,
                "password_hash": user.password_hash,
                "role": user.role,
                "phone": user.phone,
                "email": user.email,
                "created_at": user.created_at,
            }
        return None


async def db_get_user_by_email(email: str) -> Optional[Dict]:
    """通过邮箱获取用户"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(select(UserModel).where(UserModel.email == email))
        user = result.scalar_one_or_none()
        if user:
            return {
                "username": user.username,
                "password_hash": user.password_hash,
                "role": user.role,
                "phone": user.phone,
                "email": user.email,
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
            {"username": u.username, "password_hash": u.password_hash, "role": u.role,
             "phone": u.phone, "email": u.email, "created_at": u.created_at}
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


async def db_update_user_password(username: str, password_hash: str) -> bool:
    """更新用户密码"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(select(UserModel).where(UserModel.username == username))
        user = result.scalar_one_or_none()
        if not user:
            return False
        user.password_hash = password_hash
        await session.commit()
        return True


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


# ===== 聊天记录表 =====

class ChatSessionModel(Base):
    """聊天会话表"""
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    last_active: Mapped[float] = mapped_column(Float, default=time.time)


class ChatMessageModel(Base):
    """聊天消息表"""
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[float] = mapped_column(Float, default=time.time)


# ===== 聊天会话 CRUD =====

async def db_create_chat_session(session_id: str, username: str, title: str = None) -> None:
    """创建聊天会话"""
    factory = await get_session_factory()
    async with factory() as session:
        s = ChatSessionModel(
            session_id=session_id,
            username=username,
            title=title or session_id,
            created_at=time.time(),
            last_active=time.time(),
        )
        session.add(s)
        await session.commit()


async def db_get_chat_session(session_id: str) -> Optional[Dict]:
    """获取聊天会话信息"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(ChatSessionModel).where(ChatSessionModel.session_id == session_id)
        )
        s = result.scalar_one_or_none()
        if s:
            return {
                "session_id": s.session_id,
                "username": s.username,
                "title": s.title,
                "created_at": s.created_at,
                "last_active": s.last_active,
            }
        return None


async def db_update_chat_session_last_active(session_id: str, title: str = None) -> None:
    """更新聊天会话最后活跃时间和标题"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(ChatSessionModel).where(ChatSessionModel.session_id == session_id)
        )
        s = result.scalar_one_or_none()
        if s:
            s.last_active = time.time()
            if title:
                s.title = title
            await session.commit()


async def db_delete_chat_session(session_id: str) -> None:
    """删除聊天会话及关联消息"""
    factory = await get_session_factory()
    async with factory() as session:
        await session.execute(
            delete(ChatMessageModel).where(ChatMessageModel.session_id == session_id)
        )
        await session.execute(
            delete(ChatSessionModel).where(ChatSessionModel.session_id == session_id)
        )
        await session.commit()


async def db_list_chat_sessions(
    username: str,
    limit: int = 50,
    offset: int = 0
) -> List[Dict]:
    """获取用户的聊天会话列表（按最后活跃时间倒序）"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(ChatSessionModel)
            .where(ChatSessionModel.username == username)
            .order_by(ChatSessionModel.last_active.desc())
            .limit(limit)
            .offset(offset)
        )
        sessions = result.scalars().all()
        return [
            {
                "session_id": s.session_id,
                "title": s.title,
                "created_at": s.created_at,
                "last_active": s.last_active,
            }
            for s in sessions
        ]


async def db_add_chat_message(session_id: str, role: str, content: str, timestamp: float = None) -> None:
    """添加聊天消息"""
    factory = await get_session_factory()
    async with factory() as session:
        msg = ChatMessageModel(
            session_id=session_id,
            role=role,
            content=content,
            timestamp=timestamp or time.time(),
        )
        session.add(msg)
        await session.commit()


async def db_get_chat_messages(session_id: str, limit: int = 200) -> List[Dict]:
    """获取聊天消息历史"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(ChatMessageModel)
            .where(ChatMessageModel.session_id == session_id)
            .order_by(ChatMessageModel.timestamp.asc())
            .limit(limit)
        )
        messages = result.scalars().all()
        return [
            {
                "role": m.role,
                "content": m.content,
                "timestamp": m.timestamp,
            }
            for m in messages
        ]


async def db_delete_expired_chat_sessions(ttl: int) -> int:
    """删除过期的聊天会话，返回删除数量"""
    cutoff = time.time() - ttl
    factory = await get_session_factory()
    async with factory() as session:
        # 先删除关联消息
        expired_ids = await session.execute(
            select(ChatSessionModel.session_id).where(ChatSessionModel.last_active < cutoff)
        )
        ids = [row[0] for row in expired_ids.all()]
        if ids:
            await session.execute(
                delete(ChatMessageModel).where(ChatMessageModel.session_id.in_(ids))
            )
            await session.execute(
                delete(ChatSessionModel).where(ChatSessionModel.session_id.in_(ids))
            )
            await session.commit()
        return len(ids)


# ===== 权限申请表 =====

class PrivilegeRequestModel(Base):
    """root 权限申请表"""
    __tablename__ = "privilege_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    command: Mapped[str] = mapped_column(String(500), nullable=False)  # 展示用命令描述
    tool_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # 实际申请提权的工具名
    arguments: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)  # 工具参数 JSON
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending / approved / rejected / expired
    requested_by: Mapped[str] = mapped_column(String(32), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(32), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    approved_at: Mapped[float] = mapped_column(Float, nullable=True)
    expires_at: Mapped[float] = mapped_column(Float, nullable=True)  # 审批后有效期


# ===== 权限申请 CRUD =====

async def db_create_privilege_request(
    request_id: str,
    session_id: str,
    command: str,
    reason: str,
    requested_by: str,
    tool_name: Optional[str] = None,
    arguments: Optional[Dict] = None,
) -> None:
    """创建权限申请"""
    import json
    factory = await get_session_factory()
    async with factory() as session:
        req = PrivilegeRequestModel(
            request_id=request_id,
            session_id=session_id,
            command=command,
            tool_name=tool_name,
            arguments=json.dumps(arguments, ensure_ascii=False) if arguments else None,
            reason=reason,
            status="pending",
            requested_by=requested_by,
            created_at=time.time(),
        )
        session.add(req)
        await session.commit()


async def db_get_privilege_request(request_id: str) -> Optional[Dict]:
    """获取权限申请"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PrivilegeRequestModel).where(PrivilegeRequestModel.request_id == request_id)
        )
        r = result.scalar_one_or_none()
        if r:
            return {
                "request_id": r.request_id,
                "session_id": r.session_id,
                "command": r.command,
                "tool_name": r.tool_name,
                "arguments": r.arguments,
                "reason": r.reason,
                "status": r.status,
                "requested_by": r.requested_by,
                "approved_by": r.approved_by,
                "created_at": r.created_at,
                "approved_at": r.approved_at,
                "expires_at": r.expires_at,
            }
        return None


async def db_approve_privilege_request(
    request_id: str, approved_by: str, ttl_sec: int = 300
) -> bool:
    """审批通过权限申请，返回是否成功"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PrivilegeRequestModel).where(
                PrivilegeRequestModel.request_id == request_id,
                PrivilegeRequestModel.status == "pending"
            )
        )
        r = result.scalar_one_or_none()
        if not r:
            return False
        now = time.time()
        r.status = "approved"
        r.approved_by = approved_by
        r.approved_at = now
        r.expires_at = now + ttl_sec
        await session.commit()
        return True


async def db_reject_privilege_request(request_id: str, approved_by: str) -> bool:
    """拒绝权限申请"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PrivilegeRequestModel).where(
                PrivilegeRequestModel.request_id == request_id,
                PrivilegeRequestModel.status == "pending"
            )
        )
        r = result.scalar_one_or_none()
        if not r:
            return False
        r.status = "rejected"
        r.approved_by = approved_by
        r.approved_at = time.time()
        await session.commit()
        return True


async def db_list_privilege_requests(
    status: Optional[str] = None,
    requested_by: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> List[Dict]:
    """获取权限申请列表"""
    factory = await get_session_factory()
    async with factory() as session:
        stmt = select(PrivilegeRequestModel).order_by(PrivilegeRequestModel.created_at.desc())
        if status:
            stmt = stmt.where(PrivilegeRequestModel.status == status)
        if requested_by:
            stmt = stmt.where(PrivilegeRequestModel.requested_by == requested_by)
        stmt = stmt.limit(limit).offset(offset)
        result = await session.execute(stmt)
        reqs = result.scalars().all()
        return [
            {
                "request_id": r.request_id,
                "session_id": r.session_id,
                "command": r.command,
                "reason": r.reason,
                "status": r.status,
                "requested_by": r.requested_by,
                "approved_by": r.approved_by,
                "created_at": r.created_at,
                "approved_at": r.approved_at,
            }
            for r in reqs
        ]


async def db_cleanup_expired_privilege_requests() -> int:
    """清理过期的已审批权限申请，返回清理数量"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PrivilegeRequestModel).where(
                PrivilegeRequestModel.status == "approved",
                PrivilegeRequestModel.expires_at < time.time()
            )
        )
        expired = result.scalars().all()
        count = 0
        for r in expired:
            r.status = "expired"
            count += 1
        if count:
            await session.commit()
        return count


async def db_get_approved_privilege_request_by_session(
    session_id: str, requested_by: str
) -> Optional[Dict]:
    """获取指定会话下已审批且未过期的权限申请（按创建时间倒序取最新一条）"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PrivilegeRequestModel).where(
                PrivilegeRequestModel.session_id == session_id,
                PrivilegeRequestModel.requested_by == requested_by,
                PrivilegeRequestModel.status == "approved",
                PrivilegeRequestModel.expires_at >= time.time(),
            ).order_by(PrivilegeRequestModel.approved_at.desc())
        )
        r = result.scalar_one_or_none()
        if r:
            return {
                "request_id": r.request_id,
                "session_id": r.session_id,
                "command": r.command,
                "reason": r.reason,
                "status": r.status,
                "requested_by": r.requested_by,
                "approved_by": r.approved_by,
                "created_at": r.created_at,
                "approved_at": r.approved_at,
                "expires_at": r.expires_at,
            }
        return None


async def db_consume_privilege_request(request_id: str) -> bool:
    """将权限申请标记为已使用"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(PrivilegeRequestModel).where(
                PrivilegeRequestModel.request_id == request_id,
                PrivilegeRequestModel.status == "approved",
            )
        )
        r = result.scalar_one_or_none()
        if not r:
            return False
        r.status = "consumed"
        r.expires_at = time.time()
        await session.commit()
        return True


# ===== 验证码表 =====

class VerificationCodeModel(Base):
    """验证码表（邮箱/手机）"""
    __tablename__ = "verification_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target: Mapped[str] = mapped_column(String(128), nullable=False, index=True)  # 邮箱或手机号
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)  # email / phone
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)  # register / login / reset_password
    used: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)


# ===== 验证码 CRUD =====

async def db_create_verification_code(target: str, target_type: str, code: str,
                                       purpose: str, expires_at: float) -> None:
    """创建验证码记录"""
    factory = await get_session_factory()
    async with factory() as session:
        vc = VerificationCodeModel(
            target=target,
            target_type=target_type,
            code=code,
            purpose=purpose,
            used=False,
            created_at=time.time(),
            expires_at=expires_at,
        )
        session.add(vc)
        await session.commit()


async def db_get_latest_verification_code(target: str, target_type: str,
                                           purpose: str) -> Optional[Dict]:
    """获取指定目标的最新未使用验证码"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(VerificationCodeModel)
            .where(
                VerificationCodeModel.target == target,
                VerificationCodeModel.target_type == target_type,
                VerificationCodeModel.purpose == purpose,
                VerificationCodeModel.used == False,
            )
            .order_by(VerificationCodeModel.created_at.desc())
            .limit(1)
        )
        vc = result.scalar_one_or_none()
        if vc:
            return {
                "id": vc.id,
                "target": vc.target,
                "target_type": vc.target_type,
                "code": vc.code,
                "purpose": vc.purpose,
                "used": vc.used,
                "created_at": vc.created_at,
                "expires_at": vc.expires_at,
            }
        return None


async def db_mark_verification_code_used(code_id: int) -> bool:
    """标记验证码为已使用"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(VerificationCodeModel).where(VerificationCodeModel.id == code_id)
        )
        vc = result.scalar_one_or_none()
        if not vc:
            return False
        vc.used = True
        await session.commit()
        return True


async def db_cleanup_expired_verification_codes() -> int:
    """清理过期的验证码记录，返回删除数量"""
    factory = await get_session_factory()
    async with factory() as session:
        result = await session.execute(
            delete(VerificationCodeModel).where(VerificationCodeModel.expires_at < time.time())
        )
        await session.commit()
        return result.rowcount


async def test_db_connection() -> bool:
    """测试数据库连接是否正常"""
    try:
        factory = await get_session_factory()
        async with factory() as session:
            # 执行一个简单的查询来验证连接
            await session.execute(select(1))
            return True
    except Exception:
        return False
