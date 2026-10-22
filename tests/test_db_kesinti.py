"""
DB kesintisinde arka plan turları (2026-10-22 olayı). LLM YOK.

OLAY: 11:13 TR'de postgres.railway.internal'a bağlanma "Connection timed out"
ile düştü; plan_jobs.bakim patladı ve Sentry'ye hata gitti. Kesinti uzasaydı
bakım her dakika (her worker'da), bildirim turu her 5 dk aynı hatayı basacaktı.

  K  DB erişilemezken bakim() ve bildirim turu ATLANIR (sorgu yok, tur yok)
  L  Kesinti başına süreç başına TEK uyarı logu; başka worker'ın gördüğü aynı
     kesinti ikinci Sentry olayı üretmez
  D  DB dönünce turlar normal devam eder (bayat iş failed yazılır, bildirim
     turu koşar) ve "yeniden erişilebilir" bir kez loglanır
  E  Postgres engine ayarları: connect_timeout=5 gerçekten 5 sn'de vazgeçer,
     keepalive argümanları ve pool_timeout=10 engine'de

Çalıştırma: python tests/test_db_kesinti.py
"""
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "db_kesinti_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["BETA_MODE"] = "true"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

import sentry_sdk                                           # noqa: E402
from fastapi.testclient import TestClient                   # noqa: E402
from sqlalchemy.exc import OperationalError                 # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.db.session as db_session                         # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import PlanUretimIsi, User                  # noqa: E402
from api.main import app                                    # noqa: E402
from api.services import notifier, plan_jobs, topluluk_bildirim  # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, muhurle            # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


from api.zaman import bugun_tr  # noqa: E402
BUGUN = bugun_tr()

tok = client.post("/api/v1/auth/register",
                  json={"email": "kesinti@gercek.com", "password": "TestPass123!"}
                  ).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
BID = client.post("/api/v1/babies", headers=H, json={
    "name": "B", "night_wakes": 2, **TAM_PROFIL,
    "birth_date": (BUGUN - timedelta(days=245)).isoformat()}).json()["id"]
_db = SessionLocal()
UID = _db.query(User).filter(User.email == "kesinti@gercek.com").one().id
_db.close()


def satir(job_id):
    db = SessionLocal()
    try:
        return db.get(PlanUretimIsi, job_id)
    finally:
        db.close()


# --- Ölçüm kancaları ---------------------------------------------------------
class _Toplayici(logging.Handler):
    def __init__(self):
        super().__init__()
        self.kayitlar: list[logging.LogRecord] = []

    def emit(self, record):
        self.kayitlar.append(record)


LOG = _Toplayici()
logging.getLogger("tavsan.db").addHandler(LOG)


def uyarilar():
    return [r for r in LOG.kayitlar if "erişilemiyor" in r.getMessage()]


def donusler():
    return [r for r in LOG.kayitlar if "yeniden erişilebilir" in r.getMessage()]


SENTRY: list[str] = []
sentry_sdk.capture_message = lambda m, *a, **k: SENTRY.append(m)

TUR = {"bildirim": 0, "topluluk": 0}


def _sahte_tur(db, *a, **k):
    TUR["bildirim"] += 1
    return {"sent": 0, "skipped_duplicate": 0}


def _sahte_topluluk(db, *a, **k):
    TUR["topluluk"] += 1
    return {}


notifier.run_reminder_cycle = _sahte_tur
topluluk_bildirim.tur_calistir = _sahte_topluluk

_gercek_connect = engine.connect


def _kopuk(*a, **k):
    raise OperationalError("SELECT 1", {}, Exception("Connection timed out"))


db_session._KESINTI_ISARETI.unlink(missing_ok=True)
db_session._kesintide = False

# Bayat iş: kesinti varken DOKUNULMAMALI, DB dönünce failed yazılmalı.
jb = plan_jobs.create_job(UID, BID)
plan_jobs._db_yaz(jb, created_at=datetime.now(timezone.utc) - timedelta(minutes=20))
with plan_jobs._LOCK:
    plan_jobs._JOBS.pop(jb, None)

# =============================================================================
# K — kesinti: turlar atlanır
# =============================================================================
check("K0) Kesinti öncesi DB erişilebilir", db_session.db_erisilebilir_mi("test"), "")
engine.connect = _kopuk
s1 = plan_jobs.bakim()
s2 = plan_jobs.bakim()
notifier._job()
notifier._job()
engine.connect = _gercek_connect                 # satır okumak için geçici
durum_kesintide = satir(jb).status
check("K1) bakim atlandı (atlandi=True, sayaçlar sıfır)",
      s1.get("atlandi") is True and s2.get("atlandi") is True
      and not any(v for k, v in s1.items() if k != "atlandi"), f"{s1} {s2}")
check("K2) Kesintide bayat işe dokunulmadı (sorgu koşmadı)",
      durum_kesintide == "processing", durum_kesintide)
check("K3) Bildirim turu ve topluluk turu koşmadı",
      TUR == {"bildirim": 0, "topluluk": 0}, TUR)

# =============================================================================
# L — tek uyarı, tek Sentry olayı
# =============================================================================
check("L1) Dört atlanan tura karşı TEK uyarı logu (WARNING)",
      len(uyarilar()) == 1 and uyarilar()[0].levelno == logging.WARNING,
      [r.getMessage() for r in uyarilar()])
check("L2) Uyarı ilk göreni ve hata tipini söylüyor, ayrıntı/host yok",
      "plan_bakim" in uyarilar()[0].getMessage()
      and "OperationalError" in uyarilar()[0].getMessage()
      and "timed out" not in uyarilar()[0].getMessage(), uyarilar()[0].getMessage())
check("L3) Kesinti başına tek Sentry olayı", len(SENTRY) == 1, SENTRY)
check("L4) Kesinti işareti ortak dosyada", db_session._KESINTI_ISARETI.exists(), "")
# Başka bir worker aynı kesintiyi görür: kendi uyarısını yazar, Sentry'ye gitmez.
engine.connect = _kopuk
db_session._kesintide = False                    # "ikinci worker" (ayrı süreç durumu)
plan_jobs.bakim()
check("L5) İkinci worker: kendi uyarısı var, ikinci Sentry olayı YOK",
      len(uyarilar()) == 2 and len(SENTRY) == 1, f"{len(uyarilar())} {SENTRY}")

# =============================================================================
# D — DB döndü: normal devam
# =============================================================================
engine.connect = _gercek_connect
sd = plan_jobs.bakim()
notifier._job()
check("D1) DB dönünce bakim koştu: bayat iş failed yazıldı",
      "atlandi" not in sd and sd["bayat"] >= 1 and satir(jb).status == "failed",
      f"{sd} {satir(jb).status}")
check("D2) DB dönünce bildirim + topluluk turu koştu",
      TUR == {"bildirim": 1, "topluluk": 1}, TUR)
check("D3) 'Yeniden erişilebilir' tek kez loglandı, işaret kalktı",
      len(donusler()) == 1 and not db_session._KESINTI_ISARETI.exists(),
      [r.getMessage() for r in donusler()])
plan_jobs.bakim()
notifier._job()
check("D4) Sonraki normal turlarda yeni log yok",
      len(donusler()) == 1 and len(uyarilar()) == 2, "")
# Yeni kesinti → yeni (tek) Sentry olayı.
engine.connect = _kopuk
plan_jobs.bakim()
notifier._job()
engine.connect = _gercek_connect
check("D5) Sonraki ayrı kesinti yeniden bir kez bildirilir",
      len(SENTRY) == 2 and len(uyarilar()) == 3, f"{SENTRY} {len(uyarilar())}")
db_session.db_erisilebilir_mi("test")            # temizle (işaret dosyası)

# =============================================================================
# E — Postgres engine ayarları (ayrı süreç; yönlendirilemez adrese bağlanma)
# =============================================================================
_betik = r"""
import json, os, sys, time
sys.path.insert(0, os.getcwd())
os.environ["DATABASE_URL"] = "postgresql://u:p@10.255.255.1:5432/x"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
from api.db import session as s
cargs = s.engine.dialect.create_connect_args(s.engine.url)[1]
t = time.monotonic()
ok = s.db_erisilebilir_mi("test")
print(json.dumps({"ok": ok, "sure": time.monotonic() - t,
                  "pool_timeout": s.engine.pool._timeout,
                  "pool_size": s.engine.pool.size(),
                  "recycle": s.engine.pool._recycle,
                  "pre_ping": s.engine.pool._pre_ping,
                  "connect_args": s._connect_args}))
s._KESINTI_ISARETI.unlink(missing_ok=True)
"""
import json  # noqa: E402
_env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
r = subprocess.run([sys.executable, "-c", _betik], cwd=ROOT, capture_output=True,
                   text=True, encoding="utf-8", errors="replace",
                   timeout=60, env=_env)
try:
    e = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    e = {}
check("E1) Postgres connect_args: connect_timeout=5 + keepalive 1/30/10/3",
      e.get("connect_args") == {"connect_timeout": 5, "keepalives": 1,
                                "keepalives_idle": 30, "keepalives_interval": 10,
                                "keepalives_count": 3}, e or r.stderr[-500:])
check("E2) Havuz: pool_timeout=10, pool_size=5, recycle=1800, pre_ping açık",
      e.get("pool_timeout") == 10 and e.get("pool_size") == 5
      and e.get("recycle") == 1800 and e.get("pre_ping") is True, e)
check("E3) Erişilemeyen Postgres ~5 sn'de False (eskiden ~2 dk asılı)",
      e.get("ok") is False and e.get("sure", 99) < 8, e)
print(f"E3 ölçüm: {e.get('sure', 0):.1f} sn")

# =============================================================================
print("=" * 78)
print("DB KESİNTİSİ — ARKA PLAN TURLARI TEST SONUÇLARI")
print("=" * 78)
for ad, ok, detay in results:
    print(f"  {'✓' if ok else '✗'} {ad}" + ("" if ok else f"  —  {detay}"))
gecen = sum(1 for _, ok, _ in results if ok)
print("-" * 78)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
