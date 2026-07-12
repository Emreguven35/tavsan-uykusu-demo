"""
scripts/build_embeddings.py — embedding index üretimini Railway ortamında çalıştırmak
için ince sarmalayıcı (Faz 5: `railway run python scripts/build_embeddings.py`).

Asıl mantık kök dizindeki build_embeddings.py'dedir (doc2query cache + korpus embed →
data/embeddings.npy + data/corpus_meta.json). Bu dosya yalnızca repo kökünü sys.path'e
ekleyip onu çağırır — kod ÇİFTLENMEZ.

Kullanım:
    python scripts/build_embeddings.py                 # embed (doc2query cache'li)
    python scripts/build_embeddings.py --regen-expansions
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from build_embeddings import main  # noqa: E402 — kök script'in main()'i

if __name__ == "__main__":
    sys.exit(main())
