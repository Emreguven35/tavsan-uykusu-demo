"""
Otomatik kapanış kaynağı (kapanis_kaynagi) — yeniden gönderim kaydı AÇMAZ.

NEDEN VAR (2026-10-07, prod): sunucu K16.1 ile eski açık kaydı kapatıyor ama
mobil yerel kaydını açık tutup her senkronda yeniden gönderiyordu.
`_kaydi_guncelle` bitişi boşla ezince kayıt YENİDEN AÇILIYOR ve sunucunun notu
siliniyordu (Bebek 16: 09-23'ten beri "sürüyor" görünen nap).

Kapsam:
  K1  K16.1 kapanışı: kaynak 'k16_1', yanıtta kapatilanlar; yeniden gönderim
      kaydı açmıyor (bitiş korunur, kapatilanlar'da döner, timer_closed false)
  K2  Kullanıcı bitiş gönderirse kaynak silinir; aynı bitişin geri gelmesi
      (istemci kapatilanlar'ı uyguladı) kaynağı silmez
  K3  Kullanıcının kapattığı kayıt düzenlemeyle yeniden açılabiliyor (batch +
      PATCH); PATCH otomatik kapanmış kaydı da açabiliyor
  K4  Notlar ezilmiyor, birleşiyor (birim + batch)
  K5  K13.3 kapanışı işaretleniyor (yalnız kaynak, NOT YOK), yeniden
      gönderimde korunur; timeline otomatik_kapatildi K13.3'te false
  K6  Eski istemci: mevcut alanlar aynen, kapatilanlar kapanış yokken []
  K7  Migration 0023/0024: K16.1 notlu kapalı → 'k16_1', bakım notlu kapalı →
      'bakim', K13.3 notu silinir
  K8  Kolon uzunluğu tüm kaynak değerlerine yetiyor (Postgres VARCHAR)
  K9  Bakım betiği (acik_sayac_kapat --uygula) 'bakim' yazar; koruma ve
      kapatilanlar bu kaynakta da çalışır
  K10 Timeline: K16.1 kapanışı otomatik_kapatildi=true

Çalıştırma: python tests/test_kapanis_kaynagi.py
"""
import os
import sqlite3
import subprocess
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

_DB = Path(tempfile.gettempdir()) / "kapanis_kaynagi_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["LOG_GELECEK_TOLERANS_DK"] = "1440"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(Path(tempfile.gettempdir()) / "kapanis_medya")
os.environ["DENETIM_ROOT"] = str(Path(tempfile.gettempdir()) / "kapanis_denetim" / "denetim")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import Baby, SleepLog                       # noqa: E402
from api.main import app                                    # noqa: E402
from api.routers.logs import BAKIM, K13_3, K16_1, _not_birlestir  # noqa: E402
from api.services.plan_service import uyku_sureleri         # noqa: E402
from api.zaman import bugun_tr, tr_gun_araligi              # noqa: E402

from tests.llm_muhuru import muhurle                        # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


BUGUN = bugun_tr()


def an(gun_once: int, saat: str) -> datetime:
    """Türkiye saatiyle `gun_once` gün önce SS:DD → UTC an."""
    s, d = map(int, saat.split(":"))
    return tr_gun_araligi(BUGUN - timedelta(days=gun_once))[0] + timedelta(hours=s, minutes=d)


def iso(t: datetime | None) -> str | None:
    return None if t is None else t.isoformat()


tok = client.post("/api/v1/auth/register",
                  json={"email": "kapanis@test.com",
                        "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}


def bebek(ad: str) -> str:
    return client.post("/api/v1/babies", headers=H,
                       json={"name": ad, "birth_date": (BUGUN - timedelta(days=243)).isoformat(),
                             "night_wakes": 2}).json()["id"]


def kayit(bid, cid, bas, bit=None, tip="nap", notes=None) -> dict:
    return {"baby_id": bid, "type": tip, "started_at": iso(bas), "ended_at": iso(bit),
            "client_id": cid, "notes": notes}


def gonder(*loglar):
    r = client.post("/api/v1/logs/batch", headers=H, json={"logs": list(loglar)})
    assert r.status_code == 200, r.text
    return r.json()


def satir(cid) -> SleepLog:
    db = SessionLocal()
    try:
        return db.query(SleepLog).filter(SleepLog.client_id == cid).one()
    finally:
        db.close()


def acik_sayisi(bid) -> int:
    db = SessionLocal()
    try:
        return (db.query(SleepLog).filter(SleepLog.baby_id == uuid.UUID(bid),
                                          SleepLog.ended_at.is_(None)).count())
    finally:
        db.close()


def ayni_an(a, b) -> bool:
    if a is None or b is None:
        return a is b
    a = datetime.fromisoformat(a) if isinstance(a, str) else a
    b = datetime.fromisoformat(b) if isinstance(b, str) else b
    if a.tzinfo is None:
        a = a.replace(tzinfo=b.tzinfo)
    if b.tzinfo is None:
        b = b.replace(tzinfo=a.tzinfo)
    return a == b


# =============================================================================
# K1 — K16.1 kapanışı ve yeniden gönderim
# =============================================================================
B1 = bebek("Kapanis1")
db = SessionLocal()
NAP_DK, _ = uyku_sureleri(db.get(Baby, uuid.UUID(B1)))
db.close()

gonder(kayit(B1, "k1-a", an(2, "10:00")))
j = gonder(kayit(B1, "k1-b", an(2, "13:00")))
a = satir("k1-a")
beklenen_bit = min(an(2, "13:00"), an(2, "10:00") + timedelta(minutes=NAP_DK))
check("K1a) Yeni açık kayıt eskisini kapattı (K16.1)",
      ayni_an(a.ended_at, beklenen_bit), f"{a.ended_at} beklenen {beklenen_bit}")
check("K1b) kapanis_kaynagi = 'k16_1'", a.kapanis_kaynagi == K16_1, a.kapanis_kaynagi)
check("K1c) timer_closed true", j["timer_closed"] is True, j["timer_closed"])
_k = j.get("kapatilanlar") or []
check("K1d) kapatilanlar: {id, client_id, ended_at, kaynak}",
      len(_k) == 1 and _k[0]["client_id"] == "k1-a" and _k[0]["kaynak"] == "k16_1"
      and _k[0]["id"] == str(a.id) and ayni_an(_k[0]["ended_at"], beklenen_bit), str(_k))

# Eski build: yerelde hâlâ açık, kendi notuyla yeniden gönderiyor.
j = gonder(kayit(B1, "k1-a", an(2, "10:00"), notes="anne notu"))
a = satir("k1-a")
check("K1e) Yeniden gönderim otomatik kapanmış kaydı AÇMADI",
      ayni_an(a.ended_at, beklenen_bit), str(a.ended_at))
check("K1f) Kaynak korundu", a.kapanis_kaynagi == K16_1, a.kapanis_kaynagi)
check("K1g) Bebekte tek açık kayıt (yeni olan)", acik_sayisi(B1) == 1, acik_sayisi(B1))
_k = j.get("kapatilanlar") or []
check("K1h) Korunan kayıt kapatilanlar'da döner (istemci yerelini kapatsın)",
      len(_k) == 1 and _k[0]["client_id"] == "k1-a" and _k[0]["kaynak"] == "k16_1", str(_k))
check("K1i) timer_closed false (bu istekte yeni kapatma yok — eski anlam)",
      j["timer_closed"] is False, j["timer_closed"])
_log = next((x for x in j["logs"] if x["client_id"] == "k1-a"), {})
check("K1j) Yanıttaki logs da kapalı bitişi taşıyor",
      ayni_an(_log.get("ended_at"), beklenen_bit), str(_log.get("ended_at")))
check("K1k) Not birleşti: anne notu + sunucu notu",
      "anne notu" in (a.notes or "") and "otomatik kapatıldı" in (a.notes or ""), a.notes)

# Yeni build: kapatilanlar'ı uyguladı, aynı bitişle geri gönderiyor.
j = gonder(kayit(B1, "k1-a", an(2, "10:00"), beklenen_bit, notes="anne notu"))
a = satir("k1-a")
check("K2a) Aynı bitişin geri gelmesi kaynağı silmedi",
      a.kapanis_kaynagi == K16_1, a.kapanis_kaynagi)

# Kullanıcı kendi bitişini giriyor: o kazanır.
j = gonder(kayit(B1, "k1-a", an(2, "10:00"), an(2, "10:40"), notes="anne notu"))
a = satir("k1-a")
check("K2b) Kullanıcının bitişi otomatiği ezdi",
      ayni_an(a.ended_at, an(2, "10:40")), str(a.ended_at))
check("K2c) Kullanıcı kapanışında kaynak NULL", a.kapanis_kaynagi is None, a.kapanis_kaynagi)
check("K2d) Kullanıcı kapanışı kapatilanlar'da değil",
      all(x["client_id"] != "k1-a" for x in j.get("kapatilanlar") or []),
      str(j.get("kapatilanlar")))

# =============================================================================
# K3 — kullanıcının kapattığı kayıt yeniden açılabiliyor
# =============================================================================
B3 = bebek("Kapanis3")
gonder(kayit(B3, "k3-a", an(3, "09:00"), an(3, "10:00")))
check("K3a) Kullanıcının kapattığı kaydın kaynağı NULL",
      satir("k3-a").kapanis_kaynagi is None, satir("k3-a").kapanis_kaynagi)
gonder(kayit(B3, "k3-a", an(3, "09:00")))
check("K3b) Batch ile bitişi silmek kaydı yeniden açıyor (eski davranış)",
      satir("k3-a").ended_at is None, satir("k3-a").ended_at)

r = client.patch(f"/api/v1/logs/{satir('k3-a').id}", headers=H,
                 json={"ended_at": iso(an(3, "10:15"))})
check("K3c) PATCH ile kapatma", r.status_code == 200 and satir("k3-a").ended_at is not None,
      f"{r.status_code} {r.text[:120]}")
r = client.patch(f"/api/v1/logs/{satir('k3-a').id}", headers=H, json={"ended_at": None})
check("K3d) PATCH ile bitişi silip yeniden açma",
      r.status_code == 200 and satir("k3-a").ended_at is None, f"{r.status_code} {r.text[:120]}")

# Otomatik kapanmış kayıt da kullanıcının açık düzenlemesiyle açılabilir.
B3b = bebek("Kapanis3b")
gonder(kayit(B3b, "k3b-a", an(4, "09:00")))
gonder(kayit(B3b, "k3b-b", an(4, "12:00")))
check("K3e) (hazırlık) k3b-a otomatik kapandı", satir("k3b-a").kapanis_kaynagi == K16_1,
      satir("k3b-a").kapanis_kaynagi)
gonder(kayit(B3b, "k3b-b", an(4, "12:00"), an(4, "13:00")))     # yenisi bitti
r = client.patch(f"/api/v1/logs/{satir('k3b-a').id}", headers=H, json={"ended_at": None})
check("K3f) PATCH otomatik kapanmış kaydı açabiliyor ve kaynak siliniyor",
      r.status_code == 200 and satir("k3b-a").ended_at is None
      and satir("k3b-a").kapanis_kaynagi is None,
      f"{r.status_code} {satir('k3b-a').ended_at} {satir('k3b-a').kapanis_kaynagi}")
r = client.patch(f"/api/v1/logs/{satir('k3b-b').id}", headers=H, json={"notes": "yalnız not"})
check("K3g) Yalnız not düzeltmesi kaynağa dokunmuyor (kaynak zaten NULL kalır)",
      r.status_code == 200 and satir("k3b-b").kapanis_kaynagi is None, r.status_code)

# =============================================================================
# K4 — notlar birleşiyor
# =============================================================================
_vakalar = [
    ((None, "a"), "a"), (("a", None), "a"), (("a", ""), "a"), (("a", "a"), "a"),
    (("a | otomatik kapatıldı: yeni kayıt açıldı", "a"),
     "a | otomatik kapatıldı: yeni kayıt açıldı"),
    (("uyudu", "uyudu, ağladı"), "uyudu, ağladı"),
    (("x", "y"), "x | y"), (("x | y", "y | z"), "x | y | z"),
]
_hatali = [(g, _not_birlestir(*g), b) for g, b in _vakalar if _not_birlestir(*g) != b]
check("K4a) _not_birlestir vakaları", not _hatali, str(_hatali))

B4 = bebek("Kapanis4")
gonder(kayit(B4, "k4-a", an(5, "09:00"), an(5, "10:00"), notes="ilk not"))
gonder(kayit(B4, "k4-a", an(5, "09:00"), an(5, "10:00"), notes="ikinci not"))
check("K4b) Yeniden gönderim notu ezmedi, birleştirdi",
      satir("k4-a").notes == "ilk not | ikinci not", satir("k4-a").notes)
gonder(kayit(B4, "k4-a", an(5, "09:00"), an(5, "10:00"), notes=None))
check("K4c) Notsuz yeniden gönderim notu silmedi",
      satir("k4-a").notes == "ilk not | ikinci not", satir("k4-a").notes)

# =============================================================================
# K5 — K13.3 kapanışı işaretleniyor
# =============================================================================
B5 = bebek("Kapanis5")
gonder(kayit(B5, "k5-sayac", an(6, "15:00")))
j = gonder(kayit(B5, "k5-manuel", an(6, "14:50"), an(6, "16:00")))
s = satir("k5-sayac")
check("K5a) Sayaç manuel kaydın bitişinde kapandı",
      ayni_an(s.ended_at, an(6, "16:00")), str(s.ended_at))
check("K5b) kapanis_kaynagi = 'k13_3'", s.kapanis_kaynagi == K13_3, s.kapanis_kaynagi)
check("K5c) K13.3 not YAZMIYOR (bitiş annenin girdiği gerçek saat)",
      not (s.notes or ""), s.notes)
_k = j.get("kapatilanlar") or []
check("K5d) kapatilanlar'da k13_3",
      len(_k) == 1 and _k[0]["client_id"] == "k5-sayac" and _k[0]["kaynak"] == "k13_3", str(_k))
check("K5e) timer_closed true", j["timer_closed"] is True, j["timer_closed"])
gonder(kayit(B5, "k5-sayac", an(6, "15:00")))
check("K5f) Yeniden gönderim K13.3 kapanışını açmadı",
      ayni_an(satir("k5-sayac").ended_at, an(6, "16:00")), str(satir("k5-sayac").ended_at))
check("K5g) Manuel kaydın kaynağı NULL", satir("k5-manuel").kapanis_kaynagi is None,
      satir("k5-manuel").kapanis_kaynagi)


def timeline(bid) -> list[dict]:
    r = client.get("/api/v1/logs/timeline", headers=H, params={"baby_id": bid, "days": 7})
    assert r.status_code == 200, r.text
    return [o for g in r.json()["gunler"] for o in g["oturumlar"]]


# Eşit saatli ikiz (sayaç + manuel): hangisi oturum olursa olsun bayrak false.
B5b = bebek("Kapanis5b")
gonder(kayit(B5b, "k5b-sayac", an(5, "15:00")))
gonder(kayit(B5b, "k5b-manuel", an(5, "15:00"), an(5, "16:00")))
check("K5h) (hazırlık) ikiz sayaç K13.3 ile kapandı",
      satir("k5b-sayac").kapanis_kaynagi == K13_3, satir("k5b-sayac").kapanis_kaynagi)
_ot = timeline(B5b) + timeline(B5)
check("K5i) Timeline: K13.3 kapanışında otomatik_kapatildi false",
      _ot and not any(o["otomatik_kapatildi"] for o in _ot),
      str([(o["client_id"], o["otomatik_kapatildi"]) for o in _ot]))

# =============================================================================
# K6 — eski istemci yanıtı
# =============================================================================
B6 = bebek("Kapanis6")
j = gonder(kayit(B6, "k6-a", an(1, "09:00"), an(1, "10:00")))
_eski = {"created": int, "updated": int, "skipped": list, "synced": list, "logs": list,
         "plan_updated": bool, "timer_closed": bool}
check("K6a) Eski alanlar aynı tipte duruyor",
      all(isinstance(j.get(k), t) for k, t in _eski.items()), str({k: type(j.get(k)) for k in _eski}))
check("K6b) Kapanış yokken kapatilanlar boş liste", j.get("kapatilanlar") == [],
      str(j.get("kapatilanlar")))
_log_alanlari = {"id", "baby_id", "type", "started_at", "ended_at", "notes", "client_id",
                 "created_at", "kategori", "kategori_etiket"}
check("K6c) logs kalemlerinin alanları değişmedi (kapanis_kaynagi sızmıyor)",
      set(j["logs"][0]) == _log_alanlari, str(set(j["logs"][0]) ^ _log_alanlari))
check("K6d) synced/skipped kalem şekli aynı",
      set(j["synced"][0]) == {"client_id", "id"}, str(j["synced"][0]))

# =============================================================================
# K7 — migration 0023 (sıfırdan alembic, SQLite)
# =============================================================================
_mdb = Path(tempfile.gettempdir()) / "kapanis_migration_test.db"
if _mdb.exists():
    _mdb.unlink()
_env = {**os.environ, "DATABASE_URL": f"sqlite:///{_mdb.as_posix()}"}


def _alembic(*arg):
    return subprocess.run([sys.executable, "-m", "alembic", *arg], cwd=ROOT, env=_env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


_r = _alembic("upgrade", "0022_topluluk_v2")
check("K7a) 0022'ye kadar migration", _r.returncode == 0, _r.stderr[-300:])
_c = sqlite3.connect(_mdb)
_satirlar = [
    ("a1", "otomatik kapatıldı: yeni kayıt açıldı", "2026-09-20 10:00:00"),
    ("a2", "anne | otomatik kapatıldı: yeni kayıt açıldı", "2026-09-20 11:00:00"),
    ("a3", "otomatik kapatıldı: yeni kayıt açıldı", None),         # sonradan yeniden açılmış
    ("a4", "otomatik kapatıldı", "2026-09-20 12:00:00"),           # betik (K16.1 değil)
    ("a5", None, "2026-09-20 13:00:00"),
    ("a6", "anne | otomatik kapatıldı: manuel kayıt girildi", "2026-09-20 14:00:00"),
    ("a7", "otomatik kapatıldı: manuel kayıt girildi", "2026-09-20 15:00:00"),
    ("a8", "otomatik kapatıldı: manuel kayıt girildi | sonra", "2026-09-20 16:00:00"),
    ("a9", "otomatik kapatıldı", None),                            # bakım notlu ama açık
]
for _id, _not, _bit in _satirlar:
    _c.execute("INSERT INTO sleep_logs (id, user_id, baby_id, type, started_at, ended_at, notes, "
               "created_at, updated_at) VALUES (?, 'u', 'b', 'nap', '2026-09-20 09:00:00', ?, ?, "
               "'2026-09-20 09:00:00', '2026-09-20 09:00:00')", (_id, _bit, _not))
_c.commit()
_c.close()
_r = _alembic("upgrade", "head")
check("K7b) head'e migration", _r.returncode == 0, _r.stderr[-300:])
_c = sqlite3.connect(_mdb)
_kay = dict(_c.execute("SELECT id, kapanis_kaynagi FROM sleep_logs").fetchall())
_not = dict(_c.execute("SELECT id, notes FROM sleep_logs").fetchall())
_upd = {r[0] for r in _c.execute("SELECT updated_at FROM sleep_logs")}
_c.close()
check("K7c) K16.1 notlu kapalı 'k16_1', bakım notlu kapalı 'bakim', diğerleri NULL",
      _kay == {"a1": "k16_1", "a2": "k16_1", "a3": None, "a4": "bakim", "a5": None,
               "a6": None, "a7": None, "a8": None, "a9": None}, str(_kay))
check("K7f) K13.3 notu silindi, annenin notu kaldı",
      (_not["a6"], _not["a7"], _not["a8"]) == ("anne", None, "sonra"),
      str((_not["a6"], _not["a7"], _not["a8"])))
check("K7d) Doldurma updated_at'e dokunmadı", _upd == {"2026-09-20 09:00:00"}, str(_upd))
_r = _alembic("downgrade", "0022_topluluk_v2")
check("K7e) downgrade çalışıyor", _r.returncode == 0, _r.stderr[-300:])

# =============================================================================
# K8 — şema uzunluğu (SQLite VARCHAR'ı zorlamaz, Postgres zorlar)
# =============================================================================
_uz = SleepLog.__table__.c.kapanis_kaynagi.type.length
check("K8) kapanis_kaynagi uzunluğu tüm değerlere yetiyor",
      _uz >= max(len(K16_1), len(K13_3), len(BAKIM)), _uz)

# =============================================================================
# K9 — bakım betiği 'bakim' yazar
# =============================================================================
from scripts import acik_sayac_kapat                        # noqa: E402

B9 = bebek("Kapanis9")
gonder(kayit(B9, "k9-a", an(3, "09:00")))                   # 16 saatten eski, tek açık
_argv = sys.argv
sys.argv = ["acik_sayac_kapat.py", "--gun", "7", "--uygula"]
try:
    acik_sayac_kapat.main()
finally:
    sys.argv = _argv
a9 = satir("k9-a")
check("K9a) Betik kaydı kapattı ve kaynak 'bakim'",
      a9.ended_at is not None and a9.kapanis_kaynagi == BAKIM,
      f"{a9.ended_at} {a9.kapanis_kaynagi}")
check("K9b) Betik notu duruyor", "otomatik kapatıldı" in (a9.notes or ""), a9.notes)
_bit9 = a9.ended_at
j = gonder(kayit(B9, "k9-a", an(3, "09:00")))
check("K9c) Yeniden gönderim bakım kapanışını AÇMADI",
      ayni_an(satir("k9-a").ended_at, _bit9), str(satir("k9-a").ended_at))
_k = j.get("kapatilanlar") or []
check("K9d) kapatilanlar'da kaynak 'bakim'",
      len(_k) == 1 and _k[0]["client_id"] == "k9-a" and _k[0]["kaynak"] == "bakim", str(_k))
check("K9e) Timeline: bakım kapanışı otomatik_kapatildi=true",
      any(o["client_id"] == "k9-a" and o["otomatik_kapatildi"] for o in timeline(B9)),
      str([(o["client_id"], o["otomatik_kapatildi"]) for o in timeline(B9)]))

# =============================================================================
# K10 — timeline: K16.1 kapanışı işaretli
# =============================================================================
B10 = bebek("Kapanis10")
gonder(kayit(B10, "k10-a", an(2, "10:00")))
gonder(kayit(B10, "k10-b", an(2, "13:00")))
gonder(kayit(B10, "k10-b", an(2, "13:00"), an(2, "14:00")))
_ot = {o["client_id"]: o["otomatik_kapatildi"] for o in timeline(B10)}
check("K10) K16.1 kapanışı true, kullanıcının kapattığı false",
      _ot.get("k10-a") is True and _ot.get("k10-b") is False, str(_ot))


# =============================================================================
# RAPOR
# =============================================================================
print("\n" + "=" * 74)
print("KAPANIŞ KAYNAĞI — yeniden gönderim otomatik kapanışı açmaz")
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
