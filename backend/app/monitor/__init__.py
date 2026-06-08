"""系统监控模块

提供系统资源监控数据采集和缓存，支持 Linux 环境实时采集
和 Windows 开发环境 Mock 数据。
"""

from .collector import SystemMonitor, get_monitor

__all__ = ["SystemMonitor", "get_monitor"]
