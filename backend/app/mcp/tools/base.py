import time
import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from ..schema import Tool, ToolCallRequest, ToolCallResult, TextContent


class BaseTool(ABC):
    """所有运维工具的基类"""
    
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}
    
    def get_tool_definition(self) -> Tool:
        # 只有没有 default 的参数才是 required
        required = [k for k, v in self.parameters.items() if "default" not in v]
        return Tool(
            name=self.name,
            description=self.description,
            inputSchema={
                "type": "object",
                "properties": self.parameters,
                "required": required
            }
        )
    
    @abstractmethod
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        pass
    
    async def safe_execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        start_time = time.time()
        try:
            result = await self.execute(arguments)
            result.execution_time_ms = (time.time() - start_time) * 1000
            return result
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"执行出错: {str(e)}")],
                isError=True,
                errorMessage=str(e),
                execution_time_ms=(time.time() - start_time) * 1000
            )


class ToolRegistry:
    """工具注册中心"""
    
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
    
    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool
    
    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)
    
    def list_tools(self) -> List[Tool]:
        return [tool.get_tool_definition() for tool in self._tools.values()]
    
    def list_tool_names(self) -> List[str]:
        return list(self._tools.keys())


# 全局注册中心
_registry = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _registry


def register_tool(tool: BaseTool):
    _registry.register(tool)
