from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import now_utc
from app.database import Base


timestamp = DateTime().with_variant(DATETIME(fsp=6), 'mysql')


class EditionRelease(Base):
    """One release attempt per edition version.

    The latest active row identifies the current delivery. Superseded rows
    stay for evidence; a delivery already sent is never replaced, so reruns
    of an approved report return the same delivery id.
    """

    __tablename__ = 'edition_releases'
    id: Mapped[int] = mapped_column(primary_key=True)
    edition_key: Mapped[str] = mapped_column(String(70), index=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey('deliveries.id'), unique=True)
    version: Mapped[int]
    active: Mapped[bool] = mapped_column(default=True, index=True)
    replaces_delivery_id: Mapped[int | None] = mapped_column(ForeignKey('deliveries.id'))
    created_at: Mapped[datetime] = mapped_column(timestamp, default=now_utc)
