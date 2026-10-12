"""
Sor — zaman/gün bağlamı (SOR_BAGLAM_V2) ve sohbet geçmişi (SOR_GECMIS).

2026-10-12 prod incelemesi: anne "hep aynı cevabı alıyorum" dedi. Sunucu 13
farklı cevap üretmişti ama geçmiş modele hiç gitmiyordu ve bağlamda "şu an" /
"eğitimin kaçıncı günü" yoktu → her turda aynı netleştirme sorusu.

  K  Bayraklar KAPALI: motor eski imzayla çağrılır (ek parametre yok); bağlam
     ve log dışında hiçbir şey değişmez
  T  Takma ad: bayraklar açıkken modele giden yükte (system + BÜTÜN mesajlar)
     gerçek ad hiç yok — geçmişteki ekli hâller ("Ada'nın", "Ada'yı", "ADA",
     "Adanın") ve plan satırı dahil; cevapta gerçek ad var, Z?lv? kalıntısı yok
  G  Geçmiş: sırası korunur, o anki soru EN SONDA; 12 saatten eski (zamanı
     bilinen) mesaj düşer, zamansız kalır; createdAt yazımı da okunur;
     geçmişli istek cache'i ne okur ne yazar; kriz o anki soruya bakar;
     kısa takip sorusu retrieval'da önceki soruyla zenginleşir; log gecmis=N
  Z  Zaman satırı: 02:30 → "2. gününün gecesi; sabah 3. gün başlıyor" + uygulama
     "Gün 3"; gündüz / akşam; eğitim başlamamış, bekleme, yenidoğan, tamamlandı;
     plan adımı satırı; build_baby_context bayraktan ETKİLENMEZ

Çalıştırma: python tests/test_sor_baglam.py
"""
import json
import logging
import os
import re
import sys
import tempfile
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

_DB = Path(tempfile.gettempdir()) / "sor_baglam_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["BETA_MODE"] = "true"
os.environ["LOG_GELECEK_TOLERANS_DK"] = "1440"
for _b in ("SOR_BAGLAM_V2", "SOR_GECMIS"):
    os.environ.pop(_b, None)

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.main import app                                    # noqa: E402
from api.models import Baby, SleepPlan                      # noqa: E402
from api.routers import chat as chat_router                 # noqa: E402
from api.schemas.chat import ChatMessageItem                # noqa: E402
from api.services import baby_context, sor_baglam, takma_ad  # noqa: E402
from api.zaman import TR, bugun_tr, saat_sabitle            # noqa: E402
from engine import chatbot                                  # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, muhurle            # noqa: E402
muhurle()

# Sohbet motoru GERÇEK kodla koşar, yalnız Anthropic istemcisi sahte: modele
# giden yükü (system + messages) yakalar. Plan üretimi mühürlü (fallback).
os.environ["ANTHROPIC_API_KEY"] = "test-dummy"
chatbot.HAS_ANTHROPIC = True
chatbot.CACHE_PATH = Path(tempfile.gettempdir()) / "sor_baglam_cache.json"
if chatbot.CACHE_PATH.exists():
    chatbot.CACHE_PATH.unlink()
chatbot._cache_state.update(loaded=False, entries=[], emb_matrix=None, emb_idx=[])

YUKLER: list[dict] = []
Z_DESENI = re.compile(r"\bZ[aeoö][lr]v\w*")


class _SahteIstemci:
    def __init__(self, *a, **k):
        self.messages = self

    def create(self, **kw):
        YUKLER.append(kw)
        son = kw["messages"][-1]["content"]
        t = Z_DESENI.search(son)
        t = t.group(0) if t else "Bebek"
        metin = f"{t}'nın gece uykusu için {t}'yı erken yatırın."
        return SimpleNamespace(
            content=[SimpleNamespace(text=metin)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0))


chatbot.Anthropic = _SahteIstemci

Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


def bayrak(baglam: bool, gecmis: bool):
    for ad, acik in (("SOR_BAGLAM_V2", baglam), ("SOR_GECMIS", gecmis)):
        if acik:
            os.environ[ad] = "1"
        else:
            os.environ.pop(ad, None)


def yuk_metni(kw: dict) -> str:
    return json.dumps({"system": kw["system"], "messages": kw["messages"]},
                      ensure_ascii=False)


BUGUN = bugun_tr()
_n = 0


def hesap_bebek(ad: str, gun_yas: int, plan: bool = True):
    global _n
    _n += 1
    tok = client.post("/api/v1/auth/register", json={
        "email": f"sorb{_n}@gercek.com", "password": "TestPass123!"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    bid = client.post("/api/v1/babies", headers=h, json={
        "name": ad, "night_wakes": 2,
        "birth_date": (BUGUN - timedelta(days=gun_yas)).isoformat(),
        **TAM_PROFIL}).json()["id"]
    if plan:
        r = client.post("/api/v1/plans/generate?sync=true", headers=h, json={"baby_id": bid})
        assert r.status_code in (200, 201), r.text[:200]
    return h, bid


def bebek_guncelle(bid, **alanlar):
    db = SessionLocal()
    b = db.get(Baby, __import__("uuid").UUID(bid))
    for k, v in alanlar.items():
        setattr(b, k, v)
    db.commit()
    db.close()


# =============================================================================
# K — bayraklar kapalı: motor ESKİ imzayla çağrılır
# =============================================================================
H, BID = hesap_bebek("Ada", 460)                      # ~15 ay
_CAGRI: list = []
_asil = chatbot._cevap_uret


def _casus(*a, **k):
    _CAGRI.append((a, k))
    return _asil(*a, **k)


chatbot._cevap_uret = _casus
bayrak(False, False)
GECMIS = [
    {"role": "user", "content": "Ada'nın gece uyanmaları arttı, ADA hep ağlıyor"},
    {"role": "assistant", "content": "Ada'yı 19:00'da yatırın; Adanın odası karanlık olsun."},
    {"role": "user", "content": "Ada'ya kaçta mama vereyim?"},
    {"role": "assistant", "content": "Ada'ya yatıştan önce verin. Ada uyanınca 5 dk bekleyin."},
]
YUKLER.clear()
r = client.post("/api/v1/chat", headers=H, json={
    "message": "Ada'yı tekrar uyandı ne yapayım", "baby_id": BID, "history": GECMIS})
a, k = _CAGRI[-1]
check("K) Bayrak kapalı: 200", r.status_code == 200, r.text[:120])
check("K) Bayrak kapalı: motor eski imzayla (yalnız baby_context kw)",
      len(a) == 2 and set(k) == {"baby_context"}, (len(a), sorted(k)))
check("K) Bayrak kapalı: modele TEK mesaj, tek system bloğu, zaman satırı yok",
      YUKLER and len(YUKLER[-1]["messages"]) == 1 and len(YUKLER[-1]["system"]) == 1
      and "Şu an:" not in YUKLER[-1]["messages"][0]["content"],
      (len(YUKLER[-1]["messages"]), len(YUKLER[-1]["system"])) if YUKLER else None)

# Bağlam kurucusunun çıktısı bayraktan bağımsız (satır yalnız /chat'te eklenir)
db = SessionLocal()
b = db.get(Baby, __import__("uuid").UUID(BID))
bayrak(True, True)
c_acik = baby_context.build_baby_context(db, b, ad="Zalva")
bayrak(False, False)
c_kapali = baby_context.build_baby_context(db, b, ad="Zalva")
db.close()
check("K) build_baby_context bayraktan etkilenmez", c_acik == c_kapali and "Şu an" not in c_acik)

# =============================================================================
# T — takma ad: yükte gerçek ad yok (geçmiş + plan satırı dahil), cevapta var
# =============================================================================
# Plan gün bölümüne gerçek ad eklenir: LLM'in yazdığı plan metninde ad geçebilir.
db = SessionLocal()
p = db.query(SleepPlan).filter(SleepPlan.baby_id == __import__("uuid").UUID(BID)).first()
c = dict(p.content)
days = [dict(d) for d in c["days"]]
days[0]["markdown"] += "\n\nAda'yı beşiğe yatırın, dışarıda 5 dakika bekleyin."
c["days"] = days
p.content = c
db.commit()
db.close()
bebek_guncelle(BID, training_started_at=BUGUN - timedelta(days=1))

bayrak(True, True)
YUKLER.clear()
r = client.post("/api/v1/chat", headers=H, json={
    "message": "Ada'yı tekrar uyandı ne yapayım", "baby_id": BID, "history": GECMIS})
yuk = yuk_metni(YUKLER[-1]) if YUKLER else ""
GERCEK = re.compile(r"(?i)\bada(?![a-zçğıöşü])|\badanın\b")
check("T) Bayraklar açık: 200", r.status_code == 200, r.text[:160])
check("T) Yükte (system + bütün mesajlar) gerçek ad YOK",
      yuk and not GERCEK.search(yuk), GERCEK.findall(yuk)[:5])
check("T) Yükte takma ad var (geçmişte ve bağlamda)",
      yuk.count("Zalva") >= 5, yuk.count("Zalva"))
check("T) Plan satırı yükte ve takma adla ('Zalva'yı beşiğe')",
      "Zalva'yı beşiğe yatırın" in yuk, "")
cevap = r.json().get("answer", "")
check("T) Cevapta gerçek ad doğru ekle, Z?lv? kalıntısı yok",
      "Ada'nın" in cevap and "Ada'yı" in cevap and not Z_DESENI.search(cevap), cevap)
_m = YUKLER[-1]["messages"] if YUKLER else []
check("G) Mesaj sırası user/assistant…, o anki soru EN SONDA",
      [x["role"] for x in _m] == ["user", "assistant", "user", "assistant", "user"]
      and "ANNE SORUSU: Zalva'yı tekrar uyandı" in _m[-1]["content"],
      [x["role"] for x in _m])
check("G) Geçmiş mesajlar özgün sırada",
      len(_m) == 5 and "mama" in _m[2]["content"] and "5 dk" in _m[3]["content"], "")
check("Z) Bağlamda zaman satırı ve sistem eki var",
      "Şu an:" in _m[-1]["content"] and len(YUKLER[-1]["system"]) == 2
      and "E3. GÜVENLİK" in YUKLER[-1]["system"][1]["text"], "")
a, k = _CAGRI[-1]
check("G) Kısa olmayan soruda önceki soru yine verilir (zenginleştirme motorda)",
      k.get("onceki_soru", "").startswith("Zalva'ya kaçta"), k.get("onceki_soru"))
chatbot._cevap_uret = _asil

# =============================================================================
# G — geçmiş hazırlığı, cache, kriz, zenginleştirme, log
# =============================================================================
simdi = datetime(2026, 10, 11, 0, 0, tzinfo=timezone.utc)
ogeler = [ChatMessageItem.model_validate(x) for x in (
    {"role": "user", "content": "eski", "created_at": "2026-10-10T10:00:00Z"},
    {"role": "assistant", "content": "eski cevap", "createdAt": "2026-10-10T10:00:01Z"},
    {"role": "user", "content": "yeni", "createdAt": "2026-10-10T20:00:00Z"},
    {"role": "assistant", "content": "zamansız"},
)]
g = chat_router.gecmis_hazirla(ogeler, "Ada", "Zalva", simdi=simdi)
check("G) 12 saatten eski mesajlar düşer, zamansız kalır, createdAt okunur",
      [x["content"] for x in g] == ["yeni", "zamansız"], g)
check("G) Motor geçmişi: baştaki assistant ve sondaki user atılır, ardışık birleşir",
      chatbot._gecmis_mesajlari([
          {"role": "assistant", "content": "x"}, {"role": "user", "content": "a"},
          {"role": "user", "content": "b"}, {"role": "assistant", "content": "c"},
          {"role": "system", "content": "s"}, {"role": "user", "content": "d"}])
      == [{"role": "user", "content": "a\n\nb"}, {"role": "assistant", "content": "c"}])

check("G) Kısa takip zenginleşir", chatbot.retrieval_sorgusu(
    "Eğitimin 2. günü", "Şimdi uyudu tekrar uyanırsa") ==
    "Şimdi uyudu tekrar uyanırsa Eğitimin 2. günü")
check("G) Uzun soru zenginleşmez", chatbot.retrieval_sorgusu(
    "Altı dakika bekledim girdim temas etmedim uyudu ama yine uyandı", "önceki") ==
    "Altı dakika bekledim girdim temas etmedim uyudu ama yine uyandı")
check("G) Önceki soru yoksa sorgu aynı", chatbot.retrieval_sorgusu("kısa soru") == "kısa soru")

# Cache: bebeksiz istek normalde cache'e yazar; geçmişli istek ne okur ne yazar.
_okuma, _yazma = [], []
_lk, _st = chatbot._cache_lookup_entry, chatbot._cache_store
chatbot._cache_lookup_entry = lambda *a, **k: (_okuma.append(1), _lk(*a, **k))[1]
chatbot._cache_store = lambda *a, **k: (_yazma.append(1), _st(*a, **k))[1]
chatbot._cevap_uret("Gece kaç kez uyanması normal?", "12_ay",
                    gecmis=[{"role": "user", "content": "a"},
                            {"role": "assistant", "content": "b"}])
check("G) Geçmişli istek cache'i ne okur ne yazar", not _okuma and not _yazma,
      (len(_okuma), len(_yazma)))
chatbot._cevap_uret("Gece kaç kez uyanması normal?", "12_ay")
check("G) Geçmişsiz bebeksiz istek cache'i eskisi gibi kullanır", _okuma and _yazma)
chatbot._cache_lookup_entry, chatbot._cache_store = _lk, _st

# Motivasyon tekrarı: geçmişte teselli verildiyse kural eklenir, birimler başa konmaz
YUKLER.clear()
chatbot._cevap_uret("Bebeğimin ağlamasından korkuyorum, zarar görür mü", None,
                    gecmis=[{"role": "user", "content": "ağlıyor"},
                            {"role": "assistant", "content": "Ağlama her gün azalır, geçecek."}])
_p1 = YUKLER[-1]["messages"][-1]["content"] if YUKLER else ""
check("G) Geçmişte teselli varsa: tekrar kuralı var", "ZATEN VERİLDİ" in _p1, _p1[-300:])
YUKLER.clear()
chatbot._cevap_uret("Bebeğimin ağlamasından korkuyorum, zarar görür mü", None,
                    gecmis=[{"role": "user", "content": "ağlıyor"},
                            {"role": "assistant", "content": "5 dakika bekleyin."}])
_p = YUKLER[-1]["messages"][-1]["content"] if YUKLER else ""
check("G) Geçmişte teselli yoksa: kural yok", "ZATEN VERİLDİ" not in _p, "")
_say = lambda t: t.count("- Aglama ve motivasyon —")
check("G) Teselli verildiyse motivasyon birimleri bağlamın başına EKLENMEZ",
      _say(_p1) < _say(_p), (_say(_p1), _say(_p)))

kriz = "Artık dayanamıyorum, kendime zarar vereceğim"
r1 = chatbot._cevap_uret("Bugün nasıl yatırayım?", None,
                         gecmis=[{"role": "user", "content": kriz},
                                 {"role": "assistant", "content": "x"}])
r2 = chatbot._cevap_uret(kriz, None, gecmis=[{"role": "user", "content": "a"},
                                             {"role": "assistant", "content": "b"}])
check("G) Kriz o anki soruya bakar (geçmişteki kriz ifadesi tetiklemez)",
      r1.get("retrieval_layer") != "ruhsal_kriz" and r2.get("retrieval_layer") == "ruhsal_kriz",
      (r1.get("retrieval_layer"), r2.get("retrieval_layer")))

# Log: gecmis=N (içeriksiz)
_kayit: list[str] = []


class _Yakala(logging.Handler):
    def emit(self, rec):
        _kayit.append(rec.getMessage())


_h = _Yakala()
logging.getLogger("tavsan.chat").addHandler(_h)
client.post("/api/v1/chat", headers=H, json={
    "message": "Ada'yı tekrar uyandı ne yapayım", "baby_id": BID, "history": GECMIS})
logging.getLogger("tavsan.chat").removeHandler(_h)
_s = [m for m in _kayit if m.startswith("chat: ")]
check("G) Log satırında gecmis=4, içerik yok",
      _s and "gecmis=4" in _s[-1] and "Ada" not in _s[-1] and "mama" not in _s[-1],
      _s[-1:] if _s else None)

# =============================================================================
# Z — zaman satırı (TR saati dondurulur)
# =============================================================================
G0 = date(2026, 10, 11)


def satir(bid, an: datetime) -> str:
    db = SessionLocal()
    try:
        b = db.get(Baby, __import__("uuid").UUID(bid))
        with saat_sabitle(an):
            return sor_baglam.zaman_satirlari(db, b) or ""
    finally:
        db.close()


bebek_guncelle(BID, training_started_at=G0 - timedelta(days=2))   # 10-09 → bugün Gün 3
s = satir(BID, datetime(2026, 10, 11, 2, 30, tzinfo=TR))
check("Z) 02:30 → 'Eğitimin 2. gününün gecesi; sabah 3. gün başlıyor'",
      "Eğitimin 2. gününün gecesi; sabah 3. gün başlıyor" in s, s)
check("Z) 02:30 → uygulamanın gösterdiği 'Gün 3' ile çelişmez",
      "'Gün 3'" in s and "(gece)" in s and "11 Ekim 2026 Pazar, saat 02:30" in s, s)
check("Z) 02:30 → plan adımı Gün 1–3 Beşik yanı + gece uyanma protokolü",
      "Plandaki adım (Gün 1–3" in s and "Beşik yanı" in s and "gece uyanmaları (plan)" in s
      and "5 dk" in s, s)
s = satir(BID, datetime(2026, 10, 11, 5, 45, tzinfo=TR))
check("Z) 05:45 (uyanış kaydı yok, plan uyanışı ≥06:00) → hâlâ 2. günün gecesi",
      "2. gününün gecesi" in s, s)
s = satir(BID, datetime(2026, 10, 11, 14, 0, tzinfo=TR))
check("Z) 14:00 → 'Eğitimin 3. günü (gündüz)'", "Eğitimin 3. günü (gündüz)" in s
      and "gece uyanmaları" not in s, s)
check("Z) Bugün tarihli plan yoksa saatler en güncel plandan",
      ("Planın saatleri (en güncel plan):" in s and "uyanış" in s) == (G0 != BUGUN), s)
s = satir(BID, datetime(2026, 10, 11, 21, 15, tzinfo=TR))
check("Z) 21:15 → 3. günün gecesi (akşam)", "Eğitimin 3. günü, akşam/gece" in s
      and "(gece)" in s, s)

bebek_guncelle(BID, training_started_at=G0)
s = satir(BID, datetime(2026, 10, 11, 2, 30, tzinfo=TR))
check("Z) Başlangıç bugün, 02:30 → 'eğitim bu sabah başlıyor'", "bu sabah başlıyor" in s, s)

bebek_guncelle(BID, training_started_at=G0 - timedelta(days=13))   # bugün Gün 14
s = satir(BID, datetime(2026, 10, 11, 14, 0, tzinfo=TR))
check("Z) Gün 14 → '13 günlük program tamamlandı'", "13 günlük program tamamlandı" in s, s)
s = satir(BID, datetime(2026, 10, 11, 2, 30, tzinfo=TR))
check("Z) Gün 14 gecesi 02:30 (13. günün gecesi) → tamamlandı DEĞİL",
      "13. gününün gecesi" in s, s)
bebek_guncelle(BID, training_started_at=G0 - timedelta(days=2),
               training_completed_at=G0)
check("Z) training_completed_at → tamamlandı",
      "program tamamlandı" in satir(BID, datetime(2026, 10, 11, 14, 0, tzinfo=TR)))
bebek_guncelle(BID, training_started_at=None, training_completed_at=None)
s = satir(BID, datetime(2026, 10, 11, 14, 0, tzinfo=TR))
check("Z) Başlangıç yok → 'Eğitim henüz başlamadı (başlangıç: belirlenmedi)'",
      "Eğitim henüz başlamadı (başlangıç: belirlenmedi)" in s, s)

_, BID_BEK = hesap_bebek("Can", 125)                  # ~4 ay → bekleme planı
s = satir(BID_BEK, datetime(2026, 10, 11, 14, 0, tzinfo=TR))
check("Z) Bekleme planı → 'Eğitim henüz başlamadı (başlangıç: YYYY-AA-GG)'",
      re.search(r"Eğitim henüz başlamadı \(başlangıç: \d{4}-\d{2}-\d{2}\)", s), s)
_, BID_YD = hesap_bebek("Umut", 15)                   # 2 hafta → yenidoğan
s = satir(BID_YD, datetime(2026, 10, 11, 14, 0, tzinfo=TR))
check("Z) Yenidoğan → 'Yenidoğan dönemi'", "Yenidoğan dönemi" in s, s)
_, BID_PLANSIZ = hesap_bebek("Melis", 460, plan=False)
s = satir(BID_PLANSIZ, datetime(2026, 10, 11, 14, 0, tzinfo=TR))
check("Z) Plansız bebek → yine kurulur, başlamadı", "Şu an:" in s and "başlamadı" in s, s)

# Satır kurulamazsa soru yine cevaplanır
_gercek_zs = sor_baglam.zaman_satirlari
sor_baglam.zaman_satirlari = lambda *a, **k: (_ for _ in ()).throw(TypeError("x"))
bayrak(True, False)
r = client.post("/api/v1/chat", headers=H, json={"message": "Neden uyumuyor?", "baby_id": BID})
check("Z) Zaman satırı patlasa da 200", r.status_code == 200, r.status_code)
sor_baglam.zaman_satirlari = _gercek_zs
bayrak(False, False)

# =============================================================================
print("\n" + "=" * 70)
gecen = sum(1 for _, ok, _ in results if ok)
for ad, ok, detay in results:
    print(f"[{'OK ' if ok else 'XX '}] {ad}" + ("" if ok else f"\n        → {detay[:400]}"))
print("=" * 70)
print(f"SONUÇ: {gecen}/{len(results)}")
sys.exit(0 if gecen == len(results) else 1)
