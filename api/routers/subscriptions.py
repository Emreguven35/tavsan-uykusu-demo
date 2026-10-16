"""
subscriptions router — /api/v1/subscriptions

POST /verify: istemcinin gönderdiği makbuza GÜVENİLMEZ (2026-10-13 güvenlik
düzeltmesi — eskiden her makbuz süresiz "active" yazılıyordu). Gerçek durum
RevenueCat REST'ten (REVENUECAT_API_KEY, GET /subscribers/{user_id}) çekilir ve
kayıt ona göre güncellenir. Sahte (mock_ ...) makbuz YALNIZ production dışında,
süreli bir test kaydı yazar.
GET /: kullanıcının abonelik durumu (mobil paywall bunu tüketebilir).
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_db
from api.deps import get_current_user
from api.models import Subscription, User
from api.schemas.subscription import (
    SubscriptionRefreshResp, SubscriptionResp, SubscriptionStatusResp,
    SubscriptionVerifyReq,
)
from api.services import premium as premium_svc

logger = logging.getLogger("tavsan.subscriptions")
router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

# Sahte makbuz önekleri — YALNIZ production dışında (yerel/test) çalışır.
_MOCK_RECEIPT_PREFIXES = ("dev_", "mock_", "mock_receipt", "test_", "fake_", "sahte")
# Test makbuzunun yazdığı kaydın süresi; "trial" içeren makbuz deneme yazar.
_MOCK_SURE = timedelta(days=30)
_MOCK_DENEME_SURE = timedelta(days=3)


def _mock_kayit(db: Session, user: User, req: SubscriptionVerifyReq) -> Subscription:
    """Production DIŞINDA sahte makbuz: süreli test kaydı (SANDBOX)."""
    deneme = "trial" in req.receipt_data.lower()
    simdi = datetime.now(timezone.utc)
    urun = premium_svc.urun_kimligi(req.product_id)
    sub = premium_svc.abonelik_bul(db, user.id, urun)
    if sub is None:
        sub = Subscription(user_id=user.id, platform=req.platform, product_id=urun)
        db.add(sub)
    sub.status, sub.will_renew, sub.environment = "active", True, "SANDBOX"
    sub.period_type = "TRIAL" if deneme else "NORMAL"
    sub.purchased_at = simdi
    sub.expires_at = simdi + (_MOCK_DENEME_SURE if deneme else _MOCK_SURE)
    premium_svc.deneme_kaydet(user, sub)
    db.commit()
    db.refresh(sub)
    return sub


@router.post("/verify", response_model=SubscriptionResp)
def verify(req: SubscriptionVerifyReq, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    """Satın alma sonrası: gerçek durum RevenueCat'ten çekilir; istemcinin
    makbuzu karar VERMEZ. Mağazada bu ürünün kaydı yoksa 404."""
    rd = (req.receipt_data or "").strip()
    if not rd:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Geçersiz makbuz")
    if rd.lower().startswith(_MOCK_RECEIPT_PREFIXES):
        if get_settings().is_production:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Geçersiz makbuz")
        sub = _mock_kayit(db, user, req)
        logger.info("Test aboneliği (sahte makbuz): user=%s product=%s",
                    user.id, req.product_id)
        return sub
    try:
        veri = premium_svc.musteri_cek(user.id)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=str(e))
    premium_svc.musteriden_guncelle(db, user, veri)
    sub = premium_svc.abonelik_bul(db, user.id, req.product_id)
    if sub is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Mağazada bu ürün için satın alma bulunamadı.")
    logger.info("Abonelik doğrulandı (RevenueCat): user=%s product=%s durum=%s",
                user.id, req.product_id, sub.status)
    return sub


@router.get("/status", response_model=SubscriptionStatusResp)
def premium_status(db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """SUNUCU-TARAFI premium kararı. Mobil paywall bunu tek doğruluk kaynağı
    olarak kullanır — istemcinin kendi 'premium' bayrağına güvenilmez.

    Karar services.premium.durum'dan: beta → store → manual → kurucu. Premium
    uçları koruyan require_premium ile AYNI fonksiyon."""
    return SubscriptionStatusResp(**premium_svc.durum(db, user))


@router.get("/refresh", response_model=SubscriptionRefreshResp)
def refresh(db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    """Webhook kaçarsa: RevenueCat REST API'den müşteri kaydını çek, tabloyu
    güncelle, güncel kararı dön. Mobil satın alma sonrası ve "Satın alımları
    geri yükle"de çağırır."""
    try:
        veri = premium_svc.musteri_cek(user.id)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=str(e))
    urunler = premium_svc.musteriden_guncelle(db, user, veri)
    return SubscriptionRefreshResp(**premium_svc.durum(db, user),
                                   guncellenen_urunler=urunler)


@router.get("", response_model=list[SubscriptionResp])
def list_subscriptions(db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    return (db.query(Subscription).filter(Subscription.user_id == user.id)
            .order_by(Subscription.created_at.desc()).all())
