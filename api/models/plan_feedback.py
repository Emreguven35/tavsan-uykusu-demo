"""
plan_feedback — annenin plan bloğuna verdiği geri bildirim ("bu saat uymadı").

ANLIK GÖRÜNTÜ: satır, geri bildirimin verildiği ANDAKİ plan içeriğini
(schedule, adaptation), o günün kayıtlarını ve yaş/bant bilgisini birlikte
saklar. Plan her kayıtta yeniden hesaplandığı için sonradan bakıldığında
"annenin itiraz ettiği çizelge" artık yoktur; denetim (İlayda raporu) ancak
bu görüntüyle anlamlıdır.

`created_at` İSTEMCİNİN zamanıdır (mobil offline kuyruğundan saatler sonra
gelebilir); sunucunun aldığı an `received_at`.
"""
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (Date, DateTime, ForeignKey, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base, JSONBType
from api.models._mixins import uuid_pk


class PlanFeedback(Base):
    __tablename__ = "plan_feedback"
    # İdempotency: mobil aynı geri bildirimi zayıf ağda yeniden gönderiyor.
    __table_args__ = (UniqueConstraint("user_id", "client_id",
                                       name="uq_plan_feedback_user_client"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    baby_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("babies.id", ondelete="CASCADE"), index=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)

    block_key: Mapped[str] = mapped_column(String(40), nullable=False)
    block_time: Mapped[str | None] = mapped_column(String(20), nullable=True)
    secenek: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metin: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Görüntünün ait olduğu plan (FK YOK: plan satırı yeniden üretilebilir,
    # geri bildirim kalmalı).
    plan_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    plan_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # {"schedule", "adaptation", "type", "kayitlar", "yas", "bant",
    #  "egitim_gunu", "asama"}
    anlik: Mapped[Any] = mapped_column(JSONBType, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 nullable=False, index=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
