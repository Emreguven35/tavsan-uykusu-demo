"""push_tokens — cihaz başına Expo push token'ı (Faz 6.2).

Bir kullanıcının birden çok cihazı olabilir; her cihaz kendi expo_token'ıyla kayıtlı.
expo_token GLOBAL unique'tir: aynı cihaz başka bir hesaba giriş yaparsa token o
kullanıcıya taşınır (upsert), böylece bildirim yanlış kişiye gitmez.

Expo "DeviceNotRegistered" hatası dönerse token silinir (bkz. services/notifier.py).
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class PushToken(Base):
    __tablename__ = "push_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    # ExponentPushToken[xxxxxxxx] biçimi — cihazı benzersiz tanımlar.
    expo_token: Mapped[str] = mapped_column(String(255), unique=True, index=True,
                                            nullable=False)
    platform: Mapped[str | None] = mapped_column(String(20), nullable=True)   # ios|android
    device_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Her register-token çağrısında tazelenir — ölü cihazları ayıklamak için.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="push_tokens")
