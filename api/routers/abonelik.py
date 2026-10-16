"""
abonelik router — /api/v1/abonelik (premium abonelik, 2026-10-13)

Mobilin beklediği sözleşme — alan adları AYNEN korunmalı:

GET  /abonelik/durum   → {durum, deneme_gunu, deneme_bitis, deneme_hakki_var, sor_kalan}
POST /abonelik/senkron → RevenueCat REST'ten çek, kaydı güncelle, aynı cevabı dön.

Karar services.premium.durum'dan (TEK kaynak): durum = kademe
(ucretsiz | deneme | premium). BETA_MODE açıkken herkes premium görünür —
BETA_HARIC_EMAILS'teki hesaplar hariç (ücretsiz/deneme akışını denemek için).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user
from api.models import User
from api.schemas.subscription import AbonelikDurumResp
from api.services import premium as premium_svc
from api.services import sor_hakki

router = APIRouter(prefix="/abonelik", tags=["abonelik"])


def _durum_yaniti(db: Session, user: User) -> AbonelikDurumResp:
    d = premium_svc.durum(db, user)
    kademe = d["kademe"]
    return AbonelikDurumResp(
        durum=kademe,
        deneme_gunu=premium_svc.deneme_gunu(user, d),
        deneme_bitis=d.get("deneme_bitis") if kademe == "deneme" else None,
        deneme_hakki_var=premium_svc.deneme_hakki_var(db, user),
        sor_kalan=sor_hakki.kalan(db, user, kademe))


@router.get("/durum", response_model=AbonelikDurumResp)
def durum(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _durum_yaniti(db, user)


@router.post("/senkron", response_model=AbonelikDurumResp)
def senkron(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Satın alma / geri yükleme sonrası: RevenueCat'teki gerçek durumu çek.
    Mağaza bağlantısı yapılandırılmamış ya da RevenueCat yanıt vermezse 503."""
    try:
        veri = premium_svc.musteri_cek(user.id)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=str(e))
    premium_svc.musteriden_guncelle(db, user, veri)
    return _durum_yaniti(db, user)
