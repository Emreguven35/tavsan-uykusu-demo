"""Eğitim videosu şemaları — GET /education/videos, POST .../progress."""
import uuid
from typing import Any

from pydantic import BaseModel, Field


class VideoProgressReq(BaseModel):
    """Oynatıcıdan gelen ilerleme. UCUZ ÇAĞRI: duraklama/çıkış/aralıklı gönderilir.

    completed verilmezse sunucu videonun sonuna gelinip gelinmediğine bakar —
    oynatıcılar son saniyelerde ilerleme göndermeyi kesebiliyor."""
    position_sec: int = Field(ge=0, le=60 * 60 * 12)
    completed: bool | None = None


class VideoProgressResp(BaseModel):
    video_id: uuid.UUID
    position_sec: int
    completed: bool


class AsamaResp(BaseModel):
    kod: str                     # genel | besik_yani | … | egitim_sonrasi
    etiket: str                  # mobilde görünen Türkçe ad
    kaynak: str                  # "plan" (günden türetildi) | "anne" (beyan)


class VideoResp(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    description: str | None = None
    duration_sec: int
    video_url: str               # GÖRELİ: /media/videos/{slug}.mp4
    poster_url: str | None = None
    stage_tags: list[str]
    chapters: list[Any] = []
    progress: dict[str, Any]     # {"position_sec": int, "completed": bool}
    # B4 — "Başlarken" dışı videolar premium. BETA_MODE'da hep False.
    locked: bool = False
    premium_required: bool = False


class KategoriResp(BaseModel):
    key: str
    title: str
    videos: list[VideoResp]


class EgitimVideolariResp(BaseModel):
    """Eğitim sekmesinin tamamı — tek çağrı.

    Aşama ve `todays_pick` yalnız öneridir. B4: premium olmayan kullanıcıda
    "Başlarken" dışı videolar `locked` gelir (BETA_MODE'da hiçbiri)."""
    categories: list[KategoriResp]
    todays_pick: uuid.UUID | None = None
    watched_count: int
    total_count: int
    total_minutes: int
    asama: AsamaResp
