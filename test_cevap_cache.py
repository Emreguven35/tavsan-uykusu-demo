"""
Cevap cache regresyon testi (Task B).

LLM çağrısı MOCK'lanır (API'ye gidilmez, deterministik + ücretsiz) ve gerçek
API çağrısı sayısı sayılır. Semantik katman için GERÇEK retrieval embedding
modeli kullanılır (init_index ile yüklenir — local, ücretsiz).

Doğrulanan senaryolar:
  1. Aynı soru + aynı yaş bandı 2 kez  → 2. cevap cache'ten (API çağrısı artmaz).
  2. Hafif farklı ifade + aynı bant     → semantik cache (cosine >= 0.95 ise).
  3. FARKLI yaş bandı + aynı soru        → cache MISS, yeni cevap üretilir.
  4. Yaş bandı YOK + aynı soru 2 kez     → exact cache çalışır.
  5. Yaş bandı YOK + farklı ifade        → semantik ATLANIR, cache MISS.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")

from engine import chatbot  # noqa: E402

# --- LLM MOCK: her create çağrısını say, benzersiz canned metin döndür --------
_CALLS = {"n": 0}


class _FakeMessages:
    def create(self, **kw):
        _CALLS["n"] += 1
        text = f"CANNED_ANSWER#{_CALLS['n']}"
        return type("R", (), {"content": [type("B", (), {"text": text})()]})()


class FakeAnthropic:
    def __init__(self, *a, **k):
        self.messages = _FakeMessages()


def _sim(a: str, b: str) -> float:
    """İki sorunun cosine benzerliği (rapor için)."""
    import numpy as np
    va = chatbot._embed_query(a)
    vb = chatbot._embed_query(b)
    return float(np.dot(va, vb))


def main() -> int:
    print("Retrieval index yükleniyor (embedding modeli)...")
    chatbot.init_index()
    print("Aktif retrieval:", chatbot.active_retrieval())

    # Temiz cache: temp dosya + state sıfırla
    tmp = Path(tempfile.gettempdir()) / "answer_cache_test.json"
    if tmp.exists():
        tmp.unlink()
    chatbot.CACHE_PATH = tmp
    chatbot._cache_state.update(
        {"loaded": False, "entries": [], "emb_matrix": None, "emb_idx": []})

    # LLM'i mock'la
    chatbot.Anthropic = FakeAnthropic
    chatbot.HAS_ANTHROPIC = True

    results = []

    def calls():
        return _CALLS["n"]

    # --- Senaryo 1: aynı soru + aynı bant, 2 kez -----------------------------
    q1 = "Beyaz gürültü bebeğe zararlı mı?"
    before = calls()
    a1 = chatbot.cevapla(q1, yas_bandi="8_ay")
    a2 = chatbot.cevapla(q1, yas_bandi="8_ay")
    api_used = calls() - before
    ok1 = (api_used == 1 and a1 == a2)
    results.append(("1) Aynı soru+bant 2x → 2. cache'ten",
                    f"API çağrısı={api_used} (beklenen 1), a1==a2={a1==a2}", ok1))

    # --- Senaryo 2: hafif farklı ifade + aynı bant (semantik) ----------------
    q1b = "Beyaz gürültü bebek için zarar verir mi?"
    cos = _sim(q1, q1b)
    before = calls()
    a3 = chatbot.cevapla(q1b, yas_bandi="8_ay")
    api_used = calls() - before
    hit = (api_used == 0)
    ok2 = (hit == (cos >= chatbot.SEM_CACHE_THRESHOLD))  # eşik davranışı tutarlı
    results.append(("2) Farklı ifade+aynı bant → semantik",
                    f"cosine={cos:.3f} (eşik {chatbot.SEM_CACHE_THRESHOLD}), "
                    f"cache_hit={hit}, dönen={'a1' if a3==a1 else 'YENİ'}", ok2))

    # --- Senaryo 3: FARKLI yaş bandı + aynı soru → MISS ----------------------
    before = calls()
    a4 = chatbot.cevapla(q1, yas_bandi="11_ay")
    api_used = calls() - before
    ok3 = (api_used == 1 and a4 != a1)
    results.append(("3) Farklı bant+aynı soru → MISS",
                    f"API çağrısı={api_used} (beklenen 1), yeni cevap={a4 != a1}", ok3))

    # --- Senaryo 4: bant YOK + aynı soru 2 kez → exact -----------------------
    q2 = "Gece uyanmalarında ne yapmalıyım?"
    before = calls()
    b1 = chatbot.cevapla(q2, yas_bandi=None)
    b2 = chatbot.cevapla(q2, yas_bandi=None)
    api_used = calls() - before
    ok4 = (api_used == 1 and b1 == b2)
    results.append(("4) Bant YOK+aynı soru 2x → exact",
                    f"API çağrısı={api_used} (beklenen 1), b1==b2={b1==b2}", ok4))

    # --- Senaryo 5: bant YOK + farklı ifade → semantik ATLANIR (MISS) --------
    q2b = "Bebeğim gece uyanınca nasıl davranmalıyım?"
    before = calls()
    b3 = chatbot.cevapla(q2b, yas_bandi=None)
    api_used = calls() - before
    ok5 = (api_used == 1 and b3 != b1)
    results.append(("5) Bant YOK+farklı ifade → semantik atlanır (MISS)",
                    f"API çağrısı={api_used} (beklenen 1), yeni cevap={b3 != b1}", ok5))

    # --- Özet ----------------------------------------------------------------
    print("\n" + "=" * 72)
    print("CEVAP CACHE TEST SONUÇLARI")
    print("=" * 72)
    passed = 0
    for name, detail, ok in results:
        mark = "✅ PASS" if ok else "❌ FAIL"
        if ok:
            passed += 1
        print(f"{mark}  {name}\n         {detail}")
    print("-" * 72)
    print(f"TOPLAM: {passed}/{len(results)} geçti | toplam gerçek API çağrısı: {calls()}")
    print("=" * 72)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
