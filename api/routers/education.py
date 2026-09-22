"""
education router — /api/v1/education

GET  /education/videos                  bütün katalog + ilerleme + günün önerisi
POST /education/videos/{id}/progress    oynatıcı ilerlemesi (upsert)

İŞ MANTIĞI BURADA DEĞİL: api/services/education.py içindedir (betik de aynı
servisi kullanıyor). Bu router yalnız HTTP kabuğudur.

Video DOSYALARI buradan sunulmaz — onlar public `/media/videos/...` ucundadır
(bkz. api/routers/media.py). Katalog korumalı, dosya public: kişisel olan
"kim ne izledi" bilgisidir, videonun kendisi değil.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user
from api.models import Baby, EducationVideo, User
from api.schemas.education import (EgitimVideolariResp, VideoProgressReq,
                                   VideoProgressResp)
from api.services import education

logger = logging.getLogger("tavsan.education")
router = APIRouter(prefix="/education", tags=["education"])


def _bebek(db: Session, user: User, baby_id: uuid.UUID | None) -> Baby | None:
    """Aşamanın hesaplanacağı bebek.

    baby_id verilmezse kullanıcının EN YENİ bebeği kullanılır: eğitim sekmesi
    bebek seçtirmiyor ve tek bebekli hesapların ezici çoğunluğunda doğru cevap
    budur. Bebeği olmayan kullanıcıda aşama "eğitim öncesi" olur."""
    q = db.query(Baby).filter(Baby.user_id == user.id)
    if baby_id is not None:
        bebek = q.filter(Baby.id == baby_id).one_or_none()
        if bebek is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Bebek bulunamadı")
        return bebek
    return q.order_by(Baby.created_at.desc()).first()


@router.get("/videos", response_model=EgitimVideolariResp)
def egitim_videolari(baby_id: uuid.UUID | None = Query(default=None),
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """Eğitim sekmesi — kategoriler, ilerleme, günün önerisi, aşama rozeti.

    Tek çağrı bilinçli: liste ekranı açılırken ikinci bir istek atmasın."""
    return education.katalog(db, user, _bebek(db, user, baby_id))


@router.post("/videos/{video_id}/progress", response_model=VideoProgressResp)
def ilerleme(video_id: uuid.UUID, req: VideoProgressReq,
             db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """Oynatıcı ilerlemesini kaydet (upsert). Aynı video için satır YIĞILMAZ."""
    video = db.query(EducationVideo).filter(
        EducationVideo.id == video_id).one_or_none()
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Video bulunamadı")
    satir = education.ilerleme_kaydet(db, user, video, req.position_sec,
                                      req.completed)
    return VideoProgressResp(video_id=video.id,
                             position_sec=satir.position_sec,
                             completed=bool(satir.completed_at))
