"""
sleep_logs — uyku/uyanma/beslenme kayıtları.

Mobil, offline SQLite'ta üretip toplu (batch) senkronlar. İdempotency için her kayıt
mobilde bir client_id taşır; aynı (user_id, client_id) ikinci kez gelirse UPDATE edilir
(bkz. Faz 3 POST /logs/batch). Bu yüzden (user_id, client_id) üzerinde UNIQUE constraint.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import GUID, Base
from api.models._mixins import TimestampMixin, uuid_pk


class SleepLog(Base, TimestampMixin):
    __tablename__ = "sleep_logs"
    __table_args__ = (
        # Mobil senkron idempotency: aynı kullanıcının aynı client_id'si tekildir.
        UniqueConstraint("user_id", "client_id", name="uq_sleep_logs_user_client"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    baby_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("babies.id", ondelete="CASCADE"), index=True, nullable=False)

    # sleep | nap | wake | feed | night_wake
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Mobil SQLite kaydının kimliği (senkron idempotency anahtarı). NULL olabilir
    # (ör. backend'te doğrudan oluşturulan kayıt); NULL'lar unique kısıtta çakışmaz.
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user = relationship("User", back_populates="sleep_logs")
    baby = relationship("Baby")
