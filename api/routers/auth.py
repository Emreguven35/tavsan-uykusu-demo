"""
Auth router — /api/v1/auth/*

Akış (İDA pattern'i): email+parola → bcrypt hash → user + access/refresh token çifti.
Refresh token DB'de (hash) saklanır; refresh'te ROTASYON yapılır (eski iptal, yeni ver).
Bu, logout ve "tüm cihazlardan çıkış"ı (revoked=True) mümkün kılar.

Access token 1 saat, refresh 30 gün (config'ten).
Sosyal giriş (Google/Apple): BU FAZDA YOK — TODO (aşağıda işaretli).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_db
from api.deps import get_current_user
from api.models import PasswordResetToken, RefreshToken, User
from api.schemas.auth import (
    LoginReq, MessageResp, RefreshReq, RegisterReq, ResetPasswordReq,
    ResetPasswordRequestReq, ResetPasswordRequestResp, TokenPair,
)
from api.services import security

logger = logging.getLogger("tavsan.auth")
settings = get_settings()

router = APIRouter(prefix="/auth", tags=["auth"])

# Kullanıcı bulunamadığında login'de sabit-zaman için kullanılan sahte hash
# (kullanıcı sayımı/timing sızıntısını azaltır). Geçerli bir bcrypt hash olmalı.
_DUMMY_HASH = security.hash_password("zaman-esitleyici-sahte-parola")


def _issue_token_pair(db: Session, user: User) -> TokenPair:
    """Yeni access (JWT) + refresh (opak, hash'i DB'ye) çifti üret."""
    access = security.create_access_token(str(user.id))
    raw_refresh = security.generate_opaque_token()
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=security.hash_token(raw_refresh),
        expires_at=security.refresh_expiry(),
        revoked=False,
    ))
    db.commit()
    return TokenPair(
        access_token=access,
        refresh_token=raw_refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def register(req: RegisterReq, db: Session = Depends(get_db)):
    email = req.email.strip().lower()
    user = User(email=email, password_hash=security.hash_password(req.password))
    db.add(user)
    try:
        db.flush()                       # unique ihlali burada patlar
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Bu e-posta zaten kayıtlı")
    logger.info("Yeni kullanıcı kaydı: user_id=%s", user.id)   # KVKK: e-posta loglanmaz
    return _issue_token_pair(db, user)


@router.post("/login", response_model=TokenPair)
def login(req: LoginReq, db: Session = Depends(get_db)):
    email = req.email.strip().lower()
    user = db.query(User).filter(User.email == email).one_or_none()
    # Sabit-zaman: kullanıcı yoksa da bir verify çalıştır, sonra 401.
    if user is None:
        security.verify_password(req.password, _DUMMY_HASH)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="E-posta veya parola hatalı")
    if not security.verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="E-posta veya parola hatalı")
    logger.info("Giriş: user_id=%s", user.id)
    return _issue_token_pair(db, user)


@router.post("/refresh", response_model=TokenPair)
def refresh(req: RefreshReq, db: Session = Depends(get_db)):
    """Geçerli refresh token → yeni access + YENİ refresh (rotasyon). Eski iptal edilir."""
    token_hash = security.hash_token(req.refresh_token)
    row = db.query(RefreshToken).filter(
        RefreshToken.token_hash == token_hash).one_or_none()
    if row is None or row.revoked or security.is_expired(row.expires_at):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Geçersiz veya süresi dolmuş refresh token")
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Kullanıcı bulunamadı")
    row.revoked = True                   # rotasyon: eski refresh tek kullanımlık
    db.add(row)
    return _issue_token_pair(db, user)


@router.post("/logout", response_model=MessageResp)
def logout(req: RefreshReq, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    """Verilen refresh token'ı iptal et (bu cihazdan çıkış). Access token stateless
    olduğundan kalan ~1 saat içinde geçerli kalır; istemci onu da siler."""
    token_hash = security.hash_token(req.refresh_token)
    row = db.query(RefreshToken).filter(
        RefreshToken.token_hash == token_hash,
        RefreshToken.user_id == user.id).one_or_none()
    if row is not None and not row.revoked:
        row.revoked = True
        db.add(row)
        db.commit()
    return MessageResp(detail="Çıkış yapıldı")


@router.post("/reset-password-request", response_model=ResetPasswordRequestResp)
def reset_password_request(req: ResetPasswordRequestReq, db: Session = Depends(get_db)):
    """Sıfırlama token'ı üret + hash'ini DB'ye yaz.

    Faz 5R: ham token artık YANITTA DÖNMEZ ve LOGLANMAZ. E-posta kanalı (Resend)
    Faz 6'da bağlanana kadar token kullanıcıya ulaşamaz — akış kasıtlı olarak
    tamamlanamaz durumdadır (mobilde "yakında"). Bu, e-postasını bilen birinin
    hesabı ele geçirmesini engeller.

    Kullanıcı sayımını sızdırmamak için e-posta kayıtlı olmasa bile aynı yanıt döner."""
    email = req.email.strip().lower()
    user = db.query(User).filter(User.email == email).one_or_none()
    generic = "Eğer bu e-posta kayıtlıysa, sıfırlama talimatı gönderildi"
    if user is None:
        return ResetPasswordRequestResp(detail=generic)

    raw = security.generate_opaque_token()
    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=security.hash_token(raw),
        expires_at=security.reset_expiry(hours=1),
        used=False,
    ))
    db.commit()
    # KVKK + güvenlik: ham token hiçbir yere (yanıt/log) yazılmaz; yalnız hash'i DB'de.
    logger.info("Parola sıfırlama talebi: user_id=%s", user.id)
    del raw
    # TODO(Faz 6 / Resend): raw token'ı e-posta ile gönder (yanıta EKLEME).
    return ResetPasswordRequestResp(detail=generic)


@router.post("/reset-password", response_model=MessageResp)
def reset_password(req: ResetPasswordReq, db: Session = Depends(get_db)):
    """Ham sıfırlama token'ı + yeni parola → parolayı değiştir, token'ı kullanılmış işaretle.
    Güvenlik: tüm mevcut refresh token'lar iptal edilir (parola değişince oturumlar düşer)."""
    token_hash = security.hash_token(req.token)
    row = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == token_hash).one_or_none()
    if row is None or row.used or security.is_expired(row.expires_at):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Geçersiz veya süresi dolmuş sıfırlama token'ı")
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Kullanıcı bulunamadı")
    user.password_hash = security.hash_password(req.new_password)
    row.used = True
    # Parola değişti → tüm refresh token'ları iptal et (tüm cihazlardan çıkış).
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user.id, RefreshToken.revoked == False  # noqa: E712
    ).update({"revoked": True})
    db.add(user)
    db.add(row)
    db.commit()
    logger.info("Parola sıfırlandı: user_id=%s", user.id)
    return MessageResp(detail="Parola güncellendi")


@router.delete("/account", response_model=MessageResp)
def delete_account(db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """KVKK silme hakkı: kullanıcı + ilişkili TÜM veriler (babies, logs, plans,
    subscriptions, chat, voice, tokens) cascade ile silinir."""
    user_id = user.id
    db.delete(user)                      # relationship cascade + FK ON DELETE CASCADE
    db.commit()
    logger.info("Hesap silindi (KVKK): user_id=%s", user_id)
    return MessageResp(detail="Hesap ve tüm ilişkili veriler silindi")


# TODO(sosyal giriş): Google/Apple OAuth bu fazda YOK. İleride /auth/google ve
# /auth/apple eklenip id_token doğrulaması + aynı _issue_token_pair akışı kullanılacak.
