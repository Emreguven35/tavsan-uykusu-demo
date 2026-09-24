"""
SIRADAKİ BLOK + SABAH UYANIŞI KAYNAĞI (v2.4.3) — mobil sözleşme testleri.

LLM YOK, ağ YOK, prod DB YOK: geçici sqlite + FastAPI TestClient + LLM mührü.

NEDEN VAR: mobil "sıradaki uyku" kartı bugüne kadar saati KESİNMİŞ gibi
gösteriyordu. Oysa çizelge bir zincirdir: her uykunun saati bir öncekinin
GERÇEK bitişinden türer, zincirin çıpası da sabah uyanışıdır. Halkalardan biri
kayıt değilse saat TAHMİNDİR ve anneye bunu söylemek, hangi kaydın eksik
olduğunu göstermek gerekir.

adaptation.siradaki_blok sözleşmesi:
    {key, time, end, guven: "kesin"|"tahmini",
     eksik_kayit: "sabah_uyanisi"|"son_uyku_bitisi"|null}

Senaryolar:
  A  Hiç kayıt yok                → tahmini / sabah_uyanisi
  B  wake 07:00, uyku yok         → kesin
  C  wake + KAPANMIŞ nap          → kesin
  D  wake + AÇIK nap (devam)      → tahmini / son_uyku_bitisi
  E  wake YOK + kapanmış nap      → tahmini / sabah_uyanisi (çıpa önceliklidir)
  F  wake + OTOMATİK kapanan nap  → tahmini / son_uyku_bitisi
  G  Gece yatışı da geçti         → siradaki_blok None
  H  Alanlar çizelgedeki blokla birebir
  I  Erken uyanma (05:30)         → kesin (kayıt vardır, yalnız gün 06:00'dan)
  J  Sözleşme: değerler yalnız tanımlı kümeden
  K  sabah_uyanis_kaynak üç değeri de üretebiliyor (mobil sabah sorusu)
  L  Uçtan uca: GET /plans/today gövdesinde alanlar var

Çalıştırma: python tests/test_siradaki_blok.py
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

_DB = Path(tempfile.gettempdir()) / "siradaki_blok_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
# Suite tam bir günü simüle ediyor; sabah koşulduğunda 09:30 kaydı "gelecek"
# düşer ve v2.4.1 zaman doğrulaması onu eler. Üretim varsayılanı 5 dk.
os.environ["LOG_GELECEK_TOLERANS_DK"] = "1440"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"        # zamanlayıcı başlamasın
os.environ["MAIL_PROVIDER"] = "disabled"

from fastapi.testclient import TestClient              # noqa: E402
from api.db import Base, SessionLocal, engine          # noqa: E402
import api.models                                      # noqa: E402,F401
from api.models import Baby, SleepLog                  # noqa: E402
from api.main import app                               # noqa: E402
from api.services import plan_adapter as pa            # noqa: E402
from engine import yas_bantlari                        # noqa: E402
from engine.parameter_engine import hesapla_yas_ay     # noqa: E402

from tests.llm_muhuru import canli_cagri_sayisi, TAM_PROFIL, muhurle   # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


TZ = pa.TZ_OFFSET_MIN
TODAY = datetime.now(timezone.utc).date()


def utc(gun, yerel_dk: int) -> datetime:
    """Yerel (UTC+3) duvar dakikası → UTC datetime."""
    return (datetime(gun.year, gun.month, gun.day, tzinfo=timezone.utc)
            + timedelta(minutes=yerel_dk - TZ))


tok = client.post("/api/v1/auth/register",
                  json={"email": "siradaki_blok@example.com",
                        "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

DOGUM = TODAY - timedelta(days=243)                     # ≈ 8.0 ay
BABY_ID = client.post("/api/v1/babies", headers=H,
                      json={**TAM_PROFIL, "name": "Elif", "birth_date": DOGUM.isoformat(),
                            "night_wakes": 2}).json()["id"]
_gen = client.post("/api/v1/plans/generate?sync=true", headers=H,
                   json={"baby_id": BABY_ID})
assert _gen.status_code == 201, _gen.text
TEMPLATE = _gen.json()["content"]["schedule_template"]
YAS_AY = hesapla_yas_ay(DOGUM.isoformat(), 40)["duzeltilmis_ay"]
BANT = yas_bantlari.yas_bandi_getir(YAS_AY)
WAKE = pa.sabit_wake_minute(TEMPLATE)

_db = SessionLocal()
try:
    _ROW = _db.query(Baby).filter(Baby.id == _uuid.UUID(BABY_ID)).one()
    UID, BID = _ROW.user_id, _ROW.id
finally:
    _db.close()


def L(tip: str, bas_dk: int, bit_dk=None) -> SleepLog:
    """DB'ye YAZILMAYAN kayıt nesnesi (recompute_day saf fonksiyon)."""
    return SleepLog(
        id=_uuid.uuid4(), user_id=UID, baby_id=BID, type=tip,
        started_at=utc(TODAY, bas_dk),
        ended_at=None if bit_dk is None else utc(TODAY, bit_dk))


def hesapla(loglar, now_minute: int) -> dict:
    return pa.recompute_day(TEMPLATE, BANT, WAKE, loglar,
                            now_minute=now_minute, gun=TODAY, tz_offset_min=TZ)


def sb(sonuc: dict):
    return sonuc["adaptation"]["siradaki_blok"]


def blok(sonuc: dict, key: str):
    return next((b for b in sonuc["schedule"] if b.get("key") == key), None)


# Şablondaki ilk iki uykunun saatleri (bant değiştiğinde test de değişsin).
_SBL = {b["key"]: b for b in pa.normalize_schedule(TEMPLATE)}
NAP1 = _SBL["nap_1"]["start_minute"]
NAP1_BIT = _SBL["nap_1"]["end_minute"]

# =============================================================================
# A — Hiç kayıt yok: zincirin çıpası eksik
# =============================================================================
a = hesapla([], now_minute=8 * 60)
check("A1) Hiç kayıt yokken siradaki_blok üretilir",
      isinstance(sb(a), dict), str(sb(a)))
check("A2) Hiç kayıt yokken guven='tahmini'",
      sb(a) and sb(a)["guven"] == "tahmini", str(sb(a)))
check("A3) Eksik olan sabah uyanışıdır",
      sb(a) and sb(a)["eksik_kayit"] == "sabah_uyanisi", str(sb(a)))
check("A4) Sıradaki blok, saati henüz gelmemiş İLK uykudur",
      sb(a) and sb(a)["key"] == "nap_1", str(sb(a)))

# =============================================================================
# B — Sabah uyanışı kayıtlı, henüz uyku yok: zincir sağlam
# =============================================================================
b = hesapla([L("wake", 7 * 60)], now_minute=8 * 60)
check("B1) Uyanma kaydı varken guven='kesin'",
      sb(b) and sb(b)["guven"] == "kesin", str(sb(b)))
check("B2) Eksik kayıt yok (null)",
      sb(b) and sb(b)["eksik_kayit"] is None, str(sb(b)))
check("B3) sabah_uyanis_kaynak='kayit'",
      b["adaptation"]["sabah_uyanis_kaynak"] == "kayit",
      b["adaptation"]["sabah_uyanis_kaynak"])

# =============================================================================
# C — Kapanmış gerçek uyku kaydı: zincir hâlâ sağlam
# =============================================================================
c = hesapla([L("wake", 7 * 60), L("sleep", NAP1, NAP1 + 70)],
            now_minute=NAP1 + 90)
check("C1) Kapanmış kayıttan sonra guven='kesin'",
      sb(c) and sb(c)["guven"] == "kesin", str(sb(c)))
check("C2) Sıradaki blok 2. uykudur",
      sb(c) and sb(c)["key"] == "nap_2", str(sb(c)))
check("C3) 1. uyku gerçekten kayıttan geldi (ön koşul)",
      (blok(c, "nap_1") or {}).get("kaynak") == "kayit",
      str(blok(c, "nap_1")))

# =============================================================================
# D — AÇIK uyku kaydı: bitişi bilinmiyor, zincir tahmine döner
# =============================================================================
d = hesapla([L("wake", 7 * 60), L("sleep", NAP1)], now_minute=NAP1 + 30)
check("D1) Açık kayıt ön koşulu: blok 'devam' işaretli",
      (blok(d, "nap_1") or {}).get("devam") is True, str(blok(d, "nap_1")))
check("D2) Açık kayıtta guven='tahmini'",
      sb(d) and sb(d)["guven"] == "tahmini", str(sb(d)))
check("D3) Eksik olan son uykunun bitişidir",
      sb(d) and sb(d)["eksik_kayit"] == "son_uyku_bitisi", str(sb(d)))
check("D4) Sabah uyanışı kayıtlı olduğu hâlde 'kesin' DEĞİL",
      d["adaptation"]["sabah_uyanis_kaynak"] == "kayit"
      and sb(d)["guven"] == "tahmini",
      f"kaynak={d['adaptation']['sabah_uyanis_kaynak']} {sb(d)}")

# =============================================================================
# E — Sabah uyanışı YOK ama uyku kaydı var: çıpa önceliklidir
# =============================================================================
e = hesapla([L("sleep", NAP1, NAP1 + 70)], now_minute=NAP1 + 90)
check("E1) Sabah uyanışı varsayılanken guven='tahmini'",
      sb(e) and sb(e)["guven"] == "tahmini", str(sb(e)))
check("E2) Son uyku kapanmış olsa bile eksik 'sabah_uyanisi'",
      sb(e) and sb(e)["eksik_kayit"] == "sabah_uyanisi", str(sb(e)))
check("E3) Ön koşul: sabah_uyanis_kaynak='varsayilan'",
      e["adaptation"]["sabah_uyanis_kaynak"] == "varsayilan",
      e["adaptation"]["sabah_uyanis_kaynak"])

# =============================================================================
# F — Sayacı unutulmuş (otomatik kapanan) kayıt: bitiş TAHMİNDİR
# =============================================================================
# 06:30'da açılıp unutulmuş kayıt; K17 eşiğini (3 saat) aştığı için motor
# kapatır ve 'otomatik_kapandi' yazar. Bitiş saati bizim tahminimizdir.
f = hesapla([L("wake", 6 * 60), L("sleep", 6 * 60 + 40)], now_minute=14 * 60)
_f1 = next((b for b in f["schedule"]
            if b.get("otomatik_kapandi")), None)
check("F1) Ön koşul: bir blok otomatik kapatıldı",
      _f1 is not None, str([b.get("key") for b in f["schedule"]]))
check("F2) Otomatik kapanan kayıttan sonra guven='tahmini'",
      sb(f) is None or sb(f)["guven"] == "tahmini", str(sb(f)))
check("F3) Eksik olan son uykunun bitişidir",
      sb(f) is None or sb(f)["eksik_kayit"] == "son_uyku_bitisi", str(sb(f)))

# =============================================================================
# G — Gün bitti: sıradaki blok YOK
# =============================================================================
g = hesapla([L("wake", 7 * 60)], now_minute=23 * 60 + 30)
check("G1) Gece yatışı da geçtiyse siradaki_blok None",
      sb(g) is None, str(sb(g)))

# =============================================================================
# H — Alanlar çizelgedeki blokla birebir aynı
# =============================================================================
h = hesapla([L("wake", 7 * 60)], now_minute=8 * 60)
_hb = blok(h, sb(h)["key"])
check("H1) time alanı çizelgedeki blokla aynı",
      _hb and sb(h)["time"] == _hb["time"], f"{sb(h)} vs {_hb}")
check("H2) end alanı çizelgedeki blokla aynı",
      _hb and sb(h)["end"] == _hb["end"], f"{sb(h)} vs {_hb}")
check("H3) Sıradaki blok GERÇEKTEN gelecekte",
      _hb and _hb["start_minute"] > 8 * 60, str(_hb))
check("H4) Sözleşme anahtarları eksiksiz",
      set(sb(h)) == {"key", "time", "end", "guven", "eksik_kayit"},
      str(sorted(sb(h))))

# =============================================================================
# I — Erken uyanma: kayıt VARDIR, yalnız gün 06:00'dan kurulur
# =============================================================================
i = hesapla([L("wake", 5 * 60 + 30)], now_minute=7 * 60)
check("I1) Ön koşul: sabah_uyanis_kaynak='erken_uyanma'",
      i["adaptation"]["sabah_uyanis_kaynak"] == "erken_uyanma",
      i["adaptation"]["sabah_uyanis_kaynak"])
check("I2) Erken uyanma da KAYITTIR → guven='kesin'",
      sb(i) and sb(i)["guven"] == "kesin", str(sb(i)))
check("I3) Eksik kayıt yok",
      sb(i) and sb(i)["eksik_kayit"] is None, str(sb(i)))

# =============================================================================
# J — Sözleşme: değerler yalnız tanımlı kümeden çıkar
# =============================================================================
_GUVEN = {"kesin", "tahmini"}
_EKSIK = {"sabah_uyanisi", "son_uyku_bitisi", None}
_ihlal = []
for _saat in range(5 * 60, 24 * 60, 37):
    for _loglar in ([], [L("wake", 7 * 60)],
                    [L("wake", 7 * 60), L("sleep", NAP1)],
                    [L("wake", 7 * 60), L("sleep", NAP1, NAP1 + 70)]):
        _s = sb(hesapla(list(_loglar), now_minute=_saat))
        if _s is None:
            continue
        if _s["guven"] not in _GUVEN or _s["eksik_kayit"] not in _EKSIK:
            _ihlal.append((_saat, _s))
        if (_s["guven"] == "kesin") != (_s["eksik_kayit"] is None):
            _ihlal.append((_saat, "guven/eksik_kayit tutarsız", _s))
check("J1) 124 senaryoda guven ve eksik_kayit hep tanımlı kümeden",
      not _ihlal, str(_ihlal[:3]))

# =============================================================================
# K — sabah_uyanis_kaynak üç değeri de üretir (mobil sabah sorusu bunu okur)
# =============================================================================
check("K1) Kayıt yok → 'varsayilan' (mobil sabah sorusunu GÖSTERİR)",
      hesapla([], now_minute=8 * 60)["adaptation"]["sabah_uyanis_kaynak"]
      == "varsayilan", "")
check("K2) Uyanma kaydı → 'kayit' (soru gösterilmez)",
      b["adaptation"]["sabah_uyanis_kaynak"] == "kayit", "")
check("K3) Erken uyanma → 'erken_uyanma' (soru gösterilmez)",
      i["adaptation"]["sabah_uyanis_kaynak"] == "erken_uyanma", "")
check("K4) Alan adı sözleşmede 'sabah_uyanis_kaynak' (…uyanisi… DEĞİL)",
      "sabah_uyanis_kaynak" in a["adaptation"]
      and "sabah_uyanisi_kaynak" not in a["adaptation"],
      str([k for k in a["adaptation"] if "uyani" in k]))
check("K5) Gece uykusu bitişi de sabah uyanışı sayılır → 'kayit'",
      hesapla([L("sleep", -3 * 60, 7 * 60 + 10)],
              now_minute=8 * 60)["adaptation"]["sabah_uyanis_kaynak"] == "kayit",
      "")

# =============================================================================
# L — Uçtan uca: GET /plans/today gövdesinde alanlar var
# =============================================================================
_r = client.get(f"/api/v1/plans/today?baby_id={BABY_ID}", headers=H)
check("L1) GET /plans/today 200", _r.status_code == 200, _r.text[:200])
# Sözleşme kullanıcının tarif ettiği yerdedir: content.adaptation
_ad = ((_r.json().get("content") or {}).get("adaptation") or {}
       if _r.status_code == 200 else {})
check("L2) content.adaptation içinde siradaki_blok alanı var",
      "siradaki_blok" in _ad, str(sorted(_ad))[:300])
check("L3) content.adaptation içinde sabah_uyanis_kaynak var",
      "sabah_uyanis_kaynak" in _ad, str(sorted(_ad))[:300])
_sbl = _ad.get("siradaki_blok")
check("L4) siradaki_blok ya None ya tam sözleşme",
      _sbl is None
      or set(_sbl) == {"key", "time", "end", "guven", "eksik_kayit"},
      str(_sbl))
check("L5) Kayıt girilmemiş hesapta guven='tahmini'",
      _sbl is None or _sbl["guven"] == "tahmini", str(_sbl))

# --- LLM mührü ---------------------------------------------------------------
check("M1) Hiç canlı LLM çağrısı yapılmadı", canli_cagri_sayisi() == 0,
      f"çağrı={canli_cagri_sayisi()}")

# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("SIRADAKİ BLOK + SABAH UYANIŞI KAYNAĞI (v2.4.3)")
print("=" * 74)
passed = 0
for name, ok, detail in results:
    mark = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    print(f"[{mark}] {name}")
    if detail and not ok:
        print(f"       {detail}")
print("-" * 74)
print(f"TOPLAM: {passed}/{len(results)} geçti")
print("=" * 74)
sys.exit(0 if passed == len(results) else 1)
