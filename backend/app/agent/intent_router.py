import time
from typing import Any, Dict, List, Optional

from ..llm import get_llm_client, CHITCHAT_SYSTEM_PROMPT
from ..audit import ReasoningChain, ChainNodeType


class IntentClassification:
    """意图分类结果"""
    
    def __init__(self, category: str, confidence: float = 0.0,
                 resource_type: Optional[List[str]] = None,
                 risk_level: str = "无风险", suggested_path: str = "",
                 raw: str = "", is_chitchat: bool = False):
        self.category = category
        self.confidence = confidence
        self.resource_type = resource_type or []
        self.risk_level = risk_level
        self.suggested_path = suggested_path
        self.raw = raw
        self.is_chitchat = is_chitchat
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_category": self.category,
            "confidence": self.confidence,
            "resource_type": self.resource_type,
            "risk_level": self.risk_level,
            "suggested_path": self.suggested_path,
            "raw": self.raw,
            "is_chitchat": self.is_chitchat,
        }


class IntentClassifier:
    """
    意图分类器：分析用户输入，判断是运维操作还是通用对话
    """
    
    # 通用对话类意图关键词（用于快速规则匹配，作为 LLM 分析的补充/兜底）
    CHITCHAT_KEYWORDS = [
        "你好", "您好", "hello", "hi", "hey",
        "再见", "拜拜", "bye", "goodbye",
        "谢谢", "感谢", "thx", "thanks",
        "你是谁", "你是", "你叫什么", "介绍一下",
        "你能做什么", "你能干嘛", "你会什么", "功能",
        "帮助", "help", "怎么用", "使用说明",
        "天气", "吃饭", "累", "无聊", "笑话",
        "时间", "日期", "几点", "今天",
        "在吗", "在不在", "忙吗", "测试",
        "你能干嘛", "你会什么", "你是干什么的", "怎么用",
        "不客气", "多谢", "多谢你", "thank",
        "晚安", "早上好", "下午好", "晚上好",
        "讲个笑话", "说个笑话", "聊天", "陪我",
    ]
    
    # 闲聊类意图分类标签
    CHITCHAT_CATEGORIES = ["闲聊", "问候", "身份询问", "通用对话", "帮助", "感谢", "告别"]
    
    # 运维类意图分类标签
    OPS_CATEGORIES = ["信息查询", "问题诊断", "性能优化", "故障处理", "配置管理"]
    
    def __init__(self):
        self.llm = None  # 懒加载
    
    def _get_llm(self):
        if self.llm is None:
            self.llm = get_llm_client()
        return self.llm
    
    async def classify(self, user_input: str, 
                       intent_analysis: Optional[Dict[str, Any]] = None) -> IntentClassification:
        """
        对用户输入进行意图分类。
        
        策略：
        1. 优先使用 LLM 意图分析结果
        2. LLM 结果不明确时，使用规则兜底
        3. 返回标准化的 IntentClassification 对象
        """
        # 先用规则快速判断
        rule_result = self._rule_classify(user_input)
        
        # 如果有 LLM 分析结果，结合使用
        if intent_analysis and "error" not in intent_analysis:
            llm_category = intent_analysis.get("intent_category", "其他")
            
            # LLM 明确判断为闲聊相关
            if llm_category in self.CHITCHAT_CATEGORIES:
                return IntentClassification(
                    category=llm_category,
                    confidence=0.85,
                    resource_type=intent_analysis.get("resource_type", []),
                    risk_level=intent_analysis.get("risk_level", "无风险"),
                    suggested_path=intent_analysis.get("suggested_path", ""),
                    raw=intent_analysis.get("raw", ""),
                    is_chitchat=True
                )
            
            # LLM 明确判断为运维意图
            if llm_category in self.OPS_CATEGORIES:
                return IntentClassification(
                    category=llm_category,
                    confidence=0.8,
                    resource_type=intent_analysis.get("resource_type", []),
                    risk_level=intent_analysis.get("risk_level", "无风险"),
                    suggested_path=intent_analysis.get("suggested_path", ""),
                    raw=intent_analysis.get("raw", ""),
                    is_chitchat=False
                )
            
            # "其他" 类别：结合规则判断
            if rule_result.is_chitchat:
                return rule_result
            
            # 既不是闲聊也不是明确运维，默认按运维处理（让 LLM 自己决定是否需要工具）
            return IntentClassification(
                category="其他",
                confidence=0.5,
                resource_type=intent_analysis.get("resource_type", []),
                risk_level=intent_analysis.get("risk_level", "无风险"),
                suggested_path=intent_analysis.get("suggested_path", ""),
                raw=intent_analysis.get("raw", ""),
                is_chitchat=False
            )
        
        # 没有 LLM 结果，直接返回规则结果
        return rule_result
    
    def _rule_classify(self, user_input: str) -> IntentClassification:
        """基于规则的快速意图分类（无需 LLM）"""
        query_lower = user_input.lower().strip()
        
        # 匹配闲聊关键词
        for kw in self.CHITCHAT_KEYWORDS:
            if kw in query_lower:
                return IntentClassification(
                    category="通用对话",
                    confidence=0.75,
                    resource_type=[],
                    risk_level="无风险",
                    suggested_path="直接进行友好对话",
                    raw=f"规则匹配：命中闲聊关键词 '{kw}'",
                    is_chitchat=True
                )
        
        # 极短输入（1-4 个字符）且不含运维关键词，当作问候
        if len(user_input.strip()) <= 4:
            return IntentClassification(
                category="问候",
                confidence=0.6,
                resource_type=[],
                risk_level="无风险",
                suggested_path="友好回应问候",
                raw="规则匹配：短输入，默认按问候处理",
                is_chitchat=True
            )
        
        # 无法判断，默认按运维处理
        return IntentClassification(
            category="其他",
            confidence=0.3,
            resource_type=["系统"],
            risk_level="无风险",
            suggested_path="使用 LLM 进一步判断是否需要工具",
            raw="规则匹配：未命中任何已知模式",
            is_chitchat=False
        )


class ChitchatHandler:
    """
    通用对话处理器：处理闲聊、问候、知识问答等非运维对话
    """
    
    def __init__(self):
        self.llm = None
    
    def _get_llm(self):
        if self.llm is None:
            self.llm = get_llm_client()
        return self.llm
    
    async def handle(self, user_input: str, 
                     session_history: Optional[List[Dict[str, str]]] = None,
                     chain: Optional[ReasoningChain] = None) -> Dict[str, Any]:
        """
        处理通用对话请求。
        
        流程：
        1. 构建包含历史上下文的 messages
        2. 调用 LLM 生成友好回复
        3. 返回标准化结果
        """
        messages = self._build_messages(user_input, session_history)
        
        llm = self._get_llm()
        response = await llm.chat_completion(messages, max_tokens=512)
        
        if response.get("error"):
            # LLM 调用失败，返回兜底回复
            fallback = self._fallback_reply(user_input)
            if chain:
                chain.add_node(
                    ChainNodeType.ERROR,
                    "通用对话 LLM 调用失败，使用兜底回复",
                    output_data={"error": response.get("message")},
                    status="failed"
                )
            return {
                "success": True,
                "message": fallback,
                "requires_confirm": False,
                "intent_category": "通用对话",
            }
        
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            content = self._fallback_reply(user_input)
        
        if chain:
            chain.add_node(
                ChainNodeType.REASONING,
                "通用对话回复生成",
                input_data={"user_input": user_input},
                output_data={"response_preview": content[:200]},
                status="success"
            )
        
        return {
            "success": True,
            "message": content,
            "requires_confirm": False,
            "intent_category": "通用对话",
        }
    
    def _build_messages(self, user_input: str, 
                        session_history: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, str]]:
        """构建包含上下文的对话 messages"""
        import time
        system_prompt = CHITCHAT_SYSTEM_PROMPT.format(
            datetime=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        )
        messages = [
            {"role": "system", "content": system_prompt}
        ]
        
        # 加入最近的历史消息（最多 6 轮，避免超出上下文）
        if session_history:
            recent = session_history[-12:]  # 最多 12 条（6 轮对话）
            for msg in recent:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                # 只保留 user/assistant 角色
                if role in ("user", "assistant"):
                    messages.append({"role": role, "content": content})
        
        messages.append({"role": "user", "content": user_input})
        return messages
    
    def _fallback_reply(self, user_input: str) -> str:
        """兜底回复，当 LLM 不可用时使用"""
        query_lower = user_input.lower().strip()
        
        if any(kw in query_lower for kw in ["你好", "您好", "hello", "hi"]):
            return "你好！我是 Kylin Ops Agent，你的智能运维助手。有什么我可以帮你的吗？"
        elif any(kw in query_lower for kw in ["再见", "拜拜", "bye"]):
            return "再见！有需要随时回来找我，祝你工作顺利！"
        elif any(kw in query_lower for kw in ["谢谢", "感谢", "thx"]):
            return "不客气！😊 随时为你效劳。"
        elif any(kw in query_lower for kw in ["你是谁", "你是"]):
            return "我是 **Kylin Ops Agent**，一个专为麒麟操作系统设计的智能运维助手。我可以帮你查询系统状态、诊断问题、执行运维操作，也可以陪你聊天！"
        elif any(kw in query_lower for kw in ["你能做什么", "功能", "帮助", "help"]):
            return "我可以帮你：\n- 🔍 查询系统状态（CPU、内存、磁盘、网络等）\n- 🛠️ 执行运维操作（重启服务、清理日志、查看进程等）\n- 🔒 进行安全审计和权限管理\n- 📊 诊断系统问题并给出根因分析\n- 💬 陪你聊天解闷\n\n有什么想做的吗？"
        else:
            return "抱歉，我暂时没能理解你的意思。我是 Kylin Ops Agent，可以帮你进行系统运维操作，也可以陪你聊天。有什么我可以帮你的吗？"


class IntentRouter:
    """
    意图路由层：根据意图分类结果，将用户请求路由到对应的处理器
    """
    
    def __init__(self):
        self.classifier = IntentClassifier()
        self.chitchat_handler = ChitchatHandler()
    
    async def route(self, user_input: str,
                    intent_analysis: Optional[Dict[str, Any]] = None,
                    session_history: Optional[List[Dict[str, str]]] = None,
                    chain: Optional[ReasoningChain] = None) -> Dict[str, Any]:
        """
        意图路由主入口。
        
        返回:
            {
                "route": "chitchat" | "ops",
                "result": {...}  # chitchat 时直接包含回复
            }
        """
        # 1. 分类
        classification = await self.classifier.classify(user_input, intent_analysis)
        
        if chain:
            chain.add_node(
                ChainNodeType.REASONING,
                f"意图路由: {classification.category}",
                input_data={"user_input": user_input},
                output_data=classification.to_dict(),
                status="success"
            )
        
        # 2. 路由决策
        if classification.is_chitchat:
            result = await self.chitchat_handler.handle(
                user_input, session_history=session_history, chain=chain
            )
            return {
                "route": "chitchat",
                "classification": classification.to_dict(),
                "result": result
            }
        
        # 运维意图，继续后续流程
        return {
            "route": "ops",
            "classification": classification.to_dict(),
            "result": None
        }
