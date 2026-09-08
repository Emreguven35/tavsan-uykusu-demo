"""
notifications router — /api/v1/notifications/* (Faz 6.2)

- POST   /register-token  : Expo push token upsert (mobil her açılışta çağırır)
- DELETE /token           : çıkışta cihaz token'ını sil
- GET    /preferences     : bildirim tercihleri
- PATCH  /preferences     : kısmi güncelleme

Hepsi auth korumalı ve user_id scoped.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user
from api.models import PushToken, User
from api.models.user import DEFAULT_NOTIFICATION_PREFS
from api.schemas.auth import MessageResp
from api.schemas.notification import (
    NotificationPrefs, NotificationPrefsUpdate, PushTokenResp, RegisterTokenReq,
)

logger = logging.getLogger("tavsan.notifications")
router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.post("/register-token", response_model=PushTokenResp)
def register_token(req: RegisterTokenReq, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Expo push token'ını kaydet/tazele (upsert).

    expo_token GLOBAL unique: aynı cihaz başka bir hesaba giriş yaptıysa token
    YENİ kullanıcıya taşınır — aksi halde bildirim önceki hesaba giderdi."""
    now = datetime.now(timezone.utc)
    row = (db.query(PushToken)
           .filter(PushToken.expo_token == req.expo_token)
           .one_or_none())

    if row is None:
        row = PushToken(user_id=user.id, expo_token=req.expo_token,
                        platform=req.platform, device_name=req.device_name,
                        last_seen_at=now)
        db.add(row)
    else:
        if row.user_id != user.id:
            logger.info("Push token sahibi değişti: %s → %s", row.user_id, user.id)
        row.user_id = user.id
        row.platform = req.platform or row.platform
        row.device_name = req.device_name or row.device_name
        row.last_seen_at = now

    db.commit()
    db.refresh(row)
    return row


@router.delete("/token", response_model=MessageResp)
def delete_token(expo_token: str = Body(..., embed=True),
                 db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """Çıkışta cihazın token'ını sil. Kullanıcı yalnız KENDİ token'ını silebilir.

    Token bulunamasa bile 200 döner (idempotent çıkış — mobil hata göstermesin)."""
    row = (db.query(PushToken)
           .filter(PushToken.expo_token == expo_token,
                   PushToken.user_id == user.id)
           .one_or_none())
    if row is not None:
        db.delete(row)
        db.commit()
    return MessageResp(detail="Bildirim token'ı silindi")


def _current_prefs(user: User) -> dict:
    """NULL/eksik alanları varsayılana tamamla (migration geriye uyumluluğu)."""
    prefs = dict(DEFAULT_NOTIFICATION_PREFS)
    if isinstance(user.notification_prefs, dict):
        prefs.update(user.notification_prefs)
    return prefs


@router.get("/preferences", response_model=NotificationPrefs)
def get_preferences(user: User = Depends(get_current_user)):
    return NotificationPrefs(**_current_prefs(user))


@router.patch("/preferences", response_model=NotificationPrefs)
def update_preferences(req: NotificationPrefsUpdate, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    prefs = _current_prefs(user)
    changes = req.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Güncellenecek alan verilmedi")
    prefs.update(changes)
    # JSONB değişikliğinin görülmesi için YENİ sözlük ata (in-place mutasyon değil).
    user.notification_prefs = dict(prefs)
    db.commit()
    db.refresh(user)
    return NotificationPrefs(**_current_prefs(user))
