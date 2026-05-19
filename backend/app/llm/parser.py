"""
LLM 响应解析器
从模型返回的文本中提取工具调用（JSON 格式）
"""

import json
import re
from typing import Any, Dict, List


def parse_tool_calls(llm_response: str) -> List[Dict[str, Any]]:
    """
    从 LLM 响应中解析工具调用。
    
    支持多种格式：
    1. 纯 JSON 对象/数组
    2. Markdown 代码块内的 JSON
    3. 多行 JSON（每行一个工具调用）
    
    返回工具调用列表，每个元素包含 tool 和 arguments 字段。
    """
    if not llm_response:
        return []
    
    response = llm_response.strip()
    tool_calls = []
    
    # 尝试 1：提取 Markdown 代码块中的 JSON
    code_block_pattern = r'```(?:json)?\s*\n?(.*?)\n?```'
    code_blocks = re.findall(code_block_pattern, response, re.DOTALL)
    
    for block in code_blocks:
        block = block.strip()
        if not block:
            continue
        calls = _try_parse_json_block(block)
        tool_calls.extend(calls)
    
    if tool_calls:
        return tool_calls
    
    # 尝试 2：直接解析整个响应作为 JSON
    tool_calls = _try_parse_json_block(response)
    if tool_calls:
        return tool_calls
    
    # 尝试 3：查找响应中可能存在的 JSON 片段（非代码块）
    json_pattern = r'\{[^{}]*"tool"[^{}]*\}'
    matches = re.findall(json_pattern, response, re.DOTALL)
    for match in matches:
        calls = _try_parse_json_block(match)
        tool_calls.extend(calls)
    
    return tool_calls


def _try_parse_json_block(text: str) -> List[Dict[str, Any]]:
    """尝试将文本解析为工具调用列表"""
    text = text.strip()
    if not text:
        return []
    
    results = []
    
    # 尝试作为 JSON 数组解析
    try:
        data = json.loads(text)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    call = _normalize_tool_call(item)
                    if call:
                        results.append(call)
            return results
        elif isinstance(data, dict):
            call = _normalize_tool_call(data)
            if call:
                results.append(call)
            return results
    except json.JSONDecodeError:
        pass
    
    # 尝试按行解析多个 JSON 对象（JSONL 格式）
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                call = _normalize_tool_call(data)
                if call:
                    results.append(call)
        except json.JSONDecodeError:
            continue
    
    return results


def _normalize_tool_call(data: Dict[str, Any]) -> Dict[str, Any] | None:
    """
    标准化工具调用字典。
    支持字段映射：tool/action/tool_name -> tool, arguments/args/parameters -> arguments
    """
    if not isinstance(data, dict):
        return None
    
    # 查找工具名
    tool_name = None
    for key in ("tool", "action", "tool_name", "function", "name"):
        if key in data:
            tool_name = data[key]
            break
    
    if not tool_name:
        return None
    
    # 查找参数
    arguments = {}
    for key in ("arguments", "args", "parameters", "params"):
        if key in data:
            arguments = data[key] or {}
            break
    
    if not isinstance(arguments, dict):
        arguments = {}
    
    return {
        "tool": str(tool_name),
        "arguments": arguments,
        "thought": data.get("thought", ""),
        "risk_assessment": data.get("risk_assessment", "safe"),
        "explanation": data.get("explanation", ""),
    }
