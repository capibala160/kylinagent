"""监控仪表盘 API

提供系统资源监控的 REST 接口，数据来自 SystemMonitor 采集器。
同时提供 API 限流和请求监控的统计接口。
"""

from typing import Optional
from fastapi import APIRouter, Query, Depends, Request, HTTPException
from pydantic import BaseModel, Field

from ..auth.session import session_auth
from ..db import db_get_user
from ..monitor import get_monitor

router = APIRouter()


async def _get_current_user(request: Request) -> str:
    return await session_auth.verify(request)


async def _require_admin(request: Request) -> str:
    """仅允许管理员角色访问"""
    user = await session_auth.verify(request)
    user_info = await db_get_user(user)
    role = user_info.get("role", "user") if user_info else "user"
    if role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
    return user


@router.get("/monitor/overview")
async def monitor_overview(user: str = Depends(_get_current_user)):
    """
    获取系统监控概览数据

    返回 CPU、内存、磁盘、网络、进程、服务的实时数据。
    Windows 开发环境返回 Mock 数据。
    """
    monitor = get_monitor()
    data = await monitor.get_overview()
    return {
        "success": True,
        "data": data,
    }


@router.get("/monitor/history")
async def monitor_history(
    points: int = Query(60, ge=1, le=120, description="历史数据点数"),
    user: str = Depends(_get_current_user)
):
    """
    获取历史监控数据

    返回最近 N 次采集的数据，用于绘制趋势图。
    """
    monitor = get_monitor()
    history = monitor.get_history(points=points)
    return {
        "success": True,
        "points": len(history),
        "data": history,
    }


# ===== API 限流统计 =====

@router.get("/monitor/ratelimit")
async def ratelimit_stats(user: str = Depends(_get_current_user)):
    """
    获取 API 限流统计信息
    
    返回各路径的限流配置和当前状态。
    """
    from ..main import rate_limit
    stats = await rate_limit.get_stats()
    return {
        "success": True,
        "data": stats,
    }


@router.post("/monitor/ratelimit/reset")
async def ratelimit_reset(user: str = Depends(_require_admin)):
    """
    重置限流统计信息（仅管理员）
    
    清零所有计数器，保留限流配置。
    """
    from ..main import rate_limit
    await rate_limit.reset_stats()
    return {
        "success": True,
        "message": "限流统计已重置",
    }


# ===== 请求监控统计 =====

@router.get("/monitor/requests")
async def request_stats(user: str = Depends(_get_current_user)):
    """
    获取请求监控统计信息
    
    返回请求耗时、错误率、状态码分布等指标。
    """
    from ..main import request_monitor
    stats = await request_monitor.get_stats()
    return {
        "success": True,
        "data": stats,
    }


@router.get("/monitor/requests/recent")
async def recent_requests(
    limit: int = Query(50, ge=1, le=100, description="返回数量"),
    user: str = Depends(_get_current_user)
):
    """
    获取最近的请求记录
    
    返回最近 N 个请求的详细信息。
    """
    from ..main import request_monitor
    requests = await request_monitor.get_recent_requests(limit=limit)
    return {
        "success": True,
        "total": len(requests),
        "data": requests,
    }


@router.get("/monitor/requests/errors")
async def recent_errors(
    limit: int = Query(20, ge=1, le=50, description="返回数量"),
    user: str = Depends(_get_current_user)
):
    """
    获取最近的错误请求记录
    
    返回最近 N 个错误请求的详细信息。
    """
    from ..main import request_monitor
    errors = await request_monitor.get_recent_errors(limit=limit)
    return {
        "success": True,
        "total": len(errors),
        "data": errors,
    }


@router.post("/monitor/requests/reset")
async def request_stats_reset(user: str = Depends(_require_admin)):
    """
    重置请求监控统计信息（仅管理员）
    
    清零所有计数器和历史记录。
    """
    from ..main import request_monitor
    await request_monitor.reset_stats()
    return {
        "success": True,
        "message": "请求监控统计已重置",
    }


# ===== 综合监控面板 =====

@router.get("/monitor/dashboard")
async def monitor_dashboard(user: str = Depends(_get_current_user)):
    """
    获取综合监控面板数据
    
    整合系统监控、限流统计、请求监控的数据，用于前端仪表盘展示。
    """
    from ..main import rate_limit, request_monitor
    
    # 系统监控
    sys_monitor = get_monitor()
    sys_data = await sys_monitor.get_overview()
    
    # 限流统计
    ratelimit_data = await rate_limit.get_stats()
    
    # 请求监控
    request_data = await request_monitor.get_stats()
    
    # 最近错误
    recent_errors = await request_monitor.get_recent_errors(limit=10)
    
    return {
        "success": True,
        "data": {
            "system": sys_data,
            "ratelimit": ratelimit_data,
            "requests": request_data,
            "recent_errors": recent_errors,
        },
    }
