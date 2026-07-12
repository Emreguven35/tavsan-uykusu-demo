"""
SQLAlchemy engine + session fabrikası + FastAPI bağımlılığı (get_db).

Senkron SQLAlchemy (psycopg2) kullanılır — spec gereği ve FastAPI sync endpoint'leri
threadpool'da çalıştığından yeterli/basittir. get_db, istek başına bir Session verir
ve her durumda kapatır.
"""
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from api.config import get_settings

logger = logging.getLogger("tavsan.db")
settings = get_settings()

# SQLite (lokal) için özel bağlantı argümanı; postgres'te gerekmez.
_connect_args = {"check_same_thread": False} if settings.is_sqlite else {}

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,          # bayat bağlantıları otomatik tazele (Railway idle)
    future=True,
    connect_args=_connect_args,
)

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
