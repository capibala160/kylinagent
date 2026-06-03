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
            # uptime -p 在老版本或某些系统上不可用，fallback 到 uptime
            uptime = "未知"
            for cmd in [["uptime", "-p"], ["uptime"]]:
                try:
                    uptime_result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                    if uptime_result.returncode == 0:
                        uptime = uptime_result.stdout.strip()
                        break
                except Exception:
                    continue
            
            # 尝试读取 /etc/os-release 获取更详细的系统信息
            os_info = ""
            try:
                with open("/etc/os-release", "r", encoding="utf-8", errors="replace") as f:
                    os_info = f.read()
            except Exception:
                pass
            
            # 尝试读取 /etc/kylin-release
            kylin_info = ""
            try:
                with open("/etc/kylin-release", "r", encoding="utf-8", errors="replace") as f:
                    kylin_info = f.read().strip()
            except Exception:
                pass
            
            text = f"""系统基本信息:
- 系统: {uname.system}
- 节点名: {uname.node}
- 发行版: {uname.version}
- 架构: {uname.machine}
- 内核: {uname.release}
- 运行时间: {uptime}

/etc/kylin-release 内容:
{kylin_info or '无'}

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
                with open("/proc/loadavg", "r", encoding="utf-8") as f:
                    loadavg = f.read().strip()
            except Exception:
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
            # 先尝试带 --level 参数（新版本 dmesg）
            result = subprocess.run(
                ["dmesg", "--level=" + dmesg_level, "--no-pager", "-n", str(limit)],
                capture_output=True, text=True, timeout=15
            )
            output = result.stdout if result.returncode == 0 else ""
            
            if result.returncode != 0:
                # 回退到基础 dmesg + tail
                result2 = subprocess.run(
                    ["dmesg"],
                    capture_output=True, text=True, timeout=15
                )
                if result2.returncode == 0:
                    lines = result2.stdout.strip().split("\n")
                    output = "\n".join(lines[-limit:])
                else:
                    output = f"dmesg 不可用: {result2.stderr}"
            else:
                lines = output.strip().split("\n")
                output = "\n".join(lines[-limit:])
            
            text = f"内核日志 (级别: {level}, 最近 {min(limit, len(output.split(chr(10))))} 条):\n{output}"
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
            output = ""
            header = ""
            
            if record_type == "failed":
                result = subprocess.run(
                    ["lastb", "-n", str(limit)],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode != 0 and "btmp" in result.stderr.lower():
                    output = "无失败登录记录（btmp 日志为空或权限不足）"
                else:
                    output = result.stdout if result.returncode == 0 else result.stderr
                header = "失败登录记录 (lastb):"
            elif record_type == "success":
                result = subprocess.run(
                    ["last", "-n", str(limit)],
                    capture_output=True, text=True, timeout=10
                )
                output = result.stdout if result.returncode == 0 else result.stderr
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
                failed_out = result_failed.stdout
                if result_failed.returncode != 0 and "btmp" in result_failed.stderr.lower():
                    failed_out = "无失败登录记录（btmp 日志为空或权限不足）"
                output = f"--- 成功登录 ---\n{result_success.stdout}\n--- 失败登录 ---\n{failed_out}"
                header = "登录历史记录:"
            
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
