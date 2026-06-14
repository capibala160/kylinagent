#!/usr/bin/env python3
"""
软件功能测试报告脚本
覆盖：登录、健康检查、工具列表、聊天接口、审计日志
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from httpx import AsyncClient


BASE_URL = "http://127.0.0.1:8000"
TEST_USER = "testuser"
TEST_PASS = "TestPass123"


async def run_tests():
    print("=" * 60)
    print("Kylin Safe Ops Agent - 功能测试")
    print("=" * 60)
    
    async with AsyncClient(base_url=BASE_URL) as client:
        passed = 0
        failed = 0
        
        # 1. 健康检查
        print("\n[1/7] 健康检查...")
        try:
            r = await client.get("/api/health")
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "healthy"
            tools_count = data.get('tools_count', '未知')
            print(f"  ✅ 通过 - {data['agent_name']} v{data['version']}, 工具数: {tools_count}")
            passed += 1
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            failed += 1
        
        # 2. 注册
        print("\n[2/7] 用户注册...")
        try:
            r = await client.post("/api/auth/register", json={
                "username": TEST_USER,
                "password": TEST_PASS
            })
            # 可能已存在
            assert r.status_code in [200, 409]
            print(f"  ✅ 通过 - 状态码: {r.status_code}")
            passed += 1
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            failed += 1
        
        # 3. 登录
        print("\n[3/7] 用户登录...")
        session_cookie = None
        try:
            r = await client.post("/api/auth/login", json={
                "username": TEST_USER,
                "password": TEST_PASS
            })
            assert r.status_code == 200
            session_cookie = r.cookies.get("ops_session")
            print(f"  ✅ 通过 - Session: {session_cookie[:20]}..." if session_cookie else "  ✅ 通过")
            passed += 1
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            failed += 1
        
        # 4. 获取当前用户
        print("\n[4/7] 获取当前用户信息...")
        try:
            r = await client.get("/api/auth/me")
            assert r.status_code == 200
            data = r.json()
            assert data["success"] is True
            print(f"  ✅ 通过 - 用户名: {data['username']}")
            passed += 1
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            failed += 1
        
        # 5. 工具列表
        print("\n[5/7] 获取工具列表...")
        try:
            r = await client.get("/api/tools")
            assert r.status_code == 200
            data = r.json()
            tools = data.get("tools", [])
            print(f"  ✅ 通过 - 共 {len(tools)} 个工具")
            # 打印前5个工具名
            for t in tools[:5]:
                print(f"     - {t['name']}: {t['description'][:40]}...")
            passed += 1
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            failed += 1
        
        # 6. 聊天接口（系统信息）
        print("\n[6/7] 聊天接口 - 系统信息...")
        try:
            r = await client.post("/api/chat", json={
                "message": "查看系统内存",
                "session_id": "test_session_001"
            })
            assert r.status_code == 200
            data = r.json()
            assert data["success"] is True
            print(f"  ✅ 通过 - 返回消息长度: {len(data['message'])}")
            passed += 1
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            failed += 1
        
        # 7. 审计日志
        print("\n[7/7] 审计日志查询...")
        try:
            r = await client.get("/api/audit/chains?limit=10")
            assert r.status_code == 200
            data = r.json()
            chains = data.get("chains", [])
            print(f"  ✅ 通过 - 共 {len(chains)} 条记录")
            passed += 1
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            failed += 1
        
        # 总结
        print("\n" + "=" * 60)
        print(f"测试结果: 通过 {passed} / 失败 {failed} / 总计 {passed + failed}")
        print("=" * 60)
        
        return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
