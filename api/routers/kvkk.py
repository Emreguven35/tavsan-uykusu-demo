"""
KVKK router (B5)

POST /consents              onay ya da geri çekme (salt ekleme)
GET  /consents/me           tür başına güncel durum + güncel metin sürümü
GET  /consents/metin/{tur}  taslak metin (markdown) + sürümü
GET  /account/export        kullanıcının tüm verisi (JSON indirme), 24 saatte bir

Hesap silme ayrı uçtadır: DELETE /auth/account. İş mantığı api/services/kvkk.py.
"""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.config import KVKK_METIN_SURUMLERI
from api.db import get_db
from api.deps import get_current_user
from api.models import User
from api.services import kvkk, rate_limit

consents_router = APIRouter(prefix="/consents", tags=["kvkk"])
account_router = APIRouter(prefix="/account", tags=["kvkk"])


class OnayReq(BaseModel):
    tur: str = Field(pattern="^(aydinlatma|acik_riza_saglik|pazarlama)$")
    onay: bool
    # Verilmezse GÜNCEL sürüm yazılır. Mobil hangi metni gösterdiyse onu yollar.
    metin_surumu: str | None = Field(default=None, max_length=40)


class OnayResp(BaseModel):
    tur: str
    onay: bool
    metin_surumu: str
    zaman: datetime
    guncel_surum: str


@consents_router.post("", response_model=OnayResp, status_code=status.HTTP_201_CREATED)
def onay_ver(req: OnayReq, request: Request, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    c = kvkk.onay_kaydet(db, user, req.tur, req.onay, req.metin_surumu,
                         rate_limit.client_ip(request))
    return OnayResp(tur=c.tur, onay=c.onay, metin_surumu=c.metin_surumu,
                    zaman=c.zaman, guncel_surum=KVKK_METIN_SURUMLERI[c.tur])


@consents_router.get("/me")
def onaylarim(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return jsonable_encoder(kvkk.durum(db, user))


@consents_router.get("/metin/{tur}")
def onay_metni(tur: str):
    """Metin herkese açık (giriş öncesi kayıt ekranında gösterilir)."""
    m = kvkk.metin(tur)
    if m is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Metin bulunamadı")
    return {"tur": tur, "metin_surumu": KVKK_METIN_SURUMLERI[tur], "markdown": m}


@account_router.get("/export")
def disa_aktar(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    kalan = kvkk.export_bekleme(user)
    if kalan > 0:
        saat = max(1, round(kalan / 3600))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(f"Verilerinizin kopyasını 24 saatte bir alabilirsiniz. "
                    f"Yaklaşık {saat} saat sonra tekrar deneyin."),
            headers={"Retry-After": str(kalan)})
    veri = kvkk.disa_aktar(db, user)
    user.son_export_at = datetime.now(timezone.utc)
    db.commit()
    ad = f"tavsan-uykusu-verilerim-{date.today().isoformat()}.json"
    return JSONResponse(content=jsonable_encoder(veri), headers={
        "Content-Disposition": f'attachment; filename="{ad}"',
        "Cache-Control": "no-store"})
