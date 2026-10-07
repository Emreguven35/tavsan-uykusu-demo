"""
Saklama ve silme kuralları (sistemin iç kayıtları) + log maskeleme + is_mine.

Kapsam:
  S1  Hesap silme: onaylar onay_kanitlari'na taşınır (kimliksiz, e-posta özetiyle),
      plan işleri ve RevenueCat olayları silinir; başka kullanıcınınkiler durur
  S2  Hesap silmede Anne Sesi klasörü silinemezse gece temizliği siler
  S3  Plan işleri: sahipsizler + 30 günden eski bitmiş/başarısız işler silinir;
      her bebeğin en son BAŞARILI işi (ne kadar eski olursa olsun) kalır;
      süren iş ve 30 günden yeni işler kalır
  S4  /data/denetim ve /data/arsiv: 30 günden eski dosyalar silinir, yeniler kalır
  S5  onay_kanitlari: hesap silineli 10 yılı geçenler silinir
  S6  push_tokens.device_name saklanmaz (yeni kayıt ve güncelleme)
  S7  Migration 0026: onay_kanitlari kurulur, mevcut device_name boşaltılır
  S8  Mailer: alıcı maskeli; console modunda içerik/bağlantı loglanmaz
  S9  Cevap önbelleği isabetinde soru metni loglanmaz
  S10 Topluluk: is_mine (anonim kendi gönderisinde de true), başkasında false

Çalıştırma: python tests/test_saklama.py
"""
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
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

_TMP = Path(tempfile.gettempdir()) / "saklama_test"
import shutil                                                 # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)                       # önceki koşunun dosyaları
_DB = _TMP.with_suffix(".db")
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(_TMP / "medya")
os.environ["DENETIM_ROOT"] = str(_TMP / "veri" / "denetim")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import (Baby, Consent, OnayKaniti, PlanUretimIsi,   # noqa: E402
                        PushToken, RevenueCatOlayi, User)
from api.main import app                                    # noqa: E402
from api.services import kvkk, mailer, saklama, storage     # noqa: E402
from api.zaman import bugun_tr                              # noqa: E402
from engine import chatbot                                  # noqa: E402

from tests.llm_muhuru import canli_cagri_sayisi, muhurle    # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


ONAYLAR = [{"tur": "aydinlatma", "onay": True, "metin_surumu": "kvkk-2026-10"},
           {"tur": "acik_riza_saglik", "onay": True, "metin_surumu": "kvkk-2026-10"}]
PROFIL = {"birth_date": (bugun_tr() - timedelta(days=240)).isoformat(),
          "feeding_type": "breast", "sleep_method": "rocking",
          "sleep_environment": "loc:own_bed;d:1", "crying_tolerance": "5",
          "parent_experience": "none", "night_wakes": 2}


def kullanici(email, consents=None):
    govde = {"email": email, "password": "TestPass123!"}
    if consents:
        govde["consents"] = consents
    tok = client.post("/api/v1/auth/register", json=govde).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    bid = client.post("/api/v1/babies", headers=h, json={"name": "Ada", **PROFIL}).json()["id"]
    db = SessionLocal()
    uid = db.query(User).filter(User.email == email).one().id
    db.close()
    return h, uid, bid


def is_ekle(db, uid, bid, status, gun_once, jid=None):
    jid = jid or str(uuid.uuid4())
    db.add(PlanUretimIsi(id=jid, user_id=str(uid), baby_id=str(bid), status=status,
                         started=True, deneme=0,
                         created_at=datetime.now(timezone.utc) - timedelta(days=gun_once)))
    return jid


# =============================================================================
# S1 — hesap silme
# =============================================================================
HA, UA, BA = kullanici("silinen@test.com", ONAYLAR)
HB, UB, BB = kullanici("kalan@test.com", ONAYLAR)
db = SessionLocal()
is_ekle(db, UA, BA, "done", 1)
is_ekle(db, UB, BB, "done", 1)
db.add(RevenueCatOlayi(id="ev-a1", type="INITIAL_PURCHASE", app_user_id=str(UA), user_id=UA,
                       sonuc="ok", raw={"x": 1}, received_at=datetime.now(timezone.utc)))
db.add(RevenueCatOlayi(id="ev-a2", type="RENEWAL", app_user_id=str(UA), user_id=None,
                       sonuc="ok", raw={"x": 2}, received_at=datetime.now(timezone.utc)))
db.add(RevenueCatOlayi(id="ev-b1", type="RENEWAL", app_user_id=str(UB), user_id=UB,
                       sonuc="ok", raw={"x": 3}, received_at=datetime.now(timezone.utc)))
db.commit()
db.close()
r = client.delete("/api/v1/auth/account", headers=HA)
db = SessionLocal()
_kanit = db.query(OnayKaniti).all()
check("S1a) Hesap silindi", r.status_code == 200, r.text[:120])
check("S1b) Onaylar ispat tablosuna taşındı (2 satır, tür + sürüm + onay)",
      sorted((k.tur, k.metin_surumu, k.onay) for k in _kanit)
      == [("acik_riza_saglik", "kvkk-2026-10", True), ("aydinlatma", "kvkk-2026-10", True)],
      [(k.tur, k.metin_surumu) for k in _kanit])
check("S1c) Kanıtta e-posta özeti var, e-posta ve kullanıcı kimliği YOK",
      all(k.eposta_ozeti == kvkk.eposta_ozeti("silinen@test.com") for k in _kanit)
      and not {"user_id", "email"} & set(OnayKaniti.__table__.c.keys())
      and all("silinen" not in k.eposta_ozeti for k in _kanit), "")
check("S1d) Silinen kullanıcının consents satırı kalmadı; diğerinin duruyor",
      db.query(Consent).filter(Consent.user_id == UA).count() == 0
      and db.query(Consent).filter(Consent.user_id == UB).count() == 2, "")
check("S1e) Plan işleri: silinenin işi silindi, diğerininki duruyor",
      db.query(PlanUretimIsi).filter(PlanUretimIsi.user_id == str(UA)).count() == 0
      and db.query(PlanUretimIsi).filter(PlanUretimIsi.user_id == str(UB)).count() == 1, "")
check("S1f) RevenueCat: silinenin olayları (user_id ve app_user_id) silindi, diğeri duruyor",
      {e.id for e in db.query(RevenueCatOlayi).all()} == {"ev-b1"},
      [e.id for e in db.query(RevenueCatOlayi).all()])
db.close()

# =============================================================================
# S2 — Anne Sesi klasörü yeniden denenir
# =============================================================================
HC, UC, BC = kullanici("ses@test.com")
kok = Path(os.environ["MEDIA_ROOT"]) / storage.KOVA_SES / str(UC) / "p1"
kok.mkdir(parents=True, exist_ok=True)
(kok / "ninni.mp3").write_bytes(b"x")
_asil_sil = storage.YerelDepo.klasor_sil


def _patlayan_sil(self, onek):
    raise OSError("disk meşgul")


storage.YerelDepo.klasor_sil = _patlayan_sil
r = client.delete("/api/v1/auth/account", headers=HC)
storage.YerelDepo.klasor_sil = _asil_sil
check("S2a) Silme sırasında klasör silinemedi → hesap yine silindi, klasör kaldı",
      r.status_code == 200 and kok.exists(), r.status_code)
db = SessionLocal()
_n = saklama.sahipsiz_ses_klasorleri(db)
db.close()
check("S2b) Gece temizliği sahipsiz klasörü sildi",
      _n == 1 and not kok.parent.exists(), f"silinen={_n} var_mi={kok.parent.exists()}")
_HD, UD, BD = kullanici("sesli@test.com")
kendi = Path(os.environ["MEDIA_ROOT"]) / storage.KOVA_SES / str(UD) / "p"
kendi.mkdir(parents=True, exist_ok=True)
(kendi / "a.mp3").write_bytes(b"x")
db = SessionLocal()
saklama.sahipsiz_ses_klasorleri(db)
db.close()
check("S2c) Sahibi olan klasöre dokunulmadı", (kendi / "a.mp3").exists(), "")

# =============================================================================
# S3 — plan işleri
# =============================================================================
HE, UE, BE = kullanici("isler@test.com")
db = SessionLocal()
j = {
    "done_50": is_ekle(db, UE, BE, "done", 50),
    "done_40_son": is_ekle(db, UE, BE, "done", 40),
    "failed_35": is_ekle(db, UE, BE, "failed", 35),
    "failed_5": is_ekle(db, UE, BE, "failed", 5),
    "processing_40": is_ekle(db, UE, BE, "processing", 40),
    "sahipsiz_bebek": is_ekle(db, UE, uuid.uuid4(), "done", 1),
    "sahipsiz_kullanici": is_ekle(db, uuid.uuid4(), BE, "done", 1),
}
db.commit()
_sonuc = saklama.plan_islerini_temizle(db)
db.commit()
kalan = {i.id for i in db.query(PlanUretimIsi).all()}
db.close()
check("S3a) 30 günden eski bitmiş/başarısız silindi (en son başarılı HARİÇ)",
      j["done_50"] not in kalan and j["failed_35"] not in kalan, _sonuc)
check("S3b) Bebeğin en son başarılı işi (40 gün) KALDI", j["done_40_son"] in kalan, "")
check("S3c) 30 günden yeni ve süren işler kaldı",
      j["failed_5"] in kalan and j["processing_40"] in kalan, "")
check("S3d) Sahipsiz (bebek ya da kullanıcı silinmiş) işler silindi",
      j["sahipsiz_bebek"] not in kalan and j["sahipsiz_kullanici"] not in kalan, _sonuc)

# =============================================================================
# S4 — denetim / arşiv dosyaları
# =============================================================================
kls = saklama.denetim_arsiv_klasorleri()
for k in kls:
    k.mkdir(parents=True, exist_ok=True)
eski = kls[0] / "2026-08-01.html"
yeni = kls[0] / "2026-10-01.html"
eski_arsiv = kls[1] / "acik-sayac-kapat-20260801.json"
for f in (eski, yeni, eski_arsiv):
    f.write_text("x", "utf-8")
_31 = time.time() - 31 * 86400
os.utime(eski, (_31, _31))
os.utime(eski_arsiv, (_31, _31))
_n = saklama.eski_dosyalari_sil(kls)
check("S4a) Klasörler /data/denetim ve /data/arsiv (DENETIM_ROOT'tan)",
      kls[0].name == "denetim" and kls[1].name == "arsiv", kls)
check("S4b) 30 günden eski iki dosya silindi, yeni dosya kaldı",
      _n == 2 and not eski.exists() and not eski_arsiv.exists() and yeni.exists(), _n)

# =============================================================================
# S5 — onay kanıtı 10 yıl
# =============================================================================
db = SessionLocal()
simdi = datetime.now(timezone.utc)
for gun, tur in ((3653 + 5, "eski"), (3653 - 5, "yeni")):
    db.add(OnayKaniti(tur=tur, metin_surumu="v", onay=True, zaman=simdi, kaynak="kayit",
                      eposta_ozeti="x" * 64, hesap_silindi_at=simdi - timedelta(days=gun)))
db.commit()
_n = saklama.onay_kanitlarini_temizle(db)
db.commit()
_turler = {k.tur for k in db.query(OnayKaniti).all()}
db.close()
check("S5) 10 yılı geçen kanıt silindi, 10 yılı dolmayan kaldı",
      _n == 1 and "eski" not in _turler and "yeni" in _turler, _turler)

# =============================================================================
# S6 — device_name saklanmaz
# =============================================================================
r = client.post("/api/v1/notifications/register-token", headers=HB,
                json={"expo_token": "ExponentPushToken[abc]", "platform": "ios",
                      "device_name": "Ayşe'nin iPhone'u"})
r2 = client.post("/api/v1/notifications/register-token", headers=HB,
                 json={"expo_token": "ExponentPushToken[abc]", "platform": "ios",
                       "device_name": "Ayşe'nin iPhone'u"})
db = SessionLocal()
_p = db.query(PushToken).filter(PushToken.expo_token == "ExponentPushToken[abc]").one()
db.close()
check("S6) Cihaz adı saklanmadı (kayıt + güncelleme), istek yine 200",
      r.status_code == 200 and r2.status_code == 200 and _p.device_name is None,
      f"{r.status_code} {_p.device_name}")

# =============================================================================
# S7 — migration 0026
# =============================================================================
_mdb = _TMP.parent / "saklama_migration.db"
if _mdb.exists():
    _mdb.unlink()
_env = {**os.environ, "DATABASE_URL": f"sqlite:///{_mdb.as_posix()}"}


def _alembic(*arg):
    return subprocess.run([sys.executable, "-m", "alembic", *arg], cwd=ROOT, env=_env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


_r = _alembic("upgrade", "0025_bebek_olcumleri")
_c = sqlite3.connect(_mdb)
_c.execute("INSERT INTO push_tokens (id, user_id, expo_token, platform, device_name, "
           "last_seen_at, created_at) VALUES ('t1','u','tok','ios',"
           "'Ayşe''nin iPhone''u','2026-10-01','2026-10-01')")
_c.commit()
_c.close()
_r2 = _alembic("upgrade", "head")
_c = sqlite3.connect(_mdb)
_dn = _c.execute("SELECT device_name FROM push_tokens").fetchone()[0]
_tab = _c.execute("SELECT count(*) FROM sqlite_master WHERE name='onay_kanitlari'").fetchone()[0]
_c.close()
check("S7a) 0026: onay_kanitlari kuruldu, mevcut device_name boşaltıldı",
      _r.returncode == 0 and _r2.returncode == 0 and _tab == 1 and _dn is None,
      (_r.stderr[-200:], _r2.stderr[-200:], _dn))
_r3 = _alembic("downgrade", "0025_bebek_olcumleri")
check("S7b) downgrade çalışıyor", _r3.returncode == 0, _r3.stderr[-200:])

# =============================================================================
# S8 / S9 — loglar
# =============================================================================
class _Toplayici(logging.Handler):
    def __init__(self):
        super().__init__()
        self.satirlar: list[str] = []

    def emit(self, record):
        self.satirlar.append(record.getMessage())


t = _Toplayici()
logging.getLogger().addHandler(t)
check("S8a) Alıcı maskesi", mailer.maskele("anne.ornek@gmail.com") == "a***@g***.com",
      mailer.maskele("anne.ornek@gmail.com"))
_govde = "Parolanı sıfırla: tavsan://reset?token=GIZLI123"
mailer._console_send("anne.ornek@gmail.com", "Parola sıfırlama", _govde)
_log = "\n".join(t.satirlar)
check("S8b) Console modunda içerik, bağlantı ve e-posta loglanmadı",
      "GIZLI123" not in _log and "anne.ornek@gmail.com" not in _log
      and "a***@g***.com" in _log, _log[-200:])

_eski_yol = chatbot.CACHE_PATH
chatbot.CACHE_PATH = _TMP / "answer_cache_test.json"
chatbot._cache_state.update(loaded=True, entries=[], emb_matrix=None, emb_idx=[])
_soru = "Bebeğim gece neden ÖZELKELİME uyanıyor"
chatbot._cache_store(_soru, "6-9 ay", "cevap")
t.satirlar.clear()
_hit = chatbot._cache_lookup_entry(_soru, "6-9 ay")
_log = "\n".join(t.satirlar)
chatbot.CACHE_PATH = _eski_yol
check("S9) Önbellek isabetinde soru metni loglanmadı",
      _hit is not None and "Cache HIT" in _log and "ÖZELKELİME" not in _log
      and "özelkelime" not in _log.lower(), _log[-200:])
logging.getLogger().removeHandler(t)

# =============================================================================
# S10 — Topluluk is_mine
# =============================================================================
for h, ad in ((HB, "kalanAnne"), (_HD, "sesliAnne")):
    client.post("/api/v1/community/profile", headers=h, json={"nickname": ad})
r = client.post("/api/v1/community/threads", headers=HB,
                json={"title": "Gece uyanmaları hakkında", "body": "Gece çok uyanıyor, ne yapalım?",
                      "category": "uyku", "kategori": "gece_uyanmasi"})
ra = client.post("/api/v1/community/threads", headers=_HD,     # sıklık sınırı: ayrı kullanıcı
                 json={"title": "Anonim bir soru soruyorum", "body": "Anonim olarak soruyorum.",
                       "category": "uyku", "kategori": "gece_uyanmasi", "anonim": True})
_ben = {x["title"]: x for x in client.get("/api/v1/community/threads", headers=HB).json()["items"]}
_o = {x["title"]: x for x in client.get("/api/v1/community/threads", headers=_HD).json()["items"]}
_t, _a = "Gece uyanmaları hakkında", "Anonim bir soru soruyorum"
check("S10a) Kendi konusunda is_mine=true (benim ile aynı)",
      r.status_code in (200, 201) and _ben.get(_t, {}).get("is_mine") is True
      and _ben[_t].get("benim") is True, f"{r.status_code} {str(_ben.get(_t))[:120]}")
check("S10b) Anonim kendi konusunda is_mine=true, author_id yok; başkası için false",
      ra.status_code in (200, 201) and _o.get(_a, {}).get("is_mine") is True
      and _o[_a].get("author_id") is None and _ben.get(_a, {}).get("is_mine") is False,
      f"{ra.status_code} {ra.text[:160]} | {str(_o.get(_a))[:120]}")
check("S10c) Başkasının konusunda is_mine=false",
      _o.get(_t, {}).get("is_mine") is False, str(_o.get(_t))[:120])
_tid = _ben.get(_t, {}).get("id")
_d = client.get(f"/api/v1/community/threads/{_tid}", headers=HB).json() if _tid else {}
check("S10d) Konu detayında is_mine", _d.get("is_mine") is True, str(_d)[:120])

check("Z) Canlı LLM çağrısı yapılmadı", canli_cagri_sayisi() == 0, canli_cagri_sayisi())

# =============================================================================
print("\n" + "=" * 74)
print("SAKLAMA VE SİLME — iç kayıtlar, loglar, is_mine")
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
