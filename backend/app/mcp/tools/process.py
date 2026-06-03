import subprocess
import os
from typing import Any, Dict
from .base import BaseTool, register_tool
from ..schema import ToolCallResult, TextContent


class ProcessListTool(BaseTool):
    name = "list_processes"
    description = "列出系统进程，支持按 CPU 或内存排序，可限制返回数量"
    parameters = {
        "sort_by": {
            "type": "string",
            "description": "排序方式: cpu 或 mem，默认 cpu",
            "enum": ["cpu", "mem"]
        },
        "limit": {
            "type": "integer",
            "description": "返回的最大进程数，默认 20",
            "default": 20
        }
    }
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        sort_by = arguments.get("sort_by", "cpu")
        limit = arguments.get("limit", 20)
        
        try:
            if sort_by == "cpu":
                cmd = ["ps", "aux", "--sort=-%cpu"]
            else:
                cmd = ["ps", "aux", "--sort=-%mem"]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            lines = result.stdout.strip().split("\n")
            
            # 保留表头 + 前 limit 行
            header = lines[0] if lines else ""
            data_lines = lines[1:limit+1] if len(lines) > 1 else []
            
            text = f"进程列表 (按 {sort_by} 排序, 前 {limit} 个):\n{header}\n" + "\n".join(data_lines)
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取进程列表失败: {str(e)}")],
                isError=True
            )


class ProcessDetailTool(BaseTool):
    name = "get_process_detail"
    description = "获取指定 PID 的进程详细信息，包括打开的文件、环境变量等"
    parameters = {
        "pid": {
            "type": "integer",
            "description": "进程 PID"
        }
    }
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        pid = arguments.get("pid")
        if not pid:
            return ToolCallResult(
                content=[TextContent(type="text", text="错误: 必须提供 pid 参数")],
                isError=True
            )
        
        try:
            # 进程状态
            status_result = subprocess.run(["cat", f"/proc/{pid}/status"], 
                                          capture_output=True, text=True, timeout=5)
            status = status_result.stdout if status_result.returncode == 0 else "无法读取进程状态"
            
            # 打开的文件：优先 lsof，fallback 到 /proc/{pid}/fd
            lsof = ""
            lsof_result = subprocess.run(["lsof", "-p", str(pid)], 
                                        capture_output=True, text=True, timeout=10)
            if lsof_result.returncode == 0:
                lsof = lsof_result.stdout[:3000]
            else:
                # fallback: 读取 /proc/{pid}/fd
                try:
                    fd_dir = f"/proc/{pid}/fd"
                    if os.path.isdir(fd_dir):
                        fds = os.listdir(fd_dir)
                        fd_links = []
                        for fd in sorted(fds)[:50]:
                            try:
                                target = os.readlink(os.path.join(fd_dir, fd))
                                fd_links.append(f"{fd} -> {target}")
                            except Exception:
                                fd_links.append(f"{fd} -> ?")
                        lsof = f"/proc/{pid}/fd 打开的文件描述符 (Top 50):\n" + "\n".join(fd_links)
                    else:
                        lsof = "无法获取打开文件列表（lsof 不可用且 /proc/{pid}/fd 不可访问）"
                except Exception as e2:
                    lsof = f"无法获取打开文件列表: {str(e2)}"
            
            text = f"""进程 {pid} 详细信息:

--- /proc/{pid}/status ---
{status}

--- 打开的文件 ---
{lsof}
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取进程详情失败: {str(e)}")],
                isError=True
            )


class ZombieProcessTool(BaseTool):
    name = "find_zombie_processes"
    description = "查找系统中的僵尸进程（Zombie/Defunct 进程）"
    parameters = {}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        try:
            # 获取所有进程状态（不使用 shell=True，避免命令注入）
            result = subprocess.run(
                ["ps", "aux"],
                capture_output=True, text=True, timeout=10
            )
            # 在 Python 中过滤包含 Z 的进程
            zombie_lines = []
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    # 排除表头，匹配状态列包含 Z 的进程
                    parts = line.split()
                    if len(parts) >= 8 and 'Z' in parts[7]:
                        zombie_lines.append(line)
            
            # 精确查找：只取 stat 以 Z 开头的进程
            result2 = subprocess.run(
                ["ps", "-eo", "pid,ppid,stat,comm"],
                capture_output=True, text=True, timeout=10
            )
            exact_zombies = []
            if result2.returncode == 0:
                for line in result2.stdout.strip().split("\n")[1:]:
                    parts = line.split()
                    if len(parts) >= 3 and parts[2].startswith("Z"):
                        exact_zombies.append(line)
            
            # 统计僵尸进程数量
            zombie_count = subprocess.run(
                ["ps", "-eo", "stat"],
                capture_output=True, text=True, timeout=5
            )
            count = 0
            if zombie_count.returncode == 0:
                for line in zombie_count.stdout.strip().split("\n")[1:]:
                    if line.strip().startswith("Z"):
                        count += 1
            
            text = f"""僵尸进程数量: {count}

僵尸进程列表 (精确匹配):
{"\n".join(exact_zombies) if exact_zombies else '无'}

相关进程信息:
{"\n".join(zombie_lines[:20]) if zombie_lines else '无'}
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"查找僵尸进程失败: {str(e)}")],
                isError=True
            )


class KillProcessTool(BaseTool):
    name = "kill_process"
    description = "终止指定 PID 的进程。注意：此操作属于高风险操作，需要二次确认"
    parameters = {
        "pid": {
            "type": "integer",
            "description": "要终止的进程 PID"
        },
        "signal": {
            "type": "string",
            "description": "发送的信号，默认 SIGTERM (15)",
            "enum": ["SIGTERM", "SIGKILL", "SIGINT"],
            "default": "SIGTERM"
        }
    }
    
    # 禁止终止的系统关键进程 PID
    PROTECTED_PIDS = {1, 2}  # init, kthreadd
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        pid = arguments.get("pid")
        signal = arguments.get("signal", "SIGTERM")
        
        if not pid:
            return ToolCallResult(
                content=[TextContent(type="text", text="错误: 必须提供 pid 参数")],
                isError=True
            )
        
        # PID 范围校验
        if not isinstance(pid, int) or pid <= 0:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"错误: PID 必须是正整数，收到 {pid}")],
                isError=True
            )
        
        # 保护系统关键进程
        if pid in self.PROTECTED_PIDS:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"安全限制: 禁止终止系统关键进程 PID {pid}")],
                isError=True
            )
        
        sig_map = {"SIGTERM": "-15", "SIGKILL": "-9", "SIGINT": "-2"}
        sig_flag = sig_map.get(signal, "-15")
        
        try:
            result = subprocess.run(
                ["kill", sig_flag, str(pid)],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                text = f"成功发送 {signal} 信号到进程 {pid}"
            else:
                text = f"终止进程失败: {result.stderr}"
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"终止进程出错: {str(e)}")],
                isError=True
            )


register_tool(ProcessListTool())
register_tool(ProcessDetailTool())
register_tool(ZombieProcessTool())
register_tool(KillProcessTool())
