"""
Tek kullanıcıya geçici şifre ver (destek aracı — e-posta ile sıfırlama çalışmıyorken).

    railway ssh "cd /app && PYTHONIOENCODING=utf-8 /opt/venv/bin/python scripts/gecici_sifre.py <eposta> [--login-test]"

NE DEĞİŞİR (yalnız bunlar):
  • users.password_hash → "Tavsan" + 4 rastgele rakam (ör. Tavsan4827),
  • kullanıcının açık refresh token'ları revoked=True (tüm cihazlardan çıkış —
    /auth/reset-password ile AYNI davranış),
  • kullanılmamış parola sıfırlama bağlantıları used=True (eski bir bağlantı
    yeni şifreyi ezmesin).

NE DEĞİŞMEZ: bebek, kayıtlar, planlar, ses, videolar, abonelik, topluluk — hiçbiri.
Betik işlemden ÖNCE ve SONRA `user_id` taşıyan her tablodaki satır sayısını ve
kullanıcı satırının (password_hash hariç) tüm alanlarını okur; fark varsa
işlemi GERİ ALIR (commit etmez).

ACCESS TOKEN: durumsuz JWT (ACCESS_TOKEN_EXPIRE_MINUTES, varsayılan 60 dk).
Şema değişikliği olmadan tek tek iptal edilemez; açık bir access token en geç
süresi dolunca düşer, yenileme (refresh) yapılamaz.

GİZLİLİK: şifre YALNIZ terminale basılır. Loglanmaz, dosyaya yazılmaz; betik
Sentry'yi başlatmaz (sentry yalnız api.main'de kurulur). Komut satırına şifre
verilmez (kabuk geçmişine düşmesin) — betik kendisi üretir. `--login-test`
yeni şifreyle YALNIZ /auth/login ucunu dener (veri okumaz) ve açtığı oturumu
hemen /auth/logout ile kapatır.
"""
import argparse
import json
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                                # noqa: E402
from sqlalchemy import String                                 # noqa: E402

load_dotenv(ROOT / ".env")

import api.models as modeller                                 # noqa: E402
from api.db import SessionLocal                               # noqa: E402
from api.models import PasswordResetToken, RefreshToken, User  # noqa: E402
from api.services import security                             # noqa: E402

# Bu işlemin BİLEREK değiştirdiği tablolar — sayım karşılaştırmasına girmez,
# ayrıca raporlanır.
DEGISEN = {"refresh_tokens", "password_reset_tokens"}
LOGIN_URL = "http://127.0.0.1:8080/api/v1"


def _sayimlar(db, user_id) -> dict[str, int]:
    """user_id sütunu olan HER tablodaki satır sayısı (DEGISEN hariç)."""
    out = {}
    for ad in modeller.__all__:
        m = getattr(modeller, ad)
        tablo = m.__table__
        if tablo.name in DEGISEN or "user_id" not in tablo.c or ad == "User":
            continue
        # Bazı tablolarda user_id METİN (ör. plan_uretim_isleri). Postgres
        # varchar = uuid karşılaştırmasını reddeder; SQLite sessizce geçer.
        kolon = tablo.c.user_id
        deger = str(user_id) if isinstance(kolon.type, String) else user_id
        out[tablo.name] = db.query(m).filter(kolon == deger).count()
    return out


def _kullanici_satiri(u: User) -> dict:
    # updated_at: TimestampMixin şifre değişince onu kendiliğinden yeniler —
    # değişikliğin damgasıdır, kullanıcı verisi değil.
    return {c.name: str(getattr(u, c.name)) for c in User.__table__.columns
            if c.name not in ("password_hash", "updated_at")}


def _post(yol: str, govde: dict, tok: str | None = None) -> tuple[int, dict | str]:
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(LOGIN_URL + yol, data=json.dumps(govde).encode(),
                               headers=h, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("eposta")
    ap.add_argument("--login-test", action="store_true",
                    help="yeni şifreyle yalnız /auth/login'i dene, oturumu kapat")
    a = ap.parse_args()
    eposta = a.eposta.strip().lower()

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == eposta).one_or_none()
        if u is None:
            print(f"Kullanıcı bulunamadı: {eposta} — hiçbir şey değişmedi.")
            return 1
        once = _sayimlar(db, u.id)
        satir_once = _kullanici_satiri(u)
        aktif_oturum = db.query(RefreshToken).filter(
            RefreshToken.user_id == u.id, RefreshToken.revoked == False).count()  # noqa: E712
        acik_baglanti = db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == u.id, PasswordResetToken.used == False).count()  # noqa: E712

        sifre = "Tavsan" + "".join(str(secrets.randbelow(10)) for _ in range(4))
        u.password_hash = security.hash_password(sifre)
        db.query(RefreshToken).filter(
            RefreshToken.user_id == u.id, RefreshToken.revoked == False  # noqa: E712
        ).update({"revoked": True}, synchronize_session=False)
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == u.id, PasswordResetToken.used == False  # noqa: E712
        ).update({"used": True}, synchronize_session=False)
        db.flush()

        sonra = _sayimlar(db, u.id)
        satir_sonra = _kullanici_satiri(u)
        if once != sonra or satir_once != satir_sonra:
            db.rollback()
            print("HATA — kullanıcı verisinde beklenmeyen fark; işlem GERİ ALINDI.")
            print("önce :", once, "\nsonra:", sonra)
            return 2
        db.commit()

        # Commit SONRASI bağımsız okuma: gerçekten kalıcı hâl.
        db.expire_all()
        kalici = _sayimlar(db, u.id)
        kalan_oturum = db.query(RefreshToken).filter(
            RefreshToken.user_id == u.id, RefreshToken.revoked == False).count()  # noqa: E712

        print(f"Kullanıcı: {eposta}")
        print(f"{'tablo':28s} {'önce':>6s} {'sonra':>6s}")
        for t in sorted(once):
            if once[t] or kalici[t]:
                print(f"{t:28s} {once[t]:6d} {kalici[t]:6d}"
                      + ("" if once[t] == kalici[t] else "   ← FARK"))
        bos = sorted(t for t in once if not once[t] and not kalici[t])
        print(f"(0 satırlı tablolar: {len(bos)} — aynı)")
        print(f"kullanıcı satırı (password_hash hariç) aynı: {satir_once == _kullanici_satiri(u)}")
        print(f"veri sayıları aynı: {once == kalici}")
        print(f"iptal edilen oturum (refresh token): {aktif_oturum} → kalan açık: {kalan_oturum}")
        print(f"iptal edilen kullanılmamış sıfırlama bağlantısı: {acik_baglanti}")

        if a.login_test:
            s, r = _post("/auth/login", {"email": eposta, "password": sifre})
            print(f"login testi: HTTP {s}")
            if s == 200 and isinstance(r, dict):
                s2, _ = _post("/auth/logout", {"refresh_token": r["refresh_token"]},
                              r["access_token"])
                print(f"test oturumu kapatıldı: HTTP {s2}")

        print("\n" + "=" * 40)
        print(f"GEÇİCİ ŞİFRE: {sifre}")
        print("=" * 40)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
