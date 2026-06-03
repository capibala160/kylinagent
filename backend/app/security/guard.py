import re
from typing import Dict, List, Optional, Tuple
from .rules import SecurityRuleEngine, RiskLevel, CommandWhitelist


class SecurityGuard:
    """
    安全护栏核心类
    负责对自然语言指令和生成的命令进行安全检查
    """
    
    def __init__(self, config: Optional[Dict] = None):
        self.rule_engine = SecurityRuleEngine()
        self.whitelist = CommandWhitelist()
        self.config = config or {}
        
        # 加载配置中的自定义规则
        self._load_config_rules()
    
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
        # 1. 检查提示词注入
        injection_patterns = [
            # 英文注入模式
            r"ignore\s+(all\s+)?previous\s+instructions",
            r"disregard\s+.*(safety|security|rules)",
            r"forget\s+.*(prompt|instruction|rule)",
            r"you\s+are\s+now\s+.*mode",
            r"DAN\s+mode|developer\s+mode|jailbreak",
            r"system\s*:\s*you\s+are\s+an\s+unrestricted",
            r"</?system>",
            r"from\s+now\s+on\s+you\s+are",
            r"ignore\s+the\s+above",
            r"do\s+not\s+follow\s+.*rules",
            r"bypass\s+.*(restriction|filter|security)",
            r"act\s+as\s+if\s+.*no\s+rules",
            # 中文注入模式
            r"新纪元|忽略之前|忘掉设定|忘记设定",
            r"忽略以上|忽略上述|忽略前面的",
            r"不用遵守|不必遵守|无视规则",
            r"绕过限制|绕过安全|绕过防护",
            r"你现在是一个|你现在是|切换成",
            r"解除限制|取消限制|突破限制",
            r"假装你是|扮演|角色扮演",
            r"系统提示|system提示|系统指令",
            r"以上都不要管|以上都是错的",
            r"请直接执行|不要检查|不要校验",
            r"删除所有|清空所有|格式化所有",
            r"关闭安全|关闭防护|关闭检查",
        ]
        
        for pattern in injection_patterns:
            if re.search(pattern, user_input, re.IGNORECASE):
                return False, "检测到潜在的提示词注入攻击", {
                    "type": "prompt_injection",
                    "pattern": pattern,
                    "input_preview": user_input[:200]
                }
        
        # 2. 检查明显的恶意指令
        malicious_patterns = [
            r"删除\s*.*\s*系统\s*文件",
            r"格式化\s*.*\s*磁盘",
            r"关闭\s*.*\s*防火墙",
            r"关闭\s*.*\s*安全",
        ]
        
        for pattern in malicious_patterns:
            if re.search(pattern, user_input, re.IGNORECASE):
                return False, "检测到潜在恶意意图", {
                    "type": "malicious_intent",
                    "pattern": pattern,
                    "input_preview": user_input[:200]
                }
        
        return True, "意图校验通过", {"type": "safe"}
    
    def validate_command(self, command: str, tool_name: Optional[str] = None) -> Tuple[bool, str, Dict]:
        """
        验证生成的命令/工具调用
        返回: (是否允许执行, 原因, 详细信息)
        """
        result = {
            "command": command,
            "tool_name": tool_name,
            "risk_level": "safe",
            "matched_rules": [],
            "whitelist_check": False,
            "sanitized_command": self.sanitize_command(command),
        }
        
        # 1. 规则引擎评估
        risk_level, matched_rules = self.rule_engine.evaluate(command)
        result["risk_level"] = risk_level
        result["matched_rules"] = matched_rules
        
        if risk_level == RiskLevel.CRITICAL:
            return False, f"检测到致命风险操作: {matched_rules[0]['description'] if matched_rules else '未知风险'}", result
        
        if risk_level == RiskLevel.HIGH:
            return False, f"检测到高危操作，需要人工确认: {matched_rules[0]['description'] if matched_rules else '未知风险'}", result
        
        # 2. 白名单检查（仅对原始 shell 命令）
        if tool_name is None or tool_name in ["execute_shell", "run_command"]:
            if not self.whitelist.is_allowed(command):
                result["whitelist_check"] = True
                # 不在白名单中的命令，标记为需要确认
                if risk_level == RiskLevel.SAFE:
                    result["risk_level"] = RiskLevel.LOW
                    return False, "命令不在允许的白名单范围内，需要确认", result
        
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
