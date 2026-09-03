from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from server.quota.models import (
    PolicyBindingModel,
    PricingRuleModel,
    QuotaAdjustmentModel,
    QuotaBucketModel,
    QuotaConcurrencyLockModel,
    QuotaGrantModel,
    QuotaLedgerEntryModel,
    QuotaPolicyModel,
    QuotaReservationModel,
    UsageEventModel,
)


QUOTA_MODELS = (
    PricingRuleModel,
    UsageEventModel,
    QuotaPolicyModel,
    PolicyBindingModel,
    QuotaBucketModel,
    QuotaConcurrencyLockModel,
    QuotaReservationModel,
    QuotaLedgerEntryModel,
    QuotaGrantModel,
    QuotaAdjustmentModel,
)


@pytest.fixture
def quota_engine():
    """Provides an isolated, in-memory SQLite engine with all 10 quota tables created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in QUOTA_MODELS:
        model.__table__.create(engine, checkfirst=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def quota_session(quota_engine):
    """Provides a transactional database session rolled back after each test."""
    session_factory = sessionmaker(bind=quota_engine, expire_on_commit=False)
    session: Session = session_factory()
    try:
        yield session
    finally:
        session.close()
