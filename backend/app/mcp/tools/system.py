import subprocess
import platform
import asyncio
from typing import Any, Dict
from .base import BaseTool, register_tool
from ..schema import ToolCallResult, TextContent


class SystemInfoTool(BaseTool):
    name = "get_system_info"
    description = "获取操作系统基本信息，包括内核版本、发行版、架构、运行时间等"
    parameters = {}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        try:
            uname = platform.uname()
            uptime_result = subprocess.run(["uptime", "-p"], capture_output=True, text=True, timeout=5)
            uptime = uptime_result.stdout.strip() if uptime_result.returncode == 0 else "未知"
            
            # 尝试读取 /etc/os-release 获取更详细的系统信息
            os_info = ""
            try:
                with open("/etc/os-release", "r") as f:
                    os_info = f.read()
            except:
                pass
            
            text = f"""系统基本信息:
- 系统: {uname.system}
- 节点名: {uname.node}
- 发行版: {uname.version}
- 架构: {uname.machine}
- 内核: {uname.release}
- 运行时间: {uptime}

/etc/os-release 内容:
{os_info}
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取系统信息失败: {str(e)}")],
                isError=True
            )


class MemoryInfoTool(BaseTool):
    name = "get_memory_info"
    description = "获取内存使用情况，包括总内存、已用内存、缓存、交换分区等"
    parameters = {}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        try:
            result = subprocess.run(["free", "-h"], capture_output=True, text=True, timeout=5)
            meminfo = result.stdout if result.returncode == 0 else ""
            
            result2 = subprocess.run(["cat", "/proc/meminfo"], capture_output=True, text=True, timeout=5)
            meminfo_detail = result2.stdout if result2.returncode == 0 else ""
            
            text = f"内存使用情况:\n{meminfo}\n\n/proc/meminfo 详情:\n{meminfo_detail[:2000]}"
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取内存信息失败: {str(e)}")],
                isError=True
            )


class CPUInfoTool(BaseTool):
    name = "get_cpu_info"
    description = "获取 CPU 信息，包括型号、核心数、负载等"
    parameters = {}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        try:
            result = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=5)
            cpuinfo = result.stdout if result.returncode == 0 else ""
            
            loadavg = ""
            try:
                with open("/proc/loadavg", "r") as f:
                    loadavg = f.read().strip()
            except:
                pass
            
            text = f"CPU 信息:\n{cpuinfo}\n\n系统负载 (1/5/15分钟): {loadavg}"
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取CPU信息失败: {str(e)}")],
                isError=True
            )


class UptimeTool(BaseTool):
    name = "get_uptime"
    description = "获取系统运行时间和负载"
    parameters = {}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        try:
            result = subprocess.run(["uptime"], capture_output=True, text=True, timeout=5)
            text = result.stdout.strip() if result.returncode == 0 else "获取失败"
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取运行时间失败: {str(e)}")],
                isError=True
            )


class KernelLogTool(BaseTool):
    name = "get_kernel_logs"
    description = "获取内核日志（dmesg），用于诊断硬件、驱动、内核级错误"
    parameters = {
        "level": {
            "type": "string",
            "description": "日志级别过滤: err/warn/info/debug，默认 err",
            "enum": ["err", "warn", "info", "debug"],
            "default": "err"
        },
        "limit": {
            "type": "integer",
            "description": "最大返回行数，默认 100",
            "default": 100
        }
    }
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        level = arguments.get("level", "err")
        limit = arguments.get("limit", 100)
        
        level_map = {"err": "err", "warn": "warn", "info": "info", "debug": "debug"}
        dmesg_level = level_map.get(level, "err")
        
        try:
            result = subprocess.run(
                ["dmesg", "--level=" + dmesg_level, "--no-pager", "-n", str(limit)],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                # 尝试不带 --level 参数（老版本 dmesg）
                result = subprocess.run(
                    ["dmesg", "|", "tail", "-n", str(limit)],
                    capture_output=True, text=True, timeout=15, shell=True
                )
            
            output = result.stdout if result.returncode == 0 else result.stderr
            lines = output.strip().split("\n")[-limit:]
            text = f"内核日志 (级别: {level}, 最近 {len(lines)} 条):\n" + "\n".join(lines)
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取内核日志失败: {str(e)}")],
                isError=True
            )


class LoginHistoryTool(BaseTool):
    name = "get_login_history"
    description = "获取系统登录历史记录（last/lastb），包括成功/失败登录、登录来源 IP 等"
    parameters = {
        "type": {
            "type": "string",
            "description": "记录类型: success(成功)/failed(失败)/all(全部)，默认 all",
            "enum": ["success", "failed", "all"],
            "default": "all"
        },
        "limit": {
            "type": "integer",
            "description": "最大返回条数，默认 30",
            "default": 30
        }
    }
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        record_type = arguments.get("type", "all")
        limit = arguments.get("limit", 30)
        
        try:
            if record_type == "failed":
                result = subprocess.run(
                    ["lastb", "-n", str(limit)],
                    capture_output=True, text=True, timeout=10
                )
                header = "失败登录记录 (lastb):"
            elif record_type == "success":
                result = subprocess.run(
                    ["last", "-n", str(limit)],
                    capture_output=True, text=True, timeout=10
                )
                header = "成功登录记录 (last):"
            else:
                result_success = subprocess.run(
                    ["last", "-n", str(limit)],
                    capture_output=True, text=True, timeout=10
                )
                result_failed = subprocess.run(
                    ["lastb", "-n", str(limit)],
                    capture_output=True, text=True, timeout=10
                )
                output = f"--- 成功登录 ---\n{result_success.stdout}\n--- 失败登录 ---\n{result_failed.stdout}"
                header = "登录历史记录:"
                text = f"{header}\n{output}"
                return ToolCallResult(content=[TextContent(type="text", text=text)])
            
            output = result.stdout if result.returncode == 0 else result.stderr
            text = f"{header}\n{output}"
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取登录历史失败: {str(e)}")],
                isError=True
            )


# 注册工具
register_tool(SystemInfoTool())
register_tool(MemoryInfoTool())
register_tool(CPUInfoTool())
register_tool(UptimeTool())
register_tool(KernelLogTool())
register_tool(LoginHistoryTool())
