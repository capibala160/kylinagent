import os
import posixpath
import re
from typing import Any, Dict, List, Optional, Tuple
from .rules import SecurityRuleEngine, RiskLevel, CommandWhitelist


class SecurityGuard:
    """
    安全护栏核心类
    负责对自然语言指令和生成的命令进行安全检查
    
    三层防护模型：
    1. 意图校验层：检测提示词注入、恶意自然语言
    2. 命令校验层：基于规则引擎识别危险命令
    3. 权限隔离层：受限用户执行、只读白名单
    """
    
    def __init__(self, config: Optional[Dict] = None):
        self.rule_engine = SecurityRuleEngine()
        self.config = config or {}
        # 从配置加载白名单前缀（解决 agent.yaml 配置不生效问题）
        prefixes = self.config.get("allowed_command_prefixes", [])
        self.whitelist = CommandWhitelist(prefixes)
        
        # 加载配置中的自定义规则
        self._load_config_rules()
        
        # 加载配置中的路径黑白名单（未配置则使用默认值）
        cfg = self.config or {}
        self.SENSITIVE_READ_PATHS = cfg.get("sensitive_read_paths") or self.SENSITIVE_READ_PATHS
        self.CRITICAL_DIRECTORIES = cfg.get("critical_directories") or self.CRITICAL_DIRECTORIES
        self.ALLOWED_LOG_PATHS = cfg.get("allowed_log_paths") or self.ALLOWED_LOG_PATHS
        self.CRITICAL_SERVICES = cfg.get("critical_services") or self.CRITICAL_SERVICES
        self.PROTECTED_PIDS = cfg.get("protected_pids") or self.PROTECTED_PIDS
        
        # 记录校验统计
        self._stats = {
            "intent_checks": 0,
            "intent_blocks": 0,
            "command_checks": 0,
            "command_blocks": 0,
            "command_confirms": 0
        }
    
    def _load_config_rules(self):
        """从配置加载自定义危险命令规则"""
        dangerous_patterns = self.config.get("dangerous_commands", [])
        from .rules import RiskRule
        for i, pattern in enumerate(dangerous_patterns):
            self.rule_engine.add_rule(
                RiskRule(
                    name=f"config_dangerous_{i}",
                    pattern=pattern,
                    level=RiskLevel.CRITICAL,
                    description=f"配置文件定义的危险模式: {pattern}",
                    action="block"
                )
            )
    
    def validate_intent(self, user_input: str) -> Tuple[bool, str, Dict]:
        """
        验证用户意图（自然语言层面）
        返回: (是否通过, 原因, 详细信息)
        """
        self._stats["intent_checks"] += 1
        
        # 1. 检查提示词注入（增强版）
        injection_patterns = [
            # 英文注入模式
            r"ignore\s+(all\s+)?previous\s+instructions",
            r"disregard\s+.*(safety|security|rules|guardrails?)",
            r"forget\s+.*(prompt|instruction|rule|system)",
            r"you\s+are\s+now\s+.*mode",
            r"DAN\s+mode|developer\s+mode|jailbreak|master\s+mode",
            r"system\s*:\s*you\s+are\s+an\s+unrestricted",
            r"</?system>",
            r"from\s+now\s+on\s+you\s+are",
            r"ignore\s+the\s+above|ignore\s+above\s+instructions",
            r"do\s+not\s+follow\s+.*rules|do\s+not\s+obey\s+.*rules",
            r"bypass\s+.*(restriction|filter|security|guard)",
            r"act\s+as\s+if\s+.*no\s+rules|act\s+without\s+rules",
            r"remove\s+restrictions|disable\s+protections?",
            r"become\s+unrestricted|become\s+free",
            r"turn\s+off\s+.*filter|turn\s+off\s+.*safety",
            r"override\s+instructions|override\s+protocol",
            # 中文注入模式
            r"新纪元|忽略之前|忘掉设定|忘记设定|重置设定",
            r"忽略以上|忽略上述|忽略前面的|忽略所有",
            r"不用遵守|不必遵守|无视规则|漠视规则",
            r"绕过限制|绕过安全|绕过防护|绕过护栏",
            r"你现在是一个|你现在是|切换成|变身成",
            r"解除限制|取消限制|突破限制|冲破限制",
            r"假装你是|扮演|角色扮演|模拟成",
            r"系统提示|system提示|系统指令|角色设定",
            r"以上都不要管|以上都是错的|全部忽略",
            r"请直接执行|不要检查|不要校验|跳过检查",
            r"删除所有|清空所有|格式化所有|销毁所有",
            r"关闭安全|关闭防护|关闭检查|关闭过滤",
            r"强制执行|必须执行|无条件执行",
            r"紧急模式|超级管理员|最高权限",
        ]
        
        for pattern in injection_patterns:
            if re.search(pattern, user_input, re.IGNORECASE):
                self._stats["intent_blocks"] += 1
                return False, "检测到潜在的提示词注入攻击", {
                    "type": "prompt_injection",
                    "pattern": pattern,
                    "input_preview": user_input[:200],
                    "risk_level": "high"
                }
        
        # 2. 检查明显的恶意指令
        malicious_patterns = [
            r"删除\s*.*\s*系统\s*文件|删除系统.*文件",
            r"格式化\s*.*\s*磁盘|格式化磁盘",
            r"关闭\s*.*\s*防火墙|关闭防火墙",
            r"关闭\s*.*\s*安全|关闭安全.*服务",
            r"清除\s*.*\s*日志|删除日志|清空日志",
            r"破坏\s*.*\s*系统|攻击系统|入侵系统",
            r"窃取\s*.*\s*数据|盗取数据|泄露数据",
            r"绕过\s*.*\s*认证|绕过登录|破解密码",
        ]
        
        for pattern in malicious_patterns:
            if re.search(pattern, user_input, re.IGNORECASE):
                self._stats["intent_blocks"] += 1
                return False, "检测到潜在恶意意图", {
                    "type": "malicious_intent",
                    "pattern": pattern,
                    "input_preview": user_input[:200],
                    "risk_level": "critical"
                }
        
        # 3. 检查潜在危险操作描述
        dangerous_operation_patterns = [
            r"强制删除|强行删除|强制卸载",
            r"格式化硬盘|格式化分区",
            r"重启服务器|重启系统|关机",
            r"重置密码|修改密码|破解密码",
        ]
        
        for pattern in dangerous_operation_patterns:
            if re.search(pattern, user_input, re.IGNORECASE):
                return True, "检测到潜在危险操作，将在执行阶段进行安全校验", {
                    "type": "dangerous_operation_detected",
                    "pattern": pattern,
                    "input_preview": user_input[:200],
                    "risk_level": "medium"
                }
        
        return True, "意图校验通过", {"type": "safe", "risk_level": "low"}
    
    # ========== MCP 工具参数级安全检查 ==========
    # 敏感文件路径（禁止读取）
    SENSITIVE_READ_PATHS = [
        "/etc/shadow", "/etc/gshadow", "/etc/master.passwd", "/etc/passwd",
        "/etc/ssh/ssh_host_", "/root/.ssh/id_",
        "/proc/kcore", "/proc/sysrq-trigger",
        "/dev/mem", "/dev/port", "/dev/kmem",
        "/boot/System.map", "/boot/vmlinuz",
    ]
    # 系统关键目录（禁止删除/修改）
    CRITICAL_DIRECTORIES = [
        "/", "/bin", "/sbin", "/usr", "/usr/bin", "/usr/sbin",
        "/lib", "/lib64", "/etc", "/boot", "/proc", "/sys", "/dev",
        "/run", "/var/lib", "/var/run",
    ]
    # 允许的日志清理目录
    ALLOWED_LOG_PATHS = ["/var/log", "/tmp", "/run/log"]
    # 禁止停止/重启的关键服务
    CRITICAL_SERVICES = [
        "sshd", "network", "NetworkManager", "systemd-journald",
        "dbus", "polkit", "systemd-logind", "cron", "crond",
        "systemd-networkd", "systemd-resolved", "systemd-timesyncd",
    ]
    # 禁止杀死的特殊 PID
    PROTECTED_PIDS = ["1", "0", "self", "thread-self"]

    def _validate_tool_parameters(self, tool_name: str, arguments: Dict[str, Any]) -> Tuple[bool, str, RiskLevel]:
        """
        对 MCP 工具的参数进行安全检查
        返回: (是否通过, 原因, 风险等级)
        """
        path = arguments.get("path", "")
        service = arguments.get("service", "") or arguments.get("service_name", "")
        pid = str(arguments.get("pid", ""))
        target = arguments.get("target", "") or arguments.get("directory", "") or path
        dry_run = arguments.get("dry_run", True)

        # 1. read_file / read_log: 禁止读取敏感文件
        if tool_name in ("read_file", "read_log", "cat_file"):
            real_path = posixpath.abspath(posixpath.expanduser(path))
            for sp in self.SENSITIVE_READ_PATHS:
                if real_path.startswith(sp) or sp in real_path:
                    return False, f"禁止读取敏感文件: {path}", RiskLevel.CRITICAL
            # 额外检查 SSH 私钥模式
            if "/.ssh/id_" in real_path or "/.ssh/authorized_keys" in real_path:
                return False, f"禁止读取用户 SSH 密钥: {path}", RiskLevel.CRITICAL

        # 2. safe_remove / remove_file / delete_file: 禁止删除系统关键目录
        if tool_name in ("safe_remove", "remove_file", "delete_file", "rm_file"):
            real_target = posixpath.abspath(posixpath.expanduser(target))
            for cd in self.CRITICAL_DIRECTORIES:
                if real_target == cd or real_target.startswith(cd + "/") and real_target.rstrip("/").count("/") <= cd.rstrip("/").count("/"):
                    return False, f"禁止删除系统关键目录: {target}", RiskLevel.CRITICAL
            # 禁止删除 /tmp 和 /var/tmp 的目录本身（但可以删里面的文件）
            if real_target.rstrip("/") in ("/tmp", "/var/tmp"):
                return False, f"禁止删除系统临时目录本身: {target}", RiskLevel.CRITICAL

        # 3. clean_logs: 限制只能清理日志目录
        if tool_name == "clean_logs":
            log_path = arguments.get("path", "") or arguments.get("log_path", "") or arguments.get("directory", "")
            real_log = posixpath.abspath(posixpath.expanduser(log_path))
            allowed = any(real_log.startswith(ap) for ap in self.ALLOWED_LOG_PATHS)
            if not allowed:
                return False, f"只能在日志目录下清理: {log_path}", RiskLevel.HIGH
            # 非 dry_run 的清理日志属于中危操作
            if not dry_run:
                return True, f"即将清理日志: {log_path}", RiskLevel.MEDIUM

        # 4. kill_process: 禁止杀死 init 和特殊进程
        if tool_name == "kill_process":
            if pid in self.PROTECTED_PIDS:
                return False, f"禁止终止系统关键进程 (PID {pid})", RiskLevel.CRITICAL
            signal = arguments.get("signal", "SIGTERM")
            if signal == "SIGKILL":
                return True, f"强制终止进程 (SIGKILL) 需要确认", RiskLevel.HIGH

        # 5. stop_service / restart_service: 禁止操作关键服务
        if tool_name in ("stop_service", "restart_service"):
            svc = service.lower()
            for cs in self.CRITICAL_SERVICES:
                if svc == cs or svc.startswith(cs + "."):
                    return False, f"禁止停止/重启关键系统服务: {service}", RiskLevel.CRITICAL
            return True, f"{tool_name}({service}) 需要确认", RiskLevel.MEDIUM

        # 6. start_service: 相对安全，但需确认
        if tool_name == "start_service":
            return True, f"启动服务 {service} 需要确认", RiskLevel.LOW

        # 7. write_file / modify_file: 禁止写入系统关键配置文件
        if tool_name in ("write_file", "modify_file", "append_file"):
            real_path = posixpath.abspath(posixpath.expanduser(path))
            sensitive_write_paths = [
                "/etc/passwd", "/etc/shadow", "/etc/sudoers", "/etc/ssh/sshd_config",
                "/etc/fstab", "/boot/grub", "/etc/crontab",
            ]
            for swp in sensitive_write_paths:
                if real_path.startswith(swp):
                    return False, f"禁止修改系统关键配置文件: {path}", RiskLevel.CRITICAL

        return True, "参数级安全检查通过", RiskLevel.SAFE

    def validate_command(self, command: str, tool_name: Optional[str] = None, arguments: Optional[Dict] = None) -> Tuple[bool, str, Dict]:
        """
        验证生成的命令/工具调用
        返回: (是否允许执行, 原因, 详细信息)
        """
        self._stats["command_checks"] += 1
        
        result = {
            "command": command,
            "tool_name": tool_name,
            "risk_level": "safe",
            "matched_rules": [],
            "whitelist_check": False,
            "whitelist_allowed": False,
            "parameter_check": False,
            "sanitized_command": self.sanitize_command(command),
            "check_time": None,
        }
        
        # 1. MCP 工具参数级安全检查（新增）
        is_tool_call = tool_name is not None and tool_name != "shell"
        if is_tool_call and arguments:
            result["parameter_check"] = True
            param_safe, param_reason, param_level = self._validate_tool_parameters(tool_name, arguments)
            if not param_safe:
                self._stats["command_blocks"] += 1
                result["risk_level"] = param_level
                return False, f"参数安全检查未通过: {param_reason}", result
            # 参数检查通过，但如果参数检查判定为需要确认，使用其风险等级
            if param_level != RiskLevel.SAFE:
                result["risk_level"] = param_level
                # 继续走下面的规则引擎评估，取最高风险等级

        # 2. 规则引擎评估
        risk_level, matched_rules = self.rule_engine.evaluate(command)
        
        # 如果参数级检查已经判定更高或同等风险，优先使用参数检查的结果
        if result.get("risk_level") and result["risk_level"] != RiskLevel.SAFE:
            # 使用 RiskLevel 枚举的 value 属性进行比较（"safe"/"low"/"medium"/"high"/"critical"）
            LEVEL_MAP = {"safe": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
            param_level_val = LEVEL_MAP.get(result["risk_level"].value if hasattr(result["risk_level"], "value") else str(result["risk_level"]).lower(), 0)
            rule_level_val = LEVEL_MAP.get(risk_level.value if hasattr(risk_level, "value") else str(risk_level).lower(), 0)
            if param_level_val >= rule_level_val:
                risk_level = result["risk_level"]
                matched_rules.insert(0, {"name": "parameter_check", "level": risk_level, "description": param_reason, "action": "block" if param_level_val == 4 else "confirm"})
        
        result["risk_level"] = risk_level
        result["matched_rules"] = matched_rules
        
        # CRITICAL: 绝对阻断，不可逾越的红线
        if risk_level == RiskLevel.CRITICAL:
            self._stats["command_blocks"] += 1
            return False, f"检测到致命风险操作，已阻断: {matched_rules[0]['description'] if matched_rules else '未知风险'}", result
        
        # HIGH: 高危操作，需要二次确认（赛题要求）
        if risk_level == RiskLevel.HIGH:
            self._stats["command_confirms"] += 1
            return True, f"检测到高危操作，需要人工二次确认: {matched_rules[0]['description'] if matched_rules else '未知风险'}", result
        
        # MEDIUM: 中危操作，需要确认
        if risk_level == RiskLevel.MEDIUM:
            self._stats["command_confirms"] += 1
            return True, f"检测到中危操作，需要确认: {matched_rules[0]['description'] if matched_rules else '未知风险'}", result
        
        # LOW: 低风险操作，直接放行（策略调整：不再强制确认）
        if risk_level == RiskLevel.LOW:
            return True, "检测到低风险操作，已放行", result
        
        # 3. 白名单检查（增强版）
        # 仅对原始 shell 命令字符串生效（tool_name 为 None 或 "shell" 时）
        if not is_tool_call:
            if self.whitelist.is_allowed(command):
                result["whitelist_allowed"] = True
                result["whitelist_check"] = True
            else:
                result["whitelist_check"] = True
                # 不在白名单的命令升级为 MEDIUM 风险，确保必须确认
                result["risk_level"] = RiskLevel.MEDIUM
                self._stats["command_confirms"] += 1
                return True, "命令不在白名单范围内，需要确认", result
        
        return True, "命令安全检查通过", result
    
    def sanitize_command(self, command: str) -> str:
        """
        对命令进行清理和规范化
        """
        # 移除危险的 shell 元字符组合
        dangerous_chars = [
            "; rm ", "; mkfs", "; dd ",
            "&& rm ", "&& mkfs", "&& dd ",
            "|| rm ", "| bash", "| sh ",
        ]
        
        sanitized = command
        for dc in dangerous_chars:
            if dc in sanitized.lower():
                sanitized = sanitized.replace(dc, f" [BLOCKED:{dc.strip()}] ")
        
        return sanitized
    
    def get_safety_report(self) -> Dict:
        """获取当前安全配置报告"""
        return {
            "rule_count": len(self.rule_engine.rules),
            "dangerous_patterns": self.config.get("dangerous_commands", []),
            "whitelist_prefixes": self.whitelist.allowed_prefixes,
            "status": "active"
        }
