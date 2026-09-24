"""
webhooks router — /api/v1/webhooks

POST /webhooks/revenuecat    RevenueCat abonelik olayları (B4)

YETKİ: RevenueCat panelinde tanımlanan "Authorization header" değeri
REVENUECAT_WEBHOOK_SECRET ile SABİT ZAMANDA karşılaştırılır ("Bearer " öneki
olsa da olmasa da kabul). Yanlış/eksik → 401. Sır tanımsızsa uç KAPALI (503) —
sırsız bir webhook herkesin kendine abonelik yazması demektir.

Doğru yetkiyle gelen her olay 200 alır (işlenmese bile): RevenueCat 200
görmeyene kadar yeniden dener; çözülemeyen olayı tekrar denemek düzeltmez.
İş mantığı api/services/premium.py (olay_isle).
"""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_db
from api.services import premium as premium_svc

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/revenuecat")
async def revenuecat(request: Request, db: Session = Depends(get_db),
                     authorization: str | None = Header(default=None)):
    sir = get_settings().revenuecat_webhook_secret
    if not sir:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Webhook yapılandırılmamış")
    gelen = (authorization or "").strip()
    if gelen.lower().startswith("bearer "):
        gelen = gelen[7:].strip()
    if not gelen or not secrets.compare_digest(gelen.encode(), sir.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Geçersiz webhook yetkisi")
    try:
        govde = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Gövde JSON değil")
    durum, aciklama = premium_svc.olay_isle(db, govde if isinstance(govde, dict) else {})
    return {"ok": True, "durum": durum, "aciklama": aciklama}
