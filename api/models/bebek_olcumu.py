"""
bebek_olcumleri — bebeğin boy/kilo ölçüm geçmişi (ilk açılış akışı, 2026-10).

Ölçüm GEÇMİŞTİR: her ölçüm yeni satır, eski satır değişmez. Bebeğin "güncel"
boy/kilosu en son `olcum_tarihi`ndeki satırdır (eşitlikte en son yazılan).

BİLİNÇLİ SINIR: bu değerler HİÇBİR HESABA GİRMEZ — plan motoru, eğitim planı,
sohbet bağlamı ve denetim okumaz (tests/test_bebek_olcum.py sabitliyor). Motorda
bekleyen `kilo_durumu` girdisine (parameter_engine.gece_beslenme_planla) de
bağlanmaz; bağlanması bir yöntem kararıdır.

Bebek silinince (hesap silme dahil) ölçümleri de silinir: FK ON DELETE CASCADE.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (CheckConstraint, Date, DateTime, ForeignKey, Index,
                        Numeric, String, func)
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk

# Hassasiyet ŞEMADA sabit: boy 1 ondalık (≤ 999,9), kilo 2 ondalık (≤ 99,99).
# SQLite bunları zorlamaz; Postgres zorlar — testler kolon tanımını doğrular.
BOY_HASSASIYET = (4, 1)
KILO_HASSASIYET = (4, 2)

KAYNAKLAR = ("onboarding", "profil")


class BebekOlcumu(Base):
    __tablename__ = "bebek_olcumleri"
    __table_args__ = (
        CheckConstraint("boy_cm IS NOT NULL OR kilo_kg IS NOT NULL",
                        name="ck_bebek_olcumleri_dolu"),
        Index("ix_bebek_olcumleri_baby_tarih", "baby_id", "olcum_tarihi"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    baby_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("babies.id", ondelete="CASCADE"), index=True, nullable=False)
    olcum_tarihi: Mapped[date] = mapped_column(Date, nullable=False)
    boy_cm: Mapped[float | None] = mapped_column(
        Numeric(*BOY_HASSASIYET, asdecimal=False), nullable=True)
    kilo_kg: Mapped[float | None] = mapped_column(
        Numeric(*KILO_HASSASIYET, asdecimal=False), nullable=True)
    # onboarding | profil
    kaynak: Mapped[str] = mapped_column(String(12), nullable=False)
    # Uygulama tarafında mikrosaniyeli yazılır: "son ölçüm" aynı gündeki
    # ölçümleri buna göre sıralıyor (SQLite'ın CURRENT_TIMESTAMP'i saniyelik).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        server_default=func.now(), nullable=False)
