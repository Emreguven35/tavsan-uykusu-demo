"""
Sor modeli ortam ayarları + kapanış/fiyat mesajları (2026-10-12).

LLM YOK: Anthropic istemcisi sahte, modele giden yükü yakalar.

Kapsam:
  M1  Değişkenler TANIMSIZ: yük birebir eskisi gibi — yalnız model / max_tokens /
      system / messages; claude-haiku-4-5, 1024; temperature ve thinking YOK
  M2  CHATBOT_MODEL / SOR_MAX_TOKENS / SOR_TEMPERATURE uygulanıyor
  M3  temperature desteklemeyen modelde (Sonnet 5.5) gönderilmiyor
  M4  SOR_DUSUNME=kapali: Sonnet 5.5 → thinking between_tools; varsayılanı kapalı
      modelde (Haiku, Sonnet 4.6) ve kapatılamayan modelde (Opus 5.5) gönderilmiyor
  M5  Cevap metni doğru bloktan (önce thinking bloğu gelse de); metin yoksa
      sabit mesaj ve cache'e yazılmaz; sonuçtaki model etkin model; /health aynı
  K1  Kapanış mesajı (teşekkür/sağol/eyvallah) — bayrak KAPALI: eski akış (LLM);
      AÇIK: sabit cevap, LLM / RAG / bebek bağlamı YOK
  K2  Yalnız emoji — aynı
  K3  "Teşekkürler ama…" gibi içerikli mesaj kapanış SAYILMAZ
  K4  Fiyat/abonelik sorusu — bayrak KAPALI: kapsam dışı sinyali yok; AÇIK: var

Çalıştırma: python tests/test_sor_ayar.py
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "sor_ayar_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["BETA_MODE"] = "true"
AYARLAR = ("CHATBOT_MODEL", "SOR_MAX_TOKENS", "SOR_TEMPERATURE", "SOR_DUSUNME",
           "SOR_GECMIS", "SOR_BAGLAM_V2")
for _a in AYARLAR:
    os.environ.pop(_a, None)

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, engine                             # noqa: E402
import api.models                                           # noqa: E402,F401
from api.main import app                                    # noqa: E402
from api.services import baby_context                       # noqa: E402
from engine import chatbot                                  # noqa: E402
from tests.llm_muhuru import TAM_PROFIL, muhurle            # noqa: E402
muhurle()
for _a in AYARLAR:                                          # .env'den gelmiş olabilir
    os.environ.pop(_a, None)

os.environ["ANTHROPIC_API_KEY"] = "test-dummy"
chatbot.HAS_ANTHROPIC = True
chatbot.CACHE_PATH = Path(tempfile.gettempdir()) / "sor_ayar_cache.json"
if chatbot.CACHE_PATH.exists():
    chatbot.CACHE_PATH.unlink()
chatbot._cache_state.update(loaded=False, entries=[], emb_matrix=None, emb_idx=[])

YUKLER: list[dict] = []
YANIT_BLOKLARI: list = []          # boşsa varsayılan tek metin bloğu


class _SahteIstemci:
    def __init__(self, *a, **k):
        self.messages = self

    def create(self, **kw):
        YUKLER.append(kw)
        bloklar = list(YANIT_BLOKLARI) or [SimpleNamespace(type="text", text="Sahte cevap.")]
        return SimpleNamespace(
            content=bloklar, stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0))


chatbot.Anthropic = _SahteIstemci
Base.metadata.create_all(bind=engine)
client = TestClient(app)
results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), str(detail)))


def ortam(**kv):
    for a in AYARLAR:
        os.environ.pop(a, None)
    for a, v in kv.items():
        os.environ[a] = v


def yuk(soru: str = "Gece kaç kez uyanması normal?") -> dict:
    """Cache'e takılmasın diye her çağrıda farklı soru — yalnız yük önemli."""
    YUKLER.clear()
    chatbot._cevap_uret(soru, "12_ay", baby_context="BEBEK: test")
    return YUKLER[-1] if YUKLER else {}


# =============================================================================
# M — model ayarları
# =============================================================================
ortam()
y = yuk()
check("M1) Tanımsız: yük anahtarları yalnız model/max_tokens/system/messages",
      set(y) == {"model", "max_tokens", "system", "messages"}, sorted(y))
check("M1) Tanımsız: claude-haiku-4-5, 1024", y.get("model") == "claude-haiku-4-5"
      and y.get("max_tokens") == 1024, (y.get("model"), y.get("max_tokens")))
check("M1) Tanımsız: değerler eski sabitlerle aynı (CHATBOT_MODEL, MAX_TOKENS)",
      y.get("model") == chatbot.CHATBOT_MODEL and y.get("max_tokens") == chatbot.MAX_TOKENS, "")
ortam(SOR_DUSUNME="kapali")
y = yuk()
check("M1) Haiku + SOR_DUSUNME=kapali: thinking gönderilmez (varsayılanı kapalı)",
      "thinking" not in y and set(y) == {"model", "max_tokens", "system", "messages"}, sorted(y))

ortam(CHATBOT_MODEL="claude-sonnet-4-6", SOR_MAX_TOKENS="600", SOR_TEMPERATURE="0.3",
      SOR_DUSUNME="kapali")
y = yuk()
check("M2) Sonnet 4.6: model, max_tokens 600, temperature 0.3; thinking yok",
      y.get("model") == "claude-sonnet-4-6" and y.get("max_tokens") == 600
      and y.get("temperature") == 0.3 and "thinking" not in y, {k: y.get(k) for k in y if k != "messages" and k != "system"})
ortam(SOR_TEMPERATURE="0.2")
y = yuk()
check("M2) Haiku + SOR_TEMPERATURE=0.2 → temperature gönderilir",
      y.get("model") == "claude-haiku-4-5" and y.get("temperature") == 0.2, "")
ortam(SOR_TEMPERATURE="sicak")
check("M2) Geçersiz temperature yok sayılır", "temperature" not in yuk(), "")

ortam(CHATBOT_MODEL="claude-sonnet-5-5", SOR_TEMPERATURE="0.3", SOR_DUSUNME="kapali")
y = yuk()
check("M3) Sonnet 5.5: temperature GÖNDERİLMEZ", "temperature" not in y, sorted(y))
check("M4) Sonnet 5.5 + kapali: thinking between_tools (başka alan yok)",
      y.get("thinking") == {"type": "between_tools"}, y.get("thinking"))
ortam(CHATBOT_MODEL="claude-sonnet-5-5")
check("M4) Sonnet 5.5, SOR_DUSUNME yok: thinking gönderilmez (varsayılan adaptive)",
      "thinking" not in yuk(), "")
ortam(CHATBOT_MODEL="claude-opus-5-5", SOR_DUSUNME="kapali")
check("M4) Opus 5.5 (kapatılamaz): thinking gönderilmez", "thinking" not in yuk(), "")

ortam(CHATBOT_MODEL="claude-sonnet-5-5", SOR_DUSUNME="kapali")
YANIT_BLOKLARI[:] = [SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text="Doğru blok.")]
r = chatbot._cevap_uret("Uyku penceresi kaç dakika?", "12_ay", baby_context="BEBEK: t")
check("M5) Önce thinking bloğu gelse de cevap metin bloğundan",
      r["cevap"] == "Doğru blok." and r["model"] == "claude-sonnet-5-5", r.get("cevap"))
YANIT_BLOKLARI[:] = [SimpleNamespace(type="thinking", thinking="")]
_yaz = []
_st = chatbot._cache_store
chatbot._cache_store = lambda *a, **k: (_yaz.append(1), _st(*a, **k))[1]
r = chatbot._cevap_uret("Kaç saat gece uykusu gerekir acaba?", "12_ay")
check("M5) Metin bloğu yoksa sabit mesaj, cache'e YAZILMAZ",
      r["cevap"] == chatbot.BOS_CEVAP_MESAJI and not _yaz, (r.get("cevap"), _yaz))
chatbot._cache_store = _st
YANIT_BLOKLARI.clear()
check("M5) /health model alanı etkin model",
      client.get("/health").json().get("model") == "claude-sonnet-5-5", "")
ortam()
check("M5) /health tanımsızken claude-haiku-4-5",
      client.get("/health").json().get("model") == "claude-haiku-4-5", "")

# =============================================================================
# K — kapanış ve fiyat mesajları (SOR_GECMIS)
# =============================================================================
tok = client.post("/api/v1/auth/register", json={
    "email": "sorayar@ornek-test.com", "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
BID = client.post("/api/v1/babies", headers=H, json={
    "name": "Ada", "birth_date": "2025-09-01", "night_wakes": 2, **TAM_PROFIL}).json()["id"]

SAYAC = {"retrieve": 0, "baglam": 0}
_ret, _bg = chatbot.retrieve, baby_context.build_baby_context
chatbot.retrieve = lambda *a, **k: (SAYAC.__setitem__("retrieve", SAYAC["retrieve"] + 1), _ret(*a, **k))[1]
baby_context.build_baby_context = lambda *a, **k: (SAYAC.__setitem__("baglam", SAYAC["baglam"] + 1), _bg(*a, **k))[1]


def sor(mesaj: str, gecmis_bayragi: bool) -> tuple[dict, int, dict]:
    ortam(**({"SOR_GECMIS": "1"} if gecmis_bayragi else {}))
    YUKLER.clear()
    SAYAC.update(retrieve=0, baglam=0)
    r = client.post("/api/v1/chat", headers=H, json={"message": mesaj, "baby_id": BID})
    assert r.status_code == 200, r.text[:200]
    return r.json(), len(YUKLER), dict(SAYAC)


for etiket, mesaj in (("K1", "Çok teşekkürler 🙏"), ("K1", "sağol"), ("K1", "eyvallah"),
                      ("K2", "🙏❤️")):
    j0, llm0, s0 = sor(mesaj, False)
    j1, llm1, s1 = sor(mesaj, True)
    # Eski akış: RAG çalışır; bugün çoğu "kapsam dışı" (k4) cevabına düşüyor.
    check(f"{etiket}) '{mesaj}' — bayrak KAPALI: eski akış (RAG çalıştı, sabit kapanış yok)",
          s0["retrieve"] > 0 and j0["answer"] != chatbot.KAPANIS_CEVABI,
          (llm0, s0, j0["retrieval_layer"], j0["answer"][:60]))
    check(f"{etiket}) '{mesaj}' — bayrak AÇIK: sabit cevap; LLM, RAG, bağlam yok",
          j1["answer"] == chatbot.KAPANIS_CEVABI and llm1 == 0
          and s1 == {"retrieve": 0, "baglam": 0} and j1["retrieval_layer"] == "kapanis",
          (j1["answer"][:60], llm1, s1))

j, llm, s = sor("Teşekkürler ama hâlâ uyumuyor, ne yapayım?", True)
check("K3) İçerikli teşekkür mesajı kapanış SAYILMAZ (LLM'e gider)",
      j["answer"] != chatbot.KAPANIS_CEVABI and llm == 1 and s["retrieve"] > 0, (llm, s))

_sinyal: list = []
_ks = chatbot._kapsam_disi_sinyali
chatbot._kapsam_disi_sinyali = lambda soru, fiyat=False: (_sinyal.append(fiyat), _ks(soru, fiyat=fiyat))[1]
for mesaj in ("Uygulama kaç para?", "Abonelik ücreti ne kadar?", "Premium fiyatı nedir"):
    _sinyal.clear()
    j0, llm0, _ = sor(mesaj, False)
    s_kapali = list(_sinyal)
    _sinyal.clear()
    j1, llm1, _ = sor(mesaj, True)
    check(f"K4) '{mesaj}' — KAPALI: fiyat sinyali yok; AÇIK: kapsam dışı (k4, LLM yok)",
          s_kapali == [False] and _sinyal == [True]
          and j1["retrieval_layer"] == "k4" and j1["answer"] == chatbot.KAPSAM_DISI_MESAJ
          and llm1 == 0,
          (s_kapali, _sinyal, j0["retrieval_layer"], j1["retrieval_layer"], llm1))
chatbot._kapsam_disi_sinyali = _ks
check("K4) Birim: fiyat sözcükleri yalnız fiyat=True iken sinyal",
      not _ks("kaç para") and _ks("kaç para", fiyat=True)
      and not _ks("Bebeğim uyumuyor", fiyat=True), "")
chatbot.retrieve, baby_context.build_baby_context = _ret, _bg
ortam()

# =============================================================================
print("\n" + "=" * 74)
print("SOR — model ayarları, kapanış ve fiyat mesajları")
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
