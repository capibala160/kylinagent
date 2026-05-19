import subprocess
import os
import tempfile
from typing import Dict, List, Optional, Tuple
from .guard import SecurityGuard


class PrivilegeExecutor:
    """
    最小权限执行代理
    核心运维动作在受限账户下运行，非必要不使用 root
    """
    
    def __init__(self, restricted_user: Optional[str] = None, config: Optional[Dict] = None):
        self.restricted_user = restricted_user or "opsagent"
        self.config = config or {}
        self.guard = SecurityGuard(config)
    
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
        allowed, reason, check_result = self.guard.validate_command(command)
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
            # 使用 sudo -u 切换到受限用户执行
            if target_user and target_user != "root" and os.geteuid() == 0:
                # 当前是 root，可以切换到受限用户
                exec_cmd = ["sudo", "-u", target_user, "bash", "-c", command]
            elif target_user and target_user != "root":
                # 当前不是 root，尝试用 sudo
                exec_cmd = ["sudo", "-u", target_user, "bash", "-c", command]
            else:
                # 使用当前用户执行
                exec_cmd = ["bash", "-c", command]
            
            # 设置环境变量，限制 PATH
            env = os.environ.copy()
            env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            env["HOME"] = f"/home/{target_user}" if target_user != "root" else "/root"
            
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
                "executed_as": target_user
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"命令执行超时 ({timeout}秒)",
                "returncode": -1,
                "security_check": check_result,
                "executed_as": target_user
            }
        except Exception as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"执行异常: {str(e)}",
                "returncode": -1,
                "security_check": check_result,
                "executed_as": target_user
            }
    
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
            "lsblk", "fdisk -l", "head", "tail", "wc"
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
        try:
            import pwd
            pwd.getpwnam(username)
            return True
        except KeyError:
            return False
    
    def create_restricted_user(self, username: str) -> Tuple[bool, str]:
        """创建受限执行用户"""
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
