"""
GÜN İÇİ KAYMA MOTORU v2.1 — K1-K10 senaryo testleri.

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
  G1 04:30 uyandı, tekrar uyumadı      → gün 06:00'dan + 30 dk şekerleme (K10)
  G2 04:30 + 05:00-06:40 uyudu         → sabah 06:40, şekerleme YOK
  G3 05:50 uyandı                      → 06:00'dan, şekerleme var
  G4 06:05 uyandı                      → normal K2, şekerleme yok
  G5 09:00 uyandı                      → gün 09:00'dan, ÜST SINIR YOK
  G6 G1 + şekerleme gerçek kayıt       → nap_1 gerçek bitiş + pencere
  G7 G1'in ertesi günü                 → şablon 07:00, şekerleme yok
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
# Bu suite TAM BİR GÜNÜ simüle ediyor: 07:00 uyanış, 13:00 uyku… Koşma
# saati sabahsa bu damgalar "gelecek" düşer ve v2.4.1 zaman doğrulaması
# onları eler (bkz. logs.GELECEK_TOLERANS_DK). Simülasyonda tolerans
# gün boyuna açılır; ÜRETİM VARSAYILANI 5 dk ve test_yayin_duzeltmeleri
# onu ayrıca doğruluyor.
os.environ["LOG_GELECEK_TOLERANS_DK"] = "1440"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"        # zamanlayıcı başlamasın
os.environ["MAIL_PROVIDER"] = "disabled"
from fastapi.testclient import TestClient              # noqa: E402
from api.db import Base, SessionLocal, engine          # noqa: E402
import api.models                                      # noqa: E402,F401
from api.models import Baby, SleepLog, SleepPlan       # noqa: E402
from api.main import app                               # noqa: E402
from api.services import plan_adapter as pa            # noqa: E402
from api.services import plan_service                  # noqa: E402
from engine import yas_bantlari                        # noqa: E402
from engine.parameter_engine import hesapla_yas_ay     # noqa: E402

# LLM MÜHRÜ — import'lardan SONRA. `api.main` içindeki load_dotenv() anahtarı
# .env'den geri yüklediği için mührün burada olması ŞART; dosya başındaki
# os.environ.pop() tek başına işe YARAMIYORDU (ölçüldü).
from tests.llm_muhuru import (                         # noqa: E402
    TAM_PROFIL, canli_cagri_sayisi, fallback_cagri_sayisi, muhur_saglam_mi, muhurle)
muhurle()

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
                  json={**TAM_PROFIL, "name": "Elif", "birth_date": _dogum.isoformat(),
                        "night_wakes": 2}).json()["id"]
client.patch(f"/api/v1/babies/{BID}", headers=H,
             json={"training_started_at": (TODAY - timedelta(days=4)).isoformat()})

_gen = client.post("/api/v1/plans/generate?sync=true", headers=H,
                   json={"baby_id": BID})
assert _gen.status_code == 201, _gen.text
BASE = _gen.json()["content"]
assert BASE["generated_with"] == "fallback", (
    f"Deterministik suite CANLI Claude çağırdı: {BASE['generated_with']}")

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
    # v1.4 — gündüz açığı artık YALNIZ uyarı değil, ÇİZELGEYE BLOK olarak
    # kapatılıyor: akşam şekerlemesi eklendiği için K9 değerlendirmesi
    # "gerekli değil" diyebilir. Ölçülen şey artık açığın KAPANMIŞ olması.
    # v2.4.1 — ŞEKERLEME UYANIKLIK PENCERESİNE TABİ (K15). Bu senaryoda
    # zincir kayıp uykuyu öne çekiyor ve son uyku akşama yakın bitiyor;
    # bandın minimum uyanıklık penceresi kadar sonrası şekerleme penceresini
    # (17:00-19:00) aşıyor. Eskiden blok son uykudan HEMEN sonra ekleniyordu
    # ("uyandır, hemen yatır"); artık eklenmiyor ve sebebi uyarıya yazılıyor.
    _sek = sekerleme_blogu(s)
    _min_ww = BANT["uyaniklik_penceresi_dk"][0]
    _son_nap = max((b["end_minute"] for b in s.values()
                    if b.get("type") == "nap" and b["key"] != pa.SEKERLEME_KEY),
                   default=None)
    check("E · şekerleme eklendiyse uyanıklık penceresine uyuyor",
          _sek is None or (_sek["start_minute"] - _son_nap) >= _min_ww,
          f'{_sek["time"]} (son uyku {hhmm(_son_nap)}, gereken {_min_ww} dk)'
          if _sek else "şekerleme yok — pencereye sığmadı")
    check("E · şekerleme yoksa SEBEBİ uyarıda yazıyor",
          _sek is not None
          or any("şekerlemesi için yer kalmadı" in u
                 for u in c["adaptation"]["uyarilar"]),
          str(c["adaptation"]["uyarilar"]))
    tpl_deg = c["toplam_uyku_degerlendirme"]
    # Şekerleme eklenemediği için açık DURUYOR ve dürüstçe raporlanıyor:
    # uydurma bir blokla kapatmaktansa anneye "eksik kaldı" demek doğru.
    check("E · açık kapanmadıysa 24 saatlik toplam 'az' olarak raporlanıyor",
          (gunduz_toplam(s) >= GUNDUZ_MIN) == (tpl_deg["durum"] != "az"),
          f'gündüz {gunduz_toplam(s)}/{GUNDUZ_MIN}, '
          f'24s durum={tpl_deg["durum"]}')


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
# G1-G7 — K10 ERKEN UYANMA KURALI
# =============================================================================
# Gün en erken 06:00'da başlar (K10.1). 06:00 öncesi uyanıp TEKRAR UYUMAYAN
# bebekte gün 06:00'dan kurulur ve güne 30 dk ŞEKERLEME eklenir (K10.3).
# Beklenen saatler yine tablodan: şekerleme = 06:00 + bandın MİNİMUM penceresi.
WW_MIN = BANT["uyaniklik_penceresi_dk"][0]       # 8 ay → 120 dk
GUN_BAS = pa.GUN_BASLANGICI_EN_ERKEN             # 06:00
# v1.4 — şekerleme artık GÜNÜN SONUNDA ve YALNIZ gündüz uyku açığı varsa.
# Eski SEK_BAS (06:00 + min pencere) kavramı KALKTI.
SEK_PENCERE = pa.SEKERLEME_VARSAYILAN_PENCERE       # (17:00, 19:00)
_KES = yas_bantlari.kestirme_protokolu()
SEK_MIN, SEK_MAX = _KES["sure_dk_min"], _KES["sure_dk_max"]


def sekerleme_blogu(s):
    return s.get(pa.SEKERLEME_KEY)


def gunduz_toplam(s):
    return sum(b["end_minute"] - b["start_minute"]
               for b in s.values() if b.get("type") == "nap")


def test_g1_erken_uyanma_sekerleme():
    """04:30 uyandı, tekrar uyumadı → gün 06:00'dan.

    v1.4: ERKEN UYANMA TEK BAŞINA ŞEKERLEME EKLEMEZ (İlayda: "illa her zaman
    bir şekerlemeye gerek yok"). Gün 06:00'dan normal zincirle kurulur;
    şekerleme yalnız gündüz açığı kalırsa ve AKŞAM eklenir."""
    erken = 4 * 60 + 30
    kur(lambda row: [gece(row, TODAY, TPL["bedtime"]["start_minute"], erken)])
    c, s = bugun(now_minute=erken + 5)
    bekle("G1", s, "wake", GUN_BAS, "gün 06:00'dan")
    check("G1 · wake bloğunda gerçek saat notu var (K10.5)",
          "04:30" in (s["wake"].get("note") or ""), s["wake"].get("note"))
    # Gün 06:00 + pencere ile normal kurulur — şekerleme başa GİRMEZ.
    bekle("G1", s, "nap_1", GUN_BAS + WW, "06:00 + pencere (şekerleme yok)")
    bekle("G1", s, "nap_2", GUN_BAS + WW + NAP_SURE + WW)
    _sek = sekerleme_blogu(s)
    check("G1 · şekerleme varsa YALNIZ akşam penceresinde",
          _sek is None or SEK_PENCERE[0] <= _sek["start_minute"] < SEK_PENCERE[1],
          f'{_sek["time"]}-{_sek["end"]}' if _sek else "şekerleme yok")
    check("G1 · şekerleme günün BAŞINA eklenmedi (eski K10.3 kalktı)",
          _sek is None or _sek["start_minute"] > GUN_BAS + WW,
          f'{_sek["time"]}' if _sek else "yok")
    ad = c["adaptation"]
    check("G1 · adaptation.erken_uyanma dolu (K10.6)",
          ad["erken_uyanma"] == {"gercek_saat": hhmm(erken),
                                 "gun_baslangici": hhmm(GUN_BAS),
                                 "sekerleme_eklendi": False},
          ad["erken_uyanma"])
    check("G1 · sabah_uyanis_gercek GERÇEK saat (06:00 değil)",
          ad["sabah_uyanis_gercek"] == hhmm(erken), ad["sabah_uyanis_gercek"])
    check("G1 · uyarı tek satır olarak yazıldı (K10.6)",
          any("gün 06:00'dan başlatıldı" in u
              for u in ad["uyarilar"]), ad["uyarilar"])
    check("G1 · uyarı artık şekerlemeden söz ETMİYOR",
          not any("erken uyandı" in u and "şekerleme" in u
                  for u in ad["uyarilar"]), ad["uyarilar"])
    check("G1 · ŞABLON değişmedi (K1)",
          c["schedule_template"] == BASE["schedule_template"], "")


def test_g2_erken_sonra_tekrar_uyudu():
    """04:30 uyandı, 05:00-06:40 uyudu → sabah uyanışı 06:40, şekerleme YOK."""
    son = 6 * 60 + 40
    def loglar(row):
        return [gece(row, TODAY, TPL["bedtime"]["start_minute"], 4 * 60 + 30),
                SleepLog(user_id=row.user_id, baby_id=row.id, type="sleep",
                         started_at=utc(TODAY, 5 * 60), ended_at=utc(TODAY, son))]
    kur(loglar)
    c, s = bugun(now_minute=son + 5)
    bekle("G2", s, "wake", son, "son uyanış")
    check("G2 · şekerleme YOK", "sekerleme" not in s, list(s))
    check("G2 · erken_uyanma None", c["adaptation"]["erken_uyanma"] is None, "")
    check("G2 · 04:30 gece bölünmesi olarak kaydedildi",
          any(b["dakika"] == 4 * 60 + 30
              for b in c["adaptation"]["gece_bolunmeleri"]),
          c["adaptation"]["gece_bolunmeleri"])
    bekle("G2", s, "nap_1", zincir(son)[0])


def test_g3_bes_elli():
    """05:50 uyandı, tekrar uyumadı → yine 06:00'dan, şekerleme var."""
    erken = 5 * 60 + 50
    kur(lambda row: [gece(row, TODAY, TPL["bedtime"]["start_minute"], erken)])
    c, s = bugun(now_minute=erken + 5)
    bekle("G3", s, "wake", GUN_BAS)
    bekle("G3", s, "nap_1", GUN_BAS + WW, "06:00 + pencere (şekerleme başa yok)")
    check("G3 · erken_uyanma.gercek_saat 05:50",
          c["adaptation"]["erken_uyanma"]["gercek_saat"] == hhmm(erken),
          c["adaptation"]["erken_uyanma"])


def test_g4_alti_bes():
    """06:05 uyandı → normal K2 yolu, şekerleme YOK."""
    gerc = 6 * 60 + 5
    kur(lambda row: [gece(row, TODAY, TPL["bedtime"]["start_minute"], gerc)])
    c, s = bugun(now_minute=gerc + 5)
    bekle("G4", s, "wake", gerc, "gerçek uyanış")
    check("G4 · şekerleme YOK", "sekerleme" not in s, list(s))
    check("G4 · kaynak 'kayit'", c["adaptation"]["sabah_uyanis_kaynak"] == "kayit",
          c["adaptation"]["sabah_uyanis_kaynak"])
    bekle("G4", s, "nap_1", zincir(gerc)[0])


def test_g5_gec_uyanis_ust_sinir_yok():
    """09:00 wake kaydı → gün 09:00'dan. K10.2: üst sınır YOK."""
    gerc = 9 * 60
    kur(lambda row: [uyanik(row, TODAY, gerc)])
    c, s = bugun(now_minute=gerc + 5)
    bekle("G5", s, "wake", gerc, "üst sınır yok")
    check("G5 · kaynak 'kayit' (varsayılana düşmedi)",
          c["adaptation"]["sabah_uyanis_kaynak"] == "kayit",
          c["adaptation"]["sabah_uyanis_kaynak"])
    bekle("G5", s, "nap_1", zincir(gerc)[0])
    check("G5 · şekerleme YOK", "sekerleme" not in s, list(s))


def test_g6_erken_uyanma_gercek_kayit():
    """G1 + 08:10-08:35 gerçek kayıt.

    v1.4: bu kayıt artık "şekerleme yuvası" DEĞİL, günün BİRİNCİ gündüz
    uykusudur — şekerleme kavramı akşama taşındı. Kayıt olduğu gibi durur,
    zincir onun bitişinden akar."""
    erken, s_bas, s_bit = 4 * 60 + 30, 8 * 60 + 10, 8 * 60 + 35
    kur(lambda row: [gece(row, TODAY, TPL["bedtime"]["start_minute"], erken),
                     nap(row, TODAY, s_bas, s_bit - s_bas)])
    c, s = bugun(now_minute=s_bit + 5)
    bekle("G6", s, "nap_1", s_bas, "gerçek kayıt birinci uyku oldu")
    check("G6 · kaynağı 'kayit'",
          s["nap_1"].get("kaynak") == "kayit", s["nap_1"].get("kaynak"))
    bekle("G6", s, "nap_2", s_bit + WW, "gerçek bitiş + pencere")
    check("G6 · erken_uyanma hâlâ dolu",
          c["adaptation"]["erken_uyanma"] is not None, "")


def test_g7_ertesi_gun_sablona_doner():
    """G1'in ertesi günü kayıt yok → şablon 07:00, şekerleme YOK (K1)."""
    erken = 4 * 60 + 30
    kur(lambda row: [gece(row, TODAY, TPL["bedtime"]["start_minute"], erken)])
    bugun(now_minute=erken + 5)                      # bugünü hesapla (şekerlemeli)
    yarin = TODAY + timedelta(days=1)
    c2, s2 = bugun(now_minute=0, gun=yarin)
    bekle("G7", s2, "wake", W, "şablon hedefi")
    bekle("G7", s2, "nap_1", TPL["nap_1"]["start_minute"], "şablona döndü")
    check("G7 · şekerleme YOK", "sekerleme" not in s2, list(s2))
    check("G7 · erken_uyanma None", c2["adaptation"]["erken_uyanma"] is None, "")



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
    # v1.4 — nap_2 yalnız 30 dk sürdüğü için gün bandın gündüz minimumunun
    # altında kalıyor; akşam şekerlemesi ekleniyor ve yatış tavana dayanıyor.
    # İlayda: "gece uykusu gecikse dahi minimum gündüz uykusu önceliklidir."
    # v2.4.1 — şekerleme uyanıklık penceresine tabi; son uyku akşama yakın
    # bittiği için bu senaryoda yer kalmıyor (bkz. E).
    _sek_i = sekerleme_blogu(s)
    _min_ww_i = BANT["uyaniklik_penceresi_dk"][0]
    _son_i = max((b["end_minute"] for b in s.values()
                  if b.get("type") == "nap" and b["key"] != pa.SEKERLEME_KEY),
                 default=None)
    check("I · şekerleme eklendiyse uyanıklık penceresine uyuyor",
          _sek_i is None or (_sek_i["start_minute"] - _son_i) >= _min_ww_i,
          f'{_sek_i["time"]} (son uyku {hhmm(_son_i)})' if _sek_i
          else "şekerleme yok — pencereye sığmadı")
    # Şekerleme zincir halkası DEĞİL (yalnız alt sınır koyar), bu yüzden yatış
    # doğal zincirden gelmeye devam ediyor.
    bekle("I", s, "bedtime",
          beklenen_yatis(gercek_bas + gercek_sure + WW + NAP_SURE),
          "doğal zincir — şekerleme yatışı ötelemedi")
    check("I · yatış şekerleme bitişinden en az 60 dk sonra",
          _sek_i is None
          or s["bedtime"]["start_minute"] - _sek_i["end_minute"] >= 60,
          f'sek={_sek_i["end"]} yatis={s["bedtime"]["time"]}' if _sek_i else "")
    check("I · nap_3 ve bedtime yeniden hesaplananlar listesinde",
          {"nap_3", "bedtime"} <= set(c["adaptation"]["yeniden_hesaplanan_bloklar"]),
          c["adaptation"]["yeniden_hesaplanan_bloklar"])


# =============================================================================
# S1-S4 — ŞEKERLEME (v1.4: K9 + K10.3 tek mekanizma)
# =============================================================================
# İlayda: "İlla her zaman bir şekerlemeye gerek yok. Gün içerisinde uyku
# yetersiz kalırsa bir şekerleme yapıyoruz" + "günün sonunda ekliyoruz".
def _gunduz_dk(s):
    return sum(b["end_minute"] - b["start_minute"]
               for b in s.values() if b.get("type") == "nap")


def test_s1_acik_yok_sekerleme_yok():
    """Gündüz uykusu bandın minimumunu DOLDURUYORSA şekerleme EKLENMEZ."""
    # Bandın minimumunu aşan uzun kayıtlar: açık kalmasın.
    uzun = max(NAP_SURE, (GUNDUZ_MIN // N_NAP) + 20)
    def loglar(row):
        out = [gece(row, TODAY, TPL["bedtime"]["start_minute"], W)]
        bas = W + WW
        for _ in range(N_NAP):
            out.append(nap(row, TODAY, bas, uzun))
            bas = bas + uzun + WW
        return out
    kur(loglar)
    c, s = bugun(now_minute=20 * 60)
    check("S1 · gündüz toplamı bandın minimumunu karşılıyor",
          _gunduz_dk(s) >= GUNDUZ_MIN, f"{_gunduz_dk(s)} / {GUNDUZ_MIN}")
    check("S1 · ŞEKERLEME EKLENMEDİ (açık yok)",
          sekerleme_blogu(s) is None,
          str(sekerleme_blogu(s) or "yok"))
    check("S1 · adaptation.sekerleme None",
          c["adaptation"]["sekerleme"] is None, c["adaptation"]["sekerleme"])


def test_s2_acik_var_aksam_sekerlemesi():
    """Gündüz açığı varsa şekerleme AKŞAM penceresinde eklenir."""
    # Kısa uykular: toplam bandın minimumunun altında kalsın.
    kisa = 30
    def loglar(row):
        out = [gece(row, TODAY, TPL["bedtime"]["start_minute"], W)]
        bas = W + WW
        for _ in range(N_NAP):
            out.append(nap(row, TODAY, bas, kisa))
            bas = bas + kisa + WW
        return out
    kur(loglar)
    c, s = bugun(now_minute=20 * 60)
    sek = sekerleme_blogu(s)
    check("S2 · şekerleme EKLENDİ", sek is not None,
          f"gündüz={_gunduz_dk(s)} min={GUNDUZ_MIN}")
    check("S2 · akşam penceresinde (17:00-19:00)",
          sek is not None
          and SEK_PENCERE[0] <= sek["start_minute"] < SEK_PENCERE[1],
          f'{sek["time"]}-{sek["end"]}' if sek else "yok")
    check("S2 · başlık süreye göre dinamik",
          sek is not None
          and sek["title"] == pa.sekerleme_basligi(
              sek["end_minute"] - sek["start_minute"]),
          sek["title"] if sek else "")
    check("S2 · gece yatışından en az 60 dk önce bitiyor",
          sek is not None and s["bedtime"]["start_minute"] - sek["end_minute"] >= 60,
          f'sek_bit={sek["end"]} yatis={s["bedtime"]["time"]}' if sek else "")
    ad = c["adaptation"]["sekerleme"]
    check("S2 · adaptation.sekerleme gerekçeyi taşıyor",
          ad is not None and ad["tetik"] == "gunduz_acigi" and ad["eksik_dk"] > 0,
          str(ad))


def test_s3_yatisa_uc_saat_varsa_altmis_dk():
    """Gece yatışına ≥150 dk kalıyorsa şekerleme 60 dk olur.

    v2.4.1 — kurgu DEĞİŞTİ: eskiden tek kısa uyku vardı ve motor kalan
    uykuları varsayıp günü akşama kadar dolduruyordu; son uyku 17:20'de
    bitince şekerleme (uyanıklık penceresi nedeniyle) artık sığmıyor.
    Süre kuralını ölçebilmek için şekerlemenin GERÇEKTEN sığdığı bir gün
    kuruldu: bandın uyku sayısı kadar KISA ve ERKEN biten gerçek uyku."""
    def loglar(row):
        out = [gece(row, TODAY, TPL["bedtime"]["start_minute"], W)]
        bas = W + WW
        for _ in range(N_NAP):
            out.append(nap(row, TODAY, bas, 35))     # kısa → gündüz açığı kalır
            bas += 35 + WW
        return out
    kur(loglar)
    c, s = bugun(now_minute=20 * 60)
    sek = sekerleme_blogu(s)
    sure = (sek["end_minute"] - sek["start_minute"]) if sek else 0
    kalan = (s["bedtime"]["start_minute"] - sek["start_minute"]) if sek else 0
    check("S3 · şekerleme eklendi", sek is not None,
          str([(b["key"], b.get("time"), b.get("end")) for b in s.values()]))
    check("S3 · yatışa ≥150 dk varsa süre 60 dk, değilse 30 dk",
          (sure == SEK_MAX) if kalan >= 150 else (sure == SEK_MIN),
          f"süre={sure} yatışa kalan={kalan} dk")
    check("S3 · süre tablodaki aralıkta", SEK_MIN <= sure <= SEK_MAX,
          f"{sure} ∉ [{SEK_MIN},{SEK_MAX}]")
    # v2.4.1 — K15: şekerleme de uyanıklık penceresine uyar.
    _son_s3 = max((b["end_minute"] for b in s.values()
                   if b.get("type") == "nap" and b["key"] != pa.SEKERLEME_KEY),
                  default=None)
    check("S3 · son uykudan en az bandın minimum penceresi kadar sonra",
          sek is not None
          and (sek["start_minute"] - _son_s3) >= BANT["uyaniklik_penceresi_dk"][0],
          f'son uyku {hhmm(_son_s3)}, şekerleme {sek["time"] if sek else "-"}')


def test_s4_tek_uyku_kisa_aksam_sekerlemesi():
    """12-18 ay TEK UYKU: öğle uykusu 90 dk (<120) → akşam şekerlemesi."""
    tok2 = client.post("/api/v1/auth/register",
                       json={"email": "sekerleme_tek@example.com",
                             "password": "TestPass123!"}).json()["access_token"]
    h2 = {"Authorization": f"Bearer {tok2}"}
    dogum15 = TODAY - timedelta(days=456)          # ≈ 15 ay
    bid2 = client.post("/api/v1/babies", headers=h2,
                       json={**TAM_PROFIL, "name": "Tek", "birth_date": dogum15.isoformat(),
                             "night_wakes": 1}).json()["id"]
    gen = client.post("/api/v1/plans/generate?sync=true", headers=h2,
                      json={"baby_id": bid2})
    assert gen.status_code == 201, gen.text
    bant15 = yas_bantlari.yas_bandi_getir(
        hesapla_yas_ay(dogum15.isoformat(), 40)["duzeltilmis_ay"],
        tek_uyku=True)
    tpl15 = pa.build_schedule({}, 7 * 60, yas_ay=15, tek_uyku=True)
    w15 = pa.sabit_wake_minute(tpl15)

    class L:
        def __init__(s_, tip, bas, bit=None, gun=TODAY):
            import uuid as _u
            s_.id = _u.uuid4(); s_.type = tip
            s_.started_at = utc(gun, bas)
            s_.ended_at = None if bit is None else utc(gun, bit)

    # Gece uykusu + 12:00-13:30 öğle uykusu (90 dk < 120 dk minimum)
    loglar = [L("sleep", 21 * 60 + 30, w15, TODAY - timedelta(days=1)),
              L("nap", 12 * 60, 13 * 60 + 30)]
    loglar[0].ended_at = utc(TODAY, w15)
    r = pa.recompute_day(tpl15, bant15, w15, loglar, now_minute=20 * 60,
                         gun=TODAY, tz_offset_min=TZ)
    blok = {b["key"]: b for b in r["schedule"]}
    sek = blok.get(pa.SEKERLEME_KEY)
    check("S4 · tek uykuda 90 dk → şekerleme eklendi", sek is not None,
          str([(b["key"], b["time"], b.get("end")) for b in r["schedule"]]))
    check("S4 · şekerleme 18:00-19:00 penceresinde (tek uyku varyantı)",
          sek is not None
          and pa.SEKERLEME_TEK_UYKU_PENCERE[0] <= sek["start_minute"]
          < pa.SEKERLEME_TEK_UYKU_PENCERE[1],
          f'{sek["time"]}-{sek["end"]}' if sek else "yok")
    check("S4 · tetik tek_uyku_kisa",
          (r["adaptation"]["sekerleme"] or {}).get("tetik") == "tek_uyku_kisa",
          str(r["adaptation"]["sekerleme"]))


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
    check("M · 'en geç saate denk geldi' uyarısı var",
          any("en geç saate denk geldi" in u for u in c["adaptation"]["uyarilar"]),
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
def test_z_llm_muhru():
    """Faz 4 — bu suite HİÇBİR canlı Sonnet çağrısı yapmamalı."""
    ok, detay = muhur_saglam_mi()
    check("Z · LLM mührü sağlam (canlı çağrı YOK)", ok, detay)
    check("Z · plan gerçekten yedek motordan üretildi",
          fallback_cagri_sayisi() > 0 and canli_cagri_sayisi() == 0,
          f"fallback={fallback_cagri_sayisi()} canli={canli_cagri_sayisi()}")
    check("Z · üretilen plan 'fallback' damgalı",
          BASE["generated_with"] == "fallback", BASE["generated_with"])


TESTLER = [test_a_plana_uyuldu, test_b_sabah_uykusu_gec, test_c_gec_uyanis,
           test_d_erken_uyanis, test_e_uyku_atlandi, test_f_gece_bolunmesi,
           test_g1_erken_uyanma_sekerleme, test_g2_erken_sonra_tekrar_uyudu,
           test_g3_bes_elli, test_g4_alti_bes, test_g5_gec_uyanis_ust_sinir_yok,
           test_g6_erken_uyanma_gercek_kayit, test_g7_ertesi_gun_sablona_doner,
    test_s1_acik_yok_sekerleme_yok, test_s2_acik_var_aksam_sekerlemesi,
    test_s3_yatisa_uc_saat_varsa_altmis_dk,
    test_s4_tek_uyku_kisa_aksam_sekerlemesi,
           test_h_kayit_yok, test_i_varsayim_bozulur,
           test_j_gecmise_donuk, test_k_sleep_tipi_karismasi, test_l_birikme_yok,
           test_m_yatis_tavani, test_n_idempotans, test_o_batch_tetikler,
           test_p_mobil_sozlesmesi, test_z_llm_muhru]



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
