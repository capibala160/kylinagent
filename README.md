# Kylin Safe Ops Agent

面向麒麟操作系统的安全智能运维 Agent，基于 MCP (Model Context Protocol) 协议构建，集成大模型推理能力与多层安全护栏。

## 核心特性

- 🧠 **自然语言运维**：通过对话方式执行系统运维任务
- 🛡️ **多层安全护栏**：意图风险过滤 + 命令风险识别 + 最小权限执行
- 🔍 **OS 深度感知**：自动调用底层工具获取进程、网络、日志等实时上下文
- 🧩 **MCP 插件化架构**：常用运维动作封装为可复用工具
- 📋 **推理链路溯源**：完整记录"接收指令 → 感知环境 → 推理决策 → 安全校验 → 执行结果"闭环
- ⚡ **B/S 架构**：Web 界面交互，支持国产 LoongArch 架构 + 麒麟服务器 V11

## 比赛环境快速部署

### 一键部署（推荐）

```bash
chmod +x deploy.sh
./deploy.sh
```

部署脚本会自动完成：环境检查、依赖安装、服务启动、健康检查。

### 手动部署

```bash
cd backend
chmod +x run.sh
./run.sh
```

### 默认账号

- 用户名：`opsadmin`
- 密码：`KylinOps@2024`
- 可通过环境变量修改：`OPS_ADMIN_USER` / `OPS_ADMIN_PASS`

### 关于 LLM 配置

- **有模型服务**：修改 `config/agent.yaml` 中的 `api_base` 和 `api_key`
- **无模型服务**：保持默认即可，系统会自动回退到 **Mock 模式**
- Mock 模式已内置支持所有运维测试用例（ps/df/free/内存/CPU/网络/进程/日志/诊断等）

## 快速开始

### 环境要求

- Python 3.9+
- 麒麟高级服务器版 V11 / 兼容的 Linux 发行版
- 可选：大模型 API 服务（DeepSeek / Qwen 等）

### 安装与启动

```bash
cd backend
chmod +x run.sh
./run.sh
```

服务启动后访问 http://localhost:8000

### 手动启动

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 项目结构

```
kylin-ops-agent/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── config.py            # 配置管理
│   │   ├── agent/
│   │   │   ├── core.py          # Agent 核心编排逻辑
│   │   │   └── state.py         # 会话状态管理
│   │   ├── mcp/
│   │   │   ├── schema.py        # MCP 协议数据结构
│   │   │   ├── server.py        # MCP Server
│   │   │   └── tools/           # 运维工具插件
│   │   │       ├── system.py    # 系统信息工具
│   │   │       ├── process.py   # 进程管理工具
│   │   │       ├── network.py   # 网络诊断工具
│   │   │       ├── disk.py      # 磁盘管理工具
│   │   │       ├── file.py      # 文件日志工具
│   │   │       ├── service.py   # 服务管理工具
│   │   │       └── diagnose.py  # 智能诊断工具
│   │   ├── security/
│   │   │   ├── guard.py         # 安全护栏核心
│   │   │   ├── rules.py         # 风险规则引擎
│   │   │   └── executor.py      # 最小权限执行器
│   │   ├── audit/
│   │   │   ├── chain.py         # 推理链路模型
│   │   │   └── logger.py        # 审计日志记录器
│   │   ├── llm/
│   │   │   ├── client.py        # 大模型客户端
│   │   │   └── prompts.py       # Prompt 模板
│   │   └── api/
│   │       └── routes.py        # REST API 路由
│   ├── requirements.txt
│   └── run.sh
├── frontend/
│   ├── index.html               # Web 界面
│   ├── login.html               # 登录页面
│   ├── style.css
│   ├── app.js
│   ├── marked.min.js
│   └── purify.min.js
├── config/
│   └── agent.yaml               # Agent 配置文件
├── deploy.sh                    # 一键部署脚本（比赛环境）
└── docs/                        # 文档
```

## 安全架构

### 三层安全防线

1. **意图层防护**：检测提示词注入、恶意意图
2. **命令层防护**：基于规则引擎识别危险命令（rm -rf /、mkfs、fork bomb 等）
3. **执行层防护**：最小权限执行（opsagent 用户）、只读命令白名单

### 风险等级

| 等级 | 处理策略 |
|------|---------|
| SAFE | 直接执行 |
| LOW / MEDIUM | 需要二次确认 |
| HIGH | 阻断并告警 |
| CRITICAL | 绝对禁止 |

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/chat` | POST | 核心对话接口 |
| `/api/tools` | GET | 获取可用工具列表 |
| `/api/audit/chains` | GET | 查询审计链路 |
| `/api/audit/chain/{id}` | GET | 获取单条链路详情 |
| `/api/health` | GET | 健康检查 |
| `/api/config` | GET | 获取配置信息 |

## 配置说明

编辑 `config/agent.yaml`：

```yaml
llm:
  provider: "openai_compatible"
  api_base: "http://localhost:8000/v1"
  api_key: "your-api-key"
  model: "deepseek-chat"

security:
  restricted_user: "opsagent"
  dangerous_commands:
    - "^\\s*rm\\s+-rf\\s+/"
```

## 许可证

本项目为比赛作品，参赛团队保留软件著作权。
