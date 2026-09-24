"""
denetim router — İlayda günlük denetim sayfası (İMZALI, giriş yok).

    GET /denetim/{tarih}.html?exp=…&sig=…

Bağlantı 7 gün geçerli HMAC imzası taşır (storage.imzala, JWT_SECRET'tan
türetilmiş anahtar). Geçersiz imzada dosyanın VAR OLUP OLMADIĞI sızdırılmaz:
önce imza, sonra dosya kontrol edilir (/media ile aynı kural).
"""
import re
from datetime import date

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from api.services import denetim, storage

router = APIRouter(prefix="/denetim", tags=["denetim"])
_TARIH = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@router.get("/{tarih}.html", include_in_schema=False)
def denetim_sayfasi(tarih: str, exp: int = 0, sig: str = ""):
    if not _TARIH.match(tarih):
        raise HTTPException(status_code=400, detail="geçersiz tarih")
    try:
        gun = date.fromisoformat(tarih)
    except ValueError:
        raise HTTPException(status_code=400, detail="geçersiz tarih") from None
    if not storage.imza_gecerli_mi(denetim.imza_yolu(gun), exp, sig):
        raise HTTPException(status_code=403,
                            detail="Bağlantının süresi doldu ya da geçersiz")
    yol = denetim.dosya_yolu(gun)
    if not yol.exists():
        raise HTTPException(status_code=404, detail="rapor bulunamadı")
    return FileResponse(yol, media_type="text/html; charset=utf-8", headers={
        "Cache-Control": "private, no-store",
        "X-Robots-Tag": "noindex, nofollow",
        "Referrer-Policy": "no-referrer",
    })
