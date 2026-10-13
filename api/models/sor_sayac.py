"""sor_sayaclari — Sor günlük soru sayacı (premium abonelik, 2026-10-13).

(kullanıcı, Türkiye günü) başına TEK satır. Hak, model çağrısından ÖNCE koşullu
atomik artırmayla alınır (`sayi < limit` ise +1): aynı anda gelen iki istek aynı
hakkı alamaz — sayıyı chat_messages'tan saymak, satır model cevabından sonra
yazıldığı için yarışa açıktı. Model çağrısı hata verirse hak geri verilir.
Kapanış mesajları ("teşekkürler") sayılmaz; önbellekten dönen cevaplar sayılır.
"""
import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base
from api.models._mixins import TimestampMixin, uuid_pk


class SorSayac(Base, TimestampMixin):
    __tablename__ = "sor_sayaclari"
    __table_args__ = (UniqueConstraint("user_id", "gun", name="uq_sor_sayac_user_gun"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    gun: Mapped[date] = mapped_column(Date, nullable=False)          # Türkiye günü
    sayi: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
