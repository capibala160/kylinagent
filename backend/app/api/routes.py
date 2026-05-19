import os
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Header, Depends, Request, Response
from pydantic import BaseModel, Field

from ..agent import OpsAgent, SessionManager
from ..audit import AuditLogger
from ..auth.session import session_auth
from ..config import get_config

router = APIRouter()
session_manager = SessionManager()
audit_logger = AuditLogger(
    log_dir=get_config().audit.log_dir,
    retention_days=get_config().audit.retention_days
)

# 全局 Agent 实例
_agent: Optional[OpsAgent] = None


def get_agent() -> OpsAgent:
    global _agent
    if _agent is None:
        use_mock = False
        _agent = OpsAgent(use_mock_llm=use_mock)
    return _agent


def get_current_user(request: Request) -> str:
    """统一认证依赖：优先 Cookie Session，降级支持 Dev Token"""
    return session_auth.verify(request)


# ========== 请求/响应模型 ==========

class LoginRequest(BaseModel):
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class LoginResponse(BaseModel):
    success: bool
    username: str
    message: str


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, description="用户名")
    password: str = Field(..., min_length=6, max_length=64, description="密码")


class RegisterResponse(BaseModel):
    success: bool
    username: str
    message: str


class ChatRequest(BaseModel):
    message: str = Field(..., description="用户输入的消息")
    session_id: Optional[str] = Field(None, description="会话 ID，不传则创建新会话")
    confirmed: bool = Field(False, description="是否已确认执行高危操作")


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
    if session_auth.user_exists(request.username):
        raise HTTPException(status_code=409, detail="用户名已存在")

    session_auth.register(request.username, request.password)
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
    if not session_auth.verify_password(request.username, request.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    sid = session_auth.create(request.username)
    response.set_cookie(
        key="ops_session",
        value=sid,
        httponly=True,
        secure=True,         # 生产环境部署到 HTTPS 后必须改为 True
        samesite="lax",
        max_age=get_config().auth.session_ttl,
        path="/",
    )
    return LoginResponse(
        success=True,
        username=request.username,
        message="登录成功"
    )


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    """用户登出接口，销毁 Session 并清除 Cookie"""
    sid = request.cookies.get("ops_session", "")
    if sid:
        session_auth.destroy(sid)
    response.delete_cookie(key="ops_session", path="/")
    return {"success": True, "message": "已登出"}


@router.get("/auth/me")
async def auth_me(user: str = Depends(get_current_user)):
    """获取当前登录用户信息"""
    return {"success": True, "username": user}


# ========== 业务路由 ==========

@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, user: str = Depends(get_current_user)):
    """
    核心聊天接口：接收用户自然语言指令，返回 Agent 处理结果
    """
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    session_id = request.session_id or str(uuid.uuid4())[:12]
    session = session_manager.get_or_create(session_id)
    session.add_message("user", request.message)

    agent = get_agent()
    result = await agent.process(
        request.message,
        session_id=session_id,
        confirmed=request.confirmed,
        session=session,
        user=user,
    )

    session.add_message("assistant", result.get("message", ""))

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


@router.get("/session/{session_id}")
async def get_session(session_id: str, user: str = Depends(get_current_user)):
    """获取会话信息"""
    session = session_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    return SessionResponse(
        session_id=session.session_id,
        message_count=len(session.message_history),
        created_at=session.created_at,
        last_active=session.last_active
    )


@router.delete("/session/{session_id}")
async def delete_session(session_id: str, user: str = Depends(get_current_user)):
    """删除会话"""
    session_manager.delete(session_id)
    return {"success": True, "message": f"会话 {session_id} 已删除"}


@router.get("/tools")
async def list_tools(user: str = Depends(get_current_user)):
    """获取所有可用的 MCP 工具列表"""
    from ..mcp.tools import get_registry
    registry = get_registry()
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.inputSchema
            }
            for t in registry.list_tools()
        ]
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


@router.get("/health")
async def health_check():
    """健康检查接口（公开访问，无需认证）"""
    from ..mcp.tools import get_registry
    registry = get_registry()
    return {
        "status": "healthy",
        "agent_name": get_config().agent.name,
        "version": get_config().agent.version,
        "tools_count": len(registry.list_tools()),
        "llm_configured": bool(get_config().llm.api_base)
    }


@router.get("/config")
async def get_agent_config(user: str = Depends(get_current_user)):
    """获取 Agent 配置信息（脱敏）"""
    cfg = get_config()
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
            "rule_count": len(cfg.security.dangerous_commands) + len(cfg.security.confirm_required_patterns)
        },
        "audit": {
            "log_dir": cfg.audit.log_dir,
            "retention_days": cfg.audit.retention_days
        }
    }
