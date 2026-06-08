"""API 限流中间件

基于 IP 和用户的请求频率限制，防止服务过载和滥用。
"""

import time
from typing import Dict, Optional, Callable
from collections import defaultdict
from fastapi import Request, Response, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimiter:
    """滑动窗口限流器
    
    使用滑动窗口算法实现精确的请求频率限制。
    支持基于 IP 和用户的双重限流。
    """
    
    def __init__(
        self,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
        burst_size: int = 10,
    ):
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        self.burst_size = burst_size
        
        # 滑动窗口存储：{key: [timestamp1, timestamp2, ...]}
        self._minute_windows: Dict[str, list] = defaultdict(list)
        self._hour_windows: Dict[str, list] = defaultdict(list)
        
        # 限流统计
        self._stats = {
            "total_requests": 0,
            "limited_requests": 0,
            "limited_ips": set(),
            "limited_users": set(),
        }
    
    def _clean_window(self, window: list, now: float, window_size: float) -> list:
        """清理过期的时间戳"""
        return [ts for ts in window if now - ts < window_size]
    
    def check_rate_limit(
        self,
        key: str,
        now: float,
    ) -> tuple[bool, Optional[str], Dict]:
        """
        检查是否超过限流阈值
        
        Args:
            key: 限流键（IP 或用户标识）
            now: 当前时间戳
            
        Returns:
            (是否允许, 原因, 详细信息)
        """
        self._stats["total_requests"] += 1
        
        # 清理过期窗口
        self._minute_windows[key] = self._clean_window(
            self._minute_windows[key], now, 60.0
        )
        self._hour_windows[key] = self._clean_window(
            self._hour_windows[key], now, 3600.0
        )
        
        minute_count = len(self._minute_windows[key])
        hour_count = len(self._hour_windows[key])
        
        # 检查分钟限流
        if minute_count >= self.requests_per_minute:
            self._stats["limited_requests"] += 1
            self._stats["limited_ips"].add(key)
            retry_after = 60 - (now - self._minute_windows[key][0])
            return False, "rate_limit_minute", {
                "limit": self.requests_per_minute,
                "current": minute_count,
                "window": "minute",
                "retry_after": max(1, int(retry_after)),
            }
        
        # 检查小时限流
        if hour_count >= self.requests_per_hour:
            self._stats["limited_requests"] += 1
            self._stats["limited_ips"].add(key)
            retry_after = 3600 - (now - self._hour_windows[key][0])
            return False, "rate_limit_hour", {
                "limit": self.requests_per_hour,
                "current": hour_count,
                "window": "hour",
                "retry_after": max(1, int(retry_after)),
            }
        
        # 检查突发限流（短时间内大量请求）
        burst_window = 5.0  # 5秒内的突发请求
        burst_count = sum(1 for ts in self._minute_windows[key] if now - ts < burst_window)
        if burst_count >= self.burst_size:
            self._stats["limited_requests"] += 1
            self._stats["limited_ips"].add(key)
            return False, "rate_limit_burst", {
                "limit": self.burst_size,
                "current": burst_count,
                "window": "burst",
                "retry_after": 5,
            }
        
        # 记录请求时间戳
        self._minute_windows[key].append(now)
        self._hour_windows[key].append(now)
        
        return True, None, {
            "minute_remaining": self.requests_per_minute - minute_count - 1,
            "hour_remaining": self.requests_per_hour - hour_count - 1,
        }
    
    def get_stats(self) -> Dict:
        """获取限流统计信息"""
        return {
            "total_requests": self._stats["total_requests"],
            "limited_requests": self._stats["limited_requests"],
            "limited_rate": round(
                100.0 * self._stats["limited_requests"] / self._stats["total_requests"], 2
            ) if self._stats["total_requests"] > 0 else 0,
            "limited_ips_count": len(self._stats["limited_ips"]),
            "limited_users_count": len(self._stats["limited_users"]),
            "active_keys_minute": len(self._minute_windows),
            "active_keys_hour": len(self._hour_windows),
            "config": {
                "requests_per_minute": self.requests_per_minute,
                "requests_per_hour": self.requests_per_hour,
                "burst_size": self.burst_size,
            }
        }
    
    def reset_stats(self):
        """重置统计信息"""
        self._stats = {
            "total_requests": 0,
            "limited_requests": 0,
            "limited_ips": set(),
            "limited_users": set(),
        }


class RateLimitMiddleware(BaseHTTPMiddleware):
    """API 限流中间件
    
    基于 IP 地址和用户身份进行请求频率限制。
    公开接口（如 /api/health）不受限流影响。
    """
    
    # 不受限流的路径
    EXEMPT_PATHS = [
        "/api/health",
        "/api/docs",
        "/api/redoc",
        "/login",
        "/",
        "/static",
    ]
    
    # 不同路径的限流配置
    PATH_LIMITS = {
        "/api/chat": {"requests_per_minute": 30, "burst_size": 5},
        "/api/mcp": {"requests_per_minute": 30, "burst_size": 5},
        "/api/auth/login": {"requests_per_minute": 10, "burst_size": 3},
        "/api/privilege": {"requests_per_minute": 20, "burst_size": 5},
    }
    
    def __init__(
        self,
        app,
        default_requests_per_minute: int = 60,
        default_requests_per_hour: int = 1000,
        default_burst_size: int = 10,
    ):
        super().__init__(app)
        self.default_config = {
            "requests_per_minute": default_requests_per_minute,
            "requests_per_hour": default_requests_per_hour,
            "burst_size": default_burst_size,
        }
        
        # 为不同路径创建独立的限流器
        self._limiters: Dict[str, RateLimiter] = {}
        self._default_limiter = RateLimiter(**self.default_config)
    
    def _get_limiter(self, path: str) -> RateLimiter:
        """获取对应路径的限流器"""
        for path_prefix, config in self.PATH_LIMITS.items():
            if path.startswith(path_prefix):
                if path_prefix not in self._limiters:
                    self._limiters[path_prefix] = RateLimiter(
                        requests_per_hour=self.default_config["requests_per_hour"],
                        **config
                    )
                return self._limiters[path_prefix]
        return self._default_limiter
    
    def _get_client_key(self, request: Request) -> str:
        """获取客户端标识键"""
        # 优先使用用户身份
        user = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            # 从 session 获取用户（如果已认证）
            # 这里简化处理，使用 token 的前 16 位作为标识
            token = auth_header[7:23]
            user = f"user:{token}"
        
        # 其次使用 IP 地址
        client_ip = request.client.host if request.client else "unknown"
        
        # 代理场景下获取真实 IP
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
        
        return user or f"ip:{client_ip}"
    
    def _is_exempt(self, path: str) -> bool:
        """检查路径是否不受限流"""
        for exempt_path in self.EXEMPT_PATHS:
            if path.startswith(exempt_path):
                return True
        return False
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """处理请求限流"""
        path = request.url.path
        
        # 跳过不受限流的路径
        if self._is_exempt(path):
            return await call_next(request)
        
        # 获取限流器和客户端标识
        limiter = self._get_limiter(path)
        key = self._get_client_key(request)
        now = time.time()
        
        # 检查限流
        allowed, reason, details = limiter.check_rate_limit(key, now)
        
        if not allowed:
            # 返回 429 Too Many Requests
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limit_exceeded",
                    "reason": reason,
                    "message": "请求频率超过限制，请稍后再试",
                    "details": details,
                },
                headers={
                    "Retry-After": str(details.get("retry_after", 60)),
                    "X-RateLimit-Limit": str(details.get("limit", 0)),
                    "X-RateLimit-Remaining": "0",
                }
            )
        
        # 添加限流信息到响应头
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limiter.requests_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(details.get("minute_remaining", 0))
        
        return response
    
    def get_stats(self) -> Dict:
        """获取所有限流器的统计信息"""
        stats = {
            "default": self._default_limiter.get_stats(),
            "paths": {},
        }
        for path, limiter in self._limiters.items():
            stats["paths"][path] = limiter.get_stats()
        return stats
    
    def reset_stats(self):
        """重置所有限流器的统计信息"""
        self._default_limiter.reset_stats()
        for limiter in self._limiters.values():
            limiter.reset_stats()