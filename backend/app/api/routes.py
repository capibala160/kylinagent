import os
import uuid
import time
import json
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, Header, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..agent import OpsAgent, SessionManager
from ..audit import AuditLogger
from ..auth.session import session_auth
from ..auth.verification import VerificationManager
from ..auth.email import get_email_service
from ..config import get_config
from ..mcp import MCPServer
from ..mcp.schema import MCPRequest
from sqlalchemy import select
from ..db import (
    db_get_chat_messages,
    db_add_chat_message,
    db_create_chat_session,
    db_get_chat_session,
    db_list_chat_sessions,
    db_delete_chat_session,
    db_update_chat_session_last_active,
    db_create_privilege_request,
    db_get_privilege_request,
    db_approve_privilege_request,
    db_reject_privilege_request,
    db_list_privilege_requests,
    db_get_user,
    db_user_exists,
    db_create_user,
    get_session_factory,
    PrivilegeRequestModel,
)

# 监控路由（子模块）
from .monitor import router as monitor_router

# MCP Server 全局实例（对外暴露标准 MCP JSON-RPC 端点）
_mcp_server: Optional[MCPServer] = None


def get_mcp_server() -> MCPServer:
    """获取 MCP Server 实例（懒加载），确保传入工具注册表和安全护栏"""
    global _mcp_server
    if _mcp_server is None:
        from ..mcp.tools import get_registry
        from ..security import SecurityGuard
        from ..config import get_config
        cfg = get_config()
        _mcp_server = MCPServer(
            tool_registry=get_registry(),
            security_guard=SecurityGuard(cfg.security.model_dump()),
        )
    return _mcp_server

router = APIRouter()
session_manager = SessionManager()
audit_logger = AuditLogger(
    log_dir=get_config().audit.log_dir,
    retention_days=get_config().audit.retention_days
)

# 全局 Agent 实例（支持测试替换和热重启）
_agent: Optional[OpsAgent] = None
_agent_use_mock: bool = False


def get_agent() -> OpsAgent:
    """获取 Agent 实例，支持 mock 模式和测试替换"""
    global _agent
    if _agent is None:
        _agent = OpsAgent(use_mock_llm=_agent_use_mock)
    return _agent


def set_agent_mock(use_mock: bool):
    """设置 Agent 使用 Mock LLM（测试或离线环境）"""
    global _agent_use_mock
    _agent_use_mock = use_mock


def reset_agent():
    """重置 Agent 实例（主要用于单元测试）"""
    global _agent
    if _agent:
        # 异步关闭需要特殊处理，简单场景直接置空
        _agent = None


async def get_current_user(request: Request) -> str:
    """统一认证依赖：优先 Cookie Session，降级支持 Dev Token"""
    return await session_auth.verify(request)


async def get_current_user_with_role(request: Request) -> Dict[str, str]:
    """返回当前用户及其角色"""
    username = await session_auth.verify(request)
    user = await db_get_user(username)
    role = user.get("role", "user") if user else "user"
    return {"username": username, "role": role}


# ========== 请求/响应模型 ==========

class LoginRequest(BaseModel):
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class LoginResponse(BaseModel):
    success: bool
    username: str
    role: str = "user"
    message: str


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, description="用户名")
    password: str = Field(..., min_length=6, max_length=64, description="密码")
    phone: Optional[str] = Field(None, description="手机号（选填）")
    email: Optional[str] = Field(None, description="邮箱（选填）")


class RegisterResponse(BaseModel):
    success: bool
    username: str
    message: str


class SendCodeRequest(BaseModel):
    target: str = Field(..., description="邮箱或手机号")
    target_type: str = Field(..., pattern="^(email|phone)$", description="类型: email 或 phone")
    purpose: str = Field(..., pattern="^(register|login|reset_password)$", description="用途")


class CodeLoginRequest(BaseModel):
    target: str = Field(..., description="邮箱或手机号")
    target_type: str = Field(..., pattern="^(email|phone)$", description="类型")
    code: str = Field(..., min_length=6, max_length=6, description="验证码")


class CodeRegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, description="用户名")
    password: str = Field(..., min_length=6, max_length=64, description="密码")
    target: str = Field(..., description="邮箱或手机号")
    target_type: str = Field(..., pattern="^(email|phone)$", description="类型")
    code: str = Field(..., min_length=6, max_length=6, description="验证码")


class ForgotPasswordRequest(BaseModel):
    target: str = Field(..., description="邮箱或手机号")
    target_type: str = Field(..., pattern="^(email|phone)$", description="类型")


class ResetPasswordRequest(BaseModel):
    target: str = Field(..., description="邮箱或手机号")
    target_type: str = Field(..., pattern="^(email|phone)$", description="类型")
    code: str = Field(..., min_length=6, max_length=6, description="验证码")
    new_password: str = Field(..., min_length=6, max_length=64, description="新密码")


class ChatRequest(BaseModel):
    message: str = Field(..., description="用户输入的消息")
    session_id: Optional[str] = Field(None, description="会话 ID，不传则创建新会话")
    confirmed: bool = Field(False, description="是否已确认执行高危操作")
    elevated: bool = Field(False, description="是否使用已审批的 root 权限执行")


class ChatResponse(BaseModel):
    success: bool
    message: str
    session_id: str
    chain_id: str
    requires_confirm: bool = False
    confirm_reason: Optional[str] = None
    tool_results: Optional[list] = None
    blocked: bool = False


class SessionResponse(BaseModel):
    session_id: str
    message_count: int
    created_at: float
    last_active: float


class AuditQueryRequest(BaseModel):
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    status: Optional[str] = None
    limit: int = 100


# ========== 认证路由 ==========

@router.post("/auth/register", response_model=RegisterResponse)
async def register(request: RegisterRequest):
    """
    用户注册接口。
    注册成功后直接返回，不自动登录（需手动登录）。
    """
    if await session_auth.user_exists(request.username):
        raise HTTPException(status_code=409, detail="用户名已存在")

    success = await session_auth.register(
        request.username, request.password,
        phone=request.phone, email=request.email
    )
    if not success:
        raise HTTPException(status_code=409, detail="注册失败，用户名/手机/邮箱可能已被占用")

    return RegisterResponse(
        success=True,
        username=request.username,
        message="注册成功，请登录"
    )


@router.post("/auth/login", response_model=LoginResponse)
async def login(request: LoginRequest, response: Response):
    """
    用户登录接口。
    成功后在响应中设置 HttpOnly Cookie（ops_session）。
    """
    if not await session_auth.verify_password(request.username, request.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    sid = await session_auth.create(request.username)
    config = get_config()
    response.set_cookie(
        key="ops_session",
        value=sid,
        httponly=True,
        secure=config.auth.cookie_secure,  # 根据配置动态设置（HTTPS 环境设为 True）
        samesite="lax",
        max_age=config.auth.session_ttl,
        path="/",
    )
    user_info = await db_get_user(request.username)
    role = user_info.get("role", "user") if user_info else "user"
    return LoginResponse(
        success=True,
        username=request.username,
        role=role,
        message="登录成功"
    )


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    """用户登出接口，销毁 Session 并清除 Cookie"""
    sid = request.cookies.get("ops_session", "")
    if sid:
        await session_auth.destroy(sid)
    response.delete_cookie(key="ops_session", path="/")
    return {"success": True, "message": "已登出"}


@router.get("/auth/me")
async def auth_me(user: str = Depends(get_current_user)):
    """获取当前登录用户信息"""
    user_info = await db_get_user(user)
    role = user_info.get("role", "user") if user_info else "user"
    return {"success": True, "username": user, "role": role}


@router.post("/auth/send-code")
async def send_code(request: SendCodeRequest):
    """
    发送验证码到邮箱或手机号。
    开发环境未配置 SMTP 时，验证码会打印到日志。
    """
    # 根据用途校验目标是否存在
    if request.purpose == "register":
        if request.target_type == "email":
            existing = await session_auth.get_user_by_email(request.target)
        else:
            existing = await session_auth.get_user_by_phone(request.target)
        if existing:
            raise HTTPException(status_code=409, detail="该账号已被注册")
    elif request.purpose in ("login", "reset_password"):
        if request.target_type == "email":
            existing = await session_auth.get_user_by_email(request.target)
        else:
            existing = await session_auth.get_user_by_phone(request.target)
        if not existing:
            raise HTTPException(status_code=404, detail="账号不存在")

    result = await VerificationManager.send_code(
        request.target, request.target_type, request.purpose
    )

    if not result["success"]:
        raise HTTPException(status_code=429, detail=result["message"])

    # 实际发送验证码
    purpose_map = {
        "register": "注册",
        "login": "登录",
        "reset_password": "密码重置",
    }
    purpose_cn = purpose_map.get(request.purpose, "验证")
    email_sent = True

    if request.target_type == "email":
        email_svc = get_email_service()
        email_sent = await email_svc.send_verification_code(request.target, result["code"], purpose_cn)
    elif request.target_type == "phone":
        from ..auth.sms import get_sms_service
        sms_svc = get_sms_service()
        await sms_svc.send_verification_code(request.target, result["code"], purpose_cn)

    # 始终返回验证码（开发/测试环境）
    resp = {
        "success": True,
        "message": result["message"] + (" (邮件已发送)" if email_sent else " (邮件发送失败)"),
        "cooldown": result["cooldown"],
        "code": result["code"],
    }
    return resp


@router.post("/auth/register-with-code", response_model=RegisterResponse)
async def register_with_code(request: CodeRegisterRequest):
    """通过验证码注册"""
    # 校验验证码
    valid = await VerificationManager.verify_code(
        request.target, request.target_type, "register", request.code
    )
    if not valid:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")

    if await session_auth.user_exists(request.username):
        raise HTTPException(status_code=409, detail="用户名已存在")

    phone = request.target if request.target_type == "phone" else None
    email = request.target if request.target_type == "email" else None

    success = await session_auth.register(
        request.username, request.password, phone=phone, email=email
    )
    if not success:
        raise HTTPException(status_code=409, detail="注册失败，用户名/手机/邮箱可能已被占用")

    return RegisterResponse(
        success=True,
        username=request.username,
        message="注册成功，请登录"
    )


@router.post("/auth/login-with-code", response_model=LoginResponse)
async def login_with_code(request: CodeLoginRequest, response: Response):
    """通过验证码登录（无需密码）"""
    # 校验验证码
    valid = await VerificationManager.verify_code(
        request.target, request.target_type, "login", request.code
    )
    if not valid:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")

    # 查找用户
    if request.target_type == "email":
        user = await session_auth.get_user_by_email(request.target)
    else:
        user = await session_auth.get_user_by_phone(request.target)

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    username = user["username"]
    sid = await session_auth.create(username)
    config = get_config()
    response.set_cookie(
        key="ops_session",
        value=sid,
        httponly=True,
        secure=config.auth.cookie_secure,
        samesite="lax",
        max_age=config.auth.session_ttl,
        path="/",
    )

    return LoginResponse(
        success=True,
        username=username,
        role=user.get("role", "user"),
        message="登录成功"
    )


@router.post("/auth/forgot-password")
async def forgot_password(request: ForgotPasswordRequest):
    """找回密码：发送验证码"""
    if request.target_type == "email":
        user = await session_auth.get_user_by_email(request.target)
    else:
        user = await session_auth.get_user_by_phone(request.target)

    if not user:
        raise HTTPException(status_code=404, detail="账号不存在")

    result = await VerificationManager.send_code(
        request.target, request.target_type, "reset_password"
    )

    if not result["success"]:
        raise HTTPException(status_code=429, detail=result["message"])

    if request.target_type == "email":
        email_svc = get_email_service()
        await email_svc.send_password_reset(request.target, result["code"])

    return {
        "success": True,
        "message": "验证码已发送",
        "cooldown": result["cooldown"],
    }


@router.post("/auth/reset-password")
async def reset_password(request: ResetPasswordRequest):
    """重置密码（通过验证码验证后）"""
    valid = await VerificationManager.verify_code(
        request.target, request.target_type, "reset_password", request.code
    )
    if not valid:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")

    if request.target_type == "email":
        user = await session_auth.get_user_by_email(request.target)
    else:
        user = await session_auth.get_user_by_phone(request.target)

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    success = await session_auth.reset_password(user["username"], request.new_password)
    if not success:
        raise HTTPException(status_code=500, detail="密码重置失败")

    return {"success": True, "message": "密码重置成功，请使用新密码登录"}


# ========== 业务路由 ==========

@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, user: str = Depends(get_current_user)):
    """
    核心聊天接口：接收用户自然语言指令，返回 Agent 处理结果
    聊天记录自动持久化到数据库
    """
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    session_id = request.session_id or str(uuid.uuid4())[:12]
    session = session_manager.get_or_create(session_id)

    # 首次访问该会话时，从数据库恢复历史消息
    if not session.message_history:
        db_session = await db_get_chat_session(session_id)
        if db_session:
            history = await db_get_chat_messages(session_id)
            for msg in history:
                session.message_history.append(msg)
        else:
            # 数据库中不存在，创建新会话记录
            await db_create_chat_session(session_id, user, title=request.message[:50])

    session.add_message("user", request.message)
    await db_add_chat_message(session_id, "user", request.message)

    agent = get_agent()
    result = await agent.process(
        request.message,
        session_id=session_id,
        confirmed=request.confirmed,
        session=session,
        user=user,
        elevated=request.elevated,
    )

    session.add_message("assistant", result.get("message", ""))
    await db_add_chat_message(session_id, "assistant", result.get("message", ""))
    await db_update_chat_session_last_active(session_id, title=request.message[:50])

    return ChatResponse(
        success=result.get("success", False),
        message=result.get("message", ""),
        session_id=session_id,
        chain_id=result.get("chain_id", ""),
        requires_confirm=result.get("requires_confirm", False),
        confirm_reason=result.get("confirm_reason"),
        tool_results=result.get("tool_results"),
        blocked=result.get("blocked", False)
    )


def _sse_event(event: str, data: dict) -> str:
    """生成 SSE 事件字符串"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, user: str = Depends(get_current_user)):
    """
    流式聊天接口（SSE）：接收用户自然语言指令，逐段返回 Agent 处理过程和结果
    前端通过 EventSource 连接，接收 event: status / message / confirm / blocked / done
    """
    if not request.message or not request.message.strip():
        async def error_stream():
            yield _sse_event("error", {"message": "消息不能为空"})
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    session_id = request.session_id or str(uuid.uuid4())[:12]
    session = session_manager.get_or_create(session_id)

    # 首次访问该会话时，从数据库恢复历史消息
    if not session.message_history:
        db_session = await db_get_chat_session(session_id)
        if db_session:
            history = await db_get_chat_messages(session_id)
            for msg in history:
                session.message_history.append(msg)
        else:
            await db_create_chat_session(session_id, user, title=request.message[:50])

    async def event_generator():
        # 发送会话信息
        yield _sse_event("status", {"step": "init", "session_id": session_id})

        # 保存用户消息
        session.add_message("user", request.message)
        await db_add_chat_message(session_id, "user", request.message)

        # 发送处理状态
        yield _sse_event("status", {"step": "security_check", "message": "正在校验输入安全..."})
        await asyncio.sleep(0.05)

        yield _sse_event("status", {"step": "reasoning", "message": "正在推理决策..."})

        agent = get_agent()
        result = await agent.process(
            request.message,
            session_id=session_id,
            confirmed=request.confirmed,
            session=session,
            user=user,
            elevated=request.elevated,
        )

        # 处理阻断/确认/错误等中断状态
        if result.get("blocked"):
            yield _sse_event("blocked", {
                "message": result.get("message", ""),
                "chain_id": result.get("chain_id", ""),
                "reason": result.get("confirm_reason")
            })
            yield _sse_event("done", {"chain_id": result.get("chain_id", ""), "success": False})
            return

        if result.get("requires_confirm"):
            yield _sse_event("confirm", {
                "message": result.get("message", ""),
                "chain_id": result.get("chain_id", ""),
                "reason": result.get("confirm_reason")
            })
            yield _sse_event("done", {"chain_id": result.get("chain_id", ""), "success": True, "requires_confirm": True})
            return

        if result.get("requires_privilege_elevation"):
            yield _sse_event("privilege", {
                "message": result.get("message", ""),
                "chain_id": result.get("chain_id", ""),
                "command": result.get("elevation_command")
            })
            yield _sse_event("done", {"chain_id": result.get("chain_id", ""), "success": False})
            return

        # 发送工具结果摘要（如有）
        tool_results = result.get("tool_results")
        if tool_results:
            for tr in tool_results:
                if tr.get("blocked"):
                    yield _sse_event("tool_blocked", {
                        "tool": tr.get("tool"),
                        "reason": tr.get("reason")
                    })
                elif tr.get("pending"):
                    yield _sse_event("tool_pending", {
                        "tool": tr.get("tool"),
                        "reason": tr.get("reason")
                    })
                elif "error" in tr:
                    yield _sse_event("tool_error", {
                        "tool": tr.get("tool"),
                        "error": tr.get("error")
                    })
                else:
                    yield _sse_event("tool_result", {
                        "tool": tr.get("tool"),
                        "result": tr.get("result", {})
                    })
                await asyncio.sleep(0.02)

        # 流式输出最终回复（打字机效果）
        message = result.get("message", "")
        # 按短句或字符组分段，兼顾速度和体验
        chunk_size = 4
        for i in range(0, len(message), chunk_size):
            chunk = message[i:i + chunk_size]
            yield _sse_event("message", {"token": chunk, "position": i})
            # 轻微延迟模拟打字效果，但不过度拖慢
            await asyncio.sleep(0.015)

        # 持久化助手消息
        session.add_message("assistant", message)
        await db_add_chat_message(session_id, "assistant", message)
        await db_update_chat_session_last_active(session_id, title=request.message[:50])

        yield _sse_event("done", {
            "chain_id": result.get("chain_id", ""),
            "success": result.get("success", False),
            "session_id": session_id
        })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: str = Depends(get_current_user)
):
    """获取历史会话列表（按最后活跃时间倒序）"""
    sessions = await db_list_chat_sessions(user, limit=limit, offset=offset)
    return {
        "success": True,
        "total": len(sessions),
        "sessions": sessions,
    }


@router.get("/session/{session_id}")
async def get_session(session_id: str, user: str = Depends(get_current_user)):
    """获取会话信息及消息历史（仅允许访问自己的会话）"""
    session = session_manager.get(session_id)
    messages = []

    if session and session.message_history:
        messages = session.message_history
    else:
        # 从数据库加载
        messages = await db_get_chat_messages(session_id)

    db_session = await db_get_chat_session(session_id)
    if not db_session:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 安全校验：仅允许访问自己的会话
    if db_session.get("username") != user:
        raise HTTPException(status_code=403, detail="无权访问该会话")

    return {
        "success": True,
        "session_id": session_id,
        "title": db_session.get("title", session_id),
        "message_count": len(messages),
        "created_at": db_session.get("created_at", 0),
        "last_active": db_session.get("last_active", 0),
        "messages": messages,
    }


@router.delete("/session/{session_id}")
async def delete_session(session_id: str, user: str = Depends(get_current_user)):
    """删除会话（同时删除数据库记录，仅允许删除自己的会话）"""
    db_session = await db_get_chat_session(session_id)
    if not db_session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if db_session.get("username") != user:
        raise HTTPException(status_code=403, detail="无权删除该会话")

    session_manager.delete(session_id)
    await db_delete_chat_session(session_id)
    return {"success": True, "message": f"会话 {session_id} 已删除"}


@router.get("/tools")
async def list_tools(user: str = Depends(get_current_user)):
    """
    获取所有可用的 MCP 工具列表。

    返回 MCP 标准的 ListToolsResult 格式（JSON-RPC 2.0）。
    """
    from ..mcp.tools import get_registry
    from ..mcp.schema import ListToolsResult

    registry = get_registry()
    tools = registry.list_tools()
    # 将 Tool Pydantic 模型转换为字典
    tools_data = [t.model_dump() for t in tools]
    result = ListToolsResult(tools=tools_data)

    return {
        "jsonrpc": "2.0",
        "id": None,
        "result": result.model_dump()
    }


@router.get("/audit/chains")
async def query_audit_chains(
    status: Optional[str] = Query(None, description="状态过滤: running, completed, failed, blocked"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: str = Depends(get_current_user)
):
    """查询审计链路记录"""
    chains = audit_logger.query_chains(status=status, limit=limit + offset)
    return {
        "total": len(chains),
        "offset": offset,
        "limit": limit,
        "chains": chains[offset:offset + limit]
    }


@router.get("/audit/chain/{chain_id}")
async def get_audit_chain(chain_id: str, user: str = Depends(get_current_user)):
    """获取单条审计链路详情"""
    chain = audit_logger.get_chain_by_id(chain_id)
    if not chain:
        raise HTTPException(status_code=404, detail="链路记录不存在")
    return chain


@router.get("/audit/stats")
async def get_audit_stats(
    days: int = Query(7, ge=1, le=30, description="统计天数"),
    user: str = Depends(get_current_user)
):
    """
    获取审计日志统计分析

    返回多维度统计：状态分布、时间趋势、工具使用、风险级别、拦截原因、耗时分布等
    """
    stats = audit_logger.get_statistics(days=days)
    return {
        "success": True,
        "data": stats,
    }


@router.get("/audit/events")
async def query_audit_events(
    event_type: Optional[str] = Query(None, description="事件类型过滤"),
    level: Optional[str] = Query(None, description="日志级别过滤"),
    limit: int = Query(100, ge=1, le=500),
    user: str = Depends(get_current_user)
):
    """
    查询审计事件日志

    返回系统事件、认证事件、权限事件等记录
    """
    events = []
    log_dir = audit_logger.log_dir
    
    for file in sorted(log_dir.glob("events_*.jsonl"), reverse=True):
        with open(file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event_type and event.get("event_type") != event_type:
                        continue
                    if level and event.get("level") != level.upper():
                        continue
                    events.append(event)
                    if len(events) >= limit:
                        break
                except json.JSONDecodeError:
                    continue
        if len(events) >= limit:
            break
    
    return {
        "success": True,
        "total": len(events),
        "data": events,
    }


@router.get("/audit/alerts")
async def query_audit_alerts(
    alert_type: Optional[str] = Query(None, description="告警类型过滤"),
    limit: int = Query(50, ge=1, le=200),
    user: str = Depends(get_current_user)
):
    """
    查询安全告警记录

    返回安全拦截、认证失败、权限提升等告警事件
    """
    events = []
    log_dir = audit_logger.log_dir
    
    for file in sorted(log_dir.glob("events_*.jsonl"), reverse=True):
        with open(file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get("event_type") != "security_alert":
                        continue
                    if alert_type and event.get("details", {}).get("alert_type") != alert_type:
                        continue
                    events.append(event)
                    if len(events) >= limit:
                        break
                except json.JSONDecodeError:
                    continue
        if len(events) >= limit:
            break
    
    return {
        "success": True,
        "total": len(events),
        "data": events,
    }


@router.get("/audit/auth")
async def query_audit_auth(
    username: Optional[str] = Query(None, description="用户名过滤"),
    success: Optional[bool] = Query(None, description="成功/失败过滤"),
    limit: int = Query(100, ge=1, le=500),
    user: str = Depends(get_current_user)
):
    """
    查询认证事件记录

    返回登录、登出、认证失败等事件
    """
    events = []
    log_dir = audit_logger.log_dir
    
    for file in sorted(log_dir.glob("events_*.jsonl"), reverse=True):
        with open(file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get("event_type") != "auth":
                        continue
                    details = event.get("details", {})
                    if username and details.get("username") != username:
                        continue
                    if success is not None and details.get("success") != success:
                        continue
                    events.append(event)
                    if len(events) >= limit:
                        break
                except json.JSONDecodeError:
                    continue
        if len(events) >= limit:
            break
    
    return {
        "success": True,
        "total": len(events),
        "data": events,
    }


@router.get("/audit/privilege")
async def query_audit_privilege(
    status: Optional[str] = Query(None, description="状态过滤"),
    requested_by: Optional[str] = Query(None, description="申请人过滤"),
    limit: int = Query(50, ge=1, le=200),
    user: str = Depends(get_current_user)
):
    """
    查询权限操作事件记录

    返回权限申请、批准、拒绝等事件
    """
    events = []
    log_dir = audit_logger.log_dir
    
    for file in sorted(log_dir.glob("events_*.jsonl"), reverse=True):
        with open(file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get("event_type") != "privilege":
                        continue
                    details = event.get("details", {})
                    if status and details.get("status") != status:
                        continue
                    if requested_by and details.get("requested_by") != requested_by:
                        continue
                    events.append(event)
                    if len(events) >= limit:
                        break
                except json.JSONDecodeError:
                    continue
        if len(events) >= limit:
            break
    
    return {
        "success": True,
        "total": len(events),
        "data": events,
    }


@router.post("/audit/cleanup")
async def cleanup_audit_logs(
    days: int = Query(30, ge=1, le=365, description="保留天数"),
    user: str = Depends(get_current_user)
):
    """
    清理过期的审计日志

    删除指定天数之前的日志文件
    """
    log_dir = audit_logger.log_dir
    cutoff_time = time.time() - days * 86400
    cleaned_count = 0
    
    for file in log_dir.glob("*.jsonl"):
        if file.stat().st_mtime < cutoff_time:
            file.unlink()
            cleaned_count += 1
    
    for file in log_dir.glob("audit.log.*"):
        if file.stat().st_mtime < cutoff_time:
            file.unlink()
            cleaned_count += 1
    
    return {
        "success": True,
        "message": f"已清理 {cleaned_count} 个过期日志文件",
        "cleaned_count": cleaned_count,
    }


@router.get("/audit/export")
async def export_audit_logs(
    format: str = Query("json", pattern="^(json|csv)$", description="导出格式: json 或 csv"),
    start_time: Optional[float] = Query(None, description="开始时间戳"),
    end_time: Optional[float] = Query(None, description="结束时间戳"),
    status: Optional[str] = Query(None, description="状态过滤"),
    limit: int = Query(1000, ge=1, le=5000, description="最大条数"),
    user: str = Depends(get_current_user)
):
    """
    导出审计日志

    支持 JSON/CSV 格式下载，可按时间范围和状态过滤
    """
    from starlette.responses import StreamingResponse as StarletteStreamingResponse
    import csv
    import io

    chains = audit_logger.query_chains(
        start_time=start_time,
        end_time=end_time,
        status=status,
        limit=limit
    )

    if not chains:
        raise HTTPException(status_code=404, detail="未找到符合条件的审计日志")

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"audit_export_{timestamp_str}.{format}"

    if format == "json":
        async def json_generator():
            yield '[\n'
            for i, chain in enumerate(chains):
                prefix = '  ' if i == 0 else ',\n  '
                yield prefix + json.dumps(chain, ensure_ascii=False)
            yield '\n]\n'

        return StarletteStreamingResponse(
            json_generator(),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    # CSV 格式
    def csv_generator():
        output = io.StringIO()
        writer = csv.writer(output)
        # 表头
        writer.writerow([
            "chain_id", "session_id", "user", "user_input",
            "start_time", "end_time", "duration_sec",
            "final_status", "summary", "node_count",
            "execution_path"
        ])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        for chain in chains:
            nodes = chain.get("nodes", [])
            # 提取执行路径
            path = " -> ".join(n.get("node_type", "") for n in nodes)
            writer.writerow([
                chain.get("chain_id", ""),
                chain.get("session_id", ""),
                chain.get("user", ""),
                chain.get("user_input", "").replace("\n", " ")[:200],
                chain.get("start_time", ""),
                chain.get("end_time", ""),
                chain.get("duration_sec", ""),
                chain.get("final_status", ""),
                (chain.get("summary", "") or "").replace("\n", " ")[:300],
                len(nodes),
                path
            ])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    return StarletteStreamingResponse(
        csv_generator(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.api_route("/ping", methods=["GET", "HEAD"])
async def ping():
    """轻量级存活探测（公开访问，无需认证）"""
    return {"status": "ok"}


@router.get("/health")
async def health_check():
    """健康检查接口（公开访问，无需认证）
    
    返回系统各组件的健康状态，包括：
    - 服务基础信息
    - LLM 服务连接状态
    - MCP 工具注册状态
    - 数据库连接状态
    - 安全规则加载状态
    - 中间件状态
    """
    config = get_config()
    results = {
        "status": "healthy",
        "agent_name": config.agent.name,
        "version": config.agent.version,
        "timestamp": time.time(),
        "datetime": datetime.now().isoformat(),
        "components": {}
    }
    
    # 1. MCP 工具注册检查
    try:
        from ..mcp.tools import get_registry
        registry = get_registry()
        tools = registry.list_tools()
        results["components"]["mcp_tools"] = {
            "status": "healthy",
            "tools_count": len(tools),
            "tools": [t.name for t in tools]
        }
    except Exception as e:
        results["status"] = "degraded"
        results["components"]["mcp_tools"] = {
            "status": "unhealthy",
            "error": str(e)
        }
    
    # 2. LLM 服务检查
    try:
        from ..llm.client import get_llm_client
        llm_client = get_llm_client()
        llm_check = await llm_client.health_check()
        results["components"]["llm"] = llm_check
    except Exception as e:
        results["status"] = "degraded"
        results["components"]["llm"] = {
            "status": "unhealthy",
            "error": str(e),
            "configured": bool(config.llm.api_base)
        }
    
    # 3. 数据库连接检查
    try:
        from ..db import test_db_connection
        db_ok = await test_db_connection()
        if db_ok:
            results["components"]["database"] = {"status": "healthy"}
        else:
            results["status"] = "degraded"
            results["components"]["database"] = {"status": "unhealthy"}
    except Exception as e:
        results["status"] = "degraded"
        results["components"]["database"] = {
            "status": "unhealthy",
            "error": str(e)
        }
    
    # 4. 安全规则检查
    try:
        from ..security.rules import SecurityRuleEngine
        engine = SecurityRuleEngine()
        rules = engine.rules
        level_counts = {}
        for rule in rules:
            level_counts[rule.level] = level_counts.get(rule.level, 0) + 1
        
        results["components"]["security_rules"] = {
            "status": "healthy",
            "rules_count": len(rules),
            "rules_by_level": level_counts
        }
    except Exception as e:
        results["status"] = "degraded"
        results["components"]["security_rules"] = {
            "status": "unhealthy",
            "error": str(e)
        }
    
    # 5. 安全护栏检查
    try:
        from ..security.guard import SecurityGuard
        guard = SecurityGuard()
        results["components"]["security_guard"] = {
            "status": "healthy",
            "whitelist_enabled": True,
            "checks_performed": guard._stats.get("command_checks", 0)
        }
    except Exception as e:
        results["components"]["security_guard"] = {
            "status": "unhealthy",
            "error": str(e)
        }
    
    # 6. 中间件检查
    try:
        results["components"]["middleware"] = {
            "status": "healthy",
            "rate_limit_enabled": True,
            "request_monitor_enabled": True
        }
    except Exception as e:
        results["components"]["middleware"] = {
            "status": "unhealthy",
            "error": str(e)
        }
    
    # 7. 审计日志检查
    try:
        from ..audit import AuditLogger
        logger = AuditLogger()
        results["components"]["audit_logger"] = {
            "status": "healthy",
            "log_dir": str(logger.log_dir)
        }
    except Exception as e:
        results["components"]["audit_logger"] = {
            "status": "unhealthy",
            "error": str(e)
        }
    
    # 汇总状态
    unhealthy_count = sum(
        1 for comp in results["components"].values() 
        if comp.get("status") != "healthy"
    )
    if unhealthy_count > 0:
        results["status"] = "degraded" if unhealthy_count < len(results["components"]) else "unhealthy"
    
    # 兼容前端：在根级别提供 tools_count
    results["tools_count"] = results["components"].get("mcp_tools", {}).get("tools_count", 0)
    
    # 计算响应时间
    results["response_ms"] = round((time.time() - results["timestamp"]) * 1000, 2)
    
    return results


@router.get("/config")
async def get_agent_config(user: str = Depends(get_current_user)):
    """获取 Agent 配置信息（脱敏）"""
    cfg = get_config()
    from ..security.rules import SecurityRuleEngine
    engine = SecurityRuleEngine()
    return {
        "agent": {
            "name": cfg.agent.name,
            "version": cfg.agent.version,
            "description": cfg.agent.description
        },
        "llm": {
            "provider": cfg.llm.provider,
            "model": cfg.llm.model
        },
        "security": {
            "restricted_user": cfg.security.restricted_user,
            "rule_count": len(engine.rules)
        },
        "audit": {
            "log_dir": cfg.audit.log_dir,
            "retention_days": cfg.audit.retention_days
        }
    }


# ========== MCP 标准协议端点 ==========

@router.post("/mcp")
async def mcp_endpoint(request: Request):
    """
    MCP (Model Context Protocol) 标准 JSON-RPC 2.0 端点。

    支持方法：
    - `initialize`: 协议握手，返回服务端能力和信息
    - `tools/list`: 获取所有可用工具列表
    - `tools/call`: 调用指定工具，执行运维操作

    请求格式（JSON-RPC 2.0）：
    ```json
    {
      "jsonrpc": "2.0",
      "id": 1,
      "method": "initialize",
      "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "1.0.0"}
      }
    }
    ```

    响应格式（JSON-RPC 2.0）：
    ```json
    {
      "jsonrpc": "2.0",
      "id": 1,
      "result": {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {"listChanged": false}},
        "serverInfo": {"name": "kylin-ops-mcp-server", "version": "1.0.0"}
      }
    }
    ```
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error: 请求体不是有效的 JSON"},
            },
        )

    server = get_mcp_server()
    response = await server.handle_request(body)
    return JSONResponse(content=response)


# ========== root 权限申请端点 ==========

class PrivilegeRequestBody(BaseModel):
    session_id: str
    command: str = Field(..., description="需要提权的命令/工具调用")
    reason: str = Field(..., min_length=5, description="申请理由（至少5个字）")


class PrivilegeApproveBody(BaseModel):
    approved: bool = Field(True, description="是否批准")


@router.post("/privilege/request")
async def create_privilege_request(
    request: PrivilegeRequestBody,
    user: str = Depends(get_current_user)
):
    """创建 root 权限申请"""
    import uuid
    request_id = "priv_" + str(uuid.uuid4())[:12]
    await db_create_privilege_request(
        request_id=request_id,
        session_id=request.session_id,
        command=request.command,
        reason=request.reason,
        requested_by=user,
    )
    return {
        "success": True,
        "request_id": request_id,
        "message": "权限申请已提交，等待审批",
    }


@router.post("/privilege/approve/{request_id}")
async def approve_privilege_request(
    request_id: str,
    body: PrivilegeApproveBody,
    user: str = Depends(get_current_user)
):
    """审批 root 权限申请（仅管理员可审批）"""
    # 0. 权限检查：仅 admin 可审批，且不能自审批
    user_info = await db_get_user(user)
    role = user_info.get("role", "user") if user_info else "user"
    if role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可审批权限申请")
    
    # 1. 先检查申请是否存在且未过期
    req = await db_get_privilege_request(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="权限申请不存在")
    
    # 禁止自审批
    if req.get("requested_by") == user:
        raise HTTPException(status_code=403, detail="不能审批自己的权限申请")
    
    # 2. 检查申请是否过期（超过10分钟未处理自动失效）
    if req.get("status") == "pending":
        created_at = req.get("created_at", 0)
        if time.time() - created_at > 600:  # 10分钟超时
            # 更新状态为过期
            factory = await get_session_factory()
            async with factory() as session:
                result = await session.execute(
                    select(PrivilegeRequestModel).where(
                        PrivilegeRequestModel.request_id == request_id
                    )
                )
                r = result.scalar_one_or_none()
                if r:
                    r.status = "expired"
                    await session.commit()
            # 记录审计日志
            audit_logger.log_event("privilege_expired", {
                "request_id": request_id,
                "requested_by": req.get("requested_by"),
                "command": req.get("command"),
                "expired_by": user,
                "reason": "申请超时未处理",
                "timeout_sec": 600
            }, level="WARNING")
            raise HTTPException(status_code=400, detail="权限申请已过期（超过10分钟）")
    
    # 3. 执行审批操作
    if body.approved:
        ok = await db_approve_privilege_request(request_id, approved_by=user)
        if ok:
            # 记录审计日志
            audit_logger.log_event("privilege_approved", {
                "request_id": request_id,
                "requested_by": req.get("requested_by"),
                "approved_by": user,
                "command": req.get("command"),
                "reason": req.get("reason"),
                "expires_in": 300
            }, level="INFO")
            return {
                "success": True,
                "message": "权限申请已批准，有效期 5 分钟",
                "expires_in": 300,
            }
    else:
        ok = await db_reject_privilege_request(request_id, approved_by=user)
        if ok:
            # 记录审计日志
            audit_logger.log_event("privilege_rejected", {
                "request_id": request_id,
                "requested_by": req.get("requested_by"),
                "rejected_by": user,
                "command": req.get("command"),
                "reason": req.get("reason")
            }, level="INFO")
            return {
                "success": True,
                "message": "权限申请已拒绝",
            }
    
    raise HTTPException(status_code=400, detail="申请不存在或已处理")


@router.get("/privilege/requests")
async def list_privilege_requests(
    status: Optional[str] = Query(None, description="状态过滤: pending/approved/rejected/expired"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: str = Depends(get_current_user)
):
    """查询权限申请列表
    
    - 管理员(role=admin)可查看所有申请
    - 普通用户只能查看自己的申请
    """
    user_info = await db_get_user(user)
    role = user_info.get("role", "user") if user_info else "user"
    
    # 普通用户只能查看自己的申请
    requested_by_filter = user if role != "admin" else None
    
    reqs = await db_list_privilege_requests(
        status=status,
        requested_by=requested_by_filter,
        limit=limit,
        offset=offset
    )
    return {
        "success": True,
        "total": len(reqs),
        "requests": reqs,
    }


# 注册监控仪表盘子路由
router.include_router(monitor_router)
