"""Bildirim şemaları — /api/v1/notifications/* (Faz 6.2)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class RegisterTokenReq(BaseModel):
    """Expo push token kaydı. Mobil her açılışta çağırır (upsert + last_seen tazeleme)."""
    expo_token: str = Field(min_length=1, max_length=255)
    platform: str | None = Field(default=None, pattern="^(ios|android)$")
    device_name: str | None = Field(default=None, max_length=120)


class PushTokenResp(BaseModel):
    id: uuid.UUID
    expo_token: str
    platform: str | None
    device_name: str | None
    created_at: datetime
    last_seen_at: datetime

    model_config = {"from_attributes": True}


class NotificationPrefs(BaseModel):
    """Bildirim tercihleri. İkisi de varsayılan olarak AÇIK."""
    plan_reminders: bool = True      # "uyku vakti yaklaşıyor" hatırlatmaları
    daily_summary: bool = True       # günlük özet


class NotificationPrefsUpdate(BaseModel):
    """Kısmi güncelleme — verilmeyen alan değişmez."""
    plan_reminders: bool | None = None
    daily_summary: bool | None = None
