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
    
    从 messages 中智能提取用户原始输入，避免对整个 prompt（含系统提示、工具描述）
    做关键词匹配导致的误触发。
    """

    # 用户输入信号词到工具的映射（按优先级排序，长词优先匹配）
    _TOOL_KEYWORDS = [
        # 长词/精确匹配优先
        (["磁盘诊断", "空间分析", "disk diagnose"], "diagnose_disk", {"mountpoint": "/"}),
        (["进程诊断", "process diagnose", "僵尸进程分析"], "diagnose_process", {}),
        (["性能诊断", "performance diagnose", "瓶颈分析"], "diagnose_performance", {}),
        (["comprehensive diagnosis"], "comprehensive_diagnosis", {}),
        # 普通关键词
        (["进程", "process", "僵尸", "zombie"], "list_processes", {"sort_by": "cpu", "limit": 20}),
        (["磁盘", "disk", "空间", "空间不足", "full"], "get_disk_usage", {}),
        (["内存", "memory", "mem"], "get_memory_info", {}),
        (["网络", "network", "端口", "port", "连接"], "get_network_connections", {"protocol": "all"}),
        (["日志", "log", "journal"], "search_log", {"keyword": "error", "source": "journalctl", "since": "1 hour ago", "limit": 50}),
        (["服务", "service", "systemctl"], "list_services", {}),
        (["登录", "login", "lastb"], "get_login_history", {"type": "all", "limit": 30}),
        (["cpu", "处理器", "负载"], "get_cpu_info", {}),
        (["启动", "boot", "内核"], "get_kernel_logs", {"level": "err", "limit": 100}),
    ]

    # 诊断类意图（需要 comprehensive_diagnosis）
    _DIAGNOSIS_KEYWORDS = ["诊断", "根因", "为什么", "问题", "故障", "排查", "分析", "慢", "卡顿", "异常", "卡", "崩溃", "失败", "错误", "err", "error", "timeout", "超时"]

    @classmethod
    def _extract_user_query(cls, messages: List[Dict]) -> str:
        """
        从 messages 中提取用户原始查询。
        
        策略：
        1. 遍历所有 message，找到 role='user' 的消息
        2. 如果内容包含 "用户需求:" 前缀（Agent 构造的 prompt），提取冒号后的部分
        3. 否则直接取用户消息内容
        """
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                # Agent 构造的 prompt 格式："用户需求: xxx\n\n可用工具:\n..."
                if "用户需求:" in content:
                    # 提取 "用户需求:" 到 "\n\n可用工具:" 之间的内容
                    start = content.find("用户需求:") + len("用户需求:")
                    end = content.find("\n\n可用工具:")
                    if end > start:
                        return content[start:end].strip()
                    # 备选：只取第一行
                    return content[start:].split("\n")[0].strip()
                return content.strip()
        return ""

    async def chat_completion(self, messages: List[Dict], **kwargs) -> Dict:
        user_query = self._extract_user_query(messages)
        response = self._generate_mock_response(user_query)
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": response
                }
            }]
        }

    async def chat_completion_stream(self, messages: List[Dict], **kwargs):
        user_query = self._extract_user_query(messages)
        response = self._generate_mock_response(user_query)
        for word in response:
            yield word

    async def simple_chat(self, user_message: str, system_prompt: Optional[str] = None) -> str:
        return self._generate_mock_response(user_message)

    def _generate_mock_response(self, user_query: str) -> str:
        """
        基于用户原始查询生成模拟回复。
        只匹配用户输入部分，避免被系统提示/工具描述干扰。
        """
        if not user_query:
            return self._build_tool_response(
                "get_system_info",
                {},
                "用户请求不明确，尝试获取系统概览",
                "正在获取系统基本信息..."
            )

        query_lower = user_query.lower()

        # 1. 先检查精确/长词匹配（避免短词误触发）
        for keywords, tool_name, arguments in self._TOOL_KEYWORDS:
            if any(kw.lower() in query_lower for kw in keywords):
                # 如果是诊断类关键词，使用 comprehensive_diagnosis
                if tool_name == "comprehensive_diagnosis":
                    return self._build_tool_response(
                        "comprehensive_diagnosis",
                        {"query": user_query},
                        "用户请求诊断系统问题，使用综合智能诊断工具",
                        "正在进行综合根因分析..."
                    )
                return self._build_tool_response(
                    tool_name, arguments,
                    f"用户请求涉及 {keywords[0]}，调用对应工具",
                    f"正在处理 {keywords[0]} 相关请求..."
                )

        # 2. 检查是否为通用诊断意图
        if any(kw in query_lower for kw in self._DIAGNOSIS_KEYWORDS):
            return self._build_tool_response(
                "comprehensive_diagnosis",
                {"query": user_query},
                "用户请求诊断系统问题，使用综合智能诊断工具",
                "正在进行综合根因分析..."
            )

        # 3. 兜底：系统概览
        return self._build_tool_response(
            "get_system_info",
            {},
            "用户请求不明确，尝试获取系统概览",
            "正在获取系统基本信息..."
        )

    @staticmethod
    def _build_tool_response(tool: str, arguments: Dict, thought: str, explanation: str) -> str:
        """构建标准化的工具调用 JSON 响应"""
        import json
        return json.dumps({
            "thought": thought,
            "action": "call_tool",
            "tool": tool,
            "arguments": arguments,
            "risk_assessment": "safe",
            "explanation": explanation
        }, ensure_ascii=False)
    
    async def close(self):
        pass


def get_llm_client(use_mock: bool = False) -> LLMClient:
    if use_mock:
        return MockLLMClient()
    return LLMClient()
