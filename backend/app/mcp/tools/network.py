import subprocess
from typing import Any, Dict
from .base import BaseTool, register_tool
from ..schema import ToolCallResult, TextContent


class NetworkConnectionsTool(BaseTool):
    name = "get_network_connections"
    description = "获取网络连接信息，类似 netstat/ss 命令"
    parameters = {
        "protocol": {
            "type": "string",
            "description": "协议类型: tcp/udp/all，默认 all",
            "enum": ["tcp", "udp", "all"],
            "default": "all"
        },
        "state": {
            "type": "string",
            "description": "连接状态过滤，如 LISTEN, ESTABLISHED 等",
            "default": ""
        }
    }
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        protocol = arguments.get("protocol", "all")
        state = arguments.get("state", "")
        
        try:
            if protocol == "all":
                cmd = ["ss", "-tunapl"]
            elif protocol == "udp":
                cmd = ["ss", "-unapl"]
            else:
                cmd = ["ss", "-tnapl"]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                # ss 不可用，尝试 netstat
                result = subprocess.run(
                    ["netstat", "-tunapl"],
                    capture_output=True, text=True, timeout=10
                )
            
            output = result.stdout if result.returncode == 0 else result.stderr
            
            # 如果有状态过滤
            if state and result.returncode == 0:
                lines = output.split("\n")
                filtered = [lines[0]] + [l for l in lines[1:] if state.upper() in l.upper()]
                output = "\n".join(filtered[:200])
            
            if result.returncode != 0:
                output = f"网络连接信息获取失败（ss/netstat 均不可用）:\n{output}"
            
            return ToolCallResult(content=[TextContent(type="text", text=output)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取网络连接失败: {str(e)}")],
                isError=True
            )


class NetworkInterfaceTool(BaseTool):
    name = "get_network_interfaces"
    description = "获取网络接口配置和状态信息"
    parameters = {}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        try:
            result = subprocess.run(["ip", "addr"], capture_output=True, text=True, timeout=5)
            ip_addr = result.stdout if result.returncode == 0 else f"ip addr 失败: {result.stderr}"
            
            result2 = subprocess.run(["ip", "link"], capture_output=True, text=True, timeout=5)
            ip_link = result2.stdout if result2.returncode == 0 else f"ip link 失败: {result2.stderr}"
            
            text = f"""网络接口地址:
{ip_addr}

网络接口状态:
{ip_link}
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取网络接口信息失败: {str(e)}")],
                isError=True
            )


class PortUsageTool(BaseTool):
    name = "check_port_usage"
    description = "检查指定端口的使用情况，查看哪个进程在监听该端口"
    parameters = {
        "port": {
            "type": "integer",
            "description": "端口号"
        }
    }
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        port = arguments.get("port")
        if not port:
            return ToolCallResult(
                content=[TextContent(type="text", text="错误: 必须提供 port 参数")],
                isError=True
            )
        
        # 端口范围校验
        if not isinstance(port, int) or port < 1 or port > 65535:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"错误: 端口号必须在 1-65535 之间")],
                isError=True
            )
        
        try:
            # 先尝试 lsof
            lsof_output = ""
            result = subprocess.run(
                ["lsof", "-i", f":{port}", "-P", "-n"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                lsof_output = result.stdout
            else:
                lsof_output = f"lsof 不可用或端口 {port} 未被占用（提示: 可尝试安装 lsof）"
            
            # 使用 ss 作为备选
            result2 = subprocess.run(
                ["ss", "-tunapl"],
                capture_output=True, text=True, timeout=5
            )
            ss_lines = []
            if result2.returncode == 0:
                port_str = f":{port}"
                for line in result2.stdout.split("\n"):
                    if port_str in line:
                        ss_lines.append(line)
            ss_output = "\n".join(ss_lines[:20]) if ss_lines else "无匹配连接"
            
            text = f"""端口 {port} 使用情况:

--- lsof ---
{lsof_output}

--- ss ---
{ss_output}
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"检查端口失败: {str(e)}")],
                isError=True
            )


class PingTool(BaseTool):
    name = "ping_host"
    description = "Ping 指定主机，测试网络连通性"
    parameters = {
        "host": {
            "type": "string",
            "description": "目标主机 IP 或域名"
        },
        "count": {
            "type": "integer",
            "description": "发送的包数，默认 4",
            "default": 4
        }
    }
    
    # 禁止 ping 的私有/管理地址（防止内网扫描）
    FORBIDDEN_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        host = arguments.get("host")
        count = arguments.get("count", 4)
        
        if not host:
            return ToolCallResult(
                content=[TextContent(type="text", text="错误: 必须提供 host 参数")],
                isError=True
            )
        
        # 主机名安全校验：禁止命令注入字符
        forbidden_chars = set(";|&$`\n\r<>")
        if any(c in host for c in forbidden_chars):
            return ToolCallResult(
                content=[TextContent(type="text", text=f"安全限制: 主机名包含非法字符")],
                isError=True
            )
        
        if host in self.FORBIDDEN_HOSTS:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"安全限制: 禁止 ping 本地地址 {host}")],
                isError=True
            )
        
        # count 范围限制
        if not isinstance(count, int) or count < 1 or count > 20:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"错误: count 必须在 1-20 之间")],
                isError=True
            )
        
        try:
            result = subprocess.run(
                ["ping", "-c", str(count), "--", host],
                capture_output=True, text=True, timeout=30
            )
            output = result.stdout if result.returncode in [0, 1] else result.stderr
            return ToolCallResult(content=[TextContent(type="text", text=output)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"Ping 失败: {str(e)}")],
                isError=True
            )


class NetworkRouteTool(BaseTool):
    name = "get_network_routes"
    description = "获取系统路由表信息（ip route），包括默认网关、静态路由、策略路由"
    parameters = {}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        try:
            result = subprocess.run(["ip", "route"], capture_output=True, text=True, timeout=5)
            routes = result.stdout if result.returncode == 0 else "无法获取路由表"
            
            result2 = subprocess.run(["ip", "rule"], capture_output=True, text=True, timeout=5)
            rules = result2.stdout if result2.returncode == 0 else ""
            
            text = f"""路由表信息:
{routes}

策略路由规则:
{rules}
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取路由表失败: {str(e)}")],
                isError=True
            )


class FirewallStatusTool(BaseTool):
    name = "get_firewall_status"
    description = "获取防火墙状态（iptables/firewalld），包括活跃规则、开放端口、默认策略"
    parameters = {}
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolCallResult:
        try:
            # 尝试 firewalld
            firewalld = subprocess.run(
                ["firewall-cmd", "--state"],
                capture_output=True, text=True, timeout=5
            )
            is_firewalld = firewalld.returncode == 0
            
            if is_firewalld:
                zones = subprocess.run(
                    ["firewall-cmd", "--get-active-zones"],
                    capture_output=True, text=True, timeout=5
                )
                services = subprocess.run(
                    ["firewall-cmd", "--list-services"],
                    capture_output=True, text=True, timeout=5
                )
                ports = subprocess.run(
                    ["firewall-cmd", "--list-ports"],
                    capture_output=True, text=True, timeout=5
                )
                text = f"""防火墙状态 (firewalld):
运行状态: running
活跃区域:
{zones.stdout}
开放服务:
{services.stdout}
开放端口:
{ports.stdout}
"""
            else:
                # 回退到 iptables
                iptables = subprocess.run(
                    ["iptables", "-L", "-n", "--line-numbers"],
                    capture_output=True, text=True, timeout=10
                )
                text = f"""防火墙状态 (iptables):
{iptables.stdout if iptables.returncode == 0 else iptables.stderr}
"""
            return ToolCallResult(content=[TextContent(type="text", text=text)])
        except Exception as e:
            return ToolCallResult(
                content=[TextContent(type="text", text=f"获取防火墙状态失败: {str(e)}")],
                isError=True
            )


# 注册工具
register_tool(NetworkConnectionsTool())
register_tool(NetworkInterfaceTool())
register_tool(PortUsageTool())
register_tool(PingTool())
register_tool(NetworkRouteTool())
register_tool(FirewallStatusTool())
