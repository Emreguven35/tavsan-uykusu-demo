"""
sor_bildirim router — /api/v1/sor/mesajlar/{mesaj_id}/bildir (2026-11-01)

Anne Sor'un bir cevabını bildirir (Play "AI ile üretilen içerik" politikası).
mesaj_id: POST /chat yanıtındaki asistan satırı id'si.

- Yalnız kullanıcının KENDİ asistan mesajı; başkasının ya da olmayan id → 404
  (varlık sızdırılmaz).
- Aynı mesajı tekrar bildirmek satırı GÜNCELLER (sebep, not; durum yeniden
  "yeni") → yine 200.
- Kullanıcı başına Türkiye gününde SOR_BILDIRIM_GUNLUK yeni bildirim; aşılırsa 429.
  Güncelleme sayılmaz.
- Soru ve cevap metni bildirim anındaki kopya; e-posta tutulmaz. Log içeriksiz.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user
from api.models import AiCevapBildirimi, ChatMessage, User
from api.schemas.chat import AiBildirimReq
from api.zaman import bugun_tr, tr_gun_araligi

logger = logging.getLogger("tavsan.sor_bildirim")
router = APIRouter(prefix="/sor", tags=["sor"])

SOR_BILDIRIM_GUNLUK = 20


def _bugunku_sayi(db: Session, user: User) -> int:
    bas, bit = tr_gun_araligi(bugun_tr())
    return (db.query(AiCevapBildirimi)
            .filter(AiCevapBildirimi.user_id == user.id,
                    AiCevapBildirimi.created_at >= bas,
                    AiCevapBildirimi.created_at < bit).count())


@router.post("/mesajlar/{mesaj_id}/bildir")
def cevabi_bildir(mesaj_id: uuid.UUID, req: AiBildirimReq, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    mesaj = db.get(ChatMessage, mesaj_id)
    if mesaj is None or mesaj.user_id != user.id or mesaj.role != "assistant":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mesaj bulunamadı")
    not_ = (req.not_ or "").strip() or None

    satir = (db.query(AiCevapBildirimi)
             .filter(AiCevapBildirimi.user_id == user.id,
                     AiCevapBildirimi.mesaj_id == mesaj.id).first())
    if satir is None:
        if _bugunku_sayi(db, user) >= SOR_BILDIRIM_GUNLUK:
            return JSONResponse(status_code=status.HTTP_429_TOO_MANY_REQUESTS, content={
                "detail": "Bugün için bildirim sınırına ulaştınız. Yarın tekrar deneyebilirsiniz.",
                "hata": "GUNLUK_LIMIT"})
        soru = db.get(ChatMessage, mesaj.soru_id) if mesaj.soru_id else None
        satir = AiCevapBildirimi(
            user_id=user.id, baby_id=mesaj.baby_id, mesaj_id=mesaj.id, sebep=req.sebep,
            not_=not_, soru_metni=soru.content if soru is not None else None,
            cevap_metni=mesaj.content, durum="yeni")
        db.add(satir)
        try:
            db.commit()
        except IntegrityError:          # eşzamanlı ikinci istek: satırı güncelle
            db.rollback()
            satir = (db.query(AiCevapBildirimi)
                     .filter(AiCevapBildirimi.user_id == user.id,
                             AiCevapBildirimi.mesaj_id == mesaj.id).one())
            satir.sebep, satir.not_, satir.durum = req.sebep, not_, "yeni"
            db.commit()
        yeni = True
    else:
        satir.sebep, satir.not_, satir.durum = req.sebep, not_, "yeni"
        db.commit()
        yeni = False
    logger.info("Sor cevabı bildirildi: user=%s mesaj=%s sebep=%s yeni=%s not=%s",
                user.id, mesaj.id, req.sebep, yeni, "var" if not_ else "yok")
    return {"ok": True}
