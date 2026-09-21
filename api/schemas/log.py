"""Sleep log şemaları — mobil SQLite sync-manager sözleşmesi (batch upsert)."""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class SleepLogIn(BaseModel):
    """Batch içindeki tek kayıt. client_id mobil SQLite satır kimliğidir (idempotency).
    NULL ise (mobil dışı kaynak) her zaman yeni kayıt olarak eklenir — muaf."""
    baby_id: uuid.UUID
    # nap_skipped (v2/K7): "bu uykuyu HİÇ yapmadı". Gün içi kayma motoru bunu
    # gördüğünde o uykuyu çizelgeden düşürür ve sonraki blokları öne çeker.
    # ended_at bu tipte anlamsızdır; started_at "ne zaman uyuması gerekiyordu"dur.
    #
    # `sekerleme` (v2.2.2/K19): eski istemcilerin (build 16/18) gönderdiği ad.
    # Motor açısından normal bir uykudur — GÜNDÜZ mü GECE mi olduğunu `type`
    # DEĞİL, başlangıç saati belirler (plan_adapter.uyku_tipi_belirle).
    # Şemada açıkça kabul edilmesi ŞART: aksi hâlde kayıt 422 ile tümüyle
    # DÜŞÜYOR ve annenin uykusu hiç kaydedilmemiş oluyor (prod duman testinde
    # yakalandı). Yeni istemci yalnız sleep/nap gönderiyor.
    type: str = Field(
        pattern="^(sleep|nap|sekerleme|wake|feed|night_wake|nap_skipped)$")
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
    # K19.2 — uyku kayıtlarının SINIFI. Mobil artık anneye uyku tipi sordurmuyor
    # ("Uyudu"/"Uyandı"); ekranda gösterilecek etiketi backend veriyor.
    # `type` alanı istemcinin GÖNDERDİĞİ ham değerdir ve sınıfı BELİRLEMEZ:
    # `nap` tipiyle gelen 21:00 kaydı gece uykusudur.
    # Uyku olmayan kayıtlarda (feed/wake/night_wake) ikisi de None.
    kategori: str | None = None            # gece_uykusu | gunduz_uykusu
    kategori_etiket: str | None = None     # "Gece uykusu" | "Gündüz uykusu"

    model_config = {"from_attributes": True}


class BatchResult(BaseModel):
    created: int
    updated: int
    skipped: int                    # sahibi olunmayan baby_id vb. nedeniyle atlanan
    logs: list[SleepLogResp]
    # v2/K8: bu senkron sonucunda BUGÜNÜN çizelgesi değişti mi. true ise mobil
    # plans/today sorgusunu invalidate etmelidir.
    plan_updated: bool = False
    # v2.2/K13.3: açık kalmış bir sayaç kaydı, bu batch'teki manuel kayıtla
    # otomatik kapatıldı mı. true ise mobil elindeki "sürüyor" durumunu
    # tazelemelidir — aksi hâlde ekranda hâlâ dönen bir sayaç görünür.
    timer_closed: bool = False


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
