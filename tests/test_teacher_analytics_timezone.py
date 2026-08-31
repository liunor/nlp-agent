from datetime import datetime, timedelta, timezone

import pytest

from gateway.analytics_time import localize_turn_time, utc_offset_for


def test_utc_offset_accepts_names_and_literals():
    assert utc_offset_for("Asia/Shanghai") == timedelta(hours=8)
    assert utc_offset_for("UTC") == timedelta(0)
    assert utc_offset_for("+08:00") == timedelta(hours=8)
    assert utc_offset_for("-05:30") == timedelta(hours=-5, minutes=-30)


def test_utc_offset_rejects_unknown_or_signless_values():
    with pytest.raises(ValueError):
        utc_offset_for("America/New_York")  # DST-observing, unsupported
    with pytest.raises(ValueError):
        utc_offset_for("12:34")  # missing sign


def test_localize_turn_time_treats_naive_as_utc_and_rolls_day():
    # 2026-08-10 23:30 UTC == 2026-08-11 07:30 Asia/Shanghai
    day, hour, weekday = localize_turn_time(datetime(2026, 8, 10, 23, 30), "Asia/Shanghai")
    assert day == "2026-08-11"
    assert hour == 7


def test_localize_turn_time_with_aware_utc_value():
    aware = datetime(2026, 8, 10, 16, 0, tzinfo=timezone.utc)
    day, hour, _ = localize_turn_time(aware, "Asia/Shanghai")
    assert day == "2026-08-11"
    assert hour == 0


def test_localize_turn_time_non_datetime_falls_back_to_string_slice():
    day, hour, weekday = localize_turn_time("2026-08-10", "Asia/Shanghai")
    assert day == "2026-08-10"
    assert hour is None
    assert weekday is None