"""
chat_messages — sohbet geçmişi (her user/assistant mesaj çifti kaydedilir).

KVKK notu: içerik burada saklanır çünkü ürün özelliği (geçmiş) gerektirir; ancak
UYGULAMA LOGLARINA mesaj içeriği yazılmaz (yalnız uzunluk/süre). DELETE /auth/account
ile kullanıcının tüm mesajları cascade silinir.
"""
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    role: Mapped[str] = mapped_column(String(12), nullable=False)          # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped["object"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    user = relationship("User", back_populates="chat_messages")
