"""Ortak sütun yardımcıları — uuid PK ve created/updated zaman damgaları."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID


def uuid_pk() -> Mapped[uuid.UUID]:
    """uuid birincil anahtar — Python tarafında üretilir (dialect-bağımsız)."""
    return mapped_column(GUID, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """created_at / updated_at — DB server_default now(), update'te otomatik yenilenir."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False)
