from server.user.tencent_sms import (
    development_sms_code_logging_enabled,
    mask_phone_for_logging,
)


def test_mask_phone_for_logging_keeps_only_safe_suffix():
    assert mask_phone_for_logging("+8613800138000") == "+86*******8000"
    assert mask_phone_for_logging("13800138000") == "*******8000"


def test_development_sms_code_logging_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("NLP_AGENT_SMS_EXPOSE_CODE", raising=False)
    assert development_sms_code_logging_enabled() is False

    monkeypatch.setenv("NLP_AGENT_SMS_EXPOSE_CODE", "true")
    assert development_sms_code_logging_enabled() is True
