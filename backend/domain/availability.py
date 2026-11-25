from datetime import datetime, timedelta, timezone
from typing import NamedTuple


class Availability(NamedTuple):
    status: str
    ready_at: datetime | None
    deadline: datetime


def readiness(received_at, due, as_of, grace_hours) -> Availability:
    deadline = due + timedelta(hours=grace_hours)
    visible_receipt = received_at if received_at is not None and received_at <= as_of else None
    if as_of >= deadline or (visible_receipt is not None and visible_receipt >= deadline):
        return Availability('expired', None, deadline)
    if visible_receipt is None:
        return Availability('waiting_input', None, deadline)
    received = visible_receipt.astimezone(timezone.utc)
    if received < due - timedelta(hours=48):
        return Availability('stale_input', None, deadline)
    return Availability('on_time' if received <= due else 'backfill', max(due, received), deadline)
