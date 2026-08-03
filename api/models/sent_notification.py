"""sent_notifications — bildirim idempotency defteri (Faz 6.2).

Zamanlayıcı her 15 dakikada koşar ve aynı uyku bloğu birden çok koşuda pencereye
girebilir. Aynı (user, plan, block_key) için İKİNCİ kez gönderim yapılmaz —
mükerrer bildirim ebeveyni rahatsız eder ve güveni bozar.

block_key: plan çizelgesindeki blok kimliği + o günün tarihi (örn. "2026-08-03:nap_1")
— aynı blok ertesi gün yeniden bildirilebilsin diye tarih dahildir.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class SentNotification(Base):
    __tablename__ = "sent_notifications"
    __table_args__ = (
        # İdempotency'nin ASIL garantisi burada — yarış durumunda DB reddeder.
        UniqueConstraint("user_id", "plan_id", "block_key",
                         name="uq_sent_notifications_user_plan_block"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("sleep_plans.id", ondelete="CASCADE"), index=True,
        nullable=False)

    block_key: Mapped[str] = mapped_column(String(80), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="sent_notifications")
