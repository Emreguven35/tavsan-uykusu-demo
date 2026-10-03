"""
GET /api/v1/config — sunucudan yönetilen istemci ayarları (build gerekmeden).

Şimdilik tek anahtar: {"ui": {"yeni_tasarim": bool}}.

Karar sırası:
  1. Oturumlu kullanıcının e-postası UI_YENI_TASARIM_KULLANICILAR içinde → True
  2. İstemcinin build numarası (X-App-Version başlığı, yoksa users.app_version)
     UI_YENI_TASARIM_MIN_BUILD'den (varsayılan 26) küçük ya da bilinmiyorsa → False
  3. değilse UI_YENI_TASARIM (true/false, varsayılan false)

Oturum İSTEĞE BAĞLI: token yok/bozuk/süresi dolmuş → genel bayrak.

ÖNBELLEK: yanıt kullanıcıya göre değişebildiği için `private` (paylaşılan
önbellek/CDN saklamaz) + `Vary: Authorization, X-App-Version`; istemci 5 dk
tutabilir.
"""
from fastapi import APIRouter, Depends, Header, Response

from api.config import get_settings
from api.deps import get_optional_user
from api.models import User
from api.services.denetim import surum_build

router = APIRouter(tags=["config"])

ONBELLEK_SN = 300


def istemci_build(user: User | None, x_app_version: str | None) -> int | None:
    """Önce isteğin başlığı (o anki uygulama), yoksa son görülen sürüm."""
    build = surum_build((x_app_version or "").strip() or None)
    if build is None and user is not None:
        build = surum_build(user.app_version)
    return build


def yeni_tasarim_acik_mi(user: User | None, x_app_version: str | None = None) -> bool:
    ayar = get_settings()
    if user is not None and (user.email or "").strip().lower() in ayar.ui_yeni_tasarim_kullanicilar:
        return True
    build = istemci_build(user, x_app_version)
    if build is None or build < ayar.ui_yeni_tasarim_min_build:
        return False
    return ayar.ui_yeni_tasarim


@router.get("/config")
def istemci_ayarlari(response: Response,
                     user: User | None = Depends(get_optional_user),
                     x_app_version: str | None = Header(default=None,
                                                        alias="X-App-Version")) -> dict:
    response.headers["Cache-Control"] = f"private, max-age={ONBELLEK_SN}"
    response.headers["Vary"] = "Authorization, X-App-Version"
    return {"ui": {"yeni_tasarim": yeni_tasarim_acik_mi(user, x_app_version)}}
