"""
PREMIUM ABONELİK — kademe (ucretsiz / deneme / premium). Ağ YOK, LLM YOK.

  K  Kademe kararı: beta → premium; BETA_HARIC_EMAILS gerçek kademeyi görür;
     TRIAL → deneme; NORMAL / premium_haklari → premium; süresiz kayıt sayılmaz
  W  Webhook: TRIAL satın alma → deneme + kullanıcının deneme alanları;
     RENEWAL (NORMAL) → premium
  A  GET /abonelik/durum alanları; POST /abonelik/senkron (RevenueCat REST sahte)
  V  /subscriptions/verify istemci makbuzuna güvenmez; sahte makbuz yalnız
     production dışında; RevenueCat'te kayıt yoksa 404
  P  Plan gün gün: deneme yalnız 1-3. gün bölümü + kilitli_gunler; ücretsizde
     program ve gece kartı yok; premium aynen; tipsiz eski plan da kilitli;
     bekleme planı önizlemesiz; /account/export aynı kilit
  B  PATCH /babies: premium değilse training_started_at geriye alınamaz
  S  Sor: ücretsiz 403 PREMIUM_GEREKLI; deneme 10 soru sonra 429 GUNLUK_LIMIT;
     kapanış sayılmaz; önbellek cevabı sayılır; model hatasında hak iade
  R  Sor kural bloğu denemede 3. günden sonrasının aşamasını söylemez
  N  Eğitim bildirimleri ücretsizde kapalı, denemede açık
  E  kilitli_mi matrisi

Çalıştırma: python tests/test_abonelik_kademe.py
"""
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "abonelik_kademe_test.db"
if _DB.exists():
    _DB.unlink()
SIR = "rc-webhook-test-siri-kademe"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["REVENUECAT_WEBHOOK_SECRET"] = SIR
os.environ["REVENUECAT_API_KEY"] = "sk_test_sahte"
os.environ.pop("LANSMAN_TARIHI", None)
os.environ["LOG_GELECEK_TOLERANS_DK"] = "1440"
os.environ["MEDIA_ROOT"] = str(Path(tempfile.gettempdir()) / "kademe_medya")
# Beta AÇIK (prod gibi); haric listesindekiler gerçek kademeyi görür.
os.environ["BETA_MODE"] = "true"
os.environ["BETA_HARIC_EMAILS"] = ("ucretsiz@gercek.com, Deneme@Gercek.com,premium@gercek.com,"
                                   "suresiz@gercek.com,hak@gercek.com,webhook@gercek.com,"
                                   "senkron@gercek.com,verify@gercek.com,bekleme@gercek.com,"
                                   "sor@gercek.com,bildirim@gercek.com")
os.environ["SOR_GECMIS"] = "true"                 # kapanış mesajı modele gitmez
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.config import (DENEME_SON_GUN, ERISIM_KURALLARI,   # noqa: E402
                        SOR_GUNLUK_DENEME, get_settings)
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import (Baby, SleepPlan, SorSayac,          # noqa: E402
                        Subscription, User)
from api.main import app                                    # noqa: E402
from api.services import erisim, notifier, sor_baglam, sor_kural  # noqa: E402
from api.services import premium as premium_svc            # noqa: E402
from api.zaman import TR, bugun_tr, saat_sabitle            # noqa: E402
from engine import chatbot                                  # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, muhurle            # noqa: E402
muhurle()
get_settings.cache_clear()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)[:300]))


SIMDI = datetime.now(timezone.utc)
MS = lambda t: int(t.timestamp() * 1000)                     # noqa: E731
URUN = "tavsan_premium_aylik"


def hesap(email: str):
    tok = client.post("/api/v1/auth/register",
                      json={"email": email, "password": "TestPass123!"}
                      ).json()["access_token"]
    db = SessionLocal()
    try:
        uid = db.query(User).filter(User.email == email.lower()).one().id
    finally:
        db.close()
    return {"Authorization": f"Bearer {tok}"}, uid


def abonelik(uid, period="NORMAL", gun=30, bitis="var", status="active"):
    db = SessionLocal()
    try:
        db.add(Subscription(user_id=uid, platform="ios", product_id=URUN, status=status,
                            period_type=period, purchased_at=SIMDI,
                            expires_at=None if bitis is None else SIMDI + timedelta(days=gun)))
        db.commit()
    finally:
        db.close()


def kademe(uid):
    db = SessionLocal()
    try:
        return premium_svc.durum(db, db.get(User, uid))["kademe"]
    finally:
        db.close()


def bebek(h, gun_once=245):
    return client.post("/api/v1/babies", headers=h, json={
        "name": "Bebek", "night_wakes": 2, **TAM_PROFIL,
        "birth_date": (bugun_tr() - timedelta(days=gun_once)).isoformat()}).json()["id"]


def egitim_baslat(bid, gun_no):
    """Eğitimin `gun_no`. günündeyiz (bugün dahil)."""
    db = SessionLocal()
    try:
        db.get(Baby, uuid.UUID(bid)).training_started_at = bugun_tr() - timedelta(days=gun_no - 1)
        db.commit()
    finally:
        db.close()


# =============================================================================
# K — kademe kararı
# =============================================================================
HB, UB = hesap("beta@gercek.com")
HU, UU = hesap("ucretsiz@gercek.com")
HD, UD = hesap("deneme@gercek.com")             # listede büyük harfle — eşleşmeli
HP, UP = hesap("premium@gercek.com")
HS0, US0 = hesap("suresiz@gercek.com")
HH, UH = hesap("hak@gercek.com")
abonelik(UD, period="TRIAL", gun=3)
abonelik(UP, period="NORMAL")
abonelik(US0, period="NORMAL", bitis=None)        # eski /verify gibi süresiz satır
_db = SessionLocal()
try:
    premium_svc.hak_ver(_db, _db.get(User, UH), 30, None, "test")
finally:
    _db.close()
check("K1) Beta açık, listede değil → premium", kademe(UB) == "premium", kademe(UB))
check("K2) BETA_HARIC_EMAILS → beta'da bile gerçek kademe (ucretsiz)",
      kademe(UU) == "ucretsiz", kademe(UU))
check("K3) TRIAL aktif → deneme (e-posta listesi büyük/küçük harf duyarsız)",
      kademe(UD) == "deneme", kademe(UD))
check("K4) NORMAL aktif → premium", kademe(UP) == "premium", kademe(UP))
check("K5) Bitişi boş 'active' kayıt erişim VERMEZ → ucretsiz", kademe(US0) == "ucretsiz",
      kademe(US0))
check("K6) premium_haklari → premium", kademe(UH) == "premium", kademe(UH))
_d = client.get("/api/v1/subscriptions/status", headers=HD).json()
check("K7) /subscriptions/status kademeyi taşıyor; deneme premium SAYILMAZ",
      _d["kademe"] == "deneme" and _d["premium"] is False and _d["period_type"] == "TRIAL", _d)

# =============================================================================
# W — webhook
# =============================================================================
HW, UW = hesap("webhook@gercek.com")


def olay(tur, period, gun, eid=None):
    return {"api_version": "1.0", "event": {
        "id": eid or str(uuid.uuid4()), "type": tur, "app_user_id": str(UW),
        "product_id": URUN, "entitlement_ids": ["premium"], "store": "APP_STORE",
        "environment": "SANDBOX", "period_type": period, "purchased_at_ms": MS(SIMDI),
        "expiration_at_ms": MS(SIMDI + timedelta(days=gun))}}


r = client.post("/api/v1/webhooks/revenuecat", json=olay("INITIAL_PURCHASE", "TRIAL", 3),
                headers={"Authorization": SIR})
_db = SessionLocal()
try:
    _u = _db.get(User, UW)
    check("W1) TRIAL satın alma → deneme; deneme_baslangic/bitis/kullanildi yazıldı",
          r.status_code == 200 and kademe(UW) == "deneme"
          and _u.deneme_baslangic is not None and _u.deneme_bitis is not None
          and _u.deneme_kullanildi_at is not None, (r.text, kademe(UW)))
    _ilk_kullanim = _u.deneme_kullanildi_at
finally:
    _db.close()
r = client.post("/api/v1/webhooks/revenuecat", json=olay("RENEWAL", "NORMAL", 33),
                headers={"Authorization": SIR})
_db = SessionLocal()
try:
    check("W2) Denemeden ücretliye (RENEWAL, NORMAL) → premium; deneme kaydı korunur",
          kademe(UW) == "premium"
          and _db.get(User, UW).deneme_kullanildi_at == _ilk_kullanim, kademe(UW))
finally:
    _db.close()

# =============================================================================
# A — /abonelik/durum + /abonelik/senkron
# =============================================================================
a = client.get("/api/v1/abonelik/durum", headers=HU).json()
check("A1) Ücretsiz: durum, deneme hakkı var, sor_kalan 0, deneme alanları null",
      a == {"durum": "ucretsiz", "deneme_gunu": None, "deneme_bitis": None,
            "deneme_hakki_var": True, "sor_kalan": 0}, a)
a = client.get("/api/v1/abonelik/durum", headers=HD).json()
check("A2) Deneme: deneme_gunu 1, bitiş dolu, hak yok, sor_kalan 10",
      a["durum"] == "deneme" and a["deneme_gunu"] == 1 and a["deneme_bitis"]
      and a["deneme_hakki_var"] is False and a["sor_kalan"] == SOR_GUNLUK_DENEME, a)
_db = SessionLocal()
try:
    _u = _db.get(User, UD)
    _u.deneme_baslangic = SIMDI - timedelta(days=2)
    _db.commit()
finally:
    _db.close()
a = client.get("/api/v1/abonelik/durum", headers=HD).json()
check("A3) Denemenin 3. günü → deneme_gunu 3", a["deneme_gunu"] == 3, a)
a = client.get("/api/v1/abonelik/durum", headers=HP).json()
check("A4) Premium: sor_kalan null (sınırsız), deneme alanları null",
      a["durum"] == "premium" and a["sor_kalan"] is None and a["deneme_gunu"] is None
      and a["deneme_bitis"] is None and a["deneme_hakki_var"] is False, a)
a = client.get("/api/v1/abonelik/durum", headers=HB).json()
check("A5) Beta kullanıcısı premium görünür", a["durum"] == "premium", a)

HY, UY = hesap("senkron@gercek.com")
_gercek_cek = premium_svc.musteri_cek
premium_svc.musteri_cek = lambda uid: {"subscriber": {"subscriptions": {URUN: {
    "store": "APP_STORE", "period_type": "TRIAL", "is_sandbox": True,
    "purchase_date": SIMDI.isoformat(),
    "expires_date": (SIMDI + timedelta(days=3)).isoformat()}}}}
a = client.post("/api/v1/abonelik/senkron", headers=HY).json()
check("A6) /abonelik/senkron RevenueCat'ten çekip deneme yazdı, aynı cevap şekli",
      a.get("durum") == "deneme" and a.get("deneme_gunu") == 1
      and set(a) == {"durum", "deneme_gunu", "deneme_bitis", "deneme_hakki_var", "sor_kalan"}, a)
premium_svc.musteri_cek = _gercek_cek
get_settings().revenuecat_api_key = ""
r = client.post("/api/v1/abonelik/senkron", headers=HY)
check("A7) Mağaza anahtarı yoksa senkron 503", r.status_code == 503, r.status_code)
get_settings().revenuecat_api_key = "sk_test_sahte"

# =============================================================================
# V — /subscriptions/verify
# =============================================================================
HV, UV = hesap("verify@gercek.com")
cagri = []
premium_svc.musteri_cek = lambda uid: cagri.append(uid) or {"subscriber": {"subscriptions": {}}}
r = client.post("/api/v1/subscriptions/verify", headers=HV,
                json={"platform": "ios", "product_id": URUN, "receipt_data": "GERCEK-GIBI-MAKBUZ"})
check("V1) Makbuz karar vermez: RevenueCat'e soruldu, kayıt yok → 404, kademe ucretsiz",
      r.status_code == 404 and cagri == [UV] and kademe(UV) == "ucretsiz", (r.status_code, r.text))
premium_svc.musteri_cek = lambda uid: {"subscriber": {"subscriptions": {URUN: {
    "store": "APP_STORE", "period_type": "NORMAL", "is_sandbox": False,
    "purchase_date": SIMDI.isoformat(),
    "expires_date": (SIMDI + timedelta(days=30)).isoformat()}}}}
r = client.post("/api/v1/subscriptions/verify", headers=HV,
                json={"platform": "ios", "product_id": URUN, "receipt_data": "x"})
check("V2) RevenueCat'te aktif → 200, bitiş RevenueCat'ten, premium",
      r.status_code == 200 and r.json()["expires_at"] and kademe(UV) == "premium", r.text)
premium_svc.musteri_cek = _gercek_cek
get_settings().revenuecat_api_key = ""
r = client.post("/api/v1/subscriptions/verify", headers=HD,
                json={"platform": "ios", "product_id": URUN, "receipt_data": "x"})
check("V3) Mağaza anahtarı yoksa 503 (kayıt yazılmaz)", r.status_code == 503, r.status_code)
get_settings().revenuecat_api_key = "sk_test_sahte"
H2, U2 = hesap("mock@ornek-test.com")
r = client.post("/api/v1/subscriptions/verify", headers=H2,
                json={"platform": "ios", "product_id": URUN, "receipt_data": "mock_trial_1"})
_db = SessionLocal()
try:
    _s = _db.query(Subscription).filter(Subscription.user_id == U2).one()
    check("V4) Sahte makbuz geliştirmede: süreli SANDBOX deneme kaydı",
          r.status_code == 200 and _s.expires_at is not None and _s.environment == "SANDBOX"
          and _s.period_type == "TRIAL", r.text)
finally:
    _db.close()
get_settings().is_production = True
r = client.post("/api/v1/subscriptions/verify", headers=H2,
                json={"platform": "ios", "product_id": URUN, "receipt_data": "mock_receipt"})
get_settings().is_production = False
check("V5) Sahte makbuz production'da 400", r.status_code == 400, r.status_code)

# =============================================================================
# P — plan gün gün
# =============================================================================
BP = bebek(HP)
BD = bebek(HD)
BU = bebek(HU)
for h, b in ((HP, BP), (HD, BD), (HU, BU)):
    client.post("/api/v1/plans/generate?sync=true", headers=h, json={"baby_id": b})
for b in (BP, BD, BU):
    egitim_baslat(b, 2)


def plan(h, b):
    return client.get(f"/api/v1/plans/today?baby_id={b}", headers=h).json()


pp, pd, pu = plan(HP, BP), plan(HD, BD), plan(HU, BU)
_tam = pp["content"].get("days") or []
check("P0) Ön koşul: premium planında 13 günün bölümleri var",
      len(_tam) >= 2 and max(d["end"] for d in _tam) == 13, [(d["start"], d["end"]) for d in _tam])
check("P1) Premium: içerik aynen, kilitli_gunler YOK, locked yok",
      "kilitli_gunler" not in pp["content"] and not pp.get("locked")
      and len(pp["content"]["days"]) == len(_tam), pp.get("locked"))
_dd = pd["content"]["days"]
check("P2) Deneme 2. gün: yalnız 1-3 bölümü; kilitli_gunler 4..13",
      [(d["start"], d["end"]) for d in _dd] == [(1, 3)]
      and pd["content"]["kilitli_gunler"] == list(range(4, 14)), (pd["content"].get("kilitli_gunler"),
                                                                  [(d["start"], d["end"]) for d in _dd]))
check("P3) Deneme markdown izinli bölümden yeniden kuruldu (gelecek gün metni yok)",
      pd["content"]["markdown"] == _dd[0]["markdown"]
      and all(d["markdown"] not in pd["content"]["markdown"] for d in _tam[1:]), "")
check("P4) Deneme: gece kartı VAR, eski bayrak egitim_programi_kilitli=false",
      ("night_wake_protocol" in pd["content"]) == ("night_wake_protocol" in pp["content"])
      and pd["content"]["egitim_programi_kilitli"] is False
      and pd.get("premium_required") is True, pd.get("locked"))
check("P5) Ücretsiz: program yok, markdown boş, kilitli_gunler 1..13, gece kartı yok",
      pu["content"]["days"] == [] and pu["content"]["markdown"] == ""
      and pu["content"]["kilitli_gunler"] == list(range(1, 14))
      and "night_wake_protocol" not in pu["content"]
      and pu["content"].get("schedule"), pu.get("locked"))
egitim_baslat(BD, 6)
pd6 = plan(HD, BD)
check("P6) Deneme ama eğitimin 6. günü → yine yalnız 1-3 (min(gün, 3))",
      [(d["start"], d["end"]) for d in pd6["content"]["days"]] == [(1, DENEME_SON_GUN)],
      pd6["content"].get("kilitli_gunler"))
_db = SessionLocal()
try:
    _p = _db.query(SleepPlan).filter(SleepPlan.baby_id == uuid.UUID(BU)).first()
    _c = dict(_p.content)
    _c.pop("type", None)
    _p.content = _c
    _db.commit()
finally:
    _db.close()
pu2 = plan(HU, BU)
check("P7) type alanı olmayan eski plan da kilitli", pu2["content"]["days"] == []
      and pu2["content"]["markdown"] == "", pu2.get("locked"))
HK, UK = hesap("bekleme@gercek.com")
BK = bebek(HK, gun_once=110)                     # ~3,5 ay → eğitim bekleme
client.post("/api/v1/plans/generate?sync=true", headers=HK, json={"baby_id": BK})
pk = plan(HK, BK)
check("P8) Bekleme planı (ücretsiz): 13 günlük önizleme yok, Eğitim Planı bölümü çıkarıldı",
      pk["content"].get("type") == "egitim_bekleme" and pk["content"]["days"] == []
      and not any(l.startswith("#") and ("eğitim planı" in l.lower() or "gün " in l.lower())
                  for l in (pk["content"]["markdown"] or "").splitlines())
      and (pk["content"]["markdown"] or "").strip() != ""
      and "egitim_onizleme" in (pk.get("locked") or []),
      (pk["content"].get("type"), pk.get("locked")))
ex = client.get("/api/v1/account/export", headers=HU).json()
_pl = [x for x in ex["planlar"] if x["baby_id"] == BU]
check("P9) /account/export ücretsizde aynı kilit (days boş, markdown boş)",
      _pl and all(x["content"]["days"] == [] and x["content"]["markdown"] == "" for x in _pl),
      len(_pl))
ex = client.get("/api/v1/account/export", headers=HD).json()
_pl = [x for x in ex["planlar"] if x["baby_id"] == BD]
check("P10) /account/export denemede yalnız 1-3",
      _pl and all([(d["start"], d["end"]) for d in x["content"]["days"]] == [(1, 3)] for x in _pl),
      "")

# =============================================================================
# B — training_started_at geriye alınamaz
# =============================================================================
b = client.patch(f"/api/v1/babies/{BD}", headers=HD,
                 json={"training_started_at": (bugun_tr() - timedelta(days=20)).isoformat()}).json()
check("B1) Deneme: başlangıç geriye alınamadı (mevcut başlangıç korunur)",
      b["training_started_at"] == (bugun_tr() - timedelta(days=5)).isoformat(),
      b["training_started_at"])
BU2 = bebek(HU)
client.post("/api/v1/plans/generate?sync=true", headers=HU, json={"baby_id": BU2})
b = client.patch(f"/api/v1/babies/{BU2}", headers=HU,
                 json={"training_started_at": (bugun_tr() - timedelta(days=9)).isoformat()}).json()
check("B2) Ücretsiz, başlangıç yokken geçmiş tarih → bugün", b["training_started_at"]
      == bugun_tr().isoformat(), b["training_started_at"])
b = client.patch(f"/api/v1/babies/{BP}", headers=HP,
                 json={"training_started_at": (bugun_tr() - timedelta(days=9)).isoformat()}).json()
check("B3) Premium geriye alabilir", b["training_started_at"]
      == (bugun_tr() - timedelta(days=9)).isoformat(), b["training_started_at"])

# =============================================================================
# S — Sor sayacı
# =============================================================================
CEVAP = {"cevap": "Cevap", "cache_hit": False, "kaynaklar": [], "llm": False,
         "retrieval_layer": "k1", "top_score": 0.9}
chatbot._cevap_uret = lambda *a, **k: dict(CEVAP)
r = client.post("/api/v1/chat", headers=HU, json={"message": "soru"})
check("S1) Ücretsiz: 403 + hata PREMIUM_GEREKLI", r.status_code == 403
      and r.json().get("hata") == "PREMIUM_GEREKLI" and r.json().get("premium_required"), r.text)
HQ, UQ = hesap("sor@gercek.com")
abonelik(UQ, period="TRIAL", gun=3)
cevaplar = [client.post("/api/v1/chat", headers=HQ, json={"message": f"soru {i}"})
            for i in range(SOR_GUNLUK_DENEME)]
check("S2) Deneme: 10 soru 200, kalan 9 → 0",
      [x.status_code for x in cevaplar] == [200] * SOR_GUNLUK_DENEME
      and [x.json()["ucretsiz_kalan"] for x in cevaplar] == list(range(SOR_GUNLUK_DENEME - 1, -1, -1)),
      [(x.status_code, x.json().get("ucretsiz_kalan")) for x in cevaplar])
r = client.post("/api/v1/chat", headers=HQ, json={"message": "on birinci"})
check("S3) 11. soru 429 + hata GUNLUK_LIMIT", r.status_code == 429
      and r.json().get("hata") == "GUNLUK_LIMIT", r.text)
r = client.post("/api/v1/chat", headers=HQ, json={"message": "Teşekkürler 🙏"})
check("S4) Kapanış mesajı limitte bile cevaplanır ve sayılmaz", r.status_code == 200, r.text)
_db = SessionLocal()
try:
    _say = lambda: _db.query(SorSayac.sayi).filter(SorSayac.user_id == UQ).scalar()  # noqa: E731
    _s = _say()
    _db.query(SorSayac).filter(SorSayac.user_id == UQ).update({SorSayac.sayi: 0})
    _db.commit()
finally:
    _db.close()
check("S5) Sayaç tam 10'da kaldı (kapanış artırmadı)", _s == SOR_GUNLUK_DENEME, _s)
chatbot._cevap_uret = lambda *a, **k: dict(CEVAP, cache_hit=True)
client.post("/api/v1/chat", headers=HQ, json={"message": "önbellekten"})


def _bozuk(*a, **k):
    raise RuntimeError("model düştü")


chatbot._cevap_uret = _bozuk
_hata_client = TestClient(app, raise_server_exceptions=False)
r = _hata_client.post("/api/v1/chat", headers=HQ, json={"message": "hata"})
_db = SessionLocal()
try:
    _s = _db.query(SorSayac.sayi).filter(SorSayac.user_id == UQ).scalar()
finally:
    _db.close()
check("S6) Önbellek cevabı sayıldı (1); model hatasında hak iade (yine 1)",
      r.status_code == 500 and _s == 1, (r.status_code, _s))
chatbot._cevap_uret = lambda *a, **k: dict(CEVAP)
from api.services import sor_hakki                          # noqa: E402
_db = SessionLocal()
try:
    _u = _db.get(User, UQ)
    _db.query(SorSayac).filter(SorSayac.user_id == UQ).update({SorSayac.sayi: SOR_GUNLUK_DENEME - 1})
    _db.commit()
    _a, _b = sor_hakki.hak_al(_db, _u, SOR_GUNLUK_DENEME), sor_hakki.hak_al(_db, _u, SOR_GUNLUK_DENEME)
finally:
    _db.close()
check("S7) Son hak tek kez alınır (koşullu artırma: ikinci istek alamaz)",
      _a is not None and _b is None, (_a, _b))
r = client.post("/api/v1/chat", headers=HP, json={"message": "premium soru"})
check("S8) Premium sınırsız: ucretsiz_kalan null", r.status_code == 200
      and r.json().get("ucretsiz_kalan") is None, r.text)

# =============================================================================
# R — Sor kural bloğu aşama sınırı
# =============================================================================
_db = SessionLocal()
try:
    _bb = _db.get(Baby, uuid.UUID(BD))
    _bb.training_started_at = bugun_tr() - timedelta(days=2)          # 3. gün
    _db.commit()
    _an = datetime.combine(bugun_tr(), datetime.min.time(), tzinfo=TR) + timedelta(hours=14)
    with saat_sabitle(_an):
        _d = sor_baglam.gun_durumu(_db, _bb)
        k_sinirli = sor_kural.kurallar(_d, _bb, asama_siniri=DENEME_SON_GUN)
        k_tam = sor_kural.kurallar(_d, _bb)
        z_sinirli = sor_baglam.zaman_satirlari(_db, _bb, simdi=_an, asama_siniri=DENEME_SON_GUN)
    _bb.training_started_at = bugun_tr() - timedelta(days=4)          # 5. gün
    _db.commit()
    with saat_sabitle(_an):
        k5 = sor_kural.kurallar(sor_baglam.gun_durumu(_db, _bb), _bb, asama_siniri=DENEME_SON_GUN)
finally:
    _db.close()
_takvim = lambda k: next((l for l in (k or {}).get("blok", "").splitlines() if "Takvim" in l), "")  # noqa: E731
check("R1) 3. gün, sınırlı: yarın '4. günü' — aşama adı YOK; sınırsızda var",
      "4. günü" in _takvim(k_sinirli) and "aşama" not in _takvim(k_sinirli).split("yarın")[-1]
      and "aşama" in _takvim(k_tam).split("yarın")[-1], (_takvim(k_sinirli), _takvim(k_tam)))
check("R2) 5. gün, sınırlı: kural bloğu hiç kurulmaz", k5 is None, bool(k5))
check("R3) Zaman satırı sınırlı: plan bölümü 1-3'ten (Gün 4-6 metni yok)",
      z_sinirli is not None and "Gün 4" not in z_sinirli, (z_sinirli or "")[:200])

# =============================================================================
# N — eğitim bildirimleri
# =============================================================================
_db = SessionLocal()
try:
    _onb: dict = {}
    _n = (notifier._egitim_bildirimi_kilitli(_db, _db.get(User, UU), _onb),
          notifier._egitim_bildirimi_kilitli(_db, _db.get(User, UD), _onb),
          notifier._egitim_bildirimi_kilitli(_db, _db.get(User, UB), _onb))
finally:
    _db.close()
check("N1) Eğitim bildirimi: ücretsiz kilitli, deneme ve beta/premium açık",
      _n == (True, False, False), _n)

# =============================================================================
# E — kilitli_mi matrisi
# =============================================================================
_m = {o: tuple(erisim.kilitli_mi(o, k) for k in ("ucretsiz", "deneme", "premium"))
      for o in ("egitim_programi", "gece_karti", "egitim_bildirimleri", "sor", "video_diger",
                "anne_sesi", "ses_diger", "kayit_girme")}
check("E1) Kural matrisi (ücretsiz, deneme, premium)", _m == {
    "egitim_programi": (True, False, False), "gece_karti": (True, False, False),
    "egitim_bildirimleri": (True, False, False), "sor": (True, False, False),
    "video_diger": (True, True, False), "anne_sesi": (True, True, False),
    "ses_diger": (False, False, False), "kayit_girme": (False, False, False)}, _m)
check("E2) Eski bool çağrı uyumlu (True → premium, False → ücretsiz)",
      erisim.kilitli_mi("video_diger", True) is False
      and erisim.kilitli_mi("video_diger", False) is True, "")
check("E3) Ürünler: yalnız aylık 1.499,99 TL / 3 gün deneme (eski 999 TL / 7 gün yok)",
      premium_svc.URUNLER.get(URUN) == {"tur": "abonelik", "fiyat_tl": 1499.99, "donem": "P1M",
                                         "deneme_gun": 3}
      and "tu_aylik" not in premium_svc.URUNLER and "tu_yillik" not in premium_svc.URUNLER, "")
from api.services import denetim as _denetim                 # noqa: E402
from types import SimpleNamespace as _N                      # noqa: E402
check("E5) App Review demo hesabı test hesabı: denetim/numara ve kurucu listesi dışında",
      "appreview@tavsanuykusu.com.tr".endswith(_denetim.TEST_ALANLARI)
      and premium_svc.kurucu_disinda_mi(None, _N(email="appreview@tavsanuykusu.com.tr"))
      and not "anne@tavsanuykusu.com.tr".endswith(_denetim.TEST_ALANLARI), _denetim.TEST_ALANLARI)
check("E4) Bilinmeyen özellik açık", ERISIM_KURALLARI.get("yok") is None
      and erisim.kilitli_mi("yok", "ucretsiz") is False, "")

# --- Özet --------------------------------------------------------------------
print("=" * 78)
print("PREMIUM ABONELİK — KADEME TEST SONUÇLARI")
print("=" * 78)
gecen = 0
for ad, ok, detay in results:
    gecen += ok
    print(f"  {'✓' if ok else '✗'} {ad}" + ("" if ok else f"  —  {detay}"))
print("-" * 78)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
