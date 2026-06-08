import os
import json
import time
import logging
import logging.handlers
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Callable
from collections import defaultdict
from .chain import ReasoningChain


class AuditLogger:
    """
    审计日志记录器
    完整记录"接收指令 -> 感知环境 -> 推理决策 -> 安全校验 -> 执行结果"的闭环日志
    
    增强功能：
    - 日志轮转（按大小和时间）
    - 告警机制（支持自定义告警回调）
    - 更丰富的事件类型
    - 日志压缩和自动清理
    """
    
    # 日志轮转配置
    MAX_LOG_SIZE = 10 * 1024 * 1024  # 10MB
    MAX_BACKUP_COUNT = 5
    
    # 告警级别
    ALERT_LEVELS = {
        "critical": 50,
        "warning": 40,
        "info": 30,
    }
    
    def __init__(self, log_dir: str = "./logs/audit", retention_days: int = 90):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # 设置目录权限为 750，防止其他用户读取审计日志
        os.chmod(self.log_dir, 0o750)
        self.retention_days = retention_days
        
        # 告警回调函数列表
        self._alert_callbacks: List[Callable] = []
        
        # 告警统计（用于告警抑制）
        self._alert_stats = defaultdict(lambda: {"count": 0, "last_time": 0})
        self._alert_threshold = 5  # 5分钟内超过此次数触发告警
        
        # 日志轮转计数器
        self._log_file_path = self.log_dir / "audit.log"
        self._current_log_size = 0
        
        # 同时配置 Python logging（带轮转）
        self.logger = logging.getLogger("audit")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False  # 避免重复输出
        
        if not self.logger.handlers:
            handler = logging.handlers.RotatingFileHandler(
                self._log_file_path,
                maxBytes=self.MAX_LOG_SIZE,
                backupCount=self.MAX_BACKUP_COUNT,
                encoding="utf-8"
            )
            formatter = logging.Formatter(
                '%(asctime)s | %(levelname)s | %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        
        # 定期清理过期日志
        self._cleanup_expired_logs()
    
    def add_alert_callback(self, callback: Callable[[Dict], None]):
        """添加告警回调函数"""
        self._alert_callbacks.append(callback)
    
    def _trigger_alert(self, alert_type: str, message: str, details: Dict):
        """触发告警"""
        now = time.time()
        
        # 告警抑制：5分钟内同一类型告警不超过阈值才触发
        alert_key = alert_type
        stats = self._alert_stats[alert_key]
        stats["count"] += 1
        
        if stats["last_time"] == 0 or now - stats["last_time"] > 300:  # 5分钟窗口
            stats["count"] = 1
            stats["last_time"] = now
        elif stats["count"] <= self._alert_threshold:
            # 未超过阈值，不触发告警
            return
        
        alert = {
            "timestamp": now,
            "datetime": datetime.now().isoformat(),
            "alert_type": alert_type,
            "level": "warning" if "error" not in alert_type.lower() else "critical",
            "message": message,
            "details": details,
        }
        
        # 记录告警到日志
        self.logger.error(f"ALERT {alert_type}: {message} | {json.dumps(details, ensure_ascii=False)[:200]}")
        
        # 调用所有告警回调
        for callback in self._alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                self.logger.error(f"Alert callback failed: {e}")
    
    def _cleanup_expired_logs(self):
        """清理过期的日志文件"""
        try:
            cutoff_time = time.time() - self.retention_days * 86400
            
            for file in self.log_dir.glob("*.jsonl"):
                if file.stat().st_mtime < cutoff_time:
                    file.unlink()
                    self.logger.info(f"Cleaned expired log file: {file}")
            
            for file in self.log_dir.glob("audit.log.*"):
                if file.stat().st_mtime < cutoff_time:
                    file.unlink()
                    self.logger.info(f"Cleaned expired rotated log: {file}")
        except Exception as e:
            self.logger.error(f"Failed to cleanup expired logs: {e}")
    
    def _rotate_log(self):
        """轮转日志文件"""
        try:
            if self._log_file_path.exists():
                current_size = self._log_file_path.stat().st_size
                if current_size >= self.MAX_LOG_SIZE:
                    # 压缩旧日志
                    for i in range(self.MAX_BACKUP_COUNT - 1, 0, -1):
                        old_file = self._log_file_path.with_suffix(f".log.{i}")
                        new_file = self._log_file_path.with_suffix(f".log.{i+1}")
                        if old_file.exists():
                            old_file.rename(new_file)
                    
                    # 重命名当前日志
                    self._log_file_path.rename(self._log_file_path.with_suffix(".log.1"))
                    self._current_log_size = 0
                    self.logger.info("Log file rotated")
        except Exception as e:
            self.logger.error(f"Failed to rotate log: {e}")
    
    def log_chain(self, chain: ReasoningChain):
        """记录完整的推理链路"""
        data = chain.to_dict()
        
        # 按日期分文件存储
        date_str = datetime.fromtimestamp(chain.start_time).strftime("%Y-%m-%d")
        filename = self.log_dir / f"chain_{date_str}.jsonl"
        
        with open(filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
        
        # 同时记录到 audit.log
        self.logger.info(
            f"CHAIN {chain.chain_id} | session={chain.session_id} | "
            f"status={chain.final_status} | nodes={len(chain.nodes)} | "
            f"input={chain.user_input[:100]}"
        )
    
    def log_event(self, event_type: str, details: Dict, level: str = "INFO"):
        """记录单个事件"""
        event = {
            "timestamp": time.time(),
            "datetime": datetime.now().isoformat(),
            "event_type": event_type,
            "level": level,
            "details": details
        }
        
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = self.log_dir / f"events_{date_str}.jsonl"
        
        with open(filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        
        log_func = getattr(self.logger, level.lower(), self.logger.info)
        log_func(f"EVENT {event_type} | {json.dumps(details, ensure_ascii=False)[:500]}")
    
    def log_security_alert(self, alert_type: str, command: str, 
                          reason: str, chain_id: Optional[str] = None):
        """记录安全告警"""
        event_data = {
            "alert_type": alert_type,
            "command": command,
            "reason": reason,
            "chain_id": chain_id,
            "severity": "critical" if alert_type in ["blocked", "critical"] else "warning"
        }
        
        # 触发告警（仅对高危告警）
        if event_data["severity"] == "critical":
            self._trigger_alert(
                "security_blocked",
                f"安全拦截: {alert_type}",
                event_data
            )
        
        self.log_event("security_alert", event_data, level="WARNING")
    
    def log_auth_event(self, event_type: str, username: str, 
                      client_ip: str, success: bool, 
                      reason: Optional[str] = None):
        """记录认证事件"""
        event_data = {
            "event_type": event_type,
            "username": username,
            "client_ip": client_ip,
            "success": success,
            "reason": reason
        }
        
        # 登录失败超过阈值触发告警
        if event_type == "login_failed":
            self._trigger_alert(
                "auth_failure",
                f"登录失败: {username} from {client_ip}",
                event_data
            )
        
        level = "INFO" if success else "WARNING"
        self.log_event("auth", event_data, level=level)
    
    def log_privilege_event(self, event_type: str, request_id: str,
                           requested_by: str, command: str,
                           approved_by: Optional[str] = None,
                           status: str = "pending"):
        """记录权限操作事件"""
        event_data = {
            "event_type": event_type,
            "request_id": request_id,
            "requested_by": requested_by,
            "command": command,
            "approved_by": approved_by,
            "status": status
        }
        
        if event_type == "privilege_approved":
            self._trigger_alert(
                "privilege_escalation",
                f"权限提升批准: {requested_by} -> {command}",
                event_data
            )
        
        self.log_event("privilege", event_data, level="INFO")
    
    def log_system_event(self, event_type: str, details: Dict):
        """记录系统事件"""
        self.log_event("system", {
            "event_type": event_type,
            **details
        }, level="INFO")
    
    def log_tool_execution(self, tool_name: str, arguments: Dict, 
                          result: Dict, chain_id: Optional[str] = None):
        """记录工具执行"""
        self.log_event("tool_execution", {
            "tool_name": tool_name,
            "arguments": arguments,
            "result_summary": {
                "success": result.get("success", False),
                "isError": result.get("isError", False),
                "has_output": bool(result.get("stdout", "") or result.get("content", ""))
            },
            "chain_id": chain_id
        }, level="INFO")
    
    def query_chains(self, start_time: Optional[float] = None,
                    end_time: Optional[float] = None,
                    status: Optional[str] = None,
                    limit: int = 100) -> List[Dict]:
        """查询历史链路记录"""
        results = []
        
        for file in sorted(self.log_dir.glob("chain_*.jsonl"), reverse=True):
            with open(file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if start_time and data.get("start_time", 0) < start_time:
                            continue
                        if end_time and data.get("start_time", 0) > end_time:
                            continue
                        if status and data.get("final_status") != status:
                            continue
                        results.append(data)
                        if len(results) >= limit:
                            break
                    except json.JSONDecodeError:
                        continue
            if len(results) >= limit:
                break
        
        return results
    
    def get_chain_by_id(self, chain_id: str) -> Optional[Dict]:
        """根据 ID 查询单条链路"""
        for file in self.log_dir.glob("chain_*.jsonl"):
            with open(file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        if data.get("chain_id") == chain_id:
                            return data
                    except:
                        continue
        return None

    def get_statistics(self, days: int = 7) -> Dict:
        """
        获取审计日志统计分析

        维度：状态分布、时间趋势、工具使用、风险级别、安全拦截、耗时分布
        """
        from collections import Counter, defaultdict
        import statistics as stats

        end_time = time.time()
        start_time = end_time - days * 86400
        chains = self.query_chains(start_time=start_time, end_time=end_time, limit=5000)

        if not chains:
            return self._empty_stats(days)

        # 1. 状态分布
        status_counter = Counter(c.get("final_status", "unknown") for c in chains)

        # 2. 时间趋势（按天聚合）
        daily_trend = defaultdict(int)
        hourly_trend = defaultdict(int)
        for c in chains:
            ts = c.get("start_time", 0)
            day_key = datetime.fromtimestamp(ts).strftime("%m-%d")
            hour_key = datetime.fromtimestamp(ts).strftime("%H:00")
            daily_trend[day_key] += 1
            hourly_trend[hour_key] += 1

        # 3. 工具使用分布
        tool_counter = Counter()
        for c in chains:
            for node in c.get("nodes", []):
                if node.get("node_type") == "tool_call":
                    tool_name = node.get("input_data", {}).get("tool", "unknown")
                    tool_counter[tool_name] += 1

        # 4. 风险级别分布（从 security_check 节点提取）
        risk_counter = Counter()
        for c in chains:
            for node in c.get("nodes", []):
                if node.get("node_type") == "security_check":
                    risk = node.get("output_data", {}).get("risk_level", "safe")
                    risk_counter[risk] += 1

        # 5. 安全拦截统计
        blocked_chains = [c for c in chains if c.get("final_status") == "blocked"]
        blocked_reasons = Counter()
        for c in blocked_chains:
            for node in c.get("nodes", []):
                if node.get("status") == "blocked":
                    desc = node.get("description", "")
                    if "intent" in desc.lower():
                        blocked_reasons["意图拦截"] += 1
                    elif "command" in desc.lower() or "工具" in desc:
                        blocked_reasons["命令拦截"] += 1
                    else:
                        blocked_reasons["其他拦截"] += 1

        # 6. 耗时分布
        durations = [c.get("duration_sec", 0) for c in chains if c.get("duration_sec") is not None]
        duration_stats = {}
        if durations:
            duration_stats = {
                "avg_sec": round(stats.mean(durations), 2),
                "max_sec": round(max(durations), 2),
                "min_sec": round(min(durations), 2),
                "median_sec": round(stats.median(durations), 2),
            }

        # 7. TOP 用户
        user_counter = Counter(c.get("user", "anonymous") for c in chains)

        # 8. 节点类型分布
        node_type_counter = Counter()
        for c in chains:
            for node in c.get("nodes", []):
                node_type_counter[node.get("node_type", "unknown")] += 1

        total = len(chains)
        blocked_count = status_counter.get("blocked", 0)

        return {
            "period_days": days,
            "total": total,
            "blocked_count": blocked_count,
            "block_rate": round(100.0 * blocked_count / total, 1) if total > 0 else 0,
            "status_distribution": dict(status_counter),
            "daily_trend": dict(sorted(daily_trend.items())),
            "hourly_trend": dict(sorted(hourly_trend.items())),
            "top_tools": dict(tool_counter.most_common(10)),
            "risk_distribution": dict(risk_counter),
            "blocked_reasons": dict(blocked_reasons),
            "duration_stats": duration_stats,
            "top_users": dict(user_counter.most_common(5)),
            "node_type_distribution": dict(node_type_counter),
        }

    def _empty_stats(self, days: int) -> Dict:
        return {
            "period_days": days,
            "total": 0,
            "blocked_count": 0,
            "block_rate": 0,
            "status_distribution": {},
            "daily_trend": {},
            "hourly_trend": {},
            "top_tools": {},
            "risk_distribution": {},
            "blocked_reasons": {},
            "duration_stats": {},
            "top_users": {},
            "node_type_distribution": {},
        }


# 全局审计日志实例
_global_audit_logger = None


def get_audit_logger() -> AuditLogger:
    """获取全局审计日志实例"""
    global _global_audit_logger
    if _global_audit_logger is None:
        _global_audit_logger = AuditLogger()
    return _global_audit_logger
