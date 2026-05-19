import time
import threading
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SessionState(BaseModel):
    """会话状态"""
    session_id: str
    created_at: float = Field(default_factory=time.time)
    last_active: float = Field(default_factory=time.time)
    message_history: List[Dict[str, str]] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)
    pending_confirmation: Optional[Dict[str, Any]] = None  # 待确认的操作计划
    
    def add_message(self, role: str, content: str):
        self.message_history.append({
            "role": role,
            "content": content,
            "timestamp": time.time()
        })
        self.last_active = time.time()
    
    def get_recent_messages(self, limit: int = 10) -> List[Dict[str, str]]:
        return [{"role": m["role"], "content": m["content"]} 
                for m in self.message_history[-limit:]]
    
    def update_context(self, key: str, value: Any):
        self.context[key] = value
        self.last_active = time.time()


class SessionManager:
    """会话管理器（线程安全）"""
    
    def __init__(self, max_sessions: int = 100, timeout_sec: int = 3600):
        self.sessions: Dict[str, SessionState] = {}
        self.max_sessions = max_sessions
        self.timeout_sec = timeout_sec
        self._lock = threading.Lock()
    
    def get_or_create(self, session_id: str) -> SessionState:
        with self._lock:
            self._cleanup_expired()
            
            if session_id not in self.sessions:
                self.sessions[session_id] = SessionState(session_id=session_id)
            else:
                self.sessions[session_id].last_active = time.time()
            
            return self.sessions[session_id]
    
    def get(self, session_id: str) -> Optional[SessionState]:
        with self._lock:
            return self.sessions.get(session_id)
    
    def delete(self, session_id: str):
        with self._lock:
            if session_id in self.sessions:
                del self.sessions[session_id]
    
    def _cleanup_expired(self):
        now = time.time()
        expired = [
            sid for sid, s in self.sessions.items()
            if now - s.last_active > self.timeout_sec
        ]
        for sid in expired:
            del self.sessions[sid]
        
        # 如果仍然超过最大数量，删除最旧的
        if len(self.sessions) > self.max_sessions:
            sorted_sessions = sorted(self.sessions.items(), key=lambda x: x[1].last_active)
            for sid, _ in sorted_sessions[:len(sorted_sessions) - self.max_sessions]:
                del self.sessions[sid]
