"""
plan_uretim_isleri — asenkron plan üretim işlerinin PAYLAŞILAN durumu.

NEDEN (D-3, 2026-09-24): iş kaydı yalnız süreç belleğindeydi (plan_jobs._JOBS)
ama uvicorn 4 worker ile koşuyor. POST /plans/generate'i A worker'ı alıyor,
GET /plans/generate/{id} yoklamasının ~3/4'ü BAŞKA worker'a düşüp "İş
bulunamadı" (404) dönüyordu — mobil "plan hazırlanamadı" gösterip vazgeçiyordu.

Bellek kaydı KALIR (kuyruk sırası ve havuz o süreçte), bu tablo ona YAZIM
ORTAKLIĞIDIR: işin sahibi olmayan worker durumu buradan okur.

Kimlikler String: plan_jobs birim testleri sahte kimliklerle ("u1") koşuyor ve
tablo sorgulanan bir ilişki değil, durum defteri.
"""
from datetime import datetime

from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, JSONBType


class PlanUretimIsi(Base):
    __tablename__ = "plan_uretim_isleri"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    baby_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Yeniden başlatmaya dayanıklılık: işi koşturan süreç ("konteyner:pid");
    # NULL = yetim (süreç kapandı) → bakim() devralır. `parametreler` yeniden
    # koşmak için üretim girdileri; `deneme` kaç kez devralındığı.
    sahip: Mapped[str | None] = mapped_column(String(120), nullable=True)
    parametreler: Mapped[Any] = mapped_column(JSONBType, nullable=True)
    deneme: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False)
