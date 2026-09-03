"""Tests for server.quota.bootstrap lifecycle configuration and shutdown."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from core.model_runtime.reporters import configure_global_model_usage_reporter
from server.quota import bootstrap


def test_usage_reporter_bootstrap_configures_and_cleans_up(monkeypatch):
    configured = []
    monkeypatch.setattr(
        bootstrap,
        "configure_global_model_usage_reporter",
        lambda reporter, **kwargs: configured.append((reporter, kwargs)),
    )
    engine = create_engine("sqlite:///:memory:")
    reporter = bootstrap.configure_usage_reporter(engine)

    assert reporter is not None
    assert len(configured) == 1
    assert configured[0][0] == reporter

    bootstrap.shutdown_usage_reporter(reporter)
    assert len(configured) == 2
    assert configured[1][0] is None


def test_required_usage_reporter_rejects_missing_database_configuration(monkeypatch):
    with pytest.raises(bootstrap.UsageReporterConfigurationError, match="required"):
        bootstrap.configure_usage_reporter("", required=True)

    monkeypatch.setattr(bootstrap.settings, "NLP_AGENT_DATABASE_URL", "")
    with pytest.raises(bootstrap.UsageReporterConfigurationError, match="required"):
        bootstrap.configure_usage_reporter(None, required=True)


def test_optional_usage_reporter_returns_none_when_unconfigured(monkeypatch):
    reporter = bootstrap.configure_usage_reporter("", required=False)
    assert reporter is None

    monkeypatch.setattr(bootstrap.settings, "NLP_AGENT_DATABASE_URL", "")
    reporter_none = bootstrap.configure_usage_reporter(None, required=False)
    assert reporter_none is None


def test_shutdown_usage_reporter_handles_none_gracefully():
    # Calling shutdown on None should safely no-op without exceptions
    bootstrap.shutdown_usage_reporter(None)
