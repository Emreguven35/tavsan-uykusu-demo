"""
GET /plans/today HER PLAN TİPİNDE 200 + dolu plan — zamana bağlı alan tazelemesi
(v2.5.1) planı ASLA düşürmemeli.

LLM YOK (mühür), ağ YOK, prod DB YOK: geçici sqlite + TestClient + dondurulmuş saat.

2026-09-30 olayı: Eğitim sekmesi "Henüz uyku planın yok" gösterdi. Sunucu her
istekte 200 + tam plan döndürüyordu; kök sebep deploy geçişindeki 502 + mobil
kancasının hatada planı boşaltıp bir daha denememesiydi. Bu suite sunucu
tarafının sözünü sabitler: hangi tip, hangi eksik alan olursa olsun plan döner.

  A  Eğitimdeki bebek (egitim_plani, eğitim günü 17 — 13 günlük merdiven bitti)
  B  Eğitim sonrası (training_completed_at dolu)
  C  Bekleme (3-5 ay)
  D  Yenidoğan (0-3 ay)
  E  Eksik alanlı ESKİ plan (yas / dogum_haftasi / egitim_baslangic.kalan_gun yok)
  F  Tazeleme İSTİSNA fırlatırsa bile saklanan içerik döner (200)
  G  Doğum tarihi olmayan bebek

Çalıştırma: python tests/test_plan_her_tip.py
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

_DB = Path(tempfile.gettempdir()) / "plan_her_tip_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["BETA_MODE"] = "true"              # prod ile aynı: kilit yok, plan tam döner

from fastapi.testclient import TestClient              # noqa: E402
from api.db import Base, SessionLocal, engine          # noqa: E402
import api.models                                      # noqa: E402,F401
from api.models import Baby, SleepPlan                 # noqa: E402
from api.main import app                               # noqa: E402
from api.services import plan_service                  # noqa: E402
from api.zaman import saat_sabitle                     # noqa: E402
from tests.llm_muhuru import TAM_PROFIL, canli_cagri_sayisi, muhurle  # noqa: E402

muhurle()
Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []
TR = timezone(timedelta(hours=3))
BUGUN = datetime(2026, 9, 30, 10, 0, tzinfo=TR)


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


tok = client.post("/api/v1/auth/register",
                  json={"email": "hertip@example.com", "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}


def bebek(ad: str, dogum: str | None, **ek) -> str:
    govde = {**TAM_PROFIL, "name": ad, **ek}
    if dogum:
        govde["birth_date"] = dogum
    return client.post("/api/v1/babies", headers=H, json=govde).json()["id"]


def uret(bid: str, gun: datetime) -> dict:
    with saat_sabitle(gun):
        r = client.post("/api/v1/plans/generate?sync=true", headers=H, json={"baby_id": bid})
    assert r.status_code == 201, r.text[:300]
    return r.json()


def bugun(bid: str):
    with saat_sabitle(BUGUN):
        return client.get(f"/api/v1/plans/today?baby_id={bid}", headers=H)


def dolu(r, tip: str) -> tuple[bool, str]:
    if r.status_code != 200:
        return False, f"{r.status_code} {r.text[:200]}"
    c = r.json().get("content") or {}
    ok = c.get("type") == tip and bool(c.get("markdown")) and (
        tip == "yenidogan_ritim" or bool(c.get("schedule")))
    return ok, f"type={c.get('type')} schedule={len(c.get('schedule') or [])}"


def _db_bebek(bid: str, **alanlar) -> None:
    db = SessionLocal()
    try:
        b = db.get(Baby, uuid.UUID(bid))
        for k, v in alanlar.items():
            setattr(b, k, v)
        db.commit()
    finally:
        db.close()


# --- A — eğitimdeki (merdiven bitmiş, gün 17) --------------------------------
EGITIM = bebek("Emre", "2025-10-15")                    # 11.5 ay
uret(EGITIM, datetime(2026, 9, 14, 9, tzinfo=TR))
_db_bebek(EGITIM, training_started_at=date(2026, 9, 14))
r = bugun(EGITIM)
ok, d = dolu(r, "egitim_plani")
check("A1) Eğitimdeki bebek (gün 17): 200 + dolu egitim_plani", ok, d)
check("A2) Eğitim günü 17", ((r.json().get("content") or {}).get("adaptation") or {})
      .get("egitim_gunu") == 17, str((r.json().get("content") or {}).get("adaptation", {}).get("egitim_gunu")))

# --- B — eğitim sonrası --------------------------------------------------------
SONRA = bebek("Ali", "2025-11-01")
uret(SONRA, datetime(2026, 8, 20, 9, tzinfo=TR))
_db_bebek(SONRA, training_started_at=date(2026, 8, 20),
          training_completed_at=date(2026, 9, 2))
ok, d = dolu(bugun(SONRA), "egitim_plani")
check("B1) Eğitim sonrası (completed dolu): 200 + dolu plan", ok, d)

# --- C — bekleme ---------------------------------------------------------------
BEKLE = bebek("Defne", "2026-06-11")
uret(BEKLE, datetime(2026, 9, 17, 9, tzinfo=TR))
r = bugun(BEKLE)
ok, d = dolu(r, "egitim_bekleme")
check("C1) Bekleme: 200 + dolu plan", ok, d)
check("C2) Bekleme: kalan_gun tazelenmiş (42)",
      ((r.json().get("content") or {}).get("egitim_baslangic") or {}).get("kalan_gun") == 42)

# --- D — yenidoğan -------------------------------------------------------------
YENI = bebek("Can", "2026-08-15")
uret(YENI, datetime(2026, 9, 20, 9, tzinfo=TR))
ok, d = dolu(bugun(YENI), "yenidogan_ritim")
check("D1) Yenidoğan: 200 + dolu rehber", ok, d)

# --- E — eksik alanlı eski plan -----------------------------------------------
db = SessionLocal()
try:
    p = (db.query(SleepPlan).filter(SleepPlan.baby_id == uuid.UUID(BEKLE))
         .order_by(SleepPlan.plan_date.desc()).first())
    c = dict(p.content)
    for k in ("yas", "dogum_haftasi"):
        c.pop(k, None)
    c["egitim_baslangic"] = {"tahmini_tarih": "2026-11-11"}      # kalan_gun YOK
    p.content = c
    db.commit()
finally:
    db.close()
r = bugun(BEKLE)
ok, d = dolu(r, "egitim_bekleme")
check("E1) Eksik alanlı eski plan: 200 + dolu", ok, d)
check("E2) Eksik alanlı eski plan: egitim_baslangic yine hesaplandı (42)",
      ((r.json().get("content") or {}).get("egitim_baslangic") or {}).get("kalan_gun") == 42,
      str((r.json().get("content") or {}).get("egitim_baslangic")))

# --- F — tazeleme istisna fırlatırsa --------------------------------------------
_orijinal = plan_service.zamana_bagli_alanlari_tazele


def _patlayan(*_a, **_k):
    raise RuntimeError("test: tazeleme patladı")


plan_service.zamana_bagli_alanlari_tazele = _patlayan
try:
    rf = bugun(EGITIM)
    rb = bugun(BEKLE)
finally:
    plan_service.zamana_bagli_alanlari_tazele = _orijinal
ok1, d1 = dolu(rf, "egitim_plani")
ok2, d2 = dolu(rb, "egitim_bekleme")
check("F1) Tazeleme patlasa da eğitim planı 200 + dolu (saklanan içerik)", ok1, d1)
check("F2) Tazeleme patlasa da bekleme planı 200 + dolu", ok2, d2)

# --- G — doğum tarihi olmayan bebek (plan varken tarih silinmiş) -------------
_db_bebek(EGITIM, birth_date=None)
ok, d = dolu(bugun(EGITIM), "egitim_plani")
check("G1) Doğum tarihi silinmiş bebek: 200 + dolu plan", ok, d)

# --- H — dış izleme: /health HEAD de 200 (UptimeRobot önce HEAD atıyor) --------
check("H1) HEAD /health → 200 (eskiden 405)", client.head("/health").status_code == 200)
check("H2) GET /health → 200", client.get("/health").status_code == 200)

check("Z) Canlı LLM çağrısı YOK", canli_cagri_sayisi() == 0, canli_cagri_sayisi())

print("=" * 76)
print("GET /plans/today — HER PLAN TİPİ")
print("=" * 76)
gecen = 0
for ad, ok, detay in results:
    print(f"  [{'PASS' if ok else 'FAIL'}] {ad}")
    if not ok and detay:
        print(f"         → {detay[:400]}")
    gecen += ok
print("-" * 76)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
print("=" * 76)
sys.exit(0 if gecen == len(results) else 1)
