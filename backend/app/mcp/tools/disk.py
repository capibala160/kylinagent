import subprocess
import os
from typing import Any, Dict
from .base import BaseTool, register_tool
from ..schema import ToolCallResult, TextContent


class DiskUsageTool(BaseTool):
    name = "get_disk_usage"
    description = "获取磁盘分区使用情况（df -h）"
    parameters = {}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        try:
            result = subprocess.run(["df", "-h"], capture_output=True, text=True, timeout=5)
            df_output = result.stdout if result.returncode == 0 else ""
            
            result2 = subprocess.run(["lsblk"], capture_output=True, text=True, timeout=5)
            lsblk_output = result2.stdout if result2.returncode == 0 else ""
            
            text = f"""磁盘分区使用情况:
{df_output}

块设备信息:
{lsblk_output}
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取磁盘信息失败: {str(e)}")],
                isError=True
            )


class DirectorySizeTool(BaseTool):
    name = "get_directory_size"
    description = "获取指定目录的大小，以及其中最大的子目录/文件"
    parameters = {
        "path": {
            "type": "string",
            "description": "目录路径，默认 /var/log"
        },
        "depth": {
            "type": "integer",
            "description": "遍历深度，默认 1",
            "default": 1
        }
    }
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        path = arguments.get("path", "/var/log")
        depth = arguments.get("depth", 1)
        
        # 路径规范化与校验
        real_path = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
        
        if not os.path.exists(real_path):
            return ToolCallResult(
                content=[TextContent(type="text", text=f"路径不存在: {real_path}")],
                isError=True
            )
        
        # depth 范围限制
        if not isinstance(depth, int) or depth < 0 or depth > 5:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"错误: depth 必须在 0-5 之间")],
                isError=True
            )
        
        try:
            # 总大小（使用 -- 防止路径被解析为选项）
            total_result = subprocess.run(
                ["du", "-sh", "--", real_path],
                capture_output=True, text=True, timeout=30
            )
            total = total_result.stdout.split()[0] if total_result.returncode == 0 else "未知"
            
            # 子目录大小
            detail_result = subprocess.run(
                ["du", "-h", "--max-depth=" + str(depth), "--", real_path],
                capture_output=True, text=True, timeout=60
            )
            detail = detail_result.stdout if detail_result.returncode == 0 else ""
            
            # 排序取最大的10个
            lines = detail.strip().split("\n")
            lines.sort(key=lambda x: parse_size(x.split()[0]) if len(x.split()) > 0 else 0, reverse=True)
            top10 = "\n".join(lines[:10])
            
            text = f"""目录 {real_path} 大小分析:
总大小: {total}

最大的 {depth} 层子项 (Top 10):
{top10}
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取目录大小失败: {str(e)}")],
                isError=True
            )


class IOTool(BaseTool):
    name = "get_io_stats"
    description = "获取磁盘 I/O 统计信息"
    parameters = {}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        try:
            result = subprocess.run(["iostat", "-x", "1", "3"], capture_output=True, text=True, timeout=15)
            iostat = result.stdout if result.returncode == 0 else "iostat 未安装或执行失败"
            
            # vmstat 中的 io 信息
            result2 = subprocess.run(["vmstat", "-d"], capture_output=True, text=True, timeout=5)
            vmstat = result2.stdout if result2.returncode == 0 else ""
            
            text = f"""磁盘 I/O 统计 (iostat):
{iostat}

磁盘 I/O 详情 (vmstat -d):
{vmstat}
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取 I/O 统计失败: {str(e)}")],
                isError=True
            )


class LargeFilesTool(BaseTool):
    name = "find_large_files"
    description = "查找指定目录下超过阈值的大文件，用于清理磁盘空间"
    parameters = {
        "path": {
            "type": "string",
            "description": "搜索路径，默认 /var/log",
            "default": "/var/log"
        },
        "size_threshold": {
            "type": "string",
            "description": "大小阈值，如 100M, 1G，默认 100M",
            "default": "100M"
        },
        "limit": {
            "type": "integer",
            "description": "最大返回数量，默认 20",
            "default": 20
        }
    }
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        path = arguments.get("path", "/var/log")
        size = arguments.get("size_threshold", "100M")
        limit = arguments.get("limit", 20)
        
        # 路径规范化与校验
        real_path = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
        
        # size_threshold 格式校验（只允许数字+单位）
        import re
        if not re.match(r"^\d+[KMGTkmgt]?$", str(size)):
            return ToolCallResult(
                content=[TextContent(type="text", text=f"错误: size_threshold 格式非法: {size}")],
                isError=True
            )
        
        # limit 范围限制
        if not isinstance(limit, int) or limit < 1 or limit > 100:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"错误: limit 必须在 1-100 之间")],
                isError=True
            )
        
        try:
            # 使用 -- 防止路径被解析为选项
            result = subprocess.run(
                ["find", "--", real_path, "-type", "f", "-size", "+" + size, "-exec", "ls", "-lh", "{}", "+"],
                capture_output=True, text=True, timeout=60
            )
            
            if result.returncode != 0:
                return ToolCallResult(
                    content=[TextContent(type="text", text=f"查找失败: {result.stderr}")],
                    isError=True
                )
            
            lines = result.stdout.strip().split("\n")
            lines.sort(key=lambda x: parse_size(x.split()[4]) if len(x.split()) > 4 else 0, reverse=True)
            top = "\n".join(lines[:limit])
            
            text = f"""在 {real_path} 下找到的大文件 (>{size}, Top {limit}):
{top if top else '未找到符合条件的文件'}
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"查找大文件失败: {str(e)}")],
                isError=True
            )


def parse_size(size_str: str) -> float:
    """将人类可读的大小转换为字节数用于排序"""
    units = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    size_str = size_str.strip().upper()
    for unit, factor in units.items():
        if size_str.endswith(unit):
            try:
                return float(size_str[:-1]) * factor
            except:
                return 0
    try:
        return float(size_str)
    except:
        return 0


register_tool(DiskUsageTool())
register_tool(DirectorySizeTool())
register_tool(IOTool())
register_tool(LargeFilesTool())
