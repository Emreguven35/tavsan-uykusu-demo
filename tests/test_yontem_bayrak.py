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
    """True → "1"; metin değer (ör. SOR_SAYI_DOGRULAMA="duzelt") aynen yazılır."""
    for ad in BAYRAKLAR:
        v = acik.get(ad)
        if v:
            os.environ[ad] = v if isinstance(v, str) else "1"
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
check("C) Son adım tablodaki sonuç: 'o gün başarısız sayılır'; tabloda olmayan 'yarın devam' yok",
      adim[4] == "Yine uyumazsa mecburen destek vererek uyutun; o gün başarısız sayılır."
      and "yarın" not in adim[4], adim[4])
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
check("K) Bekleyen alanlar için sayı yok: ilk bekleme ve aynı gece içindeki süreler 'planına bak'",
      "ilk bekleme süresi ve aynı gece içindeki bekleme süreleri için planına bak" in blok2
      and "artış için planına bak" not in blok2, blok2)
check("K) Bekleyen alanlarda YÖN de yasak; günden güne artış kuralı gece içine taşınmaz",
      "aynı gece içinde bir uyanmadan ötekine bekleme süresinin değişimi" in blok2
      and "sayı da YÖN de söyleme" in blok2
      and "'Aynı gece her uyanmada bekleme artar / azalır / aynı kalır' DEME" in blok2
      and "Bu artış kuralı GÜNDEN GÜNEdir, aynı gecenin uyanmaları için değil." in blok2, blok2)
check("K) 3 denemeden sonraki gece: tablodaki kural aynen + yumuşatma/suçlama/ek sonuç yasağı",
      "3 denemeden sonra da uyumayan GECE için tablodaki kural: mecburen destek verip uyutulur; "
      "o gün başarısız sayılır." in blok2 and "yumuşatma" in blok2 and "anneyi suçlama" in blok2
      and "tabloda olmayan bir sonuç" in blok2, blok2)
check("K) Annenin yeri (beşik yanı): odada, beşiğin yanında; 'içeri girmeyin' çelişkisi anılır",
      "Annenin yeri (Beşik yanı; yatırırken ve içeri girdiğinde): odada, beşiğin yanında "
      "(sandalyede ya da ayakta)." in blok2 and "'içeri girmeyin' deme" in blok2, blok2)
check("K) Gün 2: yatır-çık dizisi kullanılmaz", "Yatır-çık 13. günde başlar" in blok2
      and "5 → 10 → 15 → 20" not in blok2)
check("K) Gece akışı ve 3 kez sonrası (gece destek, gündüz uyanık tut)",
      "sonra içeri gir ve 45 dk dene; uyumazsa odada 15 dk rutin (beslenme yok); en fazla 3 kez. "
      "İçerideyken: uyuyana kadar pat-pat, pış-pış; histerik ağlama yoksa kucak yok, histerikse "
      "kucak serbest." in blok2 and "kucak serbest, 45" not in blok2
      and "destek verip uyutulur" in blok2 and "uyanık tutulur" in blok2, blok2)
k_g5, _ = kural_al(BID4, G.replace(hour=14))
check("K) Gün 5 (oda ortası): kısa gündüz 'bekleme süreleri için planına bak'",
      "Kısa gündüz uykusu (5. günün gündüz uykuları; uyku 60 dk altında kalırsa): bekleme "
      "süreleri için planına bak" in k_g5["blok"]
      and not k_g5["izinli"]["dis"], k_g5["blok"])
from api.services.sor_baglam import AYLAR as _AY
_GN = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")


def _tt(gun):
    return f"{_GN[gun.weekday()]} {gun.day} {_AY[gun.month - 1]}"


_D1, _D2 = BUGUN + timedelta(days=1), BUGUN + timedelta(days=2)
check("K) Takvim (gündüz): bugün/yarın gün adı, tarih, eğitim günü ve aşama hazır",
      f"bugün {_tt(BUGUN)}: eğitimin 2. günü, aşama Beşik yanı; yarın {_tt(_D1)}: eğitimin 3. "
      f"günü, aşama Beşik yanı." in k_g2g["blok"], k_g2g["blok"])
check("K) Takvim (gece 02:30, sabah öncesi): gecesi sayılan gün, sabahki gün, yarın",
      f"şu an {_tt(_D1)} gecesi, eğitimin 2. gününün gecesi; sabah uyanınca {_tt(_D1)}: eğitimin "
      f"3. günü, aşama Beşik yanı; yarın {_tt(_D2)}: eğitimin 4. günü, aşama Oda ortası." in blok2,
      blok2)
check("K) Gün 5: kısa gündüz dış/iç süreleri bekleyen konular arasında",
      "kısa gündüz uykusundaki dışarıda/içeride bekleme süreleri" in k_g5["blok"], k_g5["blok"])
_, B8 = hesap(egitim_gunu_bugun=8)
k_g8, _ = kural_al(B8, G.replace(hour=14))
check("K) Gün 8 (kapı): içeride temas bekleyen konu; yer kapının yanı; 'içeri girmeyin' örneği var",
      "bu aşamada içeride temasın nasıl olacağı" in k_g8["blok"]
      and "Annenin yeri (Kapı; yatırırken ve içeri girdiğinde): odanın içinde, kapının yanında"
      in k_g8["blok"], k_g8["blok"])
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


def _mod_dene(v):
    if v is None:
        os.environ.pop("SOR_SAYI_DOGRULAMA", None)
    else:
        os.environ["SOR_SAYI_DOGRULAMA"] = v
    try:
        return sor_kural.dogrulama_modu()
    finally:
        os.environ.pop("SOR_SAYI_DOGRULAMA", None)


check("V) 'İkinci uyanmada 10 dakika, üçüncü uyanmada 15 dakika' → 10 ihlal",
      ihlal("İkinci uyanmada 10 dakika, üçüncü uyanmada 15 dakika bekleyin.", k_g2) == [("gece_uyanma", 10.0)])
_v67 = ihlal("Gecenin bundan sonraki uyanmalarında ise bekleme süresini artırmaya devam edin — "
             "ikinci uyanmada 6-7 dakika, üçüncüde 8-9 dakika gibi.", k_g2)
check("V) 'ikinci uyanmada 6-7, üçüncüde 8-9 dakika' → 4 sayı ihlali + yön ihlali",
      len([r for r, _ in _v67 if r != "yon"]) == 4 and ("yon", 0.0) in _v67, _v67)
_kg = ("- **1. gündüz uykusu:** Dışarıdan 10 dakika bekleyin, sonra içeri girin ve beşiğin "
       "yanında oturup toplam 35 dakika daha bekleyin")
def _sayisal(v):          # yatış kuralını ayrı test ediyoruz
    return [x for x in v if x[0] != "yatis_dis"]


check("V) 'Dışarıdan 10 … 35 dakika': 2. gün gündüzünde uygun, 1. günde ihlal",
      _sayisal(ihlal(_kg, k_g2g)) == [] and set(r for r, _ in _sayisal(ihlal(_kg, k_g1))) == {"dis", "ic"},
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
                           "geçebiliyor.", k_g2)] == ["motivasyon"] * 3)
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


# --- Yeni kurallar (2026-10-12 b): yazıyla sayı, sayımlar, yön, yumuşatma,
#     suçlama, aşama, gün adı, tek geçişte düzeltme ---------------------------
k_g3n, _ = kural_al(BID, G.replace(hour=20, minute=35) + timedelta(days=1))   # 3. günün gecesi
check("V) Yazıyla sayı: 'on dakika' rakamla aynı kurala girer (gece 2. uyanma 10 → ihlal)",
      ihlal("İkinci uyanmada on dakika bekleyin.", k_g2) == [("gece_uyanma", 10.0)]
      == ihlal("İkinci uyanmada 10 dakika bekleyin.", k_g2))
check("V) Yazıyla: 'kırk beş dakika deneyin', 'beş dakikanın altına inmeyin' uygun",
      ihlal("İçeride kırk beş dakika deneyin.", k_g2) == []
      and ihlal("Bekleme asla beş dakikanın altına inmesin.", k_g2) == [])
check("V) Yazıyla: 'bir buçuk dakika' bekleme süresi gibi → ihlal; 'yarım saat' mola = 30 uygun",
      ihlal("Sonraki bekleme bir buçuk dakika olsun.", k_g2g) == [("belirsiz", 1.5)]
      and ihlal("Çok dirençliyse yarım saat rutin molası verin.", k_g2g) == [])
check("V) Birimsiz kullanım sayılmaz: 'bir şey', 'iki taraf'",
      ihlal("Şimdilik bir şey yapmayın; iki taraf da sakin kalsın.", k_g2g) == [])
check("V) Sayım: 'iki gün kaldı' ve '2 gün kaldı' 5. günde ihlal (kalan 8); '13 günlük' uygun",
      ihlal("İki gün kaldı.", k_g5) == [("gun", 2.0)] == ihlal("2 gün kaldı.", k_g5)
      and ihlal("Program 13 günlük; 8 gün kaldı.", k_g5) == [])
check("V) Sayım: 'beş kez' ihlal; 'en fazla üç kez', 'bir kez daha' uygun; 'dört defa' ihlal",
      ihlal("Beş kez deneyin.", k_g2g) == [("kez", 5.0)]
      and ihlal("En fazla üç kez deneyin, gerekirse bir kez daha rutin yapın.", k_g2g) == []
      and ihlal("Dört defa deneyin.", k_g2g) == [("kez", 4.0)])
check("V) Sayım: 'bir hafta' (tabloda yok) ihlal",
      ihlal("Bir hafta içinde düzelir.", k_g2g) == [("hafta", 1.0)])
_yon = ["Aynı gecede her yeni uyanmada bekleme süresini bir öncekinden mutlaka artırın.",
        "Her uyanmada bekleme süresi artacağı için, gece içindeki direnç azalacaktır.",
        "İlk bekleme süresi ve aynı gece içindeki artış için planınıza bakın."]
check("V) Yön: aynı gece içinde bekleme artar/artırın → ihlal (sayı olmasa da)",
      all(ihlal(c, k_g2) == [("yon", 0.0)] for c in _yon), [ihlal(c, k_g2) for c in _yon])
_yon_ok = ["Bekleme süresi günden güne 5 dakika artar.",
           "Her uyanmada aynı adımları izleyin; en fazla 3 kez deneyin.",
           "İlk bekleme süresi ve aynı gece içindeki bekleme süreleri için planınıza bakın."]
check("V) Yön: günden güne artış, 'en fazla 3 kez', süreyi plana bırakmak uygun",
      all(ihlal(c, k_g2) == [] for c in _yon_ok), [ihlal(c, k_g2) for c in _yon_ok])
check("V) Yumuşatma: 'başarısızlık değil, ilk 3 gün esnek' → ihlal",
      ("yumusatma", 0.0) in ihlal("3 kez sonra da uyumazsa destek vererek uyutabilirsiniz — bu bir "
                                  "başarısızlık değildir, ilk 3 gün bu esnektir.", k_g3n))
check("V) 'Hiçbir şey olmaz — bir gün başarısız, iki gün kaldı' (3. gün) → yumuşatma + gün 2",
      sorted(ihlal("Hiçbir şey olmaz — bir gün başarısız, iki gün kaldı.", k_g3n))
      == [("gun", 2.0), ("yumusatma", 0.0)], ihlal("Hiçbir şey olmaz — bir gün başarısız, iki gün kaldı.", k_g3n))
check("V) 'normal karşılanır' yalnız başarısızlık bağlamında ihlal",
      ihlal("3 kez sonunda mecburen destekle uyutun; bu normal karşılanır.", k_g3n) == [("yumusatma", 0.0)]
      and ihlal("Ağlaması bu aşamada normal karşılanır.", k_g3n) == [])
check("V) Tablodaki kural ('o gün başarısız sayılır') ihlal değil",
      ihlal("3 kez sonunda mecburen destek verip uyutursunuz; o gün başarısız sayılır.", k_g3n) == [])
check("V) Suçlama → ihlal",
      ihlal("Bu sizin hatanız değil ama yeterince kararlı olmadınız.", k_g3n) == [("suclama", 0.0)])
check("V) Aşama (beşik yanı): yatışta 'içeri girmeyin' ihlal; gece uyanmasında dışarıda bekleme ve "
      "uyuyunca odadan çıkmak uygun",
      ihlal("Hayır, içeri girmeyin — henüz 45 dakika dolmamış olabilir.", k_g3n) == [("asama", 0.0)]
      and ihlal("Gece uyandığında önce dışarıda bekleyin, hemen içeri girmeyin.", k_g3n) == []
      and ihlal("Uyuyana kadar yanında kalın, sonra odadan çıkın.", k_g3n) == [])
check("V) Aşama (kapı): 'beşiğin yanında oturun' ihlal; beşik yanında uygun",
      ihlal("Beşiğin yanında oturun.", k_g8) == [("asama", 0.0)] and ihlal("Beşiğin yanında oturun.", k_g2g) == [])
_gb, _gy, _go = _GN[BUGUN.weekday()], _GN[_D1.weekday()], _GN[_D2.weekday()]
check("V) Gün adı: 'yarın <yanlış gün>' ihlal; bugün/yarın doğruysa uygun",
      ihlal(f"Yarın ({_go}) 3. gün başlıyor.", k_g2g) == [("gun_adi", 0.0)]
      and ihlal(f"Bugün {_gb}, yarın {_gy}.", k_g2g) == []
      and ihlal(f"yarın {_go.lower()} günü", k_g2g) == [("gun_adi", 0.0)])
check("V) Gün adı düzeltmesi yerinde: cümle kalır, gün adı doğrusuyla değişir",
      sor_kural.duzelt(f"Yarın ({_go}) 3. gün başlıyor.", k_g2g) == f"Yarın ({_gy}) 3. gün başlıyor.",
      sor_kural.duzelt(f"Yarın ({_go}) 3. gün başlıyor.", k_g2g))
check("V) Gece 02:30: 'yarın' bu sabahı da kapsar (gecenin takvim günü)",
      ihlal(f"Yarın {_gy} sabahı 3. gün başlıyor.", k_g2) == [])
_kgu = k_g2["karsilik"]["gece_uyanma"]
_m, _d, _c = sor_kural.duzelt_ayrinti(_kgu + " İkinci uyanmada 10 dakika bekleyin.", k_g2)
check("V) Düzelt: karşılık cevapta aynen varsa ihlalli cümle ÇIKAR (iki kez geçmez)",
      _m == _kgu and (_d, _c) == (0, 1), (_m, _d, _c))
_m, _d, _c = sor_kural.duzelt_ayrinti("Dışarıda bekleyin; süre için planınıza bakın. İçeride 45 dakika "
                                      "deneyin, uyumazsa 15 dakika rutin yapın; en fazla 3 kez. "
                                      "İkinci uyanmada 10 dakika bekleyin.", k_g2)
check("V) Düzelt: karşılığın bilgisi (45/15/3 + planınıza bakın) zaten varsa cümle çıkar",
      "10 dakika" not in _m and _kgu not in _m and (_d, _c) == (0, 1), (_m, _d, _c))
_m, _d, _c = sor_kural.duzelt_ayrinti("İkinci uyanmada 10 dakika bekleyin. Üçüncü uyanmada 20 dakika "
                                      "bekleyin.", k_g2)
check("V) Düzelt: aynı karşılık bir kez; ikinci ihlalli cümle çıkar",
      _m == _kgu and (_d, _c) == (1, 1), (_m, _d, _c))
_m, _d, _c = sor_kural.duzelt_ayrinti("Önce sakin olun. Yeterince kararlı olmadınız.", k_g3n)
check("V) Düzelt: karşılığı olmayan (suçlama) cümle çıkar",
      _m == "Önce sakin olun." and (_d, _c) == (0, 1), (_m, _d, _c))
_m, _d, _c = sor_kural.duzelt_ayrinti("Bu bir başarısızlık değil, ilk günler esnektir.", k_g3n)
check("V) Düzelt: yumuşatma tablodaki kuralla değişir",
      _m == k_g3n["karsilik"]["yumusatma"]
      == "3 denemeden sonra da uyumazsa o gece mecburen destek verip uyutulur; o gün başarısız sayılır."
      and (_d, _c) == (1, 0), (_m, _d, _c))
check("V) Yeni karşılıklar kendi doğrulamasından geçer (yon, yumusatma, asama, gun_no)",
      all(ihlal(k["karsilik"][r], k) == [] for k in (k_g2, k_g3n, k_g8, k_g2g)
          for r in ("yon", "yumusatma", "asama", "gun_no")))
# Canlı koşudan (2026-10-12 b) çıkan durumlar
check("V) Gün no: 02:30'da 'Yarın Salı, eğitimin 3. günü' (yarın 4. gün) → ihlal; '4. gün' uygun",
      ihlal(f"Yarın {_go}, eğitimin 3. günü başlıyor.", k_g2) == [("gun_no", 0.0)]
      and ihlal(f"Yarın {_gy} değil, {_go} eğitimin 3. günü.", k_g2) != []
      and ihlal(f"Yarın {_gy}, eğitimin 3. günü başlıyor.", k_g2) == []          # gece: 'yarın' bu sabah da olabilir
      and ihlal(f"{_go} günü eğitimin 3. günü.", k_g2) != []
      and ihlal(f"Yarın {_go} eğitimin 4. günü olacak.", k_g2) == [], [
          ihlal(f"Yarın {_go}, eğitimin 3. günü başlıyor.", k_g2), ihlal(f"{_go} günü eğitimin 3. günü.", k_g2)])
check("V) Gün no (gündüz): 'Yarın eğitimin 2. günü' (yarın 3. gün) ihlal, takvim karşılığıyla değişir",
      ihlal("Yarın eğitimin 2. günü başlıyor.", k_g2g) == [("gun_no", 0.0)]
      and sor_kural.duzelt("Yarın eğitimin 2. günü başlıyor.", k_g2g) == k_g2g["karsilik"]["gun_no"]
      == f"Bugün {_tt(BUGUN)}: eğitimin 2. günü, aşama Beşik yanı; yarın {_tt(_D1)}: eğitimin 3. günü, "
         f"aşama Beşik yanı.", sor_kural.duzelt("Yarın eğitimin 2. günü başlıyor.", k_g2g))
check("V) Gün no: 'her gün 5 dakika' sıra gün değildir",
      ihlal("Bugün de bekleme her gün 5 dakika artar.", k_g2g) == [])
check("V) Kucak süresi uydurma: 'histerikse kucak serbest, en fazla 45 dakika' → kucak ihlali",
      ihlal("Histerik ağlama varsa kucağa alabilirsiniz, en fazla 45 dakika.", k_g2) == [("kucak", 45.0)])
_m, _d, _c = sor_kural.duzelt_ayrinti("Histerikse kucak serbest, 45 dakikaya kadar kucakta tutabilirsiniz. "
                                      "Kucağa alacaksanız kademeli yapın: 30 saniye, 1 dakika, 1,5 dakika, "
                                      "2 dakika.", k_g2)
check("V) Düzelt: kucak aralıkları saniye/dakika karışık zaten varsa karşılık EKLENMEZ, cümle çıkar",
      "45" not in _m and "Kucağa alma aralıkları 0,5" not in _m and (_d, _c) == (0, 1), (_m, _d, _c))
check("V) '45 dakika — en fazla 3 kez': 3 dakika sanılmaz (tire zinciri)",
      ihlal("Toplam 45 dakika deneyin; uyumazsa 15 dakika rutin molası (çok dirençli olursa 30 dakika), "
            "ardından yeniden 45 dakika — en fazla 3 kez.", k_g2g) == [])
check("V) Kısa uyku eşiği: 'kısa bir uyku (60 dakikadan az) uyursa … dışarıda 10, içeride 35' uygun",
      k_g2g["kisa_esik"] == 60 and ihlal("Eğer kısa bir uyku (60 dakikadan az) uyursa dışarıda 10 dakika "
                                         "bekleyin, sonra içeride 35 dakika deneyin.", k_g2g) == [],
      ihlal("Eğer kısa bir uyku (60 dakikadan az) uyursa dışarıda 10 dakika bekleyin, sonra içeride 35 "
            "dakika deneyin.", k_g2g))
_m, _d, _c = sor_kural.duzelt_ayrinti("İlk gün 45 dakika ağlayan bir çocuk iki hafta sonra 5 dakikada uyur. "
                                      "Ağlama her gün azalır.", k_g2)
check("V) Düzelt: motivasyon karşılığı cevapta zaten varsa eklenmez (iki kez 'azalır' yok)",
      _m == "Ağlama her gün azalır." and (_d, _c) == (0, 1), (_m, _d, _c))
check("V) Geri dalış eşiği: '5 dakika ve üzeri tekrar uyursa uzatma tamamlanır' uygun",
      ihlal("5 dakika ve üzeri tekrar uyursa uzatma denemesi tamamlanmış sayılır.", k_g2g) == [])
_m, _d, _c = sor_kural.duzelt_ayrinti("Toplam 45 dakika deneyin; uyumazsa 15 dakika rutin molası (çok "
                                      "dirençliyse 30 dakika), ardından tekrar 45 dakika. En fazla 3 deneme "
                                      "yapılır. Sonra 47 dakika deneyin.", k_g2g)
check("V) Düzelt: '3 deneme' yalın sayısı da bilgiyi taşır → seans karşılığı EKLENMEZ, cümle çıkar",
      "47" not in _m and "Uyumazsa 45 dakika deneyin, sonra" not in _m and (_d, _c) == (0, 1), (_m, _d, _c))
check("V) Yumuşatma bağlamı önceki cümleden: '…destek verip uyutabilirsiniz. Bu, bir duruma neden olmaz.'",
      ihlal("3 denemenin tamamından sonra da uyumazsa mecburen destek verip uyutabilirsiniz. Bu, eğitim "
            "adımında bir duruma neden olmaz.", k_g3n) == [("yumusatma", 0.0)]
      and ihlal("Bu, eğitim adımında bir duruma neden olmaz.", k_g3n) == [])
check("V) Kısa uyku eşiği: 'uyku uzunsa (60 dakika veya daha fazla)' rutin sayılmaz",
      ihlal("Eğer uyku uzunsa (60 dakika veya daha fazla): rutine devam edin.", k_g2g) == [])
_ilk_uyku = min(b["start_minute"] for b in plan_adapter.normalize_schedule(
    (SessionLocal().query(SleepPlan).filter(SleepPlan.baby_id == uuid.UUID(BID)).first().content or {})
    .get("schedule")) if b.get("start_minute") is not None and b["start_minute"] > 8 * 60)
_ss_ = lambda m: f"{m // 60:02d}:{m % 60:02d}"
check("V) Türeyen saat: uykudan rutin süresi (15/30) kadar önce uygun; çizelgeden türemeyen saat ihlal",
      ihlal(f"Rutin 15-30 dakika sürer, yani {_ss_(_ilk_uyku - 30)}-{_ss_(_ilk_uyku - 15)} civarında başlayın.",
            k_g2g) == []
      and ihlal("Saat 11:07'de başlayın.", k_g2g) == [("saat", 667.0)],
      ihlal(f"Rutin 15-30 dakika sürer, yani {_ss_(_ilk_uyku - 30)}-{_ss_(_ilk_uyku - 15)} civarında başlayın.", k_g2g))
_m, _d, _c = sor_kural.duzelt_ayrinti("1 → 1,5 → 2 dakika bekleme ile toplam 45 dakika deneyin. 15 dakikalık rutin "
                                      "molası verin. Sonra yeniden 45 dakika deneyin; en fazla 3 kez.", k_g2g)
check("V) Düzelt: karşılıktaki parantez ayrıntısı (30 dk) eksik diye karşılık EKLENMEZ",
      "çok dirençli bebekte" not in _m and (_d, _c) == (0, 1), (_m, _d, _c))
check("V) Yumuşatma: 'başarısız sayılır — ama korkmayın, çok nadir' ve 'kaygı kaynağı yapmayın' ihlal",
      ihlal("Bu durumda bu gece başarısız sayılır — ama korkmayın, bu senaryo çok nadir yaşanır.", k_g3n)
      == [("yumusatma", 0.0)]
      and ihlal("O gece başarısız sayılır ama bunu kaygı kaynağı yapmayın.", k_g3n) == [("yumusatma", 0.0)]
      and ihlal("Korkmayın, yanındayım.", k_g3n) == [])
check("V) Yumuşatma: 'başarısız sayılır, ama merak etmeyin' ihlal",
      ihlal("Bu durum başarısız sayılır, ama merak etmeyin, sabah devam edersiniz.", k_g3n) == [("yumusatma", 0.0)])
_m, _d, _c = sor_kural.duzelt_ayrinti("İlk bekleme süresini ve aynı gece içindeki ilerlemesini öğrenmek için "
                                      "planınızın gece bölümüne bakın. Bekleme süresi her uyanmada artacak.", k_g2)
check("V) Düzelt: 'aynı gece … planınızın gece bölümüne bakın' varsa yön karşılığı EKLENMEZ, cümle çıkar",
      "artacak" not in _m and k_g2["karsilik"]["yon"] not in _m and (_d, _c) == (0, 1), (_m, _d, _c))
check("V) 'ör.' / 'vb.' kısaltması cümleyi bölmez",
      len(sor_kural._cumleler("Kısa bir rutin yapın — ör. perde kapatmak, masaj vb. şeyler. Sonra yatırın.")) == 2)
check("V) Kısa uyku eşiği bağlamı yoksa 60 dış bekleme sayılmaz: 'dışarıda 60 dakika bekleyin' ihlal",
      _sayisal(ihlal("Dışarıda 60 dakika bekleyin.", k_g2g)) == [("dis", 60.0)])
check("V) Mod: tanımsız/kapali → kapali; izle/duzelt/yeniden; eski '1'/'true' → duzelt",
      [(_mod_dene(v)) for v in (None, "kapali", "izle", "duzelt", "yeniden", "1", "true", "abc")]
      == ["kapali", "kapali", "izle", "duzelt", "yeniden", "duzelt", "duzelt", "kapali"])


# --- 2026-10-12 c: yatış ↔ gece uyanması, mola tanımı, saatli süreler, aşama geçişi
_k_yat, _ = kural_al(BID, G.replace(hour=20, minute=35) + timedelta(days=1))   # 3. gün, yatış saati
_k_gece = k_g2                                                                 # 02:30, gece uyanması
check("K) Yatış: saat (gece yatışından sonraki 3 sa) ve soru sözcükleriyle; uyanma sorusu yatış değil",
      _k_yat["yatis"] and not _k_gece["yatis"]
      and sor_kural.yatis_mi("Bebeği yatırdım, ağlıyor", {"dk": 120, "sabah_once": True}, [])
      and not sor_kural.yatis_mi("Gece uyandı, ne yapayım?", {"dk": 20 * 60 + 35, "sabah_once": False},
                                 [{"type": "sleep", "start_minute": 20 * 60}]))
check("K) Blokta YATIŞ ve GECE UYANMASI ayrı; yatışta dışarıda bekleme YOK",
      "- YATIŞ (gece yatışı ve gündüz uykuları; bebeği yatırırken ve uykuya dalarken): annenin yeri odada, "
      "beşiğin yanında (sandalyede ya da ayakta); yatışta DIŞARIDA BEKLEME YOK." in blok2
      and "- GECE UYANMASI (bebek uyuduktan sonra gece uyanırsa; yatışta değil): önce dışarıda bekle" in blok2,
      blok2)
check("K) Yatış saatinde blok 'Soru YATIŞLA ilgili' der; 02:30'da demez",
      "Soru YATIŞLA ilgili görünüyor" in _k_yat["blok"] and "Soru YATIŞLA" not in blok2)
check("K) Gün 13 (yatır-çık) yatışı: dış bekleme dizisi yatışta",
      "yatır-çık: dışarıda 5 → 10 → 15 → 20 dk bekleyip kısa girip çık." in k_g13["blok"], k_g13["blok"])
check("K) Rutin molası tanımı tablodan: bebekle odada, beslenme yok; anne molası değil",
      "- Rutin molası nedir: bebekle odada yapılan kısa bir uyku rutinidir (oda içinde; rutinde beslenme "
      "yok (mama, meme, süt, sıvı yok)); annenin dinlenme arası DEĞİLDİR, anne odadan çıkmaz." in blok2, blok2)
check("K) Aşama geçişi notu (ağlama ortalaması / süre kovaları) blokta YOK; yalnız takvim satırı",
      all(w not in b.lower() for b in (blok2, _k_yat["blok"], k_g5["blok"])
          for w in ("ortalama", "kova", "aşama geçişi", "1 hafta")), blok2)
check("V) Yatış (20:35): 'önce dışarıda bekleyin' ihlal; gece uyanması / kısa gündüz bağlamı uygun; "
      "02:30'da aynı cümle uygun",
      ihlal("Ağlama itiraz niteliğindeyse önce dışarıda bekleyin.", _k_yat) == [("yatis_dis", 0.0)]
      and ihlal("Gece uyanırsa önce dışarıda bekleyin.", _k_yat) == []
      and ihlal("Uyku 60 dakikanın altında kalırsa dışarıda bekleyin, sonra içeri girin.", _k_yat) == []
      and ihlal("Yatışta dışarıda beklemeyin.", _k_yat) == []
      and ihlal("Ağlama itiraz niteliğindeyse önce dışarıda bekleyin.", _k_gece) == [])
_m, _d, _c = sor_kural.duzelt_ayrinti("Ağlarsa önce dışarıda bekleyin. Sonra beşiğin yanına geçin.", _k_yat)
check("V) Düzelt: yatışta dış bekleme cümlesi yatış karşılığıyla değişir",
      _m.startswith("Yatışta dışarıda beklemezsiniz, odada, beşiğin yanında") and (_d, _c) == (1, 0), _m)
check("V) Yatır-çık aşamasında yatışta dışarıda bekleme uygun",
      ihlal("Önce dışarıda bekleyin.", dict(k_g13, yatis=True)) == [])
check("V) Mola anne molası gibi: '15 dakikalık rutin molası alın: odadan çıkın, yüzünüzü yıkayın' ihlal",
      ("mola_tanimi", 0.0) in ihlal("15 dakikalık rutin molası alın: odadan çıkın, yüzünüzü yıkayın, su için.",
                                    _k_yat)
      and ihlal("15 dakikalık rutin molası verin; beslenme yok.", _k_yat) == [])
check("V) Mola: olumsuz kalıp ('odadan çıkmadan', 'siz odadan çıkmıyorsunuz') doğru tanım, ihlal DEĞİL",
      ihlal("Uyumazsa odadan çıkmadan 15 dakika rutin molası yapın.", _k_yat) == []
      and ihlal("Rutin molasında besleme yok, siz odadan çıkmıyorsunuz.", _k_yat) == [])
check("V) Mola: 'siz dinlenin diye değil, bebekle odada uyku hazırlığıdır' doğru tanım, ihlal DEĞİL",
      ihlal("Önemli: rutin molası siz de dinlenin diye değil, bebekle odada yapılan kısa bir uyku "
            "hazırlığıdır.", _k_yat) == [])
check("V) Yumuşatma: '3 deneme sonunda destekle uyutuyorsunuz; bu o gece için geçerli bir sonuç' ihlal",
      ihlal("3 deneme sonunda hâlâ uyumadıysa o geceyi destek vererek uyutuyorsunuz; bu o gece için "
            "geçerli bir sonuç, yarın eğitime devam ediyorsunuz.", k_g3n) == [("yumusatma", 0.0)])
check("V) Gündüz 'uyanmayı duyduğunuzda 10 dakika bekleyin, sonra 35 dakika' (2. gün kısa gündüz) uygun",
      _sayisal(ihlal("Eğer 60 dakikadan kısa uyursa uyanmayı duyduğunuzda önce 10 dakika bekleyin, sonra "
                     "odaya girin ve 35 dakika daha eşlik edin.", k_g2g)) == [],
      ihlal("Eğer 60 dakikadan kısa uyursa uyanmayı duyduğunuzda önce 10 dakika bekleyin, sonra odaya girin "
            "ve 35 dakika daha eşlik edin.", k_g2g))
check("V) Saatli süre: '1-1,5 saat uyanıklık' (bant 3–4 sa) ihlal; '3-4 saat' ve '3,5 saat' uygun",
      ihlal("Yaşına uygun uyanıklık süresi 1-1,5 saat civarı.", k_g2g) == [("saat_sure", 60.0), ("saat_sure", 90.0)]
      and ihlal("Uyanıklık penceresi 3-4 saat.", k_g2g) == []
      and ihlal("Uyanıklık penceresi üç buçuk saat.", k_g2g) == [])
check("V) Saatli süre: '2 saat 30 dakika' tek süre (150 dk) olarak; dakika kısmı ayrıca sayılmaz",
      ihlal("Uyanık kalma süresi 2 saat 30 dakika olsun.", k_g2g) == [("saat_sure", 150.0)]
      and ihlal("Uyanık kalma süresi 3 saat 30 dakika olsun.", k_g2g) == [])
check("V) Saatli süre: çizelge süreleri (gece uykusu 11-12 sa) ve saat (09:30) karışmaz",
      ihlal("Gece uykusu 11-12 saat sürer.", k_g2g) == [] and ihlal("Saat 2'de uyutun.", k_g2g) == [])
check("V) Saatli süre karşılığı yaş bandından; kendi doğrulamasından geçer",
      k_g2g["karsilik"]["saat_sure"] == "Bu yaşta uyanıklık penceresi 3–4 saattir."
      and ihlal(k_g2g["karsilik"]["saat_sure"], k_g2g) == [])
# --- 2026-10-12 d: hitap, rutin zamanı, kestirme (İlayda 12 Ekim) ------------
from engine import yontem as _yontem
check("K) Hitap: blokta ve sistem kuralında 'siz' kuralı",
      "- Hitap: anneye HER ZAMAN 'siz' diye hitap et" in blok2
      and "Anneye HER ZAMAN 'siz' diye hitap et; 'sen' kullanma." in sor_kural.SISTEM_KURALI)
_S = _ilk_uyku
check("K) Rutin zamanı: 'rutin uykudan ÖNCE biter' + bugünün uykuları için başlangıç aralığı (uyku − 30…15)",
      "Rutin uyku saatinden ÖNCE biter: başlangıcı = uyku saati − rutin süresi" in k_g2g["blok"]
      and f"{_ss_(_S)} uykusu → rutine {_ss_(_S - 30)}–{_ss_(_S - 15)} arasında başla" in k_g2g["blok"],
      k_g2g["blok"])
_rz = [f"{_ss_(_S)} civarında rutine başlayabilirsiniz.",
       f"{_ss_(_S - 15)}-{_ss_(_S)} arasında rutininizi başlatın.",
       f"Rutine {_ss_(_S)}'da başlayın.",
       f"{_ss_(_S)}'a doğru uyku rutinini başlatın."]
check("V) Rutin zamanı: uyku saatinde ya da sonrasında başlayan rutin ihlal",
      all(ihlal(c, k_g2g) == [("rutin_zamani", 0.0)] for c in _rz), [ihlal(c, k_g2g) for c in _rz])
_rz_ok = [f"{_ss_(_S)} uykusu için {_ss_(_S - 30)}'da rutine başlayın.",
          f"Rutine {_ss_(_S - 30)}'da başlayın.",
          f"{_ss_(_S)}'da uyku başlar, rutin {_ss_(_S - 30)}'da.",
          f"Rutin bitince {_ss_(_S)}'da yatırın."]
check("V) Rutin zamanı: uykudan önce başlayan rutin ve rutin sonrası yatış uygun",
      all(ihlal(c, k_g2g) == [] for c in _rz_ok), [ihlal(c, k_g2g) for c in _rz_ok])
check("V) Düzelt: rutin zamanı karşılığı (sonraki uyku için aralık); karşılık kendi doğrulamasından geçer",
      sor_kural.duzelt(_rz[0], k_g2g) == k_g2g["karsilik"]["rutin_zamani"]
      and "rutine" in k_g2g["karsilik"]["rutin_zamani"] and ihlal(k_g2g["karsilik"]["rutin_zamani"], k_g2g) == [],
      k_g2g["karsilik"]["rutin_zamani"])
check("T) Tablo v0.3: kestirme 30 dk (İlayda 12 Ekim), açığa göre uzamaz",
      _yontem.deger("kestirme.sure_dk") == 30 and _yontem.deger("kestirme.aciga_gore_uzamaz") is True
      and any(k.get("tur") == "ilayda_12_ekim" for k in _yontem.kaynaklar("kestirme.sure_dk")))
check("K) Kestirme: blok 30 dk der, açığa göre uzamaz",
      "- Kestirme (şekerleme): 30 dk; gündüz açığına göre UZAMAZ" in k_g2g["blok"], k_g2g["blok"])
check("V) Kestirme: '60 dakikalık kestirme' ihlal, '30 dakikalık kestirme yeterli' uygun",
      ihlal("Gündüz açığı büyükse 60 dakikalık kestirme yaptırın.", k_g2g) == [("kestirme", 60.0)]
      and ihlal("30 dakikalık kestirme yeterli.", k_g2g) == []
      and ihlal(k_g2g["karsilik"]["kestirme"], k_g2g) == [])
check("V) Yeni karşılıklar (yatis_dis, mola_tanimi) kendi doğrulamasından geçer",
      all(ihlal(k["karsilik"][r], k) == [] for k in (_k_yat, k_g2g, k_g8) for r in ("yatis_dis", "mola_tanimi")))

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


def usage_sabit():
    """Kuyruktaki önceki yazımlar bitene dek bekle (sayı 0,5 sn değişmesin)."""
    import time
    n = usage_sayisi()
    for _ in range(20):
        time.sleep(0.5)
        m = usage_sayisi()
        if m == n:
            return n
        n = m
    return n


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
bayrak(SOR_KURAL_BLOGU=True, SOR_SAYI_DOGRULAMA="yeniden")
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

bayrak(SOR_KURAL_BLOGU=True, SOR_SAYI_DOGRULAMA="duzelt")
_u0 = usage_sabit()
cev, log = sor(H, BID, ["Önce sakin olun. İkinci uyanmada 10 dakika bekleyin."], an2)
check("F) duzelt: TEK model çağrısı (düzeltme notu yok), cümle karşılıkla değişti",
      len(YUKLER) == 1 and "DÜZELTME" not in YUKLER[0]["messages"][-1]["content"]
      and usage_sayisi(_u0 + 1) == _u0 + 1
      and cev == "Önce sakin olun. " + k_g2["karsilik"]["gece_uyanma"], (len(YUKLER), cev))
check("F) duzelt: log içeriksiz — ihlal sayısı/türü, değişen/çıkan",
      "dogrulama=duzeltildi n=1 tur=gece_uyanma degisen=1 cikan=0" in log and "uyanmada" not in log, log)
cev, log = sor(H, BID, ["İçeride 45 dakika deneyin."], an2)
check("F) duzelt: ihlal yoksa cevap aynen, dogrulama=gecti", cev == "İçeride 45 dakika deneyin."
      and "dogrulama=gecti" in log and len(YUKLER) == 1, log)
os.environ["SOR_SAYI_DOGRULAMA"] = "true"
cev, log = sor(H, BID, ["İkinci uyanmada 10 dakika bekleyin."], an2)
check("F) Eski 'true' değeri duzelt sayılır: tek çağrı", len(YUKLER) == 1
      and "dogrulama=duzeltildi" in log, (len(YUKLER), log))
bayrak(SOR_KURAL_BLOGU=True, SOR_SAYI_DOGRULAMA="izle")
cev, log = sor(H, BID, ["İkinci uyanmada 10 dakika bekleyin. Yarın 5 gün kaldı."], an2)
check("F) izle: cevap DEĞİŞMEZ, tek çağrı, log'da yalnız sayı ve tür",
      len(YUKLER) == 1 and cev == "İkinci uyanmada 10 dakika bekleyin. Yarın 5 gün kaldı."
      and "dogrulama=izle n=2 tur=gece_uyanma,gun" in log and "uyanmada" not in log, log)
bayrak(SOR_KURAL_BLOGU=True, SOR_SAYI_DOGRULAMA="kapali")
cev, log = sor(H, BID, ["İkinci uyanmada 10 dakika bekleyin."], an2)
check("F) kapali (blok açık): blok gider, doğrulama yok",
      len(YUKLER) == 1 and "BUGÜNÜN KURALLARI" in YUKLER[0]["messages"][-1]["content"]
      and "dogrulama=-" in log and cev == "İkinci uyanmada 10 dakika bekleyin.", log)
# main birleşmesi (v2.8.4): kapanış mesajı (SOR_GECMIS) model çağırmaz; bütün
# bayraklar açıkken de doğrulama devreye girmez, cevap sabit kapanış metni.
bayrak(SOR_KURAL_BLOGU=True, SOR_SAYI_DOGRULAMA="duzelt", SOR_BAGLAM_V2=True, SOR_GECMIS=True)
CEVAPLAR[:] = []
YUKLER.clear()
with saat_sabitle(an2):
    _r = client.post("/api/v1/chat", headers=H, json={"message": "Çok teşekkürler 🙏", "baby_id": BID})
_log = [m for m in _loglar if m.startswith("chat: ")][-1]
check("F) Kapanış mesajı (bayraklar açık): model çağrısı yok, doğrulama yok, kapanış metni",
      _r.status_code == 200 and len(YUKLER) == 0 and "dogrulama=-" in _log
      and _r.json()["answer"] == chatbot.kapanis_cevabi()["cevap"], (_r.status_code, len(YUKLER), _log))
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
