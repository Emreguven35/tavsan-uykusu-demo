"""Sleep log şemaları — mobil SQLite sync-manager sözleşmesi (batch upsert)."""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class SleepLogIn(BaseModel):
    """Batch içindeki tek kayıt. client_id mobil SQLite satır kimliğidir (idempotency).
    NULL ise (mobil dışı kaynak) her zaman yeni kayıt olarak eklenir — muaf."""
    baby_id: uuid.UUID
    type: str = Field(pattern="^(sleep|nap|wake|feed|night_wake)$")
    started_at: datetime
    ended_at: datetime | None = None
    notes: str | None = None
    client_id: str | None = Field(default=None, max_length=64)


class BatchReq(BaseModel):
    logs: list[SleepLogIn] = Field(min_length=1, max_length=1000)


class SleepLogResp(BaseModel):
    id: uuid.UUID
    baby_id: uuid.UUID
    type: str
    started_at: datetime
    ended_at: datetime | None
    notes: str | None
    client_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class BatchResult(BaseModel):
    created: int
    updated: int
    skipped: int                    # sahibi olunmayan baby_id vb. nedeniyle atlanan
    logs: list[SleepLogResp]


class DaySummary(BaseModel):
    date: date
    sleep_hours: float
    naps: int
    night_wakes: int
    night_feeds: int


class WeeklySummaryResp(BaseModel):
    baby_id: uuid.UUID
    from_date: date
    to_date: date
    total_sleep_hours: float
    total_night_wakes: int
    total_night_feeds: int
    days: list[DaySummary]
