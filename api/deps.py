"""
Ortak FastAPI bağımlılıkları.

get_current_user: Authorization: Bearer <access_token> başlığını çözer, geçerliyse
ilgili User ORM nesnesini döner; aksi halde 401. Faz 3/4'teki TÜM korumalı
router'lar bunu Depends ile kullanır (kullanıcı kendi verisiyle sınırlanır).
"""
import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

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


def get_owned_baby(baby_id, db: Session, user: User):
    """baby_id kullanıcıya ait mi? Değilse/yoksa 404 (kullanıcı kendi verisiyle sınırlı).
    404 (403 değil) → başka kullanıcının kayıt varlığını sızdırmaz."""
    from api.models import Baby            # döngüsel import önleme
    baby = db.get(Baby, baby_id)
    if baby is None or baby.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Bebek bulunamadı")
    return baby
