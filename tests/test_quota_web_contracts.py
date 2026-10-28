from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from server.web.contracts import QuotaCreditOperationBody


@pytest.mark.parametrize("bucket, hour, weekday, valid", [
    ("daily", 0, 0, True), ("daily", 8, 0, False),
    ("weekly", 0, 0, True), ("weekly", 0, 1, False),
])
def test_managed_credits_match_the_utc_admission_period(bucket, hour, weekday, valid):
    start = datetime(2026, 10, 26, hour, tzinfo=timezone.utc) + timedelta(days=weekday)
    values = dict(owner_type="user", owner_id="u", bucket_type=bucket,
                  period_start=start, period_end=start + timedelta(days=7 if bucket == "weekly" else 1),
                  amount_micro=1_000_000, reason="gift", idempotency_key="k", effective_from=start)
    if valid:
        assert QuotaCreditOperationBody(**values).period_start == start
    else:
        with pytest.raises(ValidationError, match="UTC"):
            QuotaCreditOperationBody(**values)
