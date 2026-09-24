"""
subscriptions — mağaza abonelikleri (RevenueCat) + eski makbuz kayıtları.

B4 (RevenueCat): satır (user_id, product_id) başına TEKTİR ve webhook /
refresh ile güncellenir. `status`:
    active        — erişim var (deneme dahil; period_type=TRIAL)
    billing_issue — ödeme sorunu; mağazanın ek süresi (grace) boyunca erişim sürer
    expired       — bitti
`will_renew=False` iptal edilmiş ama süresi dolmamış abonelik demektir —
erişim expires_at'e kadar SÜRER.

Eski alanlar (platform, receipt_data) geriye uyum için duruyor: /verify ile
gelmiş satırlar da premium kararında sayılır.
"""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import GUID, Base, JSONBType
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
    receipt_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # eski /verify makbuzu

    # --- B4: RevenueCat -------------------------------------------------------
    store: Mapped[str | None] = mapped_column(String(20), nullable=True)   # APP_STORE | PLAY_STORE | …
    period_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # TRIAL | INTRO | NORMAL
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    will_renew: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    environment: Mapped[str | None] = mapped_column(String(12), nullable=True)  # SANDBOX | PRODUCTION
    # PRODUCT_CHANGE: bir sonraki yenilemede geçilecek ürün (erişimi değiştirmez).
    sonraki_urun: Mapped[str | None] = mapped_column(String(120), nullable=True)
    raw_event: Mapped[Any] = mapped_column(JSONBType, nullable=True)       # son işlenen olay

    user = relationship("User", back_populates="subscriptions")


class RevenueCatOlayi(Base):
    """İşlenen her webhook olayı — İDEMPOTENCY defteri. RevenueCat aynı olayı
    (aynı `event.id`) ağ hatasında yeniden gönderir; ikinci geliş işlenmez."""

    __tablename__ = "revenuecat_olaylari"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)     # event.id
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    app_user_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Eşleşen kullanıcı (bulunamadıysa NULL — olay yine saklanır). FK YOK:
    # hesap silinse de ödeme izi kalmalı.
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True, index=True)
    sonuc: Mapped[str] = mapped_column(String(200), nullable=False)
    raw: Mapped[Any] = mapped_column(JSONBType, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PremiumHak(Base):
    """Manuel premium hakkı (admin hediyesi — İlayda'nın birebir danışanları)."""

    __tablename__ = "premium_haklari"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    baslangic: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bitis: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    veren_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    aciklama: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
