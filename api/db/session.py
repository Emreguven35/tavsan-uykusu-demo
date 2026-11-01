"""
SQLAlchemy engine + session fabrikası + FastAPI bağımlılığı (get_db).

Senkron SQLAlchemy (psycopg2) kullanılır — spec gereği ve FastAPI sync endpoint'leri
threadpool'da çalıştığından yeterli/basittir. get_db, istek başına bir Session verir
ve her durumda kapatır.
"""
import logging
import os
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from api.config import get_settings

logger = logging.getLogger("tavsan.db")
settings = get_settings()

# SQLite (lokal) için özel bağlantı argümanı.
# POSTGRES (2026-10-22 olayı): 11:13 TR'de postgres.railway.internal'a bağlanma
# "Connection timed out" ile düştü. connect_timeout yokken libpq işletim
# sisteminin TCP süresini (~2 dk) bekliyordu: istek de bakım turu da o kadar
# asılı kalıyordu. 5 sn'de vazgeçer; keepalive, sessizce kopan bağlantıyı
# (~60 sn: 30 boşta + 3 × 10 deneme) havuzda bayat bırakmadan yakalar.
_connect_args = {"check_same_thread": False} if settings.is_sqlite else {
    "connect_timeout": 5,
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}

# HAVUZ BOYUTU (v2.4.1) — uvicorn artık birden fazla worker ile koşuyor ve
# havuz SÜREÇ BAŞINA açılıyor. SQLAlchemy varsayılanı (5 + 10 taşma) × worker
# sayısı Postgres'in bağlantı tavanını zorlar. Toplam = WORKERS × (pool_size +
# max_overflow); 4 worker × 10 = 40 bağlantı, Railway Postgres tavanının
# (100) altında ve zamanlayıcı/işler/bakım betikleri için yer bırakıyor.
# 2026-09-30: gunicorn preload + WEB_CONCURRENCY=2 → 2 × 10 = 20 bağlantı.
# ÖLÇÜM: konteyner içinden 50 eşzamanlı GET /plans/today → p95 68 ms,
# 0 hata. 8 worker denendi, KAZANÇ YOK (sunucu zaten darboğaz değil);
# 4 worker ~3,8 GB RAM ile aynı sonucu veriyor.
# SQLite'ta havuz argümanları geçersizdir (tek dosya, tek süreç).
_pool_args = {} if settings.is_sqlite else {
    "pool_size": int(os.getenv("DB_POOL_SIZE") or 5),
    "max_overflow": int(os.getenv("DB_MAX_OVERFLOW") or 5),
    "pool_recycle": 1800,        # Railway boştaki bağlantıyı düşürüyor
    "pool_timeout": 10,          # havuz doluysa 30 sn değil 10 sn bekle
}

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,          # bayat bağlantıları otomatik tazele (Railway idle)
    future=True,
    # KVKK: hata metnine "[parameters: {'name': …, 'content': …}]" eklenmesin —
    # istisna mesajı loga ve Sentry'ye gidiyor (bkz. api/observability.py).
    hide_parameters=True,
    connect_args=_connect_args,
    **_pool_args,
)

# SQLite'ta FK kısıtları VARSAYILAN OLARAK KAPALI — açmazsak ondelete=CASCADE/SET NULL
# çalışmaz (production Postgres'te çalışır ama lokal/test sqlite'ta sessizce atlanır).
# Faz T: hesap silme → thread/reply user_id SET NULL davranışı testlerde de gerçek olsun.
if settings.is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_fk_on(dbapi_conn, _rec):     # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def get_db():
    """FastAPI Depends bağımlılığı: istek ömrü boyunca tek Session."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def db_healthy() -> bool:
    """/health için: DB'ye basit bir SELECT 1 atılabiliyor mu?"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:                       # ağ/kimlik/DB down → çökme, raporla
        logger.warning("DB health check başarısız: %s", e)
        return False


# --- Arka plan turları için DB kapısı (2026-10-22 olayı) ---------------------
# Kesinti sırasında bakım (dakikada bir, her worker) ve bildirim turu (5 dk)
# her seferinde patlayıp Sentry'ye hata yağdırıyordu. Artık turun başında hafif
# bir SELECT 1 atılır; DB yoksa tur ATLANIR. Kesinti başına: süreç başına tek
# uyarı logu, TÜM worker'lar arasında tek Sentry olayı (konteynerdeki ortak
# /tmp'de O_EXCL işaret dosyası — worker'lar aynı dosya sistemini görür).
_KESINTI_ISARETI = Path(tempfile.gettempdir()) / "tavsan_db_kesinti"
_kesintide = False               # bu süreç şu an kesinti mi görüyor


def _kesinti_olayi_gonder() -> None:
    """Kesinti başına tek Sentry olayı: işareti ilk oluşturan worker gönderir."""
    try:
        fd = os.open(_KESINTI_ISARETI, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return                   # başka worker zaten bildirdi
    except OSError:
        pass                     # /tmp yazılamıyorsa yine de bir kez gönder
    try:
        import sentry_sdk
        sentry_sdk.capture_message("DB erişilemiyor — arka plan turları atlanıyor",
                                   level="warning")
    except Exception:            # Sentry kapalı/kurulu değil → yalnız log
        pass


def db_erisilebilir_mi(is_adi: str) -> bool:
    """Arka plan turu başında: DB'ye SELECT 1 gidiyor mu? Gitmiyorsa False
    (tur atlanmalı). Kesintinin başı ve sonu birer kez loglanır, arası sessiz."""
    global _kesintide
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        if not _kesintide:
            _kesintide = True
            logger.warning("DB erişilemiyor — arka plan turları atlanıyor "
                           "(ilk gören: %s, %s)", is_adi, type(e).__name__)
            _kesinti_olayi_gonder()
        return False
    if _kesintide:
        _kesintide = False
        logger.info("DB yeniden erişilebilir — arka plan turları devam ediyor (%s)",
                    is_adi)
        try:
            _KESINTI_ISARETI.unlink(missing_ok=True)
        except OSError:
            pass
    return True
