"""Sleep plan şemaları — /plans/generate (Claude + parameter_engine → JSONB)."""
import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class PlanGenerateReq(BaseModel):
    baby_id: uuid.UUID
    # Bebekte saklanmayan ama motorun kullanabileceği ek profil alanları (opsiyonel).
    dogum_haftasi: int | None = Field(default=None, ge=24, le=42)
    profile_overrides: dict[str, Any] | None = None


class PlanResp(BaseModel):
    id: uuid.UUID
    baby_id: uuid.UUID
    plan_date: date
    # {markdown, bucket, yas, plan_secimi, uygun_mu, schedule, adapted, ...}
    content: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class PlanAdaptResp(BaseModel):
    """POST /plans/adapt yanıtı (Faz 6.1) — kaydedilen plan + adaptasyon kararı.

    regenerate_required: yaş bandı ihlali nedeniyle plan TAM YENİDEN ÜRETİLDİ.
    regression_detected: İlayda protokolü — eğitim bitiminden ≥13 gün sonra son 3
      gecenin ≥2'sinde 20dk+ süren gece uyanması (kendine dalamama) görüldü.
    restart_program_suggested: kullanıcıya "Programı baştan başlatalım mı?" kartı
      gösterilir. OTOMATİK HİÇBİR ŞEY ÜRETİLMEZ — onay gelirse mobil
      POST /plans/generate çağırır ve training_started_at'i bugüne PATCH'ler."""
    plan: PlanResp
    adjusted: bool
    shift_minutes: int
    regenerate_required: bool
    regression_detected: bool
    restart_program_suggested: bool
    reasons: list[str]
