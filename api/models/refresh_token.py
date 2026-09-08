"""
refresh_tokens — refresh token'ların DB kaydı (logout / tüm cihazlardan çıkış için).

GÜVENLİK: Token'ın KENDİSİ saklanmaz; yalnız SHA-256 hash'i (token_hash) yazılır.
DB sızsa bile geçerli refresh token'lar ele geçmez. Refresh sırasında istemciden
gelen ham token hash'lenip bu tabloda aranır; bulunur + revoked=False + expires_at
gelecekteyse yeni access (ve rotasyonla yeni refresh) verilir.

Logout: ilgili satır revoked=True yapılır.
Tüm cihazlardan çıkış: kullanıcının tüm satırları revoked=True.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    # Ham token'ın SHA-256 hex digest'i (64 karakter). UNIQUE + index → hızlı arama.
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="refresh_tokens")
