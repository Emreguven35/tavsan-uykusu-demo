"""
users router — /api/v1/users/me (Topluluk v2, 2026-10-01).

Şimdilik yalnız avatar: tavsan | ayi | kedi | civciv | tilki. Seçilmemişse
kullanıcı kimliğinden SABİT bir seçim döner (topluluk.avatar_of) — aynı anne
her ekranda aynı hayvanla görünür.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user
from api.models import User
from api.schemas.community import AvatarUpdateReq, MeResp
from api.services import topluluk

router = APIRouter(prefix="/users", tags=["users"])


def _me(user: User) -> MeResp:
    return MeResp(id=user.id, avatar=topluluk.avatar_of(user.id, user.avatar),
                  avatar_secildi=user.avatar in topluluk.AVATARLAR)


@router.get("/me", response_model=MeResp)
def get_me(user: User = Depends(get_current_user)):
    return _me(user)


@router.patch("/me", response_model=MeResp)
def update_me(req: AvatarUpdateReq, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    user.avatar = req.avatar
    db.commit()
    db.refresh(user)
    return _me(user)
