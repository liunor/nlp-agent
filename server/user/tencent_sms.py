"""Tencent Cloud SMS provider for verification codes.

This module implements the SmsProvider protocol using Tencent Cloud's SMS API.
Production deployments should configure the required credentials in .env file.

Required environment variables:
- TENCENT_SMS_SECRET_ID: Tencent Cloud SecretId
- TENCENT_SMS_SECRET_KEY: Tencent Cloud SecretKey
- TENCENT_SMS_APP_ID: SMS application ID (SDK AppID)
- TENCENT_SMS_SIGN_NAME: SMS signature name (must be approved by Tencent)
- TENCENT_SMS_TEMPLATE_ID: SMS template ID for verification codes (e.g., "1234567")
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def normalize_phone_for_tencent(phone: str) -> str:
    """Tencent Cloud requires E.164 numbers (``+8613800138000``).

    Domestic numbers entered without a country code get ``+86`` prepended.
    """
    cleaned = "".join(ch for ch in phone.strip() if ch.isdigit() or ch == "+")
    if cleaned.startswith("+"):
        return cleaned
    return f"+86{cleaned}"


class TencentSmsProvider:
    """Tencent Cloud SMS sender implementation.

    Requires tencentcloud-sdk-python package to be installed.
    Install with: pip install tencentcloud-sdk-python
    """

    def __init__(
        self,
        secret_id: str,
        secret_key: str,
        app_id: str,
        sign_name: str,
        template_id: str,
        region: str = "ap-guangzhou",
    ):
        """Initialize Tencent SMS provider.

        Args:
            secret_id: Tencent Cloud SecretId
            secret_key: Tencent Cloud SecretKey
            app_id: SMS SDK AppID (e.g., "1400xxxxxx")
            sign_name: Approved SMS signature name
            template_id: SMS template ID for verification codes
            region: Tencent Cloud region (default: ap-guangzhou)
        """
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.app_id = app_id
        self.sign_name = sign_name
        self.template_id = template_id
        self.region = region
        self._client = None

    def _get_client(self):
        """Lazy-load the Tencent SMS client."""
        if self._client is None:
            try:
                from tencentcloud.common import credential
                from tencentcloud.sms.v20210111 import sms_client

                cred = credential.Credential(self.secret_id, self.secret_key)
                self._client = sms_client.SmsClient(cred, self.region)
            except ImportError:
                raise RuntimeError(
                    "tencentcloud-sdk-python is not installed. "
                    "Install it with: pip install tencentcloud-sdk-python"
                )
        return self._client

    async def send_verification_code(self, phone: str, code: str) -> bool:
        """Send a verification code via Tencent Cloud SMS.

        Args:
            phone: Recipient phone number (e.g., "13800138000" or "+86138...")
            code: Verification code to send

        Returns:
            True on success, False on failure.
        """
        try:
            from tencentcloud.sms.v20210111 import models

            client = self._get_client()
            req = models.SendSmsRequest()
            req.SmsSdkAppId = self.app_id
            req.SignName = self.sign_name
            req.TemplateId = self.template_id
            # Template parameters: [code]
            req.TemplateParamSet = [code]
            req.PhoneNumberSet = [normalize_phone_for_tencent(phone)]

            # 腾讯云 SDK 是同步 HTTP 调用，丢到线程池避免阻塞事件循环。
            resp = await asyncio.to_thread(client.SendSms, req)
            send_status = resp.SendStatusSet[0]

            if send_status.Code == "Ok":
                logger.info("[TencentSMS] Successfully sent code to %s", phone)
                return True
            else:
                logger.error(
                    "[TencentSMS] Failed to send code to %s: %s - %s",
                    phone,
                    send_status.Code,
                    send_status.Message,
                )
                return False

        except Exception as e:
            logger.error("[TencentSMS] Exception sending code to %s: %s", phone, e)
            return False


class SmsConfigurationError(RuntimeError):
    """Raised when production SMS delivery has not been configured."""


def create_tencent_sms_provider_from_env() -> Optional[TencentSmsProvider]:
    """Create a TencentSmsProvider instance from environment variables.

    Returns:
        TencentSmsProvider instance if all required env vars are set, None otherwise.
    """
    secret_id = os.getenv("TENCENT_SMS_SECRET_ID")
    secret_key = os.getenv("TENCENT_SMS_SECRET_KEY")
    app_id = os.getenv("TENCENT_SMS_APP_ID")
    sign_name = os.getenv("TENCENT_SMS_SIGN_NAME")
    template_id = os.getenv("TENCENT_SMS_TEMPLATE_ID")
    region = os.getenv("TENCENT_SMS_REGION", "ap-guangzhou")

    if not all([secret_id, secret_key, app_id, sign_name, template_id]):
        development = os.getenv("NLP_AGENT_SMS_DEVELOPMENT_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
        if development:
            return None
        raise SmsConfigurationError(
            "Tencent SMS is not configured; set TENCENT_SMS_* or explicitly enable NLP_AGENT_SMS_DEVELOPMENT_MODE"
        )

    return TencentSmsProvider(
        secret_id=secret_id,
        secret_key=secret_key,
        app_id=app_id,
        sign_name=sign_name,
        template_id=template_id,
        region=region,
    )
