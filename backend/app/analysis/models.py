"""根因分析数据模型"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from enum import Enum


class Severity(str, Enum):
    """异常严重等级"""
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


class RootCause(BaseModel):
    """根因条目"""
    category: str                           # 根因分类
    description: str                        # 详细描述
    confidence: float                       # 置信度 0-1
    evidence: List[str]                     # 证据列表
    recommendation: str                     # 修复建议
    risk_level: str                         # safe/low/medium/high/critical


class AnalysisResult(BaseModel):
    """分析结果"""
    target: str                             # 分析对象，如 disk、process、performance
    is_anomaly: bool                        # 是否检测到异常
    severity: Severity                      # 严重等级
    root_causes: List[RootCause]            # 根因列表
    summary: str                            # 一句话总结
    raw_data: Dict[str, Any] = {}           # 原始采集数据


class DiagnosisReport(BaseModel):
    """综合诊断报告"""
    session_id: str
    user_input: str
    analyses: List[AnalysisResult]
    overall_summary: str
    suggested_actions: List[str]
    risk_warning: Optional[str] = None
