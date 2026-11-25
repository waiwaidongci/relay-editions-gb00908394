from domain.availability import readiness
from domain.intake import EditionInput, EditionOptions, record_key
from domain.timezone_rules import to_utc


def plan_edition(row: EditionInput, options: EditionOptions) -> dict:
    plan = {'edition_key': record_key(row), 'venue': row.venue, 'edition': row.edition,
            'performance_day': row.performance_day.isoformat(), 'zone': row.zone,
            'local_due': row.local_due.isoformat(timespec='minutes'), 'overlap_policy': row.overlap_policy,
            'recipient': row.recipient}
    base = {**plan, 'due_at': None, 'ready_at': None, 'expires_at': None, 'overlap': False,
            'received_at': None, 'source_ref': None}
    try:
        due, overlap = to_utc(row.performance_day, row.local_due, row.zone, row.overlap_policy)
    except ValueError:
        return {**base, 'status': 'invalid_clock'}
    availability = readiness(row.received_at, due, options.as_of, options.backfill_hours)
    receipt_visible = row.received_at is not None and row.received_at <= options.as_of
    return {**base, 'due_at': due.isoformat(), 'overlap': overlap, 'status': availability.status,
            'expires_at': availability.deadline.isoformat(),
            'ready_at': availability.ready_at.isoformat() if availability.ready_at else None,
            'received_at': row.received_at.isoformat() if receipt_visible else None,
            'source_ref': row.source_ref if receipt_visible else None}
