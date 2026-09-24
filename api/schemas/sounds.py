"""Uyku sesleri şemaları — GET /sounds."""
import uuid

from pydantic import BaseModel


class SesResp(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    category: str
    duration_sec: int
    audio_url: str               # GÖRELİ: /media/sounds/{slug}.m4a
    bytes: int
    loop: bool = True            # dosya seamless loop; mobil sonsuz döndürür
    is_free: bool
    # Bu kullanıcı için kilitli mi (is_free değil VE premium değil). BETA_MODE'da
    # herkes premium sayıldığı için hep false.
    locked: bool


class SesKategoriResp(BaseModel):
    key: str
    title: str
    sounds: list[SesResp]


class UykuSesleriResp(BaseModel):
    categories: list[SesKategoriResp]
    is_premium: bool
