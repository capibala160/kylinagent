import os
import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from .chain import ReasoningChain


class AuditLogger:
    """
    审计日志记录器
    完整记录"接收指令 -> 感知环境 -> 推理决策 -> 安全校验 -> 执行结果"的闭环日志
    """
    
    def __init__(self, log_dir: str = "./logs/audit", retention_days: int = 90):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # 设置目录权限为 750，防止其他用户读取审计日志
        os.chmod(self.log_dir, 0o750)
        self.retention_days = retention_days
        
        # 同时配置 Python logging
        self.logger = logging.getLogger("audit")
        self.logger.setLevel(logging.INFO)
        
        if not self.logger.handlers:
            handler = logging.FileHandler(self.log_dir / "audit.log")
            formatter = logging.Formatter(
                '%(asctime)s | %(levelname)s | %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
    
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
        self.log_event("security_alert", {
            "alert_type": alert_type,
            "command": command,
            "reason": reason,
            "chain_id": chain_id
        }, level="WARNING")
    
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
