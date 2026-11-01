"""
egitim_degerlendirmeleri — 13. gün eğitim değerlendirmesi (2026-11-01).

Program gün sayısına ulaşınca anneye üç soru sorulur (gece kendi dönüyor mu,
yatır-çık yapılabiliyor mu, 30 dk üzeri ağlama oldu mu). Her cevap SATIR olarak
kalır; karar (mezun | devam | yeniden_basla) services/egitim_degerlendirme.py'deki
TEK kural tablosundan çıkar.

DENEME GEÇMİŞİ: "yeniden_basla" babies.training_started_at'i yarına çeker; eski
denemenin başlangıcı, o anki günü ve annenin seçtiği aşama bu satırda kalır
(`egitim_baslangic`, `egitim_gunu`, `mevcut_asama`). Uyku kayıtları ve planlar
zaten silinmez. Bir bebeğin kaçıncı denemede olduğu = 1 + "yeniden_basla" satırı.
"""
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base
from api.models._mixins import TimestampMixin, uuid_pk


class EgitimDegerlendirme(Base, TimestampMixin):
    __tablename__ = "egitim_degerlendirmeleri"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    baby_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("babies.id", ondelete="CASCADE"), index=True, nullable=False)
    gun: Mapped[date] = mapped_column(Date, nullable=False)         # Türkiye günü

    # Değerlendirilen deneme (anlık görüntü)
    deneme_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    egitim_baslangic: Mapped[date | None] = mapped_column(Date, nullable=True)
    egitim_gunu: Mapped[int | None] = mapped_column(Integer, nullable=True)   # gerçek gün
    mevcut_asama: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Annenin cevapları
    kendi_donuyor: Mapped[bool] = mapped_column(Boolean, nullable=False)
    yatir_cik: Mapped[bool] = mapped_column(Boolean, nullable=False)
    uzun_aglama: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Karar: mezun | devam | yeniden_basla
    sonuc: Mapped[str] = mapped_column(String(20), nullable=False)
    yeniden_degerlendirme_tarihi: Mapped[date | None] = mapped_column(Date, nullable=True)
    yeni_baslangic: Mapped[date | None] = mapped_column(Date, nullable=True)
