"""Tencent Cloud SMS provider for account verification codes."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def normalize_phone_for_tencent(phone: str) -> str:
    """Return the canonical E.164 number expected by Tencent Cloud."""
    cleaned = "".join(character for character in phone.strip() if character.isdigit() or character == "+")
    if cleaned.startswith("+"):
        return cleaned
    return f"+86{cleaned}"


class TencentSmsProvider:
    """Lazy-loaded Tencent Cloud SMS sender."""

    def __init__(
        self,
        secret_id: str,
        secret_key: str,
        app_id: str,
        sign_name: str,
        template_id: str,
        region: str = "ap-guangzhou",
    ) -> None:
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.app_id = app_id
        self.sign_name = sign_name
        self.template_id = template_id
        self.region = region
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from tencentcloud.common import credential
                from tencentcloud.sms.v20210111 import sms_client
            except ImportError as error:
                raise RuntimeError(
                    "tencentcloud-sdk-python is not installed"
                ) from error
            credentials = credential.Credential(self.secret_id, self.secret_key)
            self._client = sms_client.SmsClient(credentials, self.region)
        return self._client

    async def send_verification_code(self, phone: str, code: str) -> bool:
        try:
            from tencentcloud.sms.v20210111 import models

            request = models.SendSmsRequest()
            request.SmsSdkAppId = self.app_id
            request.SignName = self.sign_name
            request.TemplateId = self.template_id
            request.TemplateParamSet = [code]
            request.PhoneNumberSet = [normalize_phone_for_tencent(phone)]
            response = await asyncio.to_thread(self._get_client().SendSms, request)
            return bool(response.SendStatusSet and response.SendStatusSet[0].Code == "Ok")
        except Exception:
            logger.exception("Tencent SMS delivery failed")
            return False


class DeterministicSmsProvider:
    """Deterministic provider stub for the isolated HTTP integration suite."""

    def __init__(self, *, failure_prefix: str = "") -> None:
        self.failure_prefix = failure_prefix

    async def send_verification_code(self, phone: str, code: str) -> bool:
        del code
        return not self.failure_prefix or not phone.startswith(self.failure_prefix)


def create_tencent_sms_provider_from_env() -> Optional[TencentSmsProvider]:
    if os.getenv("NLP_AGENT_API_HTTP_SMS_PROVIDER", "").strip().lower() == "stub":
        return DeterministicSmsProvider(
            failure_prefix=os.getenv("NLP_AGENT_API_HTTP_SMS_FAILURE_PREFIX", "").strip()
        )

    values = {
        "secret_id": os.getenv("TENCENT_SMS_SECRET_ID"),
        "secret_key": os.getenv("TENCENT_SMS_SECRET_KEY"),
        "app_id": os.getenv("TENCENT_SMS_APP_ID"),
        "sign_name": os.getenv("TENCENT_SMS_SIGN_NAME"),
        "template_id": os.getenv("TENCENT_SMS_TEMPLATE_ID"),
    }
    if not all(values.values()):
        return None
    return TencentSmsProvider(
        **values,  # type: ignore[arg-type]
        region=os.getenv("TENCENT_SMS_REGION", "ap-guangzhou"),
    )
