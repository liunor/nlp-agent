"""Email providers for verification codes.

SMTP remains available for local development and rollback. Production can use
Tencent Cloud SES without changing the registration flow.

SMTP environment variables:
- NLP_AGENT_SMTP_HOST: SMTP server host (e.g. smtp.qq.com)
- NLP_AGENT_SMTP_USER: SMTP login user (usually the sender mailbox)
- NLP_AGENT_SMTP_PASSWORD: SMTP password / authorization code
- NLP_AGENT_SMTP_FROM: sender address shown to recipients

Optional:
- NLP_AGENT_SMTP_PORT (default 465)
- NLP_AGENT_SMTP_SECURITY: "ssl" | "starttls" | "none" (default "ssl")
"""

from __future__ import annotations

import asyncio
import json
import logging
import smtplib
from collections.abc import Callable
from email.mime.text import MIMEText
from typing import Any, Optional, Protocol

from configs.settings import auth_env_bool, auth_env_value

logger = logging.getLogger(__name__)


def mask_email_for_logging(email: str) -> str:
    """Keep the first local character and the domain for operational logs."""
    value = email.strip().casefold()
    local, _, domain = value.partition("@")
    if not domain:
        return "<invalid-email>"
    head = local[:1] or "*"
    return f"{head}***@{domain}"


def development_email_code_logging_enabled() -> bool:
    """Return whether a local developer explicitly opted into code logging."""
    return auth_env_bool("NLP_AGENT_EMAIL_EXPOSE_CODE", False)


class EmailProvider(Protocol):
    async def send_verification_code(self, email: str, code: str) -> bool: ...


class SmtpEmailProvider:
    """Send verification codes through any standard SMTP server."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        security: str = "ssl",
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.security = security

    def _send_sync(self, to: str, code: str) -> None:
        message = MIMEText(
            f"你的 Nova 验证码是 {code}，2 分钟内有效。如非本人操作请忽略本邮件。",
            "plain",
            "utf-8",
        )
        message["Subject"] = "Nova 邮箱验证码"
        message["From"] = self.sender
        message["To"] = to

        if self.security == "ssl":
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=15)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=15)
            server.ehlo()
            if self.security == "starttls":
                server.starttls()
                server.ehlo()
        try:
            if self.username:
                server.login(self.username, self.password)
            server.sendmail(self.sender, [to], message.as_string())
        finally:
            server.quit()

    async def send_verification_code(self, email: str, code: str) -> bool:
        """Send a verification code; returns True on success."""
        try:
            # smtplib is a blocking socket API; keep it off the event loop.
            await asyncio.to_thread(self._send_sync, email, code)
            logger.info(
                "[SMTP] Successfully sent verification code to %s",
                mask_email_for_logging(email),
            )
            return True
        except Exception as e:  # noqa: BLE001 - delivery failures must not crash
            logger.error(
                "[SMTP] Exception sending code to %s: %s",
                mask_email_for_logging(email),
                e,
            )
            return False


def _load_tencent_ses_sdk() -> tuple[Any, Any, Any, type[Exception]]:
    """Load Tencent's official SDK only when the SES provider is used."""
    try:
        from tencentcloud.common import credential
        from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
            TencentCloudSDKException,
        )
        from tencentcloud.ses.v20201002 import models, ses_client_async
    except ImportError as exc:
        raise EmailConfigurationError(
            "Tencent Cloud SES SDK is unavailable; install tencentcloud-sdk-python-ses"
        ) from exc
    return credential, models, ses_client_async, TencentCloudSDKException


class TencentSESProvider:
    """Send verification codes with Tencent Cloud SES's official async SDK."""

    def __init__(
        self,
        *,
        secret_id: str,
        secret_key: str,
        region: str,
        from_email: str,
        from_name: str,
        template_id: int,
        subject: str,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.region = region
        self.from_email = from_email
        self.from_name = from_name
        self.template_id = template_id
        self.subject = subject
        self._client_factory = client_factory

    def _build_request(self, models: Any, email: str, code: str) -> Any:
        request = models.SendEmailRequest()
        request.FromEmailAddress = f"{self.from_name} <{self.from_email}>"
        request.Subject = self.subject
        request.Destination = [email]

        template = models.Template()
        template.TemplateID = self.template_id
        template.TemplateData = json.dumps(
            {"code": code}, ensure_ascii=False, separators=(",", ":")
        )
        request.Template = template
        # Tencent SES uses TriggerType=1 for verification-code messages.
        request.TriggerType = 1
        return request

    async def send_verification_code(self, email: str, code: str) -> bool:
        """Send a verification code; return False for an API delivery failure."""
        credential_module, models, client_module, sdk_exception_type = (
            _load_tencent_ses_sdk()
        )
        try:
            request = self._build_request(models, email, code)
            if self._client_factory is not None:
                client = self._client_factory()
            else:
                credentials = credential_module.Credential(
                    self.secret_id, self.secret_key
                )
                client = client_module.SesClient(credentials, self.region)

            async with client as ses_client:
                response = await ses_client.SendEmail(request)
        except sdk_exception_type as exc:
            logger.error(
                "[Tencent SES] API failed for %s: error_code=%s request_id=%s",
                mask_email_for_logging(email),
                getattr(exc, "code", "unknown"),
                getattr(exc, "requestId", "unknown"),
            )
            return False
        except Exception as exc:  # noqa: BLE001 - delivery must not crash the API
            logger.error(
                "[Tencent SES] Unexpected %s while sending to %s",
                type(exc).__name__,
                mask_email_for_logging(email),
            )
            return False

        logger.info(
            "[Tencent SES] Successfully sent verification code to %s, request_id=%s",
            mask_email_for_logging(email),
            getattr(response, "RequestId", "unknown"),
        )
        return True


class DeterministicEmailProvider:
    """Explicit local provider used only by the real HTTP test environment."""

    def __init__(self, *, failure_prefix: str = "") -> None:
        self.failure_prefix = failure_prefix

    async def send_verification_code(self, email: str, code: str) -> bool:
        del code
        return not self.failure_prefix or not email.strip().startswith(
            self.failure_prefix
        )


class EmailConfigurationError(RuntimeError):
    """Raised when production email delivery has not been configured."""


def _create_smtp_provider_from_env() -> Optional[EmailProvider]:
    host = auth_env_value("NLP_AGENT_SMTP_HOST")
    username = auth_env_value("NLP_AGENT_SMTP_USER")
    password = auth_env_value("NLP_AGENT_SMTP_PASSWORD")
    sender = auth_env_value("NLP_AGENT_SMTP_FROM")
    raw_port = auth_env_value("NLP_AGENT_SMTP_PORT", "465") or "465"
    security = (
        (auth_env_value("NLP_AGENT_SMTP_SECURITY", "ssl") or "ssl").strip().lower()
    )

    try:
        port = int(raw_port)
    except ValueError as exc:
        raise EmailConfigurationError("NLP_AGENT_SMTP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise EmailConfigurationError("NLP_AGENT_SMTP_PORT must be between 1 and 65535")
    if security not in {"ssl", "starttls", "none"}:
        raise EmailConfigurationError(
            "NLP_AGENT_SMTP_SECURITY must be one of: ssl, starttls, none"
        )

    if not all([host, username, password, sender]):
        if auth_env_bool("NLP_AGENT_EMAIL_DEVELOPMENT_MODE", False):
            return None
        raise EmailConfigurationError(
            "Email delivery is not configured; set NLP_AGENT_SMTP_* or explicitly enable NLP_AGENT_EMAIL_DEVELOPMENT_MODE"
        )

    return SmtpEmailProvider(
        host=host,
        port=port,
        username=username,
        password=password,
        sender=sender,
        security=security,
    )


def _create_tencent_ses_provider_from_env() -> EmailProvider:
    names = {
        "secret_id": "NLP_AGENT_TENCENT_SES_SECRET_ID",
        "secret_key": "NLP_AGENT_TENCENT_SES_SECRET_KEY",
        "region": "NLP_AGENT_TENCENT_SES_REGION",
        "from_email": "NLP_AGENT_TENCENT_SES_FROM",
        "from_name": "NLP_AGENT_TENCENT_SES_FROM_NAME",
        "template_id": "NLP_AGENT_TENCENT_SES_TEMPLATE_ID",
        "subject": "NLP_AGENT_TENCENT_SES_SUBJECT",
    }
    values = {
        key: (auth_env_value(env_name) or "").strip() for key, env_name in names.items()
    }
    missing = [env_name for key, env_name in names.items() if not values[key]]
    if missing:
        raise EmailConfigurationError(
            "Missing Tencent SES configuration: " + ", ".join(missing)
        )

    if values["region"] not in {"ap-guangzhou", "ap-hongkong"}:
        raise EmailConfigurationError(
            "NLP_AGENT_TENCENT_SES_REGION must be ap-guangzhou or ap-hongkong"
        )

    try:
        template_id = int(values["template_id"])
    except ValueError as exc:
        raise EmailConfigurationError(
            "NLP_AGENT_TENCENT_SES_TEMPLATE_ID must be an integer"
        ) from exc
    if template_id <= 0:
        raise EmailConfigurationError(
            "NLP_AGENT_TENCENT_SES_TEMPLATE_ID must be greater than zero"
        )

    return TencentSESProvider(
        secret_id=values["secret_id"],
        secret_key=values["secret_key"],
        region=values["region"],
        from_email=values["from_email"],
        from_name=values["from_name"],
        template_id=template_id,
        subject=values["subject"],
    )


def create_email_provider_from_env() -> Optional[EmailProvider]:
    """Create the configured email provider.

    The HTTP test stub takes precedence. Otherwise ``smtp`` remains the
    default for backwards compatibility, while ``tencent_ses`` selects the
    official Tencent Cloud SES provider. Development mode skips real delivery.
    """
    if (
        auth_env_value("NLP_AGENT_API_HTTP_EMAIL_PROVIDER", "") or ""
    ).strip().lower() == "stub":
        return DeterministicEmailProvider(
            failure_prefix=(
                auth_env_value("NLP_AGENT_API_HTTP_EMAIL_FAILURE_PREFIX", "") or ""
            ).strip()
        )

    provider_name = (
        (auth_env_value("NLP_AGENT_EMAIL_PROVIDER", "smtp") or "smtp").strip().lower()
    )
    if provider_name == "tencent_ses":
        if auth_env_bool("NLP_AGENT_EMAIL_DEVELOPMENT_MODE", False):
            return None
        return _create_tencent_ses_provider_from_env()
    if provider_name in {"", "smtp"}:
        return _create_smtp_provider_from_env()
    raise EmailConfigurationError(
        "NLP_AGENT_EMAIL_PROVIDER must be one of: smtp, tencent_ses"
    )
