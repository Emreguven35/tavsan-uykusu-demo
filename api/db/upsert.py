"""
Atomik UPSERT yardımcıları — "önce SEÇ, yoksa EKLE" desenini bitirir.

NEDEN VAR (Sentry, 2026-09-22): `video_progress` üzerinde UniqueViolation
görüldü. Desen şuydu:

    satir = db.query(X).filter(...).one_or_none()
    if satir is None:
        db.add(X(...))
    db.commit()

SELECT ile INSERT arasında başka bir istek (ya da başka bir uvicorn worker'ı)
aynı satırı yazarsa ikinci INSERT tekillik kısıtına çarpar ve istek 500 döner.
Oynatıcı ilerlemeyi duraklama/çıkış/aralıklı gönderdiği için iki isteğin
çakışması sık: kullanıcı videoyu kapatırken hem "duraklat" hem "çıkış"
gönderiyor ve ikisi de AYNI ANDA ilk kaydı yazmaya çalışıyor.

Çözüm tek bir ifadeye indirmektir: `INSERT ... ON CONFLICT DO UPDATE`.
Kısıt ihlali artık hata değil, güncellemedir; kilit DB'nin kendisindedir.

İKİ LEHÇE: üretim Postgres, testler sqlite. İkisi de ON CONFLICT destekler
ama "iki değerin büyüğü" işlevinin ADI farklıdır (GREATEST / MAX). Bu modül
o farkı tek yerde kapatır — çağrı yerleri lehçe bilmez.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects import postgresql, sqlite

from api.db.session import engine


def _sqlite_mi() -> bool:
    """Lehçe ÇALIŞMA ANINDA sorulur: testler engine'i env ile kuruyor."""
    return engine.dialect.name == "sqlite"


def insert(model: Any):
    """Lehçeye uygun INSERT — `.on_conflict_do_update(...)` taşır."""
    return sqlite.insert(model) if _sqlite_mi() else postgresql.insert(model)


def en_buyuk(*ifadeler: Any):
    """PG: `GREATEST(a, b)` — SQLite: `MAX(a, b)`. Aynı anlam, ayrı ad.

    DİKKAT: SQLite'ta `MAX` iki yüzlüdür; TEK argümanla toplayıcı (aggregate),
    ÇOK argümanla skalerdir. Burada daima ≥2 argümanla çağrılır."""
    return func.max(*ifadeler) if _sqlite_mi() else func.greatest(*ifadeler)


def ilk_dolu(*ifadeler: Any):
    """`COALESCE(...)` — ilk NULL olmayan. Damgayı geri almamak için."""
    return func.coalesce(*ifadeler)
