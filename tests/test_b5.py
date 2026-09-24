"""
B5 — ücretsiz ninni (genel ses), kurucu üye listesi, KVKK. Ağ YOK, LLM YOK.

  N  Genel ninni bir kez üretilir (ikinci çağrı dokunmaz); sesi olmayan
     kullanıcıya genel sürüm, kendi sesi olana kendi sürümü; is_free
  K  Kurucu listesi: test alanı, moderatör, KURUCU_HARIC hariç; maskeli;
     bebek sayısı; hariç tutulan kurucu sayılmaz; /admin/kurucular yalnız admin
  C  Onay: kaydet, salt ekleme (son satır geçerli), eski sürüm → güncelleme
     gerekli, IP açık saklanmaz, kayıtta isteğe bağlı onay, metin uç noktası
  E  Dışa aktarma: bölümler, parola yok, 24 saatte bir (429 + Retry-After);
     hesap silinince onaylar da gider

Çalıştırma: python tests/test_b5.py
"""
import os
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "b5_test.db"
if _DB.exists():
    _DB.unlink()
_MEDYA = Path(tempfile.gettempdir()) / "b5_medya"
import shutil                                               # noqa: E402
shutil.rmtree(_MEDYA, ignore_errors=True)
LANSMAN = (datetime.now(timezone.utc) + timedelta(days=5)).date()   # gelecekte
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(_MEDYA)
os.environ["LANSMAN_TARIHI"] = LANSMAN.isoformat()
os.environ["KURUCU_HARIC"] = "haric@gercek.com, BIR.DAHA@gercek.com"
os.environ["ELEVENLABS_VOICE_ID"] = "anlatici-ses"
os.environ.pop("BETA_MODE", None)
os.environ.pop("BETA_PREMIUM_ALL", None)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import (Baby, CommunityProfile, Consent,    # noqa: E402
                        SleepLog, User, VoiceAudio, VoiceProfile)
from api.main import app                                    # noqa: E402
from api.services import genel_ses, premium as premium_svc, storage  # noqa: E402
from api.services import voice_uretim                       # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, muhurle            # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


def hesap(email: str, **govde):
    r = client.post("/api/v1/auth/register",
                    json={"email": email, "password": "TestPass123!", **govde})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    db = SessionLocal()
    try:
        uid = db.query(User).filter(User.email == email.lower()).one().id
    finally:
        db.close()
    return h, uid, r


# =============================================================================
# N — Genel (anlatıcı sesli) ücretsiz ninni
# =============================================================================
_cagri = []


def _sahte_seslendir(voice_id, metin, user_id):
    _cagri.append(voice_id)
    return b"ID3" + b"genel" * 40


voice_uretim._seslendir = _sahte_seslendir
r1 = genel_ses.uret()
r2 = genel_ses.uret()
check("N1) Anlatıcı sesiyle üretildi ve depoya yazıldı",
      r1["durum"] == "uretildi" and storage.depo().var_mi(genel_ses.genel_yol("ninni_dandini"))
      and _cagri == ["anlatici-ses"], f"{r1} {_cagri}")
check("N2) İkinci çağrı yeniden ÜRETMEZ (kredi bir kez)",
      r2["durum"] == "zaten_var" and len(_cagri) == 1, str(r2))

HS, US, _ = hesap("sessiz@gercek.com")
st = client.get("/api/v1/voice/stories", headers=HS).json()
_hepsi = {x["id"]: x for x in st["masallar"] + st["ninniler"]}
_d = _hepsi["ninni_dandini"]
check("N3) Sesi olmayan (ücretsiz) kullanıcı: dandini genel sürümle hazır + is_free",
      _d["hazir"] and _d["ses_kaynagi"] == "genel" and _d["is_free"]
      and _d["locked"] is False and "sig=" in (_d["audio_url"] or ""), str(_d))
check("N4) Diğer içerikler kilitli ve bağlantısız",
      all(x["locked"] and x["audio_url"] is None and not x["is_free"]
          for k, x in _hepsi.items() if k != "ninni_dandini"), "")
_u = client.get(_d["audio_url"].split("tavsan", 1)[-1] if _d["audio_url"].startswith("http")
                else _d["audio_url"])
check("N5) Genel sürümün imzalı bağlantısı çalıyor", _u.status_code == 200
      and _u.content.startswith(b"ID3"), str(_u.status_code))

HV, UV, _ = hesap("sesli@gercek.com")
_db = SessionLocal()
try:
    prof = VoiceProfile(user_id=UV, elevenlabs_voice_id=None, status="released")
    _db.add(prof)
    _db.flush()
    yol = storage.ses_yolu(UV, prof.id, "ninni_dandini")
    storage.depo().yaz(yol, b"ID3kendi")
    _db.add(VoiceAudio(voice_profile_id=prof.id, content_id="ninni_dandini",
                       storage_path=yol, bytes=8))
    _db.commit()
finally:
    _db.close()
_dv = {x["id"]: x for x in client.get("/api/v1/voice/stories", headers=HV).json()["ninniler"]}
check("N6) Kendi sesi olan kullanıcıya KENDİ sürümü", _dv["ninni_dandini"]["ses_kaynagi"] == "anne",
      str(_dv["ninni_dandini"]))

# =============================================================================
# K — Kurucu üye listesi
# =============================================================================
hesap("test-1@example.com")
hesap("haric@gercek.com")
hesap("bir.daha@gercek.com")
HMOD, UMOD, _ = hesap("moderator@gercek.com")
_db = SessionLocal()
try:
    _db.add(CommunityProfile(user_id=UMOD, nickname="mod", is_moderator=True,
                             status="active", post_count=0))
    _db.add(Baby(user_id=US, name="A", birth_date=date.today() - timedelta(days=200)))
    _db.add(Baby(user_id=US, name="B", birth_date=date.today() - timedelta(days=200)))
    _db.commit()
    liste = premium_svc.kurucu_listesi(_db)
    epostalar = [k["eposta"] for k in liste]
    check("K1) Test alanı, moderatör ve KURUCU_HARIC (büyük/küçük harf) hariç",
          not any(e.endswith("example.com") for e in epostalar)
          and "mo***@gercek.com" not in epostalar
          and "ha***@gercek.com" not in epostalar and "bi***@gercek.com" not in epostalar,
          str(epostalar))
    check("K2) Gerçek hesaplar listede, e-posta maskeli",
          "se***@gercek.com" in epostalar and all("***@" in e for e in epostalar),
          str(epostalar))
    _k = next(k for k in liste if k["user_id"] == str(US))
    check("K3) Bebek sayısı ve alanlar", _k["bebek"] == 2 and _k["kayit"]
          and "son_etkinlik" in _k, str(_k))
    _uh = _db.query(User).filter(User.email == "haric@gercek.com").one()
    check("K4) Hariç tutulan hesap kurucu SAYILMAZ (durumda da)",
          premium_svc.durum(_db, _uh)["kurucu_uye"] is False
          and premium_svc.durum(_db, _db.get(User, US))["kurucu_uye"] is True, "")
finally:
    _db.close()
r = client.get("/api/v1/admin/kurucular", headers=HS)
check("K5) /admin/kurucular admin olmayana 403", r.status_code == 403, str(r.status_code))
r = client.get("/api/v1/admin/kurucular", headers=HMOD)
check("K6) Admin listeyi alıyor (sayı + lansman)", r.status_code == 200
      and r.json()["sayi"] == len(liste) and r.json()["lansman_tarihi"], r.text[:200])

# =============================================================================
# C — KVKK onayları
# =============================================================================
HC, UC, rc = hesap("onayli@gercek.com", consents=[
    {"tur": "aydinlatma", "onay": True},
    {"tur": "acik_riza_saglik", "onay": True, "metin_surumu": "eski-surum"}])
check("C1) Kayıt onaylarla 201", rc.status_code == 201, rc.text[:120])
me = client.get("/api/v1/consents/me", headers=HC).json()
check("C2) Kayıtta gelen onay kaydedildi (güncel sürüm)",
      me["aydinlatma"]["onay"] is True and me["aydinlatma"]["guncelleme_gerekli"] is False,
      str(me["aydinlatma"]))
check("C3) Eski sürüme verilen onay → güncelleme gerekli",
      me["acik_riza_saglik"]["guncelleme_gerekli"] is True, str(me["acik_riza_saglik"]))
check("C4) Hiç sorulmamış tür → onay None, güncelleme gerekli",
      me["pazarlama"]["onay"] is None and me["pazarlama"]["guncelleme_gerekli"], "")
r = client.post("/api/v1/consents", headers=HC, json={"tur": "pazarlama", "onay": True})
r2 = client.post("/api/v1/consents", headers=HC, json={"tur": "pazarlama", "onay": False})
me = client.get("/api/v1/consents/me", headers=HC).json()
check("C5) POST 201; geri çekme yeni satır, son satır geçerli",
      r.status_code == 201 and r2.status_code == 201 and me["pazarlama"]["onay"] is False, "")
_db = SessionLocal()
try:
    _sat = _db.query(Consent).filter(Consent.user_id == UC).all()
    check("C6) Salt ekleme: 4 satır (2 kayıt + 2 uygulama)",
          len(_sat) == 4 and {s.kaynak for s in _sat} == {"kayit", "uygulama"},
          [(s.tur, s.kaynak) for s in _sat])
    check("C7) IP açık saklanmıyor (64 hane HMAC, 'testclient' içermiyor)",
          all(s.ip_hash and len(s.ip_hash) == 64 and "test" not in s.ip_hash for s in _sat),
          str([s.ip_hash for s in _sat][:1]))
finally:
    _db.close()
r = client.post("/api/v1/consents", headers=HC, json={"tur": "reklam", "onay": True})
check("C8) Bilinmeyen tür → 422", r.status_code == 422, str(r.status_code))
_, _, rz = hesap("onaysiz@gercek.com")
check("C9) Onaysız kayıt da olur (şimdilik zorlama yok)", rz.status_code == 201, "")
r = client.get("/api/v1/consents/metin/aydinlatma")
check("C10) Metin herkese açık + sürüm", r.status_code == 200
      and r.json()["metin_surumu"] and r.json()["markdown"].startswith("#"), r.text[:80])
check("C11) Bilinmeyen metin → 404",
      client.get("/api/v1/consents/metin/yok").status_code == 404, "")

# =============================================================================
# E — Dışa aktarma
# =============================================================================
_bid = client.post("/api/v1/babies", headers=HC, json={
    "name": "Bebek", "night_wakes": 1, **TAM_PROFIL,
    "birth_date": (date.today() - timedelta(days=240)).isoformat()}).json()["id"]
client.post("/api/v1/logs/batch", headers=HC, json={"logs": [{
    "baby_id": _bid, "type": "nap", "client_id": "e1",
    "started_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
    "ended_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()}]})
r = client.get("/api/v1/account/export", headers=HC)
j = r.json() if r.status_code == 200 else {}
check("E1) 200 + indirme başlığı", r.status_code == 200
      and "attachment" in r.headers.get("content-disposition", ""), r.text[:150])
check("E2) Bölümler dolu (bebek, kayıt, onaylar, hesap)",
      len(j.get("bebekler", [])) == 1 and len(j.get("uyku_kayitlari", [])) == 1
      and len(j.get("onaylar", [])) == 4 and j.get("hesap", {}).get("email") == "onayli@gercek.com",
      str({k: (len(v) if isinstance(v, list) else "…") for k, v in j.items()})[:300])
check("E3) Parola özeti YOK", "password_hash" not in j.get("hesap", {})
      and "password_hash" not in r.text, "")
check("E4) Topluluk/abonelik/sohbet bölümleri var",
      all(k in j for k in ("topluluk", "abonelikler", "sohbet", "planlar", "anne_sesi")), "")
r = client.get("/api/v1/account/export", headers=HC)
check("E5) 24 saat dolmadan → 429 + Retry-After + Türkçe", r.status_code == 429
      and int(r.headers.get("retry-after", 0)) > 23 * 3600
      and "24 saatte" in r.json()["detail"], r.text[:150])
r = client.delete("/api/v1/auth/account", headers=HC)
_db = SessionLocal()
try:
    check("E6) Hesap silinince onaylar da gitti (cascade)", r.status_code == 200
          and _db.query(Consent).filter(Consent.user_id == UC).count() == 0, str(r.status_code))
finally:
    _db.close()

# =============================================================================
print("=" * 78)
print("B5 TEST SONUÇLARI")
print("=" * 78)
for ad, ok, detay in results:
    if not ok:
        print(f"  ✗ {ad}  —  {detay}")
gecen = sum(1 for _, ok, _ in results if ok)
print("-" * 78)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
