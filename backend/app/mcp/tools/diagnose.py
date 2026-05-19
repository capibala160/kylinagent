"""智能根因诊断工具——将 analysis 模块封装为 MCP Tools

这些工具不仅返回原始系统数据，还会进行智能化根因分析，
直接告诉用户"为什么出问题"以及"该怎么办"。
"""

import asyncio
from typing import Any, Dict

from .base import BaseTool, register_tool
from ..schema import ToolCallResult, TextContent
from ...analysis import RootCauseAnalyzer


class DiagnoseDiskTool(BaseTool):
    name = "diagnose_disk"
    description = (
        "智能诊断磁盘空间问题。不仅返回 df/du 数据，还会自动分析空间占用的根因 "
        "（如日志膨胀、包缓存、core dump、临时文件等），并给出修复建议。"
    )
    parameters = {
        "mountpoint": {
            "type": "string",
            "description": "要诊断的挂载点，默认 /",
            "default": "/",
        }
    }

    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        mountpoint = arguments.get("mountpoint", "/")
        analyzer = RootCauseAnalyzer()
        result = await analyzer.diagnose_disk(mountpoint)
        text = analyzer.format_result_for_user(result)
        return ToolCallResult(content=[TextContent(type="text", text=text)])


class DiagnoseProcessTool(BaseTool):
    name = "diagnose_process"
    description = (
        "智能诊断进程问题。自动检测僵尸进程、高 CPU 进程、高内存进程， "
        "分析根因（如父进程异常、内存泄漏、服务 Fork 过多），并给出处理建议。"
    )
    parameters = {}

    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        analyzer = RootCauseAnalyzer()
        result = await analyzer.diagnose_process()
        text = analyzer.format_result_for_user(result)
        return ToolCallResult(content=[TextContent(type="text", text=text)])


class DiagnosePerformanceTool(BaseTool):
    name = "diagnose_performance"
    description = (
        "智能诊断系统性能瓶颈。综合分析 CPU 负载、内存使用、磁盘 IO、Swap 使用， "
        "自动识别瓶颈类型（CPU 过载/内存不足/IO 瓶颈/运行队列积压），并给出优化建议。"
    )
    parameters = {}

    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        analyzer = RootCauseAnalyzer()
        result = await analyzer.diagnose_performance()
        text = analyzer.format_result_for_user(result)
        return ToolCallResult(content=[TextContent(type="text", text=text)])


class DiagnoseServiceLogTool(BaseTool):
    name = "diagnose_service_log"
    description = (
        "智能诊断服务日志异常。自动扫描日志中的错误模式（OOM、段错误、权限不足、 "
        "连接超时等），定位根因并给出修复建议。"
    )
    parameters = {
        "service": {
            "type": "string",
            "description": "服务名，如 sshd、nginx、mysql。留空则扫描系统全部错误日志",
            "default": "",
        }
    }

    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        service = arguments.get("service", "")
        analyzer = RootCauseAnalyzer()
        result = await analyzer.diagnose_service_log(service)
        text = analyzer.format_result_for_user(result)
        return ToolCallResult(content=[TextContent(type="text", text=text)])


class ComprehensiveDiagnosisTool(BaseTool):
    name = "comprehensive_diagnosis"
    description = (
        "综合智能诊断。根据用户的自然语言描述，自动判断需要诊断的维度 "
        "（磁盘/进程/性能/日志），执行多维度联合分析，生成一份完整的根因分析报告。"
    )
    parameters = {
        "query": {
            "type": "string",
            "description": "用户的自然语言描述，如'系统很慢'、'帮我检查系统问题'、'nginx 启动失败'",
        }
    }

    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        query = arguments.get("query", "")
        if not query:
            return ToolCallResult(
                content=[TextContent(type="text", text="错误: 必须提供 query 参数")],
                isError=True,
            )
        analyzer = RootCauseAnalyzer()
        report = await analyzer.comprehensive_diagnosis(query, session_id="mcp_tool")
        text = analyzer.format_report_for_user(report)
        return ToolCallResult(content=[TextContent(type="text", text=text)])


# 注册工具
register_tool(DiagnoseDiskTool())
register_tool(DiagnoseProcessTool())
register_tool(DiagnosePerformanceTool())
register_tool(DiagnoseServiceLogTool())
register_tool(ComprehensiveDiagnosisTool())
