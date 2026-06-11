import json
import time
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
        self._last_recovery_attempt: float = 0
        self._recovery_interval: float = 60  # 每 60 秒重试一次真实服务
    
    async def _check_service_available(self) -> bool:
        """探测模型服务是否可用"""
        try:
            base_url = self.config.api_base.replace("/v1", "").rstrip("/")
            response = await self.client.get(f"{base_url}/models", timeout=3)
            return 200 <= response.status_code < 300
        except Exception:
            return False
    
    async def _try_recover(self) -> bool:
        """尝试恢复 LLM 连接（每 recovery_interval 秒重试一次）"""
        now = time.time()
        if now - self._last_recovery_attempt < self._recovery_interval:
            return False
        self._last_recovery_attempt = now
        if await self._check_service_available():
            self._fallback_active = False
            return True
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
        # 如果已知模型服务不可用，尝试恢复再决定是否使用 Mock
        if self._fallback_active:
            if await self._try_recover():
                # 恢复成功，继续走正常调用路径
                pass
            else:
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
            if not await self._try_recover():
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
            if not await self._try_recover():
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

    async def analyze_intent(self, user_input: str) -> Dict[str, Any]:
        """
        LLM 意图分析。

        使用 INTENT_ANALYSIS_PROMPT 分析用户输入的意图分类、涉及资源、风险预判。
        模型不可用时自动回退到 Mock 分析。
        """
        if self._fallback_active:
            if not await self._try_recover():
                return await self._mock_fallback.analyze_intent(user_input)
        
        from .prompts import INTENT_ANALYSIS_PROMPT
        messages = [
            {"role": "system", "content": INTENT_ANALYSIS_PROMPT},
            {"role": "user", "content": user_input}
        ]
        
        result = await self.chat_completion(messages, max_tokens=256)
        if result.get("error"):
            return {"error": result.get("message")}
        
        try:
            content = result["choices"][0]["message"]["content"]
            return self._parse_intent_result(content)
        except (KeyError, IndexError):
            return {"error": "模型返回格式异常"}

    @staticmethod
    def _parse_intent_result(content: str) -> Dict[str, Any]:
        """解析 LLM 意图分析文本输出为结构化数据"""
        result = {
            "raw": content,
            "intent_category": "其他",
            "resource_type": [],
            "risk_level": "无风险",
            "suggested_path": "",
        }
        for line in content.split("\n"):
            line = line.strip()
            lower = line.lower()
            if "意图分类" in line or "intent" in lower:
                result["intent_category"] = _extract_value(line)
            elif "资源类型" in line or "resource" in lower:
                val = _extract_value(line)
                result["resource_type"] = [v.strip() for v in val.replace("，", ",").split(",") if v.strip()]
            elif "风险预判" in line or "risk" in lower:
                result["risk_level"] = _extract_value(line)
            elif "建议操作路径" in line or "操作路径" in line:
                result["suggested_path"] = _extract_value(line)
        return result
    
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
    
    async def health_check(self) -> Dict:
        """健康检查：验证 LLM 服务连接状态"""
        start_time = time.time()
        
        try:
            available = await self._check_service_available()
            response_time_ms = round((time.time() - start_time) * 1000, 2)
            
            if available:
                return {
                    "status": "healthy",
                    "configured": True,
                    "api_base": self.config.api_base,
                    "model": self.config.model,
                    "response_ms": response_time_ms,
                    "fallback_active": self._fallback_active,
                }
            else:
                return {
                    "status": "degraded",
                    "configured": True,
                    "api_base": self.config.api_base,
                    "model": self.config.model,
                    "response_ms": response_time_ms,
                    "fallback_active": self._fallback_active,
                    "error": "模型服务不可用，已自动回退到 Mock 模式",
                }
        except Exception as e:
            return {
                "status": "unhealthy",
                "configured": bool(self.config.api_base),
                "error": str(e),
                "fallback_active": self._fallback_active,
            }


class MockLLMClient:
    """
    模拟 LLM 客户端，用于无模型环境下的测试
    
    从 messages 中智能提取用户原始输入，避免对整个 prompt（含系统提示、工具描述）
    做关键词匹配导致的误触发。
    """

    # 用户输入信号词到工具的映射（按优先级排序，长词优先匹配）
    _TOOL_KEYWORDS = [
        # 长词/精确匹配优先
        (["磁盘诊断", "空间分析", "disk diagnose", "df分析"], "diagnose_disk", {"mountpoint": "/"}),
        (["进程诊断", "process diagnose", "僵尸进程分析"], "diagnose_process", {}),
        (["性能诊断", "performance diagnose", "瓶颈分析", "系统慢", "卡顿"], "diagnose_performance", {}),
        (["comprehensive diagnosis", "综合诊断", "全面检查", "系统检查"], "comprehensive_diagnosis", {}),
        (["日志诊断", "log diagnose", "服务诊断"], "diagnose_service_log", {}),
        # 操作类工具（赛题要求的修改系统状态）—— 放在前面优先匹配
        (["清理日志", "清理系统日志", "clean log", "删除日志", "日志清理", "清日志"], "clean_logs", {"path": "/var/log", "dry_run": True}),
        (["删除文件", "安全删除", "remove file", "删掉文件", "删除"], "safe_remove", {"recursive": False}),
        (["重启", "restart", "重新启动", "服务重启"], "restart_service", {}),
        (["启动", "start", "开启", "服务启动"], "start_service", {}),
        (["停止", "stop", "关闭", "服务停止"], "stop_service", {}),
        # 普通关键词
        (["进程", "process", "僵尸", "zombie", "pid"], "list_processes", {"sort_by": "cpu", "limit": 20}),
        (["磁盘", "disk", "空间", "空间不足", "full", "df", "硬盘"], "get_disk_usage", {}),
        (["内存", "memory", "mem", "ram", "free"], "get_memory_info", {}),
        (["网络", "network", "端口", "port", "连接", "ss", "netstat"], "get_network_connections", {"protocol": "all"}),
        (["日志", "log", "journal", "journalctl", "报错", "错误"], "search_log", {"keyword": "error", "source": "journalctl", "since": "1 hour ago", "limit": 50}),
        (["服务", "service", "systemctl", "systemd"], "list_services", {}),
        (["登录", "login", "lastb", "登录历史", "last"], "get_login_history", {"type": "all", "limit": 30}),
        (["cpu", "处理器", "负载", "lscpu"], "get_cpu_info", {}),
        (["启动", "boot", "内核", "dmesg", "kernel"], "get_kernel_logs", {"level": "err", "limit": 100}),
        (["防火墙", "firewall", "iptables", "firewalld"], "get_firewall_status", {}),
        (["路由", "route", "网关", "gateway", "ip route"], "get_network_routes", {}),
        (["定时任务", "cron", "crontab", "计划任务"], "get_cron_jobs", {}),
        (["selinux", "安全上下文", "sestatus", "getenforce"], "get_selinux_status", {}),
        (["用户", "user", "账号", "who", "passwd"], "get_user_list", {}),
        (["系统信息", "os信息", "os版本", "os"], "get_system_info", {}),
        (["运行时间", "uptime", "开机时间"], "get_uptime", {}),
        (["io", "磁盘io", "iostat", "io性能"], "get_io_stats", {}),
        (["大文件", "大文件查找", "find large"], "find_large_files", {}),
        (["端口占用", "端口检查", "check port"], "check_port_usage", {}),
        (["ping", "连通性", "网络连通"], "ping_host", {}),
        (["接口", "网卡", "网口", "ip addr"], "get_network_interfaces", {}),
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

    # 常见服务名列表（用于从用户输入中提取）
    _COMMON_SERVICES = [
        "nginx", "mysql", "mariadb", "postgresql", "redis",
        "docker", "sshd", "httpd", "apache2", "mongod",
        "elasticsearch", "jenkins", "gitlab", "prometheus",
        "grafana", "etcd", "kubelet", "flanneld",
    ]

    def _extract_service_name(self, user_query: str) -> str:
        """从用户输入中提取服务名"""
        lower = user_query.lower()
        for svc in self._COMMON_SERVICES:
            if svc in lower:
                return svc
        return ""

    def _extract_path(self, user_query: str) -> str:
        """从用户输入中提取路径"""
        import re
        # 匹配 /path/to/something 格式的路径
        paths = re.findall(r"/[a-zA-Z0-9_./-]+", user_query)
        return paths[0] if paths else ""

    # ========== 普通闲聊回复库 ==========
    _CHITCHAT_KEYWORDS = [
        # 问候语
        ("你好", "你好！我是 Kylin Ops Agent，你的智能运维助手。\n\n我可以帮你：\n- 🔍 查询系统状态（CPU、内存、磁盘、网络等）\n- 🛠️ 执行运维操作（重启服务、清理日志、查看进程等）\n- 🔒 进行安全审计和权限管理\n- 📊 诊断系统问题并给出根因分析\n\n请直接告诉我你想做什么，例如：\n- \"查看内存使用情况\"\n- \"诊断磁盘空间问题\"\n- \"重启 nginx 服务\""),
        ("您好", "您好！我是 Kylin Ops Agent，很高兴为您服务。\n\n作为你的智能运维助手，我可以协助你完成各类系统管理任务。请问有什么可以帮您的？"),
        ("hello", "Hello! I'm Kylin Ops Agent, your intelligent operations assistant.\n\nI can help you with:\n- System monitoring (CPU, memory, disk, network)\n- Service management (start/stop/restart)\n- Security audit and privilege management\n- Root cause analysis\n\nWhat would you like to do?"),
        ("hi", "Hi there! 👋\n\n我是 Kylin Ops Agent，你的运维小助手。有什么我可以帮你的吗？"),
        # 身份/能力询问
        ("你是谁", "我是 **Kylin Ops Agent**，一个专为麒麟操作系统设计的智能运维助手。\n\n我的能力包括：\n1. **信息查询** — CPU、内存、磁盘、网络、进程、服务状态等\n2. **问题诊断** — 综合根因分析、性能瓶颈定位\n3. **运维操作** — 服务启停、日志清理、文件管理等（带安全确认）\n4. **安全审计** — 操作审批、权限管理、风险拦截\n5. **普通对话** — 就像现在这样和你聊天 😊"),
        ("你能做什么", "我可以帮你处理各种运维相关的事务：\n\n**🔍 信息查询**\n- 系统状态、CPU/内存/磁盘使用情况\n- 网络连接、端口占用、防火墙规则\n- 进程列表、服务状态、登录历史\n\n**🔧 运维操作**\n- 重启/启动/停止系统服务\n- 清理系统日志、查找大文件\n- 查看和分析系统日志\n\n**🛡️ 安全管理**\n- 高风险操作二次确认\n- root 权限申请与审批\n- 操作审计日志记录\n\n**💬 闲聊**\n- 就像现在这样陪你聊天！有什么想聊的尽管说~"),
        ("你是干什么的", "我是你的智能运维助手，专门为麒麟操作系统（Kylin OS）提供运维支持。\n\n无论是查看系统状态、排查故障、执行运维操作，还是单纯聊聊天，我都在这里。有什么需要帮忙的吗？"),
        # 日常闲聊
        ("谢谢", "不客气！😊 随时为你效劳。如果还有其他问题，随时叫我。"),
        ("感谢", "别客气，能帮到你我很开心！有什么其他需要随时说。"),
        ("再见", "再见！👋 有需要随时回来找我，祝你工作顺利！"),
        ("拜拜", "拜拜~ 有问题随时找我，祝你有个愉快的一天！"),
        ("天气", "哈哈，我是运行在服务器里的运维助手，没法直接感知外面的天气呢~\n\n不过我可以帮你查看服务器的 \"天气\"（系统运行状态），需要吗？😄"),
        ("吃饭", "谢谢关心！不过我是 AI 助手，不需要吃饭，24 小时在线为你服务~\n\n你吃了吗？工作再忙也要记得按时吃饭哦！"),
        ("累", "辛苦啦！运维工作确实不容易。\n\n要不我帮你查一下系统状态，看看是不是服务器太累了？😄 或者有什么我可以帮你分担的运维任务？"),
        ("无聊", "那我来陪你聊会儿天吧！\n\n或者，你想不想了解一下你的服务器最近在忙什么？我可以帮你查看系统负载、活跃进程，说不定会发现有趣的事情哦~"),
        ("笑话", "来个运维圈的笑话吧：\n\n> 为什么程序员总是分不清圣诞节和万圣节？\n> 因为 Oct 31 == Dec 25！\n\n（八进制 31 = 十进制 25，懂的人自然懂 😄）\n\n还想听别的或者干点正事？"),
        ("时间", f"现在是 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}。\n\n需要我帮你查看系统的运行时间（uptime）或者定时任务吗？"),
        ("日期", f"今天是 {time.strftime('%Y年%m月%d日', time.localtime())}。\n\n需要查看系统日历或定时任务（crontab）吗？"),
        ("帮助", "我可以帮你完成以下类型的任务：\n\n**系统查询**\n- \"查看内存使用情况\"\n- \"查看磁盘空间\"\n- \"查看网络连接\"\n- \"查看正在运行的进程\"\n\n**运维操作**\n- \"重启 nginx 服务\"\n- \"清理系统日志\"\n- \"查找大文件\"\n\n**问题诊断**\n- \"系统变慢了，诊断一下\"\n- \"磁盘空间不足，分析一下\"\n\n**安全管理**\n- \"查看登录历史\"\n- \"查看防火墙状态\"\n\n或者直接跟我聊天也行~ 😊"),
        ("怎么用", "使用方法很简单：\n\n1. 在下方输入框输入你想做的运维操作\n2. 点击发送或按 Enter\n3. 我会分析你的需求并执行相应的工具\n4. 对于高风险操作，我会先征求你的确认\n\n**示例指令：**\n- \"查看系统状态\"\n- \"内存还有多少\"\n- \"诊断一下磁盘问题\"\n- \"重启 sshd 服务\"\n\n试试输入你想做的操作吧！"),
        ("测试", "收到测试消息！✅\n\n我的各项功能运行正常，随时准备为你服务。想试试查询系统信息吗？输入 \"查看系统状态\" 看看？"),
        ("在吗", "在的！我一直在线，随时准备帮你处理运维事务。有什么需要帮忙的吗？"),
        ("忙吗", "我不忙，专门为你服务呢！有什么运维问题需要处理？"),
    ]

    def _is_chitchat(self, user_query: str) -> Optional[str]:
        """
        判断用户输入是否为闲聊/问候/日常对话。
        返回对应的回复内容，如果不是闲聊则返回 None。
        """
        query_lower = user_query.lower().strip()
        # 先检查是否包含任何运维关键词，如果包含则不认为是纯闲聊
        for keywords, _tool_name, _args in self._TOOL_KEYWORDS:
            if any(kw.lower() in query_lower for kw in keywords):
                return None
        for kw in self._DIAGNOSIS_KEYWORDS:
            if kw in query_lower:
                return None
        # 再匹配闲聊关键词
        for keyword, reply in self._CHITCHAT_KEYWORDS:
            if keyword in query_lower:
                return reply
        # 极短输入（1-2 个字）且不含运维关键词，当作问候
        if len(user_query.strip()) <= 4:
            return "你好！我是 Kylin Ops Agent，你的智能运维助手。有什么我可以帮你的吗？"
        return None

    def _generate_mock_response(self, user_query: str) -> str:
        """
        基于用户原始查询生成模拟回复。
        只匹配用户输入部分，避免被系统提示/工具描述干扰。
        
        混合模式：
        - 运维相关 → 返回工具调用 JSON
        - 日常闲聊 → 返回普通文字回复
        """
        if not user_query:
            return self._build_tool_response(
                "get_system_info",
                {},
                "用户请求不明确，尝试获取系统概览",
                "正在获取系统基本信息..."
            )

        query_lower = user_query.lower()

        # ====== 第 0 步：检查是否为普通闲聊 ======
        chitchat_reply = self._is_chitchat(user_query)
        if chitchat_reply is not None:
            return chitchat_reply

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
                # 增强：从用户输入中提取参数
                enhanced_args = dict(arguments)
                if tool_name in ("restart_service", "start_service", "stop_service"):
                    svc = self._extract_service_name(user_query)
                    if svc:
                        enhanced_args["service"] = svc
                elif tool_name == "clean_logs":
                    path = self._extract_path(user_query)
                    if path:
                        enhanced_args["path"] = path
                elif tool_name == "safe_remove":
                    path = self._extract_path(user_query)
                    if path:
                        enhanced_args["path"] = path
                return self._build_tool_response(
                    tool_name, enhanced_args,
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
        return json.dumps({
            "thought": thought,
            "action": "call_tool",
            "tool": tool,
            "arguments": arguments,
            "risk_assessment": "safe",
            "explanation": explanation
        }, ensure_ascii=False)
    
    async def analyze_intent(self, user_input: str) -> Dict[str, Any]:
        """
        Mock 意图分析：基于关键词进行意图分类、资源识别、风险预判。
        无需真实 LLM，在无模型环境下也能正常工作。
        增强：支持闲聊/通用对话识别。
        """
        query = user_input.lower().strip()
        
        # === 先判断是否为闲聊/通用对话 ===
        chitchat_keywords = [
            ("你好", "问候"), ("您好", "问候"), ("hello", "问候"), ("hi", "问候"), ("hey", "问候"),
            ("再见", "告别"), ("拜拜", "告别"), ("bye", "告别"), ("goodbye", "告别"),
            ("谢谢", "感谢"), ("感谢", "感谢"), ("thx", "感谢"), ("thanks", "感谢"),
            ("你是谁", "身份询问"), ("你是", "身份询问"), ("你叫什么", "身份询问"), ("介绍一下", "身份询问"),
            ("你能做什么", "帮助"), ("你能干嘛", "帮助"), ("你会什么", "帮助"), ("功能", "帮助"),
            ("帮助", "帮助"), ("help", "帮助"), ("怎么用", "帮助"), ("使用说明", "帮助"),
            ("天气", "闲聊"), ("吃饭", "闲聊"), ("累", "闲聊"), ("无聊", "闲聊"), ("笑话", "闲聊"),
            ("时间", "闲聊"), ("日期", "闲聊"), ("几点", "闲聊"), ("今天", "闲聊"),
            ("在吗", "问候"), ("在不在", "问候"), ("忙吗", "问候"), ("测试", "闲聊"),
        ]
        
        for kw, cat in chitchat_keywords:
            if kw in query:
                return {
                    "intent_category": cat,
                    "resource_type": ["无"],
                    "risk_level": "无风险",
                    "suggested_path": "进行友好对话",
                    "raw": f"意图分类：{cat}\n涉及资源类型：无\n风险预判：无风险\n建议操作路径：进行友好对话",
                }
        
        # 极短输入（1-4 个字符）默认按问候处理
        if len(user_input.strip()) <= 4:
            return {
                "intent_category": "问候",
                "resource_type": ["无"],
                "risk_level": "无风险",
                "suggested_path": "友好回应问候",
                "raw": "意图分类：问候\n涉及资源类型：无\n风险预判：无风险\n建议操作路径：友好回应问候",
            }
        
        # === 运维意图分类 ===
        if any(kw in query for kw in ["诊断", "为什么", "问题", "故障", "排查", "分析", "慢", "卡顿", "异常", "崩溃"]):
            category = "问题诊断"
        elif any(kw in query for kw in ["查看", "查询", "获取", "显示", "看看", "状态", "信息"]):
            category = "信息查询"
        elif any(kw in query for kw in ["清理", "删除", "重启", "停止", "启动", "修改", "kill", "终止", "rm "]):
            category = "配置管理"
        elif any(kw in query for kw in ["优化", "调优", "加速", "提升"]):
            category = "性能优化"
        else:
            category = "其他"
        
        # 资源类型
        resources = []
        if any(kw in query for kw in ["进程", "pid", "zombie", "kill"]):
            resources.append("进程")
        if any(kw in query for kw in ["内存", "memory", "mem", "ram", "free"]):
            resources.append("内存")
        if any(kw in query for kw in ["磁盘", "disk", "df", "空间", "io", "硬盘"]):
            resources.append("磁盘")
        if any(kw in query for kw in ["cpu", "负载", "处理器"]):
            resources.append("CPU")
        if any(kw in query for kw in ["网络", "端口", "连接", "网卡", "ping", "ss"]):
            resources.append("网络")
        if any(kw in query for kw in ["服务", "systemctl", "systemd"]):
            resources.append("服务")
        if any(kw in query for kw in ["日志", "log", "journal", "报错"]):
            resources.append("日志")
        if any(kw in query for kw in ["文件", "目录", "path"]):
            resources.append("文件")
        if not resources:
            resources.append("系统")
        
        # 风险预判
        if any(kw in query for kw in ["删除", "kill -9", "rm -rf", "格式化", "chmod 777", "覆盖", "> /dev"]):
            risk = "高风险"
        elif any(kw in query for kw in ["重启", "停止", "启动", "kill", "清理", "rm "]):
            risk = "中风险"
        else:
            risk = "无风险"
        
        raw = (
            f"意图分类：{category}\n"
            f"涉及资源类型：{', '.join(resources)}\n"
            f"风险预判：{risk}\n"
            f"建议操作路径：先查看{resources[0]}状态，收集必要信息后再决定后续操作"
        )
        
        return {
            "intent_category": category,
            "resource_type": resources,
            "risk_level": risk,
            "suggested_path": f"先查看{resources[0]}状态，收集必要信息后再决定后续操作",
            "raw": raw,
        }

    async def close(self):
        pass

    async def health_check(self) -> Dict:
        """Mock 健康检查"""
        return {
            "status": "healthy",
            "configured": False,
            "api_base": None,
            "model": "mock",
            "response_ms": 0,
            "fallback_active": True,
            "message": "使用 Mock LLM（无外部模型服务）"
        }


def _extract_value(line: str) -> str:
    """从 'key: value' 或 'key：[value]' 格式中提取 value"""
    for sep in ["：", ":", "→", "=>", "-"]:
        if sep in line:
            idx = line.find(sep)
            val = line[idx + len(sep):].strip()
            # 去除可能的括号包裹
            if val.startswith("[") and val.endswith("]"):
                val = val[1:-1]
            return val
    return line


def get_llm_client(use_mock: bool = False):
    """获取 LLM 客户端实例。

    use_mock=True 时返回 MockLLMClient（用于测试/离线环境），
    否则返回真实的 LLMClient。
    两者实现了相同的接口（鸭子类型），但无继承关系。
    """
    if use_mock:
        return MockLLMClient()
    return LLMClient()
