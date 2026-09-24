"""
sounds router — /api/v1/sounds

GET /sounds    uyku sesleri kataloğu (kategoriler, premium kilidi)

Dosyalar buradan sunulmaz — public `/media/sounds/{slug}.m4a` ucundadır
(bkz. api/routers/media.py). İş mantığı api/services/sounds.py içinde.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user, premium_karari
from api.models import User
from api.schemas.sounds import UykuSesleriResp
from api.services import sounds

router = APIRouter(prefix="/sounds", tags=["sounds"])


@router.get("", response_model=UykuSesleriResp)
def uyku_sesleri(db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """Tüm sesler HER ZAMAN listelenir; premium olmayan kullanıcıda ücretli
    sesler `locked: true` gelir (mobil kilit rozeti gösterir). Premium kararı
    deps.premium_karari'dan — BETA_MODE'da herkes premium."""
    premium, _kaynak = premium_karari(db, user)
    return sounds.katalog(db, premium)
