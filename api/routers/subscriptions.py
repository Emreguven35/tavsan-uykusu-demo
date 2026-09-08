"""
subscriptions router — /api/v1/subscriptions

POST /verify: IAP makbuzunu al, KAYDET ve status=active dön.
GERÇEK Apple/Google doğrulaması TODO (aşağıda işaretli) — şimdilik makbuz saklanır.
GET /: kullanıcının abonelik durumu (mobil paywall bunu tüketebilir).
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_db
from api.deps import get_current_user
from api.models import Subscription, User
from api.schemas.subscription import (
    SubscriptionResp, SubscriptionStatusResp, SubscriptionVerifyReq,
)

logger = logging.getLogger("tavsan.subscriptions")
router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

# Faz G5: bariz sahte/mock makbuzlar kabul edilmez (gerçek IAP doğrulaması ayrı
# sprint; o gelene kadar en azından çöp makbuz 'active' yapılmaz).
_MOCK_RECEIPT_PREFIXES = ("dev_", "mock_", "mock_receipt", "test_", "fake_", "sahte")


@router.post("/verify", response_model=SubscriptionResp)
def verify(req: SubscriptionVerifyReq, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    # Faz G5: bariz sahte/mock/boş makbuzu reddet (400). Gerçek Apple/Google
    # doğrulaması TODO (aşağıda) — ama çöp makbuz artık 'active' olmaz.
    rd = (req.receipt_data or "").strip()
    if not rd or rd.lower().startswith(_MOCK_RECEIPT_PREFIXES):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Geçersiz makbuz")

    # Aynı (user, platform, product) varsa güncelle; yoksa oluştur (upsert).
    sub = (db.query(Subscription)
           .filter(Subscription.user_id == user.id,
                   Subscription.platform == req.platform,
                   Subscription.product_id == req.product_id)
           .one_or_none())
    if sub is None:
        sub = Subscription(user_id=user.id, platform=req.platform,
                           product_id=req.product_id)
        db.add(sub)
    sub.receipt_data = req.receipt_data
    # TODO(IAP doğrulama): Apple App Store Server API / Google Play Developer API ile
    # makbuzu doğrula, gerçek expires_at ve status'u oradan al. Şimdilik active.
    sub.status = "active"
    sub.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(sub)
    logger.info("Abonelik doğrulandı (kayıt): user=%s platform=%s product=%s",
                user.id, req.platform, req.product_id)
    return sub


@router.get("/status", response_model=SubscriptionStatusResp)
def premium_status(db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """SUNUCU-TARAFI premium kararı (Faz G5). Mobil paywall bunu tek doğruluk
    kaynağı olarak kullanmalı — istemcinin kendi 'premium' bayrağına güvenilmez.

    premium = aktif abonelik VAR **veya** BETA_PREMIUM_ALL açık. Beta bittiğinde
    flag kapatılır ve karar tümüyle gerçek aboneliğe döner (kod değişmeden)."""
    settings = get_settings()
    if settings.beta_premium_all:
        return SubscriptionStatusResp(premium=True, source="beta")
    aktif = (db.query(Subscription)
             .filter(Subscription.user_id == user.id,
                     Subscription.status == "active")
             .first())
    if aktif is not None:
        return SubscriptionStatusResp(premium=True, source="subscription")
    return SubscriptionStatusResp(premium=False, source="none")


@router.get("", response_model=list[SubscriptionResp])
def list_subscriptions(db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    return (db.query(Subscription).filter(Subscription.user_id == user.id)
            .order_by(Subscription.created_at.desc()).all())
