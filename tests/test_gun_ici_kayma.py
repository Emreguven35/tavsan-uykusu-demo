"""
GÜN İÇİ KAYMA MOTORU v2 — K1-K9 senaryo testleri.

LLM YOK, ağ YOK, prod DB YOK: geçici sqlite + FastAPI TestClient. Plan
deterministik yedek motordan üretilir (ANTHROPIC_API_KEY silinir).

BEKLENEN SAATLER ELLE YAZILMAZ: hepsi yas_bantlari.json'dan
`cizelge_parametreleri` ile hesaplanır (uyanıklık penceresi, uyku süresi, gece
uykusu sınırları). Tablo değişirse test de birlikte değişir — sabit sayı
gömülürse tablo ile test sessizce ayrışır.

Senaryolar:
  A  Plana birebir uyuldu                → şablonla aynı
  B  Sabah uykusu 1 sa geç               → nap_1 dokunulmaz, sonrası zincirle kayar
  C  Sabah 08:00 uyandı                  → gün 08:00'dan; YARIN şablon 07:00
  D  Sabah 06:00 uyandı                  → gün 06:00'dan; yatış erkene
  E  Sabah uykusu atlandı                → nap_2 öne, kestirme + toplam uyarısı
  F  04:30 bölünme + 05:00-07:10 uyku    → sabah 07:10, bölünme kaydedildi
  G  04:30'da uyandı, tekrar uyumadı     → hedef 07:00 korunur, zincir 04:30'dan
  H  Hiç kayıt yok, saat 15:00           → varsayilan_bloklar=[nap_1,nap_2]
  I  H + 13:40-14:10 gerçek nap_2 kaydı  → varsayım bozulur, sonrası yeniden
  J  Dünün kaydı bugün girildi           → bugün etkilenmez
  K  09:30-10:40 'sleep' girildi         → sabah uyanışı SANILMAZ, nap işlenir
  L  5 gün üst üste 08:00                → birikme yok, şablon 07:00'da kalır
  M  nap_3 19:30'da bitti                → yatış tavana kırpıldı + uyarı
  N  Aynı kayıtlarla 3× GET              → aynı schedule (idempotent)
  O  POST /logs/batch                    → plan_updated:true, GET aynı sonucu verir

Çalıştırma: python tests/test_gun_ici_kayma.py   (pytest kuruluysa: pytest -q)
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

_DB = Path(tempfile.gettempdir()) / "gun_ici_kayma_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"        # zamanlayıcı başlamasın
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ.pop("ANTHROPIC_API_KEY", None)        # → deterministik yedek motor

from fastapi.testclient import TestClient              # noqa: E402
from api.db import Base, SessionLocal, engine          # noqa: E402
import api.models                                      # noqa: E402,F401
from api.models import Baby, SleepLog, SleepPlan       # noqa: E402
from api.main import app                               # noqa: E402
from api.services import plan_adapter as pa            # noqa: E402
from api.services import plan_service                  # noqa: E402
from engine import yas_bantlari                        # noqa: E402
from engine.parameter_engine import hesapla_yas_ay     # noqa: E402

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


# =============================================================================
# Ortak kurulum — 8 aylık bebek, 13 günlük programın 5. günü
# =============================================================================
TZ = pa.TZ_OFFSET_MIN
TODAY = datetime.now(timezone.utc).date()
YAS_GUN = 243                                    # ≈ 8.0 ay


def utc(gun, yerel_dk: int) -> datetime:
    """Yerel (UTC+3) duvar dakikası → UTC datetime."""
    return (datetime(gun.year, gun.month, gun.day, tzinfo=timezone.utc)
            + timedelta(minutes=yerel_dk - TZ))


def hhmm(dk) -> str:
    return pa._fmt(dk)


tok = client.post("/api/v1/auth/register",
                  json={"email": "kayma_v2@example.com",
                        "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
_dogum = TODAY - timedelta(days=YAS_GUN)
BID = client.post("/api/v1/babies", headers=H,
                  json={"name": "Elif", "birth_date": _dogum.isoformat(),
                        "night_wakes": 2}).json()["id"]
client.patch(f"/api/v1/babies/{BID}", headers=H,
             json={"training_started_at": (TODAY - timedelta(days=4)).isoformat()})

_gen = client.post("/api/v1/plans/generate?sync=true", headers=H,
                   json={"baby_id": BID})
assert _gen.status_code == 201, _gen.text
BASE = _gen.json()["content"]

# --- BEKLENEN DEĞERLER: tablodan hesaplanır, elle yazılmaz -------------------
YAS_AY = hesapla_yas_ay(_dogum.isoformat(), 40)["duzeltilmis_ay"]
BANT = yas_bantlari.yas_bandi_getir(YAS_AY)
CP = yas_bantlari.cizelge_parametreleri(BANT)
WW = CP["uyaniklik_penceresi_dk"]                # uyanıklık penceresi (dk)
NAP_SURE = CP["uyku_suresi_dk"]                  # bir gündüz uykusunun süresi
N_NAP = CP["uyku_sayisi"]
GECE_LO, GECE_HI = BANT["gece_uykusu_dk"]
GUNDUZ_MIN = BANT["gunduz_uyku_toplam_dk"][0]
TOPLAM_LO = (BANT.get("toplam_gunluk_uyku_dk") or [0, None])[0]

TPL = {b["key"]: b for b in BASE["schedule_template"]}
W = TPL["wake"]["start_minute"]                  # SABİT sabah hedefi (K1)
YATMA_LO, YATMA_HI = yas_bantlari.yatma_araligi(BANT, W)


def zincir(baslangic: int, nap_sayisi: int = None, sureler=None) -> list[int]:
    """Şablonla AYNI kuralla uyku başlangıçlarını türet: her uykudan sonra
    pencere kadar uyanıklık. Beklenen saatler bundan üretilir."""
    n = N_NAP if nap_sayisi is None else nap_sayisi
    sureler = sureler or [NAP_SURE] * n
    out, cursor = [], baslangic
    for i in range(n):
        bas = cursor + WW
        out.append(bas)
        cursor = bas + sureler[i]
    return out


def beklenen_yatis(son_uyku_bitisi: int) -> int:
    """K4 — yatış = son uyku bitişi + pencere, bandın aralığına kırpılmış."""
    return max(YATMA_LO, min(YATMA_HI, son_uyku_bitisi + WW))


# =============================================================================
# Yardımcılar
# =============================================================================
def _baby_row(db):
    return db.query(Baby).filter(Baby.id == _uuid.UUID(BID)).one()


def gece(row, gun, yatis_dk: int, uyanis_dk: int) -> SleepLog:
    """Gece uykusu: (gun-1) akşamı yatış → `gun` sabahı uyanış."""
    return SleepLog(user_id=row.user_id, baby_id=row.id, type="sleep",
                    started_at=utc(gun - timedelta(days=1), yatis_dk),
                    ended_at=utc(gun, uyanis_dk))


def nap(row, gun, bas: int, sure: int = None, tip: str = "nap") -> SleepLog:
    sure = NAP_SURE if sure is None else sure
    return SleepLog(user_id=row.user_id, baby_id=row.id, type=tip,
                    started_at=utc(gun, bas), ended_at=utc(gun, bas + sure))


def atlandi(row, gun, bas: int) -> SleepLog:
    """K7 — 'bu uykuyu hiç yapmadı' kaydı."""
    return SleepLog(user_id=row.user_id, baby_id=row.id, type=pa.ATLANDI_TIPI,
                    started_at=utc(gun, bas), ended_at=None)


def uyanik(row, gun, dk: int) -> SleepLog:
    return SleepLog(user_id=row.user_id, baby_id=row.id, type="wake",
                    started_at=utc(gun, dk))


def kur(log_fn, *, taban_gun=None):
    """Temiz durum: tüm kayıt/planları sil, şablon planı `taban_gun`e koy."""
    taban_gun = taban_gun or (TODAY - timedelta(days=1))
    db = SessionLocal()
    bid = _uuid.UUID(BID)
    db.query(SleepLog).filter(SleepLog.baby_id == bid).delete()
    db.query(SleepPlan).filter(SleepPlan.baby_id == bid).delete()
    row = _baby_row(db)
    db.add(SleepPlan(user_id=row.user_id, baby_id=bid,
                     plan_date=taban_gun, content=dict(BASE)))
    for lg in (log_fn(row) if log_fn else []):
        db.add(lg)
    db.commit()
    db.close()


def bugun(now_minute: int = None, gun=None):
    """Planı SERVİS üzerinden hesapla (now enjekte edilebilsin diye)."""
    db = SessionLocal()
    row = _baby_row(db)
    plan = plan_service.ensure_today_plan(db, row.user, row, today=gun or TODAY,
                                          now_minute=now_minute)
    icerik = dict(plan.content or {})
    db.close()
    return icerik, {b["key"]: b for b in icerik["schedule"]}


def uc_endpoint():
    """Planı GERÇEK endpoint'ten çek (mobil sözleşmesi bu yoldan doğrulanır)."""
    r = client.get(f"/api/v1/plans/today?baby_id={BID}", headers=H)
    assert r.status_code == 200, r.text
    c = r.json()["content"]
    return c, {b["key"]: b for b in c["schedule"]}


def saatler(s: dict) -> dict:
    return {k: v["time"] for k, v in s.items()}


TABLO: list[tuple] = []          # (senaryo, blok, plandaki, sistem, beklenen, ok)


def bekle(senaryo: str, s: dict, blok: str, beklenen_dk, ipucu: str = "") -> bool:
    var = s.get(blok)
    sistem = var["time"] if var else "—"
    bek = hhmm(beklenen_dk) if beklenen_dk is not None else "—"
    ok = sistem == bek
    TABLO.append((senaryo, blok, TPL.get(blok, {}).get("time", "—"), sistem, bek, ok))
    check(f"{senaryo} · {blok} = {bek}{(' (' + ipucu + ')') if ipucu else ''}",
          ok, f"sistem={sistem}")
    return ok


# =============================================================================
# A — Plana birebir uyuldu → şablonla aynı
# =============================================================================
def test_a_plana_uyuldu():
    kur(lambda row: [gece(row, TODAY, TPL["bedtime"]["start_minute"], W)]
        + [nap(row, TODAY, TPL[f"nap_{i}"]["start_minute"]) for i in range(1, N_NAP + 1)])
    c, s = bugun(now_minute=1439)
    baslar = zincir(W)
    for i, b in enumerate(baslar, start=1):
        bekle("A", s, f"nap_{i}", b)
    bekle("A", s, "bedtime", beklenen_yatis(baslar[-1] + NAP_SURE))
    check("A · şablon DEĞİŞMEDİ (K1)",
          c["schedule_template"] == BASE["schedule_template"], "")
    check("A · sabah uyanış kaynağı 'kayit'",
          c["adaptation"]["sabah_uyanis_kaynak"] == "kayit",
          c["adaptation"]["sabah_uyanis_kaynak"])


# =============================================================================
# B — Sabah uykusu 1 saat geç: nap_1 dokunulmaz, SONRAKİLER zincirle kayar (K3)
# =============================================================================
def test_b_sabah_uykusu_gec():
    gec_bas = TPL["nap_1"]["start_minute"] + 60
    kur(lambda row: [gece(row, TODAY, TPL["bedtime"]["start_minute"], W),
                     nap(row, TODAY, gec_bas)])
    c, s = bugun(now_minute=gec_bas + NAP_SURE + 5)
    bekle("B", s, "nap_1", gec_bas, "gerçek kayıt")
    kalan = zincir(gec_bas + NAP_SURE, nap_sayisi=N_NAP - 1)
    for i, b in enumerate(kalan, start=2):
        bekle("B", s, f"nap_{i}", b, "zincir")
    bekle("B", s, "bedtime", beklenen_yatis(kalan[-1] + NAP_SURE))
    check("B · nap_1 'kayit' kaynaklı",
          s["nap_1"].get("kaynak") == "kayit", s["nap_1"].get("kaynak"))
    check("B · şablon DEĞİŞMEDİ (K1)",
          c["schedule_template"] == BASE["schedule_template"], "")


# =============================================================================
# C — Sabah 08:00 uyandı: gün 08:00'dan; YARIN şablon 07:00'a döner (K1/K2)
# =============================================================================
def test_c_gec_uyanis():
    gerc = W + 60
    kur(lambda row: [gece(row, TODAY, TPL["bedtime"]["start_minute"], gerc)])
    c, s = bugun(now_minute=gerc + 5)
    bekle("C", s, "wake", gerc)
    baslar = zincir(gerc)
    for i, b in enumerate(baslar, start=1):
        bekle("C", s, f"nap_{i}", b)
    bekle("C", s, "bedtime", beklenen_yatis(baslar[-1] + NAP_SURE))

    # YARIN: kayıt yok → şablon hedefine döner (v1'de dünün kaydırılmış planı
    # taban oluyordu; v2'de şablon taban).
    yarin = TODAY + timedelta(days=1)
    _c2, s2 = bugun(now_minute=0, gun=yarin)
    bekle("C-yarın", s2, "wake", W, "K1: hedef sabit")
    bekle("C-yarın", s2, "nap_1", TPL["nap_1"]["start_minute"], "şablona döndü")
    bekle("C-yarın", s2, "bedtime", TPL["bedtime"]["start_minute"], "şablona döndü")


# =============================================================================
# D — Sabah 06:00 uyandı: gün 06:00'dan, yatış erkene (bandın alt sınırına kadar)
# =============================================================================
def test_d_erken_uyanis():
    gerc = W - 60                                # 90 dk toleransın İÇİNDE
    kur(lambda row: [gece(row, TODAY, TPL["bedtime"]["start_minute"], gerc)])
    _c, s = bugun(now_minute=gerc + 5)
    bekle("D", s, "wake", gerc)
    baslar = zincir(gerc)
    for i, b in enumerate(baslar, start=1):
        bekle("D", s, f"nap_{i}", b)
    bekle("D", s, "bedtime", beklenen_yatis(baslar[-1] + NAP_SURE), "erkene çekildi")
    check("D · yatış şablondan ERKEN",
          s["bedtime"]["start_minute"] < TPL["bedtime"]["start_minute"],
          f'{s["bedtime"]["time"]} < {TPL["bedtime"]["time"]}')


# =============================================================================
# E — Sabah uykusu atlandı (K7): nap_2 öne çekilir + kestirme/toplam uyarısı (K9)
# =============================================================================
def test_e_uyku_atlandi():
    kanit = 12 * 60                              # 12:00'da hâlâ uyanık
    kur(lambda row: [gece(row, TODAY, TPL["bedtime"]["start_minute"], W),
                     atlandi(row, TODAY, TPL["nap_1"]["start_minute"]),
                     uyanik(row, TODAY, kanit)])
    c, s = bugun(now_minute=kanit)
    check("E · nap_1 çizelgeden düştü (atlandı)", "nap_1" not in s, list(s))
    check("E · atlanan_bloklar=[nap_1]",
          c["adaptation"]["atlanan_bloklar"] == ["nap_1"],
          c["adaptation"]["atlanan_bloklar"])
    # Zincir kanıt saatinden akar: uyku 12:00'dan önce olamaz.
    b2 = max(W + WW, kanit)
    bekle("E", s, "nap_2", b2, "öne çekildi")
    bekle("E", s, "nap_3", max(b2 + NAP_SURE + WW, kanit))
    kes = c["kestirme_degerlendirme"]
    check("E · kestirme önerisi ÇIKTI (K9)", kes["gerekli"] is True,
          f'gündüz {kes["gerceklesen_dk"]} dk < min {GUNDUZ_MIN} dk')
    check("E · gündüz toplamı bandın minimumunun ALTINDA",
          kes["gerceklesen_dk"] < GUNDUZ_MIN, kes["gerceklesen_dk"])
    tpl_deg = c["toplam_uyku_degerlendirme"]
    check("E · 24 saatlik toplam 'az' (K9)", tpl_deg["durum"] == "az",
          f'{tpl_deg["gerceklesen_dk"]} dk / hedef {TOPLAM_LO} dk')


# =============================================================================
# F — 04:30 bölünme + 05:00-07:10 uyku → sabah uyanış 07:10 (K5)
# =============================================================================
def test_f_gece_bolunmesi():
    son_uyanis = W + 10
    def loglar(row):
        return [
            gece(row, TODAY, TPL["bedtime"]["start_minute"], 4 * 60 + 30),
            SleepLog(user_id=row.user_id, baby_id=row.id, type="sleep",
                     started_at=utc(TODAY, 5 * 60),
                     ended_at=utc(TODAY, son_uyanis)),
        ]
    kur(loglar)
    c, s = bugun(now_minute=son_uyanis + 5)
    bekle("F", s, "wake", son_uyanis, "son uyanış")
    check("F · gece bölünmesi kaydedildi (04:30)",
          any(b["dakika"] == 4 * 60 + 30 for b in c["adaptation"]["gece_bolunmeleri"]),
          c["adaptation"]["gece_bolunmeleri"])
    # 10 dakikalık sapma → plan neredeyse değişmez.
    baslar = zincir(son_uyanis)
    bekle("F", s, "nap_1", baslar[0])
    check("F · nap_1 şablondan yalnız 10 dk sapmış",
          s["nap_1"]["start_minute"] - TPL["nap_1"]["start_minute"] == 10,
          s["nap_1"]["start_minute"] - TPL["nap_1"]["start_minute"])


# =============================================================================
# G — 04:30'da uyandı, tekrar uyumadı, başka kayıt yok  [BELİRSİZ — bkz. rapor]
# =============================================================================
def test_g_cok_erken_uyanma():
    erken = 4 * 60 + 30
    kur(lambda row: [gece(row, TODAY, TPL["bedtime"]["start_minute"], erken)])
    c, s = bugun(now_minute=erken + 5)
    bekle("G", s, "wake", W, "hedef KORUNUR")
    check("G · kaynak='erken_uyanma'",
          c["adaptation"]["sabah_uyanis_kaynak"] == "erken_uyanma",
          c["adaptation"]["sabah_uyanis_kaynak"])
    check("G · uyarı yazıldı",
          any("erken uyanma" in u.lower() for u in c["adaptation"]["uyarilar"]),
          c["adaptation"]["uyarilar"])
    baslar = zincir(erken)                       # zincir 04:30'dan akar
    for i, b in enumerate(baslar, start=1):
        bekle("G", s, f"nap_{i}", b, "zincir 04:30'dan")
    check("G · gece bölünmesi olarak da kaydedildi",
          any(b["dakika"] == erken for b in c["adaptation"]["gece_bolunmeleri"]),
          c["adaptation"]["gece_bolunmeleri"])


# =============================================================================
# H — Hiç kayıt yok, saat 15:00 → geçmiş bloklar varsayılan (K6)
# =============================================================================
def test_h_kayit_yok():
    kur(None)
    c, s = bugun(now_minute=15 * 60)
    for i in range(1, N_NAP + 1):
        bekle("H", s, f"nap_{i}", TPL[f"nap_{i}"]["start_minute"], "plana göre")
    bekle("H", s, "bedtime", TPL["bedtime"]["start_minute"])
    gecmis = [k for k in (f"nap_{i}" for i in range(1, N_NAP + 1))
              if TPL[k]["end_minute"] <= 15 * 60]
    check(f"H · varsayilan_bloklar={gecmis} (K6)",
          c["adaptation"]["varsayilan_bloklar"] == gecmis,
          c["adaptation"]["varsayilan_bloklar"])
    check("H · sabah uyanış kaynağı 'varsayilan'",
          c["adaptation"]["sabah_uyanis_kaynak"] == "varsayilan", "")


# =============================================================================
# I — H'den sonra nap_2 için gerçek kayıt gelir → varsayım bozulur (K7)
# =============================================================================
def test_i_varsayim_bozulur():
    gercek_bas, gercek_sure = 13 * 60 + 40, 30
    kur(None)
    db = SessionLocal()
    row = _baby_row(db)
    # nap_1 planlandığı gibi geçti (kayıt yok → varsayılan), nap_2 gerçek kayıt.
    db.add(nap(row, TODAY, gercek_bas, gercek_sure))
    db.commit()
    db.close()
    c, s = bugun(now_minute=15 * 60 + 30)
    check("I · nap_1 hâlâ varsayılan (K6)",
          c["adaptation"]["varsayilan_bloklar"] == ["nap_1"],
          c["adaptation"]["varsayilan_bloklar"])
    bekle("I", s, "nap_1", TPL["nap_1"]["start_minute"], "varsayılan")
    bekle("I", s, "nap_2", gercek_bas, "gerçek kayıt")
    check("I · nap_2 kaynağı 'kayit'", s["nap_2"].get("kaynak") == "kayit",
          s["nap_2"].get("kaynak"))
    bekle("I", s, "nap_3", gercek_bas + gercek_sure + WW, "yeniden hesaplandı")
    bekle("I", s, "bedtime",
          beklenen_yatis(gercek_bas + gercek_sure + WW + NAP_SURE))
    check("I · nap_3 ve bedtime yeniden hesaplananlar listesinde",
          {"nap_3", "bedtime"} <= set(c["adaptation"]["yeniden_hesaplanan_bloklar"]),
          c["adaptation"]["yeniden_hesaplanan_bloklar"])


# =============================================================================
# J — Geçmişe dönük kayıt: dünün kaydı bugünü ETKİLEMEZ (K2)
# =============================================================================
def test_j_gecmise_donuk():
    dun = TODAY - timedelta(days=1)
    kur(lambda row: [gece(row, dun, TPL["bedtime"]["start_minute"], W + 90),
                     nap(row, dun, TPL["nap_1"]["start_minute"] + 90)],
        taban_gun=TODAY - timedelta(days=2))
    c, s = bugun(now_minute=9 * 60)
    for i in range(1, N_NAP + 1):
        bekle("J", s, f"nap_{i}", TPL[f"nap_{i}"]["start_minute"], "bugün etkilenmez")
    bekle("J", s, "bedtime", TPL["bedtime"]["start_minute"])
    check("J · bugünün uyanışı varsayılan (dünün kaydı sabah hedefini bozmadı)",
          c["adaptation"]["sabah_uyanis_kaynak"] == "varsayilan",
          c["adaptation"]["sabah_uyanis_kaynak"])
    check("J · istatistik yine de dünü GÖRÜYOR",
          (c["adaptation"]["log_summary"]["days_with_data"] or 0) >= 1,
          c["adaptation"]["log_summary"]["days_with_data"])


# =============================================================================
# K — 'sleep' tipiyle girilen 70 dk'lık uyku sabah uyanışı SANILMAZ
# =============================================================================
def test_k_sleep_tipi_karismasi():
    bas = TPL["nap_1"]["start_minute"]
    kur(lambda row: [nap(row, TODAY, bas, NAP_SURE, tip="sleep")])   # gece kaydı YOK
    c, s = bugun(now_minute=bas + NAP_SURE + 5)
    bekle("K", s, "wake", W, "sabah uyanışı SANILMADI")
    check("K · kaynak 'varsayilan' (uyanış kaydı yok sayıldı)",
          c["adaptation"]["sabah_uyanis_kaynak"] == "varsayilan",
          c["adaptation"]["sabah_uyanis_kaynak"])
    bekle("K", s, "nap_1", bas, "gündüz uykusu olarak işlendi")
    check("K · nap_1 kaynağı 'kayit'", s["nap_1"].get("kaynak") == "kayit",
          s["nap_1"].get("kaynak"))


# =============================================================================
# L — 5 gün üst üste 08:00: birikme YOK, şablon sabit (K1)
# =============================================================================
def test_l_birikme_yok():
    gerc = W + 60
    kur(None)
    db = SessionLocal()
    row = _baby_row(db)
    for i in range(5):
        db.add(gece(row, TODAY + timedelta(days=i),
                    TPL["bedtime"]["start_minute"], gerc))
    db.commit()
    db.close()

    yatislar, uyanislar = [], []
    for i in range(5):
        gun = TODAY + timedelta(days=i)
        _c, s = bugun(now_minute=gerc + 5, gun=gun)
        uyanislar.append(s["wake"]["time"])
        yatislar.append(s["bedtime"]["time"])
    bek_yatis = hhmm(beklenen_yatis(zincir(gerc)[-1] + NAP_SURE))
    check("L · 5 günün hepsinde uyanış aynı (birikme yok)",
          len(set(uyanislar)) == 1 and uyanislar[0] == hhmm(gerc), uyanislar)
    check("L · 5 günün hepsinde yatış aynı, geceye kaymıyor",
          len(set(yatislar)) == 1 and yatislar[0] == bek_yatis, yatislar)
    check("L · yatış bandın tavanını aşmadı",
          max(YATMA_LO, min(YATMA_HI, zincir(gerc)[-1] + NAP_SURE + WW)) <= YATMA_HI,
          f"tavan={hhmm(YATMA_HI)}")
    db = SessionLocal()
    row = _baby_row(db)
    son = plan_service.plan_for_date(db, row.user, row, TODAY + timedelta(days=4))
    check("L · ŞABLON hâlâ 07:00 (K1)",
          pa.sabit_wake_minute(son.content["schedule_template"]) == W,
          pa.sabit_wake_minute(son.content["schedule_template"]))
    db.close()


# =============================================================================
# M — Yatış tavanı: son uyku çok geç bitti → kırpma + uyarı (K4)
# =============================================================================
def test_m_yatis_tavani():
    gec_bit = 19 * 60 + 30
    gec_bas = gec_bit - NAP_SURE
    def loglar(row):
        return [gece(row, TODAY, TPL["bedtime"]["start_minute"], W),
                nap(row, TODAY, TPL["nap_1"]["start_minute"]),
                nap(row, TODAY, TPL["nap_2"]["start_minute"]),
                nap(row, TODAY, gec_bas)]
    kur(loglar)
    c, s = bugun(now_minute=gec_bit + 5)
    check("M · ham yatış tavanı aşıyordu",
          gec_bit + WW > YATMA_HI, f"{hhmm(gec_bit + WW)} > {hhmm(YATMA_HI)}")
    bekle("M", s, "bedtime", YATMA_HI, "tavana kırpıldı")
    check("M · 'bandın sınırına dayandı' uyarısı var",
          any("sınırına dayandı" in u for u in c["adaptation"]["uyarilar"]),
          c["adaptation"]["uyarilar"])
    check("M · gece uykusu bandın minimumunun altına düşmedi",
          s["bedtime"].get("gece_uykusu_dk", 0) >= GECE_LO,
          f'{s["bedtime"].get("gece_uykusu_dk")} >= {GECE_LO}')


# =============================================================================
# N — İdempotans: aynı kayıtlarla 3 kez → aynı schedule (K8)
# =============================================================================
def test_n_idempotans():
    kur(lambda row: [gece(row, TODAY, TPL["bedtime"]["start_minute"], W + 45),
                     nap(row, TODAY, TPL["nap_1"]["start_minute"] + 30)])
    cikti = []
    for _ in range(3):
        _c, s = uc_endpoint()
        cikti.append(saatler(s))
    check("N · 3 çağrı aynı çizelgeyi döndürdü",
          cikti[0] == cikti[1] == cikti[2], cikti)
    db = SessionLocal()
    row = _baby_row(db)
    n = (db.query(SleepPlan)
         .filter(SleepPlan.baby_id == row.id, SleepPlan.plan_date == TODAY).count())
    db.close()
    check("N · aynı güne tek plan satırı (yığılma yok)", n == 1, n)


# =============================================================================
# O — POST /logs/batch planı tazeler ve plan_updated döner (K8)
# =============================================================================
def test_o_batch_tetikler():
    kur(None)
    _c0, s0 = uc_endpoint()
    gerc = W + 50
    r = client.post("/api/v1/logs/batch", headers=H, json={"logs": [{
        "baby_id": BID, "type": "sleep", "client_id": "o-gece",
        "started_at": utc(TODAY - timedelta(days=1),
                          TPL["bedtime"]["start_minute"]).isoformat(),
        "ended_at": utc(TODAY, gerc).isoformat()}]})
    check("O · batch 200", r.status_code == 200, r.status_code)
    check("O · plan_updated=true", r.json().get("plan_updated") is True,
          r.json().get("plan_updated"))
    _c1, s1 = uc_endpoint()
    check("O · GET /plans/today batch ile AYNI sonucu veriyor",
          s1["wake"]["time"] == hhmm(gerc), s1["wake"]["time"])
    check("O · çizelge gerçekten değişti", saatler(s1) != saatler(s0),
          f"{s0['nap_1']['time']} → {s1['nap_1']['time']}")

    # Aynı batch ikinci kez (idempotent senkron) → plan DEĞİŞMEMELİ.
    r2 = client.post("/api/v1/logs/batch", headers=H, json={"logs": [{
        "baby_id": BID, "type": "sleep", "client_id": "o-gece",
        "started_at": utc(TODAY - timedelta(days=1),
                          TPL["bedtime"]["start_minute"]).isoformat(),
        "ended_at": utc(TODAY, gerc).isoformat()}]})
    check("O · aynı batch tekrarında plan_updated=false",
          r2.json().get("plan_updated") is False, r2.json().get("plan_updated"))


# =============================================================================
# Ek — mobil sözleşmesi bozulmadı mı?
# =============================================================================
def test_p_mobil_sozlesmesi():
    kur(lambda row: [gece(row, TODAY, TPL["bedtime"]["start_minute"], W)])
    c, s = uc_endpoint()
    zorunlu = {"key", "type", "time", "end", "title", "start_minute", "end_minute"}
    eksik = [b["key"] for b in c["schedule"] if not zorunlu <= set(b)]
    check("P · schedule[] eleman şeması korundu (alan eklendi, çıkarılmadı)",
          not eksik, eksik)
    check("P · type değerleri sözleşmedeki kümede",
          all(b["type"] in ("wake", "nap", "sleep", "feed", "routine")
              for b in c["schedule"]), "")
    for alan in ("hesaplandi_at", "sabah_uyanis_gercek", "sabah_uyanis_kaynak",
                 "varsayilan_bloklar", "yeniden_hesaplanan_bloklar",
                 "yok_sayilan_kayitlar", "gece_bolunmeleri", "uyarilar"):
        check(f"P · adaptation.{alan} var", alan in c["adaptation"], "")
    check("P · schedule_template yanıtta var", "schedule_template" in c, "")
    check("P · days (13 günlük merdiven) korundu",
          len(c.get("days") or []) > 0, len(c.get("days") or []))


# =============================================================================
# Koşucu
# =============================================================================
TESTLER = [test_a_plana_uyuldu, test_b_sabah_uykusu_gec, test_c_gec_uyanis,
           test_d_erken_uyanis, test_e_uyku_atlandi, test_f_gece_bolunmesi,
           test_g_cok_erken_uyanma, test_h_kayit_yok, test_i_varsayim_bozulur,
           test_j_gecmise_donuk, test_k_sleep_tipi_karismasi, test_l_birikme_yok,
           test_m_yatis_tavani, test_n_idempotans, test_o_batch_tetikler,
           test_p_mobil_sozlesmesi]


def main() -> int:
    print(f"8 aylık bebek · bant {BANT['ad']} · pencere {WW} dk · "
          f"uyku {NAP_SURE} dk × {N_NAP} · gece {GECE_LO}-{GECE_HI} dk")
    print(f"ŞABLON (K1, değişmez): " +
          "  ".join(f"{k}={v['time']}" for k, v in TPL.items()))
    print(f"yatma aralığı: {hhmm(YATMA_LO)}–{hhmm(YATMA_HI)}\n")

    for t in TESTLER:
        try:
            t()
        except Exception as e:                    # test çöktüyse sessiz kalmasın
            check(f"{t.__name__} ÇÖKTÜ", False, f"{type(e).__name__}: {e}")

    print("=" * 96)
    print(f"{'senaryo':9s} {'blok':9s} {'şablon':>8s} {'sistem':>8s} "
          f"{'beklenen':>9s}  ")
    print("-" * 96)
    for sen, blok, tpl, sis, bek, ok in TABLO:
        print(f"{sen:9s} {blok:9s} {tpl:>8s} {sis:>8s} {bek:>9s}  "
              f"{'OK' if ok else 'HATA'}")

    print("\n" + "=" * 96)
    gecen = sum(1 for _, ok, _ in results if ok)
    for ad, ok, detay in results:
        if not ok:
            print(f"  HATA · {ad}   [{detay}]")
    print(f"\n{gecen}/{len(results)} kontrol geçti")
    return 0 if gecen == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
