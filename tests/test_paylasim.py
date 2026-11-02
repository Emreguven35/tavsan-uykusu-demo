"""
Paylaşım kartları (/paylasim/haftalik, /paylasim/mezuniyet) + gece uyanması gün
ataması + timeline bitis_bilinmiyor/sayima_girmez (2026-11-02).

LLM YOK, ağ YOK, prod DB YOK: geçici sqlite + TestClient + dondurulmuş saat
(2026-11-02 10:00 TR). Kayıtlar sentetik; E bölümündeki 4 kayıt prod'daki
paylaşım kartı vakasının YALNIZ SAATLERİDİR (kişisel veri yok).

  A  Gün özeti: tam gün / yarım gün / kayıtsız gün (null, 0 değil)
  B  Hariç tutma: şüpheli uzun, 12:00 sonrası biten gece, sunucunun kapattığı,
     açık kayıt — toplamlara ve en uzuna girmez, haric_tutulan'da
  C  vurgu_metni: her aday, eşik altı, 0'a iniş (5 tam gece), en büyük oran,
     önceki hafta yok → null
  D  Minimum veri: 0 / 2 / 4 tam gün → paylasilabilir + mesaj + null ortalamalar
  E  Prod vakası (paylaşım kartı): paylasilabilir=false, r3 haric_tutulan'da
  F  gece_uyanma: night_wake yoksa null, varsa gecelik ortalama; önceki hafta
  G  egitim_gunu yalnız durum "devam" iken
  H  Mezuniyet: 404 (değerlendirme yok / yalnız training_completed_at / devam),
     200; kaynak egitim_oncesi → egitim_1_2_gun → beyan; simdi yetersiz → null
  I  Sahiplik: başkasının bebeği 404, oturumsuz 401, gelecek bitis 422
  J  Gece uyanması gün ataması (19:00 sonrası → ertesi gün), timeline ve
     weekly-summary aynı günü sayar
  K  Timeline: bitis_bilinmiyor / sayima_girmez
  L  baby_id opsiyonel: parametresiz → aktif (en yeni) bebek; bebek yoksa 404

Çalıştırma: python tests/test_paylasim.py
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

_DB = Path(tempfile.gettempdir()) / "paylasim_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"

from fastapi.testclient import TestClient              # noqa: E402
from api.db import Base, SessionLocal, engine          # noqa: E402
import api.models                                      # noqa: E402,F401
from api.models import Baby, EgitimDegerlendirme, SleepLog, SleepPlan  # noqa: E402
from api.main import app                               # noqa: E402
from api.services import paylasim as P                 # noqa: E402
from api.services import plan_adapter as pa            # noqa: E402
from api.zaman import saat_sabitle                     # noqa: E402

Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []
TR = timezone(timedelta(hours=3))
SAAT = datetime(2026, 11, 2, 10, 0, tzinfo=TR)
BUGUN = date(2026, 11, 2)
DUN = BUGUN - timedelta(days=1)


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


def tr(g: date, hhmm: str) -> datetime:
    s, d = map(int, hhmm.split(":"))
    return datetime(g.year, g.month, g.day, s, d, tzinfo=TR).astimezone(timezone.utc)


def kayit_ol(eposta: str) -> dict:
    tok = client.post("/api/v1/auth/register",
                      json={"email": eposta, "password": "TestPass123!"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


H = kayit_ol("paylasim@example.com")
H_BASKA = kayit_ol("baskasi-paylasim@example.com")
DOGUM = (BUGUN - timedelta(days=240)).isoformat()      # ~8 aylık


def yeni_bebek(ad: str = "X", headers: dict | None = None, **alan) -> uuid.UUID:
    bid = client.post("/api/v1/babies", headers=headers or H,
                      json={"name": ad, "birth_date": DOGUM}).json()["id"]
    if alan:
        db = SessionLocal()
        try:
            b = db.get(Baby, uuid.UUID(bid))
            for k, v in alan.items():
                setattr(b, k, v)
            db.commit()
        finally:
            db.close()
    return uuid.UUID(bid)


class L:
    """Saf testler için SleepLog benzeri (DB'siz)."""
    def __init__(self, tip, bas, bit=None, kaynak=None):
        self.id = uuid.uuid4()
        self.type, self.started_at, self.ended_at = tip, bas, bit
        self.kapanis_kaynagi = kaynak


def ekle(bebek: uuid.UUID, kayitlar: list) -> None:
    """kayitlar: (tip, (gün, 'HH:MM'), (gün, 'HH:MM') | None, kapanis_kaynagi?)"""
    db = SessionLocal()
    try:
        uid = db.get(Baby, bebek).user_id
        for i, k in enumerate(kayitlar):
            tip, b, e = k[0], k[1], k[2]
            db.add(SleepLog(user_id=uid, baby_id=bebek, type=tip, started_at=tr(*b),
                            ended_at=tr(*e) if e else None,
                            kapanis_kaynagi=k[3] if len(k) > 3 else None,
                            client_id=f"{bebek}-{i}-{uuid.uuid4().hex[:6]}"))
        db.commit()
    finally:
        db.close()


def tam_gun(g: date, gece_bas="20:00", gece_bit="06:30", nap=("10:00", "11:30")) -> list:
    """g gününe bağlanan tam gün: önceki akşam başlayan gece + bir gündüz uykusu."""
    return [("sleep", (g - timedelta(days=1), gece_bas), (g, gece_bit)),
            ("nap", (g, nap[0]), (g, nap[1]))]


def haftalik(bebek, bitis: date | None = None, headers=None):
    url = f"/api/v1/paylasim/haftalik?baby_id={bebek}" + (f"&bitis={bitis}" if bitis else "")
    with saat_sabitle(SAAT):
        return client.get(url, headers=headers or H)


def mezuniyet(bebek, headers=None):
    with saat_sabitle(SAAT):
        return client.get(f"/api/v1/paylasim/mezuniyet?baby_id={bebek}", headers=headers or H)


# =============================================================================
# A — gün özeti
# =============================================================================
G = date(2026, 10, 20)
rows_a = [L("sleep", tr(G - timedelta(days=1), "20:00"), tr(G, "06:30")),
          L("nap", tr(G, "10:00"), tr(G, "11:30")),
          L("nap", tr(G + timedelta(days=1), "10:00"), tr(G + timedelta(days=1), "11:00"))]
oz = P.gun_ozeti(rows_a, G)
check("A1 tam gün: gece 630 + gündüz 90, tam=true",
      oz["gece_dk"] == 630 and oz["gunduz_dk"] == 90 and oz["toplam_dk"] == 720 and oz["tam"], oz)
oz2 = P.gun_ozeti(rows_a, G + timedelta(days=1))
check("A2 yarım gün (yalnız gündüz): gece_dk null, tam=false, toplam=gündüz",
      oz2["gece_dk"] is None and oz2["gunduz_dk"] == 60 and oz2["toplam_dk"] == 60
      and not oz2["tam"], oz2)
oz3 = P.gun_ozeti(rows_a, G + timedelta(days=2))
check("A3 kayıtsız gün: hepsi null (0 değil)",
      oz3["gece_dk"] is None and oz3["gunduz_dk"] is None and oz3["toplam_dk"] is None, oz3)

# =============================================================================
# B — hariç tutma (saf)
# =============================================================================
rows_b = [
    L("sleep", tr(G - timedelta(days=1), "23:20"), tr(G, "13:03")),           # 823 dk
    L("nap", tr(G, "15:00"), tr(G, "16:00")),
    L("nap", tr(G, "17:00"), tr(G, "18:10"), kaynak="bakim"),                 # sunucu kapattı
]
ob = P.gun_ozeti(rows_b, G)
sebepler = sorted(h["sebep"] for h in ob["haric"])
check("B1 823 dk gece supheli_uzun; bakim → otomatik_kapandi",
      sebepler == ["otomatik_kapandi", "supheli_uzun"], ob["haric"])
check("B2 hariç kayıtlar toplam/en uzun dışı (yalnız 60 dk'lık gündüz)",
      ob["gece_dk"] is None and ob["gunduz_dk"] == 60 and ob["en_uzun_dk"] == 60
      and not ob["tam"], ob)
G2 = G + timedelta(days=5)
ob2 = P.gun_ozeti([L("sleep", tr(G2, "01:00"), tr(G2, "12:30")),               # 690 dk, 12:30
                   L("nap", tr(G2, "14:00"), tr(G2, "15:00"))], G2)
check("B3 12:00 sonrası biten gece (12 sa altı) → supheli_gec_bitis",
      [h["sebep"] for h in ob2["haric"]] == ["supheli_gec_bitis"] and ob2["gece_dk"] is None, ob2)
G3 = G + timedelta(days=8)
ob3 = P.gun_ozeti([L("sleep", tr(G3 - timedelta(days=1), "19:30"), tr(G3, "07:00")),  # 690 dk
                   L("nap", tr(G3, "09:00"), None)], G3)
check("B4 11,5 sa / 07:00 biten gece sayılır; açık gündüz uykusu 'acik' hariç",
      ob3["gece_dk"] == 690 and [h["sebep"] for h in ob3["haric"]] == ["acik"]
      and ob3["gunduz_dk"] is None, ob3)
G4 = G + timedelta(days=10)
ob4 = P.gun_ozeti([L("nap", tr(G4, "06:30"), tr(G4, "19:00"))], G4)      # 750 dk gündüz
check("B5 12 saati aşan gündüz uykusu da supheli_uzun",
      [h["sebep"] for h in ob4["haric"]] == ["supheli_uzun"], ob4)

# =============================================================================
# C — vurgu_metni (saf)
# =============================================================================
def hafta(**k):
    d = {"tam_gun_sayisi": 6, "tam_gece_sayisi": 6, "ort_uyku_dk_gun": 700,
         "gece_uyanma": 2.0, "en_uzun_uyku_dk": 300}
    d.update(k)
    return d


ONCEKI = {"ort_uyku_dk_gun": 700, "gece_uyanma": 2.0, "en_uzun_uyku_dk": 300}
check("C1 uyanma 2,0 → 0,5 (%75) → 'Gece uyanmaları %75 azaldı'",
      P.vurgu_metni(hafta(gece_uyanma=0.5), ONCEKI) == "Gece uyanmaları %75 azaldı",
      P.vurgu_metni(hafta(gece_uyanma=0.5), ONCEKI))
check("C2 uyanma azalması %20 → aday değil, null",
      P.vurgu_metni(hafta(gece_uyanma=1.6), ONCEKI) is None, P.vurgu_metni(hafta(gece_uyanma=1.6), ONCEKI))
check("C3 önceki < 1,0/gece → uyanma adayı yok",
      P.vurgu_metni(hafta(gece_uyanma=0.2), dict(ONCEKI, gece_uyanma=0.8)) is None)
check("C4 0'a iniş: 4 tam gece → null, 5 tam gece → %100",
      P.vurgu_metni(hafta(gece_uyanma=0.0, tam_gece_sayisi=4, tam_gun_sayisi=4), ONCEKI) is None
      and P.vurgu_metni(hafta(gece_uyanma=0.0, tam_gece_sayisi=5), ONCEKI)
      == "Gece uyanmaları %100 azaldı")
check("C5 en uzun 300 → 345 (+45 dk, %15) → 'En uzun uyku %15 uzadı'",
      P.vurgu_metni(hafta(en_uzun_uyku_dk=345), ONCEKI) == "En uzun uyku %15 uzadı",
      P.vurgu_metni(hafta(en_uzun_uyku_dk=345), ONCEKI))
check("C6 en uzun +25 dk → eşik altı, null",
      P.vurgu_metni(hafta(en_uzun_uyku_dk=325), ONCEKI) is None)
check("C7 ort uyku 700 → 740 (+40 dk, %5,7 aşağı → %5)",
      P.vurgu_metni(hafta(ort_uyku_dk_gun=740), ONCEKI) == "Günlük ortalama uyku %5 arttı",
      P.vurgu_metni(hafta(ort_uyku_dk_gun=740), ONCEKI))
check("C8 ort uyku +29 dk → null",
      P.vurgu_metni(hafta(ort_uyku_dk_gun=729), ONCEKI) is None)
check("C9 birden çok aday: en büyük göreli iyileşme (en uzun %50 > uyanma %25)",
      P.vurgu_metni(hafta(gece_uyanma=1.5, en_uzun_uyku_dk=450), ONCEKI)
      == "En uzun uyku %50 uzadı",
      P.vurgu_metni(hafta(gece_uyanma=1.5, en_uzun_uyku_dk=450), ONCEKI))
check("C10 eşit oranda öncelik uyanma (%50 = %50)",
      P.vurgu_metni(hafta(gece_uyanma=1.0, en_uzun_uyku_dk=450), ONCEKI)
      == "Gece uyanmaları %50 azaldı")
check("C11 önceki hafta yok → null",
      P.vurgu_metni(hafta(gece_uyanma=0.5), None) is None)
check("C12 bu hafta minimum veri altı → null",
      P.vurgu_metni(hafta(gece_uyanma=0.5, tam_gun_sayisi=3), ONCEKI) is None)
check("C13 alan null (uyanma kaydı yok) → o aday yok",
      P.vurgu_metni(hafta(gece_uyanma=None), ONCEKI) is None)
check("C14 kötüleşme → null",
      P.vurgu_metni(hafta(gece_uyanma=3.0, en_uzun_uyku_dk=200, ort_uyku_dk_gun=600),
                    ONCEKI) is None)

# =============================================================================
# D — minimum veri (endpoint)
# =============================================================================
B0 = yeni_bebek("Sifir")
r0 = haftalik(B0)
j0 = r0.json()
check("D1 0 tam gün: 200, paylasilabilir=false, mesaj '4 gün daha', ortalamalar null",
      r0.status_code == 200 and j0["paylasilabilir"] is False
      and j0["yetersiz_veri_mesaji"] == "Henüz yeterli veri yok, 4 gün daha kaydet"
      and j0["ort_uyku_dk_gun"] is None and j0["gece_uyanma"] is None
      and j0["gunduz_uyku_ort_adet"] is None and j0["tam_gun_sayisi"] == 0, j0)
check("D2 hafta = dün dahil son 7 gün, 7 gunluk, kayıtsız gün null",
      j0["hafta_bitis"] == DUN.isoformat()
      and j0["hafta_baslangic"] == (DUN - timedelta(days=6)).isoformat()
      and len(j0["gunluk"]) == 7
      and all(g["toplam_dk"] is None and g["gece_dk"] is None for g in j0["gunluk"]), j0["gunluk"])

B2 = yeni_bebek("Iki")
ekle(B2, tam_gun(DUN) + tam_gun(DUN - timedelta(days=1))
     + [("nap", (DUN - timedelta(days=3), "10:00"), (DUN - timedelta(days=3), "11:00"))])
j2 = haftalik(B2).json()
check("D3 2 tam gün + 1 yarım: paylasilabilir=false, '2 gün daha', ortalama tam günlerden",
      j2["paylasilabilir"] is False
      and j2["yetersiz_veri_mesaji"] == "Henüz yeterli veri yok, 2 gün daha kaydet"
      and j2["tam_gun_sayisi"] == 2 and j2["kayitli_gun_sayisi"] == 3
      and j2["ort_uyku_dk_gun"] == 720 and j2["vurgu_metni"] is None, j2)

B4 = yeni_bebek("Dort")
ekle(B4, [x for i in range(4) for x in tam_gun(DUN - timedelta(days=i))])
j4 = haftalik(B4).json()
check("D4 4 tam gün: paylasilabilir=true, mesaj null, ort 720, gündüz adet 1,0",
      j4["paylasilabilir"] is True and j4["yetersiz_veri_mesaji"] is None
      and j4["ort_uyku_dk_gun"] == 720 and j4["gunduz_uyku_ort_adet"] == 1.0
      and j4["en_uzun_uyku_dk"] == 630 and j4["haric_tutulan"] == [], j4)
check("D5 önceki hafta verisi yok → onceki_hafta null, vurgu null",
      j4["onceki_hafta"] is None and j4["vurgu_metni"] is None, j4)

# =============================================================================
# E — prod vakası (yalnız saatler): 4 kayıt, kart 2 Kas
# =============================================================================
BE = yeni_bebek("Vaka")
E31, E01, E02 = date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)
ekle(BE, [("wake", (E31, "07:00"), (E31, "07:00")),
          ("sleep", (E31, "11:30"), (E31, "13:30")),
          ("sleep", (E31, "16:10"), (E31, "19:49")),
          ("sleep", (E01, "23:20"), (E02, "13:03"))])
SAAT_E = datetime(2026, 11, 2, 18, 0, tzinfo=TR)
with saat_sabitle(SAAT_E):
    je = client.get(f"/api/v1/paylasim/haftalik?baby_id={BE}&bitis=2026-11-02", headers=H).json()
    je_vars = client.get(f"/api/v1/paylasim/haftalik?baby_id={BE}", headers=H).json()
check("E1 bitis=11-02: paylasilabilir=false, tam gün 0, ortalamalar null",
      je["paylasilabilir"] is False and je["tam_gun_sayisi"] == 0
      and je["ort_uyku_dk_gun"] is None and je["gece_uyanma"] is None, je)
check("E2 r3 (23:20→13:03, 823 dk) haric_tutulan'da supheli_uzun; gece_dk null",
      len(je["haric_tutulan"]) == 1 and je["haric_tutulan"][0]["sure_dk"] == 823
      and je["haric_tutulan"][0]["sebep"] == "supheli_uzun"
      and next(g for g in je["gunluk"] if g["tarih"] == "2026-11-02")["gece_dk"] is None, je)
check("E3 en uzun artık 219 (r2), 13 sa 43 dk değil",
      je["en_uzun_uyku_dk"] == 219, je["en_uzun_uyku_dk"])
check("E4 varsayılan (dün=11-01): r3 pencerede değil, 1 kayıtlı gün",
      je_vars["hafta_bitis"] == "2026-11-01" and je_vars["haric_tutulan"] == []
      and je_vars["kayitli_gun_sayisi"] == 1 and je_vars["paylasilabilir"] is False, je_vars)

# =============================================================================
# F — gece_uyanma + önceki hafta + vurgu uçtan uca
# =============================================================================
BF = yeni_bebek("Uyanma")
kay = []
for i in range(14):                                     # DUN-13 … DUN: 14 tam gün
    g = DUN - timedelta(days=i)
    kay += tam_gun(g)
    if i >= 7:                                          # önceki hafta: gece 2 uyanma
        kay += [("night_wake", (g, "01:00"), (g, "01:10")),
                ("night_wake", (g - timedelta(days=1), "23:00"), (g - timedelta(days=1), "23:05"))]
    elif i % 2 == 0:                                    # bu hafta: 4 gecede 1 uyanma
        kay += [("night_wake", (g, "02:00"), (g, "02:05"))]
ekle(BF, kay)
jf = haftalik(BF).json()
check("F1 bu hafta uyanma = 4/7 → 0,6; önceki 2,0 (23:00 uyanmaları kendi gecesinde)",
      jf["gece_uyanma"] == 0.6 and (jf["onceki_hafta"] or {}).get("gece_uyanma") == 2.0, jf)
check("F2 vurgu: 'Gece uyanmaları %70 azaldı'",
      jf["vurgu_metni"] == "Gece uyanmaları %70 azaldı", jf["vurgu_metni"])
check("F3 night_wake hiç yoksa gece_uyanma null (D4 bebeği)", j4["gece_uyanma"] is None)

# =============================================================================
# G — egitim_gunu
# =============================================================================
check("G1 eğitim yok → egitim_gunu null", j4["egitim_gunu"] is None)
BG = yeni_bebek("Egitim", training_started_at=BUGUN - timedelta(days=4))
db = SessionLocal()
try:
    b = db.get(Baby, BG)
    db.add(SleepPlan(user_id=b.user_id, baby_id=BG, plan_date=BUGUN,
                     content={"type": "egitim_plani", "plan_secimi": {"gunler": 13}}))
    db.commit()
finally:
    db.close()
check("G2 eğitim 5. günde (devam) → egitim_gunu 5", haftalik(BG).json()["egitim_gunu"] == 5)
db = SessionLocal()
try:
    db.get(Baby, BG).training_started_at = BUGUN - timedelta(days=14)    # gün 15 → bekliyor
    db.commit()
finally:
    db.close()
check("G3 gün ≥ 13, değerlendirme bekliyor → egitim_gunu null",
      haftalik(BG).json()["egitim_gunu"] is None)

# =============================================================================
# H — mezuniyet
# =============================================================================
def degerlendirme(bebek, sonuc: str, gun: date, bas: date) -> None:
    db = SessionLocal()
    try:
        b = db.get(Baby, bebek)
        db.add(EgitimDegerlendirme(user_id=b.user_id, baby_id=bebek, gun=gun, deneme_no=1,
                                   egitim_baslangic=bas, egitim_gunu=(gun - bas).days + 1,
                                   kendi_donuyor=sonuc == "mezun", yatir_cik=True,
                                   sonuc=sonuc))
        db.commit()
    finally:
        db.close()


BAS = date(2026, 10, 15)
MEZ = BAS + timedelta(days=12)                          # 13. gün = 10-27
BH0 = yeni_bebek("M0", training_started_at=BAS, training_completed_at=MEZ)
check("H1 yalnız training_completed_at (değerlendirme yok) → 404",
      mezuniyet(BH0).status_code == 404, mezuniyet(BH0).text)
degerlendirme(BH0, "devam", MEZ, BAS)
check("H2 'devam' değerlendirmesi → 404", mezuniyet(BH0).status_code == 404)

# H3: eğitim öncesi 3 gece (bas−2 … bas) 3 uyanma/gece, en uzun 240; son 3 gece 0 uyanma, 600
BH = yeni_bebek("M1", training_started_at=BAS, training_completed_at=MEZ, night_wakes=4)
kay = []
for g in (BAS - timedelta(days=2), BAS - timedelta(days=1), BAS):
    kay += [("sleep", (g - timedelta(days=1), "20:00"), (g - timedelta(days=1), "23:59")),
            ("night_wake", (g, "01:00"), (g, "01:20")),
            ("night_wake", (g, "03:00"), (g, "03:10")),
            ("night_wake", (g - timedelta(days=1), "23:59"), (g, "00:10")),
            ("sleep", (g, "00:10"), (g, "04:10"))]               # en uzun 240
for g in (MEZ - timedelta(days=2), MEZ - timedelta(days=1), MEZ):
    kay += [("sleep", (g - timedelta(days=1), "20:00"), (g, "06:00"))]   # 600, uyanma yok
ekle(BH, kay)
degerlendirme(BH, "mezun", MEZ, BAS)
rh = mezuniyet(BH)
jh = rh.json()
check("H3 mezun → 200; tamamlanma 10-27; once egitim_oncesi (3,0 / 240), simdi son_3_gun (0,0 / 600)",
      rh.status_code == 200 and jh["tamamlanma_tarihi"] == MEZ.isoformat()
      and jh["once"]["kaynak"] == "egitim_oncesi" and jh["once"]["gece_uyanma"] == 3.0
      and jh["once"]["en_uzun_uyku_dk"] == 240 and jh["simdi"]["kaynak"] == "son_3_gun"
      and jh["simdi"]["gece_uyanma"] == 0.0 and jh["simdi"]["en_uzun_uyku_dk"] == 600, jh)
check("H4 uykuya_dalma alanı yok",
      "uykuya_dalma_dk" not in jh["once"] and "uykuya_dalma_dk" not in jh["simdi"], jh)

# H5: eğitim öncesi kayıt yok → 1–2. gün (bas+1, bas+2 sabahları)
BH2 = yeni_bebek("M2", training_started_at=BAS)
kay = []
for g in (BAS + timedelta(days=1), BAS + timedelta(days=2)):
    kay += [("sleep", (g - timedelta(days=1), "20:00"), (g, "02:00")),
            ("night_wake", (g, "02:00"), (g, "02:30")),
            ("sleep", (g, "02:30"), (g, "06:30"))]
for g in (MEZ - timedelta(days=1), MEZ):
    kay += [("sleep", (g - timedelta(days=1), "20:00"), (g, "06:30"))]
ekle(BH2, kay)
degerlendirme(BH2, "mezun", MEZ, BAS)
jh2 = mezuniyet(BH2).json()
check("H5 öncesi yok → once egitim_1_2_gun (1,0 / 360); simdi 2 gece yeterli (0,0 / 630)",
      jh2["once"]["kaynak"] == "egitim_1_2_gun" and jh2["once"]["gece_uyanma"] == 1.0
      and jh2["once"]["en_uzun_uyku_dk"] == 360 and jh2["simdi"]["en_uzun_uyku_dk"] == 630
      and jh2["simdi"]["gece_uyanma"] == 0.0, jh2)

# H6: once penceresi yok, uyanma kaydı son 3 gecede var → uyanma beyandan
BH3 = yeni_bebek("M3", training_started_at=BAS, night_wakes=3)
kay = []
for g in (MEZ - timedelta(days=2), MEZ - timedelta(days=1), MEZ):
    kay += [("sleep", (g - timedelta(days=1), "20:00"), (g, "06:30")),
            ("night_wake", (g, "03:00"), (g, "03:05"))]
ekle(BH3, kay)
degerlendirme(BH3, "mezun", MEZ, BAS)
jh3 = mezuniyet(BH3).json()
check("H6 once penceresi yetersiz → gece_uyanma 'beyan' 3,0, en uzun null; simdi uyanma 1,0",
      jh3["once"]["kaynak"] == "beyan" and jh3["once"]["gece_uyanma"] == 3.0
      and jh3["once"]["gece_uyanma_kaynak"] == "beyan"
      and jh3["once"]["en_uzun_uyku_dk"] is None and jh3["simdi"]["en_uzun_uyku_dk"] is None
      and jh3["simdi"]["gece_uyanma"] == 1.0, jh3)

# H7: simdi penceresinde 1 tam gece → tüm metrikler null
BH4 = yeni_bebek("M4", training_started_at=BAS, night_wakes=2)
kay = []
for g in (BAS - timedelta(days=1), BAS):
    kay += [("sleep", (g - timedelta(days=1), "20:00"), (g, "06:30"))]
kay += [("sleep", (MEZ - timedelta(days=1), "20:00"), (MEZ, "06:30"))]
ekle(BH4, kay)
degerlendirme(BH4, "mezun", MEZ, BAS)
jh4 = mezuniyet(BH4).json()
check("H7 simdi < 2 tam gece → once/simdi metrikleri null, kaynaklar null",
      jh4["simdi"]["kaynak"] is None and jh4["simdi"]["en_uzun_uyku_dk"] is None
      and jh4["once"]["en_uzun_uyku_dk"] is None and jh4["once"]["gece_uyanma"] is None
      and jh4["once"]["kaynak"] is None, jh4)

# H8: yeniden başlatılmış eğitim — eski denemenin mezun satırı sayılmaz
db = SessionLocal()
try:
    db.get(Baby, BH4).training_started_at = BUGUN
    db.commit()
finally:
    db.close()
check("H8 yeni deneme başladı → eski 'mezun' satırı yok sayılır, 404",
      mezuniyet(BH4).status_code == 404)

# =============================================================================
# I — sahiplik / doğrulama
# =============================================================================
check("I1 başkasının bebeği → haftalik 404", haftalik(B4, headers=H_BASKA).status_code == 404)
check("I2 başkasının bebeği → mezuniyet 404", mezuniyet(BH, headers=H_BASKA).status_code == 404)
with saat_sabitle(SAAT):
    check("I3 oturumsuz → 401",
          client.get(f"/api/v1/paylasim/haftalik?baby_id={B4}").status_code == 401
          and client.get(f"/api/v1/paylasim/mezuniyet?baby_id={B4}").status_code == 401)
check("I4 gelecekteki bitis → 422", haftalik(B4, bitis=BUGUN + timedelta(days=1)).status_code == 422)
check("I5 olmayan bebek → 404", haftalik(uuid.uuid4()).status_code == 404)

# =============================================================================
# J — gece uyanması gün ataması
# =============================================================================
J = date(2026, 10, 25)
rows_j = [L("sleep", tr(J - timedelta(days=1), "20:00"), tr(J, "06:30")),
          L("night_wake", tr(J - timedelta(days=1), "23:30"), tr(J - timedelta(days=1), "23:45")),
          L("night_wake", tr(J, "03:00"), tr(J, "03:10")),
          L("night_wake", tr(J, "18:00"), tr(J, "18:05")),           # 19:00 öncesi: aynı gün
          L("night_wake", tr(J, "21:00"), tr(J, "21:10"))]           # bu akşam: yarının
kj = pa.gun_kayitlari(rows_j, J, pa.DEFAULT_WAKE_MIN, tz_offset_min=pa.TZ_OFFSET_MIN)
saat_j = sorted(pa._fmt(w["bas_dk"]) for w in kj["gece_uyanmalari"])
check("J1 J günü: dün 23:30 + 03:00 + 18:00 (bu akşam 21:00 yok)",
      saat_j == ["03:00", "18:00", "23:30"], saat_j)
w2330 = next(w for w in kj["gece_uyanmalari"] if pa._fmt(w["bas_dk"]) == "23:30")
check("J2 23:30 uyanması J ekseninde (bas_gun=J, bas_dk=-30), ilk sırada",
      w2330["bas_gun"] == J and w2330["bas_dk"] == -30
      and kj["gece_uyanmalari"][0] is w2330, (w2330["bas_gun"], w2330["bas_dk"]))
kj_dun = pa.gun_kayitlari(rows_j, J - timedelta(days=1), pa.DEFAULT_WAKE_MIN,
                          tz_offset_min=pa.TZ_OFFSET_MIN)
kj_yarin = pa.gun_kayitlari(rows_j, J + timedelta(days=1), pa.DEFAULT_WAKE_MIN,
                            tz_offset_min=pa.TZ_OFFSET_MIN)
check("J3 dünün listesinde 23:30 YOK; yarının listesinde 21:00 VAR",
      kj_dun["gece_uyanmalari"] == []
      and [pa._fmt(w["bas_dk"]) for w in kj_yarin["gece_uyanmalari"]] == ["21:00"])
sab = pa.sabah_uyanisi(kj, pa.DEFAULT_WAKE_MIN)
check("J4 gece_bolunmeleri saat sırası 23:30, 03:00, 18:00; uyanma toplamı 30 dk",
      [b["saat"] for b in sab["gece_bolunmeleri"]] == ["23:30", "03:00", "18:00"]
      and sab["gece_uykusu"]["uyanma_sayisi"] == 3
      and sab["gece_uykusu"]["uyanma_toplam_dk"] == 30, sab["gece_bolunmeleri"])

BJ = yeni_bebek("GunAtama")
JD = BUGUN - timedelta(days=2)                         # 10-31
ekle(BJ, [("sleep", (JD - timedelta(days=1), "20:00"), (JD, "06:30")),
          ("night_wake", (JD - timedelta(days=1), "22:15"), (JD - timedelta(days=1), "22:25")),
          ("night_wake", (JD, "02:00"), (JD, "02:05"))])
with saat_sabitle(SAAT):
    tl = client.get(f"/api/v1/logs/timeline?baby_id={BJ}&days=7", headers=H).json()
    ws = client.get(f"/api/v1/logs/weekly-summary?baby_id={BJ}", headers=H).json()
tl_g = {g["tarih"]: g["gece_uyanma"] for g in tl["gunler"]}
ws_g = {d["date"]: d["night_wakes"] for d in ws["days"]}
check("J5 timeline: 22:15 ve 02:00 uyanmaları gecenin sabahında (10-31: 2, 10-30: 0)",
      tl_g.get(JD.isoformat()) == 2 and tl_g.get((JD - timedelta(days=1)).isoformat()) == 0, tl_g)
check("J6 weekly-summary aynı günü sayar", ws_g == tl_g, (ws_g, tl_g))

# =============================================================================
# K — timeline bitis_bilinmiyor / sayima_girmez
# =============================================================================
BK = yeni_bebek("Timeline")
KD = BUGUN - timedelta(days=1)
ekle(BK, [("nap", (KD, "10:00"), (KD, "11:00")),
          ("nap", (KD, "13:00"), (KD, "14:30"), "k16_1"),
          ("nap", (BUGUN, "09:30"), None)])
with saat_sabitle(SAAT):
    tk = client.get(f"/api/v1/logs/timeline?baby_id={BK}&days=2", headers=H).json()
otur = {o["baslangic"][11:16]: o for g in tk["gunler"] for o in g["oturumlar"]}
# baslangic UTC ISO: 10:00 TR = 07:00Z, 13:00 TR = 10:00Z, 09:30 TR = 06:30Z
n, k16, ac = otur.get("07:00"), otur.get("10:00"), otur.get("06:30")
check("K1 normal oturum: bitis_bilinmiyor=false, sayima_girmez=false",
      n and n["bitis_bilinmiyor"] is False and n["sayima_girmez"] is False, n)
check("K2 k16_1 kapanışlı: bitis dolu ama bitis_bilinmiyor=true, sayima_girmez=true, eski alan korunur",
      k16 and k16["bitis"] is not None and k16["bitis_bilinmiyor"] is True
      and k16["sayima_girmez"] is True and k16["otomatik_kapatildi"] is True
      and k16["sure_dk"] is None, k16)
check("K3 açık oturum: sayima_girmez=true, bitis_bilinmiyor=false",
      ac and ac["sayima_girmez"] is True and ac["bitis_bilinmiyor"] is False, ac)
check("K4 gunduz_dk yalnız normal oturum (60)",
      next(g for g in tk["gunler"] if g["tarih"] == KD.isoformat())["gunduz_dk"] == 60)

# =============================================================================
# L — baby_id opsiyonel: aktif bebek (en yeni bebek, eğitim videolarıyla aynı)
# =============================================================================
H_L = kayit_ol("aktif-bebek@example.com")
with saat_sabitle(SAAT):
    r_yok = client.get("/api/v1/paylasim/haftalik", headers=H_L)
    m_yok = client.get("/api/v1/paylasim/mezuniyet", headers=H_L)
check("L1 bebeği olmayan kullanıcı, parametresiz → 404 (ikisi de)",
      r_yok.status_code == 404 and m_yok.status_code == 404, (r_yok.text, m_yok.text))

ESKI = yeni_bebek("Eski", headers=H_L)
# SQLite created_at saniye çözünürlüğünde: iki bebek aynı saniyede oluşursa sıra
# belirsiz kalır (Postgres mikrosaniye tutar). Eski bebeği açıkça geriye al.
_db = SessionLocal()
try:
    _b = _db.get(Baby, ESKI)
    _b.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _db.commit()
finally:
    _db.close()
ekle(ESKI, [x for i in range(4) for x in tam_gun(DUN - timedelta(days=i))])
YENI = yeni_bebek("Yeni", headers=H_L, training_started_at=BAS)
ekle(YENI, [x for i in range(5) for x in tam_gun(DUN - timedelta(days=i), nap=("12:00", "14:00"))])
with saat_sabitle(SAAT):
    jl = client.get("/api/v1/paylasim/haftalik", headers=H_L).json()
    jl_eski = client.get(f"/api/v1/paylasim/haftalik?baby_id={ESKI}", headers=H_L).json()
    ml = client.get("/api/v1/paylasim/mezuniyet", headers=H_L)
check("L2 parametresiz haftalik → en yeni bebek (Yeni: 5 tam gün, ort 750)",
      jl.get("bebek_adi") == "Yeni" and jl.get("tam_gun_sayisi") == 5
      and jl.get("ort_uyku_dk_gun") == 750, jl)
check("L3 baby_id verilince o bebek (Eski: 4 tam gün)",
      jl_eski.get("bebek_adi") == "Eski" and jl_eski.get("tam_gun_sayisi") == 4, jl_eski)
check("L4 parametresiz mezuniyet, aktif bebekte mezun kararı yok → 404",
      ml.status_code == 404, ml.text)
degerlendirme(YENI, "mezun", MEZ, BAS)
with saat_sabitle(SAAT):
    ml2 = client.get("/api/v1/paylasim/mezuniyet", headers=H_L)
check("L5 aktif bebek mezun → parametresiz mezuniyet 200, Yeni",
      ml2.status_code == 200 and ml2.json()["bebek_adi"] == "Yeni", ml2.text)
with saat_sabitle(SAAT):
    r_bas = client.get(f"/api/v1/paylasim/haftalik?baby_id={ESKI}", headers=H)
    m_bas = client.get(f"/api/v1/paylasim/mezuniyet?baby_id={YENI}", headers=H)
check("L6 baby_id başkasınınsa yine 404 (sahiplik)",
      r_bas.status_code == 404 and m_bas.status_code == 404)
with saat_sabitle(SAAT):
    ev = client.get("/api/v1/education/videos", headers=H_L)
check("L7 eğitim videoları ucu (aynı kural) hâlâ 200", ev.status_code == 200, ev.text[:200])

# =============================================================================
gecen = sum(1 for _, ok, _ in results if ok)
for ad, ok, detay in results:
    print(("✅" if ok else "❌"), ad, "" if ok else f"\n     → {detay[:600]}")
print(f"\n{gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
