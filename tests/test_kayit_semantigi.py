"""
KAYIT SEMANTİĞİ v2.2 — K12/K13/K14/K15 senaryo testleri (W1-W8).

LLM YOK, ağ YOK, prod DB YOK: geçici sqlite + FastAPI TestClient + LLM mührü.

NEDEN VAR: beta annelerinin GERÇEK verisinde motorun dört hatası ölçüldü —
  1. başlangıç=bitiş nokta kayıtları uyku yuvasına eşlenip sıfır uzunluklu nap
     üretiyor, zincir oradan başlıyordu (K12.1),
  2. gece uyanmaları süre şartı yüzünden hiç sayılmıyordu (K12.2),
  3. sayaçtan açık kalan kayıt ile aynı aralığı kapsayan manuel kayıt iki ayrı
     uyku sanılıyordu (K13),
  4. sabah uyanışı, gece uykusunun bitişi yerine varsayılan hedefte kalıyordu
     (K12.3/K12.4).

BEKLENEN SAATLER ELLE YAZILMAZ: bant değerleri yas_bantlari.json'dan okunur.
Tablo değişirse test de birlikte değişir.

Senaryolar:
  W1  night_wake nokta kayıtları (04:18/05:00/05:30, ended_at null)
      → hiçbir yuvaya eşlenmez, gece uyanma sayısı 3, plan şablonla AYNI
  W2  gece uykusu AÇIK + wake 08:00 → sabah uyanışı 08:00, gün 08:00'dan;
      14,5 aylıkta ilk uyku ≥ 08:00 + minimum pencere (K15)
  W3  sleep 09:00-09:00 (sıfır süre) → yok sayılır, sebep yazılır
  W4  sayaç açık 10:01 + manuel 10:00-11:49 → tek nap, hayalet ikinci uyku YOK
  W5  POST /logs/batch → açık sayaç 11:49'da kapanır, timer_closed true
  W6  gece 21:50-07:05 + night_wake 02:00-02:20 → bugünün kayıtlarında görünür,
      sabah uyanışı 07:05, net gece süresi = brüt − 20
  W7  dün 21:50 başlayan AÇIK gece uykusu → bugünün listesinde, "sürüyor"
  W8  K15: 14,5 aylık, uyanış 08:00, manuel nap 09:00-09:40 → nap KABUL edilir
      ama uyarı üretilir, sonraki bloklar 09:40'tan

v2.2.1 — K16/K17/K18 (gerçek vaka: aynı bebekte 06:30, 08:24, 08:26 başlangıçlı
üç AÇIK kayıt + 09:50 ve 14:36; 08:24/08:26 zayıf ağda yeniden gönderimden
kopya; sonuç 5 gündüz uykusu ve "şablonda olmayan ilave uyku"):
  X1  üç açık nap → tek açık kalır, ikisi kapatılır, 3 uyku bloğu
  X2  06:30 açık nap, saat 14:00 → kapanmış sayılır + "Sayaç kapatılmadı" uyarısı
  X3  aynı client_id iki kez → TEK satır (id değişmez)
  X4  client_id'siz ±2 dk kopya → TEK satır
  X5  açık nap + wake 07:15 → nap 07:15'te kapanır
  X6  toplam uyku açık kayıtta None/NaN üretmez; ŞİMDİYE kadarki süre sayılır

v2.2.2 — K19/K20 (ürün kararı: mobil artık uyku tipi sordurmuyor; anne yalnız
"Uyudu"/"Uyandı" diyor, gündüz/gece ayrımını BACKEND yapar):
  Y1  sleep 09:50-10:40      → gündüz uykusu
  Y2  sleep 20:30 açık       → gece uykusu
  Y3  nap tipi 21:00         → gece uykusu (type sınıfı BELİRLEMEZ)
  Y4  sleep 18:00-19:00 60dk → gündüz (17:00-19:00 kısa kayıt istisnası)
  Y5  sekerleme 13:00-13:30  → gündüz uykusu (eski istemci tipi)
  Z1  14:00-14:36 + 14:36-15:46 (bitişik)   → tek uyku 14:00-15:46
  Z2  08:26-09:36 + 09:50-11:00 (14 dk)     → tek uyku 08:26-11:00
  Z3  09:00-09:40 + 10:00-10:30 (20 dk)     → İKİ ayrı uyku
  Z4  Gerçek vaka (Ahmet Kerem, 6,3 ay)     → 3 gündüz uykusu, "ilave uyku" yok

Çalıştırma: python tests/test_kayit_semantigi.py
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

_DB = Path(tempfile.gettempdir()) / "kayit_semantigi_test.db"
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
from api.models import Baby, SleepLog                  # noqa: E402
from api.main import app                               # noqa: E402
from api.services import plan_adapter as pa            # noqa: E402
from engine import yas_bantlari                        # noqa: E402
from engine.parameter_engine import hesapla_yas_ay     # noqa: E402

from tests.llm_muhuru import canli_cagri_sayisi, muhurle   # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


TZ = pa.TZ_OFFSET_MIN
TODAY = datetime.now(timezone.utc).date()
DUN = TODAY - timedelta(days=1)


def utc(gun, yerel_dk: int) -> datetime:
    """Yerel (UTC+3) duvar dakikası → UTC datetime."""
    return (datetime(gun.year, gun.month, gun.day, tzinfo=timezone.utc)
            + timedelta(minutes=yerel_dk - TZ))


def hhmm(dk) -> str:
    return pa._fmt(dk)


# =============================================================================
# İki şablon: 8 aylık ve 14,5 aylık
# =============================================================================
tok = client.post("/api/v1/auth/register",
                  json={"email": "kayit_semantigi@example.com",
                        "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}


class Sablon:
    """Bir yaş için: bebek kaydı, bant, şablon çizelge ve türetilmiş sabitler."""

    def __init__(self, ad: str, yas_gun: int):
        self.ad = ad
        dogum = TODAY - timedelta(days=yas_gun)
        self.baby_id = client.post(
            "/api/v1/babies", headers=H,
            json={"name": ad, "birth_date": dogum.isoformat(),
                  "night_wakes": 2}).json()["id"]
        gen = client.post("/api/v1/plans/generate?sync=true", headers=H,
                          json={"baby_id": self.baby_id})
        assert gen.status_code == 201, gen.text
        icerik = gen.json()["content"]
        assert icerik["generated_with"] == "fallback", icerik["generated_with"]
        self.template = icerik["schedule_template"]
        self.yas_ay = hesapla_yas_ay(dogum.isoformat(), 40)["duzeltilmis_ay"]
        self.bant = yas_bantlari.yas_bandi_getir(self.yas_ay)
        self.cp = yas_bantlari.cizelge_parametreleri(self.bant)
        self.ww = self.cp["uyaniklik_penceresi_dk"]
        self.ww_min = int(self.bant["uyaniklik_penceresi_dk"][0])
        self.n_nap = self.cp["uyku_sayisi"]
        self.wake = pa.sabit_wake_minute(self.template)

    def hesapla(self, loglar, now_minute: int = 23 * 60) -> dict:
        """recompute_day — SAF yol (DB yok, LLM yok)."""
        return pa.recompute_day(self.template, self.bant, self.wake, loglar,
                                now_minute=now_minute, gun=TODAY,
                                tz_offset_min=TZ)


S8 = Sablon("Elif", 243)          # ≈ 8.0 ay
S14 = Sablon("Deniz", 441)        # ≈ 14.5 ay

_db = SessionLocal()
try:
    _ROW8 = _db.query(Baby).filter(Baby.id == _uuid.UUID(S8.baby_id)).one()
    _ROW14 = _db.query(Baby).filter(Baby.id == _uuid.UUID(S14.baby_id)).one()
    UID, BID8, BID14 = _ROW8.user_id, _ROW8.id, _ROW14.id
finally:
    _db.close()


def L(tip: str, bas_gun, bas_dk: int, bit_gun=None, bit_dk=None,
      baby_id=None) -> SleepLog:
    """DB'ye YAZILMAYAN kayıt nesnesi (recompute_day saf fonksiyon)."""
    return SleepLog(
        id=_uuid.uuid4(), user_id=UID, baby_id=baby_id or BID8, type=tip,
        started_at=utc(bas_gun, bas_dk),
        ended_at=None if bit_dk is None else utc(bit_gun or bas_gun, bit_dk))


def bloklar(sonuc: dict) -> dict:
    return {b["key"]: b for b in sonuc["schedule"]}


def naplar(sonuc: dict) -> list[dict]:
    return [b for b in sonuc["schedule"] if b.get("type") == "nap"]


def sablon_saatleri(tpl) -> dict:
    return {b["key"]: b["start_minute"] for b in pa.normalize_schedule(tpl)}


# =============================================================================
# W1 — night_wake NOKTA kayıtları yuvaya eşlenmez (K12.1 + K12.2)
# =============================================================================
# Gece uykusu şablondaki hedefte bitiyor → gün şablonla AYNI olmalı; araya
# serpiştirilen üç bitişsiz gece uyanması buna DOKUNMAMALI.
w1_loglar = [
    L("sleep", DUN, 21 * 60 + 50, TODAY, S8.wake),        # gece uykusu
    L("night_wake", TODAY, 4 * 60 + 18),                  # ended_at YOK
    L("night_wake", TODAY, 5 * 60),
    L("night_wake", TODAY, 5 * 60 + 30),
]
w1 = S8.hesapla(w1_loglar)
_w1_saat = {b["key"]: b["start_minute"] for b in w1["schedule"]}
_tpl8 = sablon_saatleri(S8.template)

check("W1a) Gece uyanma kayıtları uyku bloğu ÜRETMEDİ",
      len(naplar(w1)) == S8.n_nap,
      f"nap sayısı={len(naplar(w1))} beklenen={S8.n_nap}")
check("W1b) Hiçbir blok gece uyanma saatinden başlamıyor",
      not any(b["start_minute"] in (258, 300, 330) for b in naplar(w1)),
      str([(b["key"], hhmm(b["start_minute"])) for b in naplar(w1)]))
check("W1c) Plan ŞABLONLA AYNI (uyanma kayıtları çizelgeyi bozmadı)",
      _w1_saat == _tpl8, f"hesap={_w1_saat} şablon={_tpl8}")
check("W1d) Gece uyanma sayısı 3 (süre şartı YOK, K12.2)",
      (w1["adaptation"]["gece_uykusu"] or {}).get("uyanma_sayisi") == 3,
      str(w1["adaptation"]["gece_uykusu"]))
check("W1e) Üçü de gece bölünmesi olarak listelendi",
      len(w1["adaptation"]["gece_bolunmeleri"]) == 3,
      str(w1["adaptation"]["gece_bolunmeleri"]))
check("W1f) Sabah uyanışı gece uykusunun bitişi",
      w1["adaptation"]["sabah_uyanis_kaynak"] == "kayit"
      and bloklar(w1)["wake"]["start_minute"] == S8.wake,
      str(w1["adaptation"]["sabah_uyanis_gercek"]))

# Aynı senaryo `wake` tipiyle gönderilirse de uyku olmaz (mobil bazen böyle yolluyor)
w1b = S8.hesapla([L("sleep", DUN, 21 * 60 + 50, TODAY, S8.wake),
                  L("wake", TODAY, 4 * 60 + 18), L("wake", TODAY, 5 * 60)])
check("W1g) 06:00 öncesi `wake` kayıtları da uyku bloğu üretmiyor",
      len(naplar(w1b)) == S8.n_nap, str([b["key"] for b in naplar(w1b)]))
check("W1h) 06:00 öncesi `wake` gece uyanması olarak SAYILDI (K12.2)",
      (w1b["adaptation"]["gece_uykusu"] or {}).get("uyanma_sayisi") == 2,
      str(w1b["adaptation"]["gece_uykusu"]))


# =============================================================================
# W2 — AÇIK gece uykusu + wake 08:00 (K12.3/K12.4 + K15)
# =============================================================================
w2_loglar_8 = [
    L("sleep", DUN, 21 * 60 + 50),                        # ended_at YOK
    L("wake", TODAY, 8 * 60),
]
w2 = S8.hesapla(w2_loglar_8)
check("W2a) Sabah uyanışı 08:00 (varsayılan hedefte KALMADI)",
      bloklar(w2)["wake"]["start_minute"] == 8 * 60,
      hhmm(bloklar(w2)["wake"]["start_minute"]))
check("W2b) Kaynak 'kayit' (varsayilan değil)",
      w2["adaptation"]["sabah_uyanis_kaynak"] == "kayit",
      w2["adaptation"]["sabah_uyanis_kaynak"])
check("W2c) Gün 08:00'dan kuruldu: ilk uyku = 08:00 + pencere",
      naplar(w2)[0]["start_minute"] == 8 * 60 + S8.ww,
      f"{hhmm(naplar(w2)[0]['start_minute'])} beklenen {hhmm(8*60+S8.ww)}")
check("W2d) Açık gece uykusu 08:00'da kapanmış SAYILDI",
      (w2["adaptation"]["gece_uykusu"] or {}).get("acik_mi") is True
      and (w2["adaptation"]["gece_uykusu"] or {}).get("brut_dk")
      == (8 * 60 + 1440) - (21 * 60 + 50),
      str(w2["adaptation"]["gece_uykusu"]))

w2_14 = S14.hesapla([L("sleep", DUN, 21 * 60 + 50, baby_id=BID14),
                     L("wake", TODAY, 8 * 60, baby_id=BID14)])
_ilk14 = naplar(w2_14)[0]["start_minute"]
check("W2e) 14,5 aylık: ilk uyku ≥ 08:00 + minimum pencere (K15)",
      _ilk14 >= 8 * 60 + S14.ww_min,
      f"ilk uyku {hhmm(_ilk14)}, en erken {hhmm(8*60+S14.ww_min)} "
      f"(min pencere {S14.ww_min} dk)")
check("W2f) 14,5 aylık bant: 2 gündüz uykusu (tablo ile tutarlı)",
      S14.n_nap == 2 and S14.bant["gunduz_uyku_sayisi"] == [2, 2],
      f"n_nap={S14.n_nap} tablo={S14.bant.get('gunduz_uyku_sayisi')} "
      f"varyant={S14.bant.get('varyant')}")


# =============================================================================
# W3 — sıfır süreli `sleep` yok sayılır (K12.1)
# =============================================================================
w3 = S8.hesapla([L("sleep", DUN, 21 * 60 + 50, TODAY, S8.wake),
                 L("sleep", TODAY, 9 * 60, TODAY, 9 * 60)])     # 09:00-09:00
_sifir = [y for y in w3["adaptation"]["yok_sayilan_kayitlar"]
          if y.get("kod") == "sifir_sure"]
check("W3a) Sıfır süreli kayıt yok_sayilan_kayitlar'a yazıldı",
      len(_sifir) == 1, str(w3["adaptation"]["yok_sayilan_kayitlar"]))
check("W3b) Sebep metni insan tarafından okunabilir",
      "süresi olmayan" in (_sifir[0]["sebep"] if _sifir else ""),
      str(_sifir))
check("W3c) Sıfır uzunluklu nap bloğu ÜRETİLMEDİ",
      not any(b["start_minute"] == b["end_minute"] for b in naplar(w3)),
      str([(b["key"], hhmm(b["start_minute"]), hhmm(b["end_minute"]))
           for b in naplar(w3)]))
check("W3d) Zincir 09:00'dan BAŞLAMADI — plan şablonla aynı",
      {b["key"]: b["start_minute"] for b in w3["schedule"]} == _tpl8,
      str({b["key"]: hhmm(b["start_minute"]) for b in w3["schedule"]}))

# 5 dk altı kayıt da yuvaya eşlenmez ama izi kalır
w3b = S8.hesapla([L("sleep", DUN, 21 * 60 + 50, TODAY, S8.wake),
                  L("nap", TODAY, 9 * 60, TODAY, 9 * 60 + 3)])
check("W3e) 3 dakikalık uyku kaydı yuvaya eşlenmedi (kod=kisa_sure)",
      any(y.get("kod") == "kisa_sure"
          for y in w3b["adaptation"]["yok_sayilan_kayitlar"]),
      str(w3b["adaptation"]["yok_sayilan_kayitlar"]))


# =============================================================================
# W4 — açık sayaç + kapsayan manuel kayıt = TEK uyku (K13.1/K13.2)
# =============================================================================
w4 = S8.hesapla([L("sleep", DUN, 21 * 60 + 50, TODAY, S8.wake),
                 L("nap", TODAY, 10 * 60 + 1),                  # AÇIK sayaç
                 L("nap", TODAY, 10 * 60, TODAY, 11 * 60 + 49)])  # manuel
_kayit_bloklari = [b for b in naplar(w4) if b.get("kaynak") == "kayit"]
check("W4a) Tek gerçek uyku bloğu (hayalet ikinci uyku YOK)",
      len(_kayit_bloklari) == 1,
      str([(b["key"], hhmm(b["start_minute"]), hhmm(b["end_minute"]),
            b.get("kaynak")) for b in naplar(w4)]))
check("W4b) Blok 10:00-11:49 (manuel kayıt kazandı)",
      bool(_kayit_bloklari)
      and _kayit_bloklari[0]["start_minute"] == 10 * 60
      and _kayit_bloklari[0]["end_minute"] == 11 * 60 + 49,
      str(_kayit_bloklari))
check("W4c) Açık sayaç kaydı 'cakisma' sebebiyle yok sayıldı",
      any(y.get("kod") == "cakisma"
          for y in w4["adaptation"]["yok_sayilan_kayitlar"]),
      str(w4["adaptation"]["yok_sayilan_kayitlar"]))
check("W4d) Toplam nap sayısı şablonu aşmadı",
      len(naplar(w4)) == S8.n_nap, f"{len(naplar(w4))} != {S8.n_nap}")


# =============================================================================
# W5 — POST /logs/batch açık sayacı kapatır (K13.3)
# =============================================================================
_db = SessionLocal()
try:
    _sayac = SleepLog(user_id=UID, baby_id=BID8, type="nap",
                      started_at=utc(TODAY, 10 * 60 + 1), ended_at=None,
                      client_id="timer-w5")
    _db.add(_sayac)
    _db.commit()
    _SAYAC_ID = _sayac.id
finally:
    _db.close()

_batch = client.post("/api/v1/logs/batch", headers=H, json={"logs": [{
    "baby_id": str(BID8), "type": "nap",
    "started_at": utc(TODAY, 10 * 60).isoformat(),
    "ended_at": utc(TODAY, 11 * 60 + 49).isoformat(),
    "client_id": "manuel-w5"}]})
check("W5a) batch 200", _batch.status_code == 200, _batch.text[:200])
check("W5b) Yanıtta timer_closed: true",
      _batch.json().get("timer_closed") is True, str(_batch.json())[:200])

_db = SessionLocal()
try:
    _kapanan = _db.get(SleepLog, _SAYAC_ID)
    _kapanis_dk = (None if _kapanan.ended_at is None else
                   pa._local_minute(_kapanan.ended_at, TZ)[1])
finally:
    _db.close()
check("W5c) Açık sayaç 11:49'da kapandı (DB'de)",
      _kapanis_dk == 11 * 60 + 49,
      f"ended_at dakika={_kapanis_dk} beklenen={11*60+49}")

# İkinci kez aynı batch: kapatacak açık sayaç KALMADI
_batch2 = client.post("/api/v1/logs/batch", headers=H, json={"logs": [{
    "baby_id": str(BID8), "type": "nap",
    "started_at": utc(TODAY, 10 * 60).isoformat(),
    "ended_at": utc(TODAY, 11 * 60 + 49).isoformat(),
    "client_id": "manuel-w5"}]})
check("W5d) Tekrar gönderimde timer_closed false (tek DB yazımı)",
      _batch2.json().get("timer_closed") is False, str(_batch2.json())[:160])

# Örtüşmeyen manuel kayıt açık sayacı KAPATMAZ (5 saatlik hayalet uyku olmasın)
_db = SessionLocal()
try:
    _db.add(SleepLog(user_id=UID, baby_id=BID8, type="nap",
                     started_at=utc(TODAY, 10 * 60 + 1), ended_at=None,
                     client_id="timer-w5b"))
    _db.commit()
finally:
    _db.close()
_batch3 = client.post("/api/v1/logs/batch", headers=H, json={"logs": [{
    "baby_id": str(BID8), "type": "nap",
    "started_at": utc(TODAY, 14 * 60).isoformat(),
    "ended_at": utc(TODAY, 15 * 60).isoformat(),
    "client_id": "manuel-w5b"}]})
check("W5e) Örtüşmeyen (14:00-15:00) kayıt açık sayacı KAPATMADI",
      _batch3.json().get("timer_closed") is False, str(_batch3.json())[:160])

# Temizlik: sonraki senaryolar bu kayıtlardan etkilenmesin
_db = SessionLocal()
try:
    _db.query(SleepLog).filter(SleepLog.baby_id == BID8).delete()
    _db.commit()
finally:
    _db.close()


# =============================================================================
# W6 — gece yarısını aşan gece uykusu + içindeki uyanma (K14.1/K14.2)
# =============================================================================
_db = SessionLocal()
try:
    _db.add_all([
        SleepLog(user_id=UID, baby_id=BID8, type="sleep",
                 started_at=utc(DUN, 21 * 60 + 50),
                 ended_at=utc(TODAY, 7 * 60 + 5)),
        SleepLog(user_id=UID, baby_id=BID8, type="night_wake",
                 started_at=utc(TODAY, 2 * 60), ended_at=utc(TODAY, 2 * 60 + 20)),
    ])
    _db.commit()
finally:
    _db.close()

_gun_listesi = client.get(f"/api/v1/logs?date={TODAY.isoformat()}"
                          f"&baby_id={BID8}", headers=H)
check("W6a) GET /logs?date= 200", _gun_listesi.status_code == 200,
      _gun_listesi.text[:200])
_tipler = [r["type"] for r in _gun_listesi.json()]
check("W6b) Dün başlayıp bugün biten gece uykusu BUGÜNÜN listesinde",
      _tipler.count("sleep") == 1, str(_tipler))
check("W6c) Gece uyanması da bugünün listesinde",
      _tipler.count("night_wake") == 1, str(_tipler))

w6 = S8.hesapla([L("sleep", DUN, 21 * 60 + 50, TODAY, 7 * 60 + 5),
                 L("night_wake", TODAY, 2 * 60, TODAY, 2 * 60 + 20)])
_brut = (7 * 60 + 5 + 1440) - (21 * 60 + 50)
check("W6d) Sabah uyanışı 07:05",
      bloklar(w6)["wake"]["start_minute"] == 7 * 60 + 5,
      hhmm(bloklar(w6)["wake"]["start_minute"]))
_gu = w6["adaptation"]["gece_uykusu"] or {}
check("W6e) Brüt gece süresi doğru", _gu.get("brut_dk") == _brut,
      f"{_gu.get('brut_dk')} != {_brut}")
check("W6f) NET gece süresi = brüt − 20 (K14.2)",
      _gu.get("net_dk") == _brut - 20,
      f"net={_gu.get('net_dk')} brüt={_brut} uyanma={_gu.get('uyanma_toplam_dk')}")
check("W6g) 20 dk'lık uyanma 'uzun uyanma' alt metriğine girdi",
      _gu.get("uzun_uyanma_sayisi") == 1, str(_gu))


# =============================================================================
# W7 — dün başlayan AÇIK gece uykusu bugünün listesinde "sürüyor"
# =============================================================================
_db = SessionLocal()
try:
    _db.query(SleepLog).filter(SleepLog.baby_id == BID8).delete()
    _db.add(SleepLog(user_id=UID, baby_id=BID8, type="sleep",
                     started_at=utc(DUN, 21 * 60 + 50), ended_at=None))
    _db.commit()
finally:
    _db.close()

_w7 = client.get(f"/api/v1/logs?date={TODAY.isoformat()}&baby_id={BID8}",
                 headers=H).json()
check("W7a) Açık gece uykusu BUGÜNÜN listesinde",
      len(_w7) == 1 and _w7[0]["type"] == "sleep", str(_w7)[:200])
check("W7b) 'Sürüyor' olarak dönüyor (ended_at null)",
      bool(_w7) and _w7[0]["ended_at"] is None, str(_w7)[:200])

w7 = S8.hesapla([L("sleep", DUN, 21 * 60 + 50)])
check("W7c) Motor da görüyor: açık gece uykusu kayıt olarak işlendi",
      (w7["adaptation"]["gece_uykusu"] or {}).get("acik_mi") is True,
      str(w7["adaptation"]["gece_uykusu"]))
check("W7d) Uyanış kaydı yokken sabah hedefe düşer (uydurma saat yok)",
      w7["adaptation"]["sabah_uyanis_kaynak"] == "varsayilan",
      w7["adaptation"]["sabah_uyanis_kaynak"])


# =============================================================================
# W8 — K15: min pencereden erken KAYIT kabul edilir ama uyarı üretir
# =============================================================================
w8 = S14.hesapla([L("sleep", DUN, 21 * 60 + 50, baby_id=BID14),
                  L("wake", TODAY, 8 * 60, baby_id=BID14),
                  L("nap", TODAY, 9 * 60, TODAY, 9 * 60 + 40, baby_id=BID14)])
_w8_naplar = naplar(w8)
_w8_kayit = [b for b in _w8_naplar if b.get("kaynak") == "kayit"]
check("W8a) Erken kaydedilen nap KABUL EDİLDİ (silinmedi/kaydırılmadı)",
      len(_w8_kayit) == 1 and _w8_kayit[0]["start_minute"] == 9 * 60
      and _w8_kayit[0]["end_minute"] == 9 * 60 + 40,
      str([(b["key"], hhmm(b["start_minute"]), hhmm(b["end_minute"]),
            b.get("kaynak")) for b in _w8_naplar]))
_uyari_metni = " ".join(w8["adaptation"]["uyarilar"])
check("W8b) 'önerilen en erken saat' uyarısı üretildi",
      "önerilen en erken saat" in _uyari_metni,
      str(w8["adaptation"]["uyarilar"]))
_sonraki = [b for b in _w8_naplar if b["start_minute"] > 9 * 60 + 40]
check("W8c) Sonraki bloklar 09:40'tan (kayıt bitişinden) zincirleniyor",
      bool(_sonraki) and _sonraki[0]["start_minute"] == 9 * 60 + 40 + S14.ww,
      f"sonraki={[(b['key'], hhmm(b['start_minute'])) for b in _sonraki]} "
      f"beklenen={hhmm(9*60+40+S14.ww)}")
check("W8d) adaptation en_erken_uyku alanı bandın min penceresini yansıtıyor",
      w8["adaptation"]["min_uyaniklik_penceresi_dk"] == S14.ww_min
      and w8["adaptation"]["en_erken_uyku"] == hhmm(8 * 60 + S14.ww_min),
      str({k: w8["adaptation"][k]
           for k in ("en_erken_uyku", "min_uyaniklik_penceresi_dk")}))

# K15 örneği: 08:00 uyanış → 09:00 uyku ÜRETİLEMEZ (kayıt yokken)
w8b = S14.hesapla([L("sleep", DUN, 21 * 60 + 50, baby_id=BID14),
                   L("wake", TODAY, 8 * 60, baby_id=BID14)])
check("W8e) Kayıt yokken 08:00 uyanışa 09:00 uyku ÜRETİLMİYOR",
      all(b["start_minute"] >= 8 * 60 + S14.ww_min for b in naplar(w8b)),
      str([(b["key"], hhmm(b["start_minute"])) for b in naplar(w8b)]))


# =============================================================================
# X1 — ÜÇ AÇIK NAP → tek açık, ikisi kapatılmış (K16.2)
# =============================================================================
# Açık kayıtlar bilerek GENİŞ aralıklı: gerçek vakadaki 08:24/08:26 gibi bitişik
# olanlar kapatılınca 2 dakika kalır ve K12.1 onları zaten eler. Burada ölçülen
# şey "üçü birden sürüyor sayılmıyor" kuralı.
X_NAP = S8.cp["uyku_suresi_dk"]
x1_loglar = [
    L("sleep", DUN, 21 * 60 + 50, TODAY, S8.wake),
    L("nap", TODAY, 8 * 60),                     # AÇIK
    L("nap", TODAY, 11 * 60),                    # AÇIK
    L("nap", TODAY, 14 * 60),                    # AÇIK (en yeni)
]
x1 = S8.hesapla(x1_loglar, now_minute=14 * 60 + 10)
_x1_naplar = naplar(x1)
_x1_kayit = [b for b in _x1_naplar if b.get("kaynak") == "kayit"]
check("X1a) Üç açık kayıt → 3 uyku bloğu (fazlası/eksiği yok)",
      len(_x1_kayit) == 3,
      str([(b["key"], hhmm(b["start_minute"]), hhmm(b["end_minute"]),
            b.get("otomatik_kapandi") or ("devam" if b.get("devam") else ""))
           for b in _x1_naplar]))
check("X1b) YALNIZ en yeni kayıt 'sürüyor'",
      sum(1 for b in _x1_kayit if b.get("devam")) == 1
      and _x1_kayit[-1].get("devam") is True,
      str([(b["key"], b.get("devam")) for b in _x1_kayit]))
check("X1c) Önceki ikisi bant süresiyle kapatıldı (08:00→09:10, 11:00→12:10)",
      _x1_kayit[0]["end_minute"] == 8 * 60 + X_NAP
      and _x1_kayit[1]["end_minute"] == 11 * 60 + X_NAP,
      f"{hhmm(_x1_kayit[0]['end_minute'])} / {hhmm(_x1_kayit[1]['end_minute'])} "
      f"(bant süresi {X_NAP} dk)")
check("X1d) Kapatılanlar 'yeni_kayit' gerekçesiyle işaretli",
      all(b.get("otomatik_kapandi") == "yeni_kayit" for b in _x1_kayit[:2]),
      str([b.get("otomatik_kapandi") for b in _x1_kayit]))
check("X1e) 'ilave uyku' notu YOK (yuvalar taşmadı)",
      not any("ilave" in (b.get("note") or "") for b in _x1_naplar),
      str([b.get("note") for b in _x1_naplar]))

# İki açık kayıt bant süresinden yakınsa: K16.2 ilkini sonrakinin başlangıcında
# kapatır, ardından K20 aradaki 0 dk boşluğu görüp ikisini BİRLEŞTİRİR —
# yani "sayacı durdurup hemen yeniden başlattı" tek uyku sayılır.
x1b = S8.hesapla([L("sleep", DUN, 21 * 60 + 50, TODAY, S8.wake),
                  L("nap", TODAY, 8 * 60),
                  L("nap", TODAY, 8 * 60 + 30)], now_minute=9 * 60)
_x1b = [b for b in naplar(x1b) if b.get("kaynak") == "kayit"]
check("X1f) Yakın iki açık kayıt TEK uyku oldu (K16.2 + K20)",
      len(_x1b) == 1 and _x1b[0]["start_minute"] == 8 * 60,
      str([(hhmm(b["start_minute"]), hhmm(b["end_minute"])) for b in _x1b]))
check("X1g) Birleşme adaptation'a yazıldı",
      len(x1b["adaptation"]["birlesen_kayitlar"]) == 1
      and len(x1b["adaptation"]["birlesen_kayitlar"][0]) == 2,
      str(x1b["adaptation"]["birlesen_kayitlar"]))


# =============================================================================
# X2 — BAYAT açık nap (K17.1)
# =============================================================================
x2 = S8.hesapla([L("sleep", DUN, 21 * 60 + 50, TODAY, 6 * 60),
                 L("nap", TODAY, 6 * 60 + 30)], now_minute=14 * 60)
_x2_kayit = [b for b in naplar(x2) if b.get("kaynak") == "kayit"]
check("X2a) 7,5 saattir açık nap 'sürüyor' SAYILMIYOR",
      bool(_x2_kayit) and not _x2_kayit[0].get("devam"),
      str([(b["key"], b.get("devam"), b.get("otomatik_kapandi"))
           for b in _x2_kayit]))
check("X2b) Bitiş = başlangıç + planlanan süre",
      bool(_x2_kayit) and _x2_kayit[0]["end_minute"] == 6 * 60 + 30 + X_NAP,
      f"{hhmm(_x2_kayit[0]['end_minute']) if _x2_kayit else None} "
      f"beklenen {hhmm(6*60+30+X_NAP)}")
check("X2c) 06:30 uykusunun bitişinin girilmediği uyarısı var",
      any("bitişi girilmemiş" in u and "06:30" in u
          for u in x2["adaptation"]["uyarilar"]),
      str(x2["adaptation"]["uyarilar"]))

# Eşiğin ALTINDA olan açık nap hâlâ "sürüyor"
x2b = S8.hesapla([L("sleep", DUN, 21 * 60 + 50, TODAY, 6 * 60),
                  L("nap", TODAY, 13 * 60)], now_minute=13 * 60 + 40)
_x2b = [b for b in naplar(x2b) if b.get("kaynak") == "kayit"]
check("X2d) 40 dakikadır açık nap HÂLÂ sürüyor (eşik altı)",
      bool(_x2b) and _x2b[0].get("devam") is True,
      str([(b["key"], b.get("devam")) for b in _x2b]))
check("X2e) Eşik altındayken 'Sayaç kapatılmadı' uyarısı YOK",
      not any("Sayaç kapatılmadı" in u for u in x2b["adaptation"]["uyarilar"]),
      str(x2b["adaptation"]["uyarilar"]))

# Açık GECE uykusu için sınır 14 saat
x2c = S8.hesapla([L("sleep", DUN, 21 * 60 + 50)], now_minute=13 * 60)
check("X2f) 15 saattir açık gece uykusu → bitiş uyarısı",
      any("bitişi girilmemiş" in u and "gece" in u
          for u in x2c["adaptation"]["uyarilar"]),
      str(x2c["adaptation"]["uyarilar"]))


# =============================================================================
# X3 — aynı client_id iki kez → TEK satır
# =============================================================================
_db = SessionLocal()
try:
    _db.query(SleepLog).filter(SleepLog.baby_id == BID8).delete()
    _db.commit()
finally:
    _db.close()

_x3_govde = {"baby_id": str(BID8), "type": "nap",
             "started_at": utc(TODAY, 9 * 60).isoformat(),
             "ended_at": utc(TODAY, 10 * 60).isoformat(),
             "client_id": "x3-ayni"}
_r1 = client.post("/api/v1/logs/batch", headers=H, json={"logs": [_x3_govde]})
_r2 = client.post("/api/v1/logs/batch", headers=H, json={"logs": [_x3_govde]})
_x3_id1 = _r1.json()["logs"][0]["id"]
_x3_id2 = _r2.json()["logs"][0]["id"]
_db = SessionLocal()
try:
    _x3_n = (_db.query(SleepLog)
             .filter(SleepLog.baby_id == BID8,
                     SleepLog.client_id == "x3-ayni").count())
finally:
    _db.close()
check("X3a) Aynı client_id iki kez → TEK satır", _x3_n == 1, f"satır={_x3_n}")
check("X3b) İkinci yanıtta AYNI id döndü", _x3_id1 == _x3_id2,
      f"{_x3_id1} vs {_x3_id2}")
check("X3c) İkinci istek 'created' değil 'updated' saydı",
      _r2.json()["created"] == 0 and _r2.json()["updated"] == 1,
      str(_r2.json())[:160])


# =============================================================================
# X4 — client_id YOK, ±2 dk kopya → TEK satır (K18.2)
# =============================================================================
_db = SessionLocal()
try:
    _db.query(SleepLog).filter(SleepLog.baby_id == BID8).delete()
    _db.commit()
finally:
    _db.close()

client.post("/api/v1/logs/batch", headers=H, json={"logs": [{
    "baby_id": str(BID8), "type": "nap",
    "started_at": utc(TODAY, 8 * 60 + 24).isoformat(),
    "ended_at": utc(TODAY, 9 * 60 + 34).isoformat()}]})
_r4 = client.post("/api/v1/logs/batch", headers=H, json={"logs": [{
    "baby_id": str(BID8), "type": "nap",
    "started_at": utc(TODAY, 8 * 60 + 26).isoformat(),
    "ended_at": utc(TODAY, 9 * 60 + 36).isoformat()}]})
_db = SessionLocal()
try:
    _x4 = (_db.query(SleepLog).filter(SleepLog.baby_id == BID8,
                                      SleepLog.type == "nap").all())
    _x4_bilgi = [(pa._local_minute(r.started_at, TZ)[1],
                  pa._local_minute(r.ended_at, TZ)[1]) for r in _x4]
finally:
    _db.close()
check("X4a) ±2 dk kopya YENİ satır açmadı", len(_x4) == 1,
      f"satır={len(_x4)} {_x4_bilgi}")
check("X4b) Mevcut satır GÜNCELLENDİ (08:26-09:36)",
      _x4_bilgi == [(8 * 60 + 26, 9 * 60 + 36)], str(_x4_bilgi))
check("X4c) Yanıt 'updated' saydı", _r4.json()["updated"] == 1,
      str(_r4.json())[:160])

# ±3 dk penceresinin DIŞI ayrı kayıttır
client.post("/api/v1/logs/batch", headers=H, json={"logs": [{
    "baby_id": str(BID8), "type": "nap",
    "started_at": utc(TODAY, 8 * 60 + 40).isoformat(),
    "ended_at": utc(TODAY, 9 * 60 + 50).isoformat()}]})
_db = SessionLocal()
try:
    _x4b = _db.query(SleepLog).filter(SleepLog.baby_id == BID8,
                                      SleepLog.type == "nap").count()
finally:
    _db.close()
check("X4d) 14 dk sonraki kayıt AYRI satır (pencere dışı)", _x4b == 2,
      f"satır={_x4b}")


# =============================================================================
# X5 — açık nap + wake 07:15 → nap 07:15'te kapanır (K17.2)
# =============================================================================
x5 = S8.hesapla([L("sleep", DUN, 21 * 60 + 50, TODAY, 6 * 60),
                 L("nap", TODAY, 6 * 60 + 30),           # AÇIK
                 L("wake", TODAY, 7 * 60 + 15)],
                now_minute=7 * 60 + 30)
_x5 = [b for b in naplar(x5) if b.get("kaynak") == "kayit"]
check("X5a) Açık nap uyanma kaydıyla kapandı",
      bool(_x5) and not _x5[0].get("devam")
      and _x5[0].get("otomatik_kapandi") == "wake",
      str([(b["key"], hhmm(b["start_minute"]), hhmm(b["end_minute"]),
            b.get("otomatik_kapandi"), b.get("devam")) for b in _x5]))
check("X5b) Bitiş tam 07:15",
      bool(_x5) and _x5[0]["end_minute"] == 7 * 60 + 15,
      hhmm(_x5[0]["end_minute"]) if _x5 else None)
check("X5c) Uyarı üretildi (anne bitişi girmemişti)",
      any("uyanma kaydınıza göre" in u for u in x5["adaptation"]["uyarilar"]),
      str(x5["adaptation"]["uyarilar"]))


# =============================================================================
# X6 — toplam uyku açık kayıtta None/NaN üretmez, ŞİMDİYE kadarki süre sayılır
# =============================================================================
_x6_simdi = 13 * 60 + 20                      # nap 13:00'te başladı, 20 dk oldu
x6 = S8.hesapla([L("sleep", DUN, 21 * 60 + 50, TODAY, S8.wake),
                 L("nap", TODAY, 13 * 60)], now_minute=_x6_simdi)
_x6_gunduz, _x6_gece = pa.gun_uyku_toplamlari(x6["schedule"], _x6_simdi)
_x6_gunduz_ham, _ = pa.gun_uyku_toplamlari(x6["schedule"])
check("X6a) Toplam sayı döndü (None/NaN değil)",
      isinstance(_x6_gunduz, int) and _x6_gunduz == _x6_gunduz,
      f"gunduz={_x6_gunduz!r} gece={_x6_gece!r}")
check("X6b) Süren uyku ŞİMDİYE kadarki süresiyle sayıldı (planlanan değil)",
      _x6_gunduz < _x6_gunduz_ham,
      f"now'lu={_x6_gunduz} now'suz={_x6_gunduz_ham} (fark {X_NAP - 20} dk olmalı)")
_x6_devam = [b for b in naplar(x6) if b.get("devam")]
check("X6c) Süren blok 'devam' bayrağı taşıyor",
      len(_x6_devam) == 1, str([(b["key"], b.get("devam")) for b in naplar(x6)]))
check("X6d) Süren bloğun katkısı tam 20 dk",
      _x6_gunduz_ham - _x6_gunduz == X_NAP - 20,
      f"fark={_x6_gunduz_ham - _x6_gunduz} beklenen={X_NAP - 20}")
check("X6e) Bozuk blok toplamı çökertmiyor",
      pa.gun_uyku_toplamlari(
          [{"type": "nap", "start_minute": None, "end_minute": 10},
           {"type": "nap", "start_minute": 0, "end_minute": 30}], 100) == (30, 0),
      str(pa.gun_uyku_toplamlari(
          [{"type": "nap", "start_minute": None, "end_minute": 10},
           {"type": "nap", "start_minute": 0, "end_minute": 30}], 100)))

# Temizlik
_db = SessionLocal()
try:
    _db.query(SleepLog).filter(SleepLog.baby_id == BID8).delete()
    _db.commit()
finally:
    _db.close()


# =============================================================================
# Y1-Y5 — K19: uyku tipi SAATE göre belirlenir, type'a göre DEĞİL
# =============================================================================
def sinif(bas_dk, bit_dk=None, gun_ofset=0):
    """uyku_sinifi_ham — ORM/ham datetime yolu (GET /logs bunu kullanıyor)."""
    g = TODAY + timedelta(days=gun_ofset)
    return pa.uyku_sinifi_ham(utc(g, bas_dk),
                              None if bit_dk is None else utc(g, bit_dk), TZ)


check("Y1) sleep 09:50-10:40 → gündüz uykusu",
      sinif(9 * 60 + 50, 10 * 60 + 40) == pa.GUNDUZ_UYKUSU,
      sinif(9 * 60 + 50, 10 * 60 + 40))
check("Y2) sleep 20:30 AÇIK → gece uykusu",
      sinif(20 * 60 + 30) == pa.GECE_UYKUSU, sinif(20 * 60 + 30))
check("Y3) `nap` tipiyle 21:00 → gece uykusu (type sınıfı belirlemez)",
      sinif(21 * 60, 22 * 60) == pa.GECE_UYKUSU, sinif(21 * 60, 22 * 60))
# v1.4 — GÜNDÜZ SINIRI 17:00 → 19:00 (İlayda: "17 çok erken gece uykusu
# için; gece uykusu için 19:00 ve sonrasını baz almamız gerekiyor").
# 18:00 artık İSTİSNA DEĞİL, düpedüz gündüz: süresine de açık/kapalı
# olmasına da bakılmaz.
check("Y4) sleep 18:00-19:00 (60 dk) → gündüz (19:00 öncesi)",
      sinif(18 * 60, 19 * 60) == pa.GUNDUZ_UYKUSU, sinif(18 * 60, 19 * 60))
check("Y4b) 18:00 başlayıp 120 dk süren → gündüz (19:00 öncesi, süre önemsiz)",
      sinif(18 * 60, 20 * 60) == pa.GUNDUZ_UYKUSU, sinif(18 * 60, 20 * 60))
check("Y4c) 18:00 AÇIK kayıt → gündüz (19:00 öncesi)",
      sinif(18 * 60) == pa.GUNDUZ_UYKUSU, sinif(18 * 60))

# --- 19:00 SONRASI: kısa+kapalı istisnası, eşik BANDA bağlı ----------------
check("Y4d) 19:30-20:00 (30 dk, kapalı) → gündüz kısa uykusu",
      sinif(19 * 60 + 30, 20 * 60) == pa.GUNDUZ_UYKUSU,
      sinif(19 * 60 + 30, 20 * 60))
check("Y4e) 19:30 başlayıp 90 dk süren → gece (eşiği aştı)",
      sinif(19 * 60 + 30, 21 * 60) == pa.GECE_UYKUSU,
      sinif(19 * 60 + 30, 21 * 60))
check("Y4f) 19:30 AÇIK kayıt → gece (süre bilinmiyorsa istisna yok)",
      sinif(19 * 60 + 30) == pa.GECE_UYKUSU, sinif(19 * 60 + 30))
check("Y4g) 19:00 tam sınırı → gece tarafında",
      sinif(19 * 60, 21 * 60) == pa.GECE_UYKUSU, sinif(19 * 60, 21 * 60))

# Eşik YAŞA bağlı: 50 dk'lık 20:00 kaydı 6 ay+ için gündüz (50<60),
# 6 ay altı için gece (50>45).
_b8ay = yas_bantlari.yas_bandi_getir(8)
_b4ay = yas_bantlari.yas_bandi_getir(4)
_k50 = {"bas_dk": 20 * 60, "bit_dk": 20 * 60 + 50, "sure_dk": 50,
        "bas_gun": TODAY, "bit_gun": TODAY}
check("Y4h) Kısa uyku eşiği bandan geliyor (6 ay+ = 60, 6 ay altı = 45)",
      pa.kisa_uyku_esigi(_b8ay) == 60 and pa.kisa_uyku_esigi(_b4ay) == 45
      and pa.kisa_uyku_esigi(None) == 60,
      f"{pa.kisa_uyku_esigi(_b8ay)} / {pa.kisa_uyku_esigi(_b4ay)}")
check("Y4i) 20:00'de 50 dk: 8 aylıkta gündüz, 4 aylıkta gece",
      pa.uyku_tipi_belirle(_k50, _b8ay) == pa.GUNDUZ_UYKUSU
      and pa.uyku_tipi_belirle(_k50, _b4ay) == pa.GECE_UYKUSU,
      f"{pa.uyku_tipi_belirle(_k50, _b8ay)} / {pa.uyku_tipi_belirle(_k50, _b4ay)}")
check("Y4j) Gece yarısından sonra istisna YOK (02:00'de 30 dk → gece)",
      sinif(2 * 60, 2 * 60 + 30) == pa.GECE_UYKUSU, sinif(2 * 60, 2 * 60 + 30))
check("Y5) `sekerleme` tipi 13:00-13:30 → gündüz uykusu",
      sinif(13 * 60, 13 * 60 + 30) == pa.GUNDUZ_UYKUSU,
      sinif(13 * 60, 13 * 60 + 30))
check("Y6) 05:30 başlayan uyku → gece (06:00 öncesi)",
      sinif(5 * 60 + 30, 7 * 60) == pa.GECE_UYKUSU, sinif(5 * 60 + 30, 7 * 60))
check("Y7) Gece yarısını AŞAN kayıt her koşulda gece",
      pa.uyku_sinifi_ham(utc(TODAY, 16 * 60),
                         utc(TODAY + timedelta(days=1), 2 * 60), TZ)
      == pa.GECE_UYKUSU, "")

# `sekerleme` tipi motor içinde de normal gündüz uykusu gibi işlenir
y5 = S8.hesapla([L("sleep", DUN, 21 * 60 + 50, TODAY, S8.wake),
                 L("sekerleme", TODAY, 13 * 60, TODAY, 13 * 60 + 30)])
check("Y5b) `sekerleme` kaydı çizelgede gündüz uykusu bloğu oldu",
      any(b.get("kaynak") == "kayit" and b["start_minute"] == 13 * 60
          for b in naplar(y5)),
      str([(b["key"], hhmm(b["start_minute"]), b.get("kaynak"))
           for b in naplar(y5)]))

# GET /logs yanıtı kategori + etiket taşıyor (K19.2)
_db = SessionLocal()
try:
    _db.query(SleepLog).filter(SleepLog.baby_id == BID8).delete()
    _db.add_all([
        SleepLog(user_id=UID, baby_id=BID8, type="sleep",
                 started_at=utc(TODAY, 9 * 60 + 50),
                 ended_at=utc(TODAY, 10 * 60 + 40)),
        SleepLog(user_id=UID, baby_id=BID8, type="nap",
                 started_at=utc(TODAY, 21 * 60), ended_at=None),
        SleepLog(user_id=UID, baby_id=BID8, type="feed",
                 started_at=utc(TODAY, 12 * 60), ended_at=None),
    ])
    _db.commit()
finally:
    _db.close()
_gl = client.get(f"/api/v1/logs?date={TODAY.isoformat()}&baby_id={BID8}",
                 headers=H).json()
_kat = {r["type"]: (r.get("kategori"), r.get("kategori_etiket")) for r in _gl}
check("Y8) GET /logs: 09:50 sleep → kategori gunduz_uykusu + etiket",
      _kat.get("sleep") == ("gunduz_uykusu", "Gündüz uykusu"), str(_kat))
check("Y9) GET /logs: 21:00 nap → kategori gece_uykusu + etiket",
      _kat.get("nap") == ("gece_uykusu", "Gece uykusu"), str(_kat))
check("Y10) GET /logs: `feed` kaydında kategori YOK (uyku değil)",
      _kat.get("feed") == (None, None), str(_kat))


# =============================================================================
# Z1-Z3 — K20: parça kayıt birleştirme
# =============================================================================
GECE_TABAN = L("sleep", DUN, 21 * 60 + 50, TODAY, S8.wake)

z1 = S8.hesapla([GECE_TABAN,
                 L("nap", TODAY, 14 * 60, TODAY, 14 * 60 + 36),
                 L("nap", TODAY, 14 * 60 + 36, TODAY, 15 * 60 + 46)])
_z1 = [b for b in naplar(z1) if b.get("kaynak") == "kayit"]
check("Z1a) Bitişik iki kayıt TEK uyku oldu", len(_z1) == 1,
      str([(hhmm(b["start_minute"]), hhmm(b["end_minute"])) for b in _z1]))
check("Z1b) Birleşik uyku 14:00-15:46",
      bool(_z1) and _z1[0]["start_minute"] == 14 * 60
      and _z1[0]["end_minute"] == 15 * 60 + 46,
      str([(hhmm(b["start_minute"]), hhmm(b["end_minute"])) for b in _z1]))
check("Z1c) birlesen_kayitlar'da bir çift var",
      len(z1["adaptation"]["birlesen_kayitlar"]) == 1
      and len(z1["adaptation"]["birlesen_kayitlar"][0]) == 2,
      str(z1["adaptation"]["birlesen_kayitlar"]))

z2 = S8.hesapla([GECE_TABAN,
                 L("nap", TODAY, 8 * 60 + 26, TODAY, 9 * 60 + 36),
                 L("sleep", TODAY, 9 * 60 + 50, TODAY, 11 * 60)])
_z2 = [b for b in naplar(z2) if b.get("kaynak") == "kayit"]
check("Z2a) 14 dk boşluklu iki kayıt TEK uyku oldu", len(_z2) == 1,
      str([(hhmm(b["start_minute"]), hhmm(b["end_minute"])) for b in _z2]))
check("Z2b) Birleşik uyku 08:26-11:00",
      bool(_z2) and _z2[0]["start_minute"] == 8 * 60 + 26
      and _z2[0]["end_minute"] == 11 * 60,
      str([(hhmm(b["start_minute"]), hhmm(b["end_minute"])) for b in _z2]))

# --- Z3 — v1.4: eşik İKİYE ayrıldı (İlayda S4) -------------------------------
# İlk parça bandın kisa_uyku_esigi_dk'sının ALTINDA kaldıysa anne hedef süreyi
# tutturmak için uğraşır → boşluk 45 dk'ya kadar AYNI uyku. İlk parça TAM bir
# uykuysa yalnız 15 dk. S8 = 8 aylık → kısa uyku eşiği 60 dk.
_KISA_ESIK = pa.kisa_uyku_esigi(S8.bant)
check("Z3.0) 8 aylık bandın kısa uyku eşiği 60 dk", _KISA_ESIK == 60,
      str(_KISA_ESIK))

z3 = S8.hesapla([GECE_TABAN,
                 L("nap", TODAY, 9 * 60, TODAY, 9 * 60 + 40),      # 40 dk: KISA
                 L("nap", TODAY, 10 * 60, TODAY, 10 * 60 + 30)])   # 20 dk boşluk
_z3 = [b for b in naplar(z3) if b.get("kaynak") == "kayit"]
check("Z3a) KISA ilk parça + 20 dk boşluk → TEK uyku (45 dk eşiği)",
      len(_z3) == 1 and _z3[0]["start_minute"] == 9 * 60
      and _z3[0]["end_minute"] == 10 * 60 + 30,
      str([(hhmm(b["start_minute"]), hhmm(b["end_minute"])) for b in _z3]))
check("Z3b) Birleştirme izi düşüldü",
      len(z3["adaptation"]["birlesen_kayitlar"]) == 1,
      str(z3["adaptation"]["birlesen_kayitlar"]))

z3b = S8.hesapla([GECE_TABAN,
                  L("nap", TODAY, 9 * 60, TODAY, 9 * 60 + 40),     # 40 dk: KISA
                  L("nap", TODAY, 10 * 60 + 30, TODAY, 11 * 60)])  # 50 dk boşluk
_z3b = [b for b in naplar(z3b) if b.get("kaynak") == "kayit"]
check("Z3c) KISA ilk parça + 50 dk boşluk → İKİ ayrı uyku (45 dk aşıldı)",
      len(_z3b) == 2,
      str([(hhmm(b["start_minute"]), hhmm(b["end_minute"])) for b in _z3b]))

z3c = S8.hesapla([GECE_TABAN,
                  L("nap", TODAY, 9 * 60, TODAY, 10 * 60 + 10),    # 70 dk: TAM
                  L("nap", TODAY, 10 * 60 + 30, TODAY, 11 * 60)])  # 20 dk boşluk
_z3c = [b for b in naplar(z3c) if b.get("kaynak") == "kayit"]
check("Z3d) TAM ilk uyku + 20 dk boşluk → İKİ ayrı uyku (eşik 15 dk)",
      len(_z3c) == 2,
      str([(hhmm(b["start_minute"]), hhmm(b["end_minute"])) for b in _z3c]))

z3d = S8.hesapla([GECE_TABAN,
                  L("nap", TODAY, 9 * 60, TODAY, 10 * 60 + 10),    # 70 dk: TAM
                  L("nap", TODAY, 10 * 60 + 22, TODAY, 11 * 60)])  # 12 dk boşluk
_z3d = [b for b in naplar(z3d) if b.get("kaynak") == "kayit"]
check("Z3e) TAM ilk uyku + 12 dk boşluk → TEK uyku (15 dk içinde)",
      len(_z3d) == 1,
      str([(hhmm(b["start_minute"]), hhmm(b["end_minute"])) for b in _z3d]))

check("Z3f) İki eşik de adaptation'da raporlanıyor",
      z3c["adaptation"]["parca_birlestirme_dk"] == pa.PARCA_BIRLESTIRME_DK == 15
      and z3c["adaptation"]["parca_birlestirme_kisa_dk"]
      == pa.PARCA_BIRLESTIRME_KISA_DK == 45,
      f'{z3c["adaptation"].get("parca_birlestirme_dk")} / '
      f'{z3c["adaptation"].get("parca_birlestirme_kisa_dk")}')
check("Z3g) _parca_esigi: kısa parça 45, tam parça 15",
      pa._parca_esigi({"sure_dk": 40}, _KISA_ESIK) == 45
      and pa._parca_esigi({"sure_dk": 70}, _KISA_ESIK) == 15
      and pa._parca_esigi({"sure_dk": None}, _KISA_ESIK) == 15,
      "")


# =============================================================================
# Z4 — GERÇEK VAKA: Ahmet Kerem (36498964…), 6,3 aylık, 2026-09-19
# =============================================================================
# Kayıtlar prod'dan birebir alındı (saatler yerel). Beklenen: bandın öngördüğü
# 3 gündüz uykusu ve "şablonda olmayan ilave uyku" notu YOK.
S6 = Sablon("Ahmet Kerem", 192)          # ≈ 6,3 ay → 6-8 ay bandı, 3 uyku
_db = SessionLocal()
try:
    BID6 = _db.query(Baby).filter(Baby.id == _uuid.UUID(S6.baby_id)).one().id
finally:
    _db.close()

z4_loglar = [
    L("sleep", DUN, 14 * 60 + 20, TODAY, 8 * 60 + 25, baby_id=BID6),   # gece
    L("sleep", TODAY, 4 * 60 + 40, TODAY, 4 * 60 + 40, baby_id=BID6),  # sıfır
    L("sleep", TODAY, 6 * 60 + 30, TODAY, 7 * 60 + 15, baby_id=BID6),  # gece içi
    L("nap", TODAY, 8 * 60 + 26, TODAY, 9 * 60 + 36, baby_id=BID6),
    L("sleep", TODAY, 9 * 60 + 50, TODAY, 11 * 60, baby_id=BID6),
    L("sleep", TODAY, 14 * 60, TODAY, 14 * 60 + 36, baby_id=BID6),
    L("sleep", TODAY, 14 * 60 + 36, TODAY, 15 * 60 + 46, baby_id=BID6),
    L("sleep", TODAY, 16 * 60 + 55, TODAY, 18 * 60 + 5, baby_id=BID6),
    L("sleep", TODAY, 20 * 60 + 45, TODAY + timedelta(days=1), 7 * 60 + 45,
      baby_id=BID6),
]
z4 = S6.hesapla(z4_loglar)
_z4 = naplar(z4)
check("Z4a) Bant 6-8 ay, 3 gündüz uykusu bekleniyor",
      S6.n_nap == 3 and S6.bant["id"] == "6-8_ay",
      f"bant={S6.bant.get('id')} n_nap={S6.n_nap}")
check("Z4b) TAM 3 gündüz uykusu üretildi (önce 5-6 görünüyordu)",
      len(_z4) == 3,
      str([(b["key"], hhmm(b["start_minute"]), hhmm(b["end_minute"]))
           for b in _z4]))
check("Z4c) 'Şablonda olmayan ilave uyku' notu YOK",
      not any("ilave" in (b.get("note") or "") for b in _z4),
      str([b.get("note") for b in _z4]))
check("Z4d) Üçü de gerçek kayıttan geldi",
      all(b.get("kaynak") == "kayit" for b in _z4),
      str([(b["key"], b.get("kaynak")) for b in _z4]))
check("Z4e) 06:30 parçası gece uykusunun içinde sayıldı",
      any(y.get("kod") == "gece_icinde"
          for y in z4["adaptation"]["yok_sayilan_kayitlar"]),
      str(z4["adaptation"]["yok_sayilan_kayitlar"]))
check("Z4f) İki parça çifti birleştirildi (08:26+09:50, 14:00+14:36)",
      len(z4["adaptation"]["birlesen_kayitlar"]) == 2,
      str(z4["adaptation"]["birlesen_kayitlar"]))
check("Z4g) Sabah uyanışı gece uykusunun bitişi (08:25)",
      z4["adaptation"]["sabah_uyanis_gercek"] == "08:25",
      z4["adaptation"]["sabah_uyanis_gercek"])
_z4g, _z4ge = pa.gun_uyku_toplamlari(z4["schedule"], 23 * 60)
check("Z4h) Toplam uyku hesaplandı (None/NaN yok)",
      isinstance(_z4g, int) and _z4g > 0, f"gunduz={_z4g} gece={_z4ge}")

_db = SessionLocal()
try:
    _db.query(SleepLog).filter(SleepLog.baby_id == BID8).delete()
    _db.commit()
finally:
    _db.close()


# =============================================================================
# Mühür kontrolü — bu suite CANLI LLM çağırmadı
# =============================================================================
check("Z) Canlı LLM çağrısı YOK (deterministik suite)",
      canli_cagri_sayisi() == 0, f"canlı çağrı={canli_cagri_sayisi()}")


# =============================================================================
# RAPOR
# =============================================================================
print("\n" + "=" * 74)
print("KAYIT SEMANTİĞİ v2.2 — K12/K13/K14/K15 (W1-W8)")
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
