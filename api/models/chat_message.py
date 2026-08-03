"""
chat_messages — sohbet geçmişi (her user/assistant mesaj çifti kaydedilir).

KVKK notu: içerik burada saklanır çünkü ürün özelliği (geçmiş) gerektirir; ancak
UYGULAMA LOGLARINA mesaj içeriği yazılmaz (yalnız uzunluk/süre). DELETE /auth/account
ile kullanıcının tüm mesajları cascade silinir.
"""
import uuid

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, func
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

    # --- Kapsama telemetrisi (Faz 6.4) --------------------------------------
    # Hangi fallback katmanında cevaplandı: k1 (doğrudan) | k2 (en yakın bilgi) |
    # k3 (genel ilke + netleştirme sorusu) | k4 (kapsam dışı). Cache hit'te NULL
    # (retrieval yapılmadı → kapsama analizine karışmasın).
    # Haftalık analiz: k3/k4 satırları korpusta karşılıksız kalan soruları verir
    # → İlayda ile korpus güncelleme turlarının girdisi.
    retrieval_layer: Mapped[str | None] = mapped_column(
        String(2), nullable=True, index=True)
    # O sorgudaki en yüksek retrieval skoru (eşikten bağımsız ham değer).
    top_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped["object"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    user = relationship("User", back_populates="chat_messages")
