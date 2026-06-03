"""系统服务与安全管理工具——增强 OS 环境感知能力

覆盖：服务状态、定时任务、SELinux、开放端口、用户管理等维度
"""

import subprocess
import os
from typing import Any, Dict
from .base import BaseTool, register_tool
from ..schema import ToolCallResult, TextContent


class ServiceListTool(BaseTool):
    name = "list_services"
    description = "列出系统 systemd 服务状态，包括运行中、失败、禁用等服务"
    parameters = {
        "state": {
            "type": "string",
            "description": "状态过滤: running/failed/inactive/all，默认 all",
            "enum": ["running", "failed", "inactive", "all"],
            "default": "all"
        },
        "limit": {
            "type": "integer",
            "description": "最大返回数量，默认 50",
            "default": 50
        }
    }
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        state = arguments.get("state", "all")
        limit = arguments.get("limit", 50)
        
        try:
            if state == "running":
                cmd = ["systemctl", "list-units", "--type=service", "--state=running", "--no-pager", "-n", str(limit)]
            elif state == "failed":
                cmd = ["systemctl", "list-units", "--type=service", "--state=failed", "--no-pager", "-n", str(limit)]
            elif state == "inactive":
                cmd = ["systemctl", "list-units", "--type=service", "--state=inactive", "--no-pager", "-n", str(limit)]
            else:
                cmd = ["systemctl", "list-units", "--type=service", "--no-pager", "-n", str(limit)]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0 and "systemctl" in result.stderr.lower():
                # systemd 不可用（如容器环境）
                return ToolCallResult(
                    content=[TextContent(type="text", text=f"systemd 不可用（当前环境可能不支持 systemd）:\n{result.stderr}")],
                    isError=True
                )
            
            output = result.stdout if result.returncode == 0 else result.stderr
            
            # 同时获取失败服务的摘要
            failed_summary = ""
            if state in ("failed", "all"):
                failed_result = subprocess.run(
                    ["systemctl", "--failed", "--no-pager"],
                    capture_output=True, text=True, timeout=10
                )
                if failed_result.returncode == 0:
                    failed_summary = f"\n\n失败服务摘要:\n{failed_result.stdout}"
            
            text = f"系统服务列表 (状态: {state}):\n{output}{failed_summary}"
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取服务列表失败: {str(e)}")],
                isError=True
            )


class CronJobTool(BaseTool):
    name = "get_cron_jobs"
    description = "获取系统定时任务（crontab/cron.d），包括用户级和系统级定时任务"
    parameters = {}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        try:
            # 系统级 cron
            cron_files = ["/etc/crontab", "/etc/cron.d/0hourly"]
            system_cron = ""
            for cf in cron_files:
                if os.path.exists(cf):
                    try:
                        with open(cf, "r", encoding="utf-8", errors="replace") as f:
                            system_cron += f"\n--- {cf} ---\n" + f.read()
                    except Exception:
                        pass
            
            # /etc/cron.d 目录
            cron_d = ""
            if os.path.isdir("/etc/cron.d"):
                result = subprocess.run(
                    ["ls", "-la", "/etc/cron.d/"],
                    capture_output=True, text=True, timeout=5
                )
                cron_d = f"\n--- /etc/cron.d/ ---\n{result.stdout if result.returncode == 0 else ''}"
            
            # 当前用户 crontab
            user_cron = subprocess.run(
                ["crontab", "-l"],
                capture_output=True, text=True, timeout=5
            )
            user_cron_text = user_cron.stdout if user_cron.returncode == 0 else "当前用户无 crontab"
            
            # cron.allow / cron.deny
            access_control = ""
            for f in ["/etc/cron.allow", "/etc/cron.deny"]:
                if os.path.exists(f):
                    try:
                        with open(f, "r", encoding="utf-8", errors="replace") as fobj:
                            access_control += f"\n--- {f} ---\n" + fobj.read()
                    except Exception:
                        pass
            
            text = f"""用户级定时任务:
{user_cron_text}

系统级定时任务:
{system_cron}
{cron_d}

访问控制:
{access_control if access_control else '无 cron.allow/deny 配置'}
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取定时任务失败: {str(e)}")],
                isError=True
            )


class SELinuxStatusTool(BaseTool):
    name = "get_selinux_status"
    description = "获取 SELinux 安全状态（麒麟系统常用），包括模式、策略、上下文标签"
    parameters = {}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        try:
            # SELinux 状态
            status = subprocess.run(
                ["getenforce"],
                capture_output=True, text=True, timeout=5
            )
            if status.returncode != 0:
                # SELinux 未安装
                text = "SELinux 未安装或未启用（麒麟系统可能使用其他安全模块）"
                return ToolCallResult(content=[TextContent(type="text", text=text)])
            
            mode = status.stdout.strip()
            
            # 详细状态
            detail = subprocess.run(
                ["sestatus"],
                capture_output=True, text=True, timeout=5
            )
            detail_text = detail.stdout if detail.returncode == 0 else "sestatus 不可用"
            
            # 当前进程的安全上下文
            ps_context = subprocess.run(
                ["ps", "-eo", "pid,comm,label"],
                capture_output=True, text=True, timeout=10
            )
            context_lines = ps_context.stdout.strip().split("\n")[:20] if ps_context.returncode == 0 else []
            
            text = f"""SELinux 状态:
当前模式: {mode}

详细信息:
{detail_text}

部分进程安全上下文 (Top 20):
{"\n".join(context_lines)}
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取 SELinux 状态失败: {str(e)}")],
                isError=True
            )


class OpenPortsTool(BaseTool):
    name = "get_open_ports"
    description = "扫描系统所有监听的端口及对应进程，识别异常开放端口"
    parameters = {}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        try:
            # ss 命令获取所有监听端口
            result = subprocess.run(
                ["ss", "-tunapl", "state", "listening"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                # fallback 到 netstat
                result = subprocess.run(
                    ["netstat", "-tunapl"],
                    capture_output=True, text=True, timeout=10
                )
            
            listeners = result.stdout if result.returncode == 0 else "无法获取端口列表"
            
            text = f"""系统监听端口详情:
{listeners}

提示: 关注未知/异常端口，特别是非系统服务监听的高位端口。
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取开放端口失败: {str(e)}")],
                isError=True
            )


class UserListTool(BaseTool):
    name = "get_user_list"
    description = "获取系统用户列表，包括登录用户、系统用户、最近活跃用户"
    parameters = {}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        try:
            # 当前登录用户
            who = subprocess.run(["who"], capture_output=True, text=True, timeout=5)
            who_text = who.stdout if who.returncode == 0 else "无用户登录"
            
            # 所有用户（/etc/passwd）
            passwd = subprocess.run(
                ["getent", "passwd"],
                capture_output=True, text=True, timeout=5
            )
            # 只显示 UID >= 1000 的普通用户
            normal_users = []
            system_users = []
            if passwd.returncode == 0:
                for line in passwd.stdout.strip().split("\n"):
                    parts = line.split(":")
                    if len(parts) >= 3:
                        try:
                            uid = int(parts[2])
                            if uid >= 1000 and uid != 65534:
                                normal_users.append(f"{parts[0]} (UID:{uid})")
                            elif uid >= 1:
                                system_users.append(parts[0])
                        except ValueError:
                            pass
            
            text = f"""当前登录用户:
{who_text}

普通用户 (UID>=1000):
{"\n".join(normal_users) if normal_users else "无"}

系统用户数量: {len(system_users)}
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取用户列表失败: {str(e)}")],
                isError=True
            )


# 注册工具
register_tool(ServiceListTool())
register_tool(CronJobTool())
register_tool(SELinuxStatusTool())
register_tool(OpenPortsTool())
register_tool(UserListTool())
