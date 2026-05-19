"""根因分析策略——基于规则的智能诊断引擎

每种分析器接收原始命令输出，通过规则引擎自动识别异常、定位根因、给出修复建议。
无需 LLM 即可快速完成确定性分析，适合比赛场景的稳定演示。
"""

import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from .models import AnalysisResult, RootCause, Severity


# ============================================================
# 工具函数
# ============================================================

def _run_cmd(cmd: List[str], timeout: int = 10) -> str:
    """安全执行命令并返回 stdout"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def _parse_df(df_output: str) -> List[Dict[str, Any]]:
    """解析 df -h 输出为结构化数据"""
    partitions = []
    lines = df_output.strip().split("\n")
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        # 处理挂载点带空格的情况（取最后一个作为挂载点）
        filesystem = parts[0]
        size = parts[1]
        used = parts[2]
        available = parts[3]
        use_percent = parts[4].replace("%", "")
        mountpoint = parts[5]
        try:
            use_val = int(use_percent)
        except ValueError:
            use_val = 0
        partitions.append({
            "filesystem": filesystem,
            "size": size,
            "used": used,
            "available": available,
            "use_percent": use_val,
            "mountpoint": mountpoint,
        })
    return partitions


def _parse_size_to_mb(size_str: str) -> float:
    """将人类可读大小转为 MB 用于比较"""
    size_str = size_str.strip().upper()
    units = {"B": 1/1024/1024, "K": 1/1024, "M": 1, "G": 1024, "T": 1024*1024}
    for unit, factor in units.items():
        if size_str.endswith(unit):
            try:
                return float(size_str[:-1]) * factor
            except ValueError:
                return 0.0
    try:
        return float(size_str) / 1024 / 1024
    except ValueError:
        return 0.0


def _parse_ps_aux(ps_output: str) -> List[Dict[str, Any]]:
    """解析 ps aux 输出"""
    processes = []
    lines = ps_output.strip().split("\n")
    if not lines:
        return processes
    header = lines[0]
    # 定位列索引
    cols = header.split()
    for line in lines[1:]:
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        try:
            processes.append({
                "user": parts[0],
                "pid": int(parts[1]),
                "cpu": float(parts[2]),
                "mem": float(parts[3]),
                "vsz": parts[4],
                "rss": parts[5],
                "tty": parts[6],
                "stat": parts[7],
                "start": parts[8],
                "time": parts[9],
                "command": parts[10],
            })
        except (ValueError, IndexError):
            continue
    return processes


def _parse_loadavg() -> Tuple[float, float, float, int]:
    """解析 /proc/loadavg，返回 (1min, 5min, 15min, running/total)"""
    try:
        with open("/proc/loadavg", "r") as f:
            data = f.read().strip().split()
        return float(data[0]), float(data[1]), float(data[2]), len(data)
    except Exception:
        return 0.0, 0.0, 0.0, 0


def _get_cpu_cores() -> int:
    """获取 CPU 核心数"""
    try:
        result = subprocess.run(["nproc"], capture_output=True, text=True, timeout=5)
        return int(result.stdout.strip())
    except Exception:
        return 1


def _parse_free(free_output: str) -> Dict[str, Any]:
    """解析 free 输出，提取内存关键指标"""
    mem_info = {}
    lines = free_output.strip().split("\n")
    for line in lines:
        if line.startswith("Mem:"):
            parts = line.split()
            if len(parts) >= 7:
                mem_info["total"] = parts[1]
                mem_info["used"] = parts[2]
                mem_info["free"] = parts[3]
                mem_info["shared"] = parts[4]
                mem_info["buff_cache"] = parts[5]
                mem_info["available"] = parts[6]
        elif line.startswith("Swap:"):
            parts = line.split()
            if len(parts) >= 3:
                mem_info["swap_total"] = parts[1]
                mem_info["swap_used"] = parts[2]
    return mem_info


def _parse_iostat(iostat_output: str) -> List[Dict[str, Any]]:
    """解析 iostat -x 输出，提取设备指标"""
    devices = []
    lines = iostat_output.strip().split("\n")
    in_device_section = False
    for line in lines:
        if line.strip().startswith("Device"):
            in_device_section = True
            continue
        if in_device_section and line.strip():
            parts = line.split()
            if len(parts) >= 14:
                try:
                    devices.append({
                        "device": parts[0],
                        "r/s": float(parts[3]),
                        "w/s": float(parts[4]),
                        "rkB/s": float(parts[5]),
                        "wkB/s": float(parts[6]),
                        "await": float(parts[9]),
                        "r_await": float(parts[10]),
                        "w_await": float(parts[11]),
                        "svctm": float(parts[12]),
                        "%util": float(parts[13]),
                    })
                except (ValueError, IndexError):
                    continue
    return devices


# ============================================================
# 磁盘分析器
# ============================================================

class DiskAnalyzer:
    """磁盘空间根因分析器"""

    WARNING_THRESHOLD = 85
    CRITICAL_THRESHOLD = 95

    def analyze(self, df_output: str, du_output: str = "", large_files: str = "") -> AnalysisResult:
        partitions = _parse_df(df_output)
        if not partitions:
            return AnalysisResult(
                target="disk",
                is_anomaly=False,
                severity=Severity.NORMAL,
                root_causes=[],
                summary="无法获取磁盘分区信息",
                raw_data={"df": df_output},
            )

        root_causes: List[RootCause] = []
        anomaly_parts = []

        for p in partitions:
            use = p["use_percent"]
            mount = p["mountpoint"]
            if use >= self.CRITICAL_THRESHOLD:
                anomaly_parts.append((mount, use, Severity.CRITICAL))
            elif use >= self.WARNING_THRESHOLD:
                anomaly_parts.append((mount, use, Severity.WARNING))

        # 对每个异常分区深挖根因
        for mount, use, sev in anomaly_parts:
            causes = self._deep_analyze_partition(mount, use, du_output, large_files)
            root_causes.extend(causes)

        is_anomaly = len(anomaly_parts) > 0
        severity = Severity.NORMAL
        if any(s == Severity.CRITICAL for _, _, s in anomaly_parts):
            severity = Severity.CRITICAL
        elif any(s == Severity.WARNING for _, _, s in anomaly_parts):
            severity = Severity.WARNING

        summary = "磁盘空间正常"
        if is_anomaly:
            parts_str = ", ".join([f"{m}({u}%)" for m, u, _ in anomaly_parts])
            summary = f"检测到磁盘空间异常：{parts_str}"

        return AnalysisResult(
            target="disk",
            is_anomaly=is_anomaly,
            severity=severity,
            root_causes=root_causes,
            summary=summary,
            raw_data={"df": df_output, "du": du_output, "large_files": large_files},
        )

    def _deep_analyze_partition(self, mount: str, use_percent: int,
                                du_output: str, large_files: str) -> List[RootCause]:
        causes = []

        # 1. 如果 du_output 中有该挂载点下的目录信息，分析目录类型
        dir_types = []
        if du_output:
            for line in du_output.strip().split("\n"):
                parts = line.split(None, 1)
                if len(parts) < 2:
                    continue
                size_str, path = parts[0], parts[1].strip()
                size_mb = _parse_size_to_mb(size_str)
                if size_mb < 100:  # 小于 100MB 的忽略
                    continue
                if mount in path or path.startswith(mount):
                    dir_types.append((path, size_mb))

        # 按大小排序
        dir_types.sort(key=lambda x: x[1], reverse=True)

        # 2. 分析大文件类型
        file_categories = {
            "日志膨胀": [],
            "包缓存": [],
            "临时文件": [],
            "Core Dump": [],
            "数据库文件": [],
            "其他大文件": [],
        }

        if large_files:
            for line in large_files.strip().split("\n"):
                # 格式: -rw-r--r-- 1 root root 1.2G Jan 1 00:00 /path/to/file
                parts = line.split()
                if len(parts) < 9:
                    continue
                size_str = parts[4]
                filepath = parts[8]
                size_mb = _parse_size_to_mb(size_str)
                if size_mb < 50:
                    continue

                if "/var/log" in filepath or filepath.endswith(".log"):
                    file_categories["日志膨胀"].append((filepath, size_mb))
                elif "/var/cache" in filepath or "/var/lib/yum" in filepath or "/var/lib/apt" in filepath:
                    file_categories["包缓存"].append((filepath, size_mb))
                elif "/tmp" in filepath or filepath.endswith(".tmp"):
                    file_categories["临时文件"].append((filepath, size_mb))
                elif "core." in filepath or filepath.startswith("core"):
                    file_categories["Core Dump"].append((filepath, size_mb))
                elif "/var/lib/mysql" in filepath or "/var/lib/pgsql" in filepath or filepath.endswith(".db"):
                    file_categories["数据库文件"].append((filepath, size_mb))
                else:
                    file_categories["其他大文件"].append((filepath, size_mb))

        # 3. 生成根因
        if file_categories["日志膨胀"]:
            total = sum(s for _, s in file_categories["日志膨胀"])
            top3 = file_categories["日志膨胀"][:3]
            evidence = [f"{f} ({s:.0f}MB)" for f, s in top3]
            log_path = "/var/log" if mount == "/" else f"{mount}/var/log"
            causes.append(RootCause(
                category="日志膨胀",
                description=f"{mount} 下日志文件占用 {total:.0f}MB 空间，导致分区使用率 {use_percent}%",
                confidence=min(0.95, 0.7 + len(evidence) * 0.05),
                evidence=evidence,
                recommendation=f"建议清理 {log_path} 下过期日志，可使用 journalctl --vacuum-time=7d 或手动清理 .log 文件",
                risk_level="low",
            ))

        if file_categories["包缓存"]:
            total = sum(s for _, s in file_categories["包缓存"])
            causes.append(RootCause(
                category="包缓存堆积",
                description=f"{mount} 下软件包缓存占用 {total:.0f}MB",
                confidence=0.9,
                evidence=[f"{f} ({s:.0f}MB)" for f, s in file_categories["包缓存"][:3]],
                recommendation="执行 yum clean all 或 apt-get clean 清理包缓存",
                risk_level="safe",
            ))

        if file_categories["Core Dump"]:
            total = sum(s for _, s in file_categories["Core Dump"])
            causes.append(RootCause(
                category="Core Dump 堆积",
                description=f"发现 {len(file_categories['Core Dump'])} 个 core dump 文件，共 {total:.0f}MB",
                confidence=0.95,
                evidence=[f"{f} ({s:.0f}MB)" for f, s in file_categories["Core Dump"][:3]],
                recommendation="清理 core dump 文件，并检查 ulimit -c 设置，必要时限制 core 文件生成",
                risk_level="low",
            ))

        if file_categories["临时文件"]:
            total = sum(s for _, s in file_categories["临时文件"])
            causes.append(RootCause(
                category="临时文件堆积",
                description=f"{mount} 下临时文件占用 {total:.0f}MB",
                confidence=0.85,
                evidence=[f"{f} ({s:.0f}MB)" for f, s in file_categories["临时文件"][:3]],
                recommendation="清理 /tmp 目录下过期临时文件，注意排除正在使用的文件",
                risk_level="medium",
            ))

        if file_categories["数据库文件"]:
            total = sum(s for _, s in file_categories["数据库文件"])
            causes.append(RootCause(
                category="数据库文件增长",
                description=f"数据库相关文件占用 {total:.0f}MB",
                confidence=0.8,
                evidence=[f"{f} ({s:.0f}MB)" for f, s in file_categories["数据库文件"][:3]],
                recommendation="检查数据库日志和备份策略，确认是否有异常增长或重复备份",
                risk_level="medium",
            ))

        # 如果没有找到具体根因，给一个通用的
        if not causes:
            top_dirs = [f"{p} ({s:.0f}MB)" for p, s in dir_types[:5]]
            causes.append(RootCause(
                category="空间不足",
                description=f"{mount} 使用率 {use_percent}%，未匹配到已知空间占用模式",
                confidence=0.6,
                evidence=top_dirs if top_dirs else [f"{mount} 使用率 {use_percent}%"],
                recommendation=f"建议进一步分析 {mount} 下各目录空间分布，定位异常增长来源",
                risk_level="medium" if use_percent >= self.CRITICAL_THRESHOLD else "low",
            ))

        return causes


# ============================================================
# 进程分析器
# ============================================================

class ProcessAnalyzer:
    """进程问题根因分析器"""

    def analyze(self, ps_output: str, zombie_output: str = "") -> AnalysisResult:
        processes = _parse_ps_aux(ps_output)
        root_causes: List[RootCause] = []

        # 1. 僵尸进程分析
        zombie_count = 0
        zombie_parents = []
        if zombie_output:
            # 精确匹配 Z 状态
            for line in zombie_output.strip().split("\n"):
                if "Z" in line or "zombie" in line.lower():
                    parts = line.split()
                    if len(parts) >= 3:
                        try:
                            pid = int(parts[0])
                            ppid = int(parts[1])
                            zombie_count += 1
                            zombie_parents.append(ppid)
                        except ValueError:
                            continue

        if zombie_count > 0:
            # 分析父进程
            parent_info = []
            for ppid in set(zombie_parents):
                # 查找父进程
                parent = next((p for p in processes if p["pid"] == ppid), None)
                if parent:
                    parent_info.append(f"父进程 {ppid} ({parent['command'][:50]}) 状态 {parent['stat']}")
                else:
                    # 尝试读取 /proc/ppid/status
                    status = _run_cmd(["cat", f"/proc/{ppid}/status"], timeout=2)
                    name = "未知"
                    state = "未知"
                    for sline in status.split("\n"):
                        if sline.startswith("Name:"):
                            name = sline.split(":", 1)[1].strip()
                        elif sline.startswith("State:"):
                            state = sline.split(":", 1)[1].strip()
                    parent_info.append(f"父进程 {ppid} ({name}) 状态 {state}")

            # 判断根因
            if zombie_count >= 10:
                desc = f"系统存在大量僵尸进程({zombie_count}个)，可能是某个父进程异常未回收子进程"
                rec = "查找并重启异常的父进程服务，或通知开发人员修复信号处理逻辑"
                conf = 0.9
            elif zombie_count > 0:
                desc = f"发现 {zombie_count} 个僵尸进程"
                rec = "如果数量持续增长，需检查父进程状态；偶发的僵尸进程通常无害"
                conf = 0.75
            else:
                desc = rec = ""
                conf = 0.0

            if desc:
                root_causes.append(RootCause(
                    category="僵尸进程",
                    description=desc,
                    confidence=conf,
                    evidence=parent_info[:5],
                    recommendation=rec,
                    risk_level="low" if zombie_count < 5 else "medium",
                ))

        # 2. 高 CPU 进程分析
        high_cpu = [p for p in processes if p["cpu"] > 50.0]
        if high_cpu:
            evidence = [f"PID {p['pid']} {p['command'][:60]} CPU={p['cpu']}%" for p in high_cpu[:5]]
            # 判断是否系统进程
            sys_procs = [p for p in high_cpu if p["user"] in ("root", "sys", "daemon")]
            user_procs = [p for p in high_cpu if p["user"] not in ("root", "sys", "daemon")]

            if user_procs:
                desc = f"用户进程高 CPU 占用：{', '.join([p['command'].split()[0] for p in user_procs[:3]])}"
                rec = "评估该进程是否为预期业务负载，必要时联系业务负责人或考虑降频/限流"
            else:
                desc = f"系统进程高 CPU 占用：{', '.join([p['command'].split()[0] for p in sys_procs[:3]])}"
                rec = "检查是否为系统维护任务（如日志轮转、备份），或排查异常内核线程"

            root_causes.append(RootCause(
                category="CPU 高负载",
                description=desc,
                confidence=0.85,
                evidence=evidence,
                recommendation=rec,
                risk_level="medium",
            ))

        # 3. 高内存进程分析
        high_mem = [p for p in processes if p["mem"] > 20.0]
        if high_mem:
            evidence = [f"PID {p['pid']} {p['command'][:60]} MEM={p['mem']}%" for p in high_mem[:5]]
            # 检查是否有多个相同进程（可能是内存泄漏或过度fork）
            cmd_counts: Dict[str, int] = {}
            for p in high_mem:
                base_cmd = p["command"].split()[0]
                cmd_counts[base_cmd] = cmd_counts.get(base_cmd, 0) + 1

            leak_suspects = [cmd for cmd, cnt in cmd_counts.items() if cnt > 5]
            if leak_suspects:
                desc = f"检测到多个相同进程实例({', '.join(leak_suspects[:2])})，疑似服务异常Fork或内存泄漏"
                rec = "检查对应服务的配置（如工作进程数），必要时重启服务"
            else:
                desc = f"单个进程内存占用过高：{high_mem[0]['command'][:60]}"
                rec = "确认是否为预期行为，必要时进行内存优化或扩容"

            root_causes.append(RootCause(
                category="内存高占用",
                description=desc,
                confidence=0.8 if leak_suspects else 0.7,
                evidence=evidence,
                recommendation=rec,
                risk_level="medium",
            ))

        is_anomaly = len(root_causes) > 0
        severity = Severity.NORMAL
        if any(c.risk_level == "critical" for c in root_causes):
            severity = Severity.CRITICAL
        elif any(c.risk_level == "high" for c in root_causes):
            severity = Severity.CRITICAL
        elif any(c.risk_level == "medium" for c in root_causes):
            severity = Severity.WARNING

        summary = "进程状态正常"
        if is_anomaly:
            items = [c.category for c in root_causes]
            summary = f"检测到进程异常：{', '.join(items)}"

        return AnalysisResult(
            target="process",
            is_anomaly=is_anomaly,
            severity=severity,
            root_causes=root_causes,
            summary=summary,
            raw_data={"ps": ps_output, "zombie": zombie_output},
        )


# ============================================================
# 性能分析器
# ============================================================

class PerformanceAnalyzer:
    """系统性能根因分析器（CPU/内存/IO 瓶颈）"""

    def analyze(self, loadavg_1min: float, cpu_cores: int,
                free_output: str = "", iostat_output: str = "",
                vmstat_output: str = "") -> AnalysisResult:
        root_causes: List[RootCause] = []

        # 1. CPU 负载分析
        load_ratio = loadavg_1min / cpu_cores if cpu_cores > 0 else loadavg_1min
        if load_ratio > 2.0:
            root_causes.append(RootCause(
                category="CPU 严重过载",
                description=f"1分钟负载 {loadavg_1min}，CPU核心数 {cpu_cores}，负载比 {load_ratio:.1f}，系统严重过载",
                confidence=0.95,
                evidence=[f"loadavg={loadavg_1min}", f"cpu_cores={cpu_cores}", f"load_ratio={load_ratio:.2f}"],
                recommendation="立即查找高 CPU 进程并考虑终止非关键进程，或进行紧急扩容",
                risk_level="critical",
            ))
        elif load_ratio > 1.0:
            root_causes.append(RootCause(
                category="CPU 高负载",
                description=f"1分钟负载 {loadavg_1min}，负载比 {load_ratio:.1f}，CPU 资源紧张",
                confidence=0.85,
                evidence=[f"loadavg={loadavg_1min}", f"cpu_cores={cpu_cores}"],
                recommendation="排查高 CPU 进程，优化业务负载或增加 CPU 资源",
                risk_level="medium",
            ))
        elif load_ratio > 0.7:
            root_causes.append(RootCause(
                category="CPU 负载偏高",
                description=f"1分钟负载 {loadavg_1min}，负载比 {load_ratio:.1f}，接近饱和",
                confidence=0.7,
                evidence=[f"loadavg={loadavg_1min}", f"cpu_cores={cpu_cores}"],
                recommendation="持续监控，准备在负载继续升高时采取优化措施",
                risk_level="low",
            ))

        # 2. 内存分析
        if free_output:
            mem = _parse_free(free_output)
            if mem.get("total") and mem.get("available"):
                try:
                    total_mb = _parse_size_to_mb(mem["total"] + "M")
                    avail_mb = _parse_size_to_mb(mem["available"] + "M")
                    if total_mb > 0:
                        avail_ratio = avail_mb / total_mb
                        if avail_ratio < 0.05:
                            root_causes.append(RootCause(
                                category="内存严重不足",
                                description=f"可用内存仅 {mem['available']} / {mem['total']} ({avail_ratio*100:.1f}%)，系统面临 OOM 风险",
                                confidence=0.95,
                                evidence=[f"total={mem['total']}", f"available={mem['available']}", f"used={mem['used']}"],
                                recommendation="紧急释放内存：终止非关键大进程、清理缓存(echo 3 > /proc/sys/vm/drop_caches)、或扩容内存",
                                risk_level="critical",
                            ))
                        elif avail_ratio < 0.1:
                            root_causes.append(RootCause(
                                category="内存不足",
                                description=f"可用内存 {mem['available']} / {mem['total']} ({avail_ratio*100:.1f}%)",
                                confidence=0.85,
                                evidence=[f"total={mem['total']}", f"available={mem['available']}", f"buff_cache={mem.get('buff_cache','')}"],
                                recommendation="检查是否有内存泄漏进程，考虑清理 pagecache 或增加 SWAP",
                                risk_level="high",
                            ))
                except Exception:
                    pass

            # Swap 分析
            if mem.get("swap_total") and mem.get("swap_used"):
                try:
                    swap_total_mb = _parse_size_to_mb(mem["swap_total"] + "M")
                    swap_used_mb = _parse_size_to_mb(mem["swap_used"] + "M")
                    if swap_total_mb > 0 and swap_used_mb / swap_total_mb > 0.5:
                        root_causes.append(RootCause(
                            category="Swap 大量使用",
                            description=f"Swap 使用率 {(swap_used_mb/swap_total_mb)*100:.1f}% ({mem['swap_used']}/{mem['swap_total']})",
                            confidence=0.8,
                            evidence=[f"swap_total={mem['swap_total']}", f"swap_used={mem['swap_used']}"],
                            recommendation="内存不足导致大量换页，建议增加物理内存或优化应用内存使用",
                            risk_level="medium",
                        ))
                except Exception:
                    pass

        # 3. IO 分析
        if iostat_output:
            devices = _parse_iostat(iostat_output)
            high_io_devices = [d for d in devices if d["%util"] > 80]
            if high_io_devices:
                evidence = [f"{d['device']} util={d['%util']:.1f}% await={d['await']:.1f}ms" for d in high_io_devices[:3]]
                # 判断是读还是写密集
                reads = sum(d["rkB/s"] for d in high_io_devices)
                writes = sum(d["wkB/s"] for d in high_io_devices)
                if writes > reads * 2:
                    desc = f"磁盘写密集型 IO 瓶颈：{', '.join([d['device'] for d in high_io_devices[:2]])}"
                    rec = "检查是否有大量日志写入、数据库批量操作或临时文件生成"
                elif reads > writes * 2:
                    desc = f"磁盘读密集型 IO 瓶颈：{', '.join([d['device'] for d in high_io_devices[:2]])}"
                    rec = "检查是否有全表扫描、大文件读取或备份任务"
                else:
                    desc = f"磁盘混合 IO 瓶颈：{', '.join([d['device'] for d in high_io_devices[:2]])}"
                    rec = "检查 IO 密集型进程，考虑使用 SSD、RAID 优化或调整业务峰值"

                root_causes.append(RootCause(
                    category="IO 瓶颈",
                    description=desc,
                    confidence=0.85,
                    evidence=evidence,
                    recommendation=rec,
                    risk_level="medium",
                ))

        # 4. vmstat 辅助分析
        if vmstat_output:
            lines = vmstat_output.strip().split("\n")
            for line in lines:
                if not line.strip() or line.startswith("procs") or line.startswith("r"):
                    continue
                parts = line.split()
                if len(parts) >= 17:
                    try:
                        r = int(parts[0])  # 运行队列
                        b = int(parts[1])  # 阻塞队列
                        wa = int(parts[15])  # IO等待 (cpu 段 wa 列)
                        if r > cpu_cores * 2:
                            root_causes.append(RootCause(
                                category="运行队列积压",
                                description=f"运行队列长度 {r}，远超 CPU 核心数 {cpu_cores}",
                                confidence=0.8,
                                evidence=[f"run_queue={r}", f"block_queue={b}"],
                                recommendation="大量进程等待 CPU 调度，需优化应用并发模型或增加 CPU",
                                risk_level="medium",
                            ))
                        if wa > 20:
                            root_causes.append(RootCause(
                                category="IO 等待过高",
                                description=f"CPU IO 等待时间占比 {wa}%，磁盘成为瓶颈",
                                confidence=0.85,
                                evidence=[f"io_wait={wa}%"],
                                recommendation="优化磁盘访问模式，考虑缓存策略或升级存储设备",
                                risk_level="medium",
                            ))
                    except (ValueError, IndexError):
                        continue

        is_anomaly = len(root_causes) > 0
        severity = Severity.NORMAL
        if any(c.risk_level == "critical" for c in root_causes):
            severity = Severity.CRITICAL
        elif any(c.risk_level == "high" for c in root_causes):
            severity = Severity.CRITICAL
        elif any(c.risk_level == "medium" for c in root_causes):
            severity = Severity.WARNING

        summary = "系统性能正常"
        if is_anomaly:
            items = [c.category for c in root_causes]
            summary = f"性能瓶颈：{', '.join(items)}"

        return AnalysisResult(
            target="performance",
            is_anomaly=is_anomaly,
            severity=severity,
            root_causes=root_causes,
            summary=summary,
            raw_data={
                "loadavg_1min": loadavg_1min,
                "cpu_cores": cpu_cores,
                "free": free_output,
                "iostat": iostat_output,
                "vmstat": vmstat_output,
            },
        )


# ============================================================
# 服务/日志分析器
# ============================================================

class ServiceLogAnalyzer:
    """基于日志的服务异常根因分析器"""

    ERROR_PATTERNS = [
        (r"Out of memory", "OOM  killer 触发", "critical"),
        (r"SIGSEGV|Segmentation fault", "进程段错误", "high"),
        (r"SIGKILL", "进程被强制终止", "medium"),
        (r"No space left on device", "磁盘空间耗尽", "critical"),
        (r"Permission denied", "权限不足", "medium"),
        (r"Connection refused", "连接被拒绝", "medium"),
        (r"Too many open files", "文件句柄耗尽", "high"),
        (r"Connection timed out", "连接超时", "medium"),
        (r"Can't open PID file", "PID 文件丢失", "medium"),
        (r"Failed to start", "服务启动失败", "high"),
    ]

    def analyze(self, log_output: str, service_name: str = "") -> AnalysisResult:
        root_causes: List[RootCause] = []
        evidence_map: Dict[str, List[str]] = {}

        for pattern, category, risk in self.ERROR_PATTERNS:
            matches = re.findall(pattern, log_output, re.IGNORECASE)
            if matches:
                # 提取上下文
                contexts = []
                for m in re.finditer(pattern, log_output, re.IGNORECASE):
                    start = max(0, m.start() - 80)
                    end = min(len(log_output), m.end() + 80)
                    ctx = log_output[start:end].replace("\n", " ")
                    contexts.append(ctx)
                evidence_map[category] = contexts[:3]

        for category, contexts in evidence_map.items():
            risk = next((r for p, c, r in self.ERROR_PATTERNS if c == category), "medium")
            root_causes.append(RootCause(
                category=category,
                description=f"日志中发现 {len(contexts)} 处 '{category}' 异常记录",
                confidence=min(0.95, 0.7 + len(contexts) * 0.05),
                evidence=contexts,
                recommendation=self._recommendation_for(category),
                risk_level=risk,
            ))

        is_anomaly = len(root_causes) > 0
        severity = Severity.NORMAL
        if any(c.risk_level == "critical" for c in root_causes):
            severity = Severity.CRITICAL
        elif any(c.risk_level == "high" for c in root_causes):
            severity = Severity.CRITICAL
        elif any(c.risk_level == "medium" for c in root_causes):
            severity = Severity.WARNING

        summary = f"{'服务' if service_name else '系统'}日志无异常"
        if is_anomaly:
            items = [c.category for c in root_causes]
            summary = f"日志异常：{', '.join(items)}"

        return AnalysisResult(
            target="service_log",
            is_anomaly=is_anomaly,
            severity=severity,
            root_causes=root_causes,
            summary=summary,
            raw_data={"service": service_name, "log_preview": log_output[:2000]},
        )

    def _recommendation_for(self, category: str) -> str:
        recommendations = {
            "OOM  killer 触发": "检查内存使用趋势，增加物理内存或调整应用内存限制，排查内存泄漏",
            "进程段错误": "通常由程序 Bug 引起，建议更新软件版本或联系开发人员排查",
            "进程被强制终止": "检查是否有人为 kill 操作或 systemd 超时终止",
            "磁盘空间耗尽": "立即清理磁盘空间，重点检查日志和临时文件",
            "权限不足": "检查服务运行用户和文件权限配置",
            "连接被拒绝": "检查目标服务是否运行、端口是否正确、防火墙规则",
            "文件句柄耗尽": "增大 ulimit -n 限制，或排查文件泄漏",
            "连接超时": "检查网络连通性、目标服务负载、DNS 解析",
            "PID 文件丢失": "检查服务是否正常停止，可能需要手动清理并重启",
            "服务启动失败": "根据日志中具体错误信息修复配置或依赖问题",
        }
        return recommendations.get(category, "根据具体错误信息采取相应修复措施")
