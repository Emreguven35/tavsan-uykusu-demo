"""
GET /api/v1/config — arayüz anahtarı (yeni tasarım) sözleşmesi.

LLM YOK, ağ YOK, prod DB YOK: geçici sqlite + FastAPI TestClient.

  A  Anonim + UI_YENI_TASARIM=false            → false
  B  Listedeki kullanıcı (büyük/küçük harf)    → true
  C  Listede olmayan oturumlu kullanıcı        → false (genel bayrak)
  D  Bozuk / süresi dolmuş token               → 200 + genel bayrak (401 DEĞİL)
  E  UI_YENI_TASARIM=true                      → herkese true
  F  Cache-Control private, max-age=300 + Vary: Authorization
  G  Yanıt şekli tam olarak {"ui": {"yeni_tasarim": bool}}
  H  UI_YENI_TASARIM_MIN_BUILD=26: build 25 → false, 26 → true, sürüm yok →
     false, listedeki kullanıcı → true; başlık yoksa users.app_version

Çalıştırma: python tests/test_ui_config.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "ui_config_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["UI_YENI_TASARIM"] = "false"
os.environ["UI_YENI_TASARIM_KULLANICILAR"] = " Secili@Example.com , diger@example.com"

from fastapi.testclient import TestClient              # noqa: E402
from api.config import get_settings                    # noqa: E402
from api.db import Base, engine                        # noqa: E402
import api.models                                      # noqa: E402,F401
from api.main import app                               # noqa: E402

Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


def kayit(eposta: str) -> dict:
    tok = client.post("/api/v1/auth/register",
                      json={"email": eposta, "password": "TestPass123!"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def acik(headers: dict | None = None) -> bool:
    return client.get("/api/v1/config", headers=headers or {}).json()["ui"]["yeni_tasarim"]


H_SECILI = kayit("secili@example.com")
H_DIGER = kayit("baskasi@example.com")

r = client.get("/api/v1/config")
check("A1) Anonim → 200", r.status_code == 200, r.text)
check("A2) Anonim + genel bayrak kapalı → false", r.json()["ui"]["yeni_tasarim"] is False, r.text)
check("B1) Listedeki kullanıcı → true (liste büyük harfli + boşluklu yazıldı)",
      acik(H_SECILI) is True)
check("C1) Listede olmayan oturumlu kullanıcı → false", acik(H_DIGER) is False)

rb = client.get("/api/v1/config", headers={"Authorization": "Bearer bozuk.token.degeri"})
check("D1) Bozuk token → 200 (401 değil)", rb.status_code == 200, rb.text)
check("D2) Bozuk token → genel bayrak (false)", rb.json()["ui"]["yeni_tasarim"] is False)

h = r.headers
check("F1) Cache-Control: private, max-age=300",
      h.get("cache-control") == "private, max-age=300", h.get("cache-control"))
check("F2) Vary: Authorization, X-App-Version",
      "authorization" in (h.get("vary") or "").lower()
      and "x-app-version" in (h.get("vary") or "").lower(), h.get("vary"))
check("G1) Yanıt şekli tam {'ui': {'yeni_tasarim': bool}}",
      r.json() == {"ui": {"yeni_tasarim": False}}, r.text)

# E — genel bayrak açılınca build ≥ 26 herkese true (Settings süreç başına önbellekli).
V26 = {"X-App-Version": "1.0.0+26"}
os.environ["UI_YENI_TASARIM"] = "true"
get_settings.cache_clear()
check("E1) UI_YENI_TASARIM=true → anonim (build 26) true", acik(V26) is True)
check("E2) UI_YENI_TASARIM=true → listede olmayan kullanıcı (build 26) true",
      acik({**H_DIGER, **V26}) is True)

# H — en düşük build koşulu.
check("H1) build 25 → false", acik({**H_DIGER, "X-App-Version": "1.0.0+25"}) is False)
check("H2) build 26 → true", acik({**H_DIGER, "X-App-Version": "1.0.0+26"}) is True)
check("H3) build 30 → true", acik({**H_DIGER, "X-App-Version": "1.1.0+30"}) is True)
H_SURUMSUZ = kayit("surumsuz@example.com")
check("H4) sürüm yok (başlık yok, app_version yok) → false", acik(H_SURUMSUZ) is False)
check("H5) build numarasız sürüm ('1.0.0') → false",
      acik({**H_SURUMSUZ, "X-App-Version": "1.0.0"}) is False)
check("H6) anonim, başlıksız → false", acik() is False)
check("H7) listedeki kullanıcı sürümsüz / build 25 → true",
      acik(H_SECILI) is True and acik({**H_SECILI, "X-App-Version": "1.0.0+25"}) is True)
# Başlık yoksa son görülen sürüm (users.app_version) kullanılır: kimlikli bir
# uca X-App-Version ile gidince kaydedilir.
client.get("/api/v1/users/me", headers={**H_SURUMSUZ, "X-App-Version": "1.0.0+27"})
check("H8) başlıksız istek users.app_version (build 27) ile → true", acik(H_SURUMSUZ) is True)
client.get("/api/v1/users/me", headers={**H_DIGER, "X-App-Version": "1.0.0+24"})
check("H9) başlıksız istek users.app_version (build 24) ile → false", acik(H_DIGER) is False)
check("H10) başlık kayıtlı sürümden önce gelir (kayıt 24, başlık 26 → true)",
      acik({**H_DIGER, **V26}) is True)
os.environ["UI_YENI_TASARIM_MIN_BUILD"] = "28"
get_settings.cache_clear()
check("H11) eşik env'den: MIN_BUILD=28 iken build 27 → false", acik(H_SURUMSUZ) is False)
os.environ["UI_YENI_TASARIM_MIN_BUILD"] = "26"
get_settings.cache_clear()
os.environ["UI_YENI_TASARIM"] = "false"
get_settings.cache_clear()
check("E3) Tekrar kapatınca listedeki hâlâ true, diğeri false",
      acik(H_SECILI) is True and acik(H_DIGER) is False)

print("=" * 72)
print("GET /config — ARAYÜZ ANAHTARI")
print("=" * 72)
gecen = 0
for ad, ok, detay in results:
    print(f"  [{'PASS' if ok else 'FAIL'}] {ad}")
    if not ok and detay:
        print(f"         → {detay[:300]}")
    gecen += ok
print("-" * 72)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
print("=" * 72)
sys.exit(0 if gecen == len(results) else 1)
