"""
Ortak FastAPI bağımlılıkları.

get_current_user: Authorization: Bearer <access_token> başlığını çözer, geçerliyse
ilgili User ORM nesnesini döner; aksi halde 401. Faz 3/4'teki TÜM korumalı
router'lar bunu Depends ile kullanır (kullanıcı kendi verisiyle sınırlanır).
"""
import secrets
import uuid

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_db
from api.models import User
from api.services.security import decode_access_token

# auto_error=False → başlık yoksa kendi 401'imizi tutarlı biçimde döneriz.
_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Geçersiz veya süresi dolmuş oturum",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or not creds.credentials:
        raise _UNAUTHORIZED
    payload = decode_access_token(creds.credentials)
    if payload is None:
        raise _UNAUTHORIZED
    sub = payload.get("sub")
    if not sub:
        raise _UNAUTHORIZED
    try:
        user_id = uuid.UUID(str(sub))
    except (ValueError, TypeError):
        raise _UNAUTHORIZED
    user = db.get(User, user_id)
    if user is None:                      # silinmiş kullanıcı → token geçersiz
        raise _UNAUTHORIZED
    return user


def require_demo_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    """Demo endpoint'leri (/ask, /avatar-session) için paylaşılan anahtar koruması.

    Bu iki endpoint auth'suzdu ve doğrudan LLM (para) / LiveAvatar (kredi) tetikliyordu;
    public deploy'da kötüye kullanıma açıktı. Faz 5R:
      - DEMO_API_KEY tanımsız → endpoint KAPALI (503). Production'da güvenli varsayılan.
      - Tanımlı → X-API-Key başlığı birebir eşleşmeli (sabit-zaman karşılaştırma), yoksa 401.
    Mobil v1 bu endpoint'leri KULLANMAZ; yalnız avatar.html demo sayfası içindir."""
    settings = get_settings()
    if not settings.demo_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo endpoint devre dışı (DEMO_API_KEY tanımlı değil)",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.demo_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Geçersiz veya eksik X-API-Key",
        )


def get_owned_baby(baby_id, db: Session, user: User):
    """baby_id kullanıcıya ait mi? Değilse/yoksa 404 (kullanıcı kendi verisiyle sınırlı).
    404 (403 değil) → başka kullanıcının kayıt varlığını sızdırmaz."""
    from api.models import Baby            # döngüsel import önleme
    baby = db.get(Baby, baby_id)
    if baby is None or baby.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Bebek bulunamadı")
    return baby
