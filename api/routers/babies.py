"""
babies router — /api/v1/babies

Hepsi auth korumalı ve user_id scoped: kullanıcı yalnız KENDİ bebeklerini görür/değiştirir.
"""
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user, get_owned_baby
from api.models import Baby, User
from api.schemas.baby import BabyCreate, BabyResp, BabyUpdate

router = APIRouter(prefix="/babies", tags=["babies"])


@router.post("", response_model=BabyResp, status_code=status.HTTP_201_CREATED)
def create_baby(req: BabyCreate, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    baby = Baby(user_id=user.id, **req.model_dump())
    db.add(baby)
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
    # Yalnız gönderilen alanları güncelle (exclude_unset).
    for field, value in req.model_dump(exclude_unset=True).items():
        setattr(baby, field, value)
    db.commit()
    db.refresh(baby)
    return baby
