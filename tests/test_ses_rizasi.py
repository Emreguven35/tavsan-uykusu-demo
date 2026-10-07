"""
Anne Sesi: açık rıza kapısı (acik_riza_ses) + "Sesimi sil".

Kapsam:
  R1  Build 28+ onaysız ses kaydı → 403 riza_gerekli (ses servisine GİDİLMEZ)
  R2  Build 27 (ve başlıksız, sürümü bilinmeyen) → bugünkü gibi kabul
  R3  Build 28 + güncel onay → kabul; eski sürüm onay → 403; reddedilmiş onay → 403
  R4  Başlık yoksa kullanıcının son görülen sürümü (28) → kapı uygulanır
  R5  /consents: acik_riza_ses kabul ediliyor, /consents/me'de görünüyor,
      metin ucu taslak metni dönüyor
  D1  Sesimi sil: dosyalar + profiller + ses satırları silinir, ElevenLabs'te
      kalan klon silinir; voice-status "none"
  D2  Kayıt hakkı ETKİLENMEZ: silmeden önce kapalıysa kapalı kalır (aynı tarih),
      açıksa açık kalır
  D3  Paket hazırlanırken → 409, hiçbir şey silinmez
  D4  Silinecek bir şey yoksa 200 (idempotent)
  K1  KVKK dışa aktarımı acik_riza_ses onayını içeriyor; hesap silinince
      onay_kanitlari'na taşınıyor

Çalıştırma: python tests/test_ses_rizasi.py
"""
import io
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "ses_rizasi_test.db"
if _DB.exists():
    _DB.unlink()
_MEDYA = Path(tempfile.gettempdir()) / "ses_rizasi_medya"
import shutil                                                 # noqa: E402
shutil.rmtree(_MEDYA, ignore_errors=True)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["ELEVENLABS_API_KEY"] = "test-key"
os.environ["MEDIA_ROOT"] = str(_MEDYA)
os.environ["BETA_MODE"] = "true"                              # Anne Sesi premium kapısı
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import OnayKaniti, User, VoiceAudio, VoiceProfile   # noqa: E402
from api.main import app                                    # noqa: E402
from api.services import storage, voice as voice_svc, voice_uretim   # noqa: E402

from tests.llm_muhuru import canli_cagri_sayisi, muhurle    # noqa: E402
muhurle()

KLON = {"n": 0}
SILINEN: list[str] = []


def sahte_klon(name, audio_bytes, filename, content_type):
    KLON["n"] += 1
    return {"ok": True, "voice_id": f"voice-{KLON['n']}"}


def sahte_sil(voice_id):
    SILINEN.append(voice_id)
    return {"ok": True, "error": None, "status": 200}


voice_svc.clone_voice = sahte_klon
voice_svc.delete_voice = sahte_sil
voice_uretim.kuyruga_al = lambda pid: None

Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


def anne(email, consents=None):
    govde = {"email": email, "password": "TestPass123!"}
    if consents is not None:
        govde["consents"] = consents
    tok = client.post("/api/v1/auth/register", json=govde).json()["access_token"]
    db = SessionLocal()
    uid = db.query(User).filter(User.email == email).one().id
    db.close()
    return {"Authorization": f"Bearer {tok}"}, uid


def kaydet(h, build=None):
    hh = dict(h)
    if build is not None:
        hh["X-App-Version"] = f"1.0.0+{build}"
    return client.post("/api/v1/voice/clone", headers=hh,
                       files={"audio": ("s.m4a", io.BytesIO(b"SES" * 200), "audio/mp4")})


SES_ONAY = [{"tur": "acik_riza_ses", "onay": True, "metin_surumu": "kvkk-2026-10"}]

# R1 -------------------------------------------------------------------------
H1, U1 = anne("onaysiz@test.com")
_n = KLON["n"]
r = kaydet(H1, 28)
check("R1) Build 28 + onay yok → 403 riza_gerekli, ses servisine gidilmedi",
      r.status_code == 403 and r.json().get("code") == "riza_gerekli"
      and r.json().get("tur") == "acik_riza_ses" and KLON["n"] == _n, r.text[:200])

# R2 -------------------------------------------------------------------------
H2, U2 = anne("eski@test.com")
check("R2a) Build 27 onaysız → kabul (bugünkü gibi)", kaydet(H2, 27).status_code == 200, "")
H2b, _ = anne("basliksiz@test.com")
check("R2b) Başlık yok, sürüm bilinmiyor → kabul", kaydet(H2b).status_code == 200, "")

# R3 -------------------------------------------------------------------------
H3, U3 = anne("onayli@test.com", SES_ONAY)
check("R3a) Build 28 + güncel onay → kabul", kaydet(H3, 28).status_code == 200, "")
H3b, _ = anne("eskionay@test.com", [{"tur": "acik_riza_ses", "onay": True,
                                     "metin_surumu": "taslak-2026-09"}])
check("R3b) Eski sürümle onay → 403", kaydet(H3b, 29).status_code == 403, "")
H3c, _ = anne("red@test.com", [{"tur": "acik_riza_ses", "onay": False,
                                "metin_surumu": "kvkk-2026-10"}])
check("R3c) Reddedilmiş onay → 403", kaydet(H3c, 28).status_code == 403, "")
client.post("/api/v1/consents", headers=H3c,
            json={"tur": "acik_riza_ses", "onay": True, "metin_surumu": "kvkk-2026-10"})
check("R3d) Sonradan uygulamadan onay verince → kabul", kaydet(H3c, 28).status_code == 200, "")

# R4 -------------------------------------------------------------------------
H4, U4 = anne("surumlu@test.com")
client.get("/api/v1/babies", headers={**H4, "X-App-Version": "1.0.0+28"})   # sürüm kaydı
check("R4) Başlık yok ama son görülen sürüm 28 → kapı uygulanır (403)",
      kaydet(H4).status_code == 403, "")

# R5 -------------------------------------------------------------------------
me = client.get("/api/v1/consents/me", headers=H3).json()
check("R5a) /consents/me acik_riza_ses: onay, güncel",
      me.get("acik_riza_ses", {}).get("onay") is True
      and me["acik_riza_ses"]["guncelleme_gerekli"] is False, me.get("acik_riza_ses"))
m = client.get("/api/v1/consents/metin/acik_riza_ses")
check("R5b) Metin ucu taslak metni dönüyor (sürüm kvkk-2026-10)",
      m.status_code == 200 and m.json().get("metin_surumu") == "kvkk-2026-10"
      and "Anne Sesi" in m.json().get("markdown", ""), m.status_code)

# D1 / D2 ----------------------------------------------------------------------
db = SessionLocal()
p3 = db.query(VoiceProfile).filter(VoiceProfile.user_id == U3).one()
p3.status = "released"                       # paket bitti
db.add(VoiceAudio(voice_profile_id=p3.id, content_id="ninni-1",
                  storage_path=f"{storage.KOVA_SES}/{U3}/{p3.id}/ninni-1.mp3"))
db.commit()
pid, vid = str(p3.id), p3.elevenlabs_voice_id
db.close()
klasor = _MEDYA / storage.KOVA_SES / str(U3) / pid
klasor.mkdir(parents=True, exist_ok=True)
(klasor / "ninni-1.mp3").write_bytes(b"x")
once = client.get("/api/v1/voice/voice-status", headers=H3).json()
r = client.delete("/api/v1/voice/me", headers=H3)
j = r.json()
db = SessionLocal()
kalan_p = db.query(VoiceProfile).filter(VoiceProfile.user_id == U3).count()
kalan_a = db.query(VoiceAudio).count()
db.close()
check("D1a) Sesimi sil 200; profil, ses satırı ve dosyalar silindi",
      r.status_code == 200 and kalan_p == 0 and not klasor.exists()
      and j.get("silinen_profil") == 1 and j.get("silinen_dosya") == 1,
      f"{r.status_code} {j} profil={kalan_p} klasor={klasor.exists()}")
check("D1b) ElevenLabs'te kalan klon silindi", vid in SILINEN and j.get("klon_silindi") == 1,
      (vid, SILINEN))
sonra = client.get("/api/v1/voice/voice-status", headers=H3).json()
check("D1c) voice-status 'none'", sonra.get("status") == "none", sonra)
check("D2a) Kayıt hakkı etkilenmedi: kapalıydı, kapalı kaldı, aynı tarih",
      once.get("can_clone") is False and sonra.get("can_clone") is False
      and j.get("can_clone") is False
      and str(sonra.get("next_clone_available_at"))[:16] == str(once.get("next_clone_available_at"))[:16],
      (once.get("next_clone_available_at"), sonra.get("next_clone_available_at")))
check("D2b) Silmeden sonra yeni kayıt hâlâ 429 (hak geri gelmedi)",
      kaydet(H3, 28).status_code == 429, "")
# Hakkı AÇIK olan anne: 31 gün önce kaydetmiş
H5, U5 = anne("acik@test.com", SES_ONAY)
kaydet(H5, 28)
db = SessionLocal()
for p in db.query(VoiceProfile).filter(VoiceProfile.user_id == U5):
    p.last_cloned_at = datetime.now(timezone.utc) - timedelta(days=31)
    p.status = "released"
db.commit()
db.close()
r = client.delete("/api/v1/voice/me", headers=H5)
check("D2c) Hakkı açık olanın hakkı açık kaldı",
      r.status_code == 200 and r.json().get("can_clone") is True
      and kaydet(H5, 28).status_code == 200, r.text[:160])

# D3 / D4 ----------------------------------------------------------------------
H6, U6 = anne("hazirlaniyor@test.com", SES_ONAY)
kaydet(H6, 28)                                   # status cloning
r = client.delete("/api/v1/voice/me", headers=H6)
db = SessionLocal()
_n6 = db.query(VoiceProfile).filter(VoiceProfile.user_id == U6).count()
db.close()
check("D3) Paket hazırlanırken → 409, hiçbir şey silinmedi", r.status_code == 409 and _n6 == 1,
      f"{r.status_code} {_n6}")
check("D4) Silinecek bir şey yok → 200 (idempotent)",
      client.delete("/api/v1/voice/me", headers=H1).status_code == 200, "")

# K1 -------------------------------------------------------------------------
H7, U7 = anne("disa@test.com", SES_ONAY)
ex = client.get("/api/v1/account/export", headers=H7).json()
check("K1a) Dışa aktarımda acik_riza_ses onayı",
      any(o.get("tur") == "acik_riza_ses" for o in ex.get("onaylar") or []), ex.get("onaylar"))
client.delete("/api/v1/auth/account", headers=H7)
db = SessionLocal()
_k = [k.tur for k in db.query(OnayKaniti).all()]
db.close()
check("K1b) Hesap silinince acik_riza_ses onay_kanitlari'na taşındı", "acik_riza_ses" in _k, _k)

check("Z) Canlı LLM çağrısı yapılmadı", canli_cagri_sayisi() == 0, canli_cagri_sayisi())

print("\n" + "=" * 74)
print("ANNE SESİ — AÇIK RIZA + SESİMİ SİL")
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
sys.exit(0 if _gecen == len(results) else 1)
