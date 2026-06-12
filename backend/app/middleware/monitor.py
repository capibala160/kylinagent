"""请求监控中间件

记录请求耗时、错误率、状态码分布等监控指标。
"""

import asyncio
import time
import logging
from typing import Dict, Optional, Callable
from collections import defaultdict, deque
from datetime import datetime
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


logger = logging.getLogger("request_monitor")


class RequestMonitor:
    """请求监控数据收集器
    
    收集并统计 API 请求的各项指标：
    - 请求耗时分布
    - 状态码分布
    - 错误率统计
    - 请求路径分布
    - 时间趋势分析
    """
    
    # 保留的历史记录数量
    MAX_HISTORY = 1000
    MAX_ERROR_HISTORY = 100
    
    def __init__(self):
        # 请求历史记录
        self._request_history: deque = deque(maxlen=self.MAX_HISTORY)
        self._error_history: deque = deque(maxlen=self.MAX_ERROR_HISTORY)
        
        # 统计数据
        self._stats = {
            "total_requests": 0,
            "total_errors": 0,
            "total_time_ms": 0,
            "status_codes": defaultdict(int),
            "path_counts": defaultdict(int),
            "path_times": defaultdict(list),
            "hourly_counts": defaultdict(int),
            "hourly_errors": defaultdict(int),
            "hourly_avg_time": defaultdict(list),
        }
        
        # 开始时间
        self._start_time = time.time()

        # 保护共享状态的异步锁
        self._lock = asyncio.Lock()

    async def record_request(
        self,
        path: str,
        method: str,
        status_code: int,
        duration_ms: float,
        client_ip: str,
        user: Optional[str] = None,
        error: Optional[str] = None,
    ):
        """记录单个请求"""
        async with self._lock:
            now = time.time()
            hour_key = datetime.now().strftime("%Y-%m-%d %H:00")

            # 更新统计
            self._stats["total_requests"] += 1
            self._stats["total_time_ms"] += duration_ms
            self._stats["status_codes"][status_code] += 1
            self._stats["path_counts"][path] += 1
            self._stats["path_times"][path].append(duration_ms)
            self._stats["hourly_counts"][hour_key] += 1
            self._stats["hourly_avg_time"][hour_key].append(duration_ms)

            # 记录错误
            if status_code >= 400:
                self._stats["total_errors"] += 1
                self._stats["hourly_errors"][hour_key] += 1
                error_record = {
                    "timestamp": now,
                    "datetime": datetime.now().isoformat(),
                    "path": path,
                    "method": method,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "client_ip": client_ip,
                    "user": user,
                    "error": error,
                }
                self._error_history.append(error_record)
                logger.warning(
                    f"Request error: {method} {path} -> {status_code} ({duration_ms:.1f}ms) "
                    f"client={client_ip} user={user}"
                )

            # 记录请求历史
            request_record = {
                "timestamp": now,
                "datetime": datetime.now().isoformat(),
                "path": path,
                "method": method,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "client_ip": client_ip,
                "user": user,
            }
            self._request_history.append(request_record)

            # 清理过期的路径时间记录（保留最近 100 个）
            for path_key in self._stats["path_times"]:
                if len(self._stats["path_times"][path_key]) > 100:
                    self._stats["path_times"][path_key] = self._stats["path_times"][path_key][-100:]

    async def get_stats(self) -> Dict:
        """获取监控统计信息"""
        async with self._lock:
            total = self._stats["total_requests"]
            errors = self._stats["total_errors"]
            total_time = self._stats["total_time_ms"]

            # 计算平均耗时
            avg_time = round(total_time / total, 2) if total > 0 else 0

            # 计算错误率
            error_rate = round(100.0 * errors / total, 2) if total > 0 else 0

            # 计算路径平均耗时
            path_avg_times = {}
            for path, times in self._stats["path_times"].items():
                if times:
                    path_avg_times[path] = round(sum(times) / len(times), 2)

            # 计算小时平均耗时
            hourly_avg_times = {}
            for hour, times in self._stats["hourly_avg_time"].items():
                if times:
                    hourly_avg_times[hour] = round(sum(times) / len(times), 2)

            # 耗时分布
            durations = [r["duration_ms"] for r in self._request_history]
            duration_stats = self._calculate_duration_stats(durations)

            # 运行时长
            uptime_seconds = round(time.time() - self._start_time, 1)

            return {
            "uptime_seconds": uptime_seconds,
            "total_requests": total,
            "total_errors": errors,
            "error_rate": error_rate,
            "avg_time_ms": avg_time,
            "duration_stats": duration_stats,
            "status_codes": dict(self._stats["status_codes"]),
            "top_paths": dict(sorted(
                self._stats["path_counts"].items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]),
            "path_avg_times": dict(sorted(
                path_avg_times.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]),
            "hourly_counts": dict(sorted(self._stats["hourly_counts"].items())[-24:]),
            "hourly_errors": dict(sorted(self._stats["hourly_errors"].items())[-24:]),
            "hourly_avg_times": dict(sorted(hourly_avg_times.items())[-24:]),
            "requests_per_minute": round(total / (uptime_seconds / 60), 2) if uptime_seconds > 60 else 0,
        }
    
    def _calculate_duration_stats(self, durations: list) -> Dict:
        """计算耗时分布统计"""
        if not durations:
            return {
                "min_ms": 0,
                "max_ms": 0,
                "avg_ms": 0,
                "median_ms": 0,
                "p90_ms": 0,
                "p95_ms": 0,
                "p99_ms": 0,
            }
        
        sorted_durations = sorted(durations)
        n = len(sorted_durations)
        
        def percentile(p: float) -> float:
            idx = int(n * p / 100)
            return sorted_durations[min(idx, n - 1)]
        
        return {
            "min_ms": round(sorted_durations[0], 2),
            "max_ms": round(sorted_durations[-1], 2),
            "avg_ms": round(sum(durations) / n, 2),
            "median_ms": round(sorted_durations[n // 2], 2),
            "p90_ms": round(percentile(90), 2),
            "p95_ms": round(percentile(95), 2),
            "p99_ms": round(percentile(99), 2),
        }
    
    async def get_recent_requests(self, limit: int = 50) -> list:
        """获取最近的请求记录"""
        async with self._lock:
            return list(self._request_history)[-limit:]

    async def get_recent_errors(self, limit: int = 20) -> list:
        """获取最近的错误记录"""
        async with self._lock:
            return list(self._error_history)[-limit:]

    async def reset_stats(self):
        """重置统计信息"""
        async with self._lock:
            self._request_history.clear()
            self._error_history.clear()
            self._stats = {
                "total_requests": 0,
                "total_errors": 0,
                "total_time_ms": 0,
                "status_codes": defaultdict(int),
                "path_counts": defaultdict(int),
                "path_times": defaultdict(list),
                "hourly_counts": defaultdict(int),
                "hourly_errors": defaultdict(int),
                "hourly_avg_time": defaultdict(list),
            }
            self._start_time = time.time()


class RequestMonitorMiddleware(BaseHTTPMiddleware):
    """请求监控中间件
    
    自动记录每个请求的耗时、状态码等信息。
    """
    
    # 不监控的路径（静态资源等）
    SKIP_PATHS = [
        "/static",
        "/api/docs",
        "/api/redoc",
        "/favicon.ico",
    ]
    
    def __init__(self, app, monitor: Optional[RequestMonitor] = None):
        super().__init__(app)
        self.monitor = monitor or RequestMonitor()
    
    def _should_skip(self, path: str) -> bool:
        """检查是否跳过监控"""
        for skip_path in self.SKIP_PATHS:
            if path.startswith(skip_path):
                return True
        return False
    
    def _get_client_info(self, request: Request) -> tuple[str, Optional[str]]:
        """获取客户端信息"""
        client_ip = request.client.host if request.client else "unknown"
        
        # 代理场景下获取真实 IP
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
        
        # 获取用户身份（如果已认证）
        user = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            user = "authenticated"  # 简化处理，不暴露具体用户
        
        return client_ip, user
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """处理请求监控"""
        path = request.url.path
        method = request.method
        
        # 跳过不监控的路径
        if self._should_skip(path):
            return await call_next(request)
        
        # 记录开始时间
        start_time = time.time()
        error_msg = None
        
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as e:
            status_code = 500
            error_msg = str(e)
            raise
        finally:
            # 计算耗时
            duration_ms = (time.time() - start_time) * 1000
            
            # 获取客户端信息
            client_ip, user = self._get_client_info(request)
            
            # 记录请求
            await self.monitor.record_request(
                path=path,
                method=method,
                status_code=status_code,
                duration_ms=duration_ms,
                client_ip=client_ip,
                user=user,
                error=error_msg,
            )
        
        return response
    
    async def get_stats(self) -> Dict:
        """获取监控统计信息"""
        return await self.monitor.get_stats()

    async def get_recent_requests(self, limit: int = 50) -> list:
        """获取最近的请求记录"""
        return await self.monitor.get_recent_requests(limit)

    async def get_recent_errors(self, limit: int = 20) -> list:
        """获取最近的错误记录"""
        return await self.monitor.get_recent_errors(limit)

    async def reset_stats(self):
        """重置统计信息"""
        await self.monitor.reset_stats()