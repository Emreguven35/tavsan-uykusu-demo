"""
UYKU SESLERİ — katalog, premium kilidi, /media/sounds Range, döngü matematiği.
LLM YOK, ağ YOK, ffmpeg YOK (sahte m4a baytları yeter).

Kapsam:
  1  katalog_upsert idempotent; URL slug'dan GÖRELİ türetiliyor
  2  GET /sounds şeması: categories[{key,title,sounds[...]}] + sıra + başlıklar
  3  Premium: abonesiz → ücretli ses locked; abonelik → açık; BETA_MODE → açık
  4  /media/sounds/{slug}.m4a: auth yok, Range 206, 1 yıl cache, 404, 400
  5  ses_uret: dongu_yap dikişi kusursuz (son örnek → ilk örnek ardışık),
     zincirle hedefi aşıyor, N 1024'ün katı ve 600±1 sn

Çalıştırma: python tests/test_uyku_sesleri.py
"""
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "uyku_sesleri_test.db"
if _DB.exists():
    _DB.unlink()
_MEDYA = Path(tempfile.gettempdir()) / "uyku_sesleri_medya"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(_MEDYA)
os.environ.pop("BETA_MODE", None)
os.environ.pop("BETA_PREMIUM_ALL", None)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

import numpy as np                                          # noqa: E402
from fastapi.testclient import TestClient                   # noqa: E402
from api.config import get_settings                         # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import Subscription, User                   # noqa: E402
from api.main import app                                    # noqa: E402
from api.services import sounds, storage                    # noqa: E402
from scripts import ses_uret                                # noqa: E402

from tests.llm_muhuru import muhurle                        # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)
get_settings.cache_clear()

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


# =============================================================================
# 1) KATALOG UPSERT
# =============================================================================
KAYITLAR = [
    dict(slug="beyaz-gurultu", title="Beyaz gürültü", category="gurultu",
         order_in_category=1, is_free=True),
    dict(slug="pembe-gurultu", title="Pembe gürültü", category="gurultu",
         order_in_category=2, is_free=False),
    dict(slug="sssh-beyaz", title="Şşşh + beyaz gürültü", category="karisik",
         order_in_category=2, is_free=False),
    dict(slug="yagmur", title="Yağmur", category="doga",
         order_in_category=1, is_free=True),
]
OLCU = {k["slug"]: {"duration_sec": 600, "bytes": 7_000_000} for k in KAYITLAR}
_db = SessionLocal()
try:
    s1 = sounds.katalog_upsert(_db, KAYITLAR, OLCU)
    s2 = sounds.katalog_upsert(_db, KAYITLAR, OLCU)
finally:
    _db.close()
check("1a) 4 ses eklendi", len(s1["eklenen"]) == 4, str(s1))
check("1b) İkinci koşu değişiklik yapmıyor (idempotent)",
      s2 == {"eklenen": [], "guncellenen": []}, str(s2))

# =============================================================================
# 2) GET /sounds ŞEMASI
# =============================================================================
tok = client.post("/api/v1/auth/register",
                  json={"email": "ses@test.com",
                        "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
r = client.get("/api/v1/sounds", headers=H)
j = r.json() if r.status_code == 200 else {}
check("2a) GET /sounds 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
check("2b) Kategori sırası gurultu → doga → karisik (boşlar gizli)",
      [c["key"] for c in j.get("categories", [])] == ["gurultu", "doga", "karisik"],
      str([c["key"] for c in j.get("categories", [])]))
check("2c) Kategori başlıkları Türkçe",
      [c["title"] for c in j.get("categories", [])]
      == ["Beyaz gürültü", "Doğa", "Karışık"], "")
_ses = j["categories"][0]["sounds"][0] if j else {}
check("2d) Ses alanları tam",
      set(_ses) >= {"id", "slug", "title", "category", "duration_sec",
                    "audio_url", "bytes", "loop", "is_free", "locked"},
      str(sorted(_ses)))
check("2e) audio_url GÖRELİ /media/sounds/{slug}.m4a",
      _ses.get("audio_url") == "/media/sounds/beyaz-gurultu.m4a",
      str(_ses.get("audio_url")))
check("2f) loop: true, süre ve boyut dolu",
      _ses.get("loop") is True and _ses.get("duration_sec") == 600
      and _ses.get("bytes") == 7_000_000, str(_ses))
check("2g) Kategori içi sıra korunuyor",
      [s["slug"] for s in j["categories"][0]["sounds"]]
      == ["beyaz-gurultu", "pembe-gurultu"] if j else False, "")
check("2h) Auth olmadan /sounds → 401",
      client.get("/api/v1/sounds").status_code == 401, "")

# =============================================================================
# 3) PREMIUM KİLİDİ
# =============================================================================
def kilitler() -> dict:
    jj = client.get("/api/v1/sounds", headers=H).json()
    return {s["slug"]: s["locked"] for c in jj["categories"] for s in c["sounds"]}


k = kilitler()
check("3a) Abonesiz: ücretsiz ses AÇIK", k["beyaz-gurultu"] is False, str(k))
check("3b) Abonesiz: ücretli ses KİLİTLİ", k["pembe-gurultu"] is True, str(k))

_db = SessionLocal()
try:
    _u = _db.query(User).filter(User.email == "ses@test.com").one()
    _db.add(Subscription(user_id=_u.id, platform="ios", product_id="test",
                         status="active"))
    _db.commit()
finally:
    _db.close()
k = kilitler()
check("3c) Aktif abonelik → ücretli ses açık", k["pembe-gurultu"] is False, str(k))

tok2 = client.post("/api/v1/auth/register",
                   json={"email": "beta@test.com",
                         "password": "TestPass123!"}).json()["access_token"]
os.environ["BETA_MODE"] = "true"
get_settings.cache_clear()
jb = client.get("/api/v1/sounds",
                headers={"Authorization": f"Bearer {tok2}"}).json()
check("3d) BETA_MODE: abonesiz hesapta hiçbir ses kilitli değil",
      jb["is_premium"] is True
      and not any(s["locked"] for c in jb["categories"] for s in c["sounds"]),
      str(jb.get("is_premium")))
check("3e) BETA_MODE is_free bilgisini SİLMİYOR",
      {s["slug"]: s["is_free"] for c in jb["categories"] for s in c["sounds"]}
      ["pembe-gurultu"] is False, "")
os.environ.pop("BETA_MODE", None)
get_settings.cache_clear()

# =============================================================================
# 4) /media/sounds — Range
# =============================================================================
_VERI = bytes(range(256)) * 300
storage.depo().yaz(storage.uyku_sesi_yolu("beyaz-gurultu"), _VERI)
r = client.get("/media/sounds/beyaz-gurultu.m4a")
check("4a) Auth'suz tam istek 200 + tam dosya",
      r.status_code == 200 and r.content == _VERI, f"{r.status_code}")
check("4b) audio/mp4 + 1 yıl cache",
      r.headers.get("content-type", "").startswith("audio/mp4")
      and "max-age=31536000" in r.headers.get("cache-control", ""),
      f'{r.headers.get("content-type")} | {r.headers.get("cache-control")}')
r = client.get("/media/sounds/beyaz-gurultu.m4a", headers={"Range": "bytes=0-1"})
check("4c) Range bytes=0-1 → 206", r.status_code == 206
      and r.content == _VERI[:2], f"{r.status_code}")
check("4d) Content-Range doğru",
      r.headers.get("content-range") == f"bytes 0-1/{len(_VERI)}",
      str(r.headers.get("content-range")))
r = client.get("/media/sounds/beyaz-gurultu.m4a",
               headers={"Range": f"bytes={len(_VERI) - 100}-"})
check("4e) Açık uçlu Range son 100 bayt", r.status_code == 206
      and r.content == _VERI[-100:], f"{r.status_code}")
check("4f) Olmayan ses → 404",
      client.get("/media/sounds/yok.m4a").status_code == 404, "")
check("4g) Geçersiz slug → 400",
      client.get("/media/sounds/Kotu_Slug.m4a").status_code == 400, "")

# =============================================================================
# 5) DÖNGÜ MATEMATİĞİ (ses_uret) — ffmpeg'siz
# =============================================================================
check("5a) N 1024'ün katı (AAC sonuna dolgu eklenmez)",
      ses_uret.N % 1024 == 0, str(ses_uret.N))
check("5b) N süresi 600±1 sn", abs(ses_uret.N / ses_uret.SR - 600) <= 1,
      str(ses_uret.N / ses_uret.SR))

_fazla = int((ses_uret.XFADE_DONGU + 1) * ses_uret.SR)
_t = np.arange(ses_uret.N + _fazla, dtype=np.float64) / ses_uret.SR
_sin = np.sin(2 * np.pi * 220.3 * _t).astype(np.float32)    # 600 sn'ye oturmayan
_dongu = ses_uret.dongu_yap(_sin)
_adim = float(np.max(np.abs(np.diff(_sin))))
_dikis = float(abs(_dongu[-1] - _dongu[0]))
check("5c) dongu_yap N örnek döndürüyor", len(_dongu) == ses_uret.N,
      str(len(_dongu)))
check("5d) Dikişte sıçrama normal bir adımdan büyük değil",
      _dikis <= _adim * 1.05, f"dikiş={_dikis:.4f} adım={_adim:.4f}")
_ham_dikis = float(abs(_sin[ses_uret.N - 1] - _sin[0]))
check("5e) (kontrol) crossfade'siz kesim dikişte sıçrama yapardı",
      _ham_dikis > _adim * 3, f"{_ham_dikis:.4f}")

_rng = np.random.default_rng(1)
_takeler = [_rng.standard_normal(30 * ses_uret.SR).astype(np.float32)
            for _ in range(3)]
_z = ses_uret.zincirle(_takeler, 100 * ses_uret.SR)
check("5f) zincirle hedef uzunluğa ulaşıyor", len(_z) >= 100 * ses_uret.SR,
      str(len(_z)))
_pencere = int(0.1 * ses_uret.SR)
_rms = np.sqrt(np.mean(_z[:len(_z) // _pencere * _pencere]
                       .reshape(-1, _pencere) ** 2, axis=1))
check("5g) Eşit güçlü crossfade: geçişlerde ses yüksekliği ±1.5 dB içinde",
      float(20 * np.log10(_rms.max() / _rms.min())) < 3.0,
      f"{20 * np.log10(_rms.max() / _rms.min()):.2f} dB")

# =============================================================================
print("=" * 78)
print("UYKU SESLERİ TEST SONUÇLARI")
print("=" * 78)
for ad, ok, detay in results:
    if not ok:
        print(f"  ✗ {ad}  —  {detay}")
gecen = sum(1 for _, ok, _ in results if ok)
print("-" * 78)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
