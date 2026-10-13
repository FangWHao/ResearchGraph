"""只读时间规范化；原始发生和入库字段不改写。"""

from datetime import UTC, datetime
from typing import Any


def instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def canonical_times(occurred: Any, recorded: Any) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for field, raw in (("occurred_at_utc", occurred), ("recorded_at_utc", recorded)):
        parsed = instant(raw)
        result[field] = parsed.isoformat(timespec="microseconds") if parsed else None
    return result
