"""
SQLAlchemy 2.0 DeclarativeBase + dialect-bağımsız ortak tipler.

PostgreSQL production hedefidir (uuid + JSONB). Ancak modeller lokal SQLite'ta da
çalışsın diye tipler 'variant' ile tanımlanır:
  - GUID  → PG'de native UUID, SQLite'ta CHAR(32) (sa.Uuid otomatik yapar)
  - JSONBType → PG'de JSONB, diğerlerinde generic JSON
  - TextArrayType → PG'de text[], diğerlerinde JSON dizisi

Böylece `alembic upgrade head` postgres'te JSONB/uuid üretir; lokal sqlite testinde
aynı modeller sorunsuz create edilir.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Tüm ORM modellerinin tabanı. Alembic bu metadata'yı autogenerate'te kullanır."""
    pass


# uuid: PG'de native UUID, diğer dialektlerde CHAR(32). as_uuid=True → Python uuid.UUID.
GUID = sa.Uuid(as_uuid=True)

# JSONB: PG'de JSONB (indekslenebilir), diğerlerinde generic JSON.
JSONBType = sa.JSON().with_variant(JSONB(astext_type=sa.Text()), "postgresql")

# Metin listesi: PG'de text[] (GIN ile indekslenebilir, `@>` ile
# sorgulanabilir), diğer dialektlerde generic JSON dizisi. Python tarafında
# İKİSİ DE list[str] döndürür; çağıran kod dialekti bilmez.
TextArrayType = sa.JSON().with_variant(ARRAY(sa.Text()), "postgresql")
