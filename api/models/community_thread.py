"""
threads — topluluk konusu (Faz T). Düz metin; iç içe cevap yok.

user_id ondelete=SET NULL: hesap silinince konu KALIR, yazarı "Silinmiş kullanıcı"
olarak render edilir (community_profiles CASCADE ile gitse de thread durur).
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True)

    # uyku | beslenme | gelisim | anne_hali | oneri
    category: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    body: Mapped[str] = mapped_column(String(1000), nullable=False)
    # published | pending | hidden | removed
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="published", index=True)

    reply_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expert_replied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
