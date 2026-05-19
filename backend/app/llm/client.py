import json
import httpx
from typing import Any, Dict, List, Optional, AsyncGenerator
from ..config import get_config, LLMConfig


class LLMClient:
    """
    大模型客户端，兼容 OpenAI API 格式
    支持 DeepSeek、Qwen 等国产开源模型
    当外部模型服务不可用时，自动回退到 MockLLMClient
    """
    
    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or get_config().llm
        self.client = httpx.AsyncClient(timeout=self.config.timeout)
        self._mock_fallback = MockLLMClient()
        self._fallback_active = False
    
    async def _check_service_available(self) -> bool:
        """探测模型服务是否可用"""
        try:
            base_url = self.config.api_base.replace("/v1", "").rstrip("/")
            response = await self.client.get(f"{base_url}/models", timeout=3)
            return response.status_code < 500
        except Exception:
            return False
    
    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None
    ) -> Dict[str, Any]:
        """非流式聊天补全，模型不可用时自动回退到 Mock"""
        # 如果已知模型服务不可用，直接使用 Mock
        if self._fallback_active:
            return await self._mock_fallback.chat_completion(messages, temperature=temperature,
                                                               max_tokens=max_tokens, stream=stream,
                                                               tools=tools, tool_choice=tool_choice)
        
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature or self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
            "stream": stream
        }
        
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            response = await self.client.post(
                f"{self.config.api_base}/chat/completions",
                json=payload,
                headers=headers
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            # 404 或其他 HTTP 错误，说明模型服务未配置或不可用
            if e.response.status_code in (404, 502, 503, 504):
                self._fallback_active = True
                return await self._mock_fallback.chat_completion(messages, temperature=temperature,
                                                                   max_tokens=max_tokens, stream=stream,
                                                                   tools=tools, tool_choice=tool_choice)
            return {
                "error": True,
                "message": f"HTTP 错误: {e.response.status_code}",
                "detail": e.response.text[:500]
            }
        except (httpx.ConnectError, httpx.NetworkError, httpx.TimeoutException) as e:
            # 连接失败，激活 Mock 回退
            self._fallback_active = True
            return await self._mock_fallback.chat_completion(messages, temperature=temperature,
                                                               max_tokens=max_tokens, stream=stream,
                                                               tools=tools, tool_choice=tool_choice)
        except Exception as e:
            return {
                "error": True,
                "message": f"请求失败: {str(e)}"
            }
    
    async def chat_completion_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> AsyncGenerator[str, None]:
        """流式聊天补全（SSE 格式），模型不可用时回退到 Mock"""
        if self._fallback_active:
            async for chunk in self._mock_fallback.chat_completion_stream(messages, temperature=temperature,
                                                                            max_tokens=max_tokens):
                yield chunk
            return
        
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature or self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
            "stream": True
        }
        
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            async with self.client.stream(
                "POST",
                f"{self.config.api_base}/chat/completions",
                json=payload,
                headers=headers
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.NetworkError, httpx.TimeoutException) as e:
            self._fallback_active = True
            async for chunk in self._mock_fallback.chat_completion_stream(messages, temperature=temperature,
                                                                            max_tokens=max_tokens):
                yield chunk
        except Exception as e:
            yield f"[ERROR] 流式请求失败: {str(e)}"
    
    async def simple_chat(self, user_message: str, system_prompt: Optional[str] = None) -> str:
        """简化版单轮对话，模型不可用时回退到 Mock"""
        if self._fallback_active:
            return await self._mock_fallback.simple_chat(user_message, system_prompt=system_prompt)
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})
        
        result = await self.chat_completion(messages)
        
        if result.get("error"):
            return f"模型调用失败: {result.get('message', '未知错误')}"
        
        try:
            return result["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            return "模型返回格式异常"
    
    def build_tool_messages(self, user_input: str, tools_description: str, 
                           context: str = "", system_prompt: Optional[str] = None) -> List[Dict[str, str]]:
        """构建带工具描述的 messages"""
        from .prompts import TOOL_CALL_PROMPT
        
        prompt = TOOL_CALL_PROMPT.format(
            tools_description=tools_description,
            user_input=user_input,
            context=context or "暂无系统上下文信息"
        )
        
        messages = [
            {"role": "system", "content": system_prompt or ""},
            {"role": "user", "content": prompt}
        ]
        return messages
    
    async def close(self):
        await self.client.aclose()
        await self._mock_fallback.close()


class MockLLMClient:
    """
    模拟 LLM 客户端，用于无模型环境下的测试
    """
    
    async def chat_completion(self, messages: List[Dict], **kwargs) -> Dict:
        user_msg = messages[-1]["content"] if messages else ""
        
        # 简单的规则匹配，模拟智能回复
        response = self._generate_mock_response(user_msg)
        
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": response
                }
            }]
        }
    
    async def chat_completion_stream(self, messages: List[Dict], **kwargs):
        response = self._generate_mock_response(messages[-1]["content"] if messages else "")
        for word in response:
            yield word
    
    async def simple_chat(self, user_message: str, system_prompt: Optional[str] = None) -> str:
        return self._generate_mock_response(user_message)
    
    def _generate_mock_response(self, user_input: str) -> str:
        user_lower = user_input.lower()
        
        if any(k in user_lower for k in ["进程", "process", "僵尸", "zombie"]):
            return '{"thought": "用户想了解进程信息，先获取进程列表", "action": "call_tool", "tool": "list_processes", "arguments": {"sort_by": "cpu", "limit": 20}, "risk_assessment": "safe", "explanation": "正在获取系统进程列表..."}'
        elif any(k in user_lower for k in ["磁盘", "disk", "空间", "空间不足", "full"]):
            return '{"thought": "用户关心磁盘空间，获取磁盘使用情况", "action": "call_tool", "tool": "get_disk_usage", "arguments": {}, "risk_assessment": "safe", "explanation": "正在检查磁盘使用情况..."}'
        elif any(k in user_lower for k in ["内存", "memory", "mem"]):
            return '{"thought": "用户想了解内存使用情况", "action": "call_tool", "tool": "get_memory_info", "arguments": {}, "risk_assessment": "safe", "explanation": "正在获取内存信息..."}'
        elif any(k in user_lower for k in ["网络", "network", "端口", "port", "连接"]):
            return '{"thought": "用户想了解网络状况", "action": "call_tool", "tool": "get_network_connections", "arguments": {"protocol": "all"}, "risk_assessment": "safe", "explanation": "正在检查网络连接..."}'
        elif any(k in user_lower for k in ["日志", "log", "journal"]):
            return '{"thought": "用户想查看日志", "action": "call_tool", "tool": "search_log", "arguments": {"keyword": "error", "source": "journalctl", "since": "1 hour ago", "limit": 50}, "risk_assessment": "safe", "explanation": "正在搜索近期错误日志..."}'
        elif any(k in user_lower for k in ["诊断", "根因", "为什么", "问题", "故障", "排查", "分析", "慢", "卡顿", "异常"]):
            return '{"thought": "用户请求诊断系统问题，使用综合智能诊断工具", "action": "call_tool", "tool": "comprehensive_diagnosis", "arguments": {"query": "' + user_input + '"}, "risk_assessment": "safe", "explanation": "正在进行综合根因分析..."}'
        elif any(k in user_lower for k in ["磁盘诊断", "空间分析", "disk diagnose"]):
            return '{"thought": "用户请求磁盘根因诊断", "action": "call_tool", "tool": "diagnose_disk", "arguments": {"mountpoint": "/"}, "risk_assessment": "safe", "explanation": "正在诊断磁盘空间问题..."}'
        elif any(k in user_lower for k in ["进程诊断", "process diagnose", "僵尸进程分析"]):
            return '{"thought": "用户请求进程根因诊断", "action": "call_tool", "tool": "diagnose_process", "arguments": {}, "risk_assessment": "safe", "explanation": "正在诊断进程问题..."}'
        elif any(k in user_lower for k in ["性能诊断", "performance diagnose", "瓶颈分析"]):
            return '{"thought": "用户请求性能根因诊断", "action": "call_tool", "tool": "diagnose_performance", "arguments": {}, "risk_assessment": "safe", "explanation": "正在诊断性能瓶颈..."}'
        else:
            return '{"thought": "用户请求不明确，尝试获取系统概览", "action": "call_tool", "tool": "get_system_info", "arguments": {}, "risk_assessment": "safe", "explanation": "正在获取系统基本信息..."}'
    
    async def close(self):
        pass


def get_llm_client(use_mock: bool = False) -> LLMClient:
    if use_mock:
        return MockLLMClient()
    return LLMClient()
