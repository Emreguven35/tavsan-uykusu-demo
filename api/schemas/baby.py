"""Baby (bebek profili) şemaları — mobil Supabase 'babies' alanlarıyla birebir."""
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from api.models.education_video import ASAMA_KODLARI

# Eğitim aşaması — TEK sözlük (models/education_video.py). Literal ile
# doğrulanır: geçersiz kod 422 döner, DB'ye çöp yazılmaz ve video eşlemesi
# sessizce "hiçbir videoyla eşleşmeyen aşama"ya düşmez.
AsamaKodu = Literal[ASAMA_KODLARI]


class BabyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    birth_date: date | None = None
    gender: str | None = None
    feeding_type: str | None = None
    crying_tolerance: str | None = None
    parent_experience: str | None = None
    sleep_environment: str | None = None
    sleep_method: str | None = None
    night_wakes: int | None = None
    night_feeds: int | None = None
    # v2.1 — KALICI PROFİL (eskiden yalnız profile_overrides içinde geliyordu).
    saglik_problemi: str | None = None
    dogum_haftasi: int | None = Field(default=None, ge=24, le=42)
    # Eğitim takibi (Faz 6.1R) — mobilin 14 günlük modülü set eder.
    training_started_at: date | None = None
    training_completed_at: date | None = None


class BabyUpdate(BaseModel):
    """Kısmi güncelleme (PATCH) — verilmeyen alanlar değişmez.

    Mobil 14 günlük eğitim modülü: modül başlarken training_started_at,
    bitince training_completed_at PATCH'lenir. Regresyon tespiti (İlayda
    protokolü) training_completed_at'e dayanır."""
    name: str | None = Field(default=None, min_length=1, max_length=120)
    birth_date: date | None = None
    gender: str | None = None
    feeding_type: str | None = None
    crying_tolerance: str | None = None
    parent_experience: str | None = None
    sleep_environment: str | None = None
    sleep_method: str | None = None
    night_wakes: int | None = None
    night_feeds: int | None = None
    # v2.1 — KALICI PROFİL (eskiden yalnız profile_overrides içinde geliyordu).
    saglik_problemi: str | None = None
    dogum_haftasi: int | None = Field(default=None, ge=24, le=42)
    training_started_at: date | None = None
    training_completed_at: date | None = None
    # Eğitim videolarının aşama rozeti. NULL'a çekilirse aşama yeniden PLANDAN
    # türetilir; dolu ise annenin beyanı hesabı ezer.
    mevcut_asama: AsamaKodu | None = None


class BabyResp(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    birth_date: date | None
    gender: str | None
    feeding_type: str | None
    crying_tolerance: str | None
    parent_experience: str | None
    sleep_environment: str | None
    sleep_method: str | None
    night_wakes: int | None
    night_feeds: int | None
    saglik_problemi: str | None
    dogum_haftasi: int | None
    training_started_at: date | None
    training_completed_at: date | None
    mevcut_asama: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
