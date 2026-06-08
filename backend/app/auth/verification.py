"""
验证码管理模块

负责验证码的生成、存储、校验和过期清理。
支持邮箱验证码和短信验证码。
"""

import random
import time
import logging
from typing import Optional, Dict

from ..db import (
    db_create_verification_code,
    db_get_latest_verification_code,
    db_mark_verification_code_used,
    db_cleanup_expired_verification_codes,
)

logger = logging.getLogger(__name__)

# 验证码有效期（秒）
CODE_EXPIRY = 300  # 5 分钟
# 发送间隔（秒）
SEND_INTERVAL = 60  # 1 分钟
# 验证码长度
CODE_LENGTH = 6


class VerificationManager:
    """验证码管理器"""

    @staticmethod
    def generate_code(length: int = CODE_LENGTH) -> str:
        """生成随机数字验证码"""
        return "".join([str(random.randint(0, 9)) for _ in range(length)])

    @classmethod
    async def send_code(cls, target: str, target_type: str, purpose: str) -> Dict:
        """
        发送验证码。

        Args:
            target: 邮箱地址或手机号
            target_type: "email" 或 "phone"
            purpose: "register" / "login" / "reset_password"

        Returns:
            {"success": bool, "message": str, "cooldown": int}
        """
        # 检查是否在冷却期内
        latest = await db_get_latest_verification_code(target, target_type, purpose)
        if latest:
            elapsed = time.time() - latest["created_at"]
            if elapsed < SEND_INTERVAL:
                cooldown = int(SEND_INTERVAL - elapsed)
                return {
                    "success": False,
                    "message": f"请 {cooldown} 秒后再试",
                    "cooldown": cooldown,
                }

        code = cls.generate_code()
        expires_at = time.time() + CODE_EXPIRY

        # 保存到数据库
        await db_create_verification_code(target, target_type, code, purpose, expires_at)

        # 发送（实际发送由调用方处理，这里只返回验证码内容用于调试/日志）
        logger.info(f"[验证码] {target_type}={target}, purpose={purpose}, code={code}")

        return {
            "success": True,
            "message": "验证码已发送",
            "code": code,  # 仅在开发/调试时使用，生产环境不应返回
            "cooldown": SEND_INTERVAL,
        }

    @classmethod
    async def verify_code(cls, target: str, target_type: str, purpose: str,
                          code: str, consume: bool = True) -> bool:
        """
        校验验证码。

        Args:
            target: 邮箱或手机号
            target_type: "email" 或 "phone"
            purpose: 用途
            code: 用户输入的验证码
            consume: 校验通过后是否标记为已使用

        Returns:
            bool: 校验是否通过
        """
        latest = await db_get_latest_verification_code(target, target_type, purpose)
        if not latest:
            return False

        # 检查是否过期
        if time.time() > latest["expires_at"]:
            return False

        # 检查是否匹配
        if latest["code"] != code:
            return False

        # 标记为已使用
        if consume:
            await db_mark_verification_code_used(latest["id"])

        return True

    @classmethod
    async def cleanup_expired(cls) -> int:
        """清理过期验证码"""
        return await db_cleanup_expired_verification_codes()
