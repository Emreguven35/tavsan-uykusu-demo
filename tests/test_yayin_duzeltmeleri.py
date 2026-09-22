"""
YAYIN ÖNCESİ DÜZELTMELER — tests/YAYIN_ONCESI_RAPOR.md bulgularının kilidi.
LLM YOK, ağ YOK, prod DB YOK.

Kilitlenen dört düzeltme:
  K1  5 ayını dolduran bebek eğitim planına GEÇER (bayrak + arka plan üretim).
  K2  Şekerleme bandın MİNİMUM uyanıklık penceresini tanır (K15).
  O1  /logs/weekly-summary kayıt semantiğini uygular; gün 24 saati aşmaz.
  O2  Gelecek tarihli / ters kayıt batch'te elenir (Türkçe invalid_time).

Çalıştırma: python tests/test_yayin_duzeltmeleri.py
"""
import os
import sys
import tempfile
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "yayin_duzeltme_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import Baby                                 # noqa: E402
from api.main import app                                    # noqa: E402
from api.services import plan_adapter as pa                 # noqa: E402
from api.services import plan_jobs, plan_service            # noqa: E402
from engine import yas_bantlari                             # noqa: E402

from tests.llm_muhuru import muhur_saglam_mi, muhurle       # noqa: E402
muhurle()                                    # canlı Sonnet YOK

Base.metadata.create_all(bind=engine)
client = TestClient(app)
TZ = pa.TZ_OFFSET_MIN
TODAY = datetime.now(timezone.utc).date()
DUN = TODAY - timedelta(days=1)

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


def utc(gun: date, dk: int) -> str:
    return (datetime(gun.year, gun.month, gun.day, tzinfo=timezone.utc)
            + timedelta(minutes=dk - TZ)).isoformat().replace("+00:00", "Z")


def _tok(eposta: str) -> dict:
    r = client.post("/api/v1/auth/register",
                    json={"email": eposta, "password": "TestPass123!"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def bebek_kur(H, ay: float, **ek) -> str:
    govde = {"name": "Test",
             "birth_date": (TODAY - timedelta(days=int(ay * 30.44))).isoformat()}
    govde.update(ek)
    return client.post("/api/v1/babies", headers=H, json=govde).json()["id"]


def yas_degistir(bid: str, ay: float) -> None:
    db = SessionLocal()
    try:
        row = db.query(Baby).filter(Baby.id == _uuid.UUID(bid)).one()
        row.birth_date = TODAY - timedelta(days=int(ay * 30.44))
        db.commit()
    finally:
        db.close()


class L:
    """ORM benzeri kayıt — motor yalnız bu dört alanı okur."""
    def __init__(self, tip, gun, bas, bit=None, bit_gun=None):
        self.id = _uuid.uuid4()
        self.type = tip
        self.started_at = _dt(gun, bas)
        self.ended_at = None if bit is None else _dt(bit_gun or gun, bit)


def _dt(g, dk):
    return (datetime(g.year, g.month, g.day, tzinfo=timezone.utc)
            + timedelta(minutes=dk - TZ))


# =============================================================================
# K2 — ŞEKERLEME MİNİMUM UYANIKLIK PENCERESİNİ TANIR
# =============================================================================
print("K2) Şekerleme uyanıklık penceresi")


def _sekerleme_senaryosu(yas_ay: float, nap_sure: int, n: int, bosluk: int):
    """Gündüz açığı bırakan kısa uykular → şekerleme tetiklenir."""
    tpl = pa.build_schedule({}, 7 * 60, yas_ay=yas_ay)
    icerik = {"schedule": tpl, "schedule_template": tpl,
              "yas_bandi": yas_bantlari.yas_bandi_getir(yas_ay)}
    loglar = [L("sleep", DUN, 20 * 60 + 30, 7 * 60, bit_gun=TODAY)]
    bas = 7 * 60 + bosluk
    for _ in range(n):
        loglar.append(L("nap", TODAY, bas, bas + nap_sure))
        bas += nap_sure + bosluk
    ozet = pa.summarize_logs(loglar, today=TODAY)
    r = pa.adapt(icerik, {}, loglar, today=TODAY, now_minute=23 * 60,
                 yas_ay=yas_ay, log_summary=ozet)
    bl = {b["key"]: b for b in r["schedule"]}
    naplar = [b for b in r["schedule"] if b.get("type") == "nap"
              and b["key"] != pa.SEKERLEME_KEY]
    son = max((b["end_minute"] for b in naplar), default=None)
    return r, bl.get(pa.SEKERLEME_KEY), son


# 4 aylık: bant minimumu 90 dk. Son uyku 16:30'da bitiyor.
r4, sek4, son4 = _sekerleme_senaryosu(4.0, 40, 4, 100)
_min4 = yas_bantlari.yas_bandi_getir(4.0)["uyaniklik_penceresi_dk"][0]
check("K2a) 4 aylık: şekerleme eklendi", sek4 is not None,
      str([(b["key"], b.get("time")) for b in r4["schedule"]]))
check(f"K2b) 4 aylık: son uykudan en az {_min4} dk sonra başlıyor",
      sek4 is not None and sek4["start_minute"] - son4 >= _min4,
      f'son uyku {pa._fmt(son4)}, şekerleme {sek4["time"] if sek4 else "-"} '
      f'→ {sek4["start_minute"] - son4 if sek4 else "-"} dk')
check("K2c) 4 aylık: şekerleme akşam penceresinde",
      sek4 is not None
      and pa.SEKERLEME_VARSAYILAN_PENCERE[0] <= sek4["start_minute"]
      < pa.SEKERLEME_VARSAYILAN_PENCERE[1],
      sek4["time"] if sek4 else "")

# 8 aylık: bant minimumu 120 dk.
r8, sek8, son8 = _sekerleme_senaryosu(8.0, 35, 3, 130)
_min8 = yas_bantlari.yas_bandi_getir(8.0)["uyaniklik_penceresi_dk"][0]
check(f"K2d) 8 aylık: son uykudan en az {_min8} dk sonra başlıyor",
      sek8 is not None and sek8["start_minute"] - son8 >= _min8,
      f'son uyku {pa._fmt(son8)}, şekerleme {sek8["time"] if sek8 else "-"} '
      f'→ {sek8["start_minute"] - son8 if sek8 else "-"} dk')

# Son uyku 17:10'da bitiyor → 90 dk sonrası 18:40, pencere 19:00'a kadar:
# 30 dk sığmaz (19:00 - 60 dk geçiş = 18:00 tavanı) → şekerleme EKLENMEZ + uyarı.
tpl = pa.build_schedule({}, 7 * 60, yas_ay=4.0)
icerik = {"schedule": tpl, "schedule_template": tpl,
          "yas_bandi": yas_bantlari.yas_bandi_getir(4.0)}
loglar = [L("sleep", DUN, 20 * 60 + 30, 7 * 60 + 10, bit_gun=TODAY),
          L("nap", TODAY, 9 * 60, 9 * 60 + 40),
          L("nap", TODAY, 12 * 60, 12 * 60 + 40),
          L("nap", TODAY, 15 * 60, 15 * 60 + 40),
          L("nap", TODAY, 16 * 60 + 30, 17 * 60 + 10)]
rk = pa.adapt(icerik, {}, loglar, today=TODAY, now_minute=23 * 60, yas_ay=4.0,
              log_summary=pa.summarize_logs(loglar, today=TODAY))
_sek = {b["key"]: b for b in rk["schedule"]}.get(pa.SEKERLEME_KEY)
_uyarilar = (rk["adaptation"] or {}).get("uyarilar") or []
check("K2e) Pencereye sığmıyorsa şekerleme EKLENMEZ", _sek is None,
      f'{_sek["time"]}-{_sek.get("end")}' if _sek else "eklenmedi")
check("K2f) Sığmadığında istenen uyarı üretiliyor",
      any("akşam şekerlemesi için yer kalmadı" in u for u in _uyarilar),
      str(_uyarilar))
check("K2g) Uyarı anne diliyle 'en geç saatte tutun' diyor",
      any("yaşına uygun en geç saatte tutun" in u for u in _uyarilar),
      str(_uyarilar))

# Hiçbir senaryoda sıfır/kısa uyanıklıkla şekerleme üretilmemeli.
_ihlal = []
for ay in (4.0, 6.0, 8.0, 10.0):
    mn = yas_bantlari.yas_bandi_getir(ay)["uyaniklik_penceresi_dk"][0]
    for sure in (20, 35, 45, 60):
        for bosluk in (90, 110, 130, 150):
            _r, _s, _son = _sekerleme_senaryosu(ay, sure, 3, bosluk)
            if _s is not None and _son is not None and _s["start_minute"] - _son < mn:
                _ihlal.append(f"{ay}ay/{sure}dk/{bosluk}dk → "
                              f"{_s['start_minute'] - _son} dk < {mn}")
check("K2h) 64 senaryonun hiçbirinde uyanıklık penceresi ihlali yok",
      not _ihlal, "; ".join(_ihlal[:4]))


# =============================================================================
# K1 — 5 AYINI DOLDURAN BEBEK EĞİTİM PLANINA GEÇER
# =============================================================================
print("K1) 5 ay geçişi")
H = _tok("gecis1@example.com")
BID = bebek_kur(H, 4.83)                     # ≈ 4 ay 25 gün

r = client.post("/api/v1/plans/generate?sync=true", headers=H,
                json={"baby_id": BID})
c0 = r.json()["content"]
check("K1a) 4 ay 25 günlük bebeğe BEKLEME planı üretildi",
      c0.get("type") == "egitim_bekleme" and c0.get("uygun_mu") is False,
      f'type={c0.get("type")} uygun_mu={c0.get("uygun_mu")}')

c1 = client.get(f"/api/v1/plans/today?baby_id={BID}", headers=H).json()["content"]
check("K1b) Yaş gelmeden geçiş bayrağı YOK",
      not c1.get("egitim_zamani_geldi"), str(c1.get("egitim_zamani_geldi")))

# 5 gün ilerlet (doğum tarihini geriye çekerek)
yas_degistir(BID, 5.0 + 5 / 30.44)
plan_jobs.reset()
c2 = client.get(f"/api/v1/plans/today?baby_id={BID}", headers=H).json()["content"]
check("K1c) 5 ayı geçince geçiş bayrakları geliyor",
      c2.get("egitim_zamani_geldi") is True
      and c2.get("yeniden_uretiliyor") is True,
      f'geldi={c2.get("egitim_zamani_geldi")} '
      f'uretiliyor={c2.get("yeniden_uretiliyor")}')
check("K1d) GET bloklanmadı — eski plan hâlâ dönüyor",
      c2.get("type") == "egitim_bekleme", str(c2.get("type")))

db = SessionLocal()
try:
    _b = db.query(Baby).filter(Baby.id == _uuid.UUID(BID)).one()
    check("K1e) training_started_at backend'de set edildi",
          _b.training_started_at is not None, str(_b.training_started_at))
finally:
    db.close()

# Arka plan işinin bitmesini bekle (LLM mühürlü → fallback, hızlı)
import time                                                  # noqa: E402
for _ in range(60):
    time.sleep(0.5)
    _isler = [j for j in plan_jobs._JOBS.values()]
    if _isler and all(j["status"] != "processing" for j in _isler):
        break
_durumlar = [j["status"] for j in plan_jobs._JOBS.values()]
check("K1f) Arka plan üretimi tamamlandı", _durumlar and "done" in _durumlar,
      str(_durumlar))

c3 = client.get(f"/api/v1/plans/today?baby_id={BID}", headers=H).json()["content"]
check("K1g) Sonraki GET GERÇEK eğitim planını veriyor",
      c3.get("type") == "egitim_plani" and c3.get("uygun_mu") is True,
      f'type={c3.get("type")} uygun_mu={c3.get("uygun_mu")}')
check("K1h) Günler artık önizleme DEĞİL",
      all(not d.get("preview") for d in (c3.get("days") or [])),
      str([d.get("preview") for d in (c3.get("days") or [])][:5]))
check("K1i) Üretim izi içerikte (bildirim bunu kullanıyor)",
      c3.get("egitim_gecisi") is True, str(c3.get("egitim_gecisi")))
check("K1j) Geçiş tamamlanınca bayrak tekrar TETİKLENMİYOR",
      not c3.get("yeniden_uretiliyor"), str(c3.get("yeniden_uretiliyor")))

# Yaş dışı sebeple bekleyen plan (sağlık onayı) DOKUNULMAZ.
H2 = _tok("gecis2@example.com")
BID2 = bebek_kur(H2, 7.0, saglik_problemi="kalp rahatsızlığı")
c4 = client.post("/api/v1/plans/generate?sync=true", headers=H2,
                 json={"baby_id": BID2}).json()["content"]
check("K1k) Sağlık nedeniyle uygun olmayan bebek BEKLEME planında",
      c4.get("type") == "egitim_bekleme", str(c4.get("type")))
c5 = client.get(f"/api/v1/plans/today?baby_id={BID2}", headers=H2).json()["content"]
check("K1l) Yaş dışı sebeple bekleyen plan geçişe ZORLANMIYOR",
      not c5.get("egitim_zamani_geldi"),
      f'geldi={c5.get("egitim_zamani_geldi")} type={c5.get("type")}')


# =============================================================================
# O1 — HAFTALIK ÖZET KAYIT SEMANTİĞİNİ UYGULAR
# =============================================================================
print("O1) weekly-summary")
H3 = _tok("ozet1@example.com")
BID3 = bebek_kur(H3, 6.3)

# Raporun 27,85 saatlik vakası: AYNI gece iki kayıt + parça uykular +
# gece uykusunun içinde kalan bir kayıt.
gun = TODAY - timedelta(days=1)
onceki = gun - timedelta(days=1)
client.post("/api/v1/logs/batch", headers=H3, json={"logs": [
    {"baby_id": BID3, "type": "sleep", "started_at": utc(onceki, 20 * 60 + 45),
     "ended_at": utc(gun, 7 * 60 + 45), "client_id": "w-gece1"},
    {"baby_id": BID3, "type": "sleep", "started_at": utc(onceki, 21 * 60 + 50),
     "ended_at": utc(gun, 8 * 60 + 25), "client_id": "w-gece2"},
    {"baby_id": BID3, "type": "sleep", "started_at": utc(gun, 6 * 60 + 30),
     "ended_at": utc(gun, 7 * 60 + 15), "client_id": "w-geceici"},
    {"baby_id": BID3, "type": "nap", "started_at": utc(gun, 14 * 60),
     "ended_at": utc(gun, 14 * 60 + 36), "client_id": "w-parca1"},
    {"baby_id": BID3, "type": "nap", "started_at": utc(gun, 14 * 60 + 36),
     "ended_at": utc(gun, 15 * 60 + 46), "client_id": "w-parca2"},
]})
oz = client.get(f"/api/v1/logs/weekly-summary?baby_id={BID3}", headers=H3).json()
_gun = next((d for d in oz["days"] if d["date"] == gun.isoformat()), None)
check("O1a) Hiçbir gün 24 saati AŞMIYOR",
      all(d["sleep_hours"] <= 24 for d in oz["days"]),
      str([(d["date"], d["sleep_hours"]) for d in oz["days"]]))
check("O1b) Çakışan iki gece kaydı TEK sayıldı (≈11 sa, 21,8 değil)",
      _gun is not None and _gun["sleep_hours"] < 16,
      f'{_gun["sleep_hours"]} sa' if _gun else "gün yok")
check("O1c) Parça uykular tek uyku sayıldı (2 değil 1 nap)",
      _gun is not None and _gun["naps"] == 1,
      f'naps={_gun["naps"]}' if _gun else "")
check("O1d) Haftalık toplam 7×24'ü aşmıyor",
      oz["total_sleep_hours"] <= 7 * 24, str(oz["total_sleep_hours"]))


# =============================================================================
# O2 — GELECEK TARİHLİ / TERS KAYIT ELENİR
# =============================================================================
print("O2) batch zaman doğrulaması")
H4 = _tok("zaman1@example.com")
BID4 = bebek_kur(H4, 8.0)
# Saatler ŞİMDİYE GÖRE kurulur: sabit duvar saati kullanmak, testin
# koştuğu saate göre kaydı "gelecek" yapıp yanlış dala düşürüyordu.
_simdi = datetime.now(timezone.utc)
ileri = (_simdi + timedelta(days=3)).isoformat()
_gecmis_bas = (_simdi - timedelta(hours=3)).isoformat()
_gecmis_bit = (_simdi - timedelta(hours=2)).isoformat()
r = client.post("/api/v1/logs/batch", headers=H4, json={"logs": [
    {"baby_id": BID4, "type": "nap", "started_at": ileri, "client_id": "gel"},
    # ters: bitiş başlangıçtan ÖNCE, ikisi de geçmişte
    {"baby_id": BID4, "type": "nap", "started_at": _gecmis_bit,
     "ended_at": _gecmis_bas, "client_id": "ters"},
    {"baby_id": BID4, "type": "nap", "started_at": _gecmis_bas,
     "ended_at": _gecmis_bit, "client_id": "saglam"},
]})
j = r.json()
_elenen = {x["client_id"]: x for x in j["skipped"]}
check("O2a) İstek 200 (kayıt bazlı ret, batch düşmüyor)",
      r.status_code == 200, str(r.status_code))
check("O2b) Gelecek tarihli kayıt elendi",
      _elenen.get("gel", {}).get("reason") == "invalid_time",
      str(_elenen.get("gel")))
check("O2c) Ters kayıt elendi",
      _elenen.get("ters", {}).get("reason") == "invalid_time",
      str(_elenen.get("ters")))
check("O2d) Ret mesajları Türkçe ve ayrı ayrı açıklayıcı",
      "gelecek bir zamana" in (_elenen.get("gel", {}).get("detail") or "")
      and "başlangıcından önce" in (_elenen.get("ters", {}).get("detail") or ""),
      str([(k, _elenen.get(k, {}).get("detail")) for k in ("gel", "ters")]))
check("O2e) Sağlam kayıt yazıldı", j["created"] == 1, str(j["created"]))

# Tolerans: 2 dakika ilerideki kayıt KABUL edilir (telefon saati sapması).
yakin = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
r2 = client.post("/api/v1/logs/batch", headers=H4, json={"logs": [
    {"baby_id": BID4, "type": "feed", "started_at": yakin, "client_id": "yakin"}]})
check("O2f) 2 dk ilerideki kayıt kabul ediliyor (tolerans)",
      r2.json()["created"] == 1, str(r2.json()))


# =============================================================================
# ORTA3 — bildirim yayma
# =============================================================================
print("ORTA3) bildirim yayma")
from api.services import notifier                            # noqa: E402
_kaymalar = [notifier.yayma_dakikasi(f"kullanici-{i}") for i in range(200)]
check("O3a) Kayma 0..10 aralığında",
      all(0 <= k <= notifier.BILDIRIM_YAYMA_DK for k in _kaymalar),
      f"min={min(_kaymalar)} max={max(_kaymalar)}")
check("O3b) Kullanıcılar arasında gerçekten yayılıyor",
      len(set(_kaymalar)) >= 8, f"{len(set(_kaymalar))} farklı dakika")
check("O3c) Aynı kullanıcı HER ZAMAN aynı dakikayı alıyor",
      notifier.yayma_dakikasi("abc") == notifier.yayma_dakikasi("abc"),
      "")


# =============================================================================
# Mühür
# =============================================================================
_ok, _detay = muhur_saglam_mi()
check("Z) LLM mührü sağlam (canlı çağrı YOK)", _ok, _detay)

print("\n" + "=" * 78)
print("YAYIN ÖNCESİ DÜZELTMELER — TEST SONUÇLARI")
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
