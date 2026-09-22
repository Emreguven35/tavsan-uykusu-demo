"""
education_videos + video_progress — uygulama içi eğitim videoları.

KATALOG DOSYADA DEĞİL DB'DE: video listesi `tavsan-videolar/videolar.csv`'den
tohumlanır ama tek doğru kaynak bu tablodur. Böylece yeni video eklemek
BUILD GEREKTİRMEZ — `scripts/video_yukle.py` dosyayı volume'a koyup satırı
upsert eder, mobil bir sonraki `GET /education/videos`'ta görür.

`slug` doğal anahtardır: dosya adı, depo yolu ve URL onun üzerinden kurulur,
bu yüzden upsert slug bazlıdır ve slug DEĞİŞMEZ kabul edilir.
"""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (DateTime, ForeignKey, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import GUID, Base, JSONBType, TextArrayType

# Aşama kodları — babies.mevcut_asama ve stage_tags AYNI sözlüğü kullanır.
# Eğitim 13 günlük ve beş aşamalı: beşik yanı → oda ortası → kapı → eşik → bitiş.
ASAMA_KODLARI = ("genel", "besik_yani", "oda_ortasi", "kapi", "esik", "bitis",
                 "egitim_oncesi", "egitim_sonrasi")

ASAMA_ETIKETLERI = {
    "genel": "Genel",
    "besik_yani": "Beşik yanı",
    "oda_ortasi": "Oda ortası",
    "kapi": "Kapı",
    "esik": "Eşik",
    "bitis": "Bitiş",
    "egitim_oncesi": "Eğitim öncesi",
    "egitim_sonrasi": "Eğitim sonrası",
}

# Kategori kodu → mobilde görünen başlık. SIRA ÖNEMLİ: yanıt bu sırayla döner.
KATEGORILER = (
    ("baslarken", "Başlarken"),
    ("egitim_sirasinda", "Eğitim sırasında"),
    ("ozel_durumlar", "Özel durumlar"),
    ("egitim_sonrasi", "Eğitim sonrası"),
)


class EducationVideo(Base):
    __tablename__ = "education_videos"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True,
                                          default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True,
                                      index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    order_in_category: Mapped[int] = mapped_column(Integer, nullable=False,
                                                   default=0)
    duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Mutlak DEĞİL göreli yol (/media/videos/...): alan adı deploy'dan deploy'a
    # değişebilir, kayıtlı mutlak URL'ler bayatlar.
    video_url: Mapped[str] = mapped_column(String(400), nullable=False)
    poster_url: Mapped[str | None] = mapped_column(String(400), nullable=True)
    # PG'de text[], sqlite'ta JSON dizisi — ikisi de list[str].
    stage_tags: Mapped[list[str]] = mapped_column(TextArrayType, nullable=False,
                                                  default=list)
    # Bölüm işaretleri ({"baslik": ..., "saniye": ...}); şimdilik boş liste.
    chapters: Mapped[Any] = mapped_column(JSONBType, nullable=False,
                                          default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False)

    progress = relationship("VideoProgress", back_populates="video",
                            cascade="all, delete-orphan")


class VideoProgress(Base):
    """Kullanıcının bir videodaki konumu. UCUZ YAZIM: oynatıcı saniyede bir
    değil, duraklama/çıkış/aralıklı olarak gönderir; satır upsert edilir."""

    __tablename__ = "video_progress"
    __table_args__ = (UniqueConstraint("user_id", "video_id",
                                       name="uq_video_progress_user_video"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True,
                                          default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        index=True)
    video_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("education_videos.id", ondelete="CASCADE"),
        nullable=False, index=True)
    position_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Dolu ise video "izlendi" sayılır. Geri sarıp yeniden izlemek bu damgayı
    # SİLMEZ: "izledim" bilgisi kalıcıdır, konum ayrı alandır.
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False)

    video = relationship("EducationVideo", back_populates="progress")
