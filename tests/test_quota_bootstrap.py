from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
import pytest

from server.infrastructure.mysql.base import Base
from server.quota.models import (
    PolicyBindingModel,
    QuotaAdjustmentModel,
    QuotaAlertModel,
    QuotaBucketModel,
    QuotaConcurrencyLockModel,
    QuotaCreditOperationModel,
    QuotaCreditScopeLockModel,
    QuotaDailyRollupModel,
    QuotaGrantModel,
    QuotaLedgerEntryModel,
    QuotaPolicyModel,
    QuotaProviderBillingModel,
    QuotaReservationModel,
    QuotaRoleCreditOperationModel,
    QuotaUsageArchiveBatchModel,
)
from server.quota.service import QuotaService


def test_usage_reporter_bootstrap_configures_and_cleans_up(monkeypatch):
    from server.quota import bootstrap

    configured = []
    monkeypatch.setattr(
        bootstrap,
        "configure_global_model_usage_reporter",
        lambda reporter: configured.append(reporter),
    )
    engine = create_engine("sqlite:///:memory:")
    reporter = bootstrap.configure_usage_reporter(engine)

    assert reporter is not None
    assert configured == [reporter]

    bootstrap.shutdown_usage_reporter(reporter)
    assert configured == [reporter, None]


def test_required_usage_reporter_rejects_missing_database_configuration():
    from server.quota import bootstrap

    with pytest.raises(bootstrap.UsageReporterConfigurationError, match="required"):
        bootstrap.configure_usage_reporter("", required=True)


def test_quota_schema_verification_probes_counter_primary_key():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            QuotaPolicyModel.__table__,
            PolicyBindingModel.__table__,
            QuotaBucketModel.__table__,
            QuotaConcurrencyLockModel.__table__,
            QuotaReservationModel.__table__,
            QuotaLedgerEntryModel.__table__,
            QuotaGrantModel.__table__,
            QuotaAdjustmentModel.__table__,
            QuotaCreditOperationModel.__table__,
            QuotaRoleCreditOperationModel.__table__,
            QuotaCreditScopeLockModel.__table__,
            QuotaDailyRollupModel.__table__,
            QuotaProviderBillingModel.__table__,
            QuotaUsageArchiveBatchModel.__table__,
            QuotaAlertModel.__table__,
        ],
    )

    QuotaService(engine).verify_schema()

    with engine.connect() as connection:
        assert connection.execute(
            QuotaConcurrencyLockModel.__table__.select()
        ).first() is None


def test_quota_schema_verification_probes_daily_weekly_policy_columns():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            QuotaPolicyModel.__table__,
            PolicyBindingModel.__table__,
            QuotaBucketModel.__table__,
            QuotaConcurrencyLockModel.__table__,
            QuotaReservationModel.__table__,
            QuotaLedgerEntryModel.__table__,
            QuotaGrantModel.__table__,
            QuotaAdjustmentModel.__table__,
            QuotaCreditOperationModel.__table__,
            QuotaRoleCreditOperationModel.__table__,
            QuotaCreditScopeLockModel.__table__,
            QuotaDailyRollupModel.__table__,
            QuotaProviderBillingModel.__table__,
            QuotaUsageArchiveBatchModel.__table__,
            QuotaAlertModel.__table__,
        ],
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE nlp_quota_policies DROP COLUMN weekly_limit_micro"
        )

    with pytest.raises(OperationalError, match="weekly_limit_micro"):
        QuotaService(engine).verify_schema()
