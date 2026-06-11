"""
邮件服务模块

支持通过 SMTP 发送邮件。
未配置 SMTP 时，邮件内容会打印到日志（开发环境）。
"""

import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from typing import Optional

logger = logging.getLogger(__name__)


class EmailService:
    """邮件发送服务

    配置优先级：环境变量 > config/agent.yaml
    """

    def __init__(self):
        # 尝试从配置文件读取
        try:
            from ..config import get_config
            cfg = get_config()
            email_cfg = getattr(cfg, "email", None)
            if email_cfg:
                self.smtp_host = os.environ.get("SMTP_HOST", getattr(email_cfg, "smtp_host", "") or "")
                self.smtp_port = int(os.environ.get("SMTP_PORT", str(getattr(email_cfg, "smtp_port", 587) or 587)))
                self.smtp_user = os.environ.get("SMTP_USER", getattr(email_cfg, "smtp_user", "") or "")
                self.smtp_password = os.environ.get("SMTP_PASSWORD", getattr(email_cfg, "smtp_password", "") or "")
                self.sender_name = os.environ.get("SMTP_SENDER_NAME", getattr(email_cfg, "sender_name", "Kylin Ops Agent") or "Kylin Ops Agent")
            else:
                self._fallback_env()
        except Exception:
            self._fallback_env()

        self.enabled = bool(self.smtp_host and self.smtp_user and self.smtp_password)

    def _fallback_env(self):
        self.smtp_host = os.environ.get("SMTP_HOST", "")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        self.smtp_user = os.environ.get("SMTP_USER", "")
        self.smtp_password = os.environ.get("SMTP_PASSWORD", "")
        self.sender_name = os.environ.get("SMTP_SENDER_NAME", "Kylin Ops Agent")

    async def send_verification_code(self, to_email: str, code: str, purpose: str = "注册") -> bool:
        """
        发送验证码邮件。

        Args:
            to_email: 收件人邮箱
            code: 验证码
            purpose: 用途描述

        Returns:
            bool: 是否发送成功
        """
        subject = f"[{self.sender_name}] {purpose}验证码"
        body = f"""您好，

您的验证码是：{code}

该验证码 5 分钟内有效，请勿泄露给他人。

如非本人操作，请忽略此邮件。

—— {self.sender_name}
"""
        return await self._send(to_email, subject, body)

    async def send_password_reset(self, to_email: str, code: str) -> bool:
        """发送密码重置验证码邮件"""
        return await self.send_verification_code(to_email, code, "密码重置")

    async def _send(self, to_email: str, subject: str, body: str) -> bool:
        """底层发送方法"""
        if not self.enabled:
            # 未配置 SMTP，打印到日志（开发环境）
            logger.info(f"[邮件模拟] To: {to_email}\nSubject: {subject}\n\n{body}")
            return True

        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["From"] = self.smtp_user
            msg["To"] = Header(to_email, "utf-8")
            msg["Subject"] = Header(subject, "utf-8")

            # 465 端口用 SSL，587 端口用 STARTTLS
            if self.smtp_port == 465:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=10) as server:
                    server.login(self.smtp_user, self.smtp_password)
                    server.sendmail(self.smtp_user, [to_email], msg.as_string())
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_password)
                    server.sendmail(self.smtp_user, [to_email], msg.as_string())

            logger.info(f"邮件发送成功: {to_email}, subject={subject}")
            return True
        except Exception as e:
            logger.error(f"邮件发送失败: {to_email}, error={e}")
            return False


# 全局单例
_email_service: Optional[EmailService] = None


def get_email_service() -> EmailService:
    """获取邮件服务实例"""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
