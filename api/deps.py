"""
Ortak FastAPI bağımlılıkları.

get_current_user: Authorization: Bearer <access_token> başlığını çözer, geçerliyse
ilgili User ORM nesnesini döner; aksi halde 401. Faz 3/4'teki TÜM korumalı
router'lar bunu Depends ile kullanır (kullanıcı kendi verisiyle sınırlanır).

require_premium: premium uçların TEK kapısı (Faz V). Abonelik YA DA BETA_MODE
ile geçer; geçmezse 403 + Türkçe detail. Kapıyı her router'ın kendince
kurmaması bilinçli: kapı çoğalınca bir ucu açık kalıyordu.
"""
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_db
from api.observability import kullaniciyi_isaretle
from api.models import Subscription, User
from api.services.security import decode_access_token

# auto_error=False → başlık yoksa kendi 401'imizi tutarlı biçimde döneriz.
_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Geçersiz veya süresi dolmuş oturum",
    headers={"WWW-Authenticate": "Bearer"},
)


# X-App-Version (ör. "1.0.0+21"). Biçim dışı değer yazılmaz (DB'ye çöp gitmez).
_SURUM = re.compile(r"^[0-9A-Za-z.+_-]{1,40}$")
# Aynı sürüm tekrar geldiğinde "son görülme" en fazla bu sıklıkla yazılır —
# her istekte UPDATE atmamak için.
SURUM_YAZMA_ARALIGI = timedelta(hours=1)


def surum_kaydet(db: Session, user: User, surum: str | None) -> None:
    """Denetim B3 — annenin son görülen uygulama sürümü. EN İYİ ÇABA: yazılamazsa
    istek düşmez."""
    if not surum or not _SURUM.match(surum.strip()):
        return
    surum = surum.strip()
    simdi = datetime.now(timezone.utc)
    son = user.app_version_seen_at
    if son is not None and son.tzinfo is None:
        son = son.replace(tzinfo=timezone.utc)
    if user.app_version == surum and son is not None and simdi - son < SURUM_YAZMA_ARALIGI:
        return
    try:
        user.app_version = surum
        user.app_version_seen_at = simdi
        db.commit()
    except Exception:
        db.rollback()


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    x_app_version: str | None = Header(default=None, alias="X-App-Version"),
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
    # Hata izlemede "kim etkilendi" görünsün ama KİMLİĞİ görünmesin: yalnız
    # tuzlanmış hash gider (e-posta ASLA). Sentry kapalıysa no-op.
    kullaniciyi_isaretle(user.id)
    surum_kaydet(db, user, x_app_version)
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
# --- Premium kapısı (Faz V) --------------------------------------------------
# Mobil BETA_MODE'da herkesi premium sayıyor, sunucuda ise premium kararı YALNIZ
# GET /subscriptions/status içinde gömülüydü ve hiçbir uç onu ZORLAMIYORDU.
# Karar artık burada, TEK yerde: hem /subscriptions/status hem premium uçlar
# aynı fonksiyondan besleniyor — iki taraf ayrışamaz.
PREMIUM_GEREKLI_MESAJ = "Bu özellik Premium üyelik gerektiriyor."


def premium_karari(db: Session, user: User) -> tuple[bool, str]:
    """(premium_mi, kaynak) — premium kararının TEK kaynağı.

    kaynak: "beta" (BETA_MODE açık) | "subscription" (aktif abonelik) | "none".

    BETA_MODE önce bakılır ve DB'ye hiç gidilmez: beta süresince her annenin
    aboneliği yok, olmaması da gerekmiyor."""
    if get_settings().beta_mode:
        return True, "beta"
    aktif = (db.query(Subscription)
             .filter(Subscription.user_id == user.id,
                     Subscription.status == "active")
             .first())
    if aktif is not None:
        return True, "subscription"
    return False, "none"


def require_premium(db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> User:
    """Premium uçların bağımlılığı: `user: User = Depends(require_premium)`.

    get_current_user'ın YERİNE kullanılır (onu kendisi çağırır, FastAPI aynı
    istekte tekrar çözmez) — böylece uçlar iki bağımlılık taşımaz ve kapıyı
    eklemeyi unutmak zorlaşır.

    Geçmezse 403 + Türkçe detail: mobil `detail`i OLDUĞU GİBİ gösteriyor, bu
    yüzden gövde boş ya da İngilizce bırakılamaz."""
    premium, _kaynak = premium_karari(db, user)
    if not premium:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail=PREMIUM_GEREKLI_MESAJ)
    return user
