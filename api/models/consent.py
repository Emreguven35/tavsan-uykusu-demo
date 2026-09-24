"""
consents — KVKK onay kayıtları (B5).

SALT EKLEME (append-only): her onay/geri çekme YENİ satırdır; eski satır
değiştirilmez. "Kullanıcı hangi metnin hangi sürümüne, ne zaman, evet mi
hayır mı dedi" sorusu geriye dönük cevaplanabilmeli (ispat yükü veri
sorumlusundadır). Güncel durum = her tür için EN SON satır.

IP adresi AÇIK SAKLANMAZ: `ip_hash` = HMAC-SHA256(JWT_SECRET'tan türetilmiş
anahtar, ip). Aynı IP'den geldiği gösterilebilir, IP'nin kendisi geri
hesaplanamaz.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class Consent(Base):
    __tablename__ = "consents"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    # aydinlatma | acik_riza_saglik | pazarlama  (config.KVKK_METIN_SURUMLERI)
    tur: Mapped[str] = mapped_column(String(30), nullable=False)
    metin_surumu: Mapped[str] = mapped_column(String(40), nullable=False)
    onay: Mapped[bool] = mapped_column(Boolean, nullable=False)
    zaman: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                            index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Nereden geldi: "kayit" (POST /auth/register) | "uygulama" (POST /consents)
    kaynak: Mapped[str] = mapped_column(String(20), nullable=False, default="uygulama")
