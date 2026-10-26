"""
Sor — uzamış program ve her zaman açık saat korkuluğu (2026-10-26 prod şikâyeti).

VAKA: 7 aylık bebek, 13 günlük planın 18. günü, anne aşamayı "Kapı" seçmiş,
training_completed_at BOŞ. Bugün 06:35 uyanış; uykular 09:31–10:08, 13:04–13:42,
16:43–17:13; plan gece yatışı 19:43 (17:13 + 150 dk). Sor eğitimi "tamamlandı"
saydı → kural bloğu ve sayı doğrulaması hiç çalışmadı, model "yatır-çık
aşamasındasınız" ve "gece yatışını 19:00–19:15'e çekin" dedi.

  D  gun_durumu: completed boş → "uzatma" (tamamlandi DEĞİL); aşama Kapı, kural
     günü 9; completed dolu → tamamlandi
  B  Bağlam: "Eğitimin 18. günü", "UZATILDI", aşama Kapı; "tamamlandı" yok;
     uyanıklık penceresi 120–180 ve planın notu var
  K  Kural bloğu dolu (Kapı, 9. günün değerleri), yatır-çık yok; takvim
     "program tamamlanmış olur" demiyor
  V  /chat: SISTEM_KURALI + geçmiş kuralı (E6) sistemde; doğrulama "duzelt":
     "19:00–19:15'e çekin" → "Bugünkü plana göre gece yatışı 19:43."
  S  Eğitim tamamlandı + bugünün planı: yalnız SAAT korkuluğu (SAAT_KURALI,
     saat düzeltilir, dakika sayısına dokunulmaz)
  G  Plan son cevaptan sonra yeniden hesaplandıysa bağlam söyler

Çalıştırma: python tests/test_sor_uzatma.py
"""
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "sor_uzatma_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["BETA_MODE"] = "true"
for _b, _v in (("SOR_BAGLAM_V2", "true"), ("SOR_GECMIS", "true"), ("SOR_KURAL_BLOGU", "true"),
               ("SOR_SAYI_DOGRULAMA", "duzelt"), ("YONTEM_TABLOSU", "true")):
    os.environ[_b] = _v

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.main import app                                    # noqa: E402
from api.models import Baby, ChatMessage, SleepLog, SleepPlan, User  # noqa: E402
from api.services import plan_service, sor_baglam, sor_kural  # noqa: E402
from api.zaman import TR, bugun_tr, saat_sabitle            # noqa: E402
from engine import chatbot                                  # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, muhurle            # noqa: E402
muhurle()

# Sohbet motoru gerçek kodla; Anthropic istemcisi sahte — yükü yakalar ve
# vakadaki yanlış cevabı (19:00–19:15) üretir.
os.environ["ANTHROPIC_API_KEY"] = "test-dummy"
chatbot.HAS_ANTHROPIC = True
chatbot.CACHE_PATH = Path(tempfile.gettempdir()) / "sor_uzatma_cache.json"
if chatbot.CACHE_PATH.exists():
    chatbot.CACHE_PATH.unlink()
chatbot._cache_state.update(loaded=False, entries=[], emb_matrix=None, emb_idx=[])

YUKLER: list[dict] = []
CEVAP = {"metin": ""}


class _SahteIstemci:
    def __init__(self, *a, **k):
        self.messages = self

    def create(self, **kw):
        YUKLER.append(kw)
        return SimpleNamespace(
            content=[SimpleNamespace(text=CEVAP["metin"])],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0))


chatbot.Anthropic = _SahteIstemci

Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


BUGUN = bugun_tr()
AN = datetime(BUGUN.year, BUGUN.month, BUGUN.day, 17, 15, tzinfo=TR)   # "Kestirme yapmazsa?"


def tr(gun_fark: int, hhmm: str) -> datetime:
    """TR duvar saati → UTC an (SQLite saat dilimini atar; kayıtlar UTC saklanır)."""
    g = BUGUN + timedelta(days=gun_fark)
    return datetime(g.year, g.month, g.day, int(hhmm[:2]), int(hhmm[3:]),
                    tzinfo=TR).astimezone(timezone.utc)


# --- Kurulum: vakanın bebeği -------------------------------------------------
tok = client.post("/api/v1/auth/register", json={
    "email": "uzatma@gercek.com", "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
BID = client.post("/api/v1/babies", headers=H, json={
    "name": "Ada", "night_wakes": 2,
    "birth_date": (BUGUN - timedelta(days=216)).isoformat(),        # 7 aylık
    **TAM_PROFIL}).json()["id"]
r = client.post("/api/v1/plans/generate?sync=true", headers=H, json={"baby_id": BID})
assert r.status_code in (200, 201), r.text[:200]

db = SessionLocal()
b = db.get(Baby, uuid.UUID(BID))
b.training_started_at = BUGUN - timedelta(days=17)                  # bugün 18. gün
b.training_completed_at = None
b.mevcut_asama = "kapi"
for bas, bit in ((tr(-1, "20:04"), tr(0, "06:35")), (tr(0, "09:31"), tr(0, "10:08")),
                 (tr(0, "13:04"), tr(0, "13:42")), (tr(0, "16:43"), tr(0, "17:13"))):
    db.add(SleepLog(user_id=b.user_id, baby_id=b.id, type="sleep", started_at=bas,
                    ended_at=bit, client_id=f"t-{bas:%H%M}"))
db.commit()
user = db.get(User, b.user_id)
with saat_sabitle(AN):
    plan = plan_service.ensure_today_plan(db, user, b, BUGUN, now_minute=17 * 60 + 15)
sched = {x.get("key"): x for x in (plan.content or {}).get("schedule") or []}
YATIS = (sched.get("bedtime") or {}).get("time")
check("Kurulum: plan bugünün, gece yatışı 19:43 (17:13 + 150 dk)",
      plan.plan_date == BUGUN and YATIS == "19:43", (plan.plan_date, YATIS))
check("Kurulum: planın notu 'en erken 19:13' (kestirmeye yer yok)",
      any("19:13" in u for u in (plan.content.get("adaptation") or {}).get("uyarilar") or []),
      (plan.content.get("adaptation") or {}).get("uyarilar"))

# =============================================================================
# D — gun_durumu
# =============================================================================
d = sor_baglam.gun_durumu(db, b, AN)
check("D) 18. gün, completed boş → durum 'uzatma' (tamamlandi DEĞİL)",
      d["durum"] == "uzatma" and d["app_gun"] == 18, (d["durum"], d["app_gun"]))
_ua, _kg = sor_kural.uzatma_asamasi(d)
check("D) Uzatma aşaması annenin seçtiği Kapı, kural günü 9",
      d["uzatma_beyan"] == "kapi" and _ua["kod"] == "kapi" and _kg == 9, (_ua["kod"], _kg))
_ua2, _kg2 = sor_kural.uzatma_asamasi(dict(d, uzatma_beyan=None))
check("D) Aşama seçilmemişse yatır-çık, kural günü 13",
      _ua2["kod"] == "yatir_cik" and _kg2 == 13, (_ua2["kod"], _kg2))

# =============================================================================
# B — bağlam satırları
# =============================================================================
s = sor_baglam.zaman_satirlari(db, b, simdi=AN)
check("B) 'Eğitimin 18. günü' + 'UZATILDI' + aşama Kapı",
      "Eğitimin 18. günü" in s and "UZATILDI" in s and "aşama: Kapı" in s, s)
check("B) 'program tamamlandı' ve yatır-çık aşaması yok",
      "program tamamlandı." not in s and "aşama: Yatır-çık" not in s, s)
check("B) Uyanıklık penceresi 120–180 dk bağlamda", "Uyanıklık penceresi" in s
      and "120–180 dk" in s, s)
check("B) Planın notu bağlamda (en erken 19:13)", "Planın notu:" in s and "19:13" in s, s)
check("B) Plan adımı Kapı aşamasının bölümünden (Gün 7–9)", "Gün 7–9" in s or "Kapı" in s, s)

# =============================================================================
# K — kural bloğu
# =============================================================================
k = sor_kural.kurallar(d, b, soru="Kestirme yapmazsa?")
check("K) Kural bloğu DOLU", k is not None and "BUGÜNÜN KURALLARI" in k["blok"])
blok = k["blok"] if k else ""
check("K) Aşama Kapı, 9. günün değerleri, program tamamlanmadı",
      k and k["asama"]["kod"] == "kapi" and k["gun"] == 9
      and "eğitimin 18. günü" in blok and "TAMAMLANMADI" in blok, blok[:400])
check("K) Yatır-çık dizisi kullanılmaz", "Yatır-çık 13. günde başlar" in blok, blok[-600:])
check("K) Takvim 'program tamamlanmış olur' demiyor; yarın 19. gün",
      "program tamamlanmış olur" not in blok and "eğitimin 19. günü" in blok, blok[:600])
check("K) Plan yatış saati ve uyanıklık sınırları (19:13 / 20:13) izinli",
      k and {"19:43", "19:13", "20:13"} <= k["saatler"], sorted(k["saatler"]) if k else None)
check("K) 19:15 izinli değil", k and "19:15" not in k["saatler"])

# =============================================================================
# V — /chat uçtan uca: sistem kuralı + doğrulama duzelt
# =============================================================================
CEVAP["metin"] = ("Kestirme yapmazsa gece yatışını 19:00–19:15'e çekin. "
                  "Aşırı yorgunluğun önüne geçmiş olursunuz.")
YUKLER.clear()
with saat_sabitle(AN):
    r = client.post("/api/v1/chat", headers=H, json={"message": "Kestirme yapmazsa?",
                                                       "baby_id": BID})
yuk = json.dumps({"system": YUKLER[-1]["system"], "messages": YUKLER[-1]["messages"]},
                 ensure_ascii=False) if YUKLER else ""
check("V) 200", r.status_code == 200, r.text[:200])
check("V) Sistemde SISTEM_KURALI ve geçmiş kuralı (E6)",
      "EK KURAL (süre ve sayı)" in yuk and "çelişirse plan doğrudur" in yuk, yuk[-800:])
check("V) Bağlamda kural bloğu + uyanıklık penceresi + plan notu",
      "BUGÜNÜN KURALLARI" in yuk and "Uyanıklık penceresi" in yuk and "Planın notu" in yuk)
cevap = r.json().get("answer", "") if r.status_code == 200 else ""
check("V) Doğrulama 'duzelt': 19:00–19:15 cümlesi plan yatışıyla değişti",
      "19:15" not in cevap and "gece yatışı 19:43" in cevap, cevap)
check("V) İhlalsiz cümle korunur", "Aşırı yorgunluğun" in cevap, cevap)

# =============================================================================
# G — plan son cevaptan sonra yeniden hesaplandı
# =============================================================================
c = dict(plan.content)
c["adaptation"] = dict(c.get("adaptation") or {}, hesaplandi_at=tr(0, "17:13").isoformat())
plan.content = c
db.query(ChatMessage).filter(ChatMessage.user_id == b.user_id).delete()
db.add(ChatMessage(user_id=b.user_id, role="assistant", content="eski cevap",
                   created_at=tr(0, "16:43")))
db.commit()
YUKLER.clear()
CEVAP["metin"] = "Gece yatışı 19:43."
with saat_sabitle(AN):
    r = client.post("/api/v1/chat", headers=H, json={
        "message": "Uyandı", "baby_id": BID,
        "history": [{"role": "user", "content": "Şimdi uyudu"},
                    {"role": "assistant", "content": "Gece yatışını 20:23'te koru."}]})
yuk = json.dumps(YUKLER[-1]["messages"], ensure_ascii=False) if YUKLER else ""
check("G) Plan son cevaptan (16:43) sonra 17:13'te hesaplandı → bağlam söylüyor",
      "Plan güncellendi" in yuk and "17:13" in yuk, yuk[:300])
with saat_sabitle(AN):
    s_gecmissiz = sor_baglam.zaman_satirlari(db, b, simdi=AN)
check("G) Sohbet yoksa 'Plan güncellendi' satırı yok", "Plan güncellendi" not in s_gecmissiz)

# =============================================================================
# S — tamamlandı: yalnız saat korkuluğu
# =============================================================================
b.training_completed_at = BUGUN - timedelta(days=2)
db.commit()
d2 = sor_baglam.gun_durumu(db, b, AN)
check("S) completed dolu → tamamlandi, kural bloğu yok",
      d2["durum"] == "tamamlandi" and sor_kural.kurallar(d2, b) is None, d2["durum"])
kk = sor_kural.saat_korkulugu(d2, b)
check("S) Bugünün planı var → saat korkuluğu kurulur", kk is not None and kk.get("yalniz_saat"))
CEVAP["metin"] = ("Kestirme yapmazsa gece yatışını 19:00–19:15'e çekin. "
                  "Uyumazsa 7 dakika bekleyin.")
YUKLER.clear()
with saat_sabitle(AN):
    r = client.post("/api/v1/chat", headers=H, json={"message": "Kestirme yapmazsa?",
                                                       "baby_id": BID})
yuk = json.dumps({"system": YUKLER[-1]["system"]}, ensure_ascii=False) if YUKLER else ""
cevap = r.json().get("answer", "") if r.status_code == 200 else ""
check("S) Sistemde SAAT_KURALI (süre/sayı kuralı değil)",
      "EK KURAL (saatler)" in yuk and "EK KURAL (süre ve sayı)" not in yuk, yuk[-600:])
check("S) Yanlış yatış saati düzeltildi", "19:15" not in cevap and "19:43" in cevap, cevap)
check("S) Dakika sayısına dokunulmadı (yalnız saat)", "7 dakika" in cevap, cevap)

# Bugün için plan yoksa korkuluk yok (eski davranış)
d3 = dict(d2, plan=None)
check("S) Bugünün planı yoksa korkuluk None", sor_kural.saat_korkulugu(d3, b) is None)

db.close()

# =============================================================================
gecen = sum(1 for _, ok, _ in results if ok)
for ad, ok, detay in results:
    print(("PASS " if ok else "FAIL ") + ad + ("" if ok else f"\n     {detay[:600]}"))
print(f"\n{gecen}/{len(results)} geçti")
sys.exit(0 if gecen == len(results) else 1)
