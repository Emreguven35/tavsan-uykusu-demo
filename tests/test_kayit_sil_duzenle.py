"""
DELETE /logs/{id} + PATCH /logs/{id} — tek kayıt silme ve düzeltme.

Kapsam:
  S1  Silme 204; satır gider, silinen_sleep_logs'a TAM JSON arşivlenir
  S2  İdempotent: ikinci silme de 204 (arşivden tanınır), arşive 2. satır YOK
  S3  Başkasının kaydı → 404 (silinmez); hiç var olmamış kimlik → 404
  S4  Başkasının SİLİNMİŞ kaydı → 404 (arşiv kullanıcıya göre süzülür)
  P1  PATCH saat + not düzeltmesi → güncellenmiş kayıt (kategori dahil)
  P2  Gelecek / ters kayıt → Türkçe 422; kayıt DEĞİŞMEZ
  P3  Yalnız bitiş gönderilirse mevcut başlangıçla karşılaştırılır
  P4  Başkasının kaydı → 404; bilinmeyen alan (type) → 422
  P5  ended_at: null açıkça gönderilirse kayıt açılır; hiç gönderilmezse kalır
  PL  Silme ve düzeltme bugünün planını yeniden hesaplatıyor

Çalıştırma: python tests/test_kayit_sil_duzenle.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "kayit_sil_duzenle_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
# Plan testi bugünün 08:20'sine (yerel) gece uykusu bitişi yazıyor; sabah
# erken koşulursa "gelecek" sayılmasın. P2 +2 gün kullanır, tolerans 1 gün.
os.environ["LOG_GELECEK_TOLERANS_DK"] = "1440"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(Path(tempfile.gettempdir()) / "kayit_sil_medya")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import SilinenSleepLog, SleepLog            # noqa: E402
from api.main import app                                    # noqa: E402
from api.routers import logs as logs_router                 # noqa: E402
from api.zaman import bugun_tr                              # noqa: E402

from tests.llm_muhuru import muhurle, TAM_PROFIL                        # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


# Plan tazeleme çağrılarını say — gerçek hesap da koşar.
_TAZELENEN: list[set] = []
_orijinal_tazele = logs_router._plani_tazele


def _sayan_tazele(db, user, baby_ids):
    _TAZELENEN.append(set(baby_ids))
    return _orijinal_tazele(db, user, baby_ids)


logs_router._plani_tazele = _sayan_tazele

SIMDI = datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def hesap(email: str):
    tok = client.post("/api/v1/auth/register",
                      json={"email": email, "password": "TestPass123!"}
                      ).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    bid = client.post("/api/v1/babies", headers=h,
                      json={**TAM_PROFIL, "name": "Test",
                            "birth_date": (SIMDI.date() - timedelta(days=243)).isoformat(),
                            "night_wakes": 2}).json()["id"]
    return h, bid


def kayit_ekle(h, bid, bas: datetime, bit: datetime | None, cid: str,
               tip: str = "nap") -> str:
    r = client.post("/api/v1/logs/batch", headers=h, json={"logs": [{
        "baby_id": bid, "type": tip, "started_at": iso(bas),
        "ended_at": iso(bit) if bit else None, "client_id": cid}]})
    return r.json()["synced"][0]["id"]


def satir(log_id: str):
    import uuid
    db = SessionLocal()
    try:
        return db.get(SleepLog, uuid.UUID(log_id))
    finally:
        db.close()


def arsiv(log_id: str) -> list:
    import uuid
    db = SessionLocal()
    try:
        return (db.query(SilinenSleepLog)
                .filter(SilinenSleepLog.sleep_log_id == uuid.UUID(log_id)).all())
    finally:
        db.close()


H, BID = hesap("sil@test.com")
H2, BID2 = hesap("yabanci@test.com")

# =============================================================================
# S — DELETE
# =============================================================================
L1 = kayit_ekle(H, BID, SIMDI - timedelta(hours=3), SIMDI - timedelta(hours=2),
                "sil-1")
_TAZELENEN.clear()
r = client.delete(f"/api/v1/logs/{L1}", headers=H)
check("S1a) Silme 204 ve gövde boş", r.status_code == 204 and not r.content,
      f"{r.status_code} {r.text[:100]}")
check("S1b) Satır DB'den gitti", satir(L1) is None, "")
_ar = arsiv(L1)
check("S1c) Arşive tek satır düştü", len(_ar) == 1, str(len(_ar)))
_veri = json.loads(_ar[0].veri) if _ar else {}
check("S1d) Arşiv TAM JSON (client_id, type, zamanlar)",
      _veri.get("client_id") == "sil-1" and _veri.get("type") == "nap"
      and _veri.get("started_at") and _veri.get("ended_at"), str(_veri))
check("S1e) Arşiv sebebi kullanici_sildi",
      _ar and _ar[0].sebep == "kullanici_sildi", "")

r = client.delete(f"/api/v1/logs/{L1}", headers=H)
check("S2a) İkinci silme de 204 (idempotent)", r.status_code == 204,
      f"{r.status_code} {r.text[:100]}")
check("S2b) Arşive ikinci satır yazılmadı", len(arsiv(L1)) == 1,
      str(len(arsiv(L1))))

L2 = kayit_ekle(H2, BID2, SIMDI - timedelta(hours=3), SIMDI - timedelta(hours=2),
                "yab-1")
r = client.delete(f"/api/v1/logs/{L2}", headers=H)
check("S3a) Başkasının kaydı → 404", r.status_code == 404,
      f"{r.status_code} {r.text[:100]}")
check("S3b) Başkasının kaydı silinmedi", satir(L2) is not None, "")
r = client.delete("/api/v1/logs/00000000-0000-0000-0000-000000000001",
                  headers=H)
check("S3c) Hiç var olmamış kimlik → 404", r.status_code == 404,
      f"{r.status_code}")

client.delete(f"/api/v1/logs/{L2}", headers=H2)          # sahibi siliyor
r = client.delete(f"/api/v1/logs/{L2}", headers=H)
check("S4) Başkasının SİLİNMİŞ kaydı → 404 (204 değil)", r.status_code == 404,
      f"{r.status_code}")

# =============================================================================
# P — PATCH
# =============================================================================
BAS = SIMDI - timedelta(hours=5)
BIT = SIMDI - timedelta(hours=4)
L3 = kayit_ekle(H, BID, BAS, BIT, "duz-1")

_TAZELENEN_PATCH_ONCE = len(_TAZELENEN)
yeni_bas = BAS - timedelta(minutes=10)
yeni_bit = BIT + timedelta(minutes=15)
r = client.patch(f"/api/v1/logs/{L3}", headers=H,
                 json={"started_at": iso(yeni_bas), "ended_at": iso(yeni_bit),
                       "notes": "saat düzeltildi"})
j = r.json() if r.status_code == 200 else {}
check("P1a) PATCH 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
check("P1b) Yanıt güncellenmiş kaydı taşıyor",
      j.get("id") == L3 and j.get("notes") == "saat düzeltildi"
      and datetime.fromisoformat(j["started_at"].replace("Z", "+00:00"))
      .replace(tzinfo=timezone.utc) == yeni_bas.replace(tzinfo=timezone.utc)
      if j else False, str(j)[:200])
check("P1c) Yanıtta kategori var (uyku kaydı)", j.get("kategori") is not None,
      str(j.get("kategori")))
_s = satir(L3)
check("P1d) DB'de de güncellendi",
      _s is not None and _s.notes == "saat düzeltildi", "")
check("P1e) client_id ve type değişmedi",
      _s is not None and _s.client_id == "duz-1" and _s.type == "nap", "")

r = client.patch(f"/api/v1/logs/{L3}", headers=H,
                 json={"started_at": iso(SIMDI + timedelta(days=2))})
check("P2a) Gelecek başlangıç → 422", r.status_code == 422, f"{r.status_code}")
check("P2b) Mesaj Türkçe (gelecek)",
      "gelecek" in (r.json().get("detail") or ""), r.text[:200])
r = client.patch(f"/api/v1/logs/{L3}", headers=H,
                 json={"started_at": iso(BAS), "ended_at": iso(BAS - timedelta(hours=1))})
check("P2c) Ters kayıt → 422", r.status_code == 422, f"{r.status_code}")
check("P2d) Mesaj Türkçe (bitiş başlangıçtan önce)",
      "başlangıcından önce" in (r.json().get("detail") or ""), r.text[:200])
_s = satir(L3)
check("P2e) Reddedilen PATCH kaydı değiştirmedi",
      _s is not None and _s.notes == "saat düzeltildi", "")

# Yalnız bitiş: mevcut başlangıçtan (yeni_bas) önceye çekmek → ters
r = client.patch(f"/api/v1/logs/{L3}", headers=H,
                 json={"ended_at": iso(yeni_bas - timedelta(minutes=1))})
check("P3a) Yalnız bitiş, mevcut başlangıçtan önce → 422",
      r.status_code == 422, f"{r.status_code}")
r = client.patch(f"/api/v1/logs/{L3}", headers=H,
                 json={"notes": "yalnız not"})
_s = satir(L3)
check("P3b) Yalnız not → saatler korunur",
      r.status_code == 200 and _s.ended_at is not None
      and _s.notes == "yalnız not", f"{r.status_code}")

r = client.patch(f"/api/v1/logs/{L3}", headers=H2, json={"notes": "x"})
check("P4a) Başkasının kaydı → 404", r.status_code == 404, f"{r.status_code}")
r = client.patch(f"/api/v1/logs/{L3}", headers=H, json={"type": "sleep"})
check("P4b) Bilinmeyen alan (type) → 422", r.status_code == 422,
      f"{r.status_code}")
r = client.patch(f"/api/v1/logs/{L3}", headers=H, json={"started_at": None})
check("P4c) started_at: null → 422", r.status_code == 422, f"{r.status_code}")

L4 = kayit_ekle(H, BID, SIMDI - timedelta(minutes=40),
                SIMDI - timedelta(minutes=10), "duz-2")
r = client.patch(f"/api/v1/logs/{L4}", headers=H, json={"ended_at": None})
check("P5a) ended_at: null → kayıt açıldı",
      r.status_code == 200 and r.json().get("ended_at") is None,
      f"{r.status_code} {r.text[:150]}")

check("PL1) DELETE planı yeniden hesaplattı",
      any(BID in {str(x) for x in s} for s in _TAZELENEN[:_TAZELENEN_PATCH_ONCE]),
      str(_TAZELENEN[:3]))
check("PL2) PATCH planı yeniden hesaplattı",
      len(_TAZELENEN) > _TAZELENEN_PATCH_ONCE, str(len(_TAZELENEN)))

# Gerçek plan: gece uykusunun bitişi günün uyanışını belirler. PATCH ile
# bitiş kayınca uyanış kaymalı, DELETE ile kayıt gidince eski hâle dönmeli.
H3, BID3 = hesap("plan@test.com")
client.patch(f"/api/v1/babies/{BID3}", headers=H3,
             json={"training_started_at": (bugun_tr() - timedelta(days=4)).isoformat()})
_gen = client.post("/api/v1/plans/generate?sync=true", headers=H3,
                   json={"baby_id": BID3})
check("PL3) Plan üretildi (fallback)", _gen.status_code == 201
      and _gen.json()["content"].get("generated_with") == "fallback",
      f"{_gen.status_code} {_gen.text[:150]}")


def uyanis() -> str | None:
    r = client.get(f"/api/v1/plans/today?baby_id={BID3}", headers=H3)
    if r.status_code != 200:
        return None
    bloklar = {b["key"]: b for b in r.json()["content"]["schedule"]}
    return (bloklar.get("wake") or {}).get("time")


def yerel(gun_farki: int, dk: int) -> datetime:
    """Yerel (UTC+3) gün + dakika → UTC."""
    g = bugun_tr() + timedelta(days=gun_farki)            # B6: Türkiye günü
    return (datetime(g.year, g.month, g.day, tzinfo=timezone.utc)
            + timedelta(minutes=dk - 180))


w0 = uyanis()
LG = kayit_ekle(H3, BID3, yerel(-1, 20 * 60), yerel(0, 8 * 60 + 20), "gece-1",
                tip="sleep")
w1 = uyanis()
check("PL4) Gece kaydı uyanışı 08:20 yaptı", w1 == "08:20", f"{w0} → {w1}")
r = client.patch(f"/api/v1/logs/{LG}", headers=H3,
                 json={"ended_at": iso(yerel(0, 8 * 60 + 40))})
w2 = uyanis()
check("PL5) PATCH bitişi kaydırdı → plan uyanışı 08:40", r.status_code == 200
      and w2 == "08:40", f"{r.status_code} {w1} → {w2}")
r = client.delete(f"/api/v1/logs/{LG}", headers=H3)
w3 = uyanis()
check("PL6) DELETE → plan kayıtsız hâline döndü", r.status_code == 204
      and w3 == w0 and w3 != "08:40", f"{w0} / {w3}")

# =============================================================================
print("=" * 78)
print("KAYIT SİL / DÜZENLE TEST SONUÇLARI")
print("=" * 78)
for ad, ok, detay in results:
    if not ok:
        print(f"  ✗ {ad}  —  {detay}")
gecen = sum(1 for _, ok, _ in results if ok)
print("-" * 78)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
