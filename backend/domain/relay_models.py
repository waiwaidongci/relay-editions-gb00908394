from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EditionRelease(Base):
    __tablename__ = 'edition_releases'
    edition_key: Mapped[str] = mapped_column(String(70), primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey('deliveries.id'), unique=True)
