"""
SQLAlchemy engine + session fabrikası + FastAPI bağımlılığı (get_db).

Senkron SQLAlchemy (psycopg2) kullanılır — spec gereği ve FastAPI sync endpoint'leri
threadpool'da çalıştığından yeterli/basittir. get_db, istek başına bir Session verir
ve her durumda kapatır.
"""
import logging
import os

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from api.config import get_settings

logger = logging.getLogger("tavsan.db")
settings = get_settings()

# SQLite (lokal) için özel bağlantı argümanı; postgres'te gerekmez.
_connect_args = {"check_same_thread": False} if settings.is_sqlite else {}

# HAVUZ BOYUTU (v2.4.1) — uvicorn artık birden fazla worker ile koşuyor ve
# havuz SÜREÇ BAŞINA açılıyor. SQLAlchemy varsayılanı (5 + 10 taşma) × worker
# sayısı Postgres'in bağlantı tavanını zorlar. Toplam = WORKERS × (pool_size +
# max_overflow); 4 worker × 10 = 40 bağlantı, Railway Postgres tavanının
# (100) altında ve zamanlayıcı/işler/bakım betikleri için yer bırakıyor.
# ÖLÇÜM: konteyner içinden 50 eşzamanlı GET /plans/today → p95 68 ms,
# 0 hata. 8 worker denendi, KAZANÇ YOK (sunucu zaten darboğaz değil);
# 4 worker ~3,8 GB RAM ile aynı sonucu veriyor.
# SQLite'ta havuz argümanları geçersizdir (tek dosya, tek süreç).
_pool_args = {} if settings.is_sqlite else {
    "pool_size": int(os.getenv("DB_POOL_SIZE") or 5),
    "max_overflow": int(os.getenv("DB_MAX_OVERFLOW") or 5),
    "pool_recycle": 1800,        # Railway boştaki bağlantıyı düşürüyor
}

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,          # bayat bağlantıları otomatik tazele (Railway idle)
    future=True,
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
