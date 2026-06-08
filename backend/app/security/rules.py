import re
from typing import Dict, List, Optional, Tuple
from enum import Enum


class RiskLevel(str, Enum):
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskRule:
    """单条风险规则"""
    
    def __init__(self, name: str, pattern: str, level: RiskLevel, 
                 description: str, action: str = "allow"):
        self.name = name
        self.pattern = re.compile(pattern, re.IGNORECASE)
        self.level = level
        self.description = description
        self.action = action  # allow, block, confirm
    
    def match(self, command: str) -> bool:
        return bool(self.pattern.search(command))


class SecurityRuleEngine:
    """安全规则引擎"""
    
    def __init__(self):
        self.rules: List[RiskRule] = []
        self._init_default_rules()
    
    def _init_default_rules(self):
        """初始化默认安全规则"""
        default_rules = [
            # 绝对禁止的规则
            RiskRule(
                "rm_root",
                r"^\s*rm\s+.*-rf\s+/\s*$|^\s*rm\s+.*-rf\s+/\s+",
                RiskLevel.CRITICAL,
                "删除根目录是极其危险的操作",
                "block"
            ),
            RiskRule(
                "mkfs_disk",
                r"^\s*mkfs\.\w+\s+/dev/[sh]d[a-z]\d*",
                RiskLevel.CRITICAL,
                "格式化磁盘分区",
                "block"
            ),
            RiskRule(
                "dd_disk",
                r"^\s*dd\s+.*of\s*=\s*/dev/[sh]d[a-z]",
                RiskLevel.CRITICAL,
                "直接写入磁盘设备",
                "block"
            ),
            RiskRule(
                "fork_bomb",
                r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
                RiskLevel.CRITICAL,
                "Fork Bomb 拒绝服务攻击",
                "block"
            ),
            RiskRule(
                "overwrite_boot",
                r">\s*/dev/[sh]d[a-z]\b|of\s*=\s*/dev/[sh]d[a-z]\b",
                RiskLevel.CRITICAL,
                "覆盖磁盘设备",
                "block"
            ),
            RiskRule(
                "reboot_system",
                r"^\s*(reboot|shutdown)\s*(-h|-r)?\s*(\d+|now)?\s*$",
                RiskLevel.CRITICAL,
                "系统重启/关机操作",
                "block"
            ),
            RiskRule(
                "chmod_recursive_dangerous",
                r"chmod\s+-R\s+777\s+/|chmod\s+-R\s+666\s+/",
                RiskLevel.CRITICAL,
                "递归设置根目录危险权限",
                "block"
            ),
            RiskRule(
                "passwd_modify",
                r"passwd\s+(\w+)?",
                RiskLevel.HIGH,
                "修改用户密码操作",
                "confirm"
            ),
            RiskRule(
                "useradd_admin",
                r"useradd\s+.*(-G\s+root|--gid\s+0)",
                RiskLevel.HIGH,
                "创建管理员用户",
                "confirm"
            ),
            RiskRule(
                "network_restart",
                r"systemctl\s+(restart|reload)\s+(network|NetworkManager)",
                RiskLevel.HIGH,
                "重启网络服务",
                "confirm"
            ),
            
            # 需要确认的高危规则
            # 匹配原始 shell 命令格式和工具调用格式中的高危参数
            RiskRule(
                "rm_force_recursive",
                r"rm\s+.*-rf|--no-preserve-root",
                RiskLevel.HIGH,
                "强制递归删除",
                "confirm"
            ),
            RiskRule(
                "kill_force",
                r"kill\s+.*-9\s+\d+|killall\s+.*-9|kill_process\s*\(.*SIGKILL.*\)",
                RiskLevel.HIGH,
                "强制终止进程 (SIGKILL)",
                "confirm"
            ),
            RiskRule(
                "chmod_dangerous",
                r"chmod\s+.*777\s+|chmod\s+.*666\s+/etc",
                RiskLevel.HIGH,
                "设置危险的文件权限",
                "confirm"
            ),
            RiskRule(
                "systemctl_stop_critical",
                r"systemctl\s+(stop|restart)\s+(sshd|network|NetworkManager|systemd-journald|dbus|polkit)|stop_service\s*\(.*(sshd|network|NetworkManager|systemd-journald|dbus|polkit).*\)|restart_service\s*\(.*(sshd|network|NetworkManager|systemd-journald|dbus|polkit).*\)",
                RiskLevel.HIGH,
                "停止关键系统服务",
                "confirm"
            ),
            RiskRule(
                "user_management",
                r"userdel\s+.*-r|usermod\s+.*-G\s+root",
                RiskLevel.HIGH,
                "用户账号修改操作",
                "confirm"
            ),
            
            # 中危规则
            RiskRule(
                "wget_curl_pipe",
                r"(wget|curl)\s+.*\|\s*(sh|bash|python|perl)",
                RiskLevel.MEDIUM,
                "从网络下载并直接执行脚本",
                "confirm"
            ),
            RiskRule(
                "modify_sudoers",
                r"visudo|/etc/sudoers",
                RiskLevel.MEDIUM,
                "修改 sudo 权限配置",
                "confirm"
            ),
            RiskRule(
                "iptables_flush",
                r"iptables\s+.*-F",
                RiskLevel.MEDIUM,
                "清空防火墙规则",
                "confirm"
            ),
            
            # 中危 - 操作类工具（赛题要求的修改系统状态操作）
            RiskRule(
                "clean_logs",
                r"clean_logs\s*\(",
                RiskLevel.MEDIUM,
                "清理日志文件操作",
                "confirm"
            ),
            RiskRule(
                "safe_remove",
                r"safe_remove\s*\(",
                RiskLevel.MEDIUM,
                "删除文件操作",
                "confirm"
            ),
            RiskRule(
                "restart_service",
                r"restart_service\s*\(",
                RiskLevel.MEDIUM,
                "重启系统服务",
                "confirm"
            ),
            RiskRule(
                "stop_service",
                r"stop_service\s*\(",
                RiskLevel.MEDIUM,
                "停止系统服务",
                "confirm"
            ),
            
            # 低风险 - 服务启动 + 提示词注入检测
            RiskRule(
                "start_service",
                r"start_service\s*\(|systemctl\s+start",
                RiskLevel.LOW,
                "启动系统服务",
                "confirm"
            ),
            RiskRule(
                "prompt_ignore",
                r"ignore\s+previous\s+instructions|disregard\s+.*rules|forget\s+.*prompt",
                RiskLevel.LOW,
                "可能的提示词注入尝试",
                "confirm"
            ),
            RiskRule(
                "prompt_override",
                r"system\s+prompt|developer\s+mode|DAN\s+mode",
                RiskLevel.LOW,
                "提示词注入特征",
                "confirm"
            ),
        ]
        self.rules.extend(default_rules)
    
    def add_rule(self, rule: RiskRule):
        self.rules.append(rule)
    
    def evaluate(self, command: str) -> Tuple[RiskLevel, List[Dict]]:
        """
        评估命令风险等级
        返回: (最高风险等级, 匹配的规则列表)
        """
        matched = []
        max_level = RiskLevel.SAFE
        
        for rule in self.rules:
            if rule.match(command):
                matched.append({
                    "name": rule.name,
                    "level": rule.level,
                    "description": rule.description,
                    "action": rule.action
                })
                if self._level_value(rule.level) > self._level_value(max_level):
                    max_level = rule.level
        
        return max_level, matched
    
    @staticmethod
    def _level_value(level: RiskLevel) -> int:
        mapping = {
            RiskLevel.SAFE: 0,
            RiskLevel.LOW: 1,
            RiskLevel.MEDIUM: 2,
            RiskLevel.HIGH: 3,
            RiskLevel.CRITICAL: 4
        }
        return mapping.get(level, 0)


# 命令白名单检查
class CommandWhitelist:
    """命令白名单检查器
    
    支持从 agent.yaml 配置加载 allowed_command_prefixes，
    若配置为空则使用默认安全白名单。
    """
    
    DEFAULT_PREFIXES = [
        "ps", "top", "htop", "df", "du", "free", "lsof",
        "netstat", "ss", "ip", "ping", "journalctl",
        "systemctl status", "cat", "grep", "find", "ls",
        "pwd", "uname", "uptime", "who", "last",
        "vmstat", "iostat", "mpstat", "sar", "dmesg",
        "lsblk", "fdisk -l", "tar", "gzip", "tail", "head", "wc",
        "echo", "mkdir", "touch", "cp", "mv", "diff",
        "getenforce", "sestatus", "crontab", "getent", "kill",
    ]
    
    def __init__(self, prefixes: list = None):
        self.allowed_prefixes = prefixes if prefixes else self.DEFAULT_PREFIXES
    
    def is_allowed(self, command: str) -> bool:
        """检查命令是否在白名单范围内"""
        command = command.strip()
        if not command:
            return False
        cmd_first = command.split()[0] if command else ""
        return any(
            command.startswith(prefix) or cmd_first == prefix.split()[0]
            for prefix in self.allowed_prefixes
        )
