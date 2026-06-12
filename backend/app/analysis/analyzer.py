"""根因分析器入口——协调规则引擎与 LLM 增强分析"""

import json
import os
import re
from typing import Any, Dict, List, Optional

from .models import AnalysisResult, DiagnosisReport, Severity
from .strategies import (
    DiskAnalyzer,
    ProcessAnalyzer,
    PerformanceAnalyzer,
    ServiceLogAnalyzer,
    _parse_loadavg,
    _get_cpu_cores,
    _run_cmd,
)


class RootCauseAnalyzer:
    """
    智能化根因分析器

    提供两种分析模式：
    1. **规则分析** (默认): 基于专家规则快速、确定性分析，无需 LLM
    2. **LLM 增强分析**: 在规则分析基础上，将采集数据交给 LLM 做深度综合推理
    """

    def __init__(self, llm_client=None):
        self.disk_analyzer = DiskAnalyzer()
        self.process_analyzer = ProcessAnalyzer()
        self.performance_analyzer = PerformanceAnalyzer()
        self.log_analyzer = ServiceLogAnalyzer()
        self.llm = llm_client

    # ------------------------------------------------------------------
    # 一键诊断接口
    # ------------------------------------------------------------------

    async def diagnose_disk(self, mountpoint: str = "/") -> AnalysisResult:
        """对指定磁盘分区进行根因诊断"""
        # 参数校验：挂载点必须是合法绝对路径且为真实目录
        if not mountpoint or not mountpoint.startswith("/"):
            return AnalysisResult(
                category="disk",
                is_anomaly=True,
                severity=Severity.HIGH,
                root_causes=[],
                summary="挂载点参数不合法：必须为绝对路径",
            )
        if not re.match(r"^[/a-zA-Z0-9_.-]+$", mountpoint):
            return AnalysisResult(
                category="disk",
                is_anomaly=True,
                severity=Severity.HIGH,
                root_causes=[],
                summary="挂载点参数包含非法字符",
            )
        real_mount = os.path.realpath(mountpoint)
        if not os.path.isdir(real_mount):
            return AnalysisResult(
                category="disk",
                is_anomaly=True,
                severity=Severity.HIGH,
                root_causes=[],
                summary=f"挂载点不存在或不是目录: {real_mount}",
            )

        df = _run_cmd(["df", "-h"])
        du = _run_cmd(["du", "-h", "--max-depth=2", "--", real_mount], timeout=30)
        large = _run_cmd(
            ["find", "--", real_mount, "-type", "f", "-size", "+50M", "-exec", "ls", "-lh", "{}", "+"],
            timeout=30,
        )
        return self.disk_analyzer.analyze(df, du, large)

    async def diagnose_process(self) -> AnalysisResult:
        """对系统进程进行根因诊断"""
        ps = _run_cmd(["ps", "aux", "--sort=-%cpu"], timeout=10)
        zombie = _run_cmd(
            ["ps", "-eo", "pid,ppid,stat,comm"],
            timeout=10,
        )
        # 过滤出 Z 状态进程
        zombie_lines = []
        for line in zombie.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 3 and parts[2].startswith("Z"):
                zombie_lines.append(line)
        zombie = "\n".join(zombie_lines)
        return self.process_analyzer.analyze(ps, zombie)

    async def diagnose_performance(self) -> AnalysisResult:
        """对系统性能进行根因诊断"""
        load_1, _, _, _ = _parse_loadavg()
        cores = _get_cpu_cores()
        free = _run_cmd(["free", "-m"], timeout=5)
        iostat = _run_cmd(["iostat", "-x", "1", "2"], timeout=15)
        vmstat = _run_cmd(["vmstat", "1", "3"], timeout=10)
        return self.performance_analyzer.analyze(load_1, cores, free, iostat, vmstat)

    async def diagnose_service_log(self, service_name: str = "") -> AnalysisResult:
        """对指定服务日志进行根因诊断"""
        # 参数校验：服务名必须是合法 systemd 服务名
        if service_name:
            if not re.match(r"^[a-zA-Z0-9_.-]+$", service_name):
                return AnalysisResult(
                    category="service_log",
                    is_anomaly=True,
                    severity=Severity.HIGH,
                    root_causes=[],
                    summary="服务名包含非法字符",
                )
            if service_name.startswith("-"):
                return AnalysisResult(
                    category="service_log",
                    is_anomaly=True,
                    severity=Severity.HIGH,
                    root_causes=[],
                    summary="服务名不能以 '-' 开头",
                )
            log = _run_cmd(
                ["journalctl", "-u", "--", service_name, "--no-pager", "-n", "200"],
                timeout=15,
            )
        else:
            log = _run_cmd(
                ["journalctl", "--priority=err", "--no-pager", "-n", "200", "--since", "1 hour ago"],
                timeout=15,
            )
        return self.log_analyzer.analyze(log, service_name)

    async def comprehensive_diagnosis(self, user_input: str, session_id: str) -> DiagnosisReport:
        """
        综合诊断：根据用户输入自动判断需要诊断的维度，
        执行多维度分析并生成综合报告。
        """
        analyses: List[AnalysisResult] = []
        suggested_actions: List[str] = []
        risk_warning = None

        # 根据用户意图决定诊断维度
        intent = self._detect_intent(user_input)

        if "disk" in intent or "all" in intent:
            result = await self.diagnose_disk("/")
            analyses.append(result)
            if result.is_anomaly:
                for rc in result.root_causes:
                    suggested_actions.append(f"[{rc.category}] {rc.recommendation}")

        if "process" in intent or "all" in intent:
            result = await self.diagnose_process()
            analyses.append(result)
            if result.is_anomaly:
                for rc in result.root_causes:
                    suggested_actions.append(f"[{rc.category}] {rc.recommendation}")

        if "performance" in intent or "cpu" in intent or "memory" in intent or "all" in intent:
            result = await self.diagnose_performance()
            analyses.append(result)
            if result.is_anomaly:
                for rc in result.root_causes:
                    suggested_actions.append(f"[{rc.category}] {rc.recommendation}")

        if "log" in intent or "service" in intent or "all" in intent:
            # 尝试提取服务名
            svc = self._extract_service_name(user_input)
            result = await self.diagnose_service_log(svc)
            analyses.append(result)
            if result.is_anomaly:
                for rc in result.root_causes:
                    suggested_actions.append(f"[{rc.category}] {rc.recommendation}")

        # 综合评估
        has_critical = any(a.severity == Severity.CRITICAL for a in analyses)
        has_warning = any(a.severity == Severity.WARNING for a in analyses)
        anomaly_count = sum(1 for a in analyses if a.is_anomaly)

        if has_critical:
            overall = "⚠️ 系统存在严重异常，建议立即处理"
            risk_warning = "检测到 CRITICAL 级别问题，可能影响系统稳定性"
        elif has_warning:
            overall = "⚡ 系统存在性能或进程异常，建议关注并优化"
        elif anomaly_count > 0:
            overall = "ℹ️ 发现轻微异常，建议持续观察"
        else:
            overall = "✅ 系统状态良好，未检测到明显异常"

        return DiagnosisReport(
            session_id=session_id,
            user_input=user_input,
            analyses=analyses,
            overall_summary=overall,
            suggested_actions=list(set(suggested_actions)),
            risk_warning=risk_warning,
        )

    # ------------------------------------------------------------------
    # LLM 增强分析（可选）
    # ------------------------------------------------------------------

    async def llm_enhanced_analysis(self, analysis_result: AnalysisResult) -> str:
        """
        将规则分析结果交给 LLM，生成更深度的根因解释和修复方案。
        适合复杂场景或需要向非技术人员汇报时使用。
        """
        if self.llm is None:
            return "LLM 未配置，仅返回规则分析结果。"

        prompt = f"""你是一位资深的 Linux 系统运维专家。请根据以下自动采集的系统指标和根因分析结果，生成一份深度的诊断报告。

## 分析目标
{analysis_result.target}

## 是否异常
{"是" if analysis_result.is_anomaly else "否"}

## 严重等级
{analysis_result.severity}

## 已识别的根因
{json.dumps([rc.dict() for rc in analysis_result.root_causes], ensure_ascii=False, indent=2)}

## 原始数据摘要
{json.dumps({k: str(v)[:500] for k, v in analysis_result.raw_data.items()}, ensure_ascii=False, indent=2)}

## 要求
1. 用中文输出，面向系统管理员
2. 对根因进行深度解释（为什么会出现这个问题）
3. 给出优先级排序的修复步骤
4. 提供预防措施建议
5. 如果涉及安全风险，明确标注
"""
        messages = [
            {"role": "system", "content": "你是 Linux 运维专家，擅长根因分析和故障排查。"},
            {"role": "user", "content": prompt},
        ]
        try:
            response = await self.llm.chat_completion(messages)
            if response.get("error"):
                return f"LLM 分析失败: {response.get('message')}"
            content = response["choices"][0]["message"]["content"]
            return content
        except Exception as e:
            return f"LLM 增强分析出错: {str(e)}"

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _detect_intent(self, user_input: str) -> List[str]:
        """简单意图识别，判断需要诊断哪些维度"""
        text = user_input.lower()
        keywords = {
            "disk": ["磁盘", "空间", "满了", "df", "清理", "垃圾", "log", "日志"],
            "process": ["进程", "僵尸", "zombie", "卡死", "无响应", "cpu高"],
            "performance": ["性能", "慢", "卡顿", "负载", "load", "瓶颈"],
            "cpu": ["cpu", "处理器", "计算"],
            "memory": ["内存", "memory", "oom", "swap"],
            "log": ["日志", "报错", "错误", "error", "fail"],
            "service": ["服务", "service", "systemctl", "启动失败"],
        }
        matched = []
        for dim, words in keywords.items():
            if any(w in text for w in words):
                matched.append(dim)
        # 如果没匹配到任何关键词，默认全量诊断
        if not matched:
            matched.append("all")
        return matched

    def _extract_service_name(self, user_input: str) -> str:
        """从用户输入中提取可能的服务名"""
        # 常见服务名匹配
        common_services = [
            "sshd", "nginx", "mysql", "mariadb", "postgresql", "redis",
            "docker", "kubelet", "httpd", "apache2", "mongod", "elasticsearch",
            "jenkins", "gitlab", "prometheus", "grafana", "etcd", "flanneld",
        ]
        lower = user_input.lower()
        for svc in common_services:
            if svc in lower:
                return svc
        # 尝试匹配 systemctl 后面的服务名
        import re
        m = re.search(r"systemctl\s+\w+\s+(\S+)", lower)
        if m:
            return m.group(1)
        m = re.search(r"服务\s*[:：]?\s*(\S+)", user_input)
        if m:
            return m.group(1)
        return ""

    def format_result_for_user(self, result: AnalysisResult) -> str:
        """将分析结果格式化为面向用户的 Markdown 文本"""
        lines = []
        emoji = "✅" if not result.is_anomaly else "⚠️" if result.severity == Severity.WARNING else "🔴"
        lines.append(f"## {emoji} {result.target.upper()} 诊断结果")
        lines.append("")
        lines.append(f"**总结**: {result.summary}")
        lines.append("")

        if result.root_causes:
            lines.append("### 🔍 根因分析")
            for i, rc in enumerate(result.root_causes, 1):
                risk_emoji = {"safe": "🟢", "low": "🟡", "medium": "🟠", "high": "🔴", "critical": "⛔"}.get(rc.risk_level, "⚪")
                lines.append(f"\n**{i}. {rc.category}** {risk_emoji} (置信度: {rc.confidence*100:.0f}%)")
                lines.append(f"- **描述**: {rc.description}")
                if rc.evidence:
                    lines.append(f"- **证据**:")
                    for ev in rc.evidence[:5]:
                        lines.append(f"  - `{ev}`")
                lines.append(f"- **建议**: {rc.recommendation}")
        else:
            lines.append("> 未检测到异常，系统状态良好。")

        return "\n".join(lines)

    def format_report_for_user(self, report: DiagnosisReport) -> str:
        """将综合诊断报告格式化为面向用户的 Markdown 文本"""
        lines = []
        lines.append("# 🔬 智能根因分析报告")
        lines.append("")
        lines.append(f"**查询**: {report.user_input}")
        lines.append("")

        if report.risk_warning:
            lines.append(f"> ⛔ **风险告警**: {report.risk_warning}")
            lines.append("")

        lines.append(f"## 📊 总体评估")
        lines.append(f"{report.overall_summary}")
        lines.append("")

        for analysis in report.analyses:
            lines.append(self.format_result_for_user(analysis))
            lines.append("")

        if report.suggested_actions:
            lines.append("## 🛠️ 推荐修复动作")
            for i, action in enumerate(report.suggested_actions[:10], 1):
                lines.append(f"{i}. {action}")
            lines.append("")

        return "\n".join(lines)
