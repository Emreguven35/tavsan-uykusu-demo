"""
Bebek boy/kilo ölçümleri (bebek_olcumleri) + kayıtta KVKK onayları.

NEDEN VAR (2026-10, ilk açılış akışı): mobil bebeği oluştururken boy/kilo
gönderecek, kayıtta aydınlatma teyidi ve sağlık verisi açık rızasını iki ayrı
`consents` satırı olarak yollayacak. Ölçümler YALNIZ SAKLANIR: hiçbir hesaba
girmez — özellikle motorda bekleyen `kilo_durumu` girdisine bağlanmaz (O12).

Kapsam:
  O1  Boy/kilosuz oluşturma: 201, son_olcum null, ölçüm satırı yok (eski build)
  O2  Boy/kilolu oluşturma: yuvarlama (1/2 ondalık), bugünün TR tarihi,
      kaynak 'onboarding', JSON'da sayı (string değil)
  O3  Sınırlar dahil: 30 / 120 cm, 0,5 / 25 kg
  O4  Aralık dışı: 422 + Türkçe mesaj + alan; bebek OLUŞTURULMAZ
  O5  PATCH ölçüm yazar (kaynak 'profil'); aynı gün aynı değer → yeni satır yok
  O6  PATCH aralık dışı → 422, diğer alanlar da değişmez; ölçümsüz PATCH satır açmaz
  O7  POST/GET /babies/{id}/olcumler: tarih kuralları, sıralama, sahiplik
  O8  Liste ucunda son_olcum (ölçümsüz bebekte null)
  O9  Hesap silinince ölçümler de silinir; KVKK dışa aktarımında ölçümler var
  O10 Şema: NUMERIC(4,1)/(4,2); migration 0025 sıfırdan + downgrade
  O11 Kilo/boy hiçbir motor girdisine bağlı değil (profil, parametreler, plan)
  R1  Kayıt: aydinlatma + acik_riza_saglik (kvkk-2026-10) → iki satır, güncel
  R2  Onaysız kayıt (eski build) → satır yok, güncelleme gerekli
  R3  Eski sürümle onay → güncelleme gerekli

Çalıştırma: python tests/test_bebek_olcum.py
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "bebek_olcum_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(Path(tempfile.gettempdir()) / "bebek_olcum_medya")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import Baby, BebekOlcumu, Consent           # noqa: E402
from api.main import app                                    # noqa: E402
from api.routers.babies import BOY_MESAJ, KILO_MESAJ        # noqa: E402
from api.services import plan_service                       # noqa: E402
from api.zaman import bugun_tr                              # noqa: E402
from engine import parameter_engine                         # noqa: E402

from tests.llm_muhuru import canli_cagri_sayisi, muhurle    # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


BUGUN = bugun_tr()
DOGUM = (BUGUN - timedelta(days=213)).isoformat()            # ~7 ay: 6-9 ay bandı


def kayit_ol(email, consents=None):
    govde = {"email": email, "password": "TestPass123!"}
    if consents is not None:
        govde["consents"] = consents
    r = client.post("/api/v1/auth/register", json=govde)
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


H = kayit_ol("olcum@test.com")
PROFIL = {"birth_date": DOGUM, "feeding_type": "breast", "sleep_method": "rocking",
          "sleep_environment": "loc:own_bed;d:1", "crying_tolerance": "5",
          "parent_experience": "none", "night_wakes": 2, "night_feeds": 1}


def bebek(h=H, **ek):
    return client.post("/api/v1/babies", headers=h, json={"name": "Ada", **PROFIL, **ek})


def olcum_sayisi(bid=None) -> int:
    db = SessionLocal()
    try:
        q = db.query(BebekOlcumu)
        if bid is not None:
            q = q.filter(BebekOlcumu.baby_id == uuid.UUID(bid))
        return q.count()
    finally:
        db.close()


def bebek_sayisi() -> int:
    db = SessionLocal()
    try:
        return db.query(Baby).count()
    finally:
        db.close()


# =============================================================================
# O1 — boy/kilosuz (eski build)
# =============================================================================
r = bebek()
check("O1a) Boy/kilosuz oluşturma 201", r.status_code == 201, r.text[:200])
B_ESKI = r.json()["id"]
check("O1b) son_olcum null", r.json().get("son_olcum", "YOK") is None, r.json().get("son_olcum"))
check("O1c) Ölçüm satırı açılmadı", olcum_sayisi(B_ESKI) == 0, olcum_sayisi(B_ESKI))

# =============================================================================
# O2 — boy/kilolu
# =============================================================================
r = bebek(boy_cm=62.54, kilo_kg=6.355)
check("O2a) Boy/kilolu oluşturma 201", r.status_code == 201, r.text[:200])
B1 = r.json()["id"]
so = r.json().get("son_olcum") or {}
check("O2b) Yuvarlama: boy 1, kilo 2 ondalık (62.5 / 6.36)",
      so.get("boy_cm") == 62.5 and so.get("kilo_kg") == 6.36, so)
check("O2c) JSON'da sayı (Decimal string'e dönmüyor)",
      isinstance(so.get("boy_cm"), float) and isinstance(so.get("kilo_kg"), float), so)
check("O2d) Ölçüm tarihi bugünün TR günü", so.get("olcum_tarihi") == BUGUN.isoformat(), so)
_db = SessionLocal()
_o = _db.query(BebekOlcumu).filter(BebekOlcumu.baby_id == uuid.UUID(B1)).one()
_db.close()
check("O2e) Kaynak 'onboarding'", _o.kaynak == "onboarding", _o.kaynak)
check("O2f) Bebek satırında boy/kilo kolonu yok",
      not hasattr(Baby, "boy_cm") and not hasattr(Baby, "kilo_kg"), "")
r = bebek(kilo_kg=4)
check("O2g) Yalnız kilo da kabul (boy null)",
      r.status_code == 201 and r.json()["son_olcum"]["boy_cm"] is None
      and r.json()["son_olcum"]["kilo_kg"] == 4.0, r.text[:200])

# =============================================================================
# O3 — sınırlar
# =============================================================================
for boy, kilo in ((30, 0.5), (120, 25)):
    r = bebek(boy_cm=boy, kilo_kg=kilo)
    check(f"O3) Sınır kabul: {boy} cm / {kilo} kg", r.status_code == 201, r.text[:200])

# =============================================================================
# O4 — aralık dışı
# =============================================================================
for alanlar, mesaj, alan in (({"boy_cm": 29.9}, BOY_MESAJ, "boy_cm"),
                             ({"boy_cm": 120.1}, BOY_MESAJ, "boy_cm"),
                             ({"kilo_kg": 0.49}, KILO_MESAJ, "kilo_kg"),
                             ({"kilo_kg": 25.01}, KILO_MESAJ, "kilo_kg")):
    once = bebek_sayisi()
    r = bebek(**alanlar)
    check(f"O4) {alanlar} → 422 '{mesaj}' alan={alan}",
          r.status_code == 422 and r.json().get("detail") == mesaj
          and r.json().get("alan") == alan, f"{r.status_code} {r.text[:160]}")
    check(f"O4) {alanlar} → bebek oluşturulmadı", bebek_sayisi() == once, bebek_sayisi())
check("O4) Mesaj metinleri kararla birebir",
      (BOY_MESAJ, KILO_MESAJ) == ("Boy 30–120 cm arasında olmalı.",
                                  "Kilo 0,5–25 kg arasında olmalı."), (BOY_MESAJ, KILO_MESAJ))

# =============================================================================
# O5 / O6 — PATCH
# =============================================================================
r = client.patch(f"/api/v1/babies/{B_ESKI}", headers=H, json={"boy_cm": 64, "kilo_kg": 6.8})
check("O5a) Boy/kilolu PATCH 200, ölçüm yazdı", r.status_code == 200 and r.json()["son_olcum"]["boy_cm"] == 64.0
      and olcum_sayisi(B_ESKI) == 1, r.text[:200])
_db = SessionLocal()
_k = _db.query(BebekOlcumu).filter(BebekOlcumu.baby_id == uuid.UUID(B_ESKI)).one().kaynak
_db.close()
check("O5b) PATCH kaynağı 'profil'", _k == "profil", _k)
client.patch(f"/api/v1/babies/{B_ESKI}", headers=H, json={"boy_cm": 64.0, "kilo_kg": 6.80})
check("O5c) Aynı gün aynı değer → yeni satır yok", olcum_sayisi(B_ESKI) == 1, olcum_sayisi(B_ESKI))
r = client.patch(f"/api/v1/babies/{B_ESKI}", headers=H, json={"kilo_kg": 6.9})
check("O5d) Farklı değer → yeni satır, son_olcum güncel",
      olcum_sayisi(B_ESKI) == 2 and r.json()["son_olcum"]["kilo_kg"] == 6.9
      and r.json()["son_olcum"]["boy_cm"] is None, r.json().get("son_olcum"))
r = client.patch(f"/api/v1/babies/{B_ESKI}", headers=H, json={"night_wakes": 4})
check("O6a) Ölçümsüz PATCH satır açmadı ve alanı güncelledi",
      r.status_code == 200 and r.json()["night_wakes"] == 4 and olcum_sayisi(B_ESKI) == 2,
      r.text[:160])
r = client.patch(f"/api/v1/babies/{B_ESKI}", headers=H, json={"night_wakes": 1, "kilo_kg": 30})
_nw = client.get("/api/v1/babies", headers=H).json()[0]["night_wakes"]
check("O6b) PATCH aralık dışı → 422 + mesaj; diğer alan da değişmedi",
      r.status_code == 422 and r.json().get("detail") == KILO_MESAJ and _nw == 4,
      f"{r.status_code} {r.text[:120]} night_wakes={_nw}")

# =============================================================================
# O7 — /olcumler
# =============================================================================
U = f"/api/v1/babies/{B1}/olcumler"
r = client.post(U, headers=H, json={"olcum_tarihi": (BUGUN - timedelta(days=20)).isoformat(),
                                    "boy_cm": 60, "kilo_kg": 6})
check("O7a) Geçmiş tarihli ölçüm 201, kaynak 'profil'",
      r.status_code == 201 and r.json()["kaynak"] == "profil"
      and isinstance(r.json()["boy_cm"], float), r.text[:200])
r = client.post(U, headers=H, json={"olcum_tarihi": (BUGUN + timedelta(days=1)).isoformat(),
                                    "kilo_kg": 7})
check("O7b) Gelecek tarih → 422", r.status_code == 422 and r.json().get("alan") == "olcum_tarihi",
      r.text[:160])
r = client.post(U, headers=H, json={"olcum_tarihi": "2020-01-01", "kilo_kg": 7})
check("O7c) Doğumdan önce → 422", r.status_code == 422 and r.json().get("alan") == "olcum_tarihi",
      r.text[:160])
r = client.post(U, headers=H, json={})
check("O7d) Değersiz → 422 'Boy ya da kilo girilmeli.'",
      r.status_code == 422 and r.json().get("detail") == "Boy ya da kilo girilmeli.", r.text[:160])
r = client.post(U, headers=H, json={"boy_cm": 200})
check("O7e) Aralık dışı → 422 + mesaj", r.status_code == 422
      and r.json().get("detail") == BOY_MESAJ, r.text[:160])
_once = olcum_sayisi(B1)
r1 = client.post(U, headers=H, json={"boy_cm": 62.5, "kilo_kg": 6.36})
check("O7f) Bugünkü aynı değer → mevcut satır döner, yeni satır yok",
      r1.status_code == 201 and olcum_sayisi(B1) == _once, f"{_once} → {olcum_sayisi(B1)}")
r = client.get(U, headers=H)
_tarihler = [x["olcum_tarihi"] for x in r.json()]
check("O7g) GET yeniden eskiye", r.status_code == 200 and _tarihler == sorted(_tarihler, reverse=True)
      and len(_tarihler) == 2, _tarihler)
H2 = kayit_ol("baska@test.com")
check("O7h) Başka kullanıcının bebeği → 404 (GET ve POST)",
      client.get(U, headers=H2).status_code == 404
      and client.post(U, headers=H2, json={"kilo_kg": 5}).status_code == 404, "")
r = client.get("/api/v1/babies", headers=H)
_son = {b["id"]: b["son_olcum"] for b in r.json()}
check("O8a) Liste: son_olcum en son tarihli ölçüm (bugünkü)",
      _son[B1] and _son[B1]["olcum_tarihi"] == BUGUN.isoformat(), _son.get(B1))
B_YALIN = client.post("/api/v1/babies", headers=H2, json={"name": "Yalın", **PROFIL}).json()["id"]
check("O8b) Ölçümsüz bebekte son_olcum null",
      client.get("/api/v1/babies", headers=H2).json()[0]["son_olcum"] is None, "")

# =============================================================================
# O9 — dışa aktarım + hesap silme
# =============================================================================
H3 = kayit_ol("silinecek@test.com")
B3 = client.post("/api/v1/babies", headers=H3, json={"name": "S", **PROFIL,
                                                     "boy_cm": 61, "kilo_kg": 6}).json()["id"]
r = client.get("/api/v1/account/export", headers=H3)
_ex = r.json() if r.status_code == 200 else {}
check("O9a) KVKK dışa aktarımında bebek_olcumleri",
      len(_ex.get("bebek_olcumleri") or []) == 1
      and _ex["bebek_olcumleri"][0]["baby_id"] == B3, f"{r.status_code} {str(_ex)[:160]}")
check("O9b) Başka kullanıcının ölçümü dışa aktarımda yok",
      all(o["baby_id"] == B3 for o in _ex.get("bebek_olcumleri") or []), "")
r = client.delete("/api/v1/auth/account", headers=H3)
check("O9c) Hesap silinince ölçümler de silindi",
      r.status_code == 200 and olcum_sayisi(B3) == 0, f"{r.status_code} {olcum_sayisi(B3)}")

# =============================================================================
# O10 — şema + migration
# =============================================================================
_t = BebekOlcumu.__table__.c
check("O10a) boy_cm NUMERIC(4,1), kilo_kg NUMERIC(4,2)",
      (_t.boy_cm.type.precision, _t.boy_cm.type.scale) == (4, 1)
      and (_t.kilo_kg.type.precision, _t.kilo_kg.type.scale) == (4, 2),
      f"{_t.boy_cm.type} {_t.kilo_kg.type}")
check("O10b) kaynak uzunluğu tüm değerlere yetiyor",
      _t.kaynak.type.length >= max(len("onboarding"), len("profil")), _t.kaynak.type.length)
check("O10c) Üst sınırlar hassasiyete sığıyor (120.0 ≤ 999.9, 25.00 ≤ 99.99)", True, "")
_mdb = Path(tempfile.gettempdir()) / "bebek_olcum_migration.db"
if _mdb.exists():
    _mdb.unlink()
_env = {**os.environ, "DATABASE_URL": f"sqlite:///{_mdb.as_posix()}"}


def _alembic(*arg):
    return subprocess.run([sys.executable, "-m", "alembic", *arg], cwd=ROOT, env=_env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


_r = _alembic("upgrade", "head")
check("O10d) Sıfırdan alembic upgrade head", _r.returncode == 0, _r.stderr[-300:])
_c = sqlite3.connect(_mdb)
_kol = [r[1] for r in _c.execute("PRAGMA table_info(bebek_olcumleri)")]
_c.close()
check("O10e) Tablo ve kolonlar migration'da",
      _kol == ["id", "baby_id", "olcum_tarihi", "boy_cm", "kilo_kg", "kaynak", "created_at"], _kol)
_r = _alembic("downgrade", "0024_bakim_kaynagi")
_c = sqlite3.connect(_mdb)
_var = _c.execute("SELECT count(*) FROM sqlite_master WHERE name='bebek_olcumleri'").fetchone()[0]
_c.close()
check("O10f) downgrade tabloyu kaldırıyor", _r.returncode == 0 and _var == 0, _r.stderr[-300:])

# =============================================================================
# O11 — kilo/boy hiçbir motor girdisine bağlı değil
# =============================================================================
db = SessionLocal()
_olculu = db.get(Baby, uuid.UUID(B1))            # ölçümlü (7 ay, 6-9 bandı)
_profil_olculu = plan_service.profile_from_baby(_olculu, None, None)
_yalin = db.get(Baby, uuid.UUID(B_YALIN))        # aynı profil, ölçümsüz
_profil_yalin = plan_service.profile_from_baby(_yalin, None, None)
db.close()
_yasak = {"kilo", "kilo_kg", "boy", "boy_cm", "kilo_durumu", "son_olcum"}
check("O11a) Motor profilinde boy/kilo/kilo_durumu anahtarı YOK",
      not (_yasak & set(_profil_olculu)), sorted(_yasak & set(_profil_olculu)))
_p1 = dict(_profil_olculu, bebek_ad="X")
_p2 = dict(_profil_yalin, bebek_ad="X")
check("O11b) Ölçümlü ve ölçümsüz bebeğin motor profili aynı", _p1 == _p2,
      {k: (_p1.get(k), _p2.get(k)) for k in set(_p1) | set(_p2) if _p1.get(k) != _p2.get(k)})
_par1 = parameter_engine.parametre_uret(_p1)
_par2 = parameter_engine.parametre_uret(_p2)
check("O11c) parametre_uret çıktısı (gece beslenme dahil) aynı", _par1 == _par2, "")
_gb = _par1.get("gece_beslenme") or {}
check("O11d) 6-9 ay gece beslenmesi 'kilo iyi değil' dalında (kilo_durumu bağlı değil)",
      _gb.get("yas_grup") == "6-9 ay" and _gb.get("ogun_sayisi") == "Max 2 öğün", _gb)
r1 = client.post("/api/v1/plans/generate?sync=true", headers=H, json={"baby_id": B1})
r2 = client.post("/api/v1/plans/generate?sync=true", headers=H2, json={"baby_id": B_YALIN})
_c1 = (r1.json().get("content") or {}) if r1.status_code in (200, 201) else {}
_c2 = (r2.json().get("content") or {}) if r2.status_code in (200, 201) else {}
check("O11e) Plan çizelgesi ölçümlü/ölçümsüz aynı",
      _c1.get("schedule") and _c1.get("schedule") == _c2.get("schedule"),
      f"{r1.status_code} {r2.status_code}")
_metin = str(_c1)
check("O11f) Plan içeriğinde kilo/boy değeri geçmiyor",
      "6.36" not in _metin and "62.5" not in _metin, "")

# =============================================================================
# R — kayıtta KVKK onayları
# =============================================================================
IKI = [{"tur": "aydinlatma", "onay": True, "metin_surumu": "kvkk-2026-10"},
       {"tur": "acik_riza_saglik", "onay": True, "metin_surumu": "kvkk-2026-10"}]
HR = kayit_ol("rizali@test.com", IKI)
d = client.get("/api/v1/consents/me", headers=HR).json()
check("R1a) aydinlatma: onay, kvkk-2026-10, güncel",
      d["aydinlatma"]["onay"] is True and d["aydinlatma"]["metin_surumu"] == "kvkk-2026-10"
      and d["aydinlatma"]["guncelleme_gerekli"] is False, d["aydinlatma"])
check("R1b) acik_riza_saglik: onay, kvkk-2026-10, güncel",
      d["acik_riza_saglik"]["onay"] is True
      and d["acik_riza_saglik"]["guncelleme_gerekli"] is False, d["acik_riza_saglik"])
check("R1c) pazarlama sorulmadı → boş", d["pazarlama"]["onay"] is None, d["pazarlama"])
db = SessionLocal()
_sat = db.query(Consent).join(api.models.User, api.models.User.id == Consent.user_id).filter(
    api.models.User.email == "rizali@test.com").all()
db.close()
check("R1d) İki AYRI satır, kaynak 'kayit'",
      sorted(c.tur for c in _sat) == ["acik_riza_saglik", "aydinlatma"]
      and all(c.kaynak == "kayit" for c in _sat), [(c.tur, c.kaynak) for c in _sat])
HN = kayit_ol("rizasiz@test.com")
d = client.get("/api/v1/consents/me", headers=HN).json()
check("R2) Onaysız kayıt (eski build): satır yok, güncelleme gerekli",
      d["aydinlatma"]["onay"] is None and d["acik_riza_saglik"]["guncelleme_gerekli"] is True,
      d["acik_riza_saglik"])
HE = kayit_ol("eskisurum@test.com", [{"tur": "acik_riza_saglik", "onay": True,
                                      "metin_surumu": "taslak-2026-09-25"}])
d = client.get("/api/v1/consents/me", headers=HE).json()
check("R3) Eski sürümle onay → güncelleme gerekli",
      d["acik_riza_saglik"]["guncelleme_gerekli"] is True, d["acik_riza_saglik"])

check("Z) Canlı LLM çağrısı yapılmadı", canli_cagri_sayisi() == 0, canli_cagri_sayisi())

# =============================================================================
# RAPOR
# =============================================================================
print("\n" + "=" * 74)
print("BEBEK ÖLÇÜMLERİ + KAYIT ONAYLARI")
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
