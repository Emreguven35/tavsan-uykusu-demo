"""subscriptions — IAP abonelik durumu (App Store / Google Play makbuzları)."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import GUID, Base
from api.models._mixins import TimestampMixin, uuid_pk


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    platform: Mapped[str] = mapped_column(String(10), nullable=False)      # ios | android
    product_id: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    receipt_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # ham makbuz (TODO: doğrulama)

    user = relationship("User", back_populates="subscriptions")
