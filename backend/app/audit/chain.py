import time
import uuid
from typing import Any, Dict, List, Optional
from enum import Enum
from pydantic import BaseModel, Field


class ChainNodeType(str, Enum):
    INTENT_RECEIVED = "intent_received"      # 接收指令
    ENV_PERCEPTION = "env_perception"        # 环境感知
    REASONING = "reasoning"                   # 推理决策
    SECURITY_CHECK = "security_check"        # 安全校验
    TOOL_CALL = "tool_call"                   # 工具调用
    EXECUTION = "execution"                   # 执行动作
    RESULT = "result"                         # 执行结果
    ERROR = "error"                           # 错误节点


class ChainNode(BaseModel):
    node_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    node_type: ChainNodeType
    timestamp: float = Field(default_factory=time.time)
    description: str = ""
    input_data: Dict[str, Any] = Field(default_factory=dict)
    output_data: Dict[str, Any] = Field(default_factory=dict)
    duration_ms: Optional[float] = None
    status: str = "success"  # success, failed, blocked, pending
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ReasoningChain(BaseModel):
    """推理链路，记录一次完整的运维操作闭环"""
    
    chain_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    user: str = ""  # 操作用户身份
    user_input: str = ""
    start_time: float = Field(default_factory=time.time)
    end_time: Optional[float] = None
    nodes: List[ChainNode] = Field(default_factory=list)
    final_status: str = "running"  # running, completed, failed, blocked
    summary: str = ""
    
    def add_node(self, node_type: ChainNodeType, description: str,
                 input_data: Optional[Dict] = None,
                 output_data: Optional[Dict] = None,
                 status: str = "success",
                 metadata: Optional[Dict] = None) -> ChainNode:
        node = ChainNode(
            node_type=node_type,
            description=description,
            input_data=input_data or {},
            output_data=output_data or {},
            status=status,
            metadata=metadata or {}
        )
        self.nodes.append(node)
        return node
    
    def finalize(self, status: str, summary: str = ""):
        self.end_time = time.time()
        self.final_status = status
        self.summary = summary
    
    def to_dict(self) -> Dict:
        return {
            "chain_id": self.chain_id,
            "session_id": self.session_id,
            "user": self.user,
            "user_input": self.user_input,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_sec": round(self.end_time - self.start_time, 3) if self.end_time else None,
            "final_status": self.final_status,
            "summary": self.summary,
            "nodes": [
                {
                    "node_id": n.node_id,
                    "node_type": n.node_type,
                    "timestamp": n.timestamp,
                    "description": n.description,
                    "input_data": n.input_data,
                    "output_data": n.output_data,
                    "status": n.status,
                    "duration_ms": n.duration_ms,
                    "metadata": n.metadata
                }
                for n in self.nodes
            ]
        }
    
    def get_execution_path(self) -> str:
        """获取执行路径的简洁描述"""
        path = " -> ".join([n.node_type.value for n in self.nodes])
        return path
