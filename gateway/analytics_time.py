"""Fixed-offset timezone bucketing for teacher analytics.

MySQL ``DATETIME`` columns are timezone-naive and every write path stamps UTC
(``UTC_TIMESTAMP`` or a UTC-annotated ``datetime``), so naive values are
treated as UTC and shifted by a configured fixed offset before hour/weekday
bucketing.  IANA names are supported only for regions without summer-time
transitions; a raw ``±HH:MM`` offset is also accepted.  This mirrors the
existing fixed-offset Beijing handling in ``server.web.feedback`` and avoids a
``tzdata`` dependency.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

# IANA names whose current offset is fixed (no summer-time transitions).  Kept
# small and explicit so an unsupported value fails loudly instead of silently
# collapsing a DST-observing zone to its standard offset.
_FIXED_TZ_OFFSETS: dict[str, timedelta] = {
    "UTC": timedelta(0),
    "Asia/Shanghai": timedelta(hours=8),
    "Asia/Hong_Kong": timedelta(hours=8),
    "Asia/Taipei": timedelta(hours=8),
    "Asia/Macau": timedelta(hours=8),
    "Asia/Singapore": timedelta(hours=8),
    "Asia/Tokyo": timedelta(hours=9),
    "Asia/Seoul": timedelta(hours=9),
}

_OFFSET_RE = re.compile(r"^([+-])(\d{1,2})(?::?(\d{2}))?$")


def utc_offset_for(timezone_name: str) -> timedelta:
    """Resolve an analytics timezone to a fixed UTC offset.

    Accepts a name from :data:`_FIXED_TZ_OFFSETS` or a literal ``±HH[:MM]``
    (e.g. ``"+08:00"``).  Raises :class:`ValueError` for unknown or
    DST-observing names rather than guessing.
    """
    name = (timezone_name or "UTC").strip()
    if name in _FIXED_TZ_OFFSETS:
        return _FIXED_TZ_OFFSETS[name]
    match = _OFFSET_RE.match(name)
    if match:
        sign = 1 if match.group(1) == "+" else -1
        hours = int(match.group(2))
        minutes = int(match.group(3) or 0)
        if hours > 14 or minutes > 59:
            raise ValueError(f"无效的统计时区偏移：{timezone_name}")
        return timedelta(hours=sign * hours, minutes=sign * minutes)
    raise ValueError(f"不支持的统计时区：{timezone_name}")


def localize_turn_time(value: Any, timezone_name: str = "UTC") -> tuple[str, int | None, int | None]:
    """Return ``(day, hour, weekday)`` for a turn timestamp in the display timezone.

    Naive datetimes are treated as UTC.  Non-datetime values fall back to the
    read model's original string slice with no hour/weekday.
    """
    if not hasattr(value, "strftime"):
        return str(value)[:10], None, None
    offset = utc_offset_for(timezone_name)
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    local = aware.astimezone(timezone(offset))
    return local.strftime("%Y-%m-%d"), local.hour, local.weekday()