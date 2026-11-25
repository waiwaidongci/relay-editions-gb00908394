from datetime import timedelta

from domain.availability import readiness
from domain.intake import EditionInput, EditionOptions, record_key
from domain.ledger import EditionState, classify, visible_receipt
from domain.timezone_rules import to_utc


def plan_edition(row: EditionInput, options: EditionOptions,
                 state: EditionState | None = None) -> dict:
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

    deadline = due + timedelta(hours=options.backfill_hours)
    if state is not None:
        # Bi-temporal path: the ledger replay decides what was known.
        receipt = visible_receipt(state, options.as_of)
        status = classify(receipt, due, options.as_of, options.backfill_hours)
        receipt_visible = receipt is not None
        received_at = receipt
        source_ref = state.source_ref if state.has_receipt else None
        ledger_meta = {'ledger_batch_id': state.batch_id,
                       'ledger_posted_at': state.posted_at.isoformat()
                       if state.posted_at else None,
                       'ledger_kind': state.kind.value if state.kind else None}
    else:
        # Snapshot path (legacy dataset rows carry their own receipt column).
        availability = readiness(row.received_at, due, options.as_of, options.backfill_hours)
        status = availability.status
        receipt_visible = row.received_at is not None and row.received_at <= options.as_of
        received_at = row.received_at
        source_ref = row.source_ref
        ledger_meta = {'ledger_batch_id': None, 'ledger_posted_at': None, 'ledger_kind': None}

    ready_at = (max(due, received_at.astimezone(due.tzinfo))
                if receipt_visible else None)
    return {**base, 'due_at': due.isoformat(), 'overlap': overlap, 'status': status,
            'expires_at': deadline.isoformat(),
            'ready_at': ready_at.isoformat() if ready_at else None,
            'received_at': received_at.isoformat() if receipt_visible else None,
            'source_ref': source_ref if receipt_visible else None,
            **ledger_meta}
