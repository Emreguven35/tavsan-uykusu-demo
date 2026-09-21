"""
POST /logs/batch — KAYIT BAZLI kabul/ret testleri.

NEDEN VAR: tek bozuk kayıt bütün batch'i 422 ile düşürüyordu. Mobilde
BATCH_SIZE 200 olduğu için bir bozuk kayıt 199 sağlamı birlikte gömüyor,
sync-manager batch'in tamamını "gönderildi" işaretliyordu — sessiz veri kaybı.
Mobil (lib/offline/sync-batch.ts) bunu ikili bölmeyle telafi etmeye çalışıyor;
sunucu kayıt bazında cevap verince o çabaya gerek kalmıyor.

Kapsam:
  B1  10 kayıt + 1 geçersiz tip → 9 yazıldı, 1 skipped (invalid_type)
  B2  Aynı batch ikinci kez → 10 synced, 10 duplicate (yeni satır AÇILMAZ)
  B3  Bozuk gövde → 422 (yalnız bu durumda)
  B4  Tüm kalemler geçersiz → yine 200, tamamı skipped
  B5  Sebep kodları: invalid_time, missing_field, not_owned, invalid
  B6  Geriye uyum: created/updated/logs/plan_updated/timer_closed duruyor;
      skipped kalemleri mobilin okuduğu şekle (client_id + reason) uyuyor

Çalıştırma: python tests/test_batch_kayit_bazli.py
"""
import os
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "batch_kayit_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(Path(tempfile.gettempdir()) / "batch_medya")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import SleepLog                             # noqa: E402
from api.main import app                                    # noqa: E402

from tests.llm_muhuru import muhurle                        # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


TODAY = datetime.now(timezone.utc).date()


def utc(dk: int) -> str:
    return (datetime(TODAY.year, TODAY.month, TODAY.day, tzinfo=timezone.utc)
            + timedelta(minutes=dk - 180)).isoformat()


tok = client.post("/api/v1/auth/register",
                  json={"email": "batch@test.com",
                        "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
BID = client.post("/api/v1/babies", headers=H,
                  json={"name": "Batch", "birth_date": (TODAY - timedelta(days=243)).isoformat(),
                        "night_wakes": 2}).json()["id"]


def kayit(i: int, tip: str = "nap", cid: str | None = None) -> dict:
    """Birbirine değmeyen kayıtlar: K18.2'nin ±3 dk kopya penceresine düşmesin."""
    bas = 8 * 60 + i * 30
    return {"baby_id": BID, "type": tip,
            "started_at": utc(bas), "ended_at": utc(bas + 20),
            "client_id": cid or f"cid-{i}"}


def gonder(loglar):
    return client.post("/api/v1/logs/batch", headers=H, json={"logs": loglar})


def satir_sayisi() -> int:
    db = SessionLocal()
    try:
        return db.query(SleepLog).count()
    finally:
        db.close()


# =============================================================================
# B1 — 10 kayıt + 1 geçersiz tip
# =============================================================================
onlu = [kayit(i) for i in range(10)]
onlu[4]["type"] = "zıpzıp"                       # şemada olmayan tip

r1 = gonder(onlu)
check("B1a) Yanıt 200 (tek bozuk kayıt batch'i düşürmüyor)",
      r1.status_code == 200, f"{r1.status_code} {r1.text[:200]}")
j1 = r1.json()
check("B1b) 9 kayıt yazıldı", len(j1["synced"]) == 9 and j1["created"] == 9,
      f"synced={len(j1['synced'])} created={j1['created']}")
check("B1c) 1 kayıt elendi", len(j1["skipped"]) == 1, str(j1["skipped"]))
_sk = j1["skipped"][0] if j1["skipped"] else {}
check("B1d) Sebep invalid_type", _sk.get("reason") == "invalid_type", str(_sk))
check("B1e) Elenen kaydın client_id'si döndü (mobil eşleyebilsin)",
      _sk.get("client_id") == "cid-4", str(_sk))
check("B1f) detail Türkçe ve beklenen tipleri söylüyor",
      "geçersiz" in (_sk.get("detail") or "")
      and "sleep" in (_sk.get("detail") or ""), str(_sk.get("detail")))
check("B1g) DB'de tam 9 satır var", satir_sayisi() == 9, str(satir_sayisi()))
check("B1h) synced kalemleri client_id + id taşıyor",
      all(x.get("client_id") and x.get("id") for x in j1["synced"]),
      str(j1["synced"][:2]))


# =============================================================================
# B2 — Aynı batch yeniden: hepsi kopya
# =============================================================================
gecerli = [k for k in onlu if k["type"] != "zıpzıp"]
_once = satir_sayisi()
r2 = gonder(gecerli)
j2 = r2.json()
check("B2a) Yanıt 200", r2.status_code == 200, f"{r2.status_code}")
check("B2b) 9'u da synced (mevcut id ile)",
      len(j2["synced"]) == 9, f"{len(j2['synced'])}")
check("B2c) 9'u da 'duplicate' olarak işaretlendi",
      len(j2["skipped"]) == 9
      and all(x["reason"] == "duplicate" for x in j2["skipped"]),
      str(j2["skipped"][:2]))
check("B2d) YENİ satır açılmadı", satir_sayisi() == _once,
      f"{satir_sayisi()} != {_once}")
check("B2e) created=0, updated=9", j2["created"] == 0 and j2["updated"] == 9,
      f"created={j2['created']} updated={j2['updated']}")
_id_ilk = {x["client_id"]: x["id"] for x in j1["synced"]}
_id_ikinci = {x["client_id"]: x["id"] for x in j2["synced"]}
check("B2f) Kopyada AYNI sunucu id'si döndü",
      all(_id_ilk[c] == _id_ikinci[c] for c in _id_ikinci), "")
check("B2g) duplicate kalemi id de taşıyor",
      all(x.get("id") for x in j2["skipped"]), str(j2["skipped"][:1]))


# =============================================================================
# B3 — Bozuk gövde → 422
# =============================================================================
check("B3a) logs alanı yok → 422",
      client.post("/api/v1/logs/batch", headers=H,
                  json={"kayitlar": []}).status_code == 422, "")
check("B3b) logs liste değil → 422",
      client.post("/api/v1/logs/batch", headers=H,
                  json={"logs": "olmaz"}).status_code == 422, "")
check("B3c) logs boş → 422",
      client.post("/api/v1/logs/batch", headers=H,
                  json={"logs": []}).status_code == 422, "")
check("B3d) gövde hiç yok → 422",
      client.post("/api/v1/logs/batch", headers=H).status_code == 422, "")


# =============================================================================
# B4 — Tüm kalemler geçersiz → yine 200
# =============================================================================
r4 = gonder([{"baby_id": BID, "type": "yok", "started_at": utc(600),
              "client_id": "hepsi-1"},
             {"baby_id": BID, "type": "nap", "started_at": "saat-değil",
              "client_id": "hepsi-2"},
             "düz metin"])
j4 = r4.json()
check("B4a) Tamamı geçersizken de 200", r4.status_code == 200,
      f"{r4.status_code} {r4.text[:160]}")
check("B4b) synced boş", j4["synced"] == [], str(j4["synced"]))
check("B4c) 3 kalem de skipped", len(j4["skipped"]) == 3, str(j4["skipped"]))
check("B4d) Nesne olmayan kalem de sebebiyle döndü",
      any(x["reason"] == "invalid" and x["client_id"] is None
          for x in j4["skipped"]), str(j4["skipped"]))


# =============================================================================
# B5 — Sebep kodları
# =============================================================================
_sebep = {x["client_id"]: x["reason"] for x in j4["skipped"] if x["client_id"]}
check("B5a) Geçersiz zaman → invalid_time",
      _sebep.get("hepsi-2") == "invalid_time", str(_sebep))
check("B5b) Geçersiz tip → invalid_type",
      _sebep.get("hepsi-1") == "invalid_type", str(_sebep))

r5 = gonder([{"baby_id": BID, "client_id": "eksik-1"},              # type/started_at yok
             {"baby_id": str(_uuid.uuid4()), "type": "nap",
              "started_at": utc(700), "client_id": "baskasi-1"}])
_s5 = {x["client_id"]: x for x in r5.json()["skipped"]}
check("B5c) Zorunlu alan eksik → missing_field",
      _s5.get("eksik-1", {}).get("reason") == "missing_field", str(_s5))
check("B5d) Başkasının bebeği → not_owned",
      _s5.get("baskasi-1", {}).get("reason") == "not_owned", str(_s5))
check("B5e) not_owned detail'i Türkçe",
      "ait değil" in (_s5.get("baskasi-1", {}).get("detail") or ""), str(_s5))


# =============================================================================
# B6 — GERİYE UYUM (mobil sync-batch.ts hangi alanları okuyor)
# =============================================================================
r6 = gonder([kayit(20)])
j6 = r6.json()
for alan in ("created", "updated", "skipped", "logs", "plan_updated",
             "timer_closed"):
    check(f"B6) `{alan}` alanı duruyor", alan in j6, str(list(j6)))
check("B6a) logs[] client_id + id taşıyor (serverIds eşlemesi)",
      all("client_id" in x and "id" in x for x in j6["logs"]),
      str(j6["logs"][:1]))
check("B6b) plan_updated/timer_closed bool",
      isinstance(j6["plan_updated"], bool)
      and isinstance(j6["timer_closed"], bool), str(j6))
# Mobilin `isDuplicateReason` regex'i: /duplicate|kopya|already|mevcut|exists/i
import re                                                          # noqa: E402
_dup = [x["reason"] for x in j2["skipped"]]
check("B6c) 'duplicate' mobilin kopya regex'ine uyuyor",
      all(re.search(r"duplicate|kopya|already|mevcut|exists", r, re.I)
          for r in _dup), str(_dup[:1]))
# Mobil skipped'ı client_id ile haritalar; anahtarsız kalem onu bozmamalı.
check("B6d) client_id'siz skipped kalemi mobili bozmuyor (null olarak döner)",
      all("client_id" in x for x in j4["skipped"]), str(j4["skipped"]))


# =============================================================================
# RAPOR
# =============================================================================
print("\n" + "=" * 74)
print("POST /logs/batch — KAYIT BAZLI KABUL/RET")
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
