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

config = get_config()

app = FastAPI(
    title=config.agent.name,
    description=config.agent.description,
    version=config.agent.version,
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# CORS 配置
# 生产环境应限制为特定域名，避免凭证泄露
# 默认允许本地开发环境
import os
cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
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


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时清理资源"""
    from app.api.routes import get_agent
    agent = get_agent()
    await agent.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
