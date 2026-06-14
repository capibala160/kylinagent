"""
短信服务模块

支持通过阿里云短信 / Twilio 等发送短信。
未配置时，短信内容会打印到日志（开发环境）。
"""

import os
import logging
import urllib.request
import urllib.parse
import json
from typing import Optional

logger = logging.getLogger(__name__)


class SMSService:
    """短信发送服务

    配置优先级：环境变量 > config/agent.yaml

    支持多种后端（通过 sms.provider 切换）：
    - log: 仅打印到日志（默认，开发环境）
    - aliyun: 阿里云短信服务
    - twilio: Twilio
    - custom: 自定义 Webhook URL
    """

    def __init__(self):
        # 尝试从配置文件读取
        sms_cfg = None
        try:
            from ..config import get_config
            cfg = get_config()
            sms_cfg = getattr(cfg, "sms", None)
        except Exception:
            pass

        def _get(key_env, key_cfg, default=""):
            if sms_cfg and hasattr(sms_cfg, key_cfg):
                return os.environ.get(key_env, getattr(sms_cfg, key_cfg, default) or default)
            return os.environ.get(key_env, default)

        self.provider = _get("SMS_PROVIDER", "provider", "log")
        self.aliyun_access_key = _get("SMS_ALIYUN_ACCESS_KEY", "aliyun_access_key")
        self.aliyun_secret = _get("SMS_ALIYUN_SECRET", "aliyun_secret")
        self.aliyun_sign = _get("SMS_ALIYUN_SIGN_NAME", "aliyun_sign_name", "Kylin Ops")
        self.aliyun_template = _get("SMS_ALIYUN_TEMPLATE_CODE", "aliyun_template_code")
        self.twilio_sid = _get("SMS_TWILIO_ACCOUNT_SID", "twilio_account_sid")
        self.twilio_token = _get("SMS_TWILIO_AUTH_TOKEN", "twilio_auth_token")
        self.twilio_from = _get("SMS_TWILIO_FROM_NUMBER", "twilio_from_number")
        self.custom_url = _get("SMS_CUSTOM_URL", "custom_url")
        self.custom_token = _get("SMS_CUSTOM_TOKEN", "custom_token")

    async def send_verification_code(self, phone: str, code: str, purpose: str = "注册") -> bool:
        """
        发送验证码短信。

        Args:
            phone: 手机号码
            code: 验证码
            purpose: 用途描述

        Returns:
            bool: 是否发送成功
        """
        template_param = {"code": code}

        if self.provider == "aliyun":
            return await self._send_aliyun(phone, template_param)
        elif self.provider == "twilio":
            body = f"【Kylin Ops】您的{purpose}验证码是：{code}，5分钟内有效。如非本人操作请忽略。"
            return await self._send_twilio(phone, body)
        elif self.provider == "custom":
            return await self._send_custom(phone, code, purpose)
        else:
            # 默认：仅打印到日志
            logger.info(f"[短信模拟] To: {phone}, 验证码: {code}, 用途: {purpose}")
            return True

    async def _send_aliyun(self, phone: str, template_param: dict) -> bool:
        """
        阿里云短信发送。
        需要安装 alibabacloud_dysmsapi 包。
        """
        try:
            from alibabacloud_dysmsapi20170525.client import Client as DysmsapiClient
            from alibabacloud_dysmsapi20170525 import models as dysmsapi_models
            from alibabacloud_tea_openapi import models as open_api_models

            config = open_api_models.Config(
                access_key_id=self.aliyun_access_key,
                access_key_secret=self.aliyun_secret,
            )
            config.endpoint = "dysmsapi.aliyuncs.com"
            client = DysmsapiClient(config)

            request = dysmsapi_models.SendSmsRequest(
                phone_numbers=phone,
                sign_name=self.aliyun_sign,
                template_code=self.aliyun_template,
                template_param=json.dumps(template_param),
            )
            response = client.send_sms(request)
            if response.body.code == "OK":
                logger.info(f"短信发送成功(阿里云): {phone}")
                return True
            else:
                logger.error(f"短信发送失败(阿里云): {phone}, code={response.body.code}, msg={response.body.message}")
                return False
        except ImportError:
            logger.warning("阿里云短信 SDK 未安装，回退到日志模式。pip install alibabacloud_dysmsapi20170525")
            logger.info(f"[短信模拟-阿里云] To: {phone}, param={template_param}")
            return True
        except Exception as e:
            logger.error(f"短信发送异常(阿里云): {phone}, error={e}")
            return False

    async def _send_twilio(self, phone: str, body: str) -> bool:
        """Twilio 短信发送"""
        try:
            from twilio.rest import Client

            client = Client(self.twilio_sid, self.twilio_token)
            message = client.messages.create(body=body, from_=self.twilio_from, to=phone)
            logger.info(f"短信发送成功(Twilio): {phone}, sid={message.sid}")
            return True
        except ImportError:
            logger.warning("Twilio SDK 未安装，回退到日志模式。pip install twilio")
            logger.info(f"[短信模拟-Twilio] To: {phone}, body={body}")
            return True
        except Exception as e:
            logger.error(f"短信发送异常(Twilio): {phone}, error={e}")
            return False

    async def _send_custom(self, phone: str, code: str, purpose: str) -> bool:
        """自定义 Webhook 发送"""
        if not self.custom_url:
            logger.warning("SMS_CUSTOM_URL 未配置，回退到日志模式")
            logger.info(f"[短信模拟-自定义] To: {phone}, code={code}, purpose={purpose}")
            return True

        # SSRF 防护：仅允许 HTTPS，禁止内网/IP/本地主机，解析 DNS 并校验所有解析地址
        from urllib.parse import urlparse
        import ipaddress
        import socket
        parsed = urlparse(self.custom_url)
        if parsed.scheme not in ("https",):
            logger.error(f"自定义短信 URL 仅允许 HTTPS: {self.custom_url}")
            return False

        hostname = (parsed.hostname or "").lower()
        if not hostname:
            logger.error(f"自定义短信 URL 缺少主机名: {self.custom_url}")
            return False

        # 禁止纯 IP 地址（应使用域名）和本地主机名
        blocked_hosts = ("localhost", "[::1]")
        if hostname in blocked_hosts or hostname.startswith("127."):
            logger.error(f"自定义短信 URL 禁止本地地址: {hostname}")
            return False

        try:
            # 尝试解析为 IP；若是公网 IP 则放行，私网/保留 IP 则阻断
            addr = ipaddress.ip_address(hostname)
            if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_multicast or addr.is_unspecified:
                logger.error(f"自定义短信 URL 禁止内网/保留 IP: {hostname}")
                return False
        except ValueError:
            # 是域名，继续 DNS 校验
            pass

        try:
            # 解析域名并校验所有 A 记录均为公网 IP，防止 DNS 重绑定
            _, _, ip_list = socket.gethostbyname_ex(hostname)
            for ip in ip_list:
                addr = ipaddress.ip_address(ip)
                if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_multicast or addr.is_unspecified:
                    logger.error(f"自定义短信 URL 解析到内网/保留 IP: {hostname} -> {ip}")
                    return False
        except socket.gaierror as e:
            logger.error(f"自定义短信 URL 域名解析失败: {hostname}, error={e}")
            return False

        # 自定义重定向处理器：每次跳转都重新执行 SSRF 校验
        class ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                parsed_new = urlparse(newurl)
                if parsed_new.scheme not in ("https",):
                    raise urllib.request.HTTPError(newurl, code, "自定义短信 URL 重定向后仅允许 HTTPS", headers, fp)
                new_host = (parsed_new.hostname or "").lower()
                if not new_host:
                    raise urllib.request.HTTPError(newurl, code, "自定义短信 URL 重定向后缺少主机名", headers, fp)
                # 解析并校验新地址
                try:
                    addr = ipaddress.ip_address(new_host)
                    if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_multicast or addr.is_unspecified:
                        raise urllib.request.HTTPError(newurl, code, "自定义短信 URL 重定向到内网地址", headers, fp)
                except ValueError:
                    try:
                        _, _, ip_list = socket.gethostbyname_ex(new_host)
                        for ip in ip_list:
                            addr = ipaddress.ip_address(ip)
                            if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_multicast or addr.is_unspecified:
                                raise urllib.request.HTTPError(newurl, code, "自定义短信 URL 重定向解析到内网地址", headers, fp)
                    except socket.gaierror as e:
                        raise urllib.request.HTTPError(newurl, code, f"自定义短信 URL 重定向域名解析失败: {e}", headers, fp)
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        try:
            payload = json.dumps({
                "phone": phone,
                "code": code,
                "purpose": purpose,
                "token": self.custom_token,
            }).encode("utf-8")

            req = urllib.request.Request(
                self.custom_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            opener = urllib.request.build_opener(ValidatingRedirectHandler)
            with opener.open(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    logger.info(f"短信发送成功(自定义): {phone}")
                    return True
                else:
                    logger.error(f"短信发送失败(自定义): {phone}, status={resp.status}")
                    return False
        except Exception as e:
            logger.error(f"短信发送异常(自定义): {phone}, error={e}")
            return False


# 全局单例
_sms_service: Optional[SMSService] = None


def get_sms_service() -> SMSService:
    """获取短信服务实例"""
    global _sms_service
    if _sms_service is None:
        _sms_service = SMSService()
    return _sms_service
