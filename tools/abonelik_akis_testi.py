"""
Abonelik uçtan uca akış testi — CANLI veritabanında, GERİ ALINAN tek işlem içinde.

Geçici test kullanıcıları (@example.com) oluşturur, gerçek uçları (TestClient)
çağırır, RevenueCat webhook olaylarını gerçek uçtan gönderir; sonunda işlemi
ROLLBACK eder. Kalıcı hiçbir şey yazılmaz:
  - SessionLocal tek bağlantıya bağlanır (join_transaction_mode=create_savepoint):
    uçların commit'leri yalnız savepoint'i bırakır, dış işlem sonda geri alınır.
  - LLM yok (plan yedek motordan, Sor cevabı sahte), maliyet kaydı yok.
  - RevenueCat silme çağrısı SAHTE (dışarı istek çıkmaz), ElevenLabs'e gidilmez.
  - Webhook sırrı / beta ayarları yalnız bu süreçte geçersiz kılınır (env değişmez).

Senaryolar: ücretsiz → TRIAL satın alma → RENEWAL (NORMAL) → CANCELLATION →
UNCANCELLATION → BILLING_ISSUE (ek süre) → RENEWAL → PRODUCT_CHANGE → EXPIRATION;
tekrar olay + yanlış yetki; APP_STORE ve PLAY_STORE; BETA_MODE kapalı/açık
(açıkta BETA_HARIC_EMAILS gerçek kademeyi, diğer hesap premium görür); demo hesap
(salt okunur); aktif aboneli hesap silme.

Çalıştırma (konteynerde; deploy gerekmez, betik stdin'den gider):
    railway ssh "cd /app && export $(cat /proc/1/environ | tr '\\0' '\\n' | grep ^LD_LIBRARY_PATH=) \
        && PYTHONIOENCODING=utf-8 /opt/venv/bin/python - --beta ikisi" < tools/abonelik_akis_testi.py
Yerelde: python tools/abonelik_akis_testi.py   (tavsan_local.db'ye, yine rollback)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

_dosya = Path(globals().get("__file__") or "<stdin>")
# stdin'den çalıştırılınca __file__ "<stdin>" olur → çalışma dizini (/app) kök sayılır.
ROOT = _dosya.resolve().parent.parent if _dosya.is_file() else Path.cwd()
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SIR = "abonelik-akis-testi-gecici-sir"
URUN_IOS = "tavsan_premium_aylik"
URUN_PLAY = "tavsan_premium_aylik:aylik"           # RevenueCat'in Google biçimi (abonelik:temel plan)
ETIKET = uuid.uuid4().hex[:8]
# Gerçek istemci gibi: gece kartı (yontem_kart.GECE_KARTI_MIN_BUILD) ve ses rızası
# kapısı build 28+ ister. Sürümsüz kullanıcıda gece kartı HİÇ gelmez (canlıda ölçüldü).
SURUM = "1.0.0+30"

SONUC: list[tuple[str, str, str, str, bool]] = []  # (bağlam, senaryo, beklenen, gerçek, ok)


def kaydet(baglam, senaryo, beklenen, gercek, ok):
    SONUC.append((baglam, senaryo, str(beklenen), str(gercek), bool(ok)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Abonelik uçtan uca akış testi (rollback)")
    ap.add_argument("--beta", choices=("ikisi", "kapali", "acik"), default="ikisi")
    args = ap.parse_args(argv)

    os.environ.pop("SENTRY_DSN", None)
    logging.disable(logging.WARNING - 1)            # INFO altı sus; RevenueCat logu yakalanır

    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from api.config import (DEMO_EPOSTALARI, SOR_GUNLUK_DENEME, get_settings)
    from api.db import SessionLocal, engine
    from api.main import app
    from api.models import (Baby, ChatMessage, RevenueCatOlayi, SleepLog, SleepPlan,
                            Subscription, User, VoiceProfile)
    from api.services import notifier, usage
    from api.services import premium as premium_svc
    from api.services import voice_paket
    from api.services.security import create_access_token, hash_password
    from api.zaman import TR, bugun_tr, saat_sabitle
    from engine import chatbot, plan_generator

    # --- dış dünyayı kapat ------------------------------------------------------
    os.environ["ANTHROPIC_API_KEY"] = ""             # api.main load_dotenv'den SONRA
    plan_generator.HAS_ANTHROPIC = False

    class _Patlayan:
        def __init__(self, *a, **k):
            raise RuntimeError("akış testi canlı Anthropic istemcisi kurmaya çalıştı")
    plan_generator.Anthropic = _Patlayan
    usage.kaydet = lambda *a, **k: None
    chatbot._cevap_uret = lambda *a, **k: {"cevap": "Test cevabı", "cache_hit": False,
                                           "kaynaklar": [], "llm": False,
                                           "retrieval_layer": "k1", "top_score": 0.9}
    import requests
    rc_cagrilari: list[str] = []

    class _Yanit:
        status_code = 200
    requests.delete = lambda url, **k: rc_cagrilari.append(url) or _Yanit()
    requests.get = requests.post = requests.put = requests.patch = (
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"dış istek engellendi: {a[:1]}")))
    rc_loglari: list[str] = []

    class _Yakala(logging.Handler):
        def emit(self, kayit):
            rc_loglari.append(kayit.getMessage())
    _rc_log = logging.getLogger("tavsan.revenuecat")
    _rc_log.addHandler(_Yakala())
    _rc_log.setLevel(logging.INFO)
    logging.disable(logging.NOTSET)
    for ad in ("tavsan", "uvicorn", "httpx", "sqlalchemy"):
        logging.getLogger(ad).setLevel(logging.WARNING)
    _rc_log.setLevel(logging.INFO)

    ayar = get_settings()
    ayar.revenuecat_webhook_secret = SIR
    ayar.revenuecat_api_key = "sk_akis_testi_sahte"   # requests.delete sahte
    gercek_beta = (ayar.beta_mode, set(ayar.beta_haric_emails))

    # --- tek bağlantı, dış işlem -------------------------------------------------
    conn = engine.connect()
    dis = conn.begin()
    if engine.dialect.name == "sqlite":
        # pysqlite BEGIN'i ilk DML'e kadar ertelemiyor değil, HİÇ göndermiyor: ilk
        # SAVEPOINT en dış işlem olur ve RELEASE'i commit eder (yerelde 1 satır
        # sızdı). Postgres/psycopg2 BEGIN'i kendisi gönderir.
        conn.exec_driver_sql("BEGIN")
    SessionLocal.configure(bind=conn, join_transaction_mode="create_savepoint")
    c = TestClient(app, raise_server_exceptions=False)
    W = "/api/v1/webhooks/revenuecat"

    def oturum():
        return SessionLocal()

    def eposta(ad):
        return f"akis-{ETIKET}-{'b1' if ayar.beta_mode else 'b0'}-{ad}@example.com"

    def kullanici(ad):
        s = oturum()
        try:
            u = User(email=eposta(ad), app_version=SURUM,
                     password_hash=hash_password("Akis-Test-123!"))
            s.add(u)
            s.commit()
            uid = u.id
        finally:
            s.close()
        h = {"Authorization": f"Bearer {create_access_token(str(uid))}", "X-App-Version": SURUM}
        for tur in ("aydinlatma", "acik_riza_saglik", "acik_riza_ses"):
            c.post("/api/v1/consents", headers=h, json={"tur": tur, "onay": True})
        return h, uid

    def bebek_ve_plan(h):
        r = c.post("/api/v1/babies", headers=h, json={
            "name": "Akis", "night_wakes": 2, "feeding_type": "karma",
            "sleep_method": "yatağında", "sleep_environment": "kendi odası",
            "crying_tolerance": "orta", "parent_experience": "ilk bebek",
            "birth_date": (bugun_tr() - timedelta(days=245)).isoformat()})
        bid = r.json()["id"]
        rp = c.post("/api/v1/plans/generate?sync=true", headers=h, json={"baby_id": bid})
        s = oturum()
        try:
            s.get(Baby, uuid.UUID(bid)).training_started_at = bugun_tr()     # eğitimin 1. günü
            s.commit()
        finally:
            s.close()
        dun = bugun_tr() - timedelta(days=1)
        an = lambda g, hh, mm: datetime(g.year, g.month, g.day, hh, mm, tzinfo=TR).isoformat()  # noqa: E731
        c.post("/api/v1/logs/batch", headers=h, json={"logs": [
            {"baby_id": bid, "type": "nap", "started_at": an(dun, 10, 0),
             "ended_at": an(dun, 11, 0), "client_id": f"akis-{ETIKET}-{bid[:6]}-1"},
            {"baby_id": bid, "type": "sleep", "started_at": an(dun, 19, 45),
             "ended_at": an(bugun_tr(), 6, 30), "client_id": f"akis-{ETIKET}-{bid[:6]}-2"}]})
        c.post("/api/v1/chat", headers=h, json={"message": "kurulum sorusu"})
        return bid, rp.status_code

    def olay(uid, tur, store, urun, period="NORMAL", bitis=None, **ek):
        e = {"id": str(uuid.uuid4()), "type": tur, "app_user_id": str(uid),
             "product_id": urun, "entitlement_ids": ["premium"], "store": store,
             "environment": "SANDBOX", "period_type": period,
             "purchased_at_ms": int(datetime.now(timezone.utc).timestamp() * 1000)}
        if bitis is not None:
            e["expiration_at_ms"] = int(bitis.timestamp() * 1000)
        e.update(ek)
        return {"api_version": "1.0", "event": e}

    def gonder(govde):
        r = c.post(W, json=govde, headers={"Authorization": f"Bearer {SIR}"})
        return r.status_code, (r.json() if r.status_code == 200 else r.text)

    def gozle(h, uid, bid) -> dict:
        """Kullanıcının o anki görünümü — tüm kapılar gerçek uçlardan."""
        g = {}
        d = c.get("/api/v1/abonelik/durum", headers=h).json()
        g["durum"], g["deneme_gunu"], g["sor_kalan"] = d.get("durum"), d.get("deneme_gunu"), d.get("sor_kalan")
        st = c.get("/api/v1/subscriptions/status", headers=h).json()
        g["will_renew"] = st.get("will_renew")
        p = c.get(f"/api/v1/plans/today?baby_id={bid}", headers=h).json()
        ct = p.get("content") or {}
        g["gunler"] = [(x.get("start"), x.get("end")) for x in (ct.get("days") or [])]
        g["kilitli_gunler"] = ct.get("kilitli_gunler")
        g["gece_karti"] = "night_wake_protocol" in ct
        g["plan_var"] = bool(ct.get("schedule"))
        v = c.get(f"/api/v1/education/videos?baby_id={bid}", headers=h).json()
        vids = []

        def _tara(x):
            if isinstance(x, dict):
                if "locked" in x and "video_url" in x:
                    vids.append(x)
                for y in x.values():
                    _tara(y)
            elif isinstance(x, list):
                for y in x:
                    _tara(y)
        _tara(v)
        tekil = {x.get("id") or x.get("slug"): x for x in vids}.values()
        acik = [x for x in tekil if not x["locked"]]
        kilitli = [x for x in tekil if x["locked"]]
        g["video_acik"], g["video_kilitli"] = len(acik), len(kilitli)
        g["video_kilitli_adressiz"] = all(x.get("video_url") is None for x in kilitli)
        g["video_acik_imzali"] = all("sig=" in (x.get("video_url") or "") for x in acik)
        vs = c.get("/api/v1/voice/voice-status", headers=h).json()
        g["ses_kayit_acik"] = vs.get("can_clone")
        g["paket_kaydi_hakki"] = vs.get("paket_kaydi_hakki")
        # Kapıyı geçen kayıt boş dosyada 400'e düşer — ElevenLabs'e gidilmez.
        bos = {"audio": ("bos.m4a", b"", "audio/m4a")}
        g["klon_onizleme_http"] = c.post("/api/v1/voice/clone", headers=h, files=bos,
                                         data={"amac": voice_paket.AMAC_ONIZLEME}).status_code
        g["klon_paket_http"] = c.post("/api/v1/voice/clone", headers=h, files=bos,
                                      data={"amac": voice_paket.AMAC_PAKET}).status_code
        r = c.post("/api/v1/chat", headers=h, json={"message": "gözlem sorusu"})
        g["sor_http"] = r.status_code
        g["sor_hata"] = r.json().get("hata") if r.status_code != 200 else None
        s = oturum()
        try:
            g["bildirim_kilitli"] = notifier._egitim_bildirimi_kilitli(s, s.get(User, uid), {})
        finally:
            s.close()
        return g

    def bekle(kademe, deneme_gunu=1):
        if kademe == "ucretsiz":
            return {"durum": "ucretsiz", "gunler": [], "kilitli_gunler": list(range(1, 14)),
                    "gece_karti": False, "video_acik": 3, "video_kilitli_adressiz": True,
                    "ses_kayit_acik": True, "klon_onizleme_http": 400, "klon_paket_http": 403,
                    "sor_http": 403, "sor_hata": "PREMIUM_GEREKLI", "bildirim_kilitli": True}
        if kademe == "deneme":
            return {"durum": "deneme", "deneme_gunu": deneme_gunu, "gunler": [(1, 3)],
                    "kilitli_gunler": list(range(4, 14)), "gece_karti": True,
                    "video_acik": 3, "video_kilitli_adressiz": True,
                    "klon_onizleme_http": 400, "klon_paket_http": 403,
                    "sor_http": 200, "bildirim_kilitli": False}
        return {"durum": "premium", "sor_kalan": None, "kilitli_gunler": None,
                "gece_karti": True, "video_kilitli": 0, "video_acik_imzali": True,
                "klon_paket_http": 400, "sor_http": 200, "bildirim_kilitli": False}

    def karsilastir(baglam, senaryo, g, b, ek=None):
        fark = {k: (v, g.get(k)) for k, v in b.items() if g.get(k) != v}
        if "gunler" not in b and b.get("durum") == "premium":
            son = max((e for _, e in g["gunler"]), default=0)
            if son != 13:
                fark["13 gün açık"] = (13, son)
        for k, (beklenen, gercek) in (ek or {}).items():
            if beklenen != gercek:
                fark[k] = (beklenen, gercek)
        ozet = ", ".join(f"{k}={v}" for k, v in b.items()) + (
            "" if not ek else "; " + ", ".join(f"{k}={v[0]}" for k, v in ek.items()))
        gercek = "aynı" if not fark else "; ".join(f"{k}: {v[1]}" for k, v in fark.items())
        kaydet(baglam, senaryo, ozet, gercek, not fark)

    def abonelik_satiri(uid, urun):
        s = oturum()
        try:
            x = (s.query(Subscription).filter(Subscription.user_id == uid,
                                              Subscription.product_id
                                              == premium_svc.urun_kimligi(urun)).one_or_none())
            return None if x is None else {"status": x.status, "will_renew": x.will_renew,
                                           "store": x.store, "platform": x.platform,
                                           "sonraki_urun": x.sonraki_urun,
                                           "expires_at": x.expires_at}
        finally:
            s.close()

    def yasam_dongusu(baglam, store, urun, golge: bool):
        """Bir kullanıcının ücretsizden bitişe tüm akışı. golge=True → BETA açık ve
        hesap BETA_HARIC_EMAILS'te DEĞİL: her adımda premium beklenir."""
        h, uid = kullanici(f"{store.lower()}-{'golge' if golge else 'gercek'}")
        if not golge and ayar.beta_mode:
            ayar.beta_haric_emails = set(gercek_beta[1]) | {eposta(f"{store.lower()}-gercek")}
        bid, plan_http = bebek_ve_plan(h)
        if plan_http not in (200, 201):
            kaydet(baglam, "Kurulum: plan üretimi", "201", plan_http, False)
        B = (lambda k, **kw: bekle("premium")) if golge else bekle
        simdi = datetime.now(timezone.utc)

        karsilastir(baglam, "1 Ücretsiz", gozle(h, uid, bid), B("ucretsiz"))

        ilk = olay(uid, "INITIAL_PURCHASE", store, urun, "TRIAL", simdi + timedelta(days=3))
        kod, yan = gonder(ilk)
        g = gozle(h, uid, bid)
        karsilastir(baglam, "2 INITIAL_PURCHASE TRIAL", g, B("deneme"),
                    {"webhook": (200, kod), "webhook_durum": ("islendi", yan.get("durum") if isinstance(yan, dict) else yan)})
        satir = abonelik_satiri(uid, urun) or {}
        beklenen_platform = "android" if store == "PLAY_STORE" else "ios"
        kaydet(baglam, "2b Mağaza satırı", f"store={store}, platform={beklenen_platform}",
               f"store={satir.get('store')}, platform={satir.get('platform')}",
               satir.get("store") == store and satir.get("platform") == beklenen_platform)

        # Sor günlük limit: bugün kalan hakkı tüket, sonra 429; TR gece yarısında sıfırlanır.
        kodlar = [c.post("/api/v1/chat", headers=h, json={"message": f"soru {i}"}).status_code
                  for i in range(SOR_GUNLUK_DENEME + 1)]
        son = c.post("/api/v1/chat", headers=h, json={"message": "limit sonrası"})
        yarin = datetime.combine(bugun_tr() + timedelta(days=1), time(0, 1), tzinfo=TR)
        with saat_sabitle(yarin):
            ertesi = c.post("/api/v1/chat", headers=h, json={"message": "gece yarısından sonra"})
        if golge:
            kaydet(baglam, "2c Sor sınırsız (beta)", f"{SOR_GUNLUK_DENEME + 2} soru da 200",
                   f"{sorted(set(kodlar + [son.status_code]))}",
                   set(kodlar + [son.status_code]) == {200})
        else:
            # Kurulum + gözlem soruları ücretsizde 403 (sayılmaz); denemede gözlem 1 hak yedi.
            ilk_429 = next((i for i, k in enumerate(kodlar) if k == 429), None)
            kaydet(baglam, "2c Sor 10. sorudan sonra 429 GUNLUK_LIMIT",
                   "10 soru 200 (gözlem dahil), sonra 429 GUNLUK_LIMIT",
                   f"200 sayısı={kodlar.count(200) + 1}, sonraki={son.status_code} "
                   f"{son.json().get('hata')}",
                   kodlar.count(200) + 1 == SOR_GUNLUK_DENEME and ilk_429 is not None
                   and son.status_code == 429 and son.json().get("hata") == "GUNLUK_LIMIT")
            kaydet(baglam, "2d Sor TR gece yarısında sıfırlanır", "00:01 TR → 200",
                   ertesi.status_code, ertesi.status_code == 200)

        kod, yan = gonder(ilk)
        kaydet(baglam, "2e Aynı olay ikinci kez", "200, durum=tekrar", f"{kod}, durum={yan.get('durum') if isinstance(yan, dict) else yan}",
               kod == 200 and isinstance(yan, dict) and yan.get("durum") == "tekrar")
        r = c.post(W, json=olay(uid, "EXPIRATION", store, urun, bitis=simdi),
                   headers={"Authorization": "Bearer yanlis-sir"})
        kaydet(baglam, "2f Yanlış Authorization", "401, kademe değişmez",
               f"{r.status_code}, {c.get('/api/v1/abonelik/durum', headers=h).json().get('durum')}",
               r.status_code == 401 and c.get("/api/v1/abonelik/durum", headers=h).json().get("durum")
               == ("premium" if golge else "deneme"))

        gonder(olay(uid, "RENEWAL", store, urun, "NORMAL", simdi + timedelta(days=33)))
        s = oturum()
        try:
            s.add(VoiceProfile(user_id=uid, status="ready", amac=voice_paket.AMAC_ONIZLEME,
                               last_cloned_at=simdi, progress_done=1, progress_total=1))
            s.commit()
        finally:
            s.close()
        karsilastir(baglam, "3 RENEWAL NORMAL", gozle(h, uid, bid), B("premium"),
                    {"paket_kaydi_hakki": (True, gozle(h, uid, bid)["paket_kaydi_hakki"])})
        sor = [c.post("/api/v1/chat", headers=h, json={"message": f"p{i}"}) for i in range(12)]
        kaydet(baglam, "3b Premium Sor sınırsız", "12 soru 200, ucretsiz_kalan=null",
               f"{sorted({x.status_code for x in sor})}, kalan={sor[-1].json().get('ucretsiz_kalan')}",
               {x.status_code for x in sor} == {200} and sor[-1].json().get("ucretsiz_kalan") is None)

        gonder(olay(uid, "CANCELLATION", store, urun, "NORMAL", simdi + timedelta(days=33),
                    cancel_reason="UNSUBSCRIBE"))
        g = gozle(h, uid, bid)
        karsilastir(baglam, "4 CANCELLATION (dönem sonuna kadar)", g, B("premium"),
                    {"will_renew": (False, g["will_renew"])})
        gonder(olay(uid, "UNCANCELLATION", store, urun, "NORMAL", simdi + timedelta(days=33)))
        g = gozle(h, uid, bid)
        karsilastir(baglam, "5 UNCANCELLATION", g, B("premium"), {"will_renew": (True, g["will_renew"])})
        gonder(olay(uid, "BILLING_ISSUE", store, urun, "NORMAL", simdi - timedelta(minutes=1),
                    grace_period_expiration_at_ms=int((simdi + timedelta(days=6)).timestamp() * 1000)))
        karsilastir(baglam, "6 BILLING_ISSUE (ek süre 6 gün)", gozle(h, uid, bid), B("premium"),
                    {"satır durumu": ("billing_issue", (abonelik_satiri(uid, urun) or {}).get("status"))})
        gonder(olay(uid, "RENEWAL", store, urun, "NORMAL", simdi + timedelta(days=30)))
        yeni_urun = urun.replace("aylik", "yillik")
        gonder(olay(uid, "PRODUCT_CHANGE", store, urun, "NORMAL", simdi + timedelta(days=30),
                    new_product_id=yeni_urun))
        karsilastir(baglam, "7 PRODUCT_CHANGE", gozle(h, uid, bid), B("premium"),
                    {"sonraki_urun": (premium_svc.urun_kimligi(yeni_urun), (abonelik_satiri(uid, urun) or {}).get("sonraki_urun"))})

        s = oturum()
        try:
            once = (s.query(SleepLog).filter(SleepLog.baby_id == uuid.UUID(bid)).count(),
                    s.query(ChatMessage).filter(ChatMessage.user_id == uid).count(),
                    s.query(SleepPlan).filter(SleepPlan.baby_id == uuid.UUID(bid)).count())
        finally:
            s.close()
        gonder(olay(uid, "EXPIRATION", store, urun, "NORMAL", simdi - timedelta(minutes=1),
                    expiration_reason="UNSUBSCRIBE"))
        g = gozle(h, uid, bid)
        s = oturum()
        try:
            sonra = (s.query(SleepLog).filter(SleepLog.baby_id == uuid.UUID(bid)).count(),
                     s.query(ChatMessage).filter(ChatMessage.user_id == uid).count(),
                     s.query(SleepPlan).filter(SleepPlan.baby_id == uuid.UUID(bid)).count())
        finally:
            s.close()
        exp = B("ucretsiz")
        exp.pop("ses_kayit_acik", None)                # önizleme kaydı yapılmış → 30 gün bekleme
        exp.pop("klon_onizleme_http", None)
        karsilastir(baglam, "8 EXPIRATION (ücretsize dönüş)", g, exp,
                    {"kayıt/sohbet/plan korunur (sohbet +1 gözlem)":
                     (True, sonra[0] == once[0] and sonra[1] >= once[1] and sonra[2] >= 1 and g["plan_var"])})
        if not golge and ayar.beta_mode:
            ayar.beta_haric_emails = set(gercek_beta[1])

    def hesap_silme(baglam):
        h, uid = kullanici("silme")
        bid, _ = bebek_ve_plan(h)
        simdi = datetime.now(timezone.utc)
        gonder(olay(uid, "INITIAL_PURCHASE", "APP_STORE", URUN_IOS, "NORMAL", simdi + timedelta(days=30)))
        rc_cagrilari.clear()
        rc_loglari.clear()
        r = c.delete("/api/v1/auth/account", headers=h)
        s = oturum()
        try:
            kalan = (s.get(User, uid) is not None,
                     s.query(Subscription).filter(Subscription.user_id == uid).count(),
                     s.query(Baby).filter(Baby.id == uuid.UUID(bid)).count(),
                     s.query(RevenueCatOlayi).filter(RevenueCatOlayi.user_id == uid).count())
        finally:
            s.close()
        kaydet(baglam, "9 Aktif aboneli hesap silme", "200; kullanıcı/abonelik/bebek/olay 0",
               f"{r.status_code}; kullanıcı={kalan[0]}, abonelik={kalan[1]}, bebek={kalan[2]}, olay={kalan[3]}",
               r.status_code == 200 and kalan == (False, 0, 0, 0))
        log_ok = any("RevenueCat müşteri kaydı silindi" in m and str(uid) in m for m in rc_loglari)
        kaydet(baglam, "9b RevenueCat silme çağrısı + log", f"DELETE …/subscribers/{{uid}} + INFO log",
               f"çağrı={len(rc_cagrilari)} ({'uid doğru' if rc_cagrilari and str(uid) in rc_cagrilari[0] else '-'}), "
               f"log={'var' if log_ok else 'YOK'}",
               len(rc_cagrilari) == 1 and str(uid) in rc_cagrilari[0] and log_ok)

    try:
        modlar = {"ikisi": (False, True), "kapali": (False,), "acik": (True,)}[args.beta]
        for beta in modlar:
            ayar.beta_mode = beta
            ayar.beta_premium_all = beta
            ad = f"BETA={'açık' if beta else 'kapalı'}"
            for store, urun in (("APP_STORE", URUN_IOS), ("PLAY_STORE", URUN_PLAY)):
                yasam_dongusu(f"{ad} {store}" + (" (haric)" if beta else ""), store, urun, golge=False)
                if beta:
                    yasam_dongusu(f"{ad} {store} (beta kullanıcısı)", store, urun, golge=True)
            hesap_silme(ad)

        # Demo hesap — salt okunur (yalnız durum() hesaplanır, uç çağrılmaz).
        ayar.beta_mode = ayar.beta_premium_all = False
        s = oturum()
        try:
            demo = s.query(User).filter(User.email == DEMO_EPOSTALARI[0]).one_or_none()
            d = premium_svc.durum(s, demo) if demo is not None else {}
        finally:
            s.close()
        kaydet("Demo hesap", "BETA kapalıyken appreview@", "premium (source=manual)",
               f"{d.get('kademe')} (source={d.get('source')}, bitiş={str(d.get('expires_at'))[:10]})"
               if demo else "hesap YOK", d.get("kademe") == "premium" and d.get("source") == "manual")
        kaydet("Ortam", "Canlı BETA_MODE / BETA_HARIC_EMAILS", "bilgi",
               f"BETA_MODE={gercek_beta[0]}, BETA_HARIC_EMAILS={len(gercek_beta[1])} hesap", True)
    finally:
        dis.rollback()
        conn.close()
        SessionLocal.configure(bind=engine, join_transaction_mode="conservative_savepoint")
        with engine.connect() as k:
            artik = k.execute(text("SELECT count(*) FROM users WHERE email LIKE :e"),
                              {"e": f"akis-{ETIKET}-%"}).scalar()
        print(f"ROLLBACK yapıldı — kalan test kullanıcısı: {artik} (0 olmalı)")

    print()
    print("| Bağlam | Senaryo | Beklenen | Gerçek | |")
    print("|---|---|---|---|---|")
    for b, s_, bek, ger, ok in SONUC:
        print(f"| {b} | {s_} | {bek} | {ger} | {'✓' if ok else '✗'} |")
    gecen = sum(1 for x in SONUC if x[4])
    print(f"\nTOPLAM: {gecen}/{len(SONUC)} geçti")
    return 0 if gecen == len(SONUC) else 1


if __name__ == "__main__":
    raise SystemExit(main())
