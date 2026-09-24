"""
babies router — /api/v1/babies

Hepsi auth korumalı ve user_id scoped: kullanıcı yalnız KENDİ bebeklerini görür/değiştirir.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user, get_owned_baby
from api.models import Baby, User
from api.schemas.baby import BabyCreate, BabyResp, BabyUpdate
from api.services import plan_service

logger = logging.getLogger("tavsan.babies")
router = APIRouter(prefix="/babies", tags=["babies"])


def _dogum_tarihini_dogrula(dogum) -> None:
    hata = plan_service.dogum_tarihi_hatasi(dogum)
    if hata:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=hata)


def _egitim_baslangicini_suz(db: Session, baby: Baby) -> None:
    """Denetim B3 — eğitim programı yürümeyen bebekte (bekleme/yenidoğan planı,
    ya da plansız ve 5 aydan küçük) training_started_at YAZILMAZ.

    Mobil Eğitim sekmesini ilk açışta bu alanı HER bebek için gönderiyor; 3
    aylık bebekler denetimde "eğitimin 7. günü · aşama: Kapı" görünüyordu.
    İstek REDDEDİLMEZ (eski istemci hata ekranı göstermesin), alan sessizce
    boş bırakılır. Gerçek başlangıcı 5 ay geçişi sunucuda yazar
    (plan_service.egitim_gecisini_baslat)."""
    if baby.training_started_at is not None and not plan_service.egitim_aktif_mi(db, baby):
        logger.info("training_started_at yok sayıldı (eğitim aktif değil): baby=%s",
                    baby.id)
        baby.training_started_at = None


@router.post("", response_model=BabyResp, status_code=status.HTTP_201_CREATED)
def create_baby(req: BabyCreate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _dogum_tarihini_dogrula(req.birth_date)
    baby = Baby(user_id=user.id, **req.model_dump())
    db.add(baby)
    db.flush()
    _egitim_baslangicini_suz(db, baby)
    db.commit()
    db.refresh(baby)
    return baby


@router.get("", response_model=list[BabyResp])
def list_babies(db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    return (db.query(Baby).filter(Baby.user_id == user.id)
            .order_by(Baby.created_at).all())


@router.patch("/{baby_id}", response_model=BabyResp)
def update_baby(baby_id: uuid.UUID, req: BabyUpdate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    baby = get_owned_baby(baby_id, db, user)
    degisen = req.model_dump(exclude_unset=True)
    if "birth_date" in degisen:
        _dogum_tarihini_dogrula(degisen["birth_date"])
    # Yalnız gönderilen alanları güncelle (exclude_unset).
    for field, value in degisen.items():
        setattr(baby, field, value)
    if degisen.get("training_started_at") is not None:
        _egitim_baslangicini_suz(db, baby)
    db.commit()
    db.refresh(baby)
    return baby
