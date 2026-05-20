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


class AuditConfig(BaseModel):
    log_dir: str = "./logs/audit"
    log_level: str = "INFO"
    retention_days: int = 90
    enable_console: bool = True


class AuthConfig(BaseModel):
    session_ttl: int = 28800  # 8小时，单位秒
    enable_dev_token: bool = True  # 是否允许开发Token降级
    cookie_secure: bool = False  # Cookie 是否启用 secure（HTTPS 环境设为 True）


class MCPConfig(BaseModel):
    server_name: str = "kylin-ops-mcp-server"
    version: str = "1.0.0"


class AppConfig(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
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
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return AppConfig(**data)
    return AppConfig()


# 全局配置实例（支持热更新和测试替换）
_config: Optional[AppConfig] = None
_config_path: Optional[str] = None


def get_config(force_reload: bool = False) -> AppConfig:
    """
    获取应用配置。
    
    Args:
        force_reload: 为 True 时强制重新加载配置文件（热更新）
    
    Returns:
        AppConfig 实例
    """
    global _config
    if _config is None or force_reload:
        _config = load_config(_config_path)
    return _config


def set_config_path(path: Optional[str] = None):
    """设置配置文件路径，下次 get_config 时生效"""
    global _config_path
    _config_path = path


def reset_config():
    """重置配置（主要用于单元测试）"""
    global _config
    _config = None
