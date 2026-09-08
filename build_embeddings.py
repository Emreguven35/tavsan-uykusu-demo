"""
Embedding index üretici (offline build script) + doc2query genişletme.

İki aşama:
  A) DOC2QUERY (bir kez, LLM ile): Curated kuralların terse metinleri, "18 aylık
     bebeği gece uykusundan kaç saat önce emzirmeliyim" gibi compound sorularda
     yaş-chunk'larının altında kalıyor. Her curated kuralın yanıtladığı doğal
     ebeveyn sorularını (eşanlamlılarıyla) LLM ile üretip data/kb_expansions.json'a
     yazıyoruz. Bu RUNTIME synonym yaması DEĞİL — bir kez üretilip cache'lenir/commit'lenir.
  B) EMBED: Birleşik korpusu (genişletilmiş embed_text ile) embed edip diske yaz:
       data/embeddings.npy   — (N, dim) float32, normalize edilmiş
       data/corpus_meta.json — model, boyut, birim sayısı ve birimler

Kullanım:
    python build_embeddings.py                 # cache varsa doc2query atlanır, sadece embed
    python build_embeddings.py --regen-expansions   # doc2query'yi yeniden üret (LLM gerekir)

Doc2query yalnızca ANTHROPIC_API_KEY varsa çalışır; yoksa mevcut cache kullanılır
(yoksa genişletmesiz embed edilir — sistem yine çalışır, sadece compound sorularda
biraz daha zayıf). Query-time embedding tamamen LOCAL ve ücretsizdir.
"""
import os
import sys
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
load_dotenv()

from engine import chatbot  # noqa: E402

DOC2QUERY_SYSTEM = (
    "Verilen bilgi parçasının DOĞRUDAN yanıtladığı, bir annenin günlük/farklı "
    "kelimelerle sorabileceği 5 kısa Türkçe soru üret. Eşanlamlıları çeşitlendir "
    "(örn. emzirme/mama/süt/beslenme; uyutma/uyku/yatırma; oda/ortam/ışık). "
    "Bilgi parçasında yaş geçiyorsa bazı sorulara yaşı da kat. Yalnızca soruları, "
    "her biri ayrı satırda, numarasız ve açıklamasız yaz."
)


def _gen_one(client, unit: dict) -> tuple[str, str]:
    """Tek birim için doc2query soruları üret."""
    resp = client.messages.create(
        model=chatbot.MODEL_NAME,
        max_tokens=256,
        system=DOC2QUERY_SYSTEM,
        messages=[{"role": "user", "content": unit["text"][:1200]}],
    )
    text = resp.content[0].text
    qs = [ln.strip(" -•\t") for ln in text.splitlines() if ln.strip()]
    return unit["chunk_id"], " | ".join(qs)


def generate_expansions(regen: bool = False) -> dict:
    """Curated kurallar için doc2query genişletmeleri üret (cache'li)."""
    cache = {}
    if chatbot.EXPANSIONS_PATH.exists() and not regen:
        cache = json.loads(chatbot.EXPANSIONS_PATH.read_text(encoding="utf-8"))

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY yok — doc2query atlanıyor "
              f"(mevcut cache: {len(cache)} birim).")
        return cache

    from anthropic import Anthropic
    client = Anthropic()

    units = chatbot.build_corpus()
    targets = [u for u in units if chatbot._is_expandable(u)
               and (regen or u["chunk_id"] not in cache)]
    if not targets:
        print(f"Doc2query cache güncel ({len(cache)} birim) — üretim atlanıyor.")
        return cache

    print(f"Doc2query: {len(targets)} curated birim için soru üretiliyor (LLM)...")
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_gen_one, client, u): u for u in targets}
        for fut in as_completed(futs):
            try:
                cid, qs = fut.result()
                cache[cid] = qs
            except Exception as e:
                logging.warning("Doc2query hata (%s): %s", futs[fut]["chunk_id"], e)
            done += 1
            if done % 20 == 0:
                print(f"  {done}/{len(targets)}")

    chatbot.EXPANSIONS_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ Doc2query yazıldı: {chatbot.EXPANSIONS_PATH} ({len(cache)} birim)")
    return cache


def main() -> int:
    regen = "--regen-expansions" in sys.argv
    print(f"Embedding modeli: {chatbot.EMB_MODEL_NAME}")

    # A) doc2query genişletmeleri (cache'li)
    generate_expansions(regen=regen)

    # B) korpusu embed et
    stats = chatbot.corpus_stats()
    print(f"Korpus: {stats}")
    print("Embed ediliyor (ilk çalıştırmada model indirilir ~470MB)...")
    result = chatbot.build_index_to_disk(verbose=True)
    print(f"\n✅ Index yazıldı:")
    print(f"   - {chatbot.EMB_PATH}  shape={result['shape']}")
    print(f"   - {chatbot.META_PATH}")
    print(f"   Birim dağılımı: toplam={result['toplam']} "
          f"(chunk={result.get('chunk',0)}, "
          f"global_rule={result.get('global_rule',0)}, "
          f"yas_bucket={result.get('yas_bucket',0)}, "
          f"yas_bandi={result.get('yas_bandi',0)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
