"""
Anne Sesi vitrini (2026-10-20) — premium olmayanın önizleme ekranında premium
içeriğin İSİMLERİ kilitli liste olarak. Ağ YOK.

  V  Ücretsiz/deneme: /voice/stories `vitrin` taşır; Masallar bölümü, önizleme
     masalı ilk sırada (onizleme=true, kilitli=false), diğerleri kilitli;
     başlık/süre etiketi katalogdan; gosterilecek_adet ayardan (3/2).
  N  Ninniler bölümü yalnız ANNE_SESI_STS açık VE referans kaydı varsa; izin
     yoksa da dönmez.
  B  Bağlantı yok: vitrinde hiçbir URL; mevcut listede kilitlilerde audio_url yok.
  P  Premium: vitrin null; mevcut alanlar aynı (kilitsiz liste).

Çalıştırma: python tests/test_ses_vitrin.py
"""
import json
import os
import shutil
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "ses_vitrin_test.db"
if _DB.exists():
    _DB.unlink()
_MEDYA = Path(tempfile.gettempdir()) / "ses_vitrin_medya"
_REF = Path(tempfile.gettempdir()) / "ses_vitrin_referans"
shutil.rmtree(_MEDYA, ignore_errors=True)
shutil.rmtree(_REF, ignore_errors=True)
_REF.mkdir()

os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(_MEDYA)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ["ELEVENLABS_API_KEY"] = "test-key"
for _k in ("ANNE_SESI_STS", "VOICE_PAKET_NINNILER", "VOICE_PAKET_MASALLAR",
           "VOICE_ONIZLEME_ICERIK", "VOICE_VITRIN_MASAL_ADET",
           "VOICE_VITRIN_NINNI_ADET", "PUBLIC_BASE_URL"):
    os.environ.pop(_k, None)
# Canlıdaki gibi beta açık; ücretsiz/deneme hesapları BETA_HARIC ile gerçek kademede.
os.environ["BETA_MODE"] = "true"
os.environ["BETA_HARIC_EMAILS"] = "ucretsiz@test.com,deneme@test.com"

from api.db import Base, SessionLocal, engine                  # noqa: E402
import api.models                                              # noqa: E402,F401
from api.models import Subscription                            # noqa: E402
from api.services import voice_paket, voice_sts                # noqa: E402
from api.services import voice as voice_svc                    # noqa: E402

voice_sts.REFERANS_KLASOR = _REF
Base.metadata.create_all(bind=engine)
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


from fastapi.testclient import TestClient                     # noqa: E402
from api.main import app                                      # noqa: E402

client = TestClient(app)
KATALOG = {x["id"]: x for x in voice_svc.load_stories()["masallar"]
           + voice_svc.load_stories()["ninniler"]}
ONIZLEME = voice_paket.onizleme_icerik_id()


def reg(email):
    r = client.post("/api/v1/auth/register",
                    json={"email": email, "password": "TestPass123!"})
    return r.json()["access_token"]


def uid_of(tok):
    from api.services.security import decode_access_token
    return _uuid.UUID(decode_access_token(tok)["sub"])


def stories(tok):
    r = client.get("/api/v1/voice/stories", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    return r.json()


def bolum(s, ad):
    return next((b for b in (s.get("vitrin") or []) if b["bolum_adi"] == ad), None)


def deneme(uid):
    db = SessionLocal()
    try:
        db.add(Subscription(user_id=uid, platform="ios", product_id="tu_aylik",
                            status="active", period_type="TRIAL",
                            expires_at=datetime.now(timezone.utc) + timedelta(days=3)))
        db.commit()
    finally:
        db.close()


# =============================================================================
# V — ÜCRETSİZ: VİTRİN
# =============================================================================
tu = reg("ucretsiz@test.com")
s = stories(tu)
m = bolum(s, "Masallar")
check("V1) Ücretsizde vitrin var, Masallar bölümü", isinstance(s.get("vitrin"), list)
      and m is not None, str(s.get("vitrin"))[:300])
og = (m or {}).get("ogeler", [])
check("V2) Önizleme masalı ilk sırada, onizleme=true, kilitli=false",
      og and og[0]["id"] == ONIZLEME and og[0]["onizleme"] is True
      and og[0]["kilitli"] is False, str(og[:1]))
check("V3) Diğer masallar kilitli, onizleme=false",
      len(og) > 1 and all(x["kilitli"] and not x["onizleme"] for x in og[1:]),
      str([(x["id"], x["kilitli"]) for x in og]))
check("V4) Başlık ve süre etiketi katalogdan, tür masal",
      all(x["baslik"] == KATALOG[x["id"]]["title"]
          and x["sure_etiketi"] == KATALOG[x["id"]].get("duration_hint")
          and x["tur"] == "masal" for x in og), str(og))
check("V5) Masallar = paketteki masallar (satın alınca üretilecekler), tekrar yok",
      sorted(x["id"] for x in og) == sorted(
          x["id"] for x in voice_paket.paket_icerikleri() if x["type"] == "masal")
      and len({x["id"] for x in og}) == len(og), str([x["id"] for x in og]))
check("V6) gosterilecek_adet varsayılan 3", (m or {}).get("gosterilecek_adet") == 3,
      str(m and m.get("gosterilecek_adet")))
check("V7) STS kapalı → Ninniler bölümü YOK", bolum(s, "Ninniler") is None,
      str(s.get("vitrin")))
check("V8) Mevcut alanlar duruyor (masallar/ninniler/paket/paket_icerikleri)",
      all(k in s for k in ("masallar", "ninniler", "paket", "paket_icerikleri")), "")

# --- B — bağlantı yok --------------------------------------------------------
check("B1) Vitrinde hiçbir bağlantı/URL yok",
      "url" not in json.dumps(s["vitrin"]).lower()
      and "http" not in json.dumps(s["vitrin"]).lower(), json.dumps(s["vitrin"])[:300])
check("B2) Mevcut listede kilitli içerikler audio_url taşımıyor",
      all(x["audio_url"] is None for x in s["masallar"] + s["ninniler"] if x["locked"]),
      str([(x["id"], x["locked"], x["audio_url"]) for x in s["masallar"]]))

# --- ayar --------------------------------------------------------------------
os.environ["VOICE_VITRIN_MASAL_ADET"] = "4"
check("V9) VOICE_VITRIN_MASAL_ADET=4 uygulanır",
      bolum(stories(tu), "Masallar")["gosterilecek_adet"] == 4, "")
os.environ["VOICE_VITRIN_MASAL_ADET"] = "abc"
check("V10) Geçersiz ayar → varsayılan 3",
      bolum(stories(tu), "Masallar")["gosterilecek_adet"] == 3, "")
os.environ.pop("VOICE_VITRIN_MASAL_ADET")

# =============================================================================
# N — NİNNİ BÖLÜMÜ KOŞULA BAĞLI
# =============================================================================
os.environ["ANNE_SESI_STS"] = "true"
check("N1) STS açık ama referans kaydı yok → Ninniler YOK",
      bolum(stories(tu), "Ninniler") is None, "")
os.environ.pop("ANNE_SESI_STS")
(_REF / "ninni_dandini.mp3").write_bytes(b"\xff\xfb\x90\x00" * 200)
(_REF / "ninni_e_bebegim.mp3").write_bytes(b"\xff\xfb\x90\x00" * 200)
check("N2) Referans var ama STS kapalı → Ninniler YOK",
      bolum(stories(tu), "Ninniler") is None, "")
os.environ["ANNE_SESI_STS"] = "true"
s_n = stories(tu)
n = bolum(s_n, "Ninniler")
check("N3) STS açık + referans → Ninniler yalnız kaydı olanlar",
      n is not None and [x["id"] for x in n["ogeler"]] == ["ninni_dandini", "ninni_e_bebegim"],
      str(n))
check("N4) Ninniler kilitli, tür ninni, başlık katalogdan, adet 2",
      n is not None and n["gosterilecek_adet"] == 2
      and all(x["kilitli"] and x["tur"] == "ninni"
              and x["baslik"] == KATALOG[x["id"]]["title"] for x in n["ogeler"]), str(n))
check("N5) Bölüm sırası Masallar, Ninniler",
      [b["bolum_adi"] for b in s_n["vitrin"]] == ["Masallar", "Ninniler"], "")
_eski = voice_sts._izin_yok
voice_sts._izin_yok = True
check("N6) STS izni yoksa Ninniler YOK", bolum(stories(tu), "Ninniler") is None, "")
voice_sts._izin_yok = _eski

# =============================================================================
# D — DENEME (premium sayılmaz) ; P — PREMIUM
# =============================================================================
td = reg("deneme@test.com")
deneme(uid_of(td))
sd = stories(td)
check("D1) Deneme kullanıcıda da vitrin var", bolum(sd, "Masallar") is not None,
      str(sd.get("vitrin"))[:200])

tp = reg("premium@test.com")          # BETA_MODE → premium
sp = stories(tp)
check("P1) Premium: vitrin null", sp.get("vitrin") is None, str(sp.get("vitrin")))
check("P2) Premium: liste kilitsiz, alanlar aynı",
      all(not x["locked"] for x in sp["masallar"] + sp["ninniler"])
      and set(sp) == {"masallar", "ninniler", "paket", "paket_icerikleri", "vitrin"},
      str(set(sp)))
check("P3) Premium ninni listesi vitrinle aynı koşulda (STS+referans)",
      [x["id"] for x in sp["ninniler"]] == ["ninni_dandini", "ninni_e_bebegim"],
      str([x["id"] for x in sp["ninniler"]]))
os.environ.pop("ANNE_SESI_STS")

# =============================================================================
print("\n" + "=" * 74)
print("ANNE SESİ VİTRİNİ")
print("=" * 74)
_gecen = 0
for ad, ok, detay in results:
    print(f"  {'[PASS]' if ok else '[FAIL]'} {ad}")
    if not ok:
        print(f"         → {detay}")
    _gecen += int(ok)
print("-" * 74)
print(f"TOPLAM: {_gecen}/{len(results)} geçti")
print("=" * 74)
shutil.rmtree(_MEDYA, ignore_errors=True)
shutil.rmtree(_REF, ignore_errors=True)
sys.exit(0 if _gecen == len(results) else 1)
