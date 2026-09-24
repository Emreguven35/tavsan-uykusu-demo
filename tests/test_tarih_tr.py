"""
B6 — Tarih tutarlılığı: bütün "gün" hesapları Türkiye saatine göre. LLM YOK.

Canlı hata: Türkiye saatiyle 01:05'te üretilen planın training_started_at'i
UTC tarihiyle (bir gün önce) yazıldı. 00:00-03:00 TR arası UTC hâlâ dündür.

  Z  bugun_tr / tr_gunu / tr_gun_araligi (23:30 ve 00:30 TR)
  P  Plan 23:30 TR'de D gününe, 00:30 TR'de D+1 gününe yazılıyor (UTC ikisinde D)
  K  00:10 TR'de girilen kayıt D+1'in kayıtlarında, D'ninkinde değil
  E  egitim_gunu gece yarısı (TR) artıyor; 00:30 TR'de tamamlanan
     training_started_at D+1
  S  Sor kotası Türkiye gece yarısında sıfırlanıyor
  R  Denetim raporunun "dün"ü Türkiye günü

Çalıştırma: python tests/test_tarih_tr.py
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

_DB = Path(tempfile.gettempdir()) / "tarih_tr_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["LOG_GELECEK_TOLERANS_DK"] = "4320"
os.environ.pop("BETA_MODE", None)
os.environ.pop("BETA_PREMIUM_ALL", None)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import Baby, SleepPlan                      # noqa: E402
from api.main import app                                    # noqa: E402
from api.services import denetim                            # noqa: E402
from api.zaman import (TR, bugun_tr, saat_sabitle, tr_gun_araligi,  # noqa: E402
                       tr_gunu)
from engine import chatbot                                  # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, muhurle            # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


# D: iki gün önce (kayıt doğrulamasının "gelecek" kontrolüne takılmasın)
D = datetime.now(TR).date() - timedelta(days=2)
D1 = D + timedelta(days=1)


def tr(gun: date, s: int, dk: int = 0) -> datetime:
    return datetime(gun.year, gun.month, gun.day, s, dk, tzinfo=TR)


GECE_2330 = tr(D, 23, 30)       # UTC: D 20:30
GECE_0030 = tr(D1, 0, 30)       # UTC: D 21:30  ← UTC tarihi HÂLÂ D

# =============================================================================
# Z — yardımcılar
# =============================================================================
with saat_sabitle(GECE_2330):
    check("Z1) 23:30 TR → bugun_tr = D", bugun_tr() == D, bugun_tr())
with saat_sabitle(GECE_0030):
    check("Z2) 00:30 TR → bugun_tr = D+1 (UTC tarihi D olsa da)",
          bugun_tr() == D1 and GECE_0030.astimezone(timezone.utc).date() == D, bugun_tr())
check("Z3) tr_gunu: 21:30 UTC → ertesi gün", tr_gunu(GECE_0030.astimezone(timezone.utc)) == D1, "")
check("Z4) tr_gunu: saat dilimsiz değer UTC sayılır",
      tr_gunu(GECE_0030.astimezone(timezone.utc).replace(tzinfo=None)) == D1, "")
b, s = tr_gun_araligi(D1)
check("Z5) D+1 TR günü = [D 21:00 UTC, D+1 21:00 UTC)",
      b == datetime(D.year, D.month, D.day, 21, tzinfo=timezone.utc)
      and (s - b) == timedelta(days=1), f"{b} {s}")


def hesap(email, gun_yas=245, **ek):
    tok = client.post("/api/v1/auth/register",
                      json={"email": email, "password": "TestPass123!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    bid = client.post("/api/v1/babies", headers=h, json={
        "name": "B", "night_wakes": 2, **TAM_PROFIL,
        "birth_date": (D - timedelta(days=gun_yas)).isoformat(), **ek}).json()["id"]
    return h, bid


def plan_tarihleri(bid):
    db = SessionLocal()
    try:
        return sorted(p.plan_date for p in db.query(SleepPlan)
                      .filter(SleepPlan.baby_id == uuid.UUID(bid)).all())
    finally:
        db.close()


# =============================================================================
# P — plan günü
# =============================================================================
HA, BA = hesap("gece-yarisi-once@gercek.com")
with saat_sabitle(GECE_2330):
    r = client.post("/api/v1/plans/generate?sync=true", headers=HA, json={"baby_id": BA})
check("P1) 23:30 TR'de üretilen plan D gününe", r.status_code == 201
      and r.json()["plan_date"] == D.isoformat(), r.text[:120])
HB, BB = hesap("gece-yarisi-sonra@gercek.com")
with saat_sabitle(GECE_0030):
    r = client.post("/api/v1/plans/generate?sync=true", headers=HB, json={"baby_id": BB})
    t = client.get(f"/api/v1/plans/today?baby_id={BB}", headers=HB)
check("P2) 00:30 TR'de üretilen plan D+1 gününe (eskiden D)", r.status_code == 201
      and r.json()["plan_date"] == D1.isoformat(), r.text[:120])
check("P3) 00:30 TR'de /plans/today D+1 planını veriyor, ikinci gün satırı açmıyor",
      t.status_code == 200 and t.json()["plan_date"] == D1.isoformat()
      and plan_tarihleri(BB) == [D1], f"{t.json().get('plan_date')} {plan_tarihleri(BB)}")

# =============================================================================
# K — kayıt günü
# =============================================================================
with saat_sabitle(GECE_0030):
    client.post("/api/v1/logs/batch", headers=HB, json={"logs": [{
        "baby_id": BB, "type": "night_wake", "client_id": "k1",
        "started_at": tr(D1, 0, 10).isoformat()},
        {"baby_id": BB, "type": "feed", "client_id": "f1",
         "started_at": tr(D1, 0, 20).isoformat()}]})
g1 = [x["client_id"] for x in client.get(f"/api/v1/logs?date={D1.isoformat()}&baby_id={BB}",
                                         headers=HB).json()]
g0 = [x["client_id"] for x in client.get(f"/api/v1/logs?date={D.isoformat()}&baby_id={BB}",
                                         headers=HB).json()]
check("K1) 00:10 TR kaydı D+1'in kayıtlarında, D'ninkinde DEĞİL",
      "k1" in g1 and "k1" not in g0, f"D+1={g1} D={g0}")
with saat_sabitle(tr(D1, 12, 0)):
    ws = client.get(f"/api/v1/logs/weekly-summary?baby_id={BB}&week_start={D.isoformat()}",
                    headers=HB).json()
gunler = {x["date"]: x for x in ws["days"]}
check("K2) Haftalık özette 00:20 TR beslenmesi D+1'e sayılıyor, D'ye değil (eskiden D)",
      gunler[D1.isoformat()]["night_feeds"] == 1 and gunler[D.isoformat()]["night_feeds"] == 0,
      {k: v["night_feeds"] for k, v in gunler.items()})

# =============================================================================
# E — eğitim günü gece yarısı
# =============================================================================
HE, BE = hesap("egitim-gunu@gercek.com")
with saat_sabitle(GECE_0030):
    client.post("/api/v1/plans/generate?sync=true", headers=HE, json={"baby_id": BE})
    a = client.get(f"/api/v1/plans/today?baby_id={BE}", headers=HE).json()["content"]["adaptation"]
    ts = client.get("/api/v1/babies", headers=HE).json()[0]["training_started_at"]
check("E1) 00:30 TR'de tamamlanan training_started_at = D+1 (eskiden D)",
      ts == D1.isoformat(), ts)
check("E2) ... ve egitim_gunu = 1", a.get("egitim_gunu") == 1, a.get("egitim_gunu"))
client.patch(f"/api/v1/babies/{BE}", headers=HE,
             json={"training_started_at": (D - timedelta(days=4)).isoformat()})
with saat_sabitle(tr(D, 23, 59)):
    g_once = client.get(f"/api/v1/plans/today?baby_id={BE}", headers=HE).json()["content"]["adaptation"]["egitim_gunu"]
with saat_sabitle(tr(D1, 0, 1)):
    g_sonra = client.get(f"/api/v1/plans/today?baby_id={BE}", headers=HE).json()["content"]["adaptation"]["egitim_gunu"]
check("E3) egitim_gunu Türkiye gece yarısında artıyor (23:59 → 5, 00:01 → 6)",
      (g_once, g_sonra) == (5, 6), f"{g_once} → {g_sonra}")
_v = None
with saat_sabitle(tr(D1, 0, 1)):
    _v = client.get("/api/v1/education/videos", headers=HE).json()["asama"]["kod"]
check("E4) 6. gün → aşama oda ortası (TR günü)", _v == "oda_ortasi", _v)

# =============================================================================
# S — Sor kotası gece yarısı
# =============================================================================
chatbot._cevap_uret = lambda *a, **k: {"cevap": "C", "cache_hit": False, "kaynaklar": [],
                                       "llm": False, "retrieval_layer": "k1",
                                       "top_score": 0.9}
HS, _ = hesap("sor@gercek.com")
from api.models import ChatMessage, User                    # noqa: E402
db = SessionLocal()
try:
    uid = db.query(User).filter(User.email == "sor@gercek.com").one().id
    for i in range(3):                                      # D 23:40 TR'de 3 soru
        db.add(ChatMessage(user_id=uid, role="user", content=f"s{i}", cached=False,
                           created_at=tr(D, 23, 40).astimezone(timezone.utc)))
    db.commit()
finally:
    db.close()
with saat_sabitle(tr(D, 23, 50)):
    r_once = client.post("/api/v1/chat", headers=HS, json={"message": "dördüncü"})
with saat_sabitle(tr(D1, 0, 10)):
    r_sonra = client.post("/api/v1/chat", headers=HS, json={"message": "yeni gün"})
check("S1) 23:50 TR'de 4. soru kotaya takılıyor",
      r_once.status_code == 403, str(r_once.status_code))
check("S2) 00:10 TR'de (UTC hâlâ D) kota sıfırlandı",
      r_sonra.status_code == 200 and r_sonra.json().get("ucretsiz_kalan") == 2,
      f"{r_sonra.status_code} {r_sonra.text[:100]}")

# =============================================================================
# R — denetim raporu günü
# =============================================================================
with saat_sabitle(tr(D1, 0, 30)):
    check("R1) 00:30 TR'de raporun 'dün'ü D (UTC'ye göre D-1 olurdu)", denetim.dun() == D,
          denetim.dun())

# =============================================================================
print("=" * 78)
print("TARİH TUTARLILIĞI (TR) TEST SONUÇLARI")
print("=" * 78)
for ad, ok, detay in results:
    if not ok:
        print(f"  ✗ {ad}  —  {detay}")
gecen = sum(1 for _, ok, _ in results if ok)
print("-" * 78)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
