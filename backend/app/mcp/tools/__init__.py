# 导入所有工具模块以自动注册
from . import system, process, network, disk, file, diagnose, service

from .base import get_registry, register_tool, BaseTool

__all__ = ["get_registry", "register_tool", "BaseTool"]
