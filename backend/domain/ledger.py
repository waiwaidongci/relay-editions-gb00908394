"""Bi-temporal receipt ledger domain rules.

Two time axes are kept strictly separate:

* ``posted_at``  — system/entry time: when the batch entered the books. It
  controls visibility during an "as of" replay. Nothing is ever mutated; every
  receipt and every withdrawal is an append-only ledger entry.
* ``received_at`` — business time: when the venue says the receipt actually
  arrived. It may be back-dated relative to ``posted_at`` and is the only
  timestamp used for the on-time / late decision.

The module deliberately uses only the Python standard library so the rules can
be unit-tested without the web/database stack.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable, NamedTuple, Optional


STALE_HOURS = 48


class EntryKind(str, Enum):
    receipt = 'receipt'
    withdrawal = 'withdrawal'


class BatchStatus(str, Enum):
    accepted = 'accepted'
    rejected = 'rejected'


class RejectReason(str, Enum):
    same_instant_conflict = 'same_instant_conflict'
    empty_batch = 'empty_batch'


@dataclass(frozen=True)
class LedgerEntry:
    """One append-only fact for an edition.

    ``seq`` orders entries inside a batch. Entries sharing the same
    ``(posted_at, seq)`` are considered simultaneous.
    """

    edition_key: str
    kind: EntryKind
    seq: int
    posted_at: datetime
    received_at: Optional[datetime] = None
    source_ref: Optional[str] = None

    def __post_init__(self):
        if self.posted_at.tzinfo is None:
            raise ValueError('posted_at must carry a time zone')
        if self.kind is EntryKind.receipt:
            if self.received_at is None or self.source_ref is None:
                raise ValueError('a receipt entry needs received_at and source_ref')
            if self.received_at.tzinfo is None:
                raise ValueError('received_at must carry a time zone')
        elif self.received_at is not None or self.source_ref is not None:
            raise ValueError('a withdrawal entry cannot carry receipt payload')


@dataclass(frozen=True)
class LedgerBatch:
    batch_id: str
    posted_at: datetime
    entries: tuple[LedgerEntry, ...]
    status: BatchStatus = BatchStatus.accepted
    reject_reason: Optional[str] = None
    note: Optional[str] = None

    def __post_init__(self):
        if self.posted_at.tzinfo is None:
            raise ValueError('posted_at must carry a time zone')
        if self.status is BatchStatus.rejected and not self.reject_reason:
            raise ValueError('a rejected batch needs a reason')


@dataclass(frozen=True)
class EditionState:
    """What was known about one edition at a particular point in entry time."""

    edition_key: str
    kind: Optional[EntryKind] = None
    received_at: Optional[datetime] = None
    source_ref: Optional[str] = None
    posted_at: Optional[datetime] = None
    batch_id: Optional[str] = None

    @property
    def has_receipt(self) -> bool:
        return self.kind is EntryKind.receipt


class BatchConflict(NamedTuple):
    edition_key: str
    seq: int


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def find_same_instant_conflicts(entries: Iterable[LedgerEntry]) -> list[BatchConflict]:
    """Return simultaneous, same-edition entries whose payload disagrees.

    Two entries conflict only when they land at the exact same entry instant
    for the same edition AND describe a different fact. Two identical facts at
    the same instant are idempotent duplicates and allowed.
    """

    grouped: dict[tuple[str, int], list[LedgerEntry]] = {}
    for entry in entries:
        grouped.setdefault((entry.edition_key, entry.seq), []).append(entry)

    conflicts: list[BatchConflict] = []
    for (edition_key, seq), group in sorted(grouped.items()):
        if len(group) < 2:
            continue
        fingerprints = {
            (entry.kind.value,
             _utc(entry.received_at).isoformat() if entry.received_at else None,
             entry.source_ref)
            for entry in group
        }
        if len(fingerprints) > 1:
            conflicts.append(BatchConflict(edition_key, seq))
    return conflicts


def validate_batch(entries: Iterable[LedgerEntry]) -> Optional[RejectReason]:
    entries = tuple(entries)
    if not entries:
        return RejectReason.empty_batch
    if find_same_instant_conflicts(entries):
        return RejectReason.same_instant_conflict
    return None


def replay(batches: Iterable[LedgerBatch], as_of: datetime) -> dict[str, EditionState]:
    """Rebuild the knowledge base exactly as it stood at ``as_of``.

    Only accepted batches posted at or before ``as_of`` are visible. Within a
    batch the entry with the highest seq wins for an edition; across batches
    the later posted batch wins. Rejected batches are skipped wholesale, as if
    they had never been booked. A withdrawal leaves the edition without a
    receipt, which the planner treats as a missing report.
    """

    if as_of.tzinfo is None:
        raise ValueError('as_of must carry a time zone')

    visible = sorted(
        (batch for batch in batches
         if batch.status is BatchStatus.accepted and _utc(batch.posted_at) <= _utc(as_of)),
        key=lambda batch: (_utc(batch.posted_at), batch.batch_id),
    )
    states: dict[str, EditionState] = {}
    for batch in visible:
        for entry in sorted(batch.entries, key=lambda item: item.seq):
            states[entry.edition_key] = EditionState(
                edition_key=entry.edition_key,
                kind=entry.kind,
                received_at=entry.received_at,
                source_ref=entry.source_ref,
                posted_at=entry.posted_at,
                batch_id=batch.batch_id,
            )
    return states


def visible_receipt(state: EditionState | None, as_of: datetime) -> Optional[datetime]:
    """Receipt visible at ``as_of``.

    Visibility is governed by entry time; the returned value is the business
    ``received_at`` used for late/on-time classification. A withdrawal yields
    no receipt even if an older receipt event exists in the raw history.
    """

    if state is None or not state.has_receipt:
        return None
    if state.posted_at is not None and _utc(state.posted_at) > _utc(as_of):
        return None
    return state.received_at


def classify(received_at: Optional[datetime], due: datetime, as_of: datetime,
             grace_hours: int) -> str:
    """Late/on-time decision driven purely by the business ``received_at``.

    Mirrors :mod:`domain.availability` but operates on a ledger-derived
    receipt so back-dated补录 is judged by when the venue received it, not by
    when the batch was booked.
    """

    deadline = due + timedelta(hours=grace_hours)
    visible = received_at if received_at is not None and _utc(received_at) <= _utc(as_of) else None
    if _utc(as_of) >= _utc(deadline) or (visible is not None and _utc(visible) >= _utc(deadline)):
        return 'expired'
    if visible is None:
        return 'waiting_input'
    received = _utc(visible)
    if received < _utc(due) - timedelta(hours=STALE_HOURS):
        return 'stale_input'
    return 'on_time' if received <= _utc(due) else 'backfill'


@dataclass
class DeliveryMoment:
    """Test helper describing a delivery's lifecycle position."""

    delivery_id: str
    edition_key: str
    scheduled_at: datetime
    delivered_at: Optional[datetime] = None
    superseded_at: Optional[datetime] = None
    version: int = 1
    replaces: Optional[str] = None
    plan_snapshot: dict = field(default_factory=dict)

    @property
    def is_pending(self) -> bool:
        return self.delivered_at is None and self.superseded_at is None

    @property
    def is_delivered(self) -> bool:
        return self.delivered_at is not None


def apply_correction(
    edition_key: str,
    state: EditionState | None,
    deliveries: dict[str, list[DeliveryMoment]],
    *,
    now: datetime,
    eligible: bool,
) -> dict:
    """Apply a newly booked receipt/withdrawal to in-flight deliveries.

    Rules implemented:

    * A correction booked before delivery intercepts every still-queued
      morning report for the edition (``superseded``). When the new version is
      eligible, a replacement delivery is queued; a withdrawal or an
      ineligible receipt cancels without reissue.
    * A delivery already sent is immutable evidence: it keeps the original
      receipt and decision snapshot and is never duplicated. Re-running the
      release afterwards returns the same delivery id instead of resending.
    """

    if now.tzinfo is None:
        raise ValueError('now must carry a time zone')

    chain = deliveries.setdefault(edition_key, [])
    superseded: list[str] = []
    for moment in chain:
        if moment.is_pending:
            moment.superseded_at = now
            superseded.append(moment.delivery_id)

    reissued: Optional[str] = None
    # Delivered evidence blocks any new delivery: reruns must be idempotent.
    already_delivered = next((m for m in chain if m.is_delivered), None)
    if already_delivered is not None:
        return {'edition_key': edition_key, 'superseded': superseded,
                'reissued_delivery_id': None, 'active_delivery_id': already_delivered.delivery_id,
                'already_delivered': True}

    if state is not None and state.has_receipt and eligible:
        version = max((m.version for m in chain), default=0) + 1
        replacement = DeliveryMoment(
            delivery_id=f'del-{edition_key}-v{version}',
            edition_key=edition_key,
            scheduled_at=now,
            version=version,
            replaces=superseded[-1] if superseded else None,
            plan_snapshot={'received_at': state.received_at.isoformat()
                           if state.received_at else None,
                           'source_ref': state.source_ref},
        )
        chain.append(replacement)
        reissued = replacement.delivery_id

    active = next((m.delivery_id for m in reversed(chain) if m.is_pending), None)
    return {'edition_key': edition_key, 'superseded': superseded,
            'reissued_delivery_id': reissued, 'active_delivery_id': active,
            'already_delivered': False}
