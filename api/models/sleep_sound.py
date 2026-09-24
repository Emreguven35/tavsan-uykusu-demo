"""
sleep_sounds — uyku sesleri kataloğu (beyaz gürültü, doğa, ev sesleri…).

Eğitim videolarıyla AYNI desen: katalog DB'de, dosya volume'da. Yeni ses
eklemek build gerektirmez — `scripts/ses_yukle.py` dosyayı `sounds/{slug}.m4a`
yoluna koyar ve satırı slug bazlı upsert eder. Satır YALNIZ dosya yüklendikten
sonra yazılır: katalogda olup çalmayan ses olmaz.

Dosyalar 10 dk'lık, başı-sonu kusursuz birleşen (seamless loop) mono AAC'dir;
mobil sonsuz döngüde çalar (`loop: true`).
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base

# Kategori kodu → mobilde görünen başlık. SIRA ÖNEMLİ: yanıt bu sırayla döner.
SES_KATEGORILERI = (
    ("gurultu", "Beyaz gürültü"),
    ("doga", "Doğa"),
    ("ev", "Ev sesleri"),
    ("rahatlatici", "Rahatlatıcı"),
    ("karisik", "Karışık"),
)


class SleepSound(Base):
    __tablename__ = "sleep_sounds"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True,
                                          default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True,
                                      index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    order_in_category: Mapped[int] = mapped_column(Integer, nullable=False,
                                                   default=0)
    duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Dosya boyutu (bayt) — mobil indirmeden önce "x MB" gösterebilsin.
    bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # GÖRELİ yol (/media/sounds/{slug}.m4a) — bkz. education_videos.video_url.
    audio_url: Mapped[str] = mapped_column(String(400), nullable=False)
    is_free: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False)
