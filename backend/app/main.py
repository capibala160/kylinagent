import sys
from pathlib import Path

# 确保可以导入 app 模块
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from app.config import get_config
from app.api.routes import router
from app.middleware import RateLimitMiddleware, RequestMonitorMiddleware
from app.middleware.monitor import RequestMonitor

config = get_config()

app = FastAPI(
    title=config.agent.name,
    description=config.agent.description,
    version=config.agent.version,
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# 请求监控中间件（最先添加，确保所有请求都被监控）
# request_monitor 被 api/monitor.py 导入使用
request_monitor = RequestMonitor()
app.add_middleware(RequestMonitorMiddleware, monitor=request_monitor)

# API 限流中间件（通过共享限流器统计，monitor.py 不再依赖模块级实例）
_rate_limit_config = dict(
    default_requests_per_minute=60,
    default_requests_per_hour=1000,
    default_burst_size=10,
)
app.add_middleware(RateLimitMiddleware, **_rate_limit_config)

# CORS 配置
# 生产环境必须显式配置 CORS_ORIGINS，否则默认不允许任何跨域来源，
# 防止在共享/容器环境或 DNS rebinding 场景下凭证泄露。
import os
_cors_env = os.environ.get("CORS_ORIGINS", "")
if _cors_env.strip():
    # 生产环境：严格限制为配置的域名
    cors_origins = [origin.strip() for origin in _cors_env.split(",") if origin.strip()]
else:
    # 未配置时默认不允许任何跨域来源，开发环境请设置 CORS_ORIGINS
    cors_origins = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# API 路由
app.include_router(router, prefix="/api", tags=["agent"])

# 静态文件（前端）
frontend_dir = Path(__file__).parent.parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """登录页入口"""
    return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="0; url=/static/login.html">
</head>
<body>
    <p>正在跳转到登录页面...</p>
    <p>如果没有自动跳转，请 <a href="/static/login.html">点击这里</a></p>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def root():
    """重定向到前端页面"""
    html_content = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Kylin Safe Ops Agent</title>
    <meta http-equiv="refresh" content="0; url=/static/index.html">
</head>
<body>
    <p>正在跳转到运维 Agent 控制台...</p>
    <p>如果没有自动跳转，请 <a href="/static/index.html">点击这里</a></p>
</body>
</html>"""
    return html_content


@app.on_event("startup")
async def startup_event():
    """应用启动时初始化资源"""
    # 初始化数据库
    from app.db import init_db
    await init_db()
    
    # 初始化默认管理员账号
    from app.auth.session import get_session_auth
    auth = get_session_auth()
    await auth.init_default_user()
    
    print("[OK] 启动完成: 数据库已初始化，默认管理员已创建")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时清理资源"""
    from app.api.routes import get_agent
    agent = get_agent()
    await agent.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
