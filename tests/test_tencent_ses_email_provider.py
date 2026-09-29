import json
import logging
from types import SimpleNamespace

import pytest
from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
    TencentCloudSDKException,
)

from server.user import email_provider
from server.user.email_provider import (
    EmailConfigurationError,
    TencentSESProvider,
    create_email_provider_from_env,
)


def _set_tencent_ses_env(monkeypatch):
    monkeypatch.setenv("NLP_AGENT_EMAIL_PROVIDER", "tencent_ses")
    monkeypatch.setenv("NLP_AGENT_EMAIL_DEVELOPMENT_MODE", "false")
    monkeypatch.setenv("NLP_AGENT_TENCENT_SES_SECRET_ID", "secret-id-placeholder")
    monkeypatch.setenv("NLP_AGENT_TENCENT_SES_SECRET_KEY", "secret-key-placeholder")
    monkeypatch.setenv("NLP_AGENT_TENCENT_SES_REGION", "ap-hongkong")
    monkeypatch.setenv("NLP_AGENT_TENCENT_SES_FROM", "noreply@mail.lsnunlp.com")
    monkeypatch.setenv("NLP_AGENT_TENCENT_SES_FROM_NAME", "LSNU NLP")
    monkeypatch.setenv("NLP_AGENT_TENCENT_SES_TEMPLATE_ID", "218769")
    monkeypatch.setenv("NLP_AGENT_TENCENT_SES_SUBJECT", "Nova 邮箱验证码")


class FakeSESClient:
    def __init__(self, response=None, error=None):
        self.request = None
        self.response = response or SimpleNamespace(RequestId="request-123")
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def SendEmail(self, request):
        self.request = request
        if self.error is not None:
            raise self.error
        return self.response


def test_tencent_ses_provider_is_selected_from_environment(monkeypatch):
    _set_tencent_ses_env(monkeypatch)

    provider = create_email_provider_from_env()

    assert provider is not None
    assert provider.__class__.__name__ == "TencentSESProvider"


@pytest.mark.asyncio
async def test_tencent_ses_provider_builds_template_request():
    client = FakeSESClient()
    provider = TencentSESProvider(
        secret_id="secret-id-placeholder",
        secret_key="secret-key-placeholder",
        region="ap-hongkong",
        from_email="noreply@mail.lsnunlp.com",
        from_name="LSNU NLP",
        template_id=218769,
        subject="Nova 邮箱验证码",
        client_factory=lambda: client,
    )

    assert (
        await provider.send_verification_code("student@example.com", "483921") is True
    )

    request = client.request
    assert request.FromEmailAddress == "LSNU NLP <noreply@mail.lsnunlp.com>"
    assert request.Destination == ["student@example.com"]
    assert request.Subject == "Nova 邮箱验证码"
    assert request.Template.TemplateID == 218769
    assert json.loads(request.Template.TemplateData) == {"code": "483921"}
    assert request.TriggerType == 1


@pytest.mark.asyncio
async def test_tencent_ses_provider_passes_credentials_and_region_to_sdk(monkeypatch):
    from tencentcloud.common import credential
    from tencentcloud.ses.v20201002 import ses_client_async

    captured = {}
    client = FakeSESClient()

    def fake_credential(secret_id, secret_key):
        captured["credentials"] = (secret_id, secret_key)
        return "fake-credentials"

    def fake_client(credentials, region):
        captured["client_args"] = (credentials, region)
        return client

    monkeypatch.setattr(credential, "Credential", fake_credential)
    monkeypatch.setattr(ses_client_async, "SesClient", fake_client)
    provider = TencentSESProvider(
        secret_id="secret-id-placeholder",
        secret_key="secret-key-placeholder",
        region="ap-hongkong",
        from_email="noreply@mail.lsnunlp.com",
        from_name="LSNU NLP",
        template_id=218769,
        subject="Nova 邮箱验证码",
    )

    assert (
        await provider.send_verification_code("student@example.com", "483921") is True
    )
    assert captured["credentials"] == (
        "secret-id-placeholder",
        "secret-key-placeholder",
    )
    assert captured["client_args"] == ("fake-credentials", "ap-hongkong")


@pytest.mark.asyncio
async def test_tencent_ses_api_exception_returns_false_without_logging_code(caplog):
    caplog.set_level(logging.ERROR, logger="server.user.email_provider")
    error = TencentCloudSDKException(
        code="FailedOperation.SendEmailErr",
        message="send failed",
        requestId="request-456",
    )
    client = FakeSESClient(error=error)
    provider = TencentSESProvider(
        secret_id="secret-id-placeholder",
        secret_key="secret-key-placeholder",
        region="ap-hongkong",
        from_email="noreply@mail.lsnunlp.com",
        from_name="LSNU NLP",
        template_id=218769,
        subject="Nova 邮箱验证码",
        client_factory=lambda: client,
    )

    assert (
        await provider.send_verification_code("student@example.com", "483921") is False
    )
    assert "483921" not in caplog.text
    assert "request-456" in caplog.text
    assert "FailedOperation.SendEmailErr" in caplog.text


def test_tencent_ses_development_mode_does_not_create_provider(monkeypatch):
    monkeypatch.setenv("NLP_AGENT_EMAIL_PROVIDER", "tencent_ses")
    monkeypatch.setenv("NLP_AGENT_EMAIL_DEVELOPMENT_MODE", "true")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("development mode must not construct the SES provider")

    monkeypatch.setattr(email_provider, "TencentSESProvider", fail_if_called)

    assert create_email_provider_from_env() is None


def test_tencent_ses_rejects_application_server_region_as_ses_region(monkeypatch):
    _set_tencent_ses_env(monkeypatch)
    monkeypatch.setenv("NLP_AGENT_TENCENT_SES_REGION", "ap-chengdu")

    with pytest.raises(EmailConfigurationError, match="ap-guangzhou or ap-hongkong"):
        create_email_provider_from_env()
