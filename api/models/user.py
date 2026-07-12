"""users — kimlik. Supabase auth yerine kendi JWT auth'umuz (Faz 2)."""
import uuid

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base
from api.models._mixins import TimestampMixin, uuid_pk


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

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
