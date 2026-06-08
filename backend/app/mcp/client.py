"""
MCP (Model Context Protocol) 标准客户端实现

Agent 内部通过 MCPClient 以标准 JSON-RPC 2.0 格式调用工具，
实现"Agent → MCP 协议 → 工具"的闭环，而非直接函数调用。

实际为本地调用（不走 HTTP），但保持完整的协议消息格式，
便于审计追踪和协议一致性验证。
"""

import json
import time
from typing import Any, Dict, List, Optional

from .schema import (
    ToolCallResult,
    ToolCallRequest,
    TextContent,
    Tool,
)
from .server import MCPServer


class MCPClient:
    """
    MCP 标准客户端。

    使用方式：
        server = MCPServer(tool_registry)
        client = MCPClient(server)
        result = await client.call_tool("get_memory_info", {})

    内部流程：
        1. 构造标准 JSON-RPC 2.0 请求
        2. 调用 MCPServer.handle_request()（本地调用）
        3. 解析 JSON-RPC 2.0 响应
        4. 返回 ToolCallResult
    """

    def __init__(self, server: MCPServer):
        self.server = server
        self._initialized = False
        self._tools_cache: Optional[List[Tool]] = None
        self._tools_cache_time: float = 0
        self._tools_cache_ttl = 60  # 工具列表缓存 60 秒

    async def initialize(self) -> Dict[str, Any]:
        """
        MCP 协议握手。

        返回服务端信息，标志着 MCP 会话正式开始。
        """
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "kylin-ops-agent", "version": "1.0.0"},
            },
        }
        response = await self.server.handle_request(request)

        if "error" in response:
            raise RuntimeError(f"MCP initialize 失败: {response['error']['message']}")

        self._initialized = True
        return response.get("result", {})

    async def list_tools(self, use_cache: bool = True) -> List[Tool]:
        """
        获取可用工具列表（tools/list）。

        Args:
            use_cache: 是否使用本地缓存（默认 True）
        """
        if use_cache and self._tools_cache is not None:
            if time.time() - self._tools_cache_time < self._tools_cache_ttl:
                return self._tools_cache

        request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {},
        }
        response = await self.server.handle_request(request)

        if "error" in response:
            raise RuntimeError(f"MCP tools/list 失败: {response['error']['message']}")

        result = response.get("result", {})
        tools_raw = result.get("tools", [])
        tools = [Tool(**t) for t in tools_raw]

        self._tools_cache = tools
        self._tools_cache_time = time.time()
        return tools

    async def call_tool(self, name: str, arguments: Dict[str, Any] = None) -> ToolCallResult:
        """
        调用指定工具（tools/call）。

        通过标准 JSON-RPC 2.0 协议调用 MCPServer.handle_request()，
        实现"Agent → MCP 协议 → 工具"的完整协议栈。

        Args:
            name: 工具名称
            arguments: 工具参数

        Returns:
            ToolCallResult，包含执行结果或错误信息
        """
        if arguments is None:
            arguments = {}

        request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
                "meta": {"source": "agent_core"},
            },
        }

        # 通过标准 JSON-RPC 2.0 流程调用 Server（保持协议一致性）
        response = await self.server.handle_request(request)

        if "error" in response:
            # JSON-RPC 层级错误
            error_msg = response["error"].get("message", "未知错误")
            return ToolCallResult(
                isError=True,
                errorMessage=error_msg,
                content=[TextContent(type="text", text=f"MCP 调用失败: {error_msg}")],
            )

        # 解析 JSON-RPC 响应中的 result
        result = response.get("result", {})

        # 解析 tools/call 返回结果
        content_list = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                content_list.append(TextContent(type="text", text=item.get("text", "")))
            else:
                content_list.append(TextContent(type="text", text=str(item)))

        return ToolCallResult(
            content=content_list,
            isError=result.get("isError", False),
            errorMessage=result.get("errorMessage"),
            execution_time_ms=result.get("_meta", {}).get("execution_time_ms"),
        )

    def _next_id(self) -> int:
        """生成递增的请求 ID（仅用于本地调用标识）。"""
        self._req_id = getattr(self, "_req_id", 0) + 1
        return self._req_id

    async def close(self):
        """清理资源（本地调用无网络连接，无需特殊处理）。"""
        self._tools_cache = None
        self._initialized = False
