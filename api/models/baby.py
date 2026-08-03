"""babies — bebek profili (mobil Supabase 'babies' tablosuyla birebir alanlar)."""
import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import GUID, Base
from api.models._mixins import TimestampMixin, uuid_pk


class Baby(Base, TimestampMixin):
    __tablename__ = "babies"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    feeding_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    crying_tolerance: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parent_experience: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sleep_environment: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sleep_method: Mapped[str | None] = mapped_column(String(80), nullable=True)
    night_wakes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    night_feeds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Eğitim takibi (Faz 6.1R, İlayda protokolü) --------------------------
    # Mobildeki 14 günlük eğitim modülü bu tarihleri set eder. Regresyon tespiti
    # training_completed_at üzerinden çalışır (bkz. services/plan_adapter.py).
    training_started_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    training_completed_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    user = relationship("User", back_populates="babies")
