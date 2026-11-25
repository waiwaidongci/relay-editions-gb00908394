from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import now_utc
from app.database import Base


timestamp = DateTime().with_variant(DATETIME(fsp=6), 'mysql')


class ReceiptBatch(Base):
    """A booked intake batch.

    Rejected batches stay archived as evidence (their entries never reach the
    append-only ledger table), so "整批不入账" is preserved while the rejection
    itself remains auditable.
    """

    __tablename__ = 'receipt_batches'
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    posted_at: Mapped[datetime] = mapped_column(timestamp, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    reject_reason: Mapped[str | None] = mapped_column(String(60))
    detail: Mapped[dict | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(timestamp, default=now_utc)


class ReceiptLedgerEntry(Base):
    """Append-only bi-temporal ledger entry.

    ``posted_at`` is entry time (controls replay visibility); ``received_at``
    is business time (when the venue says the receipt arrived, possibly
    back-dated) and drives late/on-time classification.
    """

    __tablename__ = 'receipt_ledger_entries'
    __table_args__ = (UniqueConstraint('batch_id', 'edition_key', 'seq'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('receipt_batches.id'), index=True)
    seq: Mapped[int]
    edition_key: Mapped[str] = mapped_column(String(70), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    posted_at: Mapped[datetime] = mapped_column(timestamp, index=True)
    received_at: Mapped[datetime | None] = mapped_column(timestamp)
    source_ref: Mapped[str | None] = mapped_column(String(70))
