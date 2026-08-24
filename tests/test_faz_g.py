"""
Faz G düzeltmeleri testleri (G1-G6) — FastAPI TestClient, sqlite temp DB.
LLM/TTS MOCK'lanır → deterministik, ücretsiz, ağ YOK.

Çalıştırma: python tests/test_faz_g.py   (tavsan_demo/ kökünden)

Kapsam:
  G1 plans/generate async: 202+job_id, polling→done+plan, sahiplik (başka user 404),
     bilinmeyen job 404, sync=true → 201+PlanResp.
  G2 rate limit: 5 hatalı login → hesap kilidi (429+Retry-After); IP 10/dk → 429;
     başarılı login sayacı sıfırlar.
  G3 voice/generate: sahip olunmayan voiceId → 403; sahip olunan → 200 (mock TTS).
  G4 docs: dev'de /docs açık; production'da kapalı (ayrı subprocess).
  G5 subscriptions: mock makbuz ('dev_') → 400; gerçek makbuz → active;
     /status premium kararı (none / subscription / beta).
  G6 chat history: trim_history saf fonksiyon + 20 mesajlık geçmişle 200.
"""
import os
import sys
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

try:                                     # Windows konsolu (cp1254) Unicode ok'ları yazamıyor
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# --- Test ortamı: app import'undan ÖNCE env sabitle -------------------------
_DB = Path(tempfile.gettempdir()) / "faz_g_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"           # docs açık; scheduler kapalı
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")
os.environ.pop("ELEVENLABS_API_KEY", None)          # G3'te mock'la yöneteceğiz
os.environ.pop("BETA_PREMIUM_ALL", None)

from fastapi.testclient import TestClient            # noqa: E402
from sqlalchemy import text                          # noqa: E402

from api.db import Base, engine                      # noqa: E402
import api.models                                    # noqa: E402,F401  (tabloları kaydet)
from api.main import app                             # noqa: E402
from api import tts                                  # noqa: E402
from api.routers import chat as chat_router          # noqa: E402
from api.schemas.chat import ChatMessageItem         # noqa: E402
from api.services import plan_service, rate_limit    # noqa: E402
from engine import chatbot                           # noqa: E402

# Şemayı kur (alembic yerine test için create_all)
Base.metadata.create_all(bind=engine)

client = TestClient(app)
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


def _register(email: str, pw: str = "TestPass123!") -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": pw})
    assert r.status_code == 201, f"register {email}: {r.status_code} {r.text}"
    return r.json()["access_token"]


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


# ===========================================================================
# G1 — plans/generate async
# ===========================================================================
def _fake_content(baby, overrides, dogum_haftasi):
    """generate_content mock'u — LLM'siz, hızlı, geçerli içerik."""
    return {
        "markdown": "# Test Plan\nKısa plan.", "headline": "Test başlık",
        "bucket": "6_ay", "yas": {"ay": 6}, "plan_secimi": {"tip": "5_gun_standart"},
        "uygun_mu": True, "uyarilar": [], "generated_with": "fallback",
        "schedule": [{"key": "nap1", "type": "nap", "time": "10:00", "start_minute": 600}],
        "dogum_haftasi": dogum_haftasi or 40, "baseline_night_wakes": baby.night_wakes,
        "adapted": False, "night_wake_protocol": {},
    }


def test_g1():
    plan_service.generate_content = _fake_content          # MOCK: LLM yok

    tok = _register("g1_owner@example.com")
    bid = client.post("/api/v1/babies", headers=_auth(tok),
                      json={"name": "G1", "birth_date": "2025-02-01",
                            "night_wakes": 1}).json()["id"]

    # Async (default): 202 + job_id + processing
    r = client.post("/api/v1/plans/generate", headers=_auth(tok), json={"baby_id": bid})
    check("G1.1 async POST → 202",
          r.status_code == 202, f"status={r.status_code} body={r.text[:120]}")
    body = r.json()
    check("G1.2 yanıt job_id + status=processing",
          bool(body.get("job_id")) and body.get("status") == "processing", str(body))
    job_id = body.get("job_id")

    # Polling → done + plan (background mock hızlı bitti)
    done = None
    for _ in range(30):
        s = client.get(f"/api/v1/plans/generate/{job_id}", headers=_auth(tok))
        if s.status_code == 200 and s.json().get("status") in ("done", "failed"):
            done = s.json()
            break
    check("G1.3 polling → done", done is not None and done.get("status") == "done",
          str(done)[:160])
    check("G1.4 done yanıtı PlanResp içeriyor",
          bool(done and done.get("plan") and done["plan"].get("id")),
          str(done.get("plan") if done else None)[:120])

    # Sahiplik: başka kullanıcı aynı job_id'yi göremez → 404
    tok2 = _register("g1_other@example.com")
    s2 = client.get(f"/api/v1/plans/generate/{job_id}", headers=_auth(tok2))
    check("G1.5 başka kullanıcı job → 404", s2.status_code == 404, f"status={s2.status_code}")

    # Bilinmeyen job → 404
    s3 = client.get("/api/v1/plans/generate/00000000-0000-0000-0000-000000000000",
                    headers=_auth(tok))
    check("G1.6 bilinmeyen job → 404", s3.status_code == 404, f"status={s3.status_code}")

    # sync=true → 201 + PlanResp (eski davranış korunur)
    rs = client.post("/api/v1/plans/generate?sync=true", headers=_auth(tok),
                     json={"baby_id": bid})
    check("G1.7 sync=true → 201 + PlanResp",
          rs.status_code == 201 and rs.json().get("id") is not None,
          f"status={rs.status_code} body={rs.text[:120]}")

    # Doğum tarihi yok → 400 (sync yolunda erken doğrulama)
    bid2 = client.post("/api/v1/babies", headers=_auth(tok),
                       json={"name": "NoDob"}).json()["id"]
    rnd = client.post("/api/v1/plans/generate", headers=_auth(tok), json={"baby_id": bid2})
    check("G1.8 doğum tarihi yok → 400", rnd.status_code == 400, f"status={rnd.status_code}")


# ===========================================================================
# G2 — rate limit (brute-force)
# ===========================================================================
def test_g2():
    rate_limit.reset()
    _register("g2_victim@example.com", "CorrectPass1!")

    # 5 ardışık hatalı login → hesap kilidi
    codes = []
    for i in range(5):
        r = client.post("/api/v1/auth/login",
                        json={"email": "g2_victim@example.com", "password": f"wrong{i}"})
        codes.append(r.status_code)
    check("G2.1 ilk 5 hatalı deneme 401", codes == [401] * 5, f"codes={codes}")

    # 6. deneme (doğru parola bile) → hesap kilitli 429 + Retry-After
    r6 = client.post("/api/v1/auth/login",
                     json={"email": "g2_victim@example.com", "password": "CorrectPass1!"})
    check("G2.2 kilit sonrası → 429", r6.status_code == 429, f"status={r6.status_code}")
    check("G2.3 Retry-After başlığı var",
          "retry-after" in {k.lower() for k in r6.headers}, str(dict(r6.headers)))

    # Başarılı login sayacı sıfırlar (farklı hesap, temiz IP penceresi)
    rate_limit.reset()
    _register("g2_ok@example.com", "GoodPass1!")
    client.post("/api/v1/auth/login",
                json={"email": "g2_ok@example.com", "password": "bad1"})   # 1 hata
    ok = client.post("/api/v1/auth/login",
                     json={"email": "g2_ok@example.com", "password": "GoodPass1!"})
    check("G2.4 başarılı login 200 (sayaç sıfırlandı)", ok.status_code == 200,
          f"status={ok.status_code}")
    # başarıdan sonra tekrar hata → yeniden 401 (kilitli değil)
    again = client.post("/api/v1/auth/login",
                        json={"email": "g2_ok@example.com", "password": "bad2"})
    check("G2.5 başarı sonrası hata yine 401 (kilit sıfırlandı)",
          again.status_code == 401, f"status={again.status_code}")

    # IP limiti: aynı IP'den >10 istek/dk → 429 (farklı e-postalarla, hesap kilidi değil)
    rate_limit.reset()
    ip_codes = []
    for i in range(12):
        r = client.post("/api/v1/auth/login",
                        json={"email": f"ipflood_{i}@example.com", "password": "x"})
        ip_codes.append(r.status_code)
    check("G2.6 IP 10/dk aşımı → 429 görülür",
          429 in ip_codes and ip_codes[:10].count(429) == 0,
          f"ip_codes={ip_codes}")
    rate_limit.reset()


# ===========================================================================
# G3 — voice/generate voiceId sahipliği
# ===========================================================================
def test_g3():
    # TTS mock (ağ yok) — env anahtarı gerektirmeden ses üretilmiş gibi
    tts.voice_audio = lambda voice_id, text, profil=tts.MASAL_PROFILI, user_id=None: {
        "audio_url": "/audio/deadbeefdeadbeef.mp3", "cached": False, "tts_usd": 0.0}

    tok = _register("g3_user@example.com")
    from api.db import SessionLocal
    from api.models import VoiceProfile
    import uuid as _uuid
    # Kullanıcının kendi voice profilini DB'ye ekle (klon akışını mock'lamadan)
    uid = None
    dbs = SessionLocal()
    try:
        from api.services.security import decode_access_token
        uid = _uuid.UUID(decode_access_token(tok)["sub"])
        dbs.add(VoiceProfile(user_id=uid, elevenlabs_voice_id="MY_OWN_VOICE",
                             status="ready"))
        dbs.commit()
    finally:
        dbs.close()

    # Sahip olunmayan voiceId → 403
    r_bad = client.post("/api/v1/voice/generate", headers=_auth(tok),
                        json={"voiceId": "SOMEONE_ELSE_VOICE", "text": "Merhaba dünya"})
    check("G3.1 sahip olunmayan voiceId → 403", r_bad.status_code == 403,
          f"status={r_bad.status_code} body={r_bad.text[:120]}")

    # Sahip olunan voiceId → 200
    r_ok = client.post("/api/v1/voice/generate", headers=_auth(tok),
                       json={"voiceId": "MY_OWN_VOICE", "text": "Merhaba dünya"})
    check("G3.2 sahip olunan voiceId → 200", r_ok.status_code == 200,
          f"status={r_ok.status_code} body={r_ok.text[:120]}")


# ===========================================================================
# G4 — docs production'da kapalı (ayrı subprocess: farklı ENVIRONMENT)
# ===========================================================================
def test_g4():
    # Dev app'te docs AÇIK
    d = client.get("/docs")
    o = client.get("/openapi.json")
    check("G4.1 dev'de /docs açık (200)", d.status_code == 200, f"status={d.status_code}")
    check("G4.2 dev'de /openapi.json açık (200)", o.status_code == 200,
          f"status={o.status_code}")

    # Production'da /docs KAPALI (404); /openapi.json X-API-Key (DEMO_API_KEY) ARKASINDA
    # (Faz T2 güncellemesi): anahtarsız 401, doğru anahtar 200. Temiz süreçte doğrula.
    code = (
        "import os;"
        "os.environ['ENVIRONMENT']='production';"
        "os.environ['JWT_SECRET']='test-secret-en-az-otuz-iki-karakter-uzunlugunda';"
        "os.environ['DATABASE_URL']='sqlite:///./_g4_prod_test.db';"
        "os.environ['MAIL_PROVIDER']='disabled';"
        "os.environ['DEMO_API_KEY']='g4-openapi-key';"
        "os.environ.setdefault('ANTHROPIC_API_KEY','x');"
        "from fastapi.testclient import TestClient;"
        "from api.main import app;"
        "c=TestClient(app);"
        "import sys;"
        "ok=(c.get('/docs').status_code==404 "                      # docs kapalı
        "and c.get('/openapi.json').status_code==401 "             # anahtarsız 401
        "and c.get('/openapi.json',headers={'X-API-Key':'yanlis'}).status_code==401 "
        "and c.get('/openapi.json',headers={'X-API-Key':'g4-openapi-key'}).status_code==200);"
        "sys.exit(0 if ok else 1)"
    )
    p = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                       capture_output=True, text=True)
    check("G4.3 production'da /docs 404 + /openapi.json X-API-Key arkasında (401/200)",
          p.returncode == 0, f"rc={p.returncode} err={p.stderr[-200:]}")
    try:
        (ROOT / "_g4_prod_test.db").unlink()
    except OSError:
        pass


# ===========================================================================
# G5 — subscriptions mock reddi + /status premium
# ===========================================================================
def test_g5():
    tok = _register("g5_user@example.com")

    # Mock makbuz → 400
    for rd in ("dev_receipt_123", "mock_receipt", "test_abc", "   "):
        r = client.post("/api/v1/subscriptions/verify", headers=_auth(tok),
                        json={"platform": "ios", "product_id": "premium_monthly",
                              "receipt_data": rd if rd.strip() else "  "})
        # "   " min_length=1 geçer ama strip sonrası boş → 400
        check(f"G5.1 mock/boş makbuz reddi ({rd.strip() or 'bosluk'}) → 400",
              r.status_code == 400, f"status={r.status_code}")

    # /status: abonelik yokken premium=false/none
    st0 = client.get("/api/v1/subscriptions/status", headers=_auth(tok))
    check("G5.2 abonelik yok → premium=false, source=none",
          st0.status_code == 200 and st0.json() == {"premium": False, "source": "none"},
          str(st0.json()))

    # Gerçek görünen makbuz → active
    r_ok = client.post("/api/v1/subscriptions/verify", headers=_auth(tok),
                       json={"platform": "ios", "product_id": "premium_monthly",
                             "receipt_data": "A1b2C3realbase64receiptdata=="})
    check("G5.3 gerçek makbuz → 200 active",
          r_ok.status_code == 200 and r_ok.json().get("status") == "active",
          f"status={r_ok.status_code} body={r_ok.text[:120]}")

    # /status: artık premium=true, source=subscription
    st1 = client.get("/api/v1/subscriptions/status", headers=_auth(tok))
    check("G5.4 aktif abonelik → premium=true, source=subscription",
          st1.json() == {"premium": True, "source": "subscription"}, str(st1.json()))

    # BETA_PREMIUM_ALL flag (config seviyesi + status mantığı — subprocess)
    code = (
        "import os;"
        "os.environ['ENVIRONMENT']='development';"
        "os.environ['BETA_PREMIUM_ALL']='true';"
        "os.environ['JWT_SECRET']='test-secret-en-az-otuz-iki-karakter-uzunlugunda';"
        "os.environ['DATABASE_URL']='sqlite:///./_g5_beta_test.db';"
        "os.environ['MAIL_PROVIDER']='disabled';"
        "os.environ.setdefault('ANTHROPIC_API_KEY','x');"
        "from fastapi.testclient import TestClient;"
        "from api.db import Base, engine; import api.models;"
        "Base.metadata.create_all(bind=engine);"
        "from api.main import app;"
        "c=TestClient(app);"
        "reg=c.post('/api/v1/auth/register',json={'email':'beta@example.com','password':'TestPass123!'});"
        "tok=reg.json()['access_token'];"
        "st=c.get('/api/v1/subscriptions/status',headers={'Authorization':'Bearer '+tok});"
        "import sys;"
        "sys.exit(0 if st.json()=={'premium':True,'source':'beta'} else 1)"
    )
    p = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                       capture_output=True, text=True)
    check("G5.5 BETA_PREMIUM_ALL=true → premium=true, source=beta (sunucu tarafı)",
          p.returncode == 0, f"rc={p.returncode} err={p.stderr[-200:]}")
    try:
        (ROOT / "_g5_beta_test.db").unlink()
    except OSError:
        pass


# ===========================================================================
# G6 — chat history trim
# ===========================================================================
def test_g6():
    # Saf fonksiyon testi
    hist10 = [ChatMessageItem(role="user", content=str(i)) for i in range(10)]
    trimmed = chat_router.trim_history(hist10)
    check("G6.1 10 mesaj → son 6'ya kırpıldı",
          len(trimmed) == 6 and trimmed[0].content == "4" and trimmed[-1].content == "9",
          f"len={len(trimmed)}")
    check("G6.2 kısa geçmiş dokunulmaz",
          len(chat_router.trim_history(hist10[:3])) == 3, "")
    check("G6.3 boş geçmiş → boş", chat_router.trim_history([]) == [], "")

    # Entegrasyon: 20 mesajlık geçmişle POST /chat → 200 (kırpılır, çökmez).
    chatbot._cevap_uret = lambda message, yas=None, baby_context=None: {
        "cevap": "MOCK CEVAP", "cache_hit": False, "kaynaklar": [],
        "anahtar": "abc", "llm": False, "in_chars": 0, "out_chars": 9,
        "retrieval_layer": "k1", "top_score": 0.7}
    tok = _register("g6_user@example.com")
    big_history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
                   for i in range(20)]
    r = client.post("/api/v1/chat", headers=_auth(tok),
                    json={"message": "Bebeğim nasıl uyur?", "history": big_history})
    check("G6.4 20 mesajlık geçmişle /chat → 200 (çökmez)",
          r.status_code == 200 and r.json().get("answer") == "MOCK CEVAP",
          f"status={r.status_code} body={r.text[:120]}")


def main() -> int:
    for fn in (test_g1, test_g2, test_g3, test_g4, test_g5, test_g6):
        try:
            fn()
        except Exception as e:                       # bir test bloğu patlarsa raporla, devam et
            import traceback
            check(f"{fn.__name__} ÇALIŞTI", False, f"EXCEPTION: {e}\n{traceback.format_exc()[-400:]}")

    print("\n" + "=" * 74)
    print("FAZ G TEST SONUÇLARI")
    print("=" * 74)
    passed = 0
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        line = f"[{mark}] {name}"
        if not ok and detail:
            line += f"\n       {detail}"
        print(line)
    print("-" * 74)
    print(f"TOPLAM: {passed}/{len(results)} geçti")
    print("=" * 74)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
