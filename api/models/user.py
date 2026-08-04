"""users — kimlik. Supabase auth yerine kendi JWT auth'umuz (Faz 2)."""
import uuid
from typing import Any

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base, JSONBType
from api.models._mixins import TimestampMixin, uuid_pk

# Bildirim tercihleri varsayılanı (Faz 6.2) — ikisi de AÇIK.
DEFAULT_NOTIFICATION_PREFS: dict[str, bool] = {
    "plan_reminders": True,
    "daily_summary": True,
    "community_replies": True,        # Faz T: kendi konuna cevap gelince bildir
}


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Bildirim tercihleri (Faz 6.2). nullable=True + kodda varsayılana düşme →
    # migration mevcut satırları BOZMAZ (geriye uyumlu).
    notification_prefs: Mapped[dict[str, Any] | None] = mapped_column(
        JSONBType, nullable=True, default=lambda: dict(DEFAULT_NOTIFICATION_PREFS))

    # KVKK silme hakkı: kullanıcı silinince ilişkili tüm veriler cascade ile gider.
    babies = relationship("Baby", back_populates="user",
                          cascade="all, delete-orphan", passive_deletes=True)
    sleep_logs = relationship("SleepLog", back_populates="user",
                             cascade="all, delete-orphan", passive_deletes=True)
    sleep_plans = relationship("SleepPlan", back_populates="user",
                              cascade="all, delete-orphan", passive_deletes=True)
    subscriptions = relationship("Subscription", back_populates="user",
                                cascade="all, delete-orphan", passive_deletes=True)
    chat_messages = relationship("ChatMessage", back_populates="user",
                                cascade="all, delete-orphan", passive_deletes=True)
    voice_profiles = relationship("VoiceProfile", back_populates="user",
                                 cascade="all, delete-orphan", passive_deletes=True)
    refresh_tokens = relationship("RefreshToken", back_populates="user",
                                 cascade="all, delete-orphan", passive_deletes=True)
    push_tokens = relationship("PushToken", back_populates="user",
                              cascade="all, delete-orphan", passive_deletes=True)
    sent_notifications = relationship("SentNotification", back_populates="user",
                                     cascade="all, delete-orphan", passive_deletes=True)
