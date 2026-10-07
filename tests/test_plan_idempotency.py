"""
POST /plans/generate idempotency — aynı bebek için ikinci yapay zekâ işi açılmaz.

NEDEN VAR (2026-10): mobil onboarding sonunda üretimi bekletmeden ateşliyor,
Plan sekmesindeki "tekrar dene" ve çift dokunma da aynı isteği atabiliyor. Her
istek ayrı bir Sonnet işi (~90-140 sn, ücretli) başlatıyordu. Artık bebeğin
kuyrukta ya da çalışan işi varsa mevcut iş 202 ile döner.

Kapsam:
  I1  İş sürerken ikinci istek → aynı job_id, 202, yeni iş/üretim YOK
  I2  Başka bebek → ayrı iş
  I3  İş bitince (done) → yeni iş
  I4  İş başarısızsa (failed) → yeni iş
  I5  İş başka worker'da (yalnız DB'de, processing) → o iş döner
  I6  DB'deki bayat processing iş (>15 dk) aktif sayılmaz → yeni iş
  I7  Yanıt biçimi aynı: {job_id, status, queue_position}
  I8  Senkron yol (?sync=true) değişmedi: 201

Çalıştırma: python tests/test_plan_idempotency.py
"""
import os
import sys
import tempfile
import threading
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

_DB = Path(tempfile.gettempdir()) / "plan_idempotency_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(Path(tempfile.gettempdir()) / "plan_idem_medya")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import PlanUretimIsi                        # noqa: E402
from api.main import app                                    # noqa: E402
from api.services import plan_jobs                          # noqa: E402
from api.zaman import bugun_tr                              # noqa: E402

from tests.llm_muhuru import canli_cagri_sayisi, muhurle    # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


# --- Sahte üretim: iş, test bırakana kadar "processing" kalır ---------------
_BIRAK: dict[str, threading.Event] = {}
_URETIM_SAYISI = {"n": 0}
_gercek = plan_jobs.run_generation


def _sahte_uretim(job_id, *a, **kw):
    _URETIM_SAYISI["n"] += 1
    with plan_jobs._LOCK:
        if job_id in plan_jobs._JOBS:
            plan_jobs._JOBS[job_id]["started"] = True
    _BIRAK.setdefault(job_id, threading.Event()).wait(timeout=30)
    sonuc = getattr(_BIRAK[job_id], "sonuc", plan_jobs.STATUS_DONE)
    plan_jobs._set(job_id, status=sonuc)


plan_jobs.run_generation = _sahte_uretim


def bitir(job_id, sonuc=plan_jobs.STATUS_DONE):
    ev = _BIRAK.setdefault(job_id, threading.Event())
    ev.sonuc = sonuc
    ev.set()
    for _ in range(200):                                     # durum yazılana dek
        j = plan_jobs.get_job(job_id, plan_jobs._JOBS.get(job_id, {}).get("user_id"))
        if j and j["status"] != plan_jobs.STATUS_PROCESSING:
            return
        threading.Event().wait(0.02)


tok = client.post("/api/v1/auth/register",
                  json={"email": "idem@test.com", "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
PROFIL = {"birth_date": (bugun_tr() - timedelta(days=240)).isoformat(),
          "feeding_type": "breast", "sleep_method": "rocking",
          "sleep_environment": "loc:own_bed;d:1", "crying_tolerance": "5",
          "parent_experience": "none", "night_wakes": 2}
B1 = client.post("/api/v1/babies", headers=H, json={"name": "A", **PROFIL}).json()["id"]
B2 = client.post("/api/v1/babies", headers=H, json={"name": "B", **PROFIL}).json()["id"]


def uret(bid, sync=False):
    return client.post("/api/v1/plans/generate" + ("?sync=true" if sync else ""),
                       headers=H, json={"baby_id": bid})


def is_sayisi(bid) -> int:
    db = SessionLocal()
    try:
        return db.query(PlanUretimIsi).filter(PlanUretimIsi.baby_id == str(uuid.UUID(bid))).count()
    finally:
        db.close()


# I1 ----------------------------------------------------------------------
r1 = uret(B1)
r2 = uret(B1)
r3 = uret(B1)
j1 = r1.json()
check("I1a) İlk istek 202 + job_id", r1.status_code == 202 and j1.get("job_id"), r1.text[:160])
check("I1b) İş sürerken tekrar → AYNI job_id, 202",
      r2.status_code == 202 and r3.status_code == 202
      and r2.json()["job_id"] == r3.json()["job_id"] == j1["job_id"],
      [r2.json().get("job_id"), r3.json().get("job_id"), j1.get("job_id")])
check("I1c) Yeni iş kaydı açılmadı (bebekte 1 iş)", is_sayisi(B1) == 1, is_sayisi(B1))
threading.Event().wait(0.3)
check("I1d) Üretim yalnız bir kez başladı", _URETIM_SAYISI["n"] == 1, _URETIM_SAYISI["n"])
check("I7) Yanıt biçimi aynı", set(r2.json()) == {"job_id", "status", "queue_position"}
      and r2.json()["status"] == "processing", r2.json())

# I2 ----------------------------------------------------------------------
r = uret(B2)
check("I2) Başka bebek → ayrı iş", r.status_code == 202 and r.json()["job_id"] != j1["job_id"],
      r.json())
bitir(r.json()["job_id"])

# I3 ----------------------------------------------------------------------
bitir(j1["job_id"])
r = uret(B1)
check("I3) İş bittikten sonra → yeni iş", r.status_code == 202
      and r.json()["job_id"] != j1["job_id"] and is_sayisi(B1) == 2,
      f"{r.json()} iş={is_sayisi(B1)}")

# I4 ----------------------------------------------------------------------
bitir(r.json()["job_id"], plan_jobs.STATUS_FAILED)
r4 = uret(B1)
check("I4) Başarısız işten sonra → yeni iş", r4.status_code == 202 and is_sayisi(B1) == 3,
      f"{r4.json()} iş={is_sayisi(B1)}")
bitir(r4.json()["job_id"])

# I5 / I6 — iş yalnız DB'de (başka worker) -----------------------------------
_uid = plan_jobs._JOBS[j1["job_id"]]["user_id"]


def db_isi(yas_dk: int) -> str:
    jid = str(uuid.uuid4())
    db = SessionLocal()
    db.add(PlanUretimIsi(id=jid, user_id=_uid, baby_id=str(uuid.UUID(B2)),
                         status=plan_jobs.STATUS_PROCESSING, started=True, deneme=0,
                         created_at=datetime.now(timezone.utc) - timedelta(minutes=yas_dk)))
    db.commit()
    db.close()
    return jid


_taze = db_isi(2)
r = uret(B2)
check("I5) Başka worker'daki süren iş döner (yeni iş yok)",
      r.status_code == 202 and r.json()["job_id"] == _taze, f"{r.json()} beklenen {_taze}")
db = SessionLocal()
db.get(PlanUretimIsi, _taze).status = plan_jobs.STATUS_DONE
db.commit()
db.close()
_bayat = db_isi(20)
r = uret(B2)
check("I6) Bayat processing iş aktif sayılmaz → yeni iş",
      r.status_code == 202 and r.json()["job_id"] != _bayat, r.json())
bitir(r.json()["job_id"])

# I8 — senkron yol --------------------------------------------------------
plan_jobs.run_generation = _gercek
r = uret(B1, sync=True)
check("I8) Senkron yol değişmedi: 201", r.status_code == 201, f"{r.status_code} {r.text[:120]}")

check("Z) Canlı LLM çağrısı yapılmadı", canli_cagri_sayisi() == 0, canli_cagri_sayisi())

# =============================================================================
print("\n" + "=" * 74)
print("PLAN ÜRETİMİ IDEMPOTENCY")
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
os._exit(0 if _gecen == len(results) else 1)
