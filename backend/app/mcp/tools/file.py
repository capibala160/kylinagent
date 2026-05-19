import subprocess
import os
from typing import Any, Dict
from .base import BaseTool, register_tool
from ..schema import ToolCallResult, TextContent


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "安全地读取文本文件内容，限制读取行数"
    parameters = {
        "path": {
            "type": "string",
            "description": "文件路径"
        },
        "limit": {
            "type": "integer",
            "description": "最大读取行数，默认 200",
            "default": 200
        }
    }
    
    # 敏感文件禁止读取（支持路径遍历防护后的匹配）
    SENSITIVE_PATHS = {
        "/etc/shadow", "/etc/gshadow", "/etc/passwd-", "/etc/shadow-",
        "/etc/ssh/sshd_config", "/etc/sudoers",
    }
    
    def _resolve_path(self, path: str) -> str:
        """解析并规范化路径，防止路径遍历攻击"""
        # 先展开用户目录
        path = os.path.expanduser(path)
        # 获取绝对路径并解析 .. 和符号链接
        abs_path = os.path.abspath(path)
        real_path = os.path.realpath(abs_path)
        return real_path
    
    def _is_path_safe(self, path: str) -> bool:
        """检查路径是否安全（无路径遍历、非敏感文件）"""
        real_path = self._resolve_path(path)
        
        # 检查是否为符号链接（禁止通过符号链接读取敏感文件）
        if os.path.islink(path) or os.path.islink(os.path.dirname(path)):
            # 允许符号链接，但 realpath 已经解析了目标，所以再次检查目标
            pass
        
        # 检查是否为敏感文件
        if real_path in self.SENSITIVE_PATHS:
            return False
        
        # 检查是否在 /proc /sys 等特殊目录下
        for dangerous_prefix in ["/proc/", "/sys/"]:
            if real_path.startswith(dangerous_prefix):
                return False
        
        return True
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        path = arguments.get("path")
        limit = arguments.get("limit", 200)
        
        if not path:
            return ToolCallResult(
                content=[TextContent(type="text", text="错误: 必须提供 path 参数")],
                isError=True
            )
        
        # 路径安全校验
        if not self._is_path_safe(path):
            real_path = self._resolve_path(path)
            return ToolCallResult(
                content=[TextContent(type="text", text=f"安全限制: 禁止访问路径 {real_path}")],
                isError=True
            )
        
        real_path = self._resolve_path(path)
        
        if not os.path.exists(real_path):
            return ToolCallResult(
                content=[TextContent(type="text", text=f"文件不存在: {real_path}")],
                isError=True
            )
        
        if not os.path.isfile(real_path):
            return ToolCallResult(
                content=[TextContent(type="text", text=f"路径不是文件: {real_path}")],
                isError=True
            )
        
        try:
            with open(real_path, "r", encoding="utf-8", errors="replace") as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= limit:
                        break
                    lines.append(line.rstrip())
            
            text = f"文件 {real_path} 内容 (前 {len(lines)} 行):\n" + "\n".join(lines)
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"读取文件失败: {str(e)}")],
                isError=True
            )


class ListDirectoryTool(BaseTool):
    name = "list_directory"
    description = "列出指定目录的内容"
    parameters = {
        "path": {
            "type": "string",
            "description": "目录路径，默认当前目录",
            "default": "."
        }
    }
    
    # 禁止访问的敏感目录
    FORBIDDEN_DIRS = {"/etc/ssh", "/etc/sudoers.d", "/root", "/var/spool/cron"}
    
    def _resolve_path(self, path: str) -> str:
        path = os.path.expanduser(path)
        return os.path.realpath(os.path.abspath(path))
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        path = arguments.get("path", ".")
        real_path = self._resolve_path(path)
        
        # 路径安全检查
        for forbidden in self.FORBIDDEN_DIRS:
            if real_path.startswith(forbidden):
                return ToolCallResult(
                    content=[TextContent(type="text", text=f"安全限制: 禁止访问目录 {real_path}")],
                    isError=True
                )
        
        if not os.path.exists(real_path):
            return ToolCallResult(
                content=[TextContent(type="text", text=f"目录不存在: {real_path}")],
                isError=True
            )
        
        if not os.path.isdir(real_path):
            return ToolCallResult(
                content=[TextContent(type="text", text=f"路径不是目录: {real_path}")],
                isError=True
            )
        
        try:
            # 使用 -- 防止路径被解析为选项
            result = subprocess.run(
                ["ls", "-lah", "--", real_path],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout if result.returncode == 0 else result.stderr
            return ToolCallResult(content=[TextContent(type="text", text=output)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"列出目录失败: {str(e)}")],
                isError=True
            )


class SearchLogTool(BaseTool):
    name = "search_log"
    description = "在日志文件中搜索指定关键词，支持 journalctl 或文件搜索"
    parameters = {
        "keyword": {
            "type": "string",
            "description": "搜索关键词"
        },
        "source": {
            "type": "string",
            "description": "日志来源: journalctl 或文件路径",
            "default": "journalctl"
        },
        "since": {
            "type": "string",
            "description": "时间范围，如 '1 hour ago', 'today'",
            "default": "1 hour ago"
        },
        "limit": {
            "type": "integer",
            "description": "最大返回行数，默认 100",
            "default": 100
        }
    }
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        keyword = arguments.get("keyword")
        source = arguments.get("source", "journalctl")
        since = arguments.get("since", "1 hour ago")
        limit = arguments.get("limit", 100)
        
        if not keyword:
            return ToolCallResult(
                content=[TextContent(type="text", text="错误: 必须提供 keyword 参数")],
                isError=True
            )
        
        try:
            if source == "journalctl":
                result = subprocess.run(
                    ["journalctl", "--since", since, "-g", keyword, "--no-pager", "-n", str(limit)],
                    capture_output=True, text=True, timeout=30
                )
            else:
                # 文件搜索
                if not os.path.exists(source):
                    return ToolCallResult(
                        content=[TextContent(type="text", text=f"文件不存在: {source}")],
                        isError=True
                    )
                result = subprocess.run(
                    ["grep", "-n", keyword, source],
                    capture_output=True, text=True, timeout=30
                )
                lines = result.stdout.strip().split("\n")[:limit]
                output = "\n".join(lines)
                return ToolCallResult(content=[TextContent(type="text", text=output)])
            
            output = result.stdout if result.returncode in [0, 1] else result.stderr
            return ToolCallResult(content=[TextContent(type="text", text=output)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"搜索日志失败: {str(e)}")],
                isError=True
            )


class SystemctlStatusTool(BaseTool):
    name = "get_service_status"
    description = "查看 systemd 服务的状态"
    parameters = {
        "service": {
            "type": "string",
            "description": "服务名，如 sshd, nginx, mysql"
        }
    }
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        service = arguments.get("service")
        if not service:
            return ToolCallResult(
                content=[TextContent(type="text", text="错误: 必须提供 service 参数")],
                isError=True
            )
        
        try:
            result = subprocess.run(
                ["systemctl", "status", service, "--no-pager"],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout if result.stdout else result.stderr
            return ToolCallResult(content=[TextContent(type="text", text=output)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取服务状态失败: {str(e)}")],
                isError=True
            )


register_tool(ReadFileTool())
register_tool(ListDirectoryTool())
register_tool(SearchLogTool())
register_tool(SystemctlStatusTool())
