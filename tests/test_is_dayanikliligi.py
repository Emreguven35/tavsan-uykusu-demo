"""
Plan işi dayanıklılığı (2026-09-25 olayı) + eğitim günü alanları. LLM YOK.

OLAY: deploy SIGTERM'i LLM çağrısındaki işi öldürdü, satır sonsuza dek
"processing" kaldı; mobil /plans/today'i 6 dk yokladı (hep 404), vazgeçti.

  Y  Kapanışta yarım iş yetim olur; bakim() atomik devralıp BİR kez yeniden
     koşar; ikinci kesintide failed (döngü yok)
  B  15 dk'yı aşan processing iş DB'ye GERÇEKTEN failed yazılır, Türkçe mesaj
  S  Üretim sırasında hesap silinirse iş temiz iptal (plan yazılmaz, hata yok);
     hesap silme süren işi iptal eder
  H  Üretim hatası anneye Türkçe gider (teknik metin değil)
  T  /plans/today 404'ü sebebi söyler (hazırlanıyor / hazırlanamadı)
  E  Eğitim planında adaptation.egitim_gunu = egitim_baslangic_gunu, DOLU
     (training_started_at boşsa ilk eğitim planı tarihinden tamamlanır);
     bekleme planında ikisi de null

Çalıştırma: python tests/test_is_dayanikliligi.py
"""
import os
import sys
import tempfile
import threading
import time
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

_DB = Path(tempfile.gettempdir()) / "is_dayanikliligi_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["BETA_MODE"] = "true"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import Baby, PlanUretimIsi, SleepPlan, User  # noqa: E402
from api.main import app                                    # noqa: E402
from api.services import plan_jobs, plan_service            # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, muhurle            # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


from api.zaman import bugun_tr  # noqa: E402
BUGUN = bugun_tr()  # B6: sunucu günü Türkiye günü


def hesap(email, gun=245):
    tok = client.post("/api/v1/auth/register",
                      json={"email": email, "password": "TestPass123!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    bid = client.post("/api/v1/babies", headers=h, json={
        "name": "B", "night_wakes": 2, **TAM_PROFIL,
        "birth_date": (BUGUN - timedelta(days=gun)).isoformat()}).json()["id"]
    db = SessionLocal()
    try:
        uid = db.query(User).filter(User.email == email).one().id
    finally:
        db.close()
    return h, bid, uid


def satir(job_id):
    db = SessionLocal()
    try:
        return db.get(PlanUretimIsi, job_id)
    finally:
        db.close()


def bekle(kosul, sn=10):
    for _ in range(int(sn * 20)):
        if kosul():
            return True
        time.sleep(0.05)
    return False


# =============================================================================
# Y — yetim iş devralma
# =============================================================================
HY, BY, UY = hesap("yetim@gercek.com")
_gercek_run = plan_jobs.run_generation
_kosulan: list[tuple] = []
plan_jobs.run_generation = lambda *a, **k: _kosulan.append(a)
j = plan_jobs.create_job(UY, BY)
plan_jobs.submit(j, BY, {"destek": "kucak"}, 38)            # parametreler DB'ye
_kosulan.clear()
check("Y1) İş sahibi = bu süreç, parametreler yazıldı",
      satir(j).sahip == plan_jobs.SUREC_KIMLIGI
      and satir(j).parametreler == {"req_overrides": {"destek": "kucak"},
                                    "dogum_haftasi": 38, "ek_icerik": None}, "")
n = plan_jobs.sahipsizlestir()                              # SIGTERM → kapanış
with plan_jobs._LOCK:
    plan_jobs._JOBS.pop(j)                                  # süreç öldü
check("Y2) Kapanışta yarım iş yetim", n >= 1 and satir(j).sahip is None, "")
s1 = plan_jobs.bakim()
bekle(lambda: len(_kosulan) == 1)
check("Y3) bakim yetimi devraldı ve AYNI girdilerle yeniden koştu",
      s1["devralinan"] == 1 and _kosulan and _kosulan[0][2] == {"destek": "kucak"}
      and _kosulan[0][3] == 38 and satir(j).deneme == 1
      and satir(j).sahip == plan_jobs.SUREC_KIMLIGI, f"{s1} {_kosulan}")
check("Y4) İkinci bakım aynı işi tekrar almaz (sahipli)",
      plan_jobs.bakim()["devralinan"] == 0, "")
plan_jobs._db_yaz(j, sahip=None)                            # ikinci kesinti
s2 = plan_jobs.bakim()
check("Y5) İkinci kez kesilen iş failed (döngü yok) + Türkçe",
      s2["vazgecilen"] == 1 and satir(j).status == "failed"
      and "deneyin" in (satir(j).error or ""), f"{s2} {satir(j).error}")
plan_jobs.run_generation = _gercek_run

# =============================================================================
# B — bayat iş DB'ye failed
# =============================================================================
jb = plan_jobs.create_job(UY, BY)
plan_jobs._db_yaz(jb, created_at=datetime.now(timezone.utc) - timedelta(minutes=20))
sb = plan_jobs.bakim()
check("B1) 15 dk'yı aşan iş DB'de failed yazıldı", sb["bayat"] >= 1
      and satir(jb).status == "failed", f"{sb} {satir(jb).status}")
with plan_jobs._LOCK:
    plan_jobs._JOBS.pop(jb, None)
r = client.get(f"/api/v1/plans/generate/{jb}", headers=HY).json()
check("B2) Yoklama failed + Türkçe 'tekrar dene'", r["status"] == "failed"
      and "yeniden oluşturmayı deneyin" in (r.get("error") or ""), str(r))

# =============================================================================
# H — üretim hatası Türkçe; T — /plans/today 404 sebebi
# =============================================================================
HH, BH, UH = hesap("hata@gercek.com")
_gercek_gen = plan_service.generate_content


def _patla(*a, **k):
    raise plan_service.PlanError("anthropic 529 overloaded_error {'type': 'error'}")


plan_service.generate_content = _patla
jh = plan_jobs.create_job(UH, BH)
plan_jobs.run_generation(jh, BH, None, None)
check("H1) Hata anneye Türkçe; teknik metin sızmıyor",
      satir(jh).status == "failed" and satir(jh).error == plan_jobs.HATA_MESAJ
      and "anthropic" not in satir(jh).error, satir(jh).error)
r = client.get(f"/api/v1/plans/today?baby_id={BH}", headers=HH)
check("T1) /plans/today 404 → 'hazırlanamadı, tekrar deneyin' + iş durumu",
      r.status_code == 404 and r.json()["detail"] == plan_jobs.HATA_MESAJ
      and r.json()["plan_isi"]["status"] == "failed", r.text[:200])
plan_service.generate_content = _gercek_gen
HT, BT, UT = hesap("bekleyen@gercek.com")
plan_jobs.create_job(UT, BT)
r = client.get(f"/api/v1/plans/today?baby_id={BT}", headers=HT)
check("T2) İş sürerken 404 'hazırlanıyor'", r.status_code == 404
      and "hazırlanıyor" in r.json()["detail"], r.text[:200])

# =============================================================================
# S — üretim sırasında hesap silme
# =============================================================================
HS, BS, US = hesap("silinen@gercek.com")
_icerde = threading.Event()
_devam = threading.Event()


def _yavas_uret(baby, *a, **k):
    icerik = _gercek_gen(baby, *a, **k)
    _icerde.set()
    _devam.wait(10)                                         # "LLM çağrısı"
    return icerik


plan_service.generate_content = _yavas_uret
js = client.post("/api/v1/plans/generate", headers=HS, json={"baby_id": BS}).json()["job_id"]
_icerde.wait(10)
rd = client.delete("/api/v1/auth/account", headers=HS)
check("S1) Hesap silme süren işi iptal etti", rd.status_code == 200
      and satir(js).status == "failed" and "iptal" in (satir(js).error or ""),
      f"{rd.status_code} {satir(js).status} {satir(js).error}")
_devam.set()
bekle(lambda: satir(js).error == plan_jobs.IPTAL_MESAJ, 3)
time.sleep(1.0)                                              # iş bitsin
db = SessionLocal()
try:
    check("S2) Silinen bebeğe plan YAZILMADI, iş temiz iptal (hata/FK yok)",
          db.query(SleepPlan).filter(SleepPlan.baby_id == uuid.UUID(BS)).count() == 0
          and satir(js).error == plan_jobs.IPTAL_MESAJ, satir(js).error)
finally:
    db.close()
plan_service.generate_content = _gercek_gen

# =============================================================================
# E — eğitim günü alanları
# =============================================================================
HE, BE, UE = hesap("egitim@gercek.com", 245)
client.post("/api/v1/plans/generate?sync=true", headers=HE, json={"baby_id": BE})
a = client.get(f"/api/v1/plans/today?baby_id={BE}", headers=HE).json()["content"]["adaptation"]
check("E1) Eğitim planı: training_started_at PATCH'lenmeden de egitim_gunu=1 ve eşit",
      a.get("egitim_gunu") == 1 and a.get("egitim_baslangic_gunu") == 1,
      f'{a.get("egitim_gunu")} {a.get("egitim_baslangic_gunu")}')
b = client.get("/api/v1/babies", headers=HE).json()[0]
check("E2) training_started_at ilk eğitim planı tarihine tamamlandı",
      b["training_started_at"] == BUGUN.isoformat(), str(b["training_started_at"]))
client.patch(f"/api/v1/babies/{BE}", headers=HE,
             json={"training_started_at": (BUGUN - timedelta(days=4)).isoformat()})
a = client.get(f"/api/v1/plans/today?baby_id={BE}", headers=HE).json()["content"]["adaptation"]
check("E3) Başlangıç 4 gün önce → ikisi de 5", a.get("egitim_gunu") == 5
      and a.get("egitim_baslangic_gunu") == 5, str(a.get("egitim_gunu")))
HK, BK, UK = hesap("bekleme@gercek.com", 110)
client.post("/api/v1/plans/generate?sync=true", headers=HK, json={"baby_id": BK})
k = client.get(f"/api/v1/plans/today?baby_id={BK}", headers=HK).json()["content"]
check("E4) Bekleme planında ikisi de null ve başlangıç YAZILMADI",
      k["type"] == "egitim_bekleme"
      and (k.get("adaptation") or {}).get("egitim_gunu") is None
      and (k.get("adaptation") or {}).get("egitim_baslangic_gunu") is None
      and client.get("/api/v1/babies", headers=HK).json()[0]["training_started_at"] is None,
      str((k.get("adaptation") or {}).get("egitim_gunu")))

# =============================================================================
print("=" * 78)
print("PLAN İŞİ DAYANIKLILIĞI TEST SONUÇLARI")
print("=" * 78)
for ad, ok, detay in results:
    if not ok:
        print(f"  ✗ {ad}  —  {detay}")
gecen = sum(1 for _, ok, _ in results if ok)
print("-" * 78)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
