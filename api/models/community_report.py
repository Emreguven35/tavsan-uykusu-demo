"""
reports — içerik şikayeti (Faz T). Şikayetçi kendi şikayetini tekrarlayamaz (unique).
reporter_id ondelete=SET NULL: hesap silinse de şikayet kaydı (moderasyon izi) kalır.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("reporter_id", "target_type", "target_id",
                         name="uq_reports_reporter_target"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    target_type: Mapped[str] = mapped_column(String(8), nullable=False)    # thread | reply
    target_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    reporter_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True)
    # spam | hakaret | tibbi_risk | reklam | uygunsuz | diger
    reason: Mapped[str] = mapped_column(String(12), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
