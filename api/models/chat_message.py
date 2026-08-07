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

    # --- Kapsama telemetrisi (Faz 6.4, genişletildi) ------------------------
    # Hangi fallback katmanında cevaplandı:
    #   k1  (doğrudan) | k2 (en yakın bilgi) | k3 (genel ilke + netleştirme)
    #   k3_5 (alan içi ama kayıt yok — ilkelerden cevap + netleştirme)
    #   k4  (GERÇEKTEN alan dışı — son çare)
    #   ruhsal_kriz (Faz E: deterministik destek kapısı)
    # Cache hit'te NULL (retrieval yapılmadı → kapsama analizine karışmasın).
    # Haftalık analiz: k3/k3_5/k4 satırları korpusta karşılıksız kalan soruları
    # verir → İlayda ile korpus güncelleme turlarının girdisi.
    #
    # UZUNLUK: Faz 6.4'te String(2) idi ('k1'..'k4'). Faz E 'ruhsal_kriz' (11
    # karakter) yazınca Postgres StringDataRightTruncation fırlatıyor ve KRİZ
    # ANINDAKİ ANNE 500 alıyordu. SQLite VARCHAR uzunluğunu ZORLAMADIĞI için
    # yerel testler bunu görmedi. 32'ye genişletildi (migration 0008).
    retrieval_layer: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True)
    # O sorgudaki en yüksek retrieval skoru (eşikten bağımsız ham değer).
    top_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped["object"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    user = relationship("User", back_populates="chat_messages")
