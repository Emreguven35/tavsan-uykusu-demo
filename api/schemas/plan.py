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
    content: dict[str, Any]         # {markdown, bucket, yas, plan_secimi, uygun_mu, ...}
    created_at: datetime

    model_config = {"from_attributes": True}
