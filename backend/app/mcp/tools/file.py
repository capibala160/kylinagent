import subprocess
import os
import time
from typing import Any, Dict, Tuple
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
        "/etc/shadow", "/etc/gshadow", "/etc/passwd", "/etc/passwd-", "/etc/shadow-",
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
                if result.returncode != 0 and "journalctl" in result.stderr.lower():
                    # journalctl 不可用（如非 systemd 环境）
                    return ToolCallResult(
                        content=[TextContent(type="text", text=f"journalctl 不可用（当前环境可能不支持 systemd）:\n{result.stderr}")],
                        isError=True
                    )
                output = result.stdout if result.returncode in [0, 1] else result.stderr
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
            if result.returncode != 0 and "systemctl" in result.stderr.lower():
                return ToolCallResult(
                    content=[TextContent(type="text", text=f"systemd 不可用（当前环境可能不支持 systemd）:\n{result.stderr}")],
                    isError=True
                )
            output = result.stdout if result.stdout else result.stderr
            return ToolCallResult(content=[TextContent(type="text", text=output)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取服务状态失败: {str(e)}")],
                isError=True
            )


class CleanLogsTool(BaseTool):
    name = "clean_logs"
    description = (
        "安全清理指定目录下的旧日志文件，支持预览模式。"
        "默认保留最近 7 天日志，不会删除系统关键日志。"
        "dry_run=True 时只返回预览列表，不会真正删除。"
    )
    parameters = {
        "path": {
            "type": "string",
            "description": "日志目录路径，默认 /var/log",
            "default": "/var/log"
        },
        "keep_days": {
            "type": "integer",
            "description": "保留最近 N 天的日志，默认 7",
            "default": 7
        },
        "dry_run": {
            "type": "boolean",
            "description": "True=仅预览不删除，False=真正执行清理",
            "default": True
        }
    }
    
    # 系统关键日志文件（绝对禁止删除）
    CRITICAL_LOGS = {
        "/var/log/secure", "/var/log/audit", "/var/log/audit/audit.log",
        "/var/log/messages", "/var/log/lastlog", "/var/log/wtmp",
        "/var/log/btmp", "/var/log/dmesg", "/var/log/boot.log",
        "/var/log/cron", "/var/log/maillog", "/var/log/spooler",
        "/var/log/syslog", "/var/log/kern.log", "/var/log/auth.log",
    }
    
    # 禁止清理的根目录（防止误删）
    FORBIDDEN_ROOTS = {"/etc", "/boot", "/proc", "/sys", "/dev", "/bin", "/sbin", "/lib", "/lib64"}
    
    def _is_critical_log(self, real_path: str) -> bool:
        """检查是否为系统关键日志"""
        return real_path in self.CRITICAL_LOGS
    
    def _is_in_forbidden_root(self, real_path: str) -> bool:
        """检查路径是否在禁止清理的根目录下"""
        for root in self.FORBIDDEN_ROOTS:
            if real_path.startswith(root + "/") or real_path == root:
                return True
        return False
    
    def _is_old_log_file(self, file_path: str, keep_days: int) -> bool:
        """检查文件是否为超过保留期限的日志文件"""
        try:
            mtime = os.path.getmtime(file_path)
            age_days = (time.time() - mtime) / 86400
            return age_days > keep_days
        except Exception:
            return False
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        path = arguments.get("path", "/var/log")
        keep_days = arguments.get("keep_days", 7)
        dry_run = arguments.get("dry_run", True)
        
        # 路径规范化
        real_path = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
        
        if not os.path.exists(real_path):
            return ToolCallResult(
                content=[TextContent(type="text", text=f"目录不存在: {real_path}")],
                isError=True,
                errorMessage="目录不存在"
            )
        
        if not os.path.isdir(real_path):
            return ToolCallResult(
                content=[TextContent(type="text", text=f"路径不是目录: {real_path}")],
                isError=True,
                errorMessage="路径不是目录"
            )
        
        if self._is_in_forbidden_root(real_path):
            return ToolCallResult(
                content=[TextContent(type="text", text=f"安全限制: 禁止在 {real_path} 下执行清理操作")],
                isError=True,
                errorMessage="禁止在系统关键目录下清理"
            )
        
        # 扫描日志文件
        candidates = []
        try:
            for entry in os.scandir(real_path):
                if not entry.is_file():
                    continue
                name = entry.name.lower()
                # 匹配日志文件特征
                is_log = (
                    name.endswith(".log") or
                    name.endswith(".log.1") or name.endswith(".log.2") or
                    name.endswith(".log.gz") or name.endswith(".log.bz2") or
                    name.endswith(".log.xz") or
                    name.startswith("messages-") or
                    name.startswith("secure-") or
                    name.startswith("maillog-") or
                    name.startswith("cron-") or
                    name.startswith("syslog-") or
                    name.startswith("auth.log-") or
                    name.startswith("kern.log-")
                )
                if not is_log:
                    continue
                
                file_real = os.path.realpath(entry.path)
                if self._is_critical_log(file_real):
                    continue
                if not self._is_old_log_file(file_real, keep_days):
                    continue
                
                size = entry.stat().st_size
                mtime = entry.stat().st_mtime
                mtime_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
                candidates.append({
                    "path": file_real,
                    "name": entry.name,
                    "size": size,
                    "mtime": mtime_str,
                })
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"扫描目录失败: {str(e)}")],
                isError=True,
                errorMessage=f"扫描目录失败: {str(e)}"
            )
        
        if not candidates:
            msg = f"目录 {real_path} 下没有找到超过 {keep_days} 天的非关键日志文件。"
            return ToolCallResult(content=[TextContent(type="text", text=msg)])
        
        # 按大小排序
        candidates.sort(key=lambda x: x["size"], reverse=True)
        total_size = sum(c["size"] for c in candidates)
        
        lines = [f"## 日志清理{'预览' if dry_run else '结果'}: {real_path}", ""]
        lines.append(f"保留策略: 最近 {keep_days} 天内 | 共发现 {len(candidates)} 个候选文件 | 可回收 {total_size / 1024 / 1024:.2f} MB")
        lines.append("")
        lines.append("| 文件名 | 大小 | 修改时间 | 操作 |")
        lines.append("|--------|------|----------|------|")
        
        deleted_count = 0
        deleted_size = 0
        
        for c in candidates:
            size_str = f"{c['size'] / 1024:.1f} KB" if c["size"] < 1024 * 1024 else f"{c['size'] / 1024 / 1024:.2f} MB"
            if dry_run:
                action = "将删除"
            else:
                try:
                    os.remove(c["path"])
                    action = "✅ 已删除"
                    deleted_count += 1
                    deleted_size += c["size"]
                except Exception as e:
                    action = f"❌ 删除失败 ({str(e)})"
            lines.append(f"| {c['name']} | {size_str} | {c['mtime']} | {action} |")
        
        if not dry_run:
            lines.append("")
            lines.append(f"实际删除: {deleted_count} 个文件，释放 {deleted_size / 1024 / 1024:.2f} MB")
        
        if dry_run:
            lines.append("")
            lines.append("> ⚠️ 当前为预览模式（dry_run=True），不会真正删除文件。")
            lines.append("> 如需执行清理，请将 dry_run 设为 False 并再次确认。")
        
        return ToolCallResult(content=[TextContent(type="text", text="\n".join(lines))])


class SafeRemoveTool(BaseTool):
    name = "safe_remove"
    description = (
        "安全删除指定文件或目录。"
        "禁止删除系统关键目录（/etc、/boot、/bin 等），支持路径遍历防护。"
    )
    parameters = {
        "path": {
            "type": "string",
            "description": "要删除的文件或目录路径"
        },
        "recursive": {
            "type": "boolean",
            "description": "是否递归删除目录，默认 False（安全起见）",
            "default": False
        }
    }
    
    # 禁止删除的根目录
    FORBIDDEN_PATHS = {
        "/etc", "/boot", "/proc", "/sys", "/dev",
        "/bin", "/sbin", "/lib", "/lib64",
        "/usr/bin", "/usr/sbin", "/usr/lib", "/usr/lib64",
        "/", "/home", "/root", "/var", "/tmp",
    }
    
    # 禁止删除的关键文件
    CRITICAL_FILES = {
        "/etc/passwd", "/etc/shadow", "/etc/group", "/etc/gshadow",
        "/etc/fstab", "/etc/hosts", "/etc/resolv.conf",
        "/etc/ssh/sshd_config", "/etc/sudoers",
    }
    
    def _resolve_path(self, path: str) -> str:
        path = os.path.expanduser(path)
        return os.path.realpath(os.path.abspath(path))
    
    def _is_forbidden(self, real_path: str) -> Tuple[bool, str]:
        """检查路径是否在禁止删除列表中"""
        for forbidden in self.FORBIDDEN_PATHS:
            if real_path.startswith(forbidden + "/") or real_path == forbidden:
                return True, f"禁止删除系统关键路径: {forbidden}"
        
        if real_path in self.CRITICAL_FILES:
            return True, f"禁止删除关键系统文件: {real_path}"
        
        return False, ""
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        path = arguments.get("path")
        recursive = arguments.get("recursive", False)
        
        if not path:
            return ToolCallResult(
                content=[TextContent(type="text", text="错误: 必须提供 path 参数")],
                isError=True,
                errorMessage="缺少 path 参数"
            )
        
        real_path = self._resolve_path(path)
        
        # 路径遍历防护 + 黑名单校验
        forbidden, reason = self._is_forbidden(real_path)
        if forbidden:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"🛡️ 安全拦截: {reason}")],
                isError=True,
                errorMessage=reason
            )
        
        if not os.path.exists(real_path):
            return ToolCallResult(
                content=[TextContent(type="text", text=f"路径不存在: {real_path}")],
                isError=True,
                errorMessage="路径不存在"
            )
        
        is_dir = os.path.isdir(real_path)
        
        if is_dir and not recursive:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"{real_path} 是目录，但未设置 recursive=True。请确认后再删除。")],
                isError=True,
                errorMessage="目录未设置 recursive=True"
            )
        
        try:
            if is_dir:
                import shutil
                shutil.rmtree(real_path)
                text = f"已递归删除目录: {real_path}"
            else:
                os.remove(real_path)
                text = f"已删除文件: {real_path}"
            
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except PermissionError:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"权限不足，无法删除 {real_path}。可能需要 root 权限。")],
                isError=True,
                errorMessage="权限不足"
            )
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"删除失败: {str(e)}")],
                isError=True,
                errorMessage=f"删除失败: {str(e)}"
            )


register_tool(ReadFileTool())
register_tool(ListDirectoryTool())
register_tool(SearchLogTool())
register_tool(SystemctlStatusTool())
register_tool(CleanLogsTool())
register_tool(SafeRemoveTool())
