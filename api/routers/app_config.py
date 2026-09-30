"""
GET /api/v1/config — sunucudan yönetilen istemci ayarları (build gerekmeden).

Şimdilik tek anahtar: {"ui": {"yeni_tasarim": bool}}.

Karar sırası:
  1. Oturumlu kullanıcının e-postası UI_YENI_TASARIM_KULLANICILAR içinde → True
  2. değilse UI_YENI_TASARIM (true/false, varsayılan false)

Oturum İSTEĞE BAĞLI: token yok/bozuk/süresi dolmuş → genel bayrak.

ÖNBELLEK: yanıt kullanıcıya göre değişebildiği için `private` (paylaşılan
önbellek/CDN saklamaz) + `Vary: Authorization`; istemci 5 dk tutabilir.
"""
from fastapi import APIRouter, Depends, Response

from api.config import get_settings
from api.deps import get_optional_user
from api.models import User

router = APIRouter(tags=["config"])

ONBELLEK_SN = 300


def yeni_tasarim_acik_mi(user: User | None) -> bool:
    ayar = get_settings()
    if user is not None and (user.email or "").strip().lower() in ayar.ui_yeni_tasarim_kullanicilar:
        return True
    return ayar.ui_yeni_tasarim


@router.get("/config")
def istemci_ayarlari(response: Response,
                     user: User | None = Depends(get_optional_user)) -> dict:
    response.headers["Cache-Control"] = f"private, max-age={ONBELLEK_SN}"
    response.headers["Vary"] = "Authorization"
    return {"ui": {"yeni_tasarim": yeni_tasarim_acik_mi(user)}}
