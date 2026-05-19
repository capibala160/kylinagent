import json
import time
from typing import Any, Dict, List, Optional, Tuple

from ..config import get_config
from ..llm import get_llm_client, SYSTEM_PROMPT
from ..mcp.schema import ToolCallRequest
from ..mcp.tools import get_registry
from ..security import SecurityGuard, PrivilegeExecutor
from ..audit import AuditLogger, ReasoningChain, ChainNodeType
from ..analysis import RootCauseAnalyzer


class OpsAgent:
    """
    运维 Agent 核心编排逻辑
    协调 LLM、MCP 工具、安全护栏和审计日志
    """
    
    def __init__(self, use_mock_llm: bool = False):
        self.config = get_config()
        self.llm = get_llm_client(use_mock=use_mock_llm)
        self.guard = SecurityGuard(self.config.security.model_dump())
        self.executor = PrivilegeExecutor(
            restricted_user=self.config.security.restricted_user,
            config=self.config.security.model_dump()
        )
        self.audit = AuditLogger(
            log_dir=self.config.audit.log_dir,
            retention_days=self.config.audit.retention_days
        )
        self.tool_registry = get_registry()
    
    # pending 确认超时时间（秒）
    PENDING_CONFIRM_TIMEOUT = 300
    
    async def process(self, user_input: str, session_id: str = "default",
                      confirmed: bool = False, session=None,
                      user: str = "") -> Dict[str, Any]:
        """
        处理用户输入的主流程
        
        闭环流程：接收指令 -> 感知环境 -> 推理决策 -> 安全校验 -> 执行结果
        新增：根因分析节点，支持智能诊断
        新增：confirm 闭环，支持用户二次确认后执行挂起操作
        """
        
        # ====== Confirm 闭环处理 ======
        # 如果用户已确认，且当前会话有待确认的操作，直接执行
        if confirmed and session and session.pending_confirmation:
            return await self._execute_pending_confirmation(user_input, session_id, session, user)
        
        # 正常流程
        chain = ReasoningChain(session_id=session_id, user_input=user_input, user=user)
        
        # 1. 接收指令
        chain.add_node(
            ChainNodeType.INTENT_RECEIVED,
            "接收到用户运维指令",
            input_data={"user_input": user_input}
        )
        
        try:
            # 2. 意图安全校验（提示词注入、恶意意图检测）
            intent_safe, intent_reason, intent_detail = self.guard.validate_intent(user_input)
            chain.add_node(
                ChainNodeType.SECURITY_CHECK,
                "意图安全校验",
                input_data={"user_input": user_input},
                output_data=intent_detail,
                status="success" if intent_safe else "blocked"
            )
            
            if not intent_safe:
                self.audit.log_security_alert(
                    "intent_blocked", user_input, intent_reason, chain.chain_id
                )
                chain.finalize("blocked", f"意图被拦截: {intent_reason}")
                self.audit.log_chain(chain)
                return {
                    "success": False,
                    "message": f"🛡️ 安全拦截: {intent_reason}",
                    "chain_id": chain.chain_id,
                    "requires_confirm": False,
                    "suggestion": "请检查您的输入是否包含可疑内容。"
                }
            
            # 3. LLM 推理决策（判断需要调用哪些工具）
            tools_desc = self._build_tools_description()
            
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"用户需求: {user_input}\n\n可用工具:\n{tools_desc}\n\n请分析用户需求，决定调用哪些工具来收集信息或执行操作。如果不需要工具，直接回答。如果需要工具，请以 JSON 格式返回调用计划，包含 tool 和 arguments 字段。"}
            ]
            
            llm_response = await self.llm.chat_completion(messages)
            
            if llm_response.get("error"):
                chain.add_node(
                    ChainNodeType.ERROR,
                    "LLM 调用失败",
                    output_data={"error": llm_response.get("message")},
                    status="failed"
                )
                chain.finalize("failed", "LLM 调用失败")
                self.audit.log_chain(chain)
                return {
                    "success": False,
                    "message": f"模型推理失败: {llm_response.get('message')}",
                    "chain_id": chain.chain_id
                }
            
            assistant_msg = llm_response["choices"][0]["message"]["content"]
            
            chain.add_node(
                ChainNodeType.REASONING,
                "LLM 推理决策",
                input_data={"user_input": user_input},
                output_data={"llm_response": assistant_msg[:500]}
            )
            
            # 4. 解析 LLM 响应，判断是否需要工具调用
            tool_calls = self._parse_tool_calls(assistant_msg)
            
            if not tool_calls:
                # 直接回答，不需要工具
                chain.finalize("completed", "直接回答，无需工具调用")
                self.audit.log_chain(chain)
                return {
                    "success": True,
                    "message": assistant_msg,
                    "chain_id": chain.chain_id,
                    "tool_calls": [],
                    "requires_confirm": False
                }
            
            # 5. 环境感知 & 执行工具
            tool_results = []
            pending_tool_calls = []  # 只收集被挂起的工具，用于 confirm 闭环
            all_safe = True
            needs_confirm = False
            confirm_reason = ""
            
            for tool_call in tool_calls:
                tool_name = tool_call.get("tool")
                arguments = tool_call.get("arguments", {})
                
                # 安全校验命令
                cmd_str = f"{tool_name}({json.dumps(arguments)})"
                cmd_safe, cmd_reason, cmd_detail = self.guard.validate_command(cmd_str, tool_name)
                
                risk_level = cmd_detail.get("risk_level", "safe")
                
                chain.add_node(
                    ChainNodeType.SECURITY_CHECK,
                    f"命令安全校验: {tool_name}",
                    input_data={"command": cmd_str},
                    output_data=cmd_detail,
                    status="success" if cmd_safe else "blocked"
                )
                
                if not cmd_safe and risk_level in ["critical", "high"]:
                    all_safe = False
                    self.audit.log_security_alert(
                        "command_blocked", cmd_str, cmd_reason, chain.chain_id
                    )
                    tool_results.append({
                        "tool": tool_name,
                        "arguments": arguments,
                        "blocked": True,
                        "reason": cmd_reason,
                        "risk_level": risk_level
                    })
                    continue
                
                if not cmd_safe or risk_level in ["medium", "low"]:
                    needs_confirm = True
                    confirm_reason = cmd_reason
                    # 中低风险操作先挂起，等待用户确认后再执行
                    tool_results.append({
                        "tool": tool_name,
                        "arguments": arguments,
                        "pending": True,
                        "reason": cmd_reason,
                        "risk_level": risk_level
                    })
                    pending_tool_calls.append(tool_call)  # 只保存被挂起的工具
                    continue
                
                # 执行工具（仅 safe 操作到达此处）
                tool = self.tool_registry.get(tool_name)
                if not tool:
                    tool_results.append({
                        "tool": tool_name,
                        "error": f"工具 {tool_name} 不存在"
                    })
                    continue
                
                exec_start = time.time()
                result = await tool.safe_execute(arguments)
                exec_duration = (time.time() - exec_start) * 1000
                
                chain.add_node(
                    ChainNodeType.TOOL_CALL,
                    f"调用工具: {tool_name}",
                    input_data={"tool": tool_name, "arguments": arguments},
                    output_data={
                        "isError": result.isError,
                        "has_content": len(result.content) > 0,
                        "execution_time_ms": result.execution_time_ms
                    },
                    status="success" if not result.isError else "failed",
                    metadata={"execution_time_ms": exec_duration}
                )
                
                self.audit.log_tool_execution(
                    tool_name, arguments,
                    {"isError": result.isError, "content": str(result.content)[:200]},
                    chain.chain_id
                )
                
                tool_results.append({
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": {
                        "content": [c.text if hasattr(c, "text") else str(c) for c in result.content],
                        "isError": result.isError,
                        "errorMessage": result.errorMessage
                    }
                })
            
            # 6. 如果需要确认，返回确认请求
            if needs_confirm and not all_safe:
                chain.finalize("blocked", f"存在被阻断的高危操作")
                self.audit.log_chain(chain)
                return {
                    "success": False,
                    "message": f"🛡️ 操作被安全护栏拦截\n\n原因: {confirm_reason}",
                    "chain_id": chain.chain_id,
                    "tool_results": tool_results,
                    "requires_confirm": False,
                    "blocked": True
                }
            
            if needs_confirm:
                # 保存待确认的操作计划到会话，用于 confirm 闭环
                # 只保存被挂起的工具 + 之前已执行的结果，避免重复执行 safe 工具
                if session:
                    session.pending_confirmation = {
                        "user_input": user_input,
                        "tool_calls": pending_tool_calls,
                        "previous_tool_results": [
                            r for r in tool_results 
                            if not r.get("pending")
                        ],
                        "confirm_reason": confirm_reason,
                        "created_at": time.time(),
                        "chain_id": chain.chain_id,
                    }
                
                chain.finalize("pending", "等待用户确认")
                self.audit.log_chain(chain)
                return {
                    "success": True,
                    "message": f"⚠️ 检测到潜在风险操作\n\n{confirm_reason}\n\n请确认是否继续执行？",
                    "chain_id": chain.chain_id,
                    "tool_results": tool_results,
                    "requires_confirm": True,
                    "confirm_reason": confirm_reason
                }
            
            # 7. 根因分析增强（如果调用了 diagnose 工具，直接展示分析结果）
            has_diagnose = any(
                r.get("tool", "").startswith("diagnose_") or r.get("tool", "") == "comprehensive_diagnosis"
                for r in tool_results
            )
            
            # 8. 汇总结果，生成最终回复
            if tool_results and not any(r.get("blocked") for r in tool_results):
                if has_diagnose:
                    # diagnose 工具已返回格式化 Markdown，直接拼接展示
                    summary = self._summarize_diagnose_results(tool_results)
                else:
                    summary = self._summarize_results(user_input, tool_results)
            else:
                summary = "操作已执行完成。"
            
            chain.add_node(
                ChainNodeType.RESULT,
                "执行完成",
                output_data={"summary": summary, "tool_count": len(tool_results)}
            )
            chain.finalize("completed", summary)
            self.audit.log_chain(chain)
            
            return {
                "success": True,
                "message": summary,
                "chain_id": chain.chain_id,
                "tool_results": tool_results,
                "requires_confirm": False
            }
            
        except Exception as e:
            chain.add_node(
                ChainNodeType.ERROR,
                "Agent 执行异常",
                output_data={"error": str(e)},
                status="failed"
            )
            chain.finalize("failed", f"执行异常: {str(e)}")
            self.audit.log_chain(chain)
            return {
                "success": False,
                "message": f"Agent 执行出错: {str(e)}",
                "chain_id": chain.chain_id
            }
    
    def _build_tools_description(self) -> str:
        """构建工具描述文本"""
        tools = self.tool_registry.list_tools()
        lines = []
        for tool in tools:
            lines.append(f"- {tool.name}: {tool.description}")
        return "\n".join(lines)
    
    def _parse_tool_calls(self, llm_response: str) -> List[Dict]:
        """从 LLM 响应中解析工具调用（健壮版）"""
        from ..llm.parser import parse_tool_calls
        return parse_tool_calls(llm_response)
    
    def _summarize_results(self, user_input: str, tool_results: List[Dict]) -> str:
        """汇总工具执行结果生成回复"""
        lines = ["## 执行结果", ""]
        
        for result in tool_results:
            if result.get("blocked"):
                lines.append(f"- ❌ **{result['tool']}**: 被安全护栏拦截 ({result.get('reason', '')})")
            elif "error" in result:
                lines.append(f"- ⚠️ **{result['tool']}**: 调用失败 ({result['error']})")
            else:
                tool_result = result.get("result", {})
                if tool_result.get("isError"):
                    lines.append(f"- ⚠️ **{result['tool']}**: 执行出错 ({tool_result.get('errorMessage', '')})")
                else:
                    content = tool_result.get("content", [])
                    preview = "\n".join(content)[:800] if content else "无输出"
                    lines.append(f"- ✅ **{result['tool']}**:\n```\n{preview}\n```")
        
        return "\n".join(lines)
    
    def _summarize_diagnose_results(self, tool_results: List[Dict]) -> str:
        """
        汇总 diagnose 类工具的根因分析结果
        diagnose 工具已返回 Markdown 格式化内容，直接拼接并添加总结
        """
        sections = []
        
        for result in tool_results:
            if result.get("blocked") or "error" in result:
                continue
            tool_result = result.get("result", {})
            if tool_result.get("isError"):
                continue
            content = tool_result.get("content", [])
            text = "\n".join(content) if content else ""
            if text:
                sections.append(text)
        
        if not sections:
            return "诊断工具执行完成，但未获取到有效分析结果。"
        
        # diagnose 工具已经输出 Markdown，直接拼接
        return "\n\n---\n\n".join(sections)
    
    async def _execute_pending_confirmation(self, user_input: str, session_id: str,
                                               session, user: str = "") -> Dict[str, Any]:
        """
        执行用户确认后的挂起操作（confirm 闭环核心）
        """
        pending = session.pending_confirmation
        
        # 检查是否过期
        if time.time() - pending["created_at"] > self.PENDING_CONFIRM_TIMEOUT:
            session.pending_confirmation = None
            return {
                "success": False,
                "message": "⏱️ 确认请求已过期（超过5分钟），请重新发起操作。",
                "chain_id": "",
                "requires_confirm": False,
            }
        
        # 创建新的审计链路记录确认执行
        chain = ReasoningChain(session_id=session_id, user_input=user_input, user=user)
        chain.add_node(
            ChainNodeType.INTENT_RECEIVED,
            "用户确认执行挂起的操作",
            input_data={"user_input": user_input, "pending_chain_id": pending["chain_id"]}
        )
        chain.add_node(
            ChainNodeType.REASONING,
            "跳过 LLM 推理，使用已缓存的执行计划",
            output_data={"cached_tool_calls": [tc.get("tool") for tc in pending["tool_calls"]]}
        )
        
        tool_results = []
        any_blocked = False
        
        for tool_call in pending["tool_calls"]:
            tool_name = tool_call.get("tool")
            arguments = tool_call.get("arguments", {})
            
            # 再次安全校验（用户已确认，允许 medium/low 通过，但 critical/high 仍然阻断）
            cmd_str = f"{tool_name}({json.dumps(arguments)})"
            cmd_safe, cmd_reason, cmd_detail = self.guard.validate_command(cmd_str, tool_name)
            risk_level = cmd_detail.get("risk_level", "safe")
            
            chain.add_node(
                ChainNodeType.SECURITY_CHECK,
                f"二次安全校验（已确认）: {tool_name}",
                input_data={"command": cmd_str, "confirmed": True},
                output_data=cmd_detail,
                status="success" if (cmd_safe or risk_level in ["medium", "low"]) else "blocked"
            )
            
            # critical/high 即使已确认也阻断（不可逾越的红线）
            if not cmd_safe and risk_level in ["critical", "high"]:
                any_blocked = True
                self.audit.log_security_alert(
                    "command_blocked_after_confirm", cmd_str, cmd_reason, chain.chain_id
                )
                tool_results.append({
                    "tool": tool_name,
                    "arguments": arguments,
                    "blocked": True,
                    "reason": f"二次校验阻断: {cmd_reason}",
                    "risk_level": risk_level
                })
                continue
            
            # 执行工具
            tool = self.tool_registry.get(tool_name)
            if not tool:
                tool_results.append({
                    "tool": tool_name,
                    "error": f"工具 {tool_name} 不存在"
                })
                continue
            
            exec_start = time.time()
            result = await tool.safe_execute(arguments)
            exec_duration = (time.time() - exec_start) * 1000
            
            chain.add_node(
                ChainNodeType.TOOL_CALL,
                f"确认执行工具: {tool_name}",
                input_data={"tool": tool_name, "arguments": arguments, "confirmed": True},
                output_data={
                    "isError": result.isError,
                    "has_content": len(result.content) > 0,
                    "execution_time_ms": result.execution_time_ms
                },
                status="success" if not result.isError else "failed",
                metadata={"execution_time_ms": exec_duration}
            )
            
            self.audit.log_tool_execution(
                tool_name, arguments,
                {"isError": result.isError, "content": str(result.content)[:200]},
                chain.chain_id
            )
            
            tool_results.append({
                "tool": tool_name,
                "arguments": arguments,
                "result": {
                    "content": [c.text if hasattr(c, "text") else str(c) for c in result.content],
                    "isError": result.isError,
                    "errorMessage": result.errorMessage
                }
            })
        
        # 清理 pending
        session.pending_confirmation = None
        
        # 合并之前已执行的结果和本次确认执行的结果
        previous_results = pending.get("previous_tool_results", [])
        all_results = previous_results + tool_results
        
        # 汇总结果
        has_diagnose = any(
            r.get("tool", "").startswith("diagnose_") or r.get("tool", "") == "comprehensive_diagnosis"
            for r in all_results
        )
        
        if all_results and not any(r.get("blocked") for r in all_results):
            if has_diagnose:
                summary = self._summarize_diagnose_results(all_results)
            else:
                summary = self._summarize_results(user_input, all_results)
        else:
            summary = "操作已执行完成。"
        
        # 如果有被阻断的操作，在 summary 前添加警告
        if any_blocked:
            summary = f"⚠️ 部分操作在执行前被安全护栏拦截\n\n{summary}"
        
        # 添加确认执行标识
        summary = f"✅ 用户已确认执行\n\n{summary}"
        
        chain.add_node(
            ChainNodeType.RESULT,
            "确认执行完成",
            output_data={"summary": summary, "tool_count": len(all_results), "confirmed": True}
        )
        chain.finalize("completed", f"用户确认后执行: {summary}")
        self.audit.log_chain(chain)
        
        return {
            "success": True,
            "message": summary,
            "chain_id": chain.chain_id,
            "tool_results": all_results,
            "requires_confirm": False
        }
    
    async def close(self):
        await self.llm.close()
