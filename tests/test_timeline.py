"""
GET /api/v1/logs/timeline — temiz 7/14 günlük zaman çizelgesi.

LLM YOK, ağ YOK, prod DB YOK: geçici sqlite + TestClient + dondurulmuş saat.

Birinci bebeğin kayıtları 2026-09-30 prod verisinin (11 aylık bebek) YALNIZ
SAATLERİDİR — kişisel veri yok. Saat 2026-09-30 09:00 TR'ye sabitlenir.

  A  Sözleşme: 200, 7 gün (eskiden yeniye), alanlar, Cache-Control 60 sn
  B  09-29: İKİ oturum (11:25–13:55, 18:25–20:55)
  C  09-28: sıfır süreli sabah cevapları oturum DEĞİL, sabah_uyanisi dolu
  D  09-30: 06:30 wake → sabah_uyanisi 06:30/kayit; 07:43–08:55 oturum
  E  K21: 09-22 18:50 → 09-24 21:21 kaydı oturum değil, yok_sayilan'da
  F  K13: 09-22'deki aynı saatli iki kayıt TEK oturum (days=14)
  G  Toplamlar weekly-summary ile gün gün tutarlı
  H  Açık kayıt: bitis=null, devam=true, toplama girmez; bu akşam başlayan
     açık gece uykusu bugünün altında
  I  K20 parçalar tek oturum + parcalar; "otomatik kapatıldı" notu işaretli
  J  Sahiplik 404, days sınırı 422
  K  K21.2: sabah biten 16 sa üstü kayıt asiri_uzun, süre/gece_dk yok

Çalıştırma: python tests/test_timeline.py
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

_DB = Path(tempfile.gettempdir()) / "timeline_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"

from fastapi.testclient import TestClient              # noqa: E402
from api.db import Base, SessionLocal, engine          # noqa: E402
import api.models                                      # noqa: E402,F401
from api.models import Baby, SleepLog                  # noqa: E402
from api.main import app                               # noqa: E402
from api.zaman import saat_sabitle                     # noqa: E402

Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []
TR = timezone(timedelta(hours=3))


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


def tr(ay_gun: str, hhmm: str) -> datetime:
    """'09-29', '11:25' → 2026 TR anı (UTC'ye çevrilmiş)."""
    ay, gun = map(int, ay_gun.split("-"))
    s, d = map(int, hhmm.split(":"))
    return datetime(2026, ay, gun, s, d, tzinfo=TR).astimezone(timezone.utc)


def kayit(eposta: str) -> dict:
    tok = client.post("/api/v1/auth/register",
                      json={"email": eposta, "password": "TestPass123!"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


H = kayit("timeline@example.com")
H_BASKA = kayit("baskasi@example.com")
B1 = client.post("/api/v1/babies", headers=H,
                 json={"name": "A", "birth_date": "2025-10-15"}).json()["id"]
B2 = client.post("/api/v1/babies", headers=H,
                 json={"name": "B", "birth_date": "2025-10-15"}).json()["id"]

# (tip, başlangıç, bitiş | None, not) — prod hesabının saatleri, 09-30 08:59 itibarıyla.
PROD = [
    ("sleep", ("09-22", "16:20"), ("09-22", "18:00"), None),
    ("sleep", ("09-22", "16:20"), ("09-22", "18:00"), None),          # kopya
    ("sleep", ("09-22", "18:50"), ("09-24", "21:21"), None),          # K21
    ("night_wake", ("09-22", "18:50"), ("09-22", "21:10"), None),
    ("sleep", ("09-24", "18:22"), ("09-24", "20:52"), None),
    ("sleep", ("09-25", "03:00"), ("09-25", "03:30"), None),
    ("sleep", ("09-25", "03:53"), ("09-25", "21:04"), None),          # K21
    ("sleep", ("09-25", "21:15"), ("09-26", "18:04"), None),          # K21
    ("night_wake", ("09-25", "21:16"), ("09-25", "21:31"), None),
    ("sleep", ("09-28", "07:00"), ("09-28", "07:00"), None),          # sabah cevabı
    ("sleep", ("09-28", "07:30"), ("09-28", "07:30"), None),
    ("sleep", ("09-28", "07:30"), ("09-28", "07:30"), None),
    ("sleep", ("09-28", "07:30"), ("09-28", "07:30"), None),
    ("sleep", ("09-29", "11:25"), ("09-29", "13:55"), None),
    ("sleep", ("09-29", "18:25"), ("09-29", "20:55"), None),
    ("wake", ("09-30", "06:30"), ("09-30", "06:30"), None),
    ("sleep", ("09-30", "07:43"), ("09-30", "08:55"), None),
]
_db = SessionLocal()
try:
    uid = _db.get(Baby, uuid.UUID(B1)).user_id
    for i, (tip, b, e, n) in enumerate(PROD):
        _db.add(SleepLog(user_id=uid, baby_id=uuid.UUID(B1), type=tip,
                         started_at=tr(*b), ended_at=tr(*e) if e else None,
                         notes=n, client_id=f"prod-{i}"))
    # B2: K20 parçaları, sunucunun kapattığı kayıt, açık gündüz uykusu
    for tip, b, e, n, cid in [
            ("nap", ("09-29", "14:00"), ("09-29", "14:36"), None, "p1"),
            ("nap", ("09-29", "14:36"), ("09-29", "15:46"), None, "p2"),
            ("nap", ("09-29", "10:00"), ("09-29", "11:05"),
             "otomatik kapatıldı: yeni kayıt açıldı", "oto"),
            ("nap", ("09-30", "08:30"), None, None, "acik"),
            # K21.2 — sabah biten 27 sa 55 dk'lık sayaç (prod 10-02 → 10-03)
            ("sleep", ("09-26", "03:42"), ("09-27", "07:37"), None, "uzun")]:
        _db.add(SleepLog(user_id=uid, baby_id=uuid.UUID(B2), type=tip,
                         started_at=tr(*b), ended_at=tr(*e) if e else None,
                         notes=n, client_id=cid))
    _db.commit()
finally:
    _db.close()

SAAT = datetime(2026, 9, 30, 9, 0, tzinfo=TR)


def getir(bebek: str, days: int = 7, headers: dict | None = None):
    with saat_sabitle(SAAT):
        return client.get(f"/api/v1/logs/timeline?baby_id={bebek}&days={days}",
                          headers=headers or H)


def gun(yanit: dict, tarih: str) -> dict:
    return next(g for g in yanit["gunler"] if g["tarih"] == tarih)


def saatler(g: dict) -> list:
    def hm(s):
        return None if s is None else (datetime.fromisoformat(s.replace("Z", "+00:00"))
                                       .astimezone(TR).strftime("%H:%M"))
    return [(hm(o["baslangic"]), hm(o["bitis"]), o["sinif"]) for o in g["oturumlar"]]


r = getir(B1)
y = r.json()
# --- A ---------------------------------------------------------------------
check("A1) 200", r.status_code == 200, r.text[:300])
check("A2) 7 gün, 09-24 → 09-30, eskiden yeniye",
      [g["tarih"] for g in y["gunler"]] == [f"2026-09-{d}" for d in range(24, 31)],
      [g["tarih"] for g in y["gunler"]])
check("A3) Cache-Control private, max-age=60 + Vary: Authorization",
      r.headers.get("cache-control") == "private, max-age=60"
      and "authorization" in (r.headers.get("vary") or "").lower(),
      r.headers.get("cache-control"))
_o = gun(y, "2026-09-29")["oturumlar"][0]
check("A4) Oturum alanları: id, client_id, baslangic, bitis, sinif, sure_dk, devam, otomatik_kapatildi",
      {"id", "client_id", "baslangic", "bitis", "sinif", "sure_dk", "devam",
       "otomatik_kapatildi"} <= set(_o), str(_o))

# --- B ---------------------------------------------------------------------
g29 = gun(y, "2026-09-29")
check("B1) 09-29: İKİ oturum (11:25–13:55, 18:25–20:55)",
      [(a, b) for a, b, _ in saatler(g29)] == [("11:25", "13:55"), ("18:25", "20:55")],
      saatler(g29))
check("B2) 09-29 toplam 300 dk, client_id'ler kaynak kayıtlar",
      g29["gece_dk"] + g29["gunduz_dk"] == 300
      and [o["client_id"] for o in g29["oturumlar"]] == ["prod-13", "prod-14"],
      f"{g29['gece_dk']}+{g29['gunduz_dk']} {[o['client_id'] for o in g29['oturumlar']]}")

# --- C ---------------------------------------------------------------------
g28 = gun(y, "2026-09-28")
check("C1) 09-28: sıfır süreli sabah cevapları oturum DEĞİL", g28["oturumlar"] == [],
      saatler(g28))
check("C2) 09-28: sabah_uyanisi dolu (kayıttan)",
      g28["sabah_uyanisi"] is not None and g28["sabah_uyanisi"]["kaynak"] == "kayit",
      str(g28["sabah_uyanisi"]))

# --- D ---------------------------------------------------------------------
g30 = gun(y, "2026-09-30")
check("D1) 09-30: sabah_uyanisi 06:30 / kayit",
      g30["sabah_uyanisi"] == {"saat": "06:30", "kaynak": "kayit"}, str(g30["sabah_uyanisi"]))
check("D2) 09-30: 07:43–08:55 gündüz oturumu", saatler(g30) == [("07:43", "08:55", "gunduz")],
      saatler(g30))

# --- E ---------------------------------------------------------------------
g24 = gun(y, "2026-09-24")
check("E1) 09-24: 2 günlük sayaç (21:21'de durdu) oturum değil, 18:22–20:52 var",
      [(a, b) for a, b, _ in saatler(g24)] == [("18:22", "20:52")], saatler(g24))
# 7 günlük pencere weekly-summary ile AYNI sorguyu kullanır (ilk günden bir gün
# önce başlar); 09-22'de başlayan kayıt orada okunmaz. 14 günde okunur.
_g24_14 = gun(getir(B1, days=14).json(), "2026-09-24")
check("E2) 09-24 (days=14): K21 kaydı oturum değil, yok_sayilan'da (asiri_uzun)",
      any(x["kod"] == "asiri_uzun" for x in _g24_14["yok_sayilan"])
      and [(a, b) for a, b, _ in saatler(_g24_14)] == [("18:22", "20:52")],
      str(_g24_14["yok_sayilan"]))
check("E3) 09-25 ve 09-26'daki sabah dışı biten uzun sayaçlar da yok_sayilan'da",
      any(x["kod"] == "asiri_uzun" for x in gun(y, "2026-09-25")["yok_sayilan"])
      and any(x["kod"] == "asiri_uzun" for x in gun(y, "2026-09-26")["yok_sayilan"]))

# --- F ---------------------------------------------------------------------
y14 = getir(B1, days=14).json()
g22 = gun(y14, "2026-09-22")
check("F1) days=14: 14 gün (09-17 → 09-30)", len(y14["gunler"]) == 14
      and y14["from_date"] == "2026-09-17", y14["from_date"])
check("F2) 09-22: aynı saatli iki kayıt TEK oturum (K13)",
      [(a, b) for a, b, _ in saatler(g22)] == [("16:20", "18:00")], saatler(g22))

# --- G ---------------------------------------------------------------------
with saat_sabitle(SAAT):
    ws = client.get(f"/api/v1/logs/weekly-summary?baby_id={B1}", headers=H).json()
fark = [(d["date"], d["sleep_hours"], gun(y, d["date"])["gece_dk"] + gun(y, d["date"])["gunduz_dk"])
        for d in ws["days"]
        if abs(d["sleep_hours"] * 60 - (gun(y, d["date"])["gece_dk"]
                                        + gun(y, d["date"])["gunduz_dk"])) > 1]
check("G1) Her gün gece_dk + gunduz_dk = weekly-summary sleep_hours × 60", not fark, fark)
check("G2) Gece uyanma sayıları weekly-summary ile aynı",
      [d["night_wakes"] for d in ws["days"]] == [g["gece_uyanma"] for g in y["gunler"]])

# --- H + I -----------------------------------------------------------------
y2 = getir(B2).json()
h30 = gun(y2, "2026-09-30")
_acik = [o for o in h30["oturumlar"] if o["client_id"] == "acik"]
check("H1) Açık gündüz uykusu: bitis=null, devam=true, sure_dk=null",
      len(_acik) == 1 and _acik[0]["bitis"] is None and _acik[0]["devam"] is True
      and _acik[0]["sure_dk"] is None, str(_acik))
check("H2) Açık kaydın süresi toplama girmiyor", h30["gunduz_dk"] == 0, h30["gunduz_dk"])
h29 = gun(y2, "2026-09-29")
_bir = [o for o in h29["oturumlar"] if len(o["parcalar"]) == 2]
check("I1) K20: 14:00–14:36 + 14:36–15:46 TEK oturum, parcalar 2",
      len(_bir) == 1 and saatler({"oturumlar": _bir})[0][:2] == ("14:00", "15:46")
      and _bir[0]["sure_dk"] == 106, str(_bir))
_oto = [o for o in h29["oturumlar"] if o["client_id"] == "oto"]
check("I2) 'otomatik kapatıldı' notlu kayıt otomatik_kapatildi=true",
      len(_oto) == 1 and _oto[0]["otomatik_kapatildi"] is True, str(_oto))
check("I3) Diğer oturumlar otomatik_kapatildi=false",
      all(not o["otomatik_kapatildi"] for o in g29["oturumlar"]))

# --- K21.2 ------------------------------------------------------------------
h27 = gun(y2, "2026-09-27")
_uz = [o for o in h27["oturumlar"] if o["client_id"] == "uzun"]
check("K1) Sabah biten 27 sa'lik kayıt: asiri_uzun=true, sure_dk=null, gece_dk=0",
      len(_uz) == 1 and _uz[0]["asiri_uzun"] is True and _uz[0]["sure_dk"] is None
      and h27["gece_dk"] == 0, str(h27))
check("K2) Bitişi sabah uyanışı (07:37)",
      h27["sabah_uyanisi"] == {"saat": "07:37", "kaynak": "kayit"}, str(h27["sabah_uyanisi"]))
check("K3) Başladığı gün (09-26) oturum olarak görünmüyor",
      not [o for o in gun(y2, "2026-09-26")["oturumlar"] if o["client_id"] == "uzun"])
with saat_sabitle(SAAT):
    ws2 = client.get(f"/api/v1/logs/weekly-summary?baby_id={B2}", headers=H).json()
check("K4) weekly-summary 09-27: 0 saat",
      next(d for d in ws2["days"] if d["date"] == "2026-09-27")["sleep_hours"] == 0,
      str(ws2["days"]))
check("K5) Diğer oturumlar asiri_uzun=false",
      all(not o["asiri_uzun"] for g in y["gunler"] for o in g["oturumlar"]))

# Bu akşam başlayan AÇIK gece uykusu bugünün altında (saat 22:00'ye alınır).
_db = SessionLocal()
try:
    _db.add(SleepLog(user_id=uid, baby_id=uuid.UUID(B2), type="sleep",
                     started_at=tr("09-30", "20:30"), client_id="gece-acik"))
    _db.commit()
finally:
    _db.close()
with saat_sabitle(datetime(2026, 9, 30, 22, 0, tzinfo=TR)):
    y3 = client.get(f"/api/v1/logs/timeline?baby_id={B2}", headers=H).json()
_ga = [o for o in gun(y3, "2026-09-30")["oturumlar"] if o["client_id"] == "gece-acik"]
check("H3) Bu akşam 20:30'da başlayan açık gece uykusu bugünün altında (devam)",
      len(_ga) == 1 and _ga[0]["sinif"] == "gece" and _ga[0]["devam"] is True, str(_ga))

# --- J ---------------------------------------------------------------------
check("J1) Başkasının bebeği → 404", getir(B1, headers=H_BASKA).status_code == 404)
check("J2) days=15 → 422", getir(B1, days=15).status_code == 422)
check("J3) days=0 → 422", getir(B1, days=0).status_code == 422)

print("=" * 76)
print("GET /logs/timeline")
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
