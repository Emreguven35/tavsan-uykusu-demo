"""Subscription (IAP) şemaları — receipt doğrulama (şimdilik kaydet + active)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SubscriptionVerifyReq(BaseModel):
    platform: str = Field(pattern="^(ios|android)$")
    product_id: str = Field(min_length=1, max_length=120)
    receipt_data: str = Field(min_length=1)


class SubscriptionResp(BaseModel):
    id: uuid.UUID
    platform: str
    product_id: str
    status: str
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SubscriptionStatusResp(BaseModel):
    """SUNUCU-TARAFI premium kararı. Mobil bunu tek doğruluk kaynağı olarak
    kullanır; kendi istemci bayrağına güvenmez (karar: services.premium.durum).

      source: beta | store | manual | kurucu | none
      kurucu_uye: lansmandan önce kayıt olmuş (mobil indirimli teklif gösterir)
    """
    premium: bool
    source: str
    expires_at: datetime | None = None
    product_id: str | None = None
    will_renew: bool | None = None
    period_type: str | None = None          # TRIAL | INTRO | NORMAL
    kurucu_uye: bool = False


class SubscriptionRefreshResp(SubscriptionStatusResp):
    guncellenen_urunler: list[str] = []


class AdminPremiumReq(BaseModel):
    """POST /admin/premium — user_id YA DA email; gun 1-3650."""
    user_id: uuid.UUID | None = None
    email: str | None = Field(default=None, max_length=320)
    gun: int = Field(ge=1, le=3650)
    aciklama: str | None = Field(default=None, max_length=500)


class AdminPremiumResp(BaseModel):
    user_id: uuid.UUID
    baslangic: datetime
    bitis: datetime
