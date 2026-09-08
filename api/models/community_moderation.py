"""
moderation_log — moderasyon eylem defteri (Faz T). Denetlenebilirlik için tutulur.
actor_id ondelete=SET NULL (aktör hesabı silinse de kayıt kalır); otomatik
katmanlarda (filter/haiku/report) actor_id NULL'dır.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class ModerationLog(Base):
    __tablename__ = "moderation_log"

    id: Mapped[uuid.UUID] = uuid_pk()
    target_type: Mapped[str] = mapped_column(String(8), nullable=False)    # thread | reply
    target_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    # hide | remove | restore | mute | ban
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    # filter | haiku | report | admin
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
