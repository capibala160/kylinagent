# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 语言设置
- 始终使用中文（简体）回复所有问题、解释和输出
- 代码注释使用中文
- 提交信息使用中文

## Development commands

### Backend (Python/FastAPI)

```bash
cd backend

# Create venv and install
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Start dev server (with hot reload)
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# Or use the wrapper script:
./run.sh

# Run all tests
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_unit.py -v

# Run a single test function
python -m pytest tests/test_unit.py::TestValidateIntent::test_safe_input_passes -v
```

### Frontend (Vite)

```bash
cd frontend
npm install
npm run dev      # Vite dev server
npm run build    # Production build
```

In production, the FastAPI server serves the built frontend from `frontend/` as static files. No separate frontend server is needed.

### Database

SQLite database at `backend/data/ops_agent.db`. Migrations managed by Alembic:

```bash
cd backend
alembic upgrade head
alembic revision --autogenerate -m "description"
```

## Architecture

**Kylin Safe Ops Agent** — a safety-oriented intelligent ops agent for Kylin OS built on MCP (Model Context Protocol). Users ask for ops tasks in natural language; the agent reasons, picks tools, checks safety, executes, and reports results.

### Request flow (chat)

```
User input → SecurityGuard.validate_intent()  [prompt injection? malicious?]
          → IntentRouter.route()              [chitchat vs ops? chitchat → direct LLM reply]
          → LLM.chat_completion()             [decide which tools to call, returns JSON]
          → parse_tool_calls()                [extract tool+args from LLM response]
          → SecurityGuard.validate_command()  [per-tool risk: SAFE/LOW/MEDIUM/HIGH/CRITICAL]
          → _execute_tool()                   [via MCPClient → MCPServer → ToolRegistry]
          → summarize + return
          → AuditLogger writes chain to JSONL
```

Every request creates a `ReasoningChain` that records each node type (intent_received → security_check → reasoning → tool_call → result) and is persisted to `backend/logs/audit/chain_*.jsonl`.

### Security model (3 layers)

1. **Intent layer** (`SecurityGuard.validate_intent`): Regex matching against prompt injection patterns (Chinese + English), malicious intent patterns, and dangerous operation descriptions.
2. **Command layer** (`SecurityGuard.validate_command`): Rule engine (`RiskLevel` enum) checks against dangerous command patterns from `config/agent.yaml`. MCP tools also get parameter-level checks (e.g., `read_file` can't access `/etc/shadow`, `stop_service` can't touch `sshd`).
3. **Execution layer** (`PrivilegeExecutor`): Commands can run as a restricted `opsagent` user. Root privilege requires an approval workflow stored in the `privilege_requests` DB table.

Risk levels: SAFE → execute directly; LOW → allowed; MEDIUM/HIGH → requires user confirmation; CRITICAL → blocked absolutely.

### LLM client

`LLMClient` wraps any OpenAI-compatible API. When the model service is unreachable (connection error or 404/502/503), it auto-flags `_fallback_active = True` and delegates to `MockLLMClient`. The mock uses keyword matching to select tools and returns proper JSON tool-call responses. It also handles chitchat vs ops classification — if the user says "hello", it returns a conversational reply directly without tool calls.

### MCP protocol layer

`MCPServer` implements a JSON-RPC 2.0 endpoint at `POST /api/mcp` supporting three standard methods: `initialize`, `tools/list`, `tools/call`. The Agent internally uses `MCPClient → MCPServer → ToolRegistry` to execute tools, but the `/api/chat` endpoint is the primary user-facing interface.

### Tool plugins (`backend/app/mcp/tools/`)

Each tool module registers one or more `BaseTool` subclasses via `@register_tool`. The registry auto-discovers tools when modules are imported. Tools wrap system commands using `subprocess` / `psutil` and return `ToolCallResult` with `TextContent`. The 7 modules are: `system`, `process`, `network`, `disk`, `file`, `service`, `diagnose`.

### Database (`backend/app/db.py`)

SQLite with SQLAlchemy async + aiosqlite. Tables: `users`, `sessions`, `chat_sessions`, `chat_messages`, `privilege_requests`, `verification_codes`. All CRUD is in `db.py` as standalone async functions (not a DAO class). The DB is initialized on startup in `main.py`.

### Auth

Cookie-based session auth. Login creates a session (UUID SID) stored in the `sessions` table; the SID is set as an `ops_session` HttpOnly cookie. A `get_current_user` FastAPI dependency verifies it on protected routes. Supports password login, verification-code login (email/phone), and registration.

### Frontend

Vanilla JS SPA (no framework). `index.html` is the main chat interface; `login.html` handles auth. Key modules in `js/`: `api.js` (fetch wrapper with 401 redirect), `chat.js` (message rendering, SSE streaming), `approvals.js` (confirm/reject risky ops), `storage.js` (localStorage for session persistence). Uses `marked.js` for Markdown rendering and `DOMPurify` for XSS sanitization.

### Key config (`config/agent.yaml`)

- `llm.api_base` / `llm.api_key` — point to any OpenAI-compatible endpoint
- `security.dangerous_commands` — regex patterns that get CRITICAL risk
- `security.confirm_required_patterns` — regex patterns that trigger MEDIUM/HIGH (require confirm)
- `security.allowed_command_prefixes` — whitelist for raw shell commands
- `auth.session_ttl` — session duration in seconds (default 30 days)

Config is loaded once and cached; call `get_config(force_reload=True)` for hot reload.
