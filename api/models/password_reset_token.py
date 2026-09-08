"""
password_reset_tokens — parola sıfırlama akışı (Faz 2 basit sürüm).

E-posta gönderimi henüz YOK (Resend — Faz 6). /auth/reset-password-request bir sıfırlama
token'ı üretip HASH'ini bu tabloya yazar; /auth/reset-password ham token + yeni parolayı
alır, hash'i doğrular, parolayı değiştirir ve token'ı 'used' işaretler.

Faz 5R NOTU: ham token artık HTTP yanıtında DÖNMEZ (güvenlik). E-posta kanalı
bağlanana kadar akış uçtan uca tamamlanamaz — /auth/reset-password kodu hazır ve
test edilebilir, ama gerçek kullanıcıya token ulaştıran kanal Faz 6'da gelecek.

GÜVENLİK: refresh_tokens gibi, ham token saklanmaz — yalnız SHA-256 hash'i.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User")
