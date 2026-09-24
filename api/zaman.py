"""
Türkiye saati — TÜM "gün" hesaplarının tek kaynağı (B6).

Kullanıcıların hepsi Türkiye'de; bir annenin "bugün"ü Türkiye takvim günüdür.
UTC tarih kullanmak Türkiye saatiyle 00:00-03:00 arasında her şeyi BİR GÜN
GERİYE atıyordu (canlı doğrulamada gece 01:05'te üretilen planın
training_started_at'i bir önceki güne yazıldı).

Kural: bir ANI (instant) saklarken UTC (DateTime timezone=True) — bir GÜNÜ
hesaplarken yalnız buradaki fonksiyonlar:
    bugun_tr()          — Türkiye takvimine göre bugün
    tr_gunu(an)         — bir anın Türkiye günü
    tr_gun_araligi(gun) — bir Türkiye gününün UTC sınırları [başlangıç, bitiş)

Türkiye 2016'dan beri kalıcı UTC+3 (yaz saati yok) — sabit ofset doğrudur.

Testler saati `saat_sabitle(an)` ile dondurabilir (23:30 / 00:30 senaryoları).
Bu modül api paketinin geri kalanını İÇE AKTARMAZ; engine de kullanır.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone

TZ_OFFSET_MIN = 180
TR = timezone(timedelta(minutes=TZ_OFFSET_MIN), "Europe/Istanbul")

_SABIT_AN: datetime | None = None            # yalnız testler


def simdi_utc() -> datetime:
    return _SABIT_AN if _SABIT_AN is not None else datetime.now(timezone.utc)


def simdi_tr() -> datetime:
    return simdi_utc().astimezone(TR)


def bugun_tr() -> date:
    """Türkiye takvimine göre BUGÜN."""
    return simdi_tr().date()


def tr_gunu(an: datetime) -> date:
    """Bir anın Türkiye günü. Saat dilimsiz değer UTC kabul edilir (SQLite)."""
    if an.tzinfo is None:
        an = an.replace(tzinfo=timezone.utc)
    return an.astimezone(TR).date()


def tr_gun_araligi(gun: date) -> tuple[datetime, datetime]:
    """Türkiye gününün UTC sınırları: [00:00 TR, ertesi 00:00 TR)."""
    bas = datetime.combine(gun, time.min, tzinfo=TR).astimezone(timezone.utc)
    return bas, bas + timedelta(days=1)


@contextmanager
def saat_sabitle(an: datetime):
    """TEST: saati dondur. `an` saat dilimli olmalı."""
    global _SABIT_AN
    onceki, _SABIT_AN = _SABIT_AN, an.astimezone(timezone.utc)
    try:
        yield
    finally:
        _SABIT_AN = onceki
