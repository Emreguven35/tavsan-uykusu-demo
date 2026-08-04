"""
Tek seferlik admin script'i (Faz T): bir kullanıcıya is_expert + is_moderator ver.
İlayda ve Emre'nin hesaplarına uzman/moderatör rozeti tanımlamak için.

Kullanım (railway ssh / lokal):
    python scripts/grant_moderator.py --email ilayda@example.com
    python scripts/grant_moderator.py --email emre@example.com --nickname "İlayda 🐰"
    python scripts/grant_moderator.py --email x@y.com --revoke   # yetkiyi geri al

Topluluk profili yoksa OLUŞTURUR (verilen nickname ya da e-posta yerel adından türetilmiş,
çakışırsa sonuna sayı eklenir). DATABASE_URL env'den okunur (railway run/ssh ile prod).
"""
import argparse
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from api.db import SessionLocal          # noqa: E402
from api.models import CommunityProfile, User  # noqa: E402


def _unique_nickname(db, base: str) -> str:
    base = (base or "Moderatör").strip()[:20] or "Moderatör"
    cand = base
    i = 1
    while db.query(CommunityProfile).filter(CommunityProfile.nickname == cand).first():
        i += 1
        cand = f"{base}{i}"[:24]
    return cand


def main() -> int:
    ap = argparse.ArgumentParser(description="Kullanıcıya uzman+moderatör yetkisi ver.")
    ap.add_argument("--email", required=True)
    ap.add_argument("--nickname", default=None, help="Profil yoksa kullanılacak takma ad")
    ap.add_argument("--revoke", action="store_true", help="Yetkiyi geri al (is_expert/is_moderator=False)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        email = args.email.strip().lower()
        user = db.query(User).filter(User.email == email).one_or_none()
        if user is None:
            print(f"HATA: kullanıcı bulunamadı: {email}")
            return 2

        prof = db.query(CommunityProfile).filter(
            CommunityProfile.user_id == user.id).one_or_none()
        if prof is None:
            nick = _unique_nickname(db, args.nickname or email.split("@")[0])
            prof = CommunityProfile(user_id=user.id, nickname=nick,
                                    rules_accepted_at=datetime.now(timezone.utc))
            db.add(prof)
            print(f"Profil oluşturuldu: nickname={nick}")

        prof.is_expert = not args.revoke
        prof.is_moderator = not args.revoke
        db.commit()
        db.refresh(prof)
        durum = "GERİ ALINDI" if args.revoke else "VERİLDİ"
        print(f"{durum}: {email} → is_expert={prof.is_expert} "
              f"is_moderator={prof.is_moderator} nickname={prof.nickname}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
