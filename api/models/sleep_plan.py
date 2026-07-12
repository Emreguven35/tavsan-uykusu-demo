"""sleep_plans — üretilen kişisel uyku planı (içerik JSONB olarak saklanır)."""
import uuid
from datetime import date
from typing import Any

from sqlalchemy import Date, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import GUID, JSONBType, Base
from api.models._mixins import uuid_pk
from sqlalchemy import DateTime, func


class SleepPlan(Base):
    __tablename__ = "sleep_plans"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    baby_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("babies.id", ondelete="CASCADE"), index=True, nullable=False)

    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Plan içeriği: parametre motoru + Claude çıktısı yapısal biçimde (JSONB).
    content: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False)

    created_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="sleep_plans")
    baby = relationship("Baby")
