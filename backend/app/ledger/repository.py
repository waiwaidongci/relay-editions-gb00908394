from datetime import timezone
from uuid import uuid4

from sqlalchemy import select

from app.clock import now_utc
from app.database import Session
from app.delivery.models import Delivery
from app.errors import Problem
from app.ledger.contracts import describe_batch
from app.ledger.models import ReceiptBatch, ReceiptLedgerEntry
from app.ops.audit import audit
from domain.ledger import (
    BatchStatus,
    EntryKind,
    LedgerBatch,
    LedgerEntry,
    RejectReason,
    replay,
    validate_batch,
)


def _aware(value):
    return value.replace(tzinfo=timezone.utc)


def _to_domain_entries(rows, posted_at):
    entries = []
    for seq, row in enumerate(rows):
        entries.append(LedgerEntry(
            edition_key=row.edition_key,
            kind=EntryKind(row.kind),
            seq=seq,
            posted_at=_aware(posted_at),
            received_at=_aware(row.received_at) if row.received_at else None,
            source_ref=row.source_ref,
        ))
    return tuple(entries)


def _cross_batch_conflicts(db, entries, posted_at):
    """Same-instant conflicts against already-booked batches.

    A new batch may not assert a different fact for an edition at an entry
    instant that already holds a fact; identical facts are idempotent.
    """

    keys = {entry.edition_key for entry in entries}
    existing = db.scalars(select(ReceiptLedgerEntry).where(
        ReceiptLedgerEntry.posted_at == posted_at,
        ReceiptLedgerEntry.edition_key.in_(keys)))
    fingerprints = {
        (entry.edition_key,
         (entry.kind,
          entry.received_at.isoformat() if entry.received_at else None,
          entry.source_ref))
        for entry in existing
    }
    conflicts = []
    for entry in entries:
        fingerprint = (entry.edition_key,
                       (entry.kind.value,
                        entry.received_at.isoformat() if entry.received_at else None,
                        entry.source_ref))
        same_instant = next((item for item in fingerprints
                             if item[0] == entry.edition_key), None)
        if same_instant is not None and same_instant[1] != fingerprint[1]:
            conflicts.append(entry.edition_key)
    return sorted(set(conflicts))


def _intercept_pending_deliveries(db, edition_keys, actor):
    """Correction booked before delivery: stop every still-queued report.

    Already delivered reports are immutable evidence and are never touched.
    The replacement delivery is created when the new report is approved and
    the edition is released again.
    """

    if not edition_keys:
        return []
    channels = [f'edition:{key}' for key in edition_keys]
    pending = db.scalars(select(Delivery).where(
        Delivery.delivered_at.is_(None),
        Delivery.expired_at.is_(None),
        Delivery.superseded_at.is_(None),
        Delivery.channel.in_(channels)).with_for_update())
    intercepted = []
    for delivery in pending:
        delivery.superseded_at = now_utc()
        intercepted.append(delivery.id)
        audit(db, actor, 'delivery_superseded', f'delivery/{delivery.id}')
    return intercepted


def book_batch(payload, actor):
    """Book one intake batch atomically.

    Same-instant content conflicts reject the whole batch: the batch is
    archived as ``rejected`` and none of its entries reach the ledger.
    """

    posted_at = payload.posted_at or now_utc().replace(microsecond=0)
    entries = _to_domain_entries(payload.entries, posted_at)

    reason = validate_batch(entries)
    with Session.begin() as db:
        conflicts_detail = None
        if reason is None:
            cross = _cross_batch_conflicts(db, entries, posted_at)
            if cross:
                reason = RejectReason.same_instant_conflict
                conflicts_detail = {'conflicting_editions': cross}
        batch = ReceiptBatch(
            batch_id=uuid4().hex, posted_at=posted_at,
            status=(BatchStatus.rejected.value if reason else BatchStatus.accepted.value),
            reject_reason=reason.value if reason else None,
            detail=conflicts_detail, note=payload.note, created_by=actor)
        db.add(batch)
        db.flush()

        if reason is not None:
            audit(db, actor, 'receipt_batch_rejected', f'receipt_batch/{batch.id}')
            return batch, [], False

        rows = []
        for seq, entry in enumerate(entries):
            row = ReceiptLedgerEntry(
                batch_id=batch.id, seq=seq, edition_key=entry.edition_key,
                kind=entry.kind.value, posted_at=posted_at,
                received_at=entry.received_at.replace(tzinfo=None)
                if entry.received_at else None,
                source_ref=entry.source_ref)
            db.add(row)
            rows.append(row)
        intercepted = _intercept_pending_deliveries(
            db, {entry.edition_key for entry in entries}, actor)
        if intercepted:
            batch.detail = {'superseded_delivery_ids': intercepted}
        audit(db, actor, 'receipt_batch_accepted', f'receipt_batch/{batch.id}')
        db.flush()
        return batch, rows, True


def _load_batches(db):
    batches = db.scalars(select(ReceiptBatch).order_by(ReceiptBatch.id))
    result = []
    for batch in batches:
        entries = db.scalars(select(ReceiptLedgerEntry)
                             .where(ReceiptLedgerEntry.batch_id == batch.id)
                             .order_by(ReceiptLedgerEntry.seq))
        result.append(LedgerBatch(
            batch_id=batch.batch_id,
            posted_at=_aware(batch.posted_at),
            entries=tuple(LedgerEntry(
                edition_key=entry.edition_key,
                kind=EntryKind(entry.kind),
                seq=entry.seq,
                posted_at=_aware(entry.posted_at),
                received_at=_aware(entry.received_at) if entry.received_at else None,
                source_ref=entry.source_ref) for entry in entries),
            status=BatchStatus(batch.status),
            reject_reason=batch.reject_reason,
            note=batch.note))
    return result


def replay_states(db, as_of):
    """Rebuild the knowledge base visible at ``as_of`` (entry-time replay)."""
    return replay(_load_batches(db), as_of)


def list_batches(db, limit):
    batches = db.scalars(select(ReceiptBatch).order_by(ReceiptBatch.id.desc()).limit(limit))
    return [describe_batch(batch) for batch in batches]


def get_batch(db, batch_id: int):
    batch = db.get(ReceiptBatch, batch_id)
    if batch is None:
        raise Problem(404, 'receipt_batch_not_found', 'Receipt batch does not exist')
    entries = db.scalars(select(ReceiptLedgerEntry)
                         .where(ReceiptLedgerEntry.batch_id == batch.id)
                         .order_by(ReceiptLedgerEntry.seq))
    return describe_batch(batch, entries)


def edition_history(db, edition_key: str):
    entries = db.scalars(select(ReceiptLedgerEntry)
                         .where(ReceiptLedgerEntry.edition_key == edition_key)
                         .order_by(ReceiptLedgerEntry.posted_at, ReceiptLedgerEntry.seq))
    return [describe_entry(entry) for entry in entries]
