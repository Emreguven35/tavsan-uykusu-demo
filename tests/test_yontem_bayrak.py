"""
Yöntem tablosu bayrakları — YONTEM_TABLOSU, SOR_KURAL_BLOGU, SOR_SAYI_DOGRULAMA.

  C  Gece kartı v2 (GET /plans/today): bayrak kapalıyken kart aynen; açıkken
     build 27 ve sürümsüz istekte alan YOK, build 28'de baslik + adimlar;
     resist_minutes/routine_minutes aynen; bekleyen alanlar (ilk bekleme, gece
     içi artış, kapı aşamasında temas) için sayı yok
  N  Bildirim: açıkken gece rutin 15 dk önce ("yatışa 15 dakika var"), 30 dk
     önce gitmez; gündüz 30 dk aynen; kapalıyken gece 30 dk
  K  Kural bloğu: gün 2 gece / gün 5 / gün 13 içerikleri; eğitim dışı → None
  V  Doğrulama: rapor cümleleriyle rol bazlı ihlal; saat; türeyen dakika;
     düzeltme (karşılıkla değiştir / çıkar)
  F  Sohbet akışı (sahte model): gecti / yeniden / duzeltildi; düzeltme notu
     modele gider; her LLM çağrısı maliyete yazılır; log içeriksiz

Çalıştırma: python tests/test_yontem_bayrak.py
"""
import logging
import os
import re
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "yontem_bayrak_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["BETA_MODE"] = "true"
BAYRAKLAR = ("YONTEM_TABLOSU", "SOR_KURAL_BLOGU", "SOR_SAYI_DOGRULAMA",
             "SOR_BAGLAM_V2", "SOR_GECMIS")
for _b in BAYRAKLAR:
    os.environ.pop(_b, None)

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.main import app                                    # noqa: E402
from api.models import ApiUsage, Baby, SleepLog, SleepPlan, User  # noqa: E402
from api.services import notifier, plan_adapter, sor_baglam, sor_kural  # noqa: E402
from api.services.security import hash_password             # noqa: E402
from api.zaman import TR, bugun_tr, saat_sabitle            # noqa: E402
from engine import chatbot                                  # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, muhurle            # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


def bayrak(**acik):
    for ad in BAYRAKLAR:
        if acik.get(ad):
            os.environ[ad] = "1"
        else:
            os.environ.pop(ad, None)


BUGUN = bugun_tr()
_n = [0]


def hesap(gun_yas=460, egitim_gunu_bugun: int | None = 2):
    _n[0] += 1
    tok = client.post("/api/v1/auth/register", json={
        "email": f"yb{_n[0]}@gercek.com", "password": "TestPass123!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    bid = client.post("/api/v1/babies", headers=h, json={
        "name": "Ada", "night_wakes": 3,
        "birth_date": (BUGUN - timedelta(days=gun_yas)).isoformat(), **TAM_PROFIL}).json()["id"]
    r = client.post("/api/v1/plans/generate?sync=true", headers=h, json={"baby_id": bid})
    assert r.status_code in (200, 201), r.text[:200]
    db = SessionLocal()
    b = db.get(Baby, uuid.UUID(bid))
    b.training_started_at = (BUGUN - timedelta(days=egitim_gunu_bugun - 1)
                             if egitim_gunu_bugun else None)
    db.commit()
    db.close()
    return h, bid


def bugunku(h, bid, surum=None):
    hh = dict(h)
    if surum:
        hh["X-App-Version"] = surum
    r = client.get(f"/api/v1/plans/today?baby_id={bid}", headers=hh)
    assert r.status_code == 200, r.text[:200]
    return r.json()["content"]


# =============================================================================
# C — gece kartı v2
# =============================================================================
H, BID = hesap(egitim_gunu_bugun=2)
bayrak()
eski = bugunku(H, BID, "1.0.0+28")
check("C) Bayrak kapalı: kart saklanan hâliyle (adimlar/baslik yok)",
      eski.get("night_wake_protocol") == plan_adapter.NIGHT_WAKE_PROTOCOL, eski.get("night_wake_protocol"))
bayrak(YONTEM_TABLOSU=True)
c27 = bugunku(H, BID, "1.0.0+27")
check("C) Build 27: night_wake_protocol alanı YOK", "night_wake_protocol" not in c27)
H2, BID2 = hesap(egitim_gunu_bugun=2)
c_yok = bugunku(H2, BID2)              # sürüm hiç bilinmiyor
check("C) Sürümü bilinmeyen istek: alan YOK", "night_wake_protocol" not in c_yok)
c28 = bugunku(H, BID, "1.0.0+28")
k = c28.get("night_wake_protocol") or {}
check("C) Build 28: baslik 'GECE UYANIRSA · Beşik yanı'", k.get("baslik") == "GECE UYANIRSA · Beşik yanı", k)
check("C) Build 28: resist_minutes / routine_minutes aynen",
      k.get("resist_minutes") == 45 and k.get("routine_minutes") == 15, k)
adim = [a["metin"] for a in k.get("adimlar") or []]
check("C) Adımlar: dışarıda bekle → içeride 45 dk → 15 dk rutin (beslenme yok) → 3 kez → destekle uyut",
      len(adim) == 5 and "dışarıda bekleyin" in adim[0] and "45 dakika" in adim[1]
      and "15 dakikalık" in adim[2] and "beslenme olmasın" in adim[2]
      and "en fazla 3 kez" in adim[3] and "destek vererek uyutun" in adim[4], adim)
check("C) İlk bekleme (bekleyen) için sayı YOK", not re.search(r"\d", adim[0]), adim[0])
check("C) Beşik yanı yöntemi: pat-pat, histerik değilse kucak yok",
      "pat-pat" in adim[1] and "kucağa almayın" in adim[1], adim[1])
check("C) Saklanan plan DEĞİŞMEDİ (okuma anında)",
      (lambda db: (db.query(SleepPlan).filter(SleepPlan.baby_id == uuid.UUID(BID)).first()
                   .content["night_wake_protocol"]))(SessionLocal()) == plan_adapter.NIGHT_WAKE_PROTOCOL)
H3, BID3 = hesap(egitim_gunu_bugun=8)             # kapı aşaması
k8 = bugunku(H3, BID3, "1.0.0+30")["night_wake_protocol"]
check("C) Kapı aşaması (temas kuralı bekliyor): başlık 'Kapı', yöntemde sayı yok, temas tarifi yok",
      k8["baslik"] == "GECE UYANIRSA · Kapı"
      and k8["adimlar"][1]["metin"] == "Sonra içeri girin, bu aşamanın yöntemiyle 45 dakika uyutmayı deneyin."
      and not re.search(r"\d", k8["adimlar"][0]["metin"]), k8["adimlar"][:2])
H4, BID4 = hesap(egitim_gunu_bugun=5)
k5 = bugunku(H4, BID4, "1.0.0+28")["night_wake_protocol"]
check("C) Oda ortası: temassız pış-pış", k5["baslik"].endswith("Oda ortası")
      and "temassız" in k5["adimlar"][1]["metin"], k5["adimlar"][1])
bayrak()

# =============================================================================
# N — bildirim zamanı (gece 15, gündüz 30)
# =============================================================================
TZ = plan_adapter.TZ_OFFSET_MIN
SCHEDULE = plan_adapter.build_schedule({
    "uyaniklik_penceresi": {"RESMI_DEGER_genel_kullanim": "2.5 - 3.5 Saat"},
    "uyku_sayisi": {"RESMI_DEGER": "2-3"}, "gunduz_uyku_total": "2.5-3.5 Saat",
    "yatma_vakti": "18:00 - 20:00"}, 7 * 60)
GONDERILEN: list[dict] = []
notifier.send_expo_push = lambda ms: (GONDERILEN.extend(ms), [{"status": "ok"} for _ in ms])[1]
ndb = SessionLocal()


def utc_at(h, m, day):
    return (datetime(2026, 8, day, tzinfo=timezone.utc) + timedelta(hours=h, minutes=m)
            - timedelta(minutes=TZ))


def bildirim_kullanicisi(eposta, day):
    from api.models import PushToken
    u = User(email=eposta, password_hash=hash_password("GucluParola123!"), app_version="1.0.0+28")
    ndb.add(u); ndb.commit(); ndb.refresh(u)
    b = Baby(user_id=u.id, name="Can")
    ndb.add(b); ndb.commit(); ndb.refresh(b)
    ndb.add(SleepPlan(user_id=u.id, baby_id=b.id, plan_date=date(2026, 8, day),
                      content={"schedule": SCHEDULE}))
    tok = f"ExponentPushToken[{eposta}]"
    ndb.add(PushToken(user_id=u.id, expo_token=tok, platform="ios",
                      last_seen_at=datetime.now(timezone.utc)))
    ndb.add(SleepLog(user_id=u.id, baby_id=b.id, type="wake", started_at=utc_at(7, 0, day)))
    ndb.commit()
    return u, b, tok


def tur(day, dk):
    return notifier.run_reminder_cycle(ndb, now=utc_at(dk // 60, dk % 60, day))


def blok(u, b, day, key):
    from api.services import plan_service
    ndb.expire_all()
    p = plan_service.plan_for_date(ndb, u, b, date(2026, 8, day))
    return next(x["start_minute"] for x in plan_adapter.normalize_schedule(p.content["schedule"])
                if x.get("key") == key)


def gelen(tok, key):
    return [m for m in GONDERILEN if m.get("to") == tok
            and (m.get("data") or {}).get("block_key") == key and (m.get("data") or {}).get("adim") == "once"]


for etiket, acik, day in (("kapalı", False, 20), ("açık", True, 21)):
    bayrak(YONTEM_TABLOSU=acik)
    u, b, tok = bildirim_kullanicisi(f"n{day}@tavsansmoke.com", day)
    tur(day, 7 * 60 + 30)
    yat, nap = blok(u, b, day, "bedtime"), blok(u, b, day, "nap_1")
    GONDERILEN.clear(); tur(day, nap - 30)
    gunduz30 = gelen(tok, "nap_1")
    GONDERILEN.clear(); tur(day, yat - 30)
    gece30 = gelen(tok, "bedtime")
    GONDERILEN.clear(); tur(day, yat - 15)
    gece15 = gelen(tok, "bedtime")
    if not acik:
        check("N) Kapalı: gece rutin 30 dk önce, metin '30 dakika'",
              [m["body"] for m in gece30] == ["Banyo, pijama, kitap; yatışa 30 dakika var."]
              and gece15 == [], (gece30, gece15))
    else:
        check("N) Açık: gece rutin 15 dk önce, metin '15 dakika'; 30 dk önce gitmez",
              gece30 == [] and [m["body"] for m in gece15]
              == ["Banyo, pijama, kitap; yatışa 15 dakika var."], (gece30, gece15))
    check(f"N) {etiket}: gündüz 30 dk önce 'Uykuya 30 dakika var' aynen",
          [m["body"] for m in gunduz30]
          == ["Uykuya 30 dakika var; sakin oyunlara geç, uyku işaretlerine bak."], gunduz30)
bayrak()

# =============================================================================
# K — kural bloğu
# =============================================================================
def kural_al(bid, an):
    db = SessionLocal()
    try:
        b = db.get(Baby, uuid.UUID(bid))
        with saat_sabitle(an):
            d = sor_baglam.gun_durumu(db, b)
            return sor_kural.kurallar(d, b), d
    finally:
        db.close()


G = datetime.combine(BUGUN, datetime.min.time(), tzinfo=TR)
k_g2, _ = kural_al(BID, G.replace(hour=2, minute=30) + timedelta(days=1))   # 2. günün gecesi
blok2 = k_g2["blok"] if k_g2 else ""
check("K) Gün 2 gecesi: aşama beşik yanı, gece", k_g2 and k_g2["gun"] == 2 and k_g2["gece"]
      and "Aşama: Beşik yanı (eğitimin 2. günü, gece)" in blok2, blok2[:200])
check("K) 02:30 (2. günün gecesi): sabahki gündüz uykuları 3. günün → kısa gündüz 15 + 30",
      "Kısa gündüz uykusu (3. günün gündüz uykuları" in blok2
      and "dışarıda 15 dk bekle, sonra içeride 30 dk dene" in blok2
      and "Kucağa alma aralıkları: 0,5 → 1 → 1,5 → 2 dk" in blok2
      and "dış bekleme kademesi" not in blok2.lower(), blok2)
k_g2g, _ = kural_al(BID, G.replace(hour=14))                          # 2. günün gündüzü
check("K) 2. günün gündüzü: kısa gündüz 10 + 35",
      "dışarıda 10 dk bekle, sonra içeride 35 dk dene" in k_g2g["blok"] and not k_g2g["gece"], k_g2g["blok"])
check("K) Bekleyen alanlar için sayı yok: ilk bekleme ve gece içi artış 'planına bak'",
      "ilk bekleme süresi ve aynı gece içindeki artış için planına bak" in blok2)
check("K) Gün 2: yatır-çık dizisi kullanılmaz", "Yatır-çık 13. günde başlar" in blok2
      and "5 → 10 → 15 → 20" not in blok2)
check("K) Gece akışı ve 3 kez sonrası (gece destek, gündüz uyanık tut)",
      "45 dk; uyumazsa odada 15 dk rutin (beslenme yok); en fazla 3 kez" in blok2
      and "destek verip uyutulur" in blok2 and "uyanık tutulur" in blok2, blok2)
k_g5, _ = kural_al(BID4, G.replace(hour=14))
check("K) Gün 5 (oda ortası): kısa gündüz 'bekleme süreleri için planına bak'",
      "Kısa gündüz uykusu (5. günün gündüz uykuları; uyku 60 dk altında kalırsa): bekleme "
      "süreleri için planına bak" in k_g5["blok"]
      and not k_g5["izinli"]["dis"], k_g5["blok"])
_, B13 = hesap(egitim_gunu_bugun=13)
k_g13, _ = kural_al(B13, G.replace(hour=14))
check("K) Gün 13: yatır-çık 5 → 10 → 15 → 20, en fazla 20–25; kısa gündüz 20 + 25",
      "Yatır-çık: dışarıda 5 → 10 → 15 → 20 dk, en fazla 20–25 dk" in k_g13["blok"]
      and "dışarıda 20 dk bekle" in k_g13["blok"], k_g13["blok"])
_, B0 = hesap(egitim_gunu_bugun=None)
k_yok, _ = kural_al(B0, G.replace(hour=14))
check("K) Eğitim başlamamış → kural yok (blok ve doğrulama uygulanmaz)", k_yok is None)

# =============================================================================
# V — doğrulama (rapor cümleleri)
# =============================================================================
_, B1 = hesap(egitim_gunu_bugun=1)
k_g1, _ = kural_al(B1, G.replace(hour=8, minute=40))


def ihlal(metin, kural):
    return [(i["rol"], i["deger"]) for i in sor_kural.dogrula(metin, kural)]


check("V) 'İkinci uyanmada 10 dakika, üçüncü uyanmada 15 dakika' → 10 ihlal",
      ihlal("İkinci uyanmada 10 dakika, üçüncü uyanmada 15 dakika bekleyin.", k_g2) == [("gece_uyanma", 10.0)])
check("V) 'ikinci uyanmada 6-7, üçüncüde 8-9 dakika' → hepsi ihlal",
      len(ihlal("Gecenin bundan sonraki uyanmalarında ise bekleme süresini artırmaya devam edin — "
                "ikinci uyanmada 6-7 dakika, üçüncüde 8-9 dakika gibi.", k_g2)) == 4)
_kg = ("- **1. gündüz uykusu:** Dışarıdan 10 dakika bekleyin, sonra içeri girin ve beşiğin "
       "yanında oturup toplam 35 dakika daha bekleyin")
check("V) 'Dışarıdan 10 … 35 dakika': 2. gün gündüzünde uygun, 1. günde ihlal",
      ihlal(_kg, k_g2g) == [] and set(r for r, _ in ihlal(_kg, k_g1)) == {"dis", "ic"},
      (ihlal(_kg, k_g2g), ihlal(_kg, k_g1)))
check("V) Aynı cümle 02:30'da (sabah 3. gün): 10/35 ihlal", set(r for r, _ in ihlal(_kg, k_g2)) == {"dis", "ic"},
      ihlal(_kg, k_g2))
check("V) KB 'Gün 1: dış 5dk + iç 40dk. Gün 2: 10+35' 2. gün gündüzünde: 5/40 ihlal, 10/35 uygun",
      sorted(ihlal("Gün 1: dış 5dk + iç 40dk. Gün 2: 10+35.", k_g2g)) == [("dis", 5.0), ("ic", 40.0)],
      ihlal("Gün 1: dış 5dk + iç 40dk. Gün 2: 10+35.", k_g2g))
check("V) Gece 'dışarıda 5 dakika bekleyin': 5 taban, ilk bekleme süresi değil → ihlal; içeride 45 uygun",
      ihlal("Dışarıda 5 dakika bekleyin, sonra içeride 45 dakika deneyin.", k_g2) == [("dis", 5.0)],
      ihlal("Dışarıda 5 dakika bekleyin, sonra içeride 45 dakika deneyin.", k_g2))
check("V) Gece taban kalıbı uygun: 'asla 5 dakikanın altına inmeyin' / 'en az 5 dakika'",
      ihlal("Bekleme süresi asla 5 dakikanın altına inmesin.", k_g2) == []
      and ihlal("Dışarıda en az 5 dakika bekleyin; süre için planınıza bakın.", k_g2) == [])
check("V) Gece 'dışarıda 45 dakika bekleyin' (eski kart) → ihlal",
      ihlal("Dışarıda 45 dakika bekleyin.", k_g2) == [("dis", 45.0)], ihlal("Dışarıda 45 dakika bekleyin.", k_g2))
check("V) Kucak aralığı bekleme süresi gibi: 'Sonraki bekleme 1,5 dakika, ardından 2 dakika' → ihlal",
      [v for _, v in ihlal("Sonraki bekleme 1,5 dakika, ardından 2 dakika.", k_g2g)] == [1.5, 2.0]
      and ihlal("Kucağa alma aralıkları 0,5 → 1 → 1,5 → 2 dakika diye artar.", k_g2g) == [],
      ihlal("Sonraki bekleme 1,5 dakika, ardından 2 dakika.", k_g2g))
_gd = sor_kural.duzelt("Uyanırsa 5 dakika bekleyin.", k_g2g)
check("V) Gündüz 'uyanırsa 5 dakika bekleyin' → kısa gündüz karşılığı (gece akışı değil)",
      sor_kural.dogrula("Uyanırsa 5 dakika bekleyin.", k_g2g) != [] and _gd == k_g2g["karsilik"]["dis"], _gd)
check("V) Gece yanlış dış bekleme düzeltmede gece akışı karşılığıyla değişir",
      sor_kural.duzelt("Dışarıda 5 dakika bekleyin.", k_g2) == k_g2["karsilik"]["gece_uyanma"],
      sor_kural.duzelt("Dışarıda 5 dakika bekleyin.", k_g2))
check("V) Motivasyon istatistiği (45 dk ağlayan … 5 dakikada) plan sayısı değil → ihlal",
      [r for r, _ in ihlal("İlk gün 45 dakika ağlayan bir çocuk iki hafta sonra 5 dakikada uykuya "
                           "geçebiliyor.", k_g2)] == ["motivasyon", "motivasyon"])
check("V) Rol karışması: '(çok dirençli bebekte 30 dakika)' ve gündüz 'içeride 35 dakika deneyin' uygun",
      ihlal("Uyumazsa 15 dakika rutin molası verin (çok dirençli bebekte 30 dakika).", k_g2g) == []
      and ihlal("Sonra içeride 35 dakika deneyin.", k_g2g) == []
      and ihlal("Sonra içeride 35 dakika deneyin.", k_g5) != [])
check("V) Bloğun karşılık cümleleri kendi doğrulamasından geçer",
      all(ihlal(v, k) == [] for k in (k_g2, k_g2g, k_g5, k_g13) for v in k["karsilik"].values() if v))
check("V) Aynı KB cümlesi 1. gün gündüzünde: 10/35 ihlal, 5/40 uygun",
      sorted(ihlal("Gün 1: dış 5dk + iç 40dk. Gün 2: 10+35.", k_g1)) == [("dis", 10.0), ("ic", 35.0)],
      ihlal("Gün 1: dış 5dk + iç 40dk. Gün 2: 10+35.", k_g1))
check("V) Yasak kalıp: 5. günde 'dış X + iç Y' (alan İlayda kararı bekliyor) → ihlal",
      set(r for r, _ in ihlal("Kısa gündüzde dış 20 dk + iç 25 dk.", k_g5)) == {"dis", "ic"}
      and set(r for r, _ in ihlal("Kısa gündüzde dış 10 dk + iç 35 dk.", k_g5)) == {"dis", "ic"},
      ihlal("Kısa gündüzde dış 20 dk + iç 25 dk.", k_g5))
check("V) Yatır-çık dizisi 2. günde ihlal, 13. günde uygun",
      len(ihlal("Yatır-çıkta STANDART ilerleme 5 → 10 → 15 → 20 dk.", k_g2)) == 4
      and ihlal("Yatır-çıkta STANDART ilerleme 5 → 10 → 15 → 20 dk.", k_g13) == [])
check("V) B planı cümlesi uygun (45 / 15 / 30 / 3)",
      ihlal("45 dakika direnç olursa → 15 dakika rutin molası → 45 dakika yeniden deneme. Çok dirençli "
            "bebeklerde rutin molası 30 dakikaya çıkarılabilir. Maksimum 3 tekrar.", k_g2) == [])
_plan_saati = sorted(x for x in k_g1["saatler"] if x not in ("06:00", "08:40"))[0]
check("V) Planın kendi saati uygun", ihlal(f"Uyku saati {_plan_saati}.", k_g1) == [], _plan_saati)
check("V) Uydurma saat (03:17) ihlal", ihlal("Saat 03:17'de tekrar deneyin.", k_g1) == [("saat", 197.0)])
check("V) Türeyen dakika ('şu an'dan plan bloğuna) serbest",
      not [i for i in ihlal(f"Uyku saatine 50 dakika var.", k_g1)], ihlal("Uyku saatine 50 dakika var.", k_g1))
_duz = sor_kural.duzelt("Önce sakin olun.\n- İkinci uyanmada 10 dakika bekleyin.\n"
                        "- Yatır-çıkta 5 → 10 → 15 dk bekleyin.\n- Saat 03:17'de deneyin.\n"
                        "İlk gün 45 dakika ağlayan bir çocuk iki hafta sonra 5 dakikada uyur.", k_g2)
check("V) Düzeltme: gece cümlesi karşılıkla değişir; yatır-çık ve saat cümlesi çıkar",
      "Önce sakin olun." in _duz and "Gece uyandığında önce dışarıda bekleyin" in _duz
      and "Yatır-çık" not in _duz and "03:17" not in _duz and "Ağlama sabit kalmaz, her gün azalır." in _duz
      and "ağlayan" not in _duz and sor_kural.dogrula(_duz, k_g2) == [], _duz)

# =============================================================================
# F — sohbet akışı (sahte model)
# =============================================================================
os.environ["ANTHROPIC_API_KEY"] = "test-dummy"
chatbot.HAS_ANTHROPIC = True
YUKLER: list[dict] = []
CEVAPLAR: list[str] = []


class _Sahte:
    def __init__(self, *a, **k):
        self.messages = self

    def create(self, **kw):
        YUKLER.append(kw)
        metin = CEVAPLAR.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(text=metin)],
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                                     cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0))


chatbot.Anthropic = _Sahte
_loglar: list[str] = []


class _Yakala(logging.Handler):
    def emit(self, rec):
        _loglar.append(rec.getMessage())


logging.getLogger("tavsan.chat").addHandler(_Yakala())


def sor(h, bid, cevaplar, an):
    CEVAPLAR[:] = list(cevaplar)
    YUKLER.clear()
    with saat_sabitle(an):
        r = client.post("/api/v1/chat", headers=h, json={"message": "Bebeğim gece uyandı, ağlıyor; ne kadar bekleyip içeri gireyim?", "baby_id": bid})
    return r.json().get("answer", ""), [m for m in _loglar if m.startswith("chat: ")][-1]


def usage_sayisi(beklenen=None):
    """Maliyet satırları kuyruk iş parçacığıyla yazılır: beklenen sayıya ulaşana
    kadar en fazla 5 sn yokla."""
    import time
    for _ in range(50):
        db = SessionLocal()
        n = db.query(ApiUsage).count()
        db.close()
        if beklenen is None or n >= beklenen:
            return n
        time.sleep(0.1)
    return n


an2 = G.replace(hour=2, minute=30) + timedelta(days=1)
bayrak(SOR_KURAL_BLOGU=True, SOR_SAYI_DOGRULAMA=True)
_u0 = usage_sayisi()
cev, log = sor(H, BID, ["Dışarıda bekleyin (süre için planınıza bakın), sonra içeride 45 dakika deneyin; asla 5 dakikanın altına inmeyin."], an2)
check("F) Uygun cevap: tek çağrı, dogrulama=gecti",
      len(YUKLER) == 1 and "dogrulama=gecti" in log and usage_sayisi(_u0 + 1) == _u0 + 1, (len(YUKLER), log))
check("F) Blok bağlamda, sistem kuralı ayrı blokta",
      "BUGÜNÜN KURALLARI" in YUKLER[0]["messages"][-1]["content"]
      and any("YALNIZ BEBEK VERİSİ'ndeki 'BUGÜNÜN KURALLARI'" in s["text"] for s in YUKLER[0]["system"]))
check("F) Blokta ad yok (takma ad bile)", "Ada" not in blok2 and "Zalva" not in blok2)
_u0 = usage_sayisi()
cev, log = sor(H, BID, ["İkinci uyanmada 10 dakika bekleyin.", "Planınıza bakın; 45 dakika deneyin."], an2)
check("F) İhlal → yeniden üretim: 2 çağrı, düzeltme notu modele gitti, dogrulama=yeniden",
      len(YUKLER) == 2 and "DÜZELTME:" in YUKLER[1]["messages"][-1]["content"]
      and "gece_uyanma: 10 dk" in YUKLER[1]["messages"][-1]["content"]
      and "dogrulama=yeniden" in log and cev == "Planınıza bakın; 45 dakika deneyin."
      and usage_sayisi(_u0 + 2) == _u0 + 2, (len(YUKLER), log, cev))
cev, log = sor(H, BID, ["İkinci uyanmada 10 dakika bekleyin.",
                        "Önce sakin olun. Üçüncü uyanmada 20 dakika bekleyin."], an2)
check("F) İkinci cevap da ihlalli → cümle karşılıkla değişir, dogrulama=duzeltildi",
      "dogrulama=duzeltildi" in log and "Önce sakin olun." in cev and "20 dakika" not in cev
      and "Gece uyandığında önce dışarıda bekleyin" in cev, (log, cev))
check("F) Log satırında içerik yok", "uyanmada" not in log and "Ada" not in log, log)
bayrak()
YUKLER.clear()
cev, log = sor(H, BID, ["İkinci uyanmada 10 dakika bekleyin."], an2)
check("F) Bayraklar kapalı: tek çağrı, blok yok, dogrulama=-",
      len(YUKLER) == 1 and "BUGÜNÜN KURALLARI" not in YUKLER[0]["messages"][-1]["content"]
      and "dogrulama=-" in log and cev == "İkinci uyanmada 10 dakika bekleyin.", (len(YUKLER), log))

# =============================================================================
print("\n" + "=" * 70)
gecen = sum(1 for _, ok, _ in results if ok)
for ad, ok, detay in results:
    print(f"[{'OK ' if ok else 'XX '}] {ad}" + ("" if ok else f"\n        → {detay[:400]}"))
print("=" * 70)
print(f"SONUÇ: {gecen}/{len(results)}")
sys.exit(0 if gecen == len(results) else 1)
