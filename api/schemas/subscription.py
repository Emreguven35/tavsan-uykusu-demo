"""Subscription (IAP) şemaları. /verify istemci makbuzuna GÜVENMEZ — durum
RevenueCat REST'ten çekilir (2026-10-13)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SubscriptionVerifyReq(BaseModel):
    platform: str = Field(pattern="^(ios|android)$")
    product_id: str = Field(min_length=1, max_length=120)
    # Geriye uyum için alınır ama KARAR VERMEZ (bkz. routers/subscriptions.verify).
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

      kademe: ucretsiz | deneme | premium (premium = kademe "premium")
      source: beta | store | manual | kurucu | none
      kurucu_uye: lansmandan önce kayıt olmuş (mobil indirimli teklif gösterir)
    """
    kademe: str = "ucretsiz"
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


class AbonelikDurumResp(BaseModel):
    """GET /abonelik/durum · POST /abonelik/senkron (mobil sözleşmesi, 2026-10-13).

      durum             ucretsiz | deneme | premium
      deneme_gunu       denemenin kaçıncı günü (1-3), deneme değilse null
      deneme_bitis      deneme bitişi (ISO), deneme değilse null
      deneme_hakki_var  hiç mağaza kaydı yok ve deneme kullanılmadı
      sor_kalan         bugün kalan Sor hakkı — deneme: 10 - kullanılan,
                        ücretsiz: 0, premium: null (sınırsız)
    """
    durum: str
    deneme_gunu: int | None = None
    deneme_bitis: datetime | None = None
    deneme_hakki_var: bool
    sor_kalan: int | None = None
