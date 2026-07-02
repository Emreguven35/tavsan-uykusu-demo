"""
API testleri (FastAPI TestClient). LLM ve TTS MOCK'lanır → deterministik, ücretsiz.
Gerçek ElevenLabs bir kez ayrı canlı test edilir (bkz. rapor / test_tts_canli.py).

Senaryolar:
  1. GET  /health          → 200, retrieval + model alanları
  2. POST /ask geçerli soru → cevap + kaynaklar geliyor
  3. Aynı soru 2. kez       → cache_hit=true VE ses TTS'siz (dosyadan) geliyor
  4. Farklı yas_bandi       → cache_hit=false
  5. ELEVENLABS anahtarı yok → cevap gelir, ses_url=null (graceful)
  6. GET /audio/<hash>.mp3  → 200 audio/mpeg (üretilen dosya servis ediliyor)
  7. /audio path-traversal  → 400
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ["ANTHROPIC_API_KEY"] = "test-dummy"      # mock LLM için
os.environ["ELEVENLABS_API_KEY"] = "test-eleven"    # mock TTS için
os.environ["ELEVENLABS_VOICE_ID"] = "test-voice"

from fastapi.testclient import TestClient  # noqa: E402
from engine import chatbot                 # noqa: E402
from api import tts                         # noqa: E402
from api.main import app                    # noqa: E402

# --- MOCK: LLM (say + benzersiz metin) --------------------------------------
_LLM = {"n": 0}


class _Msgs:
    def create(self, **kw):
        _LLM["n"] += 1
        return type("R", (), {"content": [type("B", (), {"text": f"CANNED#{_LLM['n']}"})()]})()


class FakeAnthropic:
    def __init__(self, *a, **k):
        self.messages = _Msgs()


# --- MOCK: TTS (env'e saygılı; çağrı say) -----------------------------------
_TTS = {"n": 0}


def fake_synth(text):
    if not (os.getenv("ELEVENLABS_API_KEY") and os.getenv("ELEVENLABS_VOICE_ID")):
        return None                          # anahtar yok → graceful None
    _TTS["n"] += 1
    return b"ID3" + b"\x00" * 200            # sahte mp3 baytları


def _setup():
    chatbot.init_index()
    chatbot.Anthropic = FakeAnthropic
    chatbot.HAS_ANTHROPIC = True
    # temiz cevap cache (temp)
    cp = Path(tempfile.gettempdir()) / "api_ans_cache.json"
    if cp.exists():
        cp.unlink()
    chatbot.CACHE_PATH = cp
    chatbot._cache_state.update(
        {"loaded": True, "entries": [], "emb_matrix": None, "emb_idx": []})
    # temiz ses cache (temp)
    ad = Path(tempfile.mkdtemp(prefix="api_audio_"))
    tts.AUDIO_DIR = ad
    tts.synthesize = fake_synth


def main():
    _setup()
    client = TestClient(app)
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    # 1) /health
    h = client.get("/health")
    check("1) /health 200 + alanlar",
          h.status_code == 200 and h.json().get("status") == "ok"
          and "retrieval" in h.json() and h.json().get("model") == chatbot.CHATBOT_MODEL,
          str(h.json()))

    # 2) /ask geçerli
    r1 = client.post("/ask", json={"soru": "Beyaz gürültü zararlı mı?", "yas_bandi": "8_ay"})
    j1 = r1.json()
    check("2) /ask cevap + kaynaklar",
          r1.status_code == 200 and j1["cevap"] and isinstance(j1["kaynaklar"], list)
          and len(j1["kaynaklar"]) > 0 and j1["cache_hit"] is False,
          f"cache_hit={j1['cache_hit']} kaynak={len(j1['kaynaklar'])} ses={j1['ses_url']}")
    tts_after_first = _TTS["n"]

    # 3) Aynı soru 2. kez → cache_hit=true, TTS çağrısı ARTMAZ (dosyadan)
    r2 = client.post("/ask", json={"soru": "Beyaz gürültü zararlı mı?", "yas_bandi": "8_ay"})
    j2 = r2.json()
    check("3) 2. kez cache_hit=true + ses TTS'siz",
          j2["cache_hit"] is True and j2["ses_url"] == j1["ses_url"]
          and _TTS["n"] == tts_after_first and j2["maliyet"]["tts_usd"] == 0.0
          and j2["maliyet"]["llm_usd"] == 0.0,
          f"cache_hit={j2['cache_hit']} tts_calls={_TTS['n']}(oncesi {tts_after_first}) "
          f"maliyet={j2['maliyet']}")

    # 4) Farklı yas_bandi → cache_hit=false
    r3 = client.post("/ask", json={"soru": "Beyaz gürültü zararlı mı?", "yas_bandi": "11_ay"})
    j3 = r3.json()
    check("4) Farklı yas_bandi → cache_hit=false",
          j3["cache_hit"] is False, f"cache_hit={j3['cache_hit']}")

    # 6) /audio servis (üretilen dosya)
    if j1["ses_url"]:
        au = client.get(j1["ses_url"])
        check("6) /audio 200 audio/mpeg",
              au.status_code == 200 and au.headers.get("content-type", "").startswith("audio/mpeg"),
              f"status={au.status_code} ctype={au.headers.get('content-type')}")
    else:
        check("6) /audio 200 audio/mpeg", False, "ses_url yok (mock TTS None döndü?)")

    # 7) path-traversal engeli
    bad = client.get("/audio/..%2f..%2f.env")
    check("7) /audio traversal → 400/404", bad.status_code in (400, 404), f"status={bad.status_code}")

    # 5) ELEVENLABS anahtarı yok → cevap gelir, ses_url=null (graceful)
    old_key = os.environ.pop("ELEVENLABS_API_KEY", None)
    r5 = client.post("/ask", json={"soru": "Emzik kullanmalı mıyım?", "yas_bandi": "8_ay"})
    j5 = r5.json()
    check("5) TTS anahtarı yok → cevap var, ses_url=null",
          r5.status_code == 200 and j5["cevap"] and j5["ses_url"] is None,
          f"ses_url={j5['ses_url']} cevap_var={bool(j5['cevap'])}")
    if old_key is not None:
        os.environ["ELEVENLABS_API_KEY"] = old_key

    # --- özet ---
    print("\n" + "=" * 74)
    print("API TEST SONUÇLARI")
    print("=" * 74)
    passed = 0
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(f"[{mark}] {name}\n       {detail}")
    print("-" * 74)
    print(f"TOPLAM: {passed}/{len(results)} gecti")
    print("=" * 74)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
