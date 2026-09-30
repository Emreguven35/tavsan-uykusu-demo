"""
Bekleme planında "X gün kaldı" DONMASIN — zamana bağlı alanlar her yanıtta taze.

LLM YOK (mühür), ağ YOK, prod DB YOK: geçici sqlite + TestClient + dondurulmuş saat.

Prod vakası (2026-09-30): 11 Haziran 2026 doğumlu bebek, egitim_bekleme planı
17 Eylül'de üretildi → ekranda "11 Kasım 2026 · 55 gün kaldı". 30 Eylül'de de
55 görünüyordu; doğrusu 42. Mobil sayıyı HESAPLAMIYOR, sunucunun
content.egitim_baslangic.kalan_gun alanını gösteriyor.

  A  17 Eylül'de üretim: 55 gün, 2026-11-11
  B  30 Eylül'de GET /plans/today → 42, tarih AYNI (11-11), yaş da 30 Eylül'e göre
  C  Sayaç günde tam 1 azalır (3'er atlamaz), tarih sabit; 11-11'de 0
  D  GET /plans ve /plans/{tarih}: geçmiş plan KENDİ gününe göre (17 Eylül → 55)
  E  Prematüre: düzeltilmiş yaş (34 hf → 6 hafta ≈ 1.5 ay geç)
  F  Yenidoğan rehberi de aynı alanı taşır: 10 gün sonra 10 eksik

Çalıştırma: python tests/test_bekleme_sayaci.py
"""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "bekleme_sayaci_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"

from fastapi.testclient import TestClient              # noqa: E402
from api.db import Base, engine                        # noqa: E402
import api.models                                      # noqa: E402,F401
from api.main import app                               # noqa: E402
from api.services import plan_service                  # noqa: E402
from api.zaman import saat_sabitle                     # noqa: E402
from tests.llm_muhuru import TAM_PROFIL, canli_cagri_sayisi, muhurle  # noqa: E402

muhurle()
Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []
TR = timezone(timedelta(hours=3))


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


def an(ay: int, gun: int, saat: int = 10) -> datetime:
    return datetime(2026, ay, gun, saat, 0, tzinfo=TR)


tok = client.post("/api/v1/auth/register",
                  json={"email": "bekleme@example.com", "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}


def bebek(ad: str, dogum: str, **ek) -> str:
    return client.post("/api/v1/babies", headers=H,
                       json={**TAM_PROFIL, "name": ad, "birth_date": dogum, **ek}).json()["id"]


def eb(yanit) -> dict:
    return (yanit.json().get("content") or {}).get("egitim_baslangic") or {}


# --- A — 17 Eylül'de üretim ------------------------------------------------
DEFNE = bebek("Defne", "2026-06-11")
with saat_sabitle(an(9, 17)):
    g = client.post("/api/v1/plans/generate?sync=true", headers=H, json={"baby_id": DEFNE})
check("A0) 17 Eylül: bekleme planı üretildi (201, egitim_bekleme)",
      g.status_code == 201 and g.json()["content"].get("type") == "egitim_bekleme",
      g.text[:300])
check("A1) 17 Eylül: 55 gün, 2026-11-11",
      (eb(g).get("kalan_gun"), eb(g).get("tahmini_tarih")) == (55, "2026-11-11"), str(eb(g)))

# --- B — 30 Eylül'de GET /plans/today --------------------------------------
with saat_sabitle(an(9, 30)):
    t = client.get(f"/api/v1/plans/today?baby_id={DEFNE}", headers=H)
check("B1) 30 Eylül: GET /plans/today 200", t.status_code == 200, t.text[:300])
check("B2) 30 Eylül: kalan_gun 42 (55'te DONMADI)", eb(t).get("kalan_gun") == 42, str(eb(t)))
check("B3) Tarih aynı kaldı: 2026-11-11", eb(t).get("tahmini_tarih") == "2026-11-11", str(eb(t)))
check("B4) Yaş 30 Eylül'e göre (3.6 ay), 17 Eylül'ün 3.2'si değil",
      (t.json()["content"].get("yas") or {}).get("duzeltilmis_ay") == 3.6,
      str(t.json()["content"].get("yas")))

# --- C — sayaç günde 1 azalır, tarih sabit ---------------------------------
sayac = []
for gun in (10, 11, 12, 13, 14):
    with saat_sabitle(an(10, gun)):
        r = client.get(f"/api/v1/plans/today?baby_id={DEFNE}", headers=H)
    sayac.append((eb(r).get("kalan_gun"), eb(r).get("tahmini_tarih")))
check("C1) 10-14 Ekim: 32,31,30,29,28 — günde tam 1, tarih hep 11-11",
      sayac == [(n, "2026-11-11") for n in (32, 31, 30, 29, 28)], str(sayac))


class _B:
    birth_date = date(2026, 6, 11)
    dogum_haftasi = None


class _B_TAM(_B):                       # geçiş kontrolü uygunluk alanlarını da okur
    saglik_problemi = None
    night_wakes = 2


check("C2) 10 Kasım'da 1, 11 Kasım'da 0, sonrası 0 (negatif değil)",
      [plan_service.egitim_baslangic_hesapla(_B, d)["kalan_gun"]
       for d in (date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 20))] == [1, 0, 0])

# --- D — geçmiş plan kendi gününe göre --------------------------------------
with saat_sabitle(an(9, 30)):
    liste = client.get(f"/api/v1/plans?baby_id={DEFNE}", headers=H).json()
    eski = client.get(f"/api/v1/plans/2026-09-17?baby_id={DEFNE}", headers=H)
_gunler = {p["plan_date"]: (p["content"].get("egitim_baslangic") or {}).get("kalan_gun")
           for p in liste}
check("D1) GET /plans: 17 Eylül planı 55, 30 Eylül planı 42",
      _gunler.get("2026-09-17") == 55 and _gunler.get("2026-09-30") == 42, str(_gunler))
check("D2) GET /plans/2026-09-17: 55 (o günkü değer)",
      eski.status_code == 200 and eb(eski).get("kalan_gun") == 55, eski.text[:200])

# --- E — prematüre ---------------------------------------------------------
class _P:
    birth_date = date(2026, 6, 11)
    dogum_haftasi = 34


_e = plan_service.egitim_baslangic_hesapla(_P, date(2026, 9, 30))
check("E1) 34 hf prematüre: tarih 6 hafta (1.5 ay) sonra — 2026-12-26, 87 gün",
      (_e["tahmini_tarih"], _e["kalan_gun"]) == ("2026-12-26", 87), str(_e))

# --- F — yenidoğan rehberi -------------------------------------------------
YENI = bebek("Can", "2026-08-15")
with saat_sabitle(an(9, 20)):
    gy = client.post("/api/v1/plans/generate?sync=true", headers=H, json={"baby_id": YENI})
with saat_sabitle(an(9, 30)):
    ty = client.get(f"/api/v1/plans/today?baby_id={YENI}", headers=H)
check("F1) Yenidoğan: 10 gün sonra kalan_gun tam 10 eksik, tarih aynı",
      gy.status_code == 201 and ty.status_code == 200
      and eb(gy).get("kalan_gun") - eb(ty).get("kalan_gun") == 10
      and eb(gy).get("tahmini_tarih") == eb(ty).get("tahmini_tarih"),
      f"{eb(gy)} → {eb(ty)}")

# --- G — metin (markdown) ----------------------------------------------------
check("G1) Yeni üretilen bekleme metninde KALAN GÜN SAYISI yok (tarih var)",
      "2026-11-11" in g.json()["content"]["markdown"]
      and "55 gün" not in g.json()["content"]["markdown"],
      g.json()["content"]["markdown"][:400])
_eski_icerik = {"markdown": "Tahminen **2026-11-11**, yaklaşık 55 gün sonra başlar. "
                            "13 Günlük program; 55 günlük süre; 155 gün.",
                "egitim_baslangic": {"kalan_gun": 55, "tahmini_tarih": "2026-11-11"}}
_taze = plan_service.zamana_bagli_alanlari_tazele(_eski_icerik, _B, date(2026, 9, 30))
check("G2) Eski planın metnindeki '55 gün' → '42 gün'; '55 günlük' ve '155 gün' korunur",
      "yaklaşık 42 gün sonra" in _taze["markdown"]
      and "55 günlük" in _taze["markdown"] and "155 gün" in _taze["markdown"],
      _taze["markdown"])

# --- H — geçiş, ekrandaki tahmini_tarih GÜNÜ (onaylı, 2026-09-30) -----------
# Eskiden yuvarlanmış yaş (4.95 → 5.0) geçişi 9 Kasım'da tetikliyordu.
class _Bekleyen:
    content = {"type": "egitim_bekleme", "dogum_haftasi": 40}


_tetik = {}
for _g in (9, 10, 11):
    with saat_sabitle(an(11, _g)):
        _tetik[_g] = plan_service.egitim_zamani_geldi_mi(_B_TAM, _Bekleyen)
check("H1) Tetik: 9 ve 10 Kasım YOK, 11 Kasım VAR", _tetik == {9: False, 10: False, 11: True},
      str(_tetik))
with saat_sabitle(an(11, 10)):
    t10 = client.get(f"/api/v1/plans/today?baby_id={DEFNE}", headers=H).json()["content"]
check("H2) 10 Kasım GET /plans/today: geçiş YOK (bekleme, 1 gün kaldı)",
      not t10.get("egitim_zamani_geldi") and t10.get("type") == "egitim_bekleme"
      and (t10.get("egitim_baslangic") or {}).get("kalan_gun") == 1, str({k: t10.get(k) for k in ("type", "egitim_zamani_geldi", "egitim_baslangic")}))
with saat_sabitle(an(11, 11)):
    t11 = client.get(f"/api/v1/plans/today?baby_id={DEFNE}", headers=H).json()["content"]
check("H3) 11 Kasım GET /plans/today: geçiş VAR (egitim_zamani_geldi + yeniden_uretiliyor)",
      t11.get("egitim_zamani_geldi") is True and t11.get("yeniden_uretiliyor") is True,
      str({k: t11.get(k) for k in ("type", "egitim_zamani_geldi", "yeniden_uretiliyor")}))
check("H4) Geçiş günü = ekrandaki tahmini_tarih (2026-11-11)",
      (t10.get("egitim_baslangic") or {}).get("tahmini_tarih") == "2026-11-11")

check("Z) Canlı LLM çağrısı YOK", canli_cagri_sayisi() == 0, canli_cagri_sayisi())

print("=" * 76)
print("BEKLEME PLANI SAYACI — zamana bağlı alanlar")
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
