"""
13. gün eğitim değerlendirmesi (2026-11-01) — deterministik, LLM yok.

  T  Karar tablosu: her cevap kombinasyonu
  V  422: uzun_aglama kuralı ve eksik alan; 409: gün < 13 / tamamlandı
  G  egitim_gunu sabitleme: 18. gün → 13 (gerçek 18), 13. gün, 10. gün; mezunda sabitlenmez
  D  devam: tarih +3, satır, aşama boşalır (eski beyan satırda); durum
     degerlendirme_bekliyor → uzatma → 3 gün sonra degerlendirme_bekliyor;
     uzatmada erken yeniden değerlendirme serbest
  M  mezun: training_completed_at bugün, tamamlandi, tekrar 409
  Y  yeniden_basla: yarın 1. gün, eski deneme satırda, kayıt/plan silinmez,
     bugün "henüz başlamadı", 2. denemenin 3. günü, 2. denemenin 13. günü yeniden bekliyor
  S  Sor bağlamı: değerlendirme satırı ve deneme numarası
  K  Şema: sonuc sütunu tüm değerleri alır; KVKK dökümünde tablo var

Çalıştırma: python tests/test_egitim_degerlendirme.py
"""
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "egitim_degerlendirme_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["BETA_MODE"] = "true"
os.environ["SOR_BAGLAM_V2"] = "true"

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.main import app                                    # noqa: E402
from api.models import (Baby, EgitimDegerlendirme, SleepLog, SleepPlan,  # noqa: E402
                        User)
from api.services import egitim_degerlendirme as ed         # noqa: E402
from api.services import kvkk, sor_baglam                   # noqa: E402
from api.zaman import TR, bugun_tr, saat_sabitle            # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, canli_cagri_sayisi, muhurle  # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


BUGUN = bugun_tr()
tok = client.post("/api/v1/auth/register", json={
    "email": "degerlendirme@gercek.com", "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
db = SessionLocal()


def bebek(gun_no: int, asama: str | None = None) -> Baby:
    """Eğitim planlı 7 aylık bebek; bugün eğitimin `gun_no`. günü."""
    bid = client.post("/api/v1/babies", headers=H, json={
        "name": "Ada", "night_wakes": 2,
        "birth_date": (BUGUN - timedelta(days=216)).isoformat(), **TAM_PROFIL}).json()["id"]
    r = client.post("/api/v1/plans/generate?sync=true", headers=H, json={"baby_id": bid})
    assert r.status_code in (200, 201), r.text[:200]
    db.expire_all()
    b = db.get(Baby, uuid.UUID(bid))
    b.training_started_at = BUGUN - timedelta(days=gun_no - 1)
    b.training_completed_at = None
    b.mevcut_asama = asama
    db.commit()
    return b


def degerlendir(b: Baby, **govde):
    return client.post(f"/api/v1/babies/{b.id}/egitim-degerlendirme", headers=H, json=govde)


def adaptasyon(b: Baby) -> dict:
    r = client.get(f"/api/v1/plans/today?baby_id={b.id}", headers=H)
    assert r.status_code == 200, r.text[:200]
    return (r.json()["content"] or {}).get("adaptation") or {}


def an(gun, hhmm="15:00"):
    return datetime(gun.year, gun.month, gun.day, int(hhmm[:2]), int(hhmm[3:]), tzinfo=TR)


# =============================================================================
# T — karar tablosu
# =============================================================================
for (k, y, u), bek in {(True, True, None): "mezun", (False, True, None): "devam",
                       (True, False, True): "yeniden_basla", (False, False, True): "yeniden_basla",
                       (True, False, False): "devam", (False, False, False): "devam"}.items():
    check(f"T) kendi={k} yatir_cik={y} uzun={u} → {bek}", ed.karar(k, y, u) == bek, ed.karar(k, y, u))

# =============================================================================
# V — 422 / 409
# =============================================================================
b18 = bebek(18, asama="kapi")
for govde, ad in (({"kendi_donuyor": True, "yatir_cik": False}, "yatır-çık yok, uzun_aglama eksik"),
                  ({"kendi_donuyor": True, "yatir_cik": False, "uzun_aglama": None}, "uzun_aglama null"),
                  ({"kendi_donuyor": True, "yatir_cik": True, "uzun_aglama": True}, "yatır-çık var, uzun_aglama dolu"),
                  ({"kendi_donuyor": False, "yatir_cik": True, "uzun_aglama": False}, "yatır-çık var, uzun_aglama false"),
                  ({"yatir_cik": True}, "kendi_donuyor eksik")):
    r = degerlendir(b18, **govde)
    check(f"V) 422: {ad}", r.status_code == 422, (r.status_code, r.text[:150]))
check("V) 422'lerden sonra satır yok",
      db.query(EgitimDegerlendirme).filter_by(baby_id=b18.id).count() == 0)

b5 = bebek(5)
r = degerlendir(b5, kendi_donuyor=True, yatir_cik=True)
check("V) 5. gün → 409 DEGERLENDIRME_ZAMANI_DEGIL (egitim_durumu devam)",
      r.status_code == 409 and r.json().get("hata") == "DEGERLENDIRME_ZAMANI_DEGIL"
      and r.json().get("egitim_durumu") == "devam", (r.status_code, r.text[:200]))

# =============================================================================
# G — egitim_gunu sabitleme
# =============================================================================
a = adaptasyon(b18)
check("G) 18. gün: egitim_gunu 13, egitim_gunu_gercek 18",
      a.get("egitim_gunu") == 13 and a.get("egitim_gunu_gercek") == 18, a.get("egitim_gunu"))
check("G) 18. gün: egitim_baslangic_gunu da 13", a.get("egitim_baslangic_gunu") == 13,
      a.get("egitim_baslangic_gunu"))
check("G) 18. gün değerlendirmesiz → degerlendirme_bekliyor",
      a.get("egitim_durumu") == "degerlendirme_bekliyor", a.get("egitim_durumu"))
a = adaptasyon(b5)
check("G) 5. gün: egitim_gunu 5 = gerçek, durum devam",
      a.get("egitim_gunu") == 5 and a.get("egitim_gunu_gercek") == 5
      and a.get("egitim_durumu") == "devam", a)
b13 = bebek(13)
a = adaptasyon(b13)
check("G) 13. gün: egitim_gunu 13 ve degerlendirme_bekliyor",
      a.get("egitim_gunu") == 13 and a.get("egitim_durumu") == "degerlendirme_bekliyor",
      (a.get("egitim_gunu"), a.get("egitim_durumu")))
check("G) 12. gün durumu devam", ed.durum(db, b13, BUGUN - timedelta(days=1)) == "devam")

# =============================================================================
# D — devam ve durum geçişleri
# =============================================================================
r = degerlendir(b18, kendi_donuyor=False, yatir_cik=True)
j = r.json()
check("D) 200, sonuc devam, tarih bugün+3",
      r.status_code == 200 and j["sonuc"] == "devam"
      and j["yeniden_degerlendirme_tarihi"] == (BUGUN + timedelta(days=3)).isoformat(), j)
db.expire_all()
satir = db.query(EgitimDegerlendirme).filter_by(baby_id=b18.id).one()
check("D) Satır cevaplarla: kendi F, yatır T, uzun None, 18. gün, deneme 1",
      (satir.kendi_donuyor, satir.yatir_cik, satir.uzun_aglama, satir.egitim_gunu,
       satir.deneme_no, satir.sonuc) == (False, True, None, 18, 1, "devam"),
      (satir.kendi_donuyor, satir.yatir_cik, satir.uzun_aglama, satir.egitim_gunu))
check("D) Aşama yatır-çık: bebekte mevcut_asama boşaldı, eski beyan 'kapi' satırda",
      db.get(Baby, b18.id).mevcut_asama is None and satir.mevcut_asama == "kapi")
a = adaptasyon(b18)
check("D) Değerlendirmeden sonra uzatma; egitim_gunu hâlâ 13",
      a.get("egitim_durumu") == "uzatma" and a.get("egitim_gunu") == 13, a.get("egitim_durumu"))
check("D) +2 gün hâlâ uzatma", ed.durum(db, b18, BUGUN + timedelta(days=2)) == "uzatma")
check("D) +3 gün degerlendirme_bekliyor",
      ed.durum(db, b18, BUGUN + timedelta(days=3)) == "degerlendirme_bekliyor")
with saat_sabitle(an(BUGUN + timedelta(days=3))):
    a = adaptasyon(b18)
check("D) /plans/today +3 gün: degerlendirme_bekliyor, egitim_gunu 13, gerçek 21",
      a.get("egitim_durumu") == "degerlendirme_bekliyor" and a.get("egitim_gunu") == 13
      and a.get("egitim_gunu_gercek") == 21,
      (a.get("egitim_durumu"), a.get("egitim_gunu"), a.get("egitim_gunu_gercek")))
d = sor_baglam.gun_durumu(db, db.get(Baby, b18.id), an(BUGUN))
check("D) Sor uzatmada aşama yatır-çık (beyan yok)",
      d["durum"] == "uzatma" and d["uzatma_beyan"] is None, (d["durum"], d.get("uzatma_beyan")))
s = sor_baglam.zaman_satirlari(db, db.get(Baby, b18.id), simdi=an(BUGUN))
check("S) Bağlam: '13. gün değerlendirmesi' + 'devam, gece kendi dönmüyor, yatır-çık yapılabiliyor'",
      "13. gün değerlendirmesi" in s
      and "devam, gece kendi dönmüyor, yatır-çık yapılabiliyor" in s
      and f"yeniden değerlendirme {(BUGUN + timedelta(days=3)).isoformat()}" in s, s)
check("S) Uzatmada uygulamanın gösterdiği gün 13", "'Gün 13' görünür" in s, s)
check("S) 1. denemede 'Deneme:' satırı yok", "Deneme:" not in s, s)
r = degerlendir(b18, kendi_donuyor=True, yatir_cik=True)
check("D) Uzatmada erken yeniden değerlendirme serbest → mezun",
      r.status_code == 200 and r.json()["sonuc"] == "mezun", r.text[:200])

# =============================================================================
# M — mezun
# =============================================================================
r = degerlendir(b13, kendi_donuyor=True, yatir_cik=True)
db.expire_all()
check("M) 13. gün mezun: yanıt tarihsiz", r.status_code == 200 and r.json() == {
    "sonuc": "mezun", "yeniden_degerlendirme_tarihi": None}, r.text[:200])
check("M) training_completed_at = bugün", db.get(Baby, b13.id).training_completed_at == BUGUN)
a = adaptasyon(b13)
check("M) durum tamamlandi", a.get("egitim_durumu") == "tamamlandi", a.get("egitim_durumu"))
with saat_sabitle(an(BUGUN + timedelta(days=5))):
    a = adaptasyon(b13)
check("M) Tamamlanmış eğitimde egitim_gunu sabitlenmez (18)",
      a.get("egitim_gunu") == 18 and a.get("egitim_gunu_gercek") == 18, a.get("egitim_gunu"))
r = degerlendir(b13, kendi_donuyor=True, yatir_cik=True)
check("M) Tamamlanmışta tekrar → 409 (tamamlandi)",
      r.status_code == 409 and r.json().get("egitim_durumu") == "tamamlandi", r.text[:200])
s = sor_baglam.zaman_satirlari(db, db.get(Baby, b13.id), simdi=an(BUGUN))
check("S) Mezun bağlamı: tamamlandı + 'mezun, gece kendi dönüyor, yatır-çık yapılabiliyor'",
      "program tamamlandı" in s and "mezun, gece kendi dönüyor, yatır-çık yapılabiliyor" in s, s)

# =============================================================================
# Y — yeniden başla
# =============================================================================
b14 = bebek(14, asama="esik")
ESKI = b14.training_started_at
db.add(SleepLog(user_id=b14.user_id, baby_id=b14.id, type="sleep", client_id="y-1",
                started_at=an(BUGUN - timedelta(days=1), "20:00"),
                ended_at=an(BUGUN, "06:30")))
db.commit()
adaptasyon(b14)
kayit_once = db.query(SleepLog).filter_by(baby_id=b14.id).count()
plan_once = db.query(SleepPlan).filter_by(baby_id=b14.id).count()
r = degerlendir(b14, kendi_donuyor=True, yatir_cik=False, uzun_aglama=True)
db.expire_all()
b14 = db.get(Baby, b14.id)
check("Y) sonuc yeniden_basla, tarih null", r.status_code == 200 and r.json() == {
    "sonuc": "yeniden_basla", "yeniden_degerlendirme_tarihi": None}, r.text[:200])
check("Y) Eğitim YARIN başlar; completed ve aşama boş",
      b14.training_started_at == BUGUN + timedelta(days=1)
      and b14.training_completed_at is None and b14.mevcut_asama is None,
      (b14.training_started_at, b14.mevcut_asama))
satir = db.query(EgitimDegerlendirme).filter_by(baby_id=b14.id).one()
check("Y) Eski deneme satırda: başlangıç, 14. gün, 'esik', yeni başlangıç, deneme 1",
      (satir.egitim_baslangic, satir.egitim_gunu, satir.mevcut_asama, satir.yeni_baslangic,
       satir.deneme_no, satir.uzun_aglama)
      == (ESKI, 14, "esik", BUGUN + timedelta(days=1), 1, True),
      (satir.egitim_baslangic, satir.egitim_gunu, satir.mevcut_asama))
check("Y) Uyku kayıtları ve planlar silinmedi",
      db.query(SleepLog).filter_by(baby_id=b14.id).count() == kayit_once
      and db.query(SleepPlan).filter_by(baby_id=b14.id).count() == plan_once)
check("Y) deneme_no 2", ed.deneme_no(db, b14) == 2)
check("Y) Bugün durum devam (başlangıç yarın)", ed.durum(db, b14, BUGUN) == "devam")
a = adaptasyon(b14)
check("Y) /plans/today: egitim_gunu 0 (gün gizli), durum devam",
      a.get("egitim_gunu") == 0 and a.get("egitim_durumu") == "devam",
      (a.get("egitim_gunu"), a.get("egitim_durumu")))
r = degerlendir(b14, kendi_donuyor=True, yatir_cik=True)
check("Y) Yeni denemede hemen tekrar → 409", r.status_code == 409, r.status_code)
d = sor_baglam.gun_durumu(db, b14, an(BUGUN))
check("Y) Sor: bugün 'baslamadi' (0. gün denmez)", d["durum"] == "baslamadi", d["durum"])
s = sor_baglam.zaman_satirlari(db, b14, simdi=an(BUGUN))
check("S) Bağlam bugün: başlamadı + 2. deneme yarın 1. gün + 'yeniden başla ... başlıyor'",
      "henüz başlamadı" in s and "2. denemesi" in s and "Eğitimin 0." not in s
      and "yeniden başla, gece kendi dönüyor, yatır-çık henüz yok, 30 dk üzeri ağlama var" in s
      and "yeniden başlıyor" in s, s)
g3 = BUGUN + timedelta(days=3)                     # yarın 1. gün → +3 = 3. gün
s = sor_baglam.zaman_satirlari(db, b14, simdi=an(g3))
check("S) 2. denemenin 3. günü: 'Deneme: 2. deneme, 3. gün' + 'Eğitimin 3. günü'",
      "Deneme: 2. deneme, 3. gün" in s and "Eğitimin 3. günü" in s and "yeniden başladı" in s, s)
g13 = BUGUN + timedelta(days=13)
check("Y) 2. denemenin 13. günü → degerlendirme_bekliyor (1. denemenin satırı sayılmaz)",
      ed.durum(db, b14, g13) == "degerlendirme_bekliyor"
      and ed.son_degerlendirme(db, b14, g13) is None)

# =============================================================================
# K — şema ve KVKK
# =============================================================================
uz = EgitimDegerlendirme.__table__.c.sonuc.type.length
check("K) sonuc sütunu tüm değerleri alır", all(len(x) <= uz for x in ed.SONUCLAR), uz)
check("K) mevcut_asama sütunu babies ile aynı uzunlukta",
      EgitimDegerlendirme.__table__.c.mevcut_asama.type.length
      >= Baby.__table__.c.mevcut_asama.type.length)
dokum = kvkk.disa_aktar(db, db.get(User, b14.user_id))
check("K) KVKK dökümünde egitim_degerlendirmeleri (4 satır)",
      len(dokum.get("egitim_degerlendirmeleri") or []) == 4,
      len(dokum.get("egitim_degerlendirmeleri") or []))
check("K) Canlı LLM çağrısı yok", canli_cagri_sayisi() == 0, canli_cagri_sayisi())

db.close()
gecen = sum(1 for _, ok, _ in results if ok)
for ad, ok, detay in results:
    print(("✅ " if ok else "❌ ") + ad + ("" if ok else f"\n     → {detay[:600]}"))
print(f"\n{gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
