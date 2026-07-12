"""
Alembic ortamı — DATABASE_URL env'den okunur, target metadata = api.db.Base.metadata.

`alembic upgrade head` hem PostgreSQL (production) hem SQLite (lokal test) üzerinde
çalışır; tipler api/db/base.py'de dialect-variant tanımlıdır.
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Proje kökünü sys.path'e ekle (api paketini import edebilmek için).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from api.config import get_settings  # noqa: E402
from api.db.base import Base         # noqa: E402
import api.models                    # noqa: E402,F401 — tüm modelleri metadata'ya yükle

config = context.config
# DATABASE_URL'i env/settings'ten al (alembic.ini'de boş bırakıldı).
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # SQLite'ta ALTER kısıtlı; batch mode ile güvenli migration.
        is_sqlite = connection.dialect.name == "sqlite"
        context.configure(
            connection=connection, target_metadata=target_metadata,
            compare_type=True, render_as_batch=is_sqlite,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
