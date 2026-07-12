"""
Güvenlik yardımcıları — parola hash'i (bcrypt), JWT access token (jose), ve
opak refresh/reset token üretimi + hash'leme.

Tasarım:
  - Access token: JWT (HS256), stateless, `sub`=user_id, `type`=access, 1 saat.
    İçinde kullanıcı kimliği taşır; her istekte doğrulanır (DB'ye bakmadan).
  - Refresh token: OPAK rastgele string (JWT değil). DB'de yalnız SHA-256 hash'i
    saklanır; refresh'te ham token hash'lenip DB'de aranır (revoked/expiry kontrolü).
    Böylece logout / tüm cihazlardan çıkış mümkün (bkz. models/refresh_token.py).
  - Reset token: aynı opak+hash deseni.

Sırlar env'den (api.config). Hiçbir sır koda gömülü DEĞİL.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from api.config import get_settings

settings = get_settings()

# bcrypt — passlib CryptContext. (bcrypt<4.1 pinlendi; requirements'ta gerekçe var.)
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

ACCESS_TOKEN_TYPE = "access"


# --- Parola ------------------------------------------------------------------
def hash_password(plain: str) -> str:
    # bcrypt yalnız ilk 72 BYTE'ı dikkate alır; router şema seviyesinde max uzunluk
    # sınırlar. Burada ekstra kesme yapmayız (passlib kendi yönetir).
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd.verify(plain, hashed)
    except ValueError:
        # bozuk/uyumsuz hash → doğrulama başarısız (çökme yok)
        return False


# --- Access token (JWT) ------------------------------------------------------
def create_access_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(subject),
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    """Geçerli+süresi dolmamış access token ise payload döner; aksi halde None."""
    try:
        payload = jwt.decode(token, settings.jwt_secret,
                             algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        return None
    return payload


# --- Opak token'lar (refresh / reset) ----------------------------------------
def generate_opaque_token() -> str:
    """URL-güvenli, tahmin edilemez rastgele token (ham — istemciye verilir)."""
    return secrets.token_urlsafe(48)


def hash_token(raw: str) -> str:
    """Ham opak token'ın SHA-256 hex digest'i (DB'de saklanan/karşılaştırılan değer)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def refresh_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)


def reset_expiry(hours: int = 1) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def is_expired(expires_at: datetime) -> bool:
    """expires_at geçti mi? DB tz-aware (postgres timestamptz) ya da naive (sqlite)
    dönebilir; naive değeri UTC kabul ederiz (üretimde hep UTC yazıyoruz)."""
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expires_at
