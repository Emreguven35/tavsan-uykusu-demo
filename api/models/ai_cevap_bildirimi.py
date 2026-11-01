"""
ai_cevap_bildirimleri — annenin Sor cevabını bildirmesi (2026-11-01).

Play "AI ile üretilen içerik" politikası: kullanıcı yapay zekâ cevabını uygulama
içinden bildirebilmeli. (user_id, mesaj_id) tekildir — aynı mesajı tekrar
bildirmek satırı GÜNCELLER. Soru ve cevap metni bildirim anındaki KOPYADIR
(inceleme mesaj sonradan değişse/silinse de anlamlı kalsın). E-posta tutulmaz.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk

SEBEPLER = ("yanlis_bilgi", "programla_uyusmuyor", "guvenli_degil", "uygunsuz", "diger")
DURUMLAR = ("yeni", "incelendi")


class AiCevapBildirimi(Base):
    __tablename__ = "ai_cevap_bildirimleri"
    __table_args__ = (UniqueConstraint("user_id", "mesaj_id",
                                       name="uq_ai_cevap_bildirim_user_mesaj"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    baby_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("babies.id", ondelete="SET NULL"), nullable=True)
    mesaj_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False)
    sebep: Mapped[str] = mapped_column(String(32), nullable=False)
    # Sütun adı "not" (sözleşme); Python'da anahtar kelime olduğu için not_.
    not_: Mapped[str | None] = mapped_column("not", Text, nullable=True)
    soru_metni: Mapped[str | None] = mapped_column(Text, nullable=True)
    cevap_metni: Mapped[str] = mapped_column(Text, nullable=False)
    durum: Mapped[str] = mapped_column(String(16), nullable=False, default="yeni")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
