import os
import yaml
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional


class AgentConfig(BaseModel):
    name: str = "KylinSafeOpsAgent"
    version: str = "1.0.0"
    description: str = "面向麒麟操作系统的安全智能运维Agent"


class LLMConfig(BaseModel):
    provider: str = "openai_compatible"
    api_base: str = "http://localhost:8000/v1"
    api_key: str = "sk-dummy"
    model: str = "deepseek-chat"
    temperature: float = 0.3
    max_tokens: int = 4096
    timeout: int = 60


class SecurityConfig(BaseModel):
    dangerous_commands: List[str] = Field(default_factory=list)
    confirm_required_patterns: List[str] = Field(default_factory=list)
    restricted_user: str = "opsagent"
    allowed_command_prefixes: List[str] = Field(default_factory=list)
    # MCP 工具参数级安全检查配置
    sensitive_read_paths: List[str] = Field(default_factory=list)
    critical_directories: List[str] = Field(default_factory=list)
    allowed_log_paths: List[str] = Field(default_factory=list)
    critical_services: List[str] = Field(default_factory=list)
    protected_pids: List[str] = Field(default_factory=list)


class AuditConfig(BaseModel):
    log_dir: str = "./logs/audit"
    log_level: str = "INFO"
    retention_days: int = 90
    enable_console: bool = True


class AuthConfig(BaseModel):
    session_ttl: int = 2592000  # 30天，单位秒（延长保持登录状态）
    enable_dev_token: bool = False  # 是否允许开发Token降级（生产环境必须关闭）
    cookie_secure: bool = False  # Cookie 是否启用 secure（HTTPS 环境设为 True）


class EmailConfig(BaseModel):
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    sender_name: str = "Kylin Ops Agent"
    enabled: bool = False


class SMSConfig(BaseModel):
    provider: str = "log"       # log / aliyun / twilio / custom
    aliyun_access_key: str = ""
    aliyun_secret: str = ""
    aliyun_sign_name: str = "Kylin Ops"
    aliyun_template_code: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    custom_url: str = ""
    custom_token: str = ""


class MCPConfig(BaseModel):
    server_name: str = "kylin-ops-mcp-server"
    version: str = "1.0.0"


class AppConfig(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    sms: SMSConfig = Field(default_factory=SMSConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)


def load_config(config_path: Optional[str] = None) -> AppConfig:
    if config_path is None:
        # 从项目根目录查找配置文件
        current_file = Path(__file__).resolve()
        project_root = current_file.parent.parent.parent
        config_path = project_root / "config" / "agent.yaml"
    else:
        config_path = Path(config_path)

    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data is None:
                print(f"[WARN] 配置文件为空: {config_path}，使用默认配置")
                return AppConfig()
            return AppConfig(**data)
        except Exception as e:
            print(f"[ERROR] 配置文件解析失败: {config_path}\n  错误: {e}\n  使用默认配置启动")
            return AppConfig()
    return AppConfig()


# 全局配置实例（支持热更新和测试替换）
_config: Optional[AppConfig] = None
_config_path: Optional[str] = None
_config_path_dirty: bool = False


def get_config(force_reload: bool = False) -> AppConfig:
    """
    获取应用配置。
    
    Args:
        force_reload: 为 True 时强制重新加载配置文件（热更新）
    
    Returns:
        AppConfig 实例
    """
    global _config, _config_path_dirty
    if _config is None or force_reload or _config_path_dirty:
        _config = load_config(_config_path)
        _config_path_dirty = False
    return _config


def set_config_path(path: Optional[str] = None):
    """设置配置文件路径，下次 get_config 时自动重新加载"""
    global _config_path, _config_path_dirty
    _config_path = path
    _config_path_dirty = True


def reset_config():
    """重置配置（主要用于单元测试）"""
    global _config, _config_path_dirty
    _config = None
    _config_path_dirty = False
