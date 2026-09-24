"""
ÖDEME ALTYAPISI (B4 — RevenueCat) + erişim kuralları. Ağ YOK, LLM YOK.

  W  Webhook: yanlış/eksik sır 401; satın alma(deneme)→yenileme→iptal→bitiş;
     idempotency (aynı event.id); yenilenmeyen 45 gün; ödeme sorunu + grace;
     ürün değişimi; bilinmeyen kullanıcı; entitlement'sız olay
  R  /subscriptions/refresh RevenueCat müşteri kaydından tabloyu günceller
  K  Kurucu üye: lansmandan önce kayıt → lansmandan sonra 30 gün premium;
     sonradan kayıt olan değil; 31. gün bitti
  M  Manuel hak: yalnız admin (moderatör); e-postayla; ikinci hak UZATIR
  B  BETA_MODE her şeyi açar
  L  BETA_MODE kapalı + premium yok: doğru uçlar kilitli, ücretsizler açık

Çalıştırma: python tests/test_odeme.py
"""
import io
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

_DB = Path(tempfile.gettempdir()) / "odeme_test.db"
if _DB.exists():
    _DB.unlink()
SIR = "rc-webhook-test-siri-uzun"
LANSMAN = (datetime.now(timezone.utc) - timedelta(days=10)).date()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["REVENUECAT_WEBHOOK_SECRET"] = SIR
os.environ["REVENUECAT_API_KEY"] = "sk_test_sahte"
os.environ["LANSMAN_TARIHI"] = LANSMAN.isoformat()
os.environ["LOG_GELECEK_TOLERANS_DK"] = "1440"
os.environ["MEDIA_ROOT"] = str(Path(tempfile.gettempdir()) / "odeme_medya")
os.environ.pop("BETA_MODE", None)
os.environ.pop("BETA_PREMIUM_ALL", None)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ["ELEVENLABS_API_KEY"] = "test-key"

from fastapi.testclient import TestClient                   # noqa: E402
from api.config import get_settings                         # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import (CommunityProfile, EducationVideo,  # noqa: E402
                        RevenueCatOlayi, SleepSound, Subscription, User)
from api.main import app                                    # noqa: E402
from api.services import premium as premium_svc, storage    # noqa: E402
from engine import chatbot                                  # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, muhurle            # noqa: E402
muhurle()
get_settings.cache_clear()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


SIMDI = datetime.now(timezone.utc)
MS = lambda t: int(t.timestamp() * 1000)                     # noqa: E731


def hesap(email: str):
    tok = client.post("/api/v1/auth/register",
                      json={"email": email, "password": "TestPass123!"}
                      ).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    db = SessionLocal()
    try:
        uid = db.query(User).filter(User.email == email).one().id
    finally:
        db.close()
    return h, uid


def olay(tur, uid, eid=None, urun="tu_aylik", **ek):
    ev = {"id": eid or str(uuid.uuid4()), "type": tur, "app_user_id": str(uid),
          "product_id": urun, "entitlement_ids": ["premium"], "store": "APP_STORE",
          "environment": "SANDBOX", "period_type": "NORMAL",
          "purchased_at_ms": MS(SIMDI), **ek}
    return {"api_version": "1.0", "event": ev}


def gonder(govde, sir=SIR):
    h = {"Authorization": sir} if sir is not None else {}
    return client.post("/api/v1/webhooks/revenuecat", json=govde, headers=h)


def durum(h):
    return client.get("/api/v1/subscriptions/status", headers=h).json()


# =============================================================================
# W — Webhook
# =============================================================================
HA, UA = hesap("alici@gercek.com")
r = gonder(olay("INITIAL_PURCHASE", UA), sir="yanlis")
check("W1) Yanlış sır → 401", r.status_code == 401, str(r.status_code))
r = gonder(olay("INITIAL_PURCHASE", UA), sir=None)
check("W2) Sırsız istek → 401", r.status_code == 401, str(r.status_code))
check("W3) 401 hiçbir şey yazmadı", durum(HA)["premium"] is False, str(durum(HA)))

E1 = str(uuid.uuid4())
r = gonder(olay("INITIAL_PURCHASE", UA, eid=E1, period_type="TRIAL",
                expiration_at_ms=MS(SIMDI + timedelta(days=7))))
d = durum(HA)
check("W4) Deneme başladı → premium, source=store, TRIAL, yenilenecek",
      r.status_code == 200 and d["premium"] and d["source"] == "store"
      and d["period_type"] == "TRIAL" and d["will_renew"] is True
      and d["product_id"] == "tu_aylik", f"{r.json()} {d}")
r2 = gonder(olay("INITIAL_PURCHASE", UA, eid=E1, period_type="TRIAL",
                 expiration_at_ms=MS(SIMDI + timedelta(days=99))))
check("W5) Aynı event.id ikinci kez → 'tekrar', tablo değişmedi",
      r2.status_code == 200 and r2.json()["durum"] == "tekrar"
      and durum(HA)["expires_at"].startswith((SIMDI + timedelta(days=7)).date().isoformat()),
      f"{r2.json()} {durum(HA)['expires_at']}")
_db = SessionLocal()
try:
    check("W6) Olay defterinde tek satır",
          _db.query(RevenueCatOlayi).filter(RevenueCatOlayi.id == E1).count() == 1, "")
finally:
    _db.close()
gonder(olay("RENEWAL", UA, expiration_at_ms=MS(SIMDI + timedelta(days=37))))
d = durum(HA)
check("W7) Yenileme → süre uzadı, NORMAL", d["premium"] and d["period_type"] == "NORMAL"
      and d["expires_at"].startswith((SIMDI + timedelta(days=37)).date().isoformat()), str(d))
gonder(olay("CANCELLATION", UA, expiration_at_ms=MS(SIMDI + timedelta(days=37))))
d = durum(HA)
check("W8) İptal → erişim SÜRÜYOR ama will_renew=false", d["premium"] is True
      and d["will_renew"] is False, str(d))
gonder(olay("EXPIRATION", UA, expiration_at_ms=MS(SIMDI - timedelta(minutes=1))))
d = durum(HA)
check("W9) Bitiş → premium değil", d["premium"] is False and d["source"] == "none", str(d))

HB, UB = hesap("paket@gercek.com")
gonder(olay("NON_RENEWING_PURCHASE", UB, urun="tu_egitim_45",
            purchased_at_ms=MS(SIMDI - timedelta(days=1))))
d = durum(HB)
check("W10) 45 günlük paket → premium, bitiş satın alma + 45 gün, yenilenmez",
      d["premium"] and d["will_renew"] is False
      and d["expires_at"].startswith((SIMDI + timedelta(days=44)).date().isoformat()), str(d))

HC, UC = hesap("odeme-sorunu@gercek.com")
gonder(olay("INITIAL_PURCHASE", UC, expiration_at_ms=MS(SIMDI - timedelta(hours=1))))
gonder(olay("BILLING_ISSUE", UC, expiration_at_ms=MS(SIMDI - timedelta(hours=1)),
            grace_period_expiration_at_ms=MS(SIMDI + timedelta(days=6))))
d = durum(HC)
check("W11) Ödeme sorunu + grace süresi → grace bitene kadar premium",
      d["premium"] and d["expires_at"].startswith((SIMDI + timedelta(days=6)).date().isoformat()),
      str(d))
gonder(olay("PRODUCT_CHANGE", UC, new_product_id="tu_yillik"))
_db = SessionLocal()
try:
    _s = _db.query(Subscription).filter(Subscription.user_id == UC).one()
    check("W12) Ürün değişimi sonraki ürün olarak kaydedildi, erişim aynı",
          _s.sonraki_urun == "tu_yillik" and _s.product_id == "tu_aylik", _s.sonraki_urun)
finally:
    _db.close()
r = gonder(olay("INITIAL_PURCHASE", "$RCAnonymousID:abc"))
check("W13) Bilinmeyen kullanıcı → 200 + kullanici_yok (yeniden denetmez)",
      r.status_code == 200 and r.json()["durum"] == "kullanici_yok", r.text[:150])
HD_, UD = hesap("alias@gercek.com")
g = olay("INITIAL_PURCHASE", "$RCAnonymousID:xyz", expiration_at_ms=MS(SIMDI + timedelta(days=30)))
g["event"]["aliases"] = ["$RCAnonymousID:xyz", str(UD)]
gonder(g)
check("W14) Anonim kimlik + takma adda bizim id → eşleşti", durum(HD_)["premium"], "")
HE, UE = hesap("entitlementsiz@gercek.com")
g = olay("INITIAL_PURCHASE", UE, expiration_at_ms=MS(SIMDI + timedelta(days=30)))
g["event"]["entitlement_ids"] = ["baska"]
r = gonder(g)
check("W15) premium entitlement'ı olmayan olay erişim vermez",
      r.json()["durum"] == "atlandi" and durum(HE)["premium"] is False, r.text[:150])

# =============================================================================
# R — refresh
# =============================================================================
HR, UR = hesap("refresh@gercek.com")
premium_svc.musteri_cek = lambda uid: {"subscriber": {
    "subscriptions": {"tu_yillik": {
        "expires_date": (SIMDI + timedelta(days=300)).isoformat().replace("+00:00", "Z"),
        "purchase_date": (SIMDI - timedelta(days=65)).isoformat().replace("+00:00", "Z"),
        "period_type": "normal", "store": "app_store", "is_sandbox": True,
        "unsubscribe_detected_at": None, "billing_issues_detected_at": None}},
    "non_subscriptions": {}}}
r = client.get("/api/v1/subscriptions/refresh", headers=HR)
check("R1) Refresh RevenueCat kaydından premium yaptı", r.status_code == 200
      and r.json()["premium"] and r.json()["product_id"] == "tu_yillik"
      and r.json()["guncellenen_urunler"] == ["tu_yillik"], r.text[:200])


def _hata(uid):
    raise RuntimeError("Mağaza bilgisi alınamadı (HTTP 500)")


premium_svc.musteri_cek = _hata
r = client.get("/api/v1/subscriptions/refresh", headers=HR)
check("R2) RevenueCat erişilemezse 503 + Türkçe", r.status_code == 503
      and "Mağaza" in r.json()["detail"], r.text[:120])

# =============================================================================
# K — Kurucu üye
# =============================================================================
HK, UK = hesap("kurucu@gercek.com")
_db = SessionLocal()
try:
    _db.get(User, UK).created_at = datetime.combine(LANSMAN - timedelta(days=20),
                                                    datetime.min.time(), timezone.utc)
    _db.commit()
finally:
    _db.close()
d = durum(HK)
check("K1) Lansmandan önce kayıt → kurucu_uye + source=kurucu, bitiş lansman+30",
      d["kurucu_uye"] is True and d["premium"] and d["source"] == "kurucu"
      and datetime.fromisoformat(d["expires_at"].replace("Z", "+00:00"))
      == premium_svc.lansman_ani() + timedelta(days=30), str(d))
check("K2) Lansmandan sonra kayıt olan kurucu değil",
      durum(HR)["kurucu_uye"] is False, str(durum(HR)))
_db = SessionLocal()
try:
    _u = _db.get(User, UK)
    _l = premium_svc.lansman_ani()
    check("K3) 31. gün kurucu hakkı bitti (kurucu_uye bayrağı kalır)",
          premium_svc.durum(_db, _u, simdi=_l + timedelta(days=31))["premium"] is False
          and premium_svc.durum(_db, _u, simdi=_l + timedelta(days=31))["kurucu_uye"], "")
    check("K4) Lansmandan önce (beta dönemi) kurucu hakkı henüz başlamadı",
          premium_svc.durum(_db, _u, simdi=_l - timedelta(days=1))["source"] == "none", "")
finally:
    _db.close()

# =============================================================================
# M — Manuel hak
# =============================================================================
HM, UM = hesap("ilayda@gercek.com")
HN, UN = hesap("danisan@gercek.com")
r = client.post("/api/v1/admin/premium", headers=HN,
                json={"email": "danisan@gercek.com", "gun": 30})
check("M1) Admin olmayan → 403", r.status_code == 403, str(r.status_code))
_db = SessionLocal()
try:
    _db.add(CommunityProfile(user_id=UM, nickname="ilaydakani", is_moderator=True,
                             is_expert=True, status="active", post_count=0))
    _db.commit()
finally:
    _db.close()
r = client.post("/api/v1/admin/premium", headers=HM,
                json={"email": "DANISAN@gercek.com", "gun": 30, "aciklama": "birebir danışan"})
d = durum(HN)
check("M2) Admin e-postayla 30 gün verdi → source=manual",
      r.status_code == 200 and d["premium"] and d["source"] == "manual"
      and d["expires_at"].startswith((SIMDI + timedelta(days=30)).date().isoformat()),
      f"{r.status_code} {d}")
r = client.post("/api/v1/admin/premium", headers=HM, json={"user_id": str(UN), "gun": 15})
check("M3) İkinci hak mevcut bitişten UZATIR (30+15)",
      r.status_code == 200 and r.json()["bitis"].startswith(
          (SIMDI + timedelta(days=45)).date().isoformat()), r.text[:150])
r = client.post("/api/v1/admin/premium", headers=HM, json={"email": "yok@x.com", "gun": 5})
check("M4) Bilinmeyen kullanıcı → 404", r.status_code == 404, str(r.status_code))
r = client.post("/api/v1/admin/premium", headers=HM, json={"gun": 5})
check("M5) Kullanıcı belirtilmedi → 422", r.status_code == 422, str(r.status_code))

# =============================================================================
# L — BETA_MODE kapalı + premium yok → kilitler
# =============================================================================
HL, UL = hesap("ucretsiz@gercek.com")
BL = client.post("/api/v1/babies", headers=HL, json={
    "name": "Bebek", "night_wakes": 2, **TAM_PROFIL,
    "birth_date": (date.today() - timedelta(days=245)).isoformat()}).json()["id"]
_db = SessionLocal()
try:
    for slug, kat in (("uyku-egitimine-giris", "baslarken"), ("gun-1-3", "adim_adim")):
        _db.add(EducationVideo(id=uuid.uuid4(), slug=slug, title=slug, category=kat,
                               order_in_category=1, duration_sec=60, chapters=[],
                               video_url=storage.video_url(slug), stage_tags=["genel"]))
    for slug, bedava in (("beyaz-gurultu", True), ("fan", False)):
        _db.add(SleepSound(id=uuid.uuid4(), slug=slug, title=slug, category="gurultu",
                           order_in_category=1, duration_sec=600, bytes=1,
                           audio_url=storage.uyku_sesi_url(slug), is_free=bedava))
    _db.commit()
finally:
    _db.close()


def kilit_durumu(h, bid):
    out = {}
    g = client.post("/api/v1/plans/generate?sync=true", headers=h, json={"baby_id": bid})
    p = client.get(f"/api/v1/plans/today?baby_id={bid}", headers=h).json()
    out["plan_kilit"] = (p.get("premium_required"), p.get("locked"),
                         len(p["content"].get("days") or []),
                         len(p["content"].get("schedule") or []), g.status_code)
    v = client.get("/api/v1/education/videos", headers=h).json()
    out["video"] = {x["slug"]: x["locked"] for c in v["categories"] for x in c["videos"]}
    s = client.get("/api/v1/sounds", headers=h).json()
    out["ses"] = {x["slug"]: x["locked"] for c in s["categories"] for x in c["sounds"]}
    st = client.get("/api/v1/voice/stories", headers=h).json()
    out["hikaye"] = {x["id"]: x["locked"] for x in st["masallar"] + st["ninniler"]}
    rc = client.post("/api/v1/voice/clone", headers=h,
                     files={"audio": ("s.mp3", io.BytesIO(b"A" * 900), "audio/mpeg")},
                     data={"name": "Ses"})
    out["klon"] = (rc.status_code, rc.json().get("premium_required"))
    lg = client.post("/api/v1/logs/batch", headers=h, json={"logs": [{
        "baby_id": bid, "type": "nap", "client_id": "k-" + str(uuid.uuid4())[:8],
        "started_at": (SIMDI - timedelta(hours=3)).isoformat(),
        "ended_at": (SIMDI - timedelta(hours=2)).isoformat()}]})
    out["kayit"] = lg.status_code
    return out


chatbot._cevap_uret = lambda *a, **k: {
    "cevap": "Cevap", "cache_hit": False, "kaynaklar": [], "llm": False,
    "retrieval_layer": "k1", "top_score": 0.9}
kl = kilit_durumu(HL, BL)
check("L1) Eğitim programı kilitli: premium_required + days çıkarıldı, çizelge kaldı",
      kl["plan_kilit"][0] is True and kl["plan_kilit"][1] == ["egitim_programi"]
      and kl["plan_kilit"][2] == 0 and kl["plan_kilit"][3] > 0, str(kl["plan_kilit"]))
check("L2) Videolar: Başlarken açık, diğerleri kilitli",
      kl["video"] == {"uyku-egitimine-giris": False, "gun-1-3": True}, str(kl["video"]))
check("L3) Sesler: is_free açık, diğerleri kilitli",
      kl["ses"] == {"beyaz-gurultu": False, "fan": True}, str(kl["ses"]))
check("L4) Anne Sesi: yalnız 1 ninni açık",
      [k for k, v in kl["hikaye"].items() if not v] == ["ninni_dandini"], str(kl["hikaye"]))
check("L5) Ses klonlama 403 + premium_required", kl["klon"] == (403, True), str(kl["klon"]))
check("L6) Kayıt girme ücretsiz (200)", kl["kayit"] == 200, str(kl["kayit"]))
_sor = [client.post("/api/v1/chat", headers=HL, json={"message": f"soru {i}"})
        for i in range(4)]
check("L7) Sor: 3 soru ücretsiz, kalan hak 2→1→0",
      [x.status_code for x in _sor[:3]] == [200, 200, 200]
      and [x.json().get("ucretsiz_kalan") for x in _sor[:3]] == [2, 1, 0],
      str([(x.status_code, x.json().get("ucretsiz_kalan")) for x in _sor]))
check("L8) 4. soru 403 + premium_required + kural",
      _sor[3].status_code == 403 and _sor[3].json().get("premium_required") is True
      and _sor[3].json().get("kural") == "sor_gunluk_limit", _sor[3].text[:200])
kp = kilit_durumu(HN, client.post("/api/v1/babies", headers=HN, json={
    "name": "B", "night_wakes": 2, **TAM_PROFIL,
    "birth_date": (date.today() - timedelta(days=245)).isoformat()}).json()["id"])
check("L9) Manuel premium'da hiçbir şey kilitli değil",
      kp["plan_kilit"][0] is False and not any(kp["video"].values())
      and not any(kp["ses"].values()) and not any(kp["hikaye"].values()), str(kp))

# =============================================================================
# B — BETA_MODE her şeyi açar
# =============================================================================
os.environ["BETA_MODE"] = "true"
get_settings.cache_clear()
kb = kilit_durumu(HL, BL)
_sb = client.post("/api/v1/chat", headers=HL, json={"message": "beşinci soru"})
check("B1) BETA_MODE: premium, source=beta", (durum(HL)["premium"], durum(HL)["source"])
      == (True, "beta"), str(durum(HL)))
check("B2) BETA_MODE: plan/video/ses/hikâye kilidi yok, klon 403 değil",
      kb["plan_kilit"][0] is False and kb["plan_kilit"][2] > 0
      and not any(kb["video"].values()) and not any(kb["ses"].values())
      and not any(kb["hikaye"].values()) and kb["klon"][0] != 403, str(kb))
check("B3) BETA_MODE: Sor sınırı yok (ucretsiz_kalan None)", _sb.status_code == 200
      and _sb.json().get("ucretsiz_kalan") is None, _sb.text[:150])
os.environ.pop("BETA_MODE", None)
get_settings.cache_clear()

# =============================================================================
print("=" * 78)
print("ÖDEME ALTYAPISI TEST SONUÇLARI")
print("=" * 78)
for ad, ok, detay in results:
    if not ok:
        print(f"  ✗ {ad}  —  {detay}")
gecen = sum(1 for _, ok, _ in results if ok)
print("-" * 78)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
