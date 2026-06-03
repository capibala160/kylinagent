# Kylin Safe Ops Agent —— 安装部署文档

## 一、环境要求

| 项目 | 最低配置 | 推荐配置 |
|------|---------|---------|
| CPU | LoongArch 4核 | 8核及以上 |
| 内存 | 4GB | 8GB及以上 |
| 磁盘 | 20GB | 50GB及以上 |
| 操作系统 | 麒麟高级服务器版 V11 (kylin v10sp3 兼容) | - |
| Python | 3.9+ | 3.11 |
| 网络 | 内网可达 | 可访问 LLM API（可选） |

## 二、前置检查

```bash
# 检查 Python 版本
python3 --version

# 检查系统架构
uname -m

# 检查操作系统版本
cat /etc/os-release

# 安装基础依赖（如有需要）
sudo yum install -y python3 python3-pip python3-venv
```

## 三、一键安装

```bash
# 进入项目目录
cd kylin-ops-agent

# 添加执行权限并运行部署脚本
chmod +x deploy.sh
./deploy.sh
```

部署脚本会自动完成：环境检查、依赖安装、服务启动、健康检查。

## 四、手动安装

### 4.1 安装后端依赖

```bash
cd backend

# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 创建必要目录
mkdir -p logs/audit
mkdir -p data
```

### 4.2 配置大模型（可选）

编辑 `config/agent.yaml`：

```yaml
llm:
  provider: "openai_compatible"
  api_base: "https://api.deepseek.com/v1"  # 或其他兼容 API
  api_key: "sk-your-api-key-here"
  model: "deepseek-chat"
```

**注意**：如果比赛环境没有 LLM API，保持默认配置即可，系统会自动回退到 Mock 模式，支持所有运维测试用例。

### 4.3 启动服务

```bash
cd backend
source venv/bin/activate

# 方式一：开发模式（带热重载）
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 方式二：生产模式
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 方式三：后台运行
nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > logs/server.log 2>&1 &
```

## 五、验证部署

### 5.1 健康检查

```bash
curl http://localhost:8000/api/health
```

预期响应：

```json
{
  "status": "healthy",
  "agent_name": "KylinSafeOpsAgent",
  "version": "1.0.0",
  "tools_count": 26,
  "llm_configured": true
}
```

### 5.2 访问 Web 界面

浏览器访问 `http://<服务器IP>:8000`

默认管理员账号：
- 用户名：`opsadmin`
- 密码：`KylinOps@2024`

## 六、systemd 服务部署（可选）

```bash
sudo tee /etc/systemd/system/kylin-ops-agent.service <<EOF
[Unit]
Description=Kylin Safe Ops Agent
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/kylin-ops-agent/backend
Environment="PATH=/opt/kylin-ops-agent/backend/venv/bin"
ExecStart=/opt/kylin-ops-agent/backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable kylin-ops-agent
sudo systemctl start kylin-ops-agent
```

## 七、常见问题

### Q1: 启动时报 "ModuleNotFoundError"

确保激活了虚拟环境：

```bash
cd backend
source venv/bin/activate
```

### Q2: LLM 调用失败

检查 `config/agent.yaml` 中的 API 配置，确认网络可达：

```bash
curl <api_base>/models -H "Authorization: Bearer <api_key>"
```

无 LLM 时系统会自动使用 Mock 模式。

### Q3: 前端页面无法访问

确认前端文件存在于 `frontend/` 目录，且服务已正确启动。

### Q4: 端口被占用

```bash
# 查找占用 8000 端口的进程
ss -tlnp | grep :8000

# 终止进程
kill <PID>
```

## 八、卸载

```bash
# 停止服务
sudo systemctl stop kylin-ops-agent 2>/dev/null || true

# 删除项目目录
sudo rm -rf /opt/kylin-ops-agent

# 删除 systemd 服务
sudo rm -f /etc/systemd/system/kylin-ops-agent.service
sudo systemctl daemon-reload
```
