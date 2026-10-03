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
    """Bildirim tercihleri. Üçü de varsayılan olarak AÇIK."""
    plan_reminders: bool = True      # "uyku vakti yaklaşıyor" hatırlatmaları
    daily_summary: bool = True       # günlük özet
    community_replies: bool = True   # Faz T: kendi konuna cevap gelince
    # Topluluk v2 — cevap / uzman / cevabına yanıt bildirimleri.
    topluluk_cevap_bildirimi: bool = True
    # Topluluk v2 — "Sorunuz N anneye faydalı geldi" (3 saatte bir, toplu).
    topluluk_faydali_bildirimi: bool = True
    # v2.7 — sabah uyanış sorusu + uyku dizisi (30 dk önce / zamanında /
    # 30 dk sonra). plan_reminders ile eşlenir.
    uyku_hatirlatma_bildirimi: bool = True


class NotificationPrefsUpdate(BaseModel):
    """Kısmi güncelleme — verilmeyen alan değişmez."""
    plan_reminders: bool | None = None
    daily_summary: bool | None = None
    community_replies: bool | None = None
    topluluk_cevap_bildirimi: bool | None = None
    topluluk_faydali_bildirimi: bool | None = None
    uyku_hatirlatma_bildirimi: bool | None = None
