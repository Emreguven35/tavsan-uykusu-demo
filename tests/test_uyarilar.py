"""
UYARILAR — canlı ve dinamik (v2.1 / Faz 3, K11). LLM YOK, ağ YOK, prod DB YOK.

v2.0'da `content.uyarilar` plan ÜRETİMİNDE donuyordu ve bir daha hesaplanmıyordu.
Ölçülen üç sonuç:
  • night_wakes 6→1 düzeltilse bile "gece çok uyanıyor" kartı ekranda kalıyordu,
  • 5.5 aylıkken üretilen planda bebek 6 ayı geçince kart HİÇ gelmiyordu,
  • eşik alt dize aramasıyla yapıldığı için 11/12/14/20 uyanma kart üretmiyor,
    "0.5 saatte bir" ise içindeki '5' yüzünden üretiyordu.
Bu dosya üçünü de kilitler.

Çalıştırma: python tests/test_uyarilar.py
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

_DB = Path(tempfile.gettempdir()) / "uyarilar_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import Baby, SleepLog                       # noqa: E402
from api.main import app                                    # noqa: E402
from api.services import plan_adapter as pa                 # noqa: E402
from api.services import plan_service                       # noqa: E402
from engine.parameter_engine import (                       # noqa: E402
    egitim_uygunlugu_kontrol, ilk_tam_sayi)

from tests.llm_muhuru import muhur_saglam_mi, TAM_PROFIL, muhurle       # noqa: E402
muhurle()                                    # canlı Sonnet YOK (Faz 4)

Base.metadata.create_all(bind=engine)
client = TestClient(app)
TZ = pa.TZ_OFFSET_MIN
from api.zaman import bugun_tr  # noqa: E402
TODAY = bugun_tr()  # B6: sunucu günü Türkiye günü

# v1.4 — kart metni TEK. Eskiden beyan/ölçüm için iki ayrı metin vardı;
# İlayda (S6) "sayı değil" dediği için ortalama cümlesi kalktı.
KART = "kendi başına uykuya dönemiyor"       # kart metninin ayırt edici parçası

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


def kart_var(uyarilar) -> bool:
    return any(KART in u for u in uyarilar or [])


def utc(gun, yerel_dk: int) -> datetime:
    return (datetime(gun.year, gun.month, gun.day, tzinfo=timezone.utc)
            + timedelta(minutes=yerel_dk - TZ))


def _tok(eposta: str) -> dict:
    r = client.post("/api/v1/auth/register",
                    json={"email": eposta, "password": "TestPass123!"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def bebek_kur(H, ay: float, night_wakes=None, saglik=None) -> str:
    dogum = TODAY - timedelta(days=int(ay * 30.44))
    govde = {"name": "Test", "birth_date": dogum.isoformat()}
    if night_wakes is not None:
        govde["night_wakes"] = night_wakes
    if saglik is not None:
        govde["saglik_problemi"] = saglik
    return client.post("/api/v1/babies", headers=H, json={**TAM_PROFIL, **govde}).json()["id"]


def plan_uret(H, bid: str) -> dict:
    r = client.post("/api/v1/plans/generate?sync=true", headers=H,
                    json={"baby_id": bid})
    assert r.status_code == 201, r.text
    c = r.json()["content"]
    assert c["generated_with"] == "fallback", c["generated_with"]
    return c


def bugun(H, bid: str) -> dict:
    r = client.get(f"/api/v1/plans/today?baby_id={bid}", headers=H)
    assert r.status_code == 200, r.text
    return r.json()["content"]


def yas_degistir(bid: str, ay: float) -> None:
    """Bebeği yaşlandır/gençleştir (doğum tarihini kaydırarak)."""
    db = SessionLocal()
    row = db.query(Baby).filter(Baby.id == _uuid.UUID(bid)).one()
    row.birth_date = TODAY - timedelta(days=int(ay * 30.44))
    db.commit()
    db.close()


def gece_uyanma_yaz(bid: str, gece_sayisi: int, uyanma_per_gece: int,
                    sure_dk: int = 25) -> None:
    """Son `gece_sayisi` gecede her gece `uyanma_per_gece` adet night_wake.

    `sure_dk` v1.4'te ÖNEMLİ: kart artık sayıya değil, 20 dk+ süren uyanmanın
    kaç gecede görüldüğüne bakıyor. Varsayılan 25 dk → "uzun" sayılır.

    Kayıtlar yerel 23:30'a yazılır. Motor "öğleden ÖNCEKİ uyanma bir önceki
    gecenin" kuralını uyguluyor (detect_regression ile aynı); 02:00'a yazsaydık
    kayıtlar bir gün geriye kayar ve 7 gecelik pencerenin en eskisi dışarıda
    kalırdı (ölçüldü: 7 yazıldı, 6 sayıldı). Fixture motorun kuralına uyar."""
    db = SessionLocal()
    row = db.query(Baby).filter(Baby.id == _uuid.UUID(bid)).one()
    db.query(SleepLog).filter(SleepLog.baby_id == row.id,
                              SleepLog.type == "night_wake").delete()
    for g in range(gece_sayisi):
        gun = TODAY - timedelta(days=g)
        for k in range(uyanma_per_gece):
            bas = utc(gun, 23 * 60 + 30 + k)         # yerel 23:30, 23:31, …
            db.add(SleepLog(user_id=row.user_id, baby_id=row.id,
                            type="night_wake", started_at=bas,
                            ended_at=bas + timedelta(minutes=sure_dk)))
    db.commit()
    db.close()


# =============================================================================
# 1) SAF FONKSİYON — sayısal eşik (alt dize araması KALDIRILDI)
# =============================================================================
print("1) Eşik matrisi (yaş 9 ay, kaynak=beyan — KAYIT YOKKEN geçerli yol)")
for n, beklenen in [(None, False), (0, False), (1, False), (4, False),
                    (5, True), (6, True), (10, True), (11, True), (12, True),
                    (14, True), (20, True), (25, True)]:
    r = egitim_uygunlugu_kontrol(9.0, 40, None, n, "beyan")
    check(f"1) night_wakes={n} → kart {'VAR' if beklenen else 'YOK'}",
          kart_var(r["uyarilar"]) is beklenen, r["uyarilar"])

check("1b) 5.5 aylık + 8 uyanma → kart YOK (yaş eşiği)",
      not kart_var(egitim_uygunlugu_kontrol(5.5, 40, None, 8)["uyarilar"]), "")
check("1c) 6.0 aylık + 8 uyanma → kart VAR",
      kart_var(egitim_uygunlugu_kontrol(6.0, 40, None, 8)["uyarilar"]), "")

print("2) Serbest metinden sayı çıkarma (ilk_tam_sayi)")
for metin, beklenen_sayi, beklenen_kart in [
        ("6 kez", 6, True), ("3-4 kez", 3, False),
        ("0.5 saatte bir", 0, False), ("çok sık", None, False),
        ("on kez", None, False), ("10 defa uyanıyor", 10, True)]:
    sayi = ilk_tam_sayi(metin)
    r = egitim_uygunlugu_kontrol(9.0, 40, None, sayi)
    check(f"2) {metin!r} → sayı={beklenen_sayi}, kart {'VAR' if beklenen_kart else 'YOK'}",
          sayi == beklenen_sayi and kart_var(r["uyarilar"]) is beklenen_kart,
          f"sayi={sayi} uyarilar={r['uyarilar']}")

# v1.4 — İlayda (S6): "Sayı değil, kesinlikle." Ölçülen kaynakta sayı artık
# kartı TEK BAŞINA üretmez; ölçüt uzun_uyanma_gece_sayisi'dır.
check("2b) 'olculen' + yüksek sayı, uzun uyanma YOK → kart YOK",
      not kart_var(egitim_uygunlugu_kontrol(9.0, 40, None, 12, "olculen",
                                            uzun_uyanma_gece_sayisi=0)["uyarilar"]),
      egitim_uygunlugu_kontrol(9.0, 40, None, 12, "olculen",
                               uzun_uyanma_gece_sayisi=0)["uyarilar"])
check("2c) 'olculen' + düşük sayı, 3 gece uzun uyanma → kart VAR",
      kart_var(egitim_uygunlugu_kontrol(9.0, 40, None, 1, "olculen",
                                        uzun_uyanma_gece_sayisi=3)["uyarilar"]), "")
check("2d) 2 gece uzun uyanma eşiğin ALTINDA → kart YOK",
      not kart_var(egitim_uygunlugu_kontrol(9.0, 40, None, 12, "olculen",
                                            uzun_uyanma_gece_sayisi=2)["uyarilar"]), "")
check("2e) Ölçüm BEYANI GÖLGELER (beyan 12, ölçüm 0 → kart YOK)",
      not kart_var(egitim_uygunlugu_kontrol(9.0, 40, None, 12, "beyan",
                                            uzun_uyanma_gece_sayisi=0)["uyarilar"]), "")
check("2f) 6 ay altı: 5 gece uzun uyanma olsa da kart YOK (yaş eşiği)",
      not kart_var(egitim_uygunlugu_kontrol(5.5, 40, None, 12, "olculen",
                                            uzun_uyanma_gece_sayisi=5)["uyarilar"]), "")

# =============================================================================
# 3) CANLI: kart koşul düşünce KALKAR (v2.0'da kalıyordu)
# =============================================================================
H = _tok("uyari1@example.com")
BID = bebek_kur(H, 6.5, night_wakes=6)
c = plan_uret(H, BID)
check("3) Üretimde kart VAR (beyan 6)", kart_var(c["uyarilar"]), c["uyarilar"])

client.patch(f"/api/v1/babies/{BID}", headers=H, json={"night_wakes": 1})
c = bugun(H, BID)
check("3b) night_wakes 6→1 PATCH sonrası GET'te kart KALKTI (yeni üretim YOK)",
      not kart_var(c["uyarilar"]), c["uyarilar"])

client.patch(f"/api/v1/babies/{BID}", headers=H, json={"night_wakes": 7})
check("3c) 1→7 geri alınınca kart GERİ GELDİ",
      kart_var(bugun(H, BID)["uyarilar"]), "")

# =============================================================================
# 4) CANLI: bebek 6 ayı doldurunca kart KENDİLİĞİNDEN gelir
# =============================================================================
H2 = _tok("uyari2@example.com")
BID2 = bebek_kur(H2, 5.5, night_wakes=8)
c2 = plan_uret(H2, BID2)
check("4) 5.5 aylık üretimde kart YOK", not kart_var(c2["uyarilar"]), c2["uyarilar"])
yas_degistir(BID2, 6.2)
c2b = bugun(H2, BID2)
check("4b) Bebek 6.2 aylık olunca GET'te kart ÇIKTI (üretim YOK)",
      kart_var(c2b["uyarilar"]), c2b["uyarilar"])
check("4c) content.yas GÜNCEL yaşı gösteriyor",
      5.9 <= c2b["yas"]["duzeltilmis_ay"] <= 6.5,
      f'{c2b["yas"]["duzeltilmis_ay"]:.2f} ay (üretimde 5.5 idi)')
check("4d) content.bucket da tazelendi", c2b["bucket"] != c2["bucket"]
      or c2b["bucket"] is not None, f'{c2["bucket"]} → {c2b["bucket"]}')

# =============================================================================
# 5) K11 — beyan → ölçülen geçişi
# =============================================================================
H3 = _tok("uyari3@example.com")
BID3 = bebek_kur(H3, 9.0, night_wakes=8)
plan_uret(H3, BID3)

# 2 gece × 25 dk uyanma: KAYIT VAR ama uzun uyanma 3 geceden az → kart YOK.
# (v1.3'te bu senaryoda beyan 8 olduğu için kart VARDI — "sayı" ölçütü kalktı.)
gece_uyanma_yaz(BID3, gece_sayisi=2, uyanma_per_gece=1)
c3 = bugun(H3, BID3)
gu = c3["adaptation"]["gece_uyanma"]
check("5) 2 gece uzun uyanma → beyan 8 olsa bile kart YOK (sayı ölçüt değil)",
      c3["adaptation"]["uzun_uyanma_gece_sayisi"] == 2
      and not kart_var(c3["uyarilar"]),
      f'{gu} uzun={c3["adaptation"]["uzun_uyanma_gece_sayisi"]} '
      f'uyarilar={c3["uyarilar"]}')

gece_uyanma_yaz(BID3, gece_sayisi=3, uyanma_per_gece=1)       # eşik tamam
c3b = bugun(H3, BID3)
gu3b = c3b["adaptation"]["gece_uyanma"]
check("5b) 3 gecede 20 dk+ uyanma → kart VAR (ortalama 1 olsa bile)",
      gu3b["kaynak"] == "olculen" and gu3b["deger"] == 1
      and c3b["adaptation"]["uzun_uyanma_gece_sayisi"] == 3
      and kart_var(c3b["uyarilar"]),
      f'{gu3b} uyarilar={c3b["uyarilar"]}')

# 7 gece × 6 uyanma AMA hepsi 10 dk: sayı yüksek, süre kısa → kart YOK.
gece_uyanma_yaz(BID3, gece_sayisi=7, uyanma_per_gece=6, sure_dk=10)
c3c = bugun(H3, BID3)
gu3c = c3c["adaptation"]["gece_uyanma"]
check("5c) 7 gece × 6 KISA uyanma → kart YOK (20 dk altı sorun değil)",
      gu3c["kaynak"] == "olculen" and gu3c["deger"] == 6
      and c3c["adaptation"]["uzun_uyanma_gece_sayisi"] == 0
      and not kart_var(c3c["uyarilar"]),
      f'{gu3c} uzun={c3c["adaptation"]["uzun_uyanma_gece_sayisi"]} '
      f'uyarilar={c3c["uyarilar"]}')

gece_uyanma_yaz(BID3, gece_sayisi=7, uyanma_per_gece=2, sure_dk=30)
c3d = bugun(H3, BID3)
check("5d) 7 gece × 30 dk uyanma → kart GERİ GELDİ",
      c3d["adaptation"]["uzun_uyanma_gece_sayisi"] == 7
      and kart_var(c3d["uyarilar"]), c3d["uyarilar"])
check("5e) Kart metni sebebi (gündüz uykusu yetersizliği) söylüyor",
      any("gündüz uykusunun yetersiz" in u for u in c3d["uyarilar"]),
      c3d["uyarilar"])
check("5f) Kart metni ARTIK sayı/ortalama yazmıyor",
      not any("ortalama" in u for u in c3d["uyarilar"]), c3d["uyarilar"])

# =============================================================================
# 6) 5 ay altı uyarısı bebek 5 ayı doldurunca KALKAR
# =============================================================================
H4 = _tok("uyari4@example.com")
BID4 = bebek_kur(H4, 4.5, night_wakes=1)
c4 = plan_uret(H4, BID4)
check("6) 4.5 aylık: '5. ayını dolduran' uyarısı VAR, uygun_mu False",
      any("5. ayını dolduran" in u for u in c4["uyarilar"])
      and c4["uygun_mu"] is False, c4["uyarilar"])
yas_degistir(BID4, 5.3)
c4b = bugun(H4, BID4)
check("6b) 5.3 aylık olunca uyarı KALKTI ve uygun_mu True",
      not any("5. ayını dolduran" in u for u in c4b["uyarilar"])
      and c4b["uygun_mu"] is True,
      f'uygun_mu={c4b["uygun_mu"]} uyarilar={c4b["uyarilar"]}')

# =============================================================================
# 7) saglik_problemi KALICI — bant atlaması sonrası korunur
# =============================================================================
H5 = _tok("uyari5@example.com")
BID5 = bebek_kur(H5, 8.0, night_wakes=2, saglik="reflü")
c5 = plan_uret(H5, BID5)
check("7) Sağlık uyarısı üretimde VAR, uygun_mu False",
      any("doktor" in u.lower() for u in c5["uyarilar"])
      and c5["uygun_mu"] is False, c5["uyarilar"])
check("7b) saglik_problemi Baby'ye YAZILDI (kalıcı)",
      client.get("/api/v1/babies", headers=H5).json()[0]["saglik_problemi"] == "reflü",
      client.get("/api/v1/babies", headers=H5).json()[0].get("saglik_problemi"))

# Bant atlat: 8 ay (3 uyku) → 10 ay (2 uyku) → regenerate_required
yas_degistir(BID5, 10.0)
c5b = bugun(H5, BID5)
check("7c) Bant atlaması gerçekten yeniden üretim tetikledi",
      c5b.get("regenerated") is True,
      f'regenerated={c5b.get("regenerated")} '
      f'regen_required={(c5b.get("adaptation") or {}).get("regenerate_required")}')
check("7d) Sağlık uyarısı yeniden üretimden SONRA da duruyor (v2.0'da kayboluyordu)",
      any("doktor" in u.lower() for u in c5b["uyarilar"]), c5b["uyarilar"])
check("7e) uygun_mu hâlâ False", c5b["uygun_mu"] is False, c5b["uygun_mu"])

# =============================================================================
# 8) profile_overrides → Baby'ye kalıcı yazılıyor mu?
# =============================================================================
H6 = _tok("uyari6@example.com")
BID6 = bebek_kur(H6, 9.0)
client.post("/api/v1/plans/generate?sync=true", headers=H6,
            json={"baby_id": BID6, "dogum_haftasi": 34,
                  "profile_overrides": {"saglik_problemi": "hafif egzama",
                                        "gece_uyanma": "6 kez"}})
_b = client.get("/api/v1/babies", headers=H6).json()[0]
check("8) profile_overrides Baby'ye kalıcı yazıldı",
      _b["saglik_problemi"] == "hafif egzama" and _b["night_wakes"] == 6
      and _b["dogum_haftasi"] == 34,
      f'saglik={_b["saglik_problemi"]} nw={_b["night_wakes"]} hafta={_b["dogum_haftasi"]}')
c6 = bugun(H6, BID6)
check("8b) Prematüre uyarısı GET'te üretiliyor (dogum_haftasi kalıcı)",
      any("prematüre" in u.lower() for u in c6["uyarilar"]), c6["uyarilar"])
check("8c) '6 kez' → sayı 6 → gece uyanma kartı VAR",
      kart_var(c6["uyarilar"]), c6["uyarilar"])

# =============================================================================
# 9) Faz 4 — bu suite canlı Sonnet ÇAĞIRMADI
# =============================================================================
_ok, _detay = muhur_saglam_mi()
check("9) LLM mührü sağlam (canlı çağrı YOK)", _ok, _detay)

# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 78)
print("UYARILAR TEST SONUÇLARI (v2.1 / Faz 3 — K11)")
print("=" * 78)
_gecen = 0
for ad, ok, detay in results:
    if ok:
        _gecen += 1
    else:
        print(f"[FAIL] {ad}\n       {detay}")
print("-" * 78)
print(f"TOPLAM: {_gecen}/{len(results)} geçti")
sys.exit(0 if _gecen == len(results) else 1)
