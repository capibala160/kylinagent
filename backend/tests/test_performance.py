#!/usr/bin/env python3
"""
软件性能测试报告脚本
核心指标：启动时间、API 响应时间、并发处理能力
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from httpx import AsyncClient


BASE_URL = "http://127.0.0.1:8000"
CONCURRENCY = 10
REQUESTS_PER_CLIENT = 20


async def measure_latency(client: AsyncClient, endpoint: str, method: str = "GET", **kwargs):
    """测量单次请求延迟"""
    start = time.perf_counter()
    if method == "GET":
        r = await client.get(endpoint, **kwargs)
    else:
        r = await client.post(endpoint, **kwargs)
    elapsed = (time.perf_counter() - start) * 1000
    return r.status_code, elapsed


async def worker(client: AsyncClient, endpoint: str, method: str, count: int, **kwargs):
    """工作线程：连续发送请求"""
    latencies = []
    errors = 0
    for _ in range(count):
        try:
            status, latency = await measure_latency(client, endpoint, method, **kwargs)
            if status == 200:
                latencies.append(latency)
            else:
                errors += 1
        except Exception:
            errors += 1
    return latencies, errors


async def run_benchmark():
    print("=" * 60)
    print("Kylin Safe Ops Agent - 性能测试")
    print("=" * 60)
    
    async with AsyncClient(base_url=BASE_URL) as client:
        # 1. 单接口延迟测试
        print("\n[1/3] 单接口延迟测试...")
        
        tests = [
            ("GET", "/api/health", None, "健康检查"),
            ("GET", "/api/tools", None, "工具列表"),
            ("POST", "/api/chat", {"json": {"message": "查看系统内存", "session_id": "perf_test"}}, "聊天接口"),
        ]
        
        for method, endpoint, payload, name in tests:
            latencies = []
            for _ in range(10):
                _, latency = await measure_latency(client, endpoint, method, **(payload or {}))
                latencies.append(latency)
            
            avg = sum(latencies) / len(latencies)
            min_lat = min(latencies)
            max_lat = max(latencies)
            p95 = sorted(latencies)[int(len(latencies) * 0.95)]
            
            print(f"  {name}:")
            print(f"    平均延迟: {avg:.2f}ms | 最小: {min_lat:.2f}ms | 最大: {max_lat:.2f}ms | P95: {p95:.2f}ms")
        
        # 2. 并发压力测试
        print(f"\n[2/3] 并发压力测试 ({CONCURRENCY} 客户端 × {REQUESTS_PER_CLIENT} 请求)...")
        
        tasks = []
        for i in range(CONCURRENCY):
            c = AsyncClient(base_url=BASE_URL)
            tasks.append(worker(c, "/api/health", "GET", REQUESTS_PER_CLIENT))
        
        results = await asyncio.gather(*tasks)
        
        all_latencies = []
        total_errors = 0
        for latencies, errors in results:
            all_latencies.extend(latencies)
            total_errors += errors
        
        total_requests = CONCURRENCY * REQUESTS_PER_CLIENT
        success_rate = (len(all_latencies) / total_requests) * 100
        
        if all_latencies:
            avg = sum(all_latencies) / len(all_latencies)
            min_lat = min(all_latencies)
            max_lat = max(all_latencies)
            p95 = sorted(all_latencies)[int(len(all_latencies) * 0.95)]
            throughput = len(all_latencies) / (max_lat / 1000)
            
            print(f"  总请求: {total_requests} | 成功: {len(all_latencies)} | 失败: {total_errors}")
            print(f"  成功率: {success_rate:.1f}%")
            print(f"  平均延迟: {avg:.2f}ms | 最小: {min_lat:.2f}ms | 最大: {max_lat:.2f}ms | P95: {p95:.2f}ms")
            print(f"  估算吞吐: ~{throughput:.0f} req/s")
        else:
            print("  ⚠️ 无成功请求")
        
        # 3. 工具执行时间测试
        print("\n[3/3] 工具执行时间测试...")
        
        tool_tests = [
            {"message": "查看系统内存", "name": "内存信息"},
            {"message": "查看磁盘空间", "name": "磁盘信息"},
            {"message": "查看进程列表", "name": "进程列表"},
            {"message": "查看网络连接", "name": "网络连接"},
        ]
        
        for test in tool_tests:
            start = time.perf_counter()
            r = await client.post("/api/chat", json={
                "message": test["message"],
                "session_id": f"perf_tool_{test['name']}"
            })
            elapsed = (time.perf_counter() - start) * 1000
            
            if r.status_code == 200:
                data = r.json()
                tool_results = data.get("tool_results", [])
                print(f"  {test['name']}: {elapsed:.2f}ms (调用 {len(tool_results)} 个工具)")
            else:
                print(f"  {test['name']}: 失败 (HTTP {r.status_code})")
    
    print("\n" + "=" * 60)
    print("性能测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_benchmark())
