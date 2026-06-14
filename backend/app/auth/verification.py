"""
验证码管理模块

负责验证码的生成、存储、校验和过期清理。
支持邮箱验证码和短信验证码。
"""

import secrets
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
# 同一目标验证码最大错误尝试次数
MAX_FAILED_ATTEMPTS = 5
# 错误尝试锁定时间（秒）
FAILED_LOCKOUT_SECONDS = 900  # 15 分钟


class VerificationManager:
    """验证码管理器"""

    # 内存中记录校验失败时间戳 {(target, target_type, purpose): [timestamp, ...]}
    _failed_attempts: Dict[str, List[float]] = {}

    @classmethod
    def _check_locked(cls, key: str) -> bool:
        """检查目标是否因错误次数过多被临时锁定"""
        now = time.time()
        attempts = cls._failed_attempts.get(key, [])
        # 只保留锁定窗口内的记录
        attempts = [t for t in attempts if now - t < FAILED_LOCKOUT_SECONDS]
        cls._failed_attempts[key] = attempts
        return len(attempts) >= MAX_FAILED_ATTEMPTS

    @classmethod
    def _record_failure(cls, key: str):
        """记录一次校验失败"""
        now = time.time()
        attempts = cls._failed_attempts.get(key, [])
        attempts = [t for t in attempts if now - t < FAILED_LOCKOUT_SECONDS]
        attempts.append(now)
        cls._failed_attempts[key] = attempts

    @staticmethod
    def generate_code(length: int = CODE_LENGTH) -> str:
        """生成随机数字验证码"""
        return "".join([str(secrets.randbelow(10)) for _ in range(length)])

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

        # 发送（实际发送由调用方处理）
        # 日志脱敏：不记录验证码明文，防止日志泄露
        logger.info(f"[验证码] {target_type}={target}, purpose={purpose}, code=***")

        return {
            "success": True,
            "message": "验证码已发送",
            # 注意：code 仅供内部使用（邮件/短信发送），
            # 调用方负责在生产环境中过滤掉，禁止直接返回给客户端。
            "code": code,
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
        key = f"{target}:{target_type}:{purpose}"

        # 防暴力破解：错误次数过多则锁定
        if cls._check_locked(key):
            logger.warning(f"[验证码] 目标 {target} 因错误尝试过多被临时锁定")
            return False

        latest = await db_get_latest_verification_code(target, target_type, purpose)
        if not latest:
            cls._record_failure(key)
            return False

        # 检查是否过期
        if time.time() > latest["expires_at"]:
            cls._record_failure(key)
            return False

        # 检查是否匹配
        if latest["code"] != code:
            cls._record_failure(key)
            return False

        # 校验成功，清空失败记录
        cls._failed_attempts.pop(key, None)

        # 标记为已使用
        if consume:
            await db_mark_verification_code_used(latest["id"])

        return True

    @classmethod
    async def cleanup_expired(cls) -> int:
        """清理过期验证码"""
        return await db_cleanup_expired_verification_codes()
