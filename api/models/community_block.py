"""
blocks — kullanıcı engelleme. Engellenen kullanıcının içeriği listelerde gizlenir.
Her iki taraf da ondelete=CASCADE (hesap silinince engel kaydı anlamsız → gider).
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class Block(Base):
    __tablename__ = "blocks"
    __table_args__ = (
        UniqueConstraint("user_id", "blocked_user_id", name="uq_blocks_pair"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(          # engelleyen
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    blocked_user_id: Mapped[uuid.UUID] = mapped_column(  # engellenen
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
