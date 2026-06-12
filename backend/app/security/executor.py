import subprocess
import os
import tempfile
import platform
from typing import Dict, List, Optional, Tuple
from .guard import SecurityGuard


class PrivilegeExecutor:
    """
    最小权限执行代理
    核心运维动作在受限账户下运行，非必要不使用 root
    
    比赛环境适配：
    - 若 opsagent 用户不存在，自动降级为当前用户执行
    - 若 sudo 不可用，直接使用当前用户执行
    - 支持跨平台开发（Windows 下调试不报错）
    """
    
    def __init__(self, restricted_user: Optional[str] = None, config: Optional[Dict] = None):
        self.restricted_user = restricted_user or "opsagent"
        self.config = config or {}
        self.guard = SecurityGuard(config)
        # 运行时检测：目标用户是否存在、sudo 是否可用
        self._user_exists = self.check_user_exists(self.restricted_user)
        self._has_sudo = self._check_sudo_available()
        self._current_user = self._get_current_user()
    
    def _get_current_user(self) -> str:
        """获取当前运行用户"""
        try:
            import pwd
            return pwd.getpwuid(os.getuid()).pw_name
        except Exception:
            return os.environ.get("USER", "root")
    
    def _check_sudo_available(self) -> bool:
        """检测 sudo 是否可用"""
        if platform.system() == "Windows":
            return False
        try:
            result = subprocess.run(
                ["sudo", "-n", "true"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def execute(self, command: str, as_user: Optional[str] = None, 
                timeout: int = 30, cwd: Optional[str] = None) -> Dict:
        """
        在最小权限下执行命令
        
        Args:
            command: 要执行的命令
            as_user: 指定执行用户，None 则使用默认受限用户
            timeout: 超时时间
            cwd: 工作目录
        """
        target_user = as_user or self.restricted_user
        
        # 安全检查
        allowed, reason, check_result = self.guard.validate_command(command, tool_name=None, arguments=None)
        if not allowed:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"[SECURITY BLOCKED] {reason}",
                "returncode": -1,
                "security_check": check_result,
                "executed_as": None
            }
        
        try:
            # 构建执行命令
            exec_cmd = self._build_exec_cmd(command, target_user)
            
            # 设置环境变量，限制 PATH
            env = os.environ.copy()
            env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            if target_user != "root" and self._user_exists:
                env["HOME"] = f"/home/{target_user}"
            else:
                env["HOME"] = os.environ.get("HOME", "/root")
            
            result = subprocess.run(
                exec_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=env
            )
            
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "security_check": check_result,
                "executed_as": target_user if self._user_exists else self._current_user
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"命令执行超时 ({timeout}秒)",
                "returncode": -1,
                "security_check": check_result,
                "executed_as": target_user if self._user_exists else self._current_user
            }
        except Exception as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"执行异常: {str(e)}",
                "returncode": -1,
                "security_check": check_result,
                "executed_as": target_user if self._user_exists else self._current_user
            }
    
    def _build_exec_cmd(self, command: str, target_user: str) -> List[str]:
        """根据环境构建执行命令"""
        # Windows 开发环境直接执行
        if platform.system() == "Windows":
            return ["powershell", "-Command", command]
        
        # 目标用户存在且 sudo 可用：使用 sudo 切换
        if target_user and target_user != "root" and self._user_exists and self._has_sudo:
            return ["sudo", "-u", target_user, "bash", "-c", command]
        
        # 当前是 root 且目标用户存在：使用 sudo 切换（不需要 -n）
        if target_user and target_user != "root" and self._user_exists:
            try:
                if os.getuid() == 0:
                    return ["sudo", "-u", target_user, "bash", "-c", command]
            except AttributeError:
                pass  # Windows 没有 getuid
        
        # 降级：直接使用当前用户执行
        return ["bash", "-c", command]
    
    def execute_readonly(self, command: str, timeout: int = 10) -> Dict:
        """
        执行只读命令（信息收集类），使用最严格的权限
        """
        # 只读命令白名单
        readonly_prefixes = [
            "ps", "top", "df", "du", "free", "lsof",
            "netstat", "ss", "ip", "ping", "journalctl",
            "systemctl status", "cat", "grep", "find", "ls",
            "uname", "uptime", "who", "last",
            "vmstat", "iostat", "mpstat", "sar", "dmesg",
            "lsblk", "fdisk -l", "head", "tail", "wc",
            "getenforce", "sestatus", "crontab", "getent"
        ]
        
        cmd_first = command.strip().split()[0] if command.strip() else ""
        is_readonly = any(
            command.strip().startswith(prefix) or cmd_first == prefix.split()[0]
            for prefix in readonly_prefixes
        )
        
        if not is_readonly:
            return {
                "success": False,
                "stdout": "",
                "stderr": "[SECURITY] 非只读命令被拒绝在只读模式下执行",
                "returncode": -1,
                "security_check": {"reason": "not_readonly"},
                "executed_as": None
            }
        
        return self.execute(command, timeout=timeout)
    
    def check_user_exists(self, username: str) -> bool:
        """检查受限用户是否存在"""
        if platform.system() == "Windows":
            return False
        try:
            import pwd
            pwd.getpwnam(username)
            return True
        except (KeyError, ImportError):
            return False
    
    def create_restricted_user(self, username: str) -> Tuple[bool, str]:
        """创建受限执行用户"""
        if platform.system() == "Windows":
            return False, "Windows 环境不支持创建 Linux 用户"
        
        if self.check_user_exists(username):
            return True, f"用户 {username} 已存在"
        
        try:
            result = subprocess.run(
                ["useradd", "-r", "-s", "/bin/bash", "-m", username],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return True, f"用户 {username} 创建成功"
            else:
                return False, f"创建用户失败: {result.stderr}"
        except Exception as e:
            return False, f"创建用户异常: {str(e)}"
