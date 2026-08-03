"""
Bebek log bağlamı testleri (Faz 6.5) — DB gerçek (geçici SQLite), LLM MOCK'lu.

Kapsam:
  1-4. Bağlam derleme: profil satırı, gün etiketleri, planlanan yatışla kıyas,
       gece uyanmasının doğru geceye yazılması, veri yoksa None
  5.   Prompt'a "BEBEK VERİSİ:" bloğu RAG chunk'larından AYRI giriyor
  6.   CACHE BYPASS: baby_id'li istek cache'e YAZMAZ ve cache'ten OKUMAZ
  7.   baby_id'siz istekte cache HÂLÂ çalışıyor
  8.   Endpoint: cevap bebek adı + somut saat içeriyor; başka kullanıcının
       bebeği 404; log yoksa genel davranış korunur

Çalıştırma: python tests/test_baby_context.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

_DB = Path(tempfile.gettempdir()) / "faz65_babyctx_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["ANTHROPIC_API_KEY"] = "test-dummy"          # mock LLM
os.environ["MAIL_PROVIDER"] = "disabled"

from engine import chatbot                              # noqa: E402
from api.db import SessionLocal, engine                 # noqa: E402
from api.db.base import Base                            # noqa: E402
from api.models import Baby, SleepLog, SleepPlan, User   # noqa: E402
from api.services import baby_context as bctx           # noqa: E402
from api.services import plan_adapter                   # noqa: E402
from api.services.security import hash_password         # noqa: E402

Base.metadata.create_all(engine)

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


# --- LLM MOCK: gönderilen prompt'u yakala, sabit cevap dön -------------------
PROMPTS: list[str] = []
LLM_CALLS = {"n": 0}


class _Msgs:
    def create(self, **kw):
        LLM_CALLS["n"] += 1
        PROMPTS.append(kw["messages"][0]["content"])
        return type("R", (), {"content": [type("B", (), {
            "text": f"MOCK CEVAP #{LLM_CALLS['n']}"})()]})()


class _FakeAnthropic:
    def __init__(self, *a, **kw):
        self.messages = _Msgs()


chatbot.Anthropic = _FakeAnthropic
chatbot.HAS_ANTHROPIC = True
chatbot.init_index()

TZ = plan_adapter.TZ_OFFSET_MIN
TODAY = datetime(2026, 8, 3).date()
db = SessionLocal()


def utc_at(gun_farki: int, saat: int, dakika: int = 0) -> datetime:
    """Yerel (UTC+3) duvar saatini UTC datetime'a çevir."""
    g = datetime(2026, 8, 3, tzinfo=timezone.utc) - timedelta(days=gun_farki)
    return g + timedelta(hours=saat, minutes=dakika) - timedelta(minutes=TZ)


u = User(email="ctx@tavsansmoke.com", password_hash=hash_password("GucluParola123!"))
db.add(u)
db.commit()
db.refresh(u)

baby = Baby(user_id=u.id, name="Elif",
            birth_date=datetime(2025, 4, 3).date(),     # 2026-08-03'te 16 aylık
            night_wakes=3,
            training_started_at=datetime(2026, 7, 1).date(),
            training_completed_at=datetime(2026, 7, 15).date())
db.add(baby)
db.commit()
db.refresh(baby)

# Bugünün planı: yatış 20:00
sched = plan_adapter.build_schedule(
    {"uyaniklik_penceresi": {"RESMI_DEGER_genel_kullanim": "5-6 Saat"},
     "uyku_sayisi": {"RESMI_DEGER": "1"},
     "gunduz_uyku_total": "2-3 Saat",
     "yatma_vakti": "19:00 - 20:00"}, 7 * 60)
db.add(SleepPlan(user_id=u.id, baby_id=baby.id, plan_date=TODAY,
                 content={"schedule": sched, "bucket": "15-17_ay"}))

# Sabit log fixture'ı
LOGS = [
    # dün gece yatış 19:05 (planlanandan 55dk erken)
    SleepLog(user_id=u.id, baby_id=baby.id, type="sleep",
             started_at=utc_at(1, 19, 5), ended_at=utc_at(0, 7, 0)),
    # bugün 03:10'da 25dk gece uyanması → DÜNÜN gecesine yazılmalı
    SleepLog(user_id=u.id, baby_id=baby.id, type="night_wake",
             started_at=utc_at(0, 3, 10), ended_at=utc_at(0, 3, 35)),
    # bugün şekerleme 12:30-13:15
    SleepLog(user_id=u.id, baby_id=baby.id, type="nap",
             started_at=utc_at(0, 12, 30), ended_at=utc_at(0, 13, 15)),
    # önceki gün yatış 20:30 (30dk geç)
    SleepLog(user_id=u.id, baby_id=baby.id, type="sleep",
             started_at=utc_at(2, 20, 30), ended_at=utc_at(1, 6, 45)),
]
for lg in LOGS:
    db.add(lg)
db.commit()

# =============================================================================
# 1-4) Bağlam derleme
# =============================================================================
ctx = bctx.build_baby_context(db, baby, today=TODAY)
print("--- DERLENEN BAĞLAM ---")
print(ctx)
print("-----------------------")

check("1) Profil: ad + ay + eğitim tarihleri",
      ctx is not None and "Elif" in ctx and "16 aylık" in ctx
      and "2026-07-15" in ctx, str(ctx)[:200])

check("2) Dün gece yatış 19:05 + planlanandan 55dk erken",
      "19:05" in ctx and "55dk erken" in ctx, str(ctx)[:300])

check("3) Gece uyanması DÜNÜN gecesine yazıldı (03:10, 25dk)",
      "03:10" in ctx and "25dk" in ctx
      and ctx.index("03:10") > ctx.index("dün"), str(ctx)[:300])

check("4) Bugün şekerleme 12:30-13:15",
      "12:30-13:15" in ctx, str(ctx)[:300])

check("4b) Bugünün plan çizelgesi özeti var",
      "Bugünün planı" in ctx and "20:00 yatış" in ctx, str(ctx)[:400])

# Veri yoksa None
bos = Baby(user_id=u.id, name="Bos")
db.add(bos)
db.commit()
db.refresh(bos)
check("4c) Log ve plan yoksa None (mevcut davranış korunur)",
      bctx.build_baby_context(db, bos, today=TODAY) is None, "")

# =============================================================================
# 5) Prompt bloğu: BEBEK VERİSİ, RAG chunk'larından AYRI
# =============================================================================
PROMPTS.clear()
r = chatbot._cevap_uret("bebeğim gece uyanıyor ne yapmalıyım", baby_context=ctx)
p = PROMPTS[-1] if PROMPTS else ""
check("5) Prompt'ta 'BEBEK VERİSİ:' bloğu var",
      "BEBEK VERİSİ" in p, p[:200])
check("5b) Blok, RAG parçalarından ÖNCE ve AYRI",
      "BEBEK VERİSİ" in p and "İLGİLİ BİLGİ PARÇALARI" in p
      and p.index("BEBEK VERİSİ") < p.index("İLGİLİ BİLGİ PARÇALARI"), "")
check("5c) Sistem promptunda bebek verisi KURALI var",
      "BEBEK VERİSİ KURALI" in chatbot.SYSTEM_PROMPT, "")
check("5d) Bebek verisi system prompt'a (cache prefix) GİRMEZ",
      "Elif" not in chatbot.SYSTEM_PROMPT, "")

# =============================================================================
# 6) CACHE BYPASS — kişisel cevap paylaşılan cache'e girmez
# =============================================================================
SORU = "bebeğim gece sık uyanıyor kişisel test"
LLM_CALLS["n"] = 0
r1 = chatbot._cevap_uret(SORU, baby_context=ctx)
r2 = chatbot._cevap_uret(SORU, baby_context=ctx)
check("6) baby_context'li 2. istek cache'ten DÖNMEDİ (LLM tekrar çağrıldı)",
      r1["cache_hit"] is False and r2["cache_hit"] is False and LLM_CALLS["n"] == 2,
      f"llm_calls={LLM_CALLS['n']} c1={r1['cache_hit']} c2={r2['cache_hit']}")

# Aynı soru baby_context'SİZ sorulunca kişisel cevap SIZMAMALI
r3 = chatbot._cevap_uret(SORU)
check("6b) Kişisel cevap paylaşılan cache'e YAZILMADI",
      r3["cache_hit"] is False, f"cache_hit={r3['cache_hit']} (kişisel cevap sızdı!)")

# =============================================================================
# 7) baby_id'siz istekte cache HÂLÂ çalışıyor
# =============================================================================
GENEL = "beyaz gürültü genel test sorusu"
LLM_CALLS["n"] = 0
g1 = chatbot._cevap_uret(GENEL)
g2 = chatbot._cevap_uret(GENEL)
check("7) Genel soruda cache çalışıyor (2. istek cache hit)",
      g1["cache_hit"] is False and g2["cache_hit"] is True and LLM_CALLS["n"] == 1,
      f"llm_calls={LLM_CALLS['n']} c1={g1['cache_hit']} c2={g2['cache_hit']}")

# =============================================================================
# 8) Endpoint
# =============================================================================
from fastapi.testclient import TestClient    # noqa: E402
from api.main import app                     # noqa: E402

c = TestClient(app)
tok = c.post("/api/v1/auth/login",
             json={"email": "ctx@tavsansmoke.com",
                   "password": "GucluParola123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

PROMPTS.clear()
r = c.post("/api/v1/chat", headers=H,
           json={"message": "bebeğim gece uyanıyor", "baby_id": str(baby.id)})
check("8) /chat baby_id ile -> 200", r.status_code == 200, r.text[:200])
p = PROMPTS[-1] if PROMPTS else ""
check("8b) Endpoint bağlamı prompt'a taşıdı (ad + somut saat)",
      "Elif" in p and "19:05" in p, p[:300])
check("8c) Kişisel istek cache'lenmedi (cached=false)",
      r.json().get("cached") is False, str(r.json().get("cached")))

# Başka kullanıcının bebeği → 404
u2 = User(email="ctx2@tavsansmoke.com", password_hash=hash_password("GucluParola123!"))
db.add(u2)
db.commit()
tok2 = c.post("/api/v1/auth/login",
              json={"email": "ctx2@tavsansmoke.com",
                    "password": "GucluParola123!"}).json()["access_token"]
r = c.post("/api/v1/chat", headers={"Authorization": f"Bearer {tok2}"},
           json={"message": "test", "baby_id": str(baby.id)})
check("8d) Başka kullanıcının bebeği -> 404 (veri sızmaz)",
      r.status_code == 404, f"{r.status_code} {r.text[:120]}")

# baby_id'siz istek çalışmaya devam ediyor
r = c.post("/api/v1/chat", headers=H, json={"message": "uyku eğitimi nedir"})
check("8e) baby_id'siz /chat çalışıyor", r.status_code == 200, r.text[:160])

# Logu olmayan bebek → bağlam yok, istek yine başarılı
PROMPTS.clear()
r = c.post("/api/v1/chat", headers=H,
           json={"message": "bebeğim gece uyanıyor", "baby_id": str(bos.id)})
p = PROMPTS[-1] if PROMPTS else ""
check("8f) Logsuz bebek → BEBEK VERİSİ bloğu YOK, istek 200",
      r.status_code == 200 and "BEBEK VERİSİ" not in p, f"{r.status_code}")

db.close()

# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 74)
print("BEBEK BAĞLAMI TEST SONUÇLARI (Faz 6.5)")
print("=" * 74)
passed = 0
for name, ok, detail in results:
    mark = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    print(f"[{mark}] {name}")
    if detail and not ok:
        print(f"       {detail}")
print("-" * 74)
print(f"TOPLAM: {passed}/{len(results)} gecti")
print("=" * 74)
sys.exit(0 if passed == len(results) else 1)
