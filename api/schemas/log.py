"""Sleep log şemaları — mobil SQLite sync-manager sözleşmesi (batch upsert)."""
import uuid
from datetime import date, datetime
from typing import Any

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


class SleepLogPatch(BaseModel):
    """PATCH /logs/{id} gövdesi — yalnız GÖNDERİLEN alanlar değişir.

    `ended_at: null` AÇIKÇA gönderilirse kayıt açık hâle gelir (sürüyor);
    alan hiç gönderilmezse bitiş olduğu gibi kalır. Ayrım `model_fields_set`
    ile yapılır. Tip ve bebek değiştirilemez: o bir düzeltme değil, başka
    bir kayıttır (sil + yeni kayıt)."""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    notes: str | None = None

    model_config = {"extra": "forbid"}


class BatchReq(BaseModel):
    """Batch gövdesi — kalemler HAM alınır, TEK TEK doğrulanır.

    `list[SleepLogIn]` DEĞİL: Pydantic tek bozuk kalemde bütün isteği 422 ile
    düşürüyordu. BATCH_SIZE 200 olduğu için tek bozuk kayıt 199 sağlam kaydı
    birlikte gömüyor, mobil tarafta sessiz veri kaybına yol açıyordu (mobil
    bunu ikili bölmeyle telafi etmeye çalışıyor — artık gerek kalmıyor).

    422 YALNIZ gövdenin kendisi bozuksa döner: `logs` yok, liste değil ya da
    boş. Kalemlerin geçerliliği `batch_upsert` içinde kayıt bazında ölçülür."""
    logs: list[Any] = Field(min_length=1, max_length=1000)


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


class SyncedEntry(BaseModel):
    """Sunucuya YAZILAN kayıt: mobilin yerel satırı eşleyebilmesi için."""
    client_id: str | None = None
    id: uuid.UUID


class SkippedEntry(BaseModel):
    """Yazılmayan ya da kopya olduğu için yeniden yazılmayan kayıt.

    `reason` MAKİNE tarafı (mobil buna göre karar verir), `detail` kullanıcıya
    gösterilebilecek Türkçe cümledir.

    reason değerleri: duplicate | invalid_type | invalid_time | invalid_baby |
    not_owned | missing_field | invalid | db_error
    "duplicate" mobilde BAŞARILI sayılır (kayıt zaten sunucuda)."""
    client_id: str | None = None
    id: uuid.UUID | None = None
    reason: str
    detail: str


class BatchResult(BaseModel):
    created: int
    updated: int
    # v2.3.1 — SAYI DEĞİL LİSTE. Mobil (sync-batch.ts) iki şekli de kabul
    # ediyor; sayı gelince hangi kaydın elendiğini bilemediği için hiçbirini
    # suçlayamıyor ve hepsini "gitti" sayıyordu. Liste ile eleme kayıt bazında
    # `rejected`a yazılabiliyor.
    skipped: list[SkippedEntry] = []
    # Yazılan kayıtların (client_id → sunucu id) eşlemesi. `logs` alanı eski
    # istemciler için AYNEN duruyor; bu alan aynı bilginin sade hâli.
    synced: list[SyncedEntry] = []
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
