"""系统监控数据采集器

优先使用 psutil 高效采集系统数据，subprocess /proc 作为 fallback。
Windows 环境下自动返回 Mock 数据用于前端开发调试。
"""

import os
import re
import time
import platform
import subprocess
from typing import Dict, List, Any, Optional
from collections import deque

# 优先使用 psutil，未安装时降级为原生方式
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    psutil = None
    HAS_PSUTIL = False


class SystemMonitor:
    """系统监控数据采集器"""

    # 历史数据缓存（最多保留 60 个点，约 5 分钟 @ 5s 间隔）
    MAX_HISTORY = 60

    def __init__(self):
        self.is_linux = platform.system() == "Linux"
        self._history: deque = deque(maxlen=self.MAX_HISTORY)
        self._last_collect_time = 0
        # 网络流量速率计算（保存上一次数据）
        self._last_net_io: Dict[str, Dict] = {}
        self._last_net_time: float = 0
        # CPU 统计差值计算（无 psutil 时使用）
        self._last_cpu_stat: Optional[Tuple[int, int, float]] = None

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    async def get_overview(self) -> Dict[str, Any]:
        """获取系统概览数据（结构化 JSON）"""
        if self.is_linux:
            data = await self._collect_linux()
        else:
            data = self._mock_data()
        self._history.append(data)
        return data

    def get_history(self, points: int = 60) -> List[Dict[str, Any]]:
        """获取历史监控数据"""
        return list(self._history)[-points:]

    # ------------------------------------------------------------------
    # Linux 数据采集
    # ------------------------------------------------------------------

    async def _collect_linux(self) -> Dict[str, Any]:
        return {
            "cpu": self._read_cpu(),
            "memory": self._read_memory(),
            "disk": self._read_disk(),
            "network": self._read_network(),
            "processes": self._read_processes(),
            "services": self._read_services(),
            "system": self._read_system(),
            "timestamp": time.time(),
        }

    def _read_cpu(self) -> Dict[str, Any]:
        """读取 CPU 信息（优先 psutil）"""
        try:
            if HAS_PSUTIL:
                usage = psutil.cpu_percent(interval=0.1)
                cores = psutil.cpu_count(logical=True) or 1
                load_avg = list(psutil.getloadavg()) if hasattr(psutil, 'getloadavg') else [0.0, 0.0, 0.0]
                # 尝试获取 CPU 型号信息
                model = "Unknown"
                try:
                    info = psutil.cpu_freq()
                    if info:
                        model = f"{cores} cores @ {info.current:.0f}MHz"
                except Exception:
                    pass
                return {
                    "usage_percent": round(usage, 1),
                    "cores": cores,
                    "load_avg": load_avg,
                    "model": model,
                }

            # Fallback: /proc/stat（需两次采样计算差值）
            with open("/proc/stat", "r") as f:
                line = f.readline()
            parts = line.strip().split()
            if parts[0] == "cpu" and len(parts) >= 8:
                user, nice, system, idle, iowait, irq, softirq = map(int, parts[1:8])
                total = user + nice + system + idle + iowait + irq + softirq
                now = time.time()
                if self._last_cpu_stat is not None:
                    last_total, last_idle, last_time = self._last_cpu_stat
                    delta_total = total - last_total
                    delta_idle = idle - last_idle
                    if delta_total > 0:
                        usage = 100.0 * (delta_total - delta_idle) / delta_total
                    else:
                        usage = 0.0
                else:
                    usage = 0.0  # 首次采集，无历史数据
                self._last_cpu_stat = (total, idle, now)
            else:
                usage = 0.0

            with open("/proc/loadavg", "r") as f:
                load = f.read().strip().split()
            load_avg = [float(load[0]), float(load[1]), float(load[2])]

            model = "Unknown"
            cores = os.cpu_count() or 1
            try:
                result = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=2)
                for line in result.stdout.split("\n"):
                    if line.startswith("Model name:"):
                        model = line.split(":", 1)[1].strip()
                    elif line.startswith("CPU(s):"):
                        cores = int(line.split(":", 1)[1].strip())
            except Exception:
                pass

            return {
                "usage_percent": round(usage, 1),
                "cores": cores,
                "load_avg": load_avg,
                "model": model,
            }
        except Exception as e:
            return {"usage_percent": 0, "cores": 1, "load_avg": [0, 0, 0], "model": str(e)}

    def _read_memory(self) -> Dict[str, Any]:
        """读取内存信息（优先 psutil）"""
        try:
            if HAS_PSUTIL:
                mem = psutil.virtual_memory()
                total_gb = round(mem.total / (1024 ** 3), 2)
                used_gb = round(mem.used / (1024 ** 3), 2)
                free_gb = round(mem.available / (1024 ** 3), 2)
                swap = psutil.swap_memory()
                return {
                    "total_gb": total_gb,
                    "used_gb": used_gb,
                    "free_gb": free_gb,
                    "usage_percent": round(mem.percent, 1),
                    "buffers_mb": round(getattr(mem, 'buffers', 0) / (1024 ** 2), 1),
                    "cached_mb": round(getattr(mem, 'cached', 0) / (1024 ** 2), 1),
                    "swap_total_gb": round(swap.total / (1024 ** 3), 2),
                    "swap_used_gb": round(swap.used / (1024 ** 3), 2),
                    "swap_usage_percent": round(swap.percent, 1),
                }

            # Fallback: /proc/meminfo
            meminfo = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if ":" in line:
                        key, val = line.split(":", 1)
                        meminfo[key.strip()] = int(val.strip().split()[0]) * 1024

            total = meminfo.get("MemTotal", 0)
            free = meminfo.get("MemFree", 0)
            buffers = meminfo.get("Buffers", 0)
            cached = meminfo.get("Cached", 0)
            available = meminfo.get("MemAvailable", free + buffers + cached)
            used = total - available
            swap_total = meminfo.get("SwapTotal", 0)
            swap_free = meminfo.get("SwapFree", 0)
            swap_used = swap_total - swap_free

            return {
                "total_gb": round(total / (1024 ** 3), 2),
                "used_gb": round(used / (1024 ** 3), 2),
                "free_gb": round(available / (1024 ** 3), 2),
                "usage_percent": round(100.0 * used / total, 1) if total > 0 else 0,
                "buffers_mb": round(buffers / (1024 ** 2), 1),
                "cached_mb": round(cached / (1024 ** 2), 1),
                "swap_total_gb": round(swap_total / (1024 ** 3), 2),
                "swap_used_gb": round(swap_used / (1024 ** 3), 2),
                "swap_usage_percent": round(100.0 * swap_used / swap_total, 1) if swap_total > 0 else 0,
            }
        except Exception as e:
            return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "usage_percent": 0, "swap_total_gb": 0, "swap_used_gb": 0, "swap_usage_percent": 0, "error": str(e)}

    def _read_disk(self) -> Dict[str, Any]:
        """读取磁盘信息（优先 psutil）"""
        try:
            if HAS_PSUTIL:
                partitions = []
                for part in psutil.disk_partitions(all=False):
                    try:
                        usage = psutil.disk_usage(part.mountpoint)
                        partitions.append({
                            "device": part.device,
                            "mount": part.mountpoint,
                            "size": self._human_bytes(usage.total),
                            "used": self._human_bytes(usage.used),
                            "available": self._human_bytes(usage.free),
                            "usage_percent": int(usage.percent),
                        })
                    except PermissionError:
                        continue
                partitions.sort(key=lambda x: x["usage_percent"], reverse=True)
                return {
                    "partitions": partitions[:6],
                    "total_partitions": len(partitions),
                }

            # Fallback: df
            result = subprocess.run(["df", "-h"], capture_output=True, text=True, timeout=3)
            partitions = []
            for line in result.stdout.strip().split("\n")[1:]:
                parts = line.split()
                if len(parts) >= 6 and parts[0].startswith("/dev/"):
                    size_str, used_str, avail_str, use_percent = parts[1:5]
                    mount = parts[5]
                    partitions.append({
                        "device": parts[0],
                        "mount": mount,
                        "size": size_str,
                        "used": used_str,
                        "available": avail_str,
                        "usage_percent": int(use_percent.replace("%", "")) if "%" in use_percent else 0,
                    })
            partitions.sort(key=lambda x: x["usage_percent"], reverse=True)
            return {
                "partitions": partitions[:6],
                "total_partitions": len(partitions),
            }
        except Exception as e:
            return {"partitions": [], "total_partitions": 0, "error": str(e)}

    @staticmethod
    def _human_bytes(size_bytes: int) -> str:
        """字节转人类可读字符串"""
        if size_bytes == 0:
            return "0B"
        for unit in ["B", "K", "M", "G", "T"]:
            if abs(size_bytes) < 1024.0:
                return f"{size_bytes:.1f}{unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f}P"

    def _read_network(self) -> Dict[str, Any]:
        """读取网络信息（优先 psutil），并计算 RX/TX 速率"""
        try:
            now = time.time()
            elapsed = now - self._last_net_time if self._last_net_time else 1.0
            if elapsed < 0.1:
                elapsed = 1.0
            
            if HAS_PSUTIL:
                interfaces = []
                net_io = psutil.net_io_counters(pernic=True)
                addrs = psutil.net_if_addrs()
                stats = psutil.net_if_stats()
                for name, addr_list in addrs.items():
                    ip = None
                    for addr in addr_list:
                        if addr.family == 2:  # AF_INET
                            ip = addr.address
                            break
                    io = net_io.get(name)
                    st = stats.get(name)
                    rx = io.bytes_recv if io else 0
                    tx = io.bytes_sent if io else 0
                    # 计算速率
                    prev = self._last_net_io.get(name, {})
                    rx_rate = round((rx - prev.get("rx", 0)) / elapsed / 1024, 2) if prev else 0
                    tx_rate = round((tx - prev.get("tx", 0)) / elapsed / 1024, 2) if prev else 0
                    interfaces.append({
                        "name": name,
                        "status": "up" if st and st.isup else "down",
                        "ip": ip or "N/A",
                        "rx_bytes": rx,
                        "tx_bytes": tx,
                        "rx_rate_kb": max(0, rx_rate),
                        "tx_rate_kb": max(0, tx_rate),
                    })
                    self._last_net_io[name] = {"rx": rx, "tx": tx}
                self._last_net_time = now
                return {"interfaces": interfaces}

            # Fallback: ip addr + /proc/net/dev
            interfaces = []
            result = subprocess.run(["ip", "addr"], capture_output=True, text=True, timeout=2)
            current = {}
            for line in result.stdout.split("\n"):
                if line.startswith(" ") and "inet " in line and "scope global" in line:
                    ip = line.strip().split()[1].split("/")[0]
                    current["ip"] = ip
                elif not line.startswith(" ") and ":" in line:
                    if current.get("name"):
                        interfaces.append(current)
                    name = line.split(":", 1)[1].strip().split("@")[0].split(":")[0].strip()
                    flags = line
                    current = {"name": name, "status": "up" if "UP" in flags else "down"}
            if current.get("name"):
                interfaces.append(current)

            traffic = {}
            try:
                with open("/proc/net/dev", "r") as f:
                    for line in f:
                        if ":" in line and not line.strip().startswith("Inter") and not line.strip().startswith("face"):
                            parts = line.strip().split(":")
                            iface = parts[0].strip()
                            nums = list(map(int, parts[1].strip().split()))
                            if len(nums) >= 9:
                                traffic[iface] = {"rx_bytes": nums[0], "tx_bytes": nums[8]}
            except Exception:
                pass

            for iface in interfaces:
                rx = traffic.get(iface["name"], {}).get("rx_bytes", 0)
                tx = traffic.get(iface["name"], {}).get("tx_bytes", 0)
                prev = self._last_net_io.get(iface["name"], {})
                rx_rate = round((rx - prev.get("rx", 0)) / elapsed / 1024, 2) if prev else 0
                tx_rate = round((tx - prev.get("tx", 0)) / elapsed / 1024, 2) if prev else 0
                iface["rx_bytes"] = rx
                iface["tx_bytes"] = tx
                iface["rx_rate_kb"] = max(0, rx_rate)
                iface["tx_rate_kb"] = max(0, tx_rate)
                self._last_net_io[iface["name"]] = {"rx": rx, "tx": tx}
            self._last_net_time = now
            return {"interfaces": interfaces}
        except Exception as e:
            return {"interfaces": [], "error": str(e)}

    def _read_processes(self) -> Dict[str, Any]:
        """读取进程信息（优先 psutil）"""
        try:
            if HAS_PSUTIL:
                total = len(psutil.pids())
                zombie_count = 0
                top_cpu = []
                for proc in sorted(
                    psutil.process_iter(["pid", "username", "cpu_percent", "memory_percent", "name", "cmdline"]),
                    key=lambda p: p.info.get("cpu_percent", 0) or 0,
                    reverse=True
                )[:5]:
                    info = proc.info
                    cmdline = info.get("cmdline")
                    cmd = " ".join(cmdline) if cmdline else info.get("name", "")
                    top_cpu.append({
                        "pid": info["pid"],
                        "user": info.get("username") or "unknown",
                        "cpu": round(info.get("cpu_percent") or 0, 1),
                        "mem": round(info.get("memory_percent") or 0, 1),
                        "command": cmd[:60],
                    })
                # 统计僵尸进程
                for proc in psutil.process_iter(["status"]):
                    if proc.info.get("status") == psutil.STATUS_ZOMBIE:
                        zombie_count += 1
                return {"total": total, "zombie": zombie_count, "top_cpu": top_cpu}

            # Fallback: ps
            result = subprocess.run(["ps", "aux", "--sort=-%cpu"], capture_output=True, text=True, timeout=3)
            lines = result.stdout.strip().split("\n")[1:]
            total = len(lines)
            zombie_result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=3)
            zombie_count = sum(1 for line in zombie_result.stdout.split("\n") if "Z" in line.split()[7:8])
            top_cpu = []
            for line in lines[:5]:
                parts = line.split(None, 10)
                if len(parts) >= 11:
                    top_cpu.append({
                        "pid": int(parts[1]),
                        "user": parts[0],
                        "cpu": float(parts[2]),
                        "mem": float(parts[3]),
                        "command": parts[10][:60],
                    })
            return {"total": total, "zombie": zombie_count, "top_cpu": top_cpu}
        except Exception as e:
            return {"total": 0, "zombie": 0, "top_cpu": [], "error": str(e)}

    def _read_services(self) -> Dict[str, Any]:
        """读取服务状态"""
        try:
            result = subprocess.run(
                ["systemctl", "list-units", "--type=service", "--state=running,failed", "--no-pager", "--no-legend"],
                capture_output=True, text=True, timeout=3
            )
            running = 0
            failed = 0
            failed_list = []
            for line in result.stdout.strip().split("\n"):
                parts = line.split(None, 4)
                if len(parts) >= 4:
                    if parts[3] == "running":
                        running += 1
                    elif parts[3] == "failed":
                        failed += 1
                        failed_list.append(parts[0])

            # 总服务数
            all_result = subprocess.run(
                ["systemctl", "list-unit-files", "--type=service", "--no-pager", "--no-legend"],
                capture_output=True, text=True, timeout=3
            )
            total = len([l for l in all_result.stdout.strip().split("\n") if l.strip()])

            return {
                "running": running,
                "failed": failed,
                "total": total,
                "failed_list": failed_list[:5],
            }
        except Exception as e:
            return {"running": 0, "failed": 0, "total": 0, "failed_list": [], "error": str(e)}

    def _read_system(self) -> Dict[str, Any]:
        """读取系统基本信息"""
        try:
            uname = os.uname()
            return {
                "hostname": uname.nodename,
                "kernel": uname.release,
                "arch": uname.machine,
                "uptime_seconds": self._read_uptime(),
            }
        except Exception:
            return {"hostname": "unknown", "kernel": "", "arch": "", "uptime_seconds": 0}

    def _read_uptime(self) -> float:
        try:
            with open("/proc/uptime", "r") as f:
                return float(f.read().split()[0])
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # Mock 数据（Windows 开发环境）
    # ------------------------------------------------------------------

    def _mock_data(self) -> Dict[str, Any]:
        import random
        t = time.time()
        # 基于时间生成一些波动的 mock 数据
        cpu_wave = 30 + 20 * ((t % 60) / 60)
        mem_wave = 50 + 10 * ((t % 120) / 120)
        return {
            "cpu": {
                "usage_percent": round(cpu_wave + random.uniform(-5, 5), 1),
                "cores": 8,
                "load_avg": [round(cpu_wave / 20 + random.uniform(-0.2, 0.2), 2) for _ in range(3)],
                "model": "Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz (Mock)",
            },
            "memory": {
                "total_gb": 32.0,
                "used_gb": round(32.0 * (mem_wave + random.uniform(-3, 3)) / 100, 2),
                "free_gb": round(32.0 * (100 - mem_wave) / 100, 2),
                "usage_percent": round(mem_wave + random.uniform(-3, 3), 1),
                "buffers_mb": round(random.uniform(200, 500), 1),
                "cached_mb": round(random.uniform(1000, 3000), 1),
                "swap_total_gb": 8.0,
                "swap_used_gb": round(random.uniform(0, 2), 2),
                "swap_usage_percent": round(random.uniform(0, 25), 1),
            },
            "disk": {
                "partitions": [
                    {"device": "/dev/sda1", "mount": "/", "size": "100G", "used": "60G", "available": "40G", "usage_percent": 60},
                    {"device": "/dev/sda2", "mount": "/home", "size": "500G", "used": "120G", "available": "380G", "usage_percent": 24},
                    {"device": "/dev/sdb1", "mount": "/data", "size": "1T", "used": "800G", "available": "200G", "usage_percent": 80},
                ],
                "total_partitions": 3,
            },
            "network": {
                "interfaces": [
                    {"name": "eth0", "status": "up", "ip": "192.168.1.100", "rx_bytes": 1234567890, "tx_bytes": 987654321},
                    {"name": "lo", "status": "up", "ip": "127.0.0.1", "rx_bytes": 4567890, "tx_bytes": 4567890},
                ],
            },
            "processes": {
                "total": 245 + int(random.uniform(-10, 10)),
                "zombie": int(random.uniform(0, 3)),
                "top_cpu": [
                    {"pid": 1234, "user": "nginx", "cpu": 12.5, "mem": 2.3, "command": "/usr/sbin/nginx -g daemon off;"},
                    {"pid": 5678, "user": "postgres", "cpu": 8.2, "mem": 5.1, "command": "postgres: writer process"},
                    {"pid": 9012, "user": "root", "cpu": 5.1, "mem": 1.2, "command": "python3 /opt/agent/server.py"},
                    {"pid": 3456, "user": "root", "cpu": 3.4, "mem": 0.8, "command": "sshd: user@pts/0"},
                    {"pid": 7890, "user": "redis", "cpu": 1.8, "mem": 3.5, "command": "/usr/bin/redis-server 127.0.0.1:6379"},
                ],
            },
            "services": {
                "running": 15 + int(random.uniform(-2, 2)),
                "failed": int(random.uniform(0, 2)),
                "total": 42,
                "failed_list": [] if random.random() > 0.3 else ["test-failed-service.service"],
            },
            "system": {
                "hostname": "kylin-ops-agent",
                "kernel": "5.4.18-53-generic",
                "arch": "loongarch64",
                "uptime_seconds": 86400 + int(random.uniform(0, 3600)),
            },
            "timestamp": t,
            "mock": True,
        }


# 全局单例
_monitor_instance: Optional[SystemMonitor] = None


def get_monitor() -> SystemMonitor:
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = SystemMonitor()
    return _monitor_instance
