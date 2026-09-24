"""
feedback router — /api/v1/feedback

POST /feedback/plan    annenin plan bloğuna geri bildirimi ("bu saat uymadı")

Sunucu o anki plan içeriğini, günün kayıtlarını ve yaş/bant bilgisini birlikte
saklar (bkz. api/services/geri_bildirim.py). İdempotent: aynı client_id
ikinci kez gelirse 200 + mevcut kayıt, kopya YAZILMAZ; ilk kayıt 201.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user, get_owned_baby
from api.models import User
from api.services import geri_bildirim

router = APIRouter(prefix="/feedback", tags=["feedback"])


class PlanFeedbackReq(BaseModel):
    baby_id: uuid.UUID
    block_key: str = Field(min_length=1, max_length=40)
    block_time: str | None = Field(default=None, max_length=20)
    secenek: str | None = Field(default=None, max_length=80)
    metin: str | None = Field(default=None, max_length=2000)
    client_id: str = Field(min_length=1, max_length=64)
    # İSTEMCİNİN zamanı — offline kuyruktan saatler sonra gelebilir; asıl
    # zaman budur. Görüntü de bu anın gününe göre alınır.
    created_at: datetime


class PlanFeedbackResp(BaseModel):
    id: uuid.UUID
    client_id: str
    duplicate: bool
    created_at: datetime
    received_at: datetime


@router.post("/plan", response_model=PlanFeedbackResp,
             status_code=status.HTTP_201_CREATED,
             responses={200: {"model": PlanFeedbackResp,
                              "description": "Aynı client_id zaten kayıtlı"}})
def plan_geri_bildirimi(req: PlanFeedbackReq, response: Response,
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    baby = get_owned_baby(req.baby_id, db, user)        # başkasının bebeği → 404
    satir, yeni = geri_bildirim.kaydet(db, user, baby, req)
    if not yeni:
        response.status_code = status.HTTP_200_OK
    return PlanFeedbackResp(id=satir.id, client_id=satir.client_id,
                            duplicate=not yeni, created_at=satir.created_at,
                            received_at=satir.received_at)
