"""
MCP (Model Context Protocol) 标准服务端实现

遵循 https://modelcontextprotocol.io/specification 规范，
支持 JSON-RPC 2.0 通信，提供 initialize / tools/list / tools/call 标准方法。

所有工具调用均经过 SecurityGuard 安全校验，体现"安全审计多维Agent"设计。
"""

import json
import time
from typing import Any, Dict, List, Optional

from ..config import get_config
from ..security import SecurityGuard

from .schema import (
    MCPRequest,
    MCPResponse,
    InitializeResult,
    ServerCapability,
    ServerInfo,
    ListToolsResult,
    CallToolResult,
    Tool,
    TextContent,
    MCPError,
)
from .tools import get_registry, BaseTool


# JSON-RPC 2.0 标准错误码
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
MCP_TOOL_NOT_FOUND = -32604
MCP_TOOL_EXECUTION_ERROR = -32605
MCP_SECURITY_BLOCKED = -32606


class MCPServer:
    """
    MCP 标准服务端。

    职责：
    1. 协议握手 (initialize)
    2. 工具发现 (tools/list)
    3. 工具调用 (tools/call) —— 含安全校验
    """

    def __init__(self, tool_registry=None, security_guard=None):
        self.config = get_config()
        self.tool_registry = tool_registry or get_registry()
        self.guard = security_guard or SecurityGuard(
            self.config.security.model_dump()
        )
        self._initialized = False

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------

    async def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理 JSON-RPC 2.0 请求，返回 JSON-RPC 2.0 响应。

        Args:
            request: 原始 JSON-RPC 请求字典

        Returns:
            JSON-RPC 响应字典
        """
        start_time = time.time()

        # 1. 基础格式校验
        if not isinstance(request, dict):
            return self._error_response(None, JSONRPC_INVALID_REQUEST, "请求必须是 JSON 对象")

        jsonrpc = request.get("jsonrpc")
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if jsonrpc != "2.0":
            return self._error_response(req_id, JSONRPC_INVALID_REQUEST, "jsonrpc 必须是 2.0")

        if not method or not isinstance(method, str):
            return self._error_response(req_id, JSONRPC_INVALID_REQUEST, "method 必填")

        # 2. 方法分发
        try:
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method == "tools/list":
                result = self._handle_tools_list(params)
            elif method == "tools/call":
                result = await self._handle_tools_call(params)
            else:
                return self._error_response(
                    req_id, JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}"
                )
        except Exception as e:
            return self._error_response(
                req_id, JSONRPC_INTERNAL_ERROR, f"内部错误: {str(e)}"
            )

        # 3. 构造成功响应
        duration_ms = (time.time() - start_time) * 1000
        # Pydantic V2: 使用 model_dump() 而不是 dict()
        result_data = result if isinstance(result, dict) else result.model_dump()
        response = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result_data,
        }
        # 可选：在 meta 中附加处理耗时
        if isinstance(response["result"], dict):
            response["result"]["_meta"] = {"server_duration_ms": round(duration_ms, 2)}

        return response

    # ------------------------------------------------------------------
    # 方法处理器
    # ------------------------------------------------------------------

    def _handle_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """处理 initialize 请求，返回服务端信息和能力。"""
        self._initialized = True

        result = InitializeResult(
            protocolVersion="2024-11-05",
            capabilities=ServerCapability(tools={"listChanged": False}),
            serverInfo=ServerInfo(
                name=self.config.mcp.server_name,
                version=self.config.mcp.version,
            ),
        )
        return result.model_dump()

    def _handle_tools_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """处理 tools/list 请求，返回所有可用工具列表。"""
        tools = self.tool_registry.list_tools()
        # 将 Tool Pydantic 模型列表转换为字典列表
        tools_data = [t.model_dump() if hasattr(t, 'model_dump') else t for t in tools]
        result = ListToolsResult(tools=tools_data)
        return result.model_dump()

    async def _handle_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理 tools/call 请求，执行指定工具。

        安全校验流程：
        1. 检查工具是否存在
        2. SecurityGuard.validate_command 进行命令级风险评级
        3. 对 HIGH/CRITICAL 级别直接阻断
        4. 执行工具（最小权限由工具内部实现）
        """
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        meta = params.get("meta", {})

        if not tool_name:
            raise ValueError("tools/call 必须提供 name 参数")

        # 1. 查找工具
        tool = self.tool_registry.get(tool_name)
        if not tool:
            return CallToolResult(
                isError=True,
                errorMessage=f"工具 {tool_name} 不存在",
                content=[TextContent(type="text", text=f"工具 {tool_name} 未注册")],
            ).model_dump()

        # 2. 安全校验（命令级风险评级）
        cmd_str = f"{tool_name}({json.dumps(arguments, ensure_ascii=False)})"
        cmd_safe, cmd_reason, cmd_detail = self.guard.validate_command(cmd_str, tool_name, arguments)
        risk_level = cmd_detail.get("risk_level", "safe")

        risk_level_str = risk_level.value if hasattr(risk_level, "value") else str(risk_level).lower()
        if not cmd_safe and risk_level_str in ("critical", "high"):
            # 高危操作阻断
            return CallToolResult(
                isError=True,
                errorMessage=f"[SECURITY BLOCKED] {cmd_reason}",
                content=[TextContent(type="text", text=f"🛡️ 安全护栏拦截: {cmd_reason}")],
            ).model_dump()

        # 3. 执行工具
        exec_start = time.time()
        try:
            result = await tool.safe_execute(arguments)
            exec_duration = (time.time() - exec_start) * 1000
            result.execution_time_ms = exec_duration

            # 在 meta 中附加安全审计信息
            call_result = CallToolResult(
                content=result.content,
                isError=result.isError,
                errorMessage=result.errorMessage,
            )
            resp_dict = call_result.model_dump()
            resp_dict["_meta"] = {
                "security_check": {
                    "risk_level": risk_level,
                    "safe": cmd_safe,
                    "reason": cmd_reason if not cmd_safe else None,
                },
                "execution_time_ms": round(exec_duration, 2),
            }
            return resp_dict

        except Exception as e:
            return CallToolResult(
                isError=True,
                errorMessage=f"工具执行异常: {str(e)}",
                content=[TextContent(type="text", text=f"执行出错: {str(e)}")],
            ).model_dump()

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _error_response(
        req_id: Optional[Any], code: int, message: str, data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """构造 JSON-RPC 错误响应。"""
        resp = {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }
        if data is not None:
            resp["error"]["data"] = data
        return resp
