"""
RAG soru-cevap motoru — SEMANTİK (embedding) retrieval + Claude generation.

Birincil retrieval: sentence-transformers ile çok dilli (Türkçe destekli) local
embedding'ler üzerinden cosine similarity. Eşanlamlı/farklı ifadeyle sorulan
sorular (örn. "emzirme" ↔ "beslenme/mama/süt") artık eşleşir.

Korpus = chunks.json (506) + master_knowledge_base.json'daki global_rules ve
yaş-bazlı AÇIKLAYICI metinler (besleme merdiveni dahil). Sayısal parametre
tabloları HARİÇ.

Embedding'ler diske bir kez yazılır (data/embeddings.npy + data/corpus_meta.json);
uygulama her açılışta yeniden embed etmez. Yeniden üretmek için: build_embeddings.py.

Güvenlik: sentence-transformers/torch yüklenemez veya RAM yetmezse, otomatik
olarak eski TF-IDF retrieval'a düşülür (aynı birleşik korpus üzerinde). Demo
asla tamamen çökmez. Hangi retrieval'ın aktif olduğu loglanır.
"""
import os
import json
import re
import hashlib
import logging
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

try:
    from anthropic import Anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

from engine.config import CHATBOT_MODEL  # chatbot/RAG modeli (haiku — merkezi)
from engine.config import MODEL_NAME  # noqa: F401 — build_embeddings doc2query re-export

MAX_TOKENS = 1024
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Embedding modeli: çok dilli, Türkçe destekli, hafif (~470MB, ~118M param).
# Streamlit Cloud ücretsiz katman (~1GB RAM) için MiniLM-L12 tercih edildi;
# mpnet-base (~420M param) kalite olarak biraz daha iyi ama RAM'i zorlar.
EMB_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
EMB_PATH = DATA_DIR / "embeddings.npy"
META_PATH = DATA_DIR / "corpus_meta.json"
# Doc2query genişletmeleri (build-time, LLM ile bir kez üretilip cache'lenir).
# Curated kurallar terse olduğundan "18 aylık emzirme kaç saat önce" gibi compound
# sorularda yaş-chunk'larının altında kalıyor; bu dosya her kuralın yanıtladığı
# doğal ebeveyn sorularını embed metnine ekleyerek (yalnızca ARAMA için, gösterimde
# değil) sorgu seyrelmesini çözer. RUNTIME synonym yaması DEĞİL — bir kez üretilir.
EXPANSIONS_PATH = DATA_DIR / "kb_expansions.json"

# HİBRİT retrieval: semantik (eşanlamlı yakalar) + lexical/TF-IDF (tam terim eşleşmesi).
# Saf semantik, "kucağa alabilirsiniz → 30 saniye" gibi spesifik kural metinlerini
# genel sohbet chunk'larının altında bırakabiliyor; lexical sinyal bunları yukarı çeker.
# Saf lexical ise eşanlamlıları kaçırır. İkisinin füzyonu kalıcı çözümdür.
HYBRID_ALPHA = 0.65        # semantik ağırlığı (1-ALPHA = lexical ağırlığı)
SEM_MIN_SCORE = 0.22       # birleşik skor eşiği (testle ayarlandı; agresif değil)
SEM_TOP_K = 8              # 8: spesifik kural chunk'ı bağlama girsin (k=5 bazılarını kaçırıyor)
TFIDF_MIN_SCORE = 0.05     # saf TF-IDF fallback eşiği

logger = logging.getLogger("tavsan.chatbot")

# Lazy-loaded global cache (process boyunca tek sefer; Streamlit rerun'larında korunur)
_state: dict[str, Any] = {
    "ready": False,
    "active": None,        # "semantic" | "tfidf"
    # semantic
    "model": None,
    "embeddings": None,    # (N, dim) float32, normalize edilmiş
    "units": None,         # korpus birimleri (chunk_id, text, source, lesson_id, label)
    # tfidf fallback
    "vectorizer": None,
    "vectors": None,
}


# Türkçe için basit stopword listesi (TF-IDF fallback'inde kullanılır)
TURKCE_STOPWORDS = {
    "ve", "ile", "için", "bu", "şu", "o", "bir", "iki", "üç", "da", "de",
    "ki", "mi", "mı", "mu", "mü", "ne", "var", "yok", "olan", "ama", "ancak",
    "fakat", "ya", "veya", "hem", "hiç", "çok", "az", "daha", "en", "gibi",
    "kadar", "sonra", "önce", "sırasında", "üzerine", "altına", "yani", "şey",
}


def _normalize(text: str) -> str:
    """Türkçe karakterleri normalize et, küçük harfe çevir (TF-IDF için)."""
    text = text.lower()
    text = text.replace("i̇", "i")
    text = re.sub(r"[^\wçğıöşü\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_chunks() -> list[dict]:
    with open(DATA_DIR / "chunks.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# KORPUS KURMA — chunks + global_rules + yaş-bazlı açıklayıcı metinler
# ---------------------------------------------------------------------------
def _humanize(key: str) -> str:
    """JSON anahtarını okunabilir Türkçe etikete çevir.
    'beslenme_uyku_iliskisi (kayıt36, kayıt37)' -> 'Beslenme uyku iliskisi'
    Parantez içi kayıt/ders referansları etiketten temizlenir (modele sızmasın)."""
    key = re.sub(r"\([^)]*\)", "", key)          # parantez içini at
    key = re.sub(r"\[[^\]]*\]", "", key)          # köşeli parantez içini at
    key = key.replace("_", " ").replace(".", " ")
    key = re.sub(r"\s+", " ", key).strip()
    return key[:1].upper() + key[1:] if key else key


def _is_descriptive_text(value: Any) -> bool:
    """Açıklayıcı metin mi (embed edilmeli), yoksa sayısal/kısa parametre mi?"""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if len(v) < 20:
        return False
    # Saf saat/sayı aralığı ("19:00-20:00", "14-16 saat") gibi şeyleri ele
    if re.fullmatch(r"[\d\.\,\:\-\s]+(saat|dakika|dk|ay|hafta|öğün|kez)?", v, re.IGNORECASE):
        return False
    return True


def _flatten_texts(prefix: str, val: Any, out: list[tuple]) -> None:
    """global_rules içindeki metinsel içerikleri (str/dict/list) düz birime indir."""
    if isinstance(val, str):
        if _is_descriptive_text(val):
            out.append((prefix, val))
    elif isinstance(val, dict):
        for k, v in val.items():
            _flatten_texts(f"{prefix}.{k}", v, out)
    elif isinstance(val, list):
        for i, item in enumerate(val):
            _flatten_texts(f"{prefix}[{i}]", item, out)


def _load_expansions() -> dict:
    """Doc2query genişletmelerini yükle (chunk_id -> 'soru1 | soru2 ...'). Yoksa {}."""
    if EXPANSIONS_PATH.exists():
        try:
            return json.loads(EXPANSIONS_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("kb_expansions.json okunamadı: %s", e)
    return {}


# Doc2query genişletmesi uygulanacak birimler: curated kurallar (terse + yüksek değer).
def _is_expandable(unit: dict) -> bool:
    return (unit["source"] in ("global_rule", "yas_bucket")
            or unit["chunk_id"].startswith("kural"))


def build_corpus() -> list[dict]:
    """Birleşik aranabilir korpusu kur. Sıra deterministiktir (embeddings.npy ile hizalı):
    1) chunks.json, 2) global_rules (metinsel), 3) yaş bucket'larının açıklayıcı metinleri.

    Her birimde 'text' (modele gösterilecek temiz metin) bulunur. Doc2query
    genişletmesi ayrı RETRIEVAL SATIRLARI olarak eklenir (bkz. _build_rows) — tek
    embedding'e eklenmez (concat ortalaması seyreltir). Çok-vektörlü yaklaşım.
    """
    units: list[dict] = []

    # 1) chunks.json — metni AYNEN korunur (mevcut davranış değişmesin)
    for c in load_chunks():
        units.append({
            "chunk_id": c["chunk_id"],
            "text": c["text"],
            "source": "chunk",
            "lesson_id": c.get("lesson_id"),
            "label": c.get("lesson_title") or "İçerik",
        })

    with open(DATA_DIR / "master_knowledge_base.json", "r", encoding="utf-8") as f:
        kb = json.load(f)

    # 2) global_rules — her metinsel kuralı ayrı aranabilir birim yap
    for key, val in kb.get("global_rules", {}).items():
        label_root = _humanize(key)
        flat: list[tuple] = []
        _flatten_texts(key, val, flat)
        for path, text in flat:
            sub = path[len(key):].lstrip(".")
            label = f"{label_root}{(' — ' + _humanize(sub)) if sub else ''}"
            units.append({
                "chunk_id": f"global_rule:{path}",
                "text": f"{label}: {text}",
                "source": "global_rule",
                "lesson_id": None,
                "label": label,
            })

    # 3) yaş bucket'ları — yalnızca AÇIKLAYICI metin alanları (sayısal tablolar HARİÇ)
    for band, bucket in kb.get("yas_buckets", {}).items():
        if not isinstance(bucket, dict):
            continue
        band_h = _humanize(band)
        for fk, fv in bucket.items():
            if _is_descriptive_text(fv):
                label = f"{band_h} — {_humanize(fk)}"
                units.append({
                    "chunk_id": f"yas_bucket:{band}.{fk}",
                    "text": f"{label}: {fv}",
                    "source": "yas_bucket",
                    "lesson_id": None,
                    "label": label,
                })

    return units


def _build_rows(units: list[dict]) -> tuple[list[str], list[int]]:
    """Çok-vektörlü retrieval satırları kur. Her birim için:
      - 1 temel satır (birimin kendi metni),
      - curated birimlerde doc2query sorularının HER BİRİ ayrı satır (aynı birime işaret).
    Döner: (row_texts, row_unit_idx) — embeddings.npy ile aynı sırada.
    Sorgu, genişletme sorusuyla yüksek benzerlik kurup ana birime ulaşır (seyrelme yok).
    """
    expansions = _load_expansions()
    row_texts: list[str] = []
    row_unit: list[int] = []
    for i, u in enumerate(units):
        row_texts.append(u["text"])
        row_unit.append(i)
        if _is_expandable(u):
            exp = expansions.get(u["chunk_id"])
            if exp:
                for q in exp.split(" | "):
                    q = q.strip()
                    if q:
                        row_texts.append(q)
                        row_unit.append(i)
    return row_texts, row_unit


def corpus_stats(units: list[dict] | None = None) -> dict:
    units = units if units is not None else build_corpus()
    from collections import Counter
    c = Counter(u["source"] for u in units)
    return {"toplam": len(units), **dict(c)}


# ---------------------------------------------------------------------------
# SEMANTİK retrieval (birincil)
# ---------------------------------------------------------------------------
def _load_model():
    """Embedding modelini bir kez yükle (ağır işlem)."""
    if _state["model"] is None:
        from sentence_transformers import SentenceTransformer
        _state["model"] = SentenceTransformer(EMB_MODEL_NAME)
    return _state["model"]


def build_index_to_disk(verbose: bool = True) -> dict:
    """Korpusu embed edip diske yaz (embeddings.npy + corpus_meta.json).
    build_embeddings.py bunu çağırır. Çıktıyı tekrar üretilebilir kılar."""
    units = build_corpus()
    row_texts, row_unit = _build_rows(units)        # çok-vektörlü satırlar
    model = _load_model()
    emb = model.encode(
        row_texts, batch_size=64, normalize_embeddings=True,
        show_progress_bar=verbose,
    )
    emb = np.asarray(emb, dtype=np.float32)
    np.save(EMB_PATH, emb)
    META_PATH.write_text(
        json.dumps(
            {"model": EMB_MODEL_NAME, "dim": int(emb.shape[1]),
             "count": len(units), "n_rows": len(row_texts),
             "units": units, "row_unit": row_unit},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stats = corpus_stats(units)
    if verbose:
        logger.info("Embedding index yazıldı: %s satır (%d birim) | %s",
                    emb.shape, len(units), stats)
    return {"shape": list(emb.shape), "n_rows": len(row_texts), **stats}


def _init_semantic() -> None:
    """Model + embedding index'i yükle. Eksik/bayatsa diske yeniden yaz.
    Hata olursa istisnayı yukarı fırlatır (init_index TF-IDF'e düşer)."""
    needs_build = True
    units = build_corpus()                          # ucuz (sadece JSON)
    row_texts, row_unit = _build_rows(units)        # cache hizası için yeniden kur
    if EMB_PATH.exists() and META_PATH.exists():
        try:
            meta = json.loads(META_PATH.read_text(encoding="utf-8"))
            emb = np.load(EMB_PATH)
            if (meta.get("model") == EMB_MODEL_NAME
                    and meta.get("count") == len(units)
                    and emb.shape[0] == len(row_texts)):
                _state["units"] = meta["units"]
                _state["row_unit"] = np.asarray(meta["row_unit"], dtype=np.int64)
                _state["row_texts"] = row_texts
                _state["embeddings"] = emb.astype(np.float32)
                needs_build = False
        except Exception as e:  # bozuk cache → yeniden kur
            logger.warning("Embedding cache okunamadı, yeniden kurulacak: %s", e)

    if needs_build:
        logger.info("Embedding cache yok/bayat — yeniden üretiliyor (%d birim, %d satır)...",
                    len(units), len(row_texts))
        build_index_to_disk(verbose=False)
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        _state["units"] = meta["units"]
        _state["row_unit"] = np.asarray(meta["row_unit"], dtype=np.int64)
        _state["row_texts"], _ = _build_rows(meta["units"])
        _state["embeddings"] = np.load(EMB_PATH).astype(np.float32)

    _load_model()              # sorgu embedding'i için modeli hazırla
    _build_lexical_index()     # hibrit için lexical (TF-IDF) sinyali


def _max_per_unit(row_scores: np.ndarray) -> np.ndarray:
    """Satır skorlarını birim başına MAX ile topla (çok-vektörlü → birim skoru)."""
    n_units = len(_state["units"])
    agg = np.zeros(n_units, dtype=np.float32)
    np.maximum.at(agg, _state["row_unit"], row_scores)
    return agg


def _build_lexical_index() -> None:
    """Retrieval SATIRLARI üzerinde TF-IDF index'i kur — hibrit füzyon için."""
    texts = [_normalize(t) for t in _state["row_texts"]]
    vectorizer = TfidfVectorizer(
        analyzer="word", ngram_range=(1, 2), max_features=20000,
        min_df=1, max_df=0.95, stop_words=list(TURKCE_STOPWORDS),
        token_pattern=r"(?u)\b\w\w+\b",
    )
    _state["vectors"] = vectorizer.fit_transform(texts)   # (M_rows, V)
    _state["vectorizer"] = vectorizer


def _retrieve_semantic(query: str, top_k: int, min_score: float) -> list[dict]:
    """HİBRİT + ÇOK-VEKTÖRLÜ: satır bazında semantik & lexical cosine hesapla,
    birim başına MAX'a indir, sonra füzyonla. combined = ALPHA*sem + (1-ALPHA)*lex."""
    model = _state["model"]
    qv = model.encode([query], normalize_embeddings=True)[0].astype(np.float32)
    sem_rows = np.clip(_state["embeddings"] @ qv, 0.0, 1.0)        # (M,)
    sem = _max_per_unit(sem_rows)                                  # (N,)

    lex = np.zeros_like(sem)
    if _state.get("vectorizer") is not None:
        q_lex = _state["vectorizer"].transform([_normalize(query)])
        lex_rows = cosine_similarity(q_lex, _state["vectors"]).flatten()  # (M,)
        lex = _max_per_unit(lex_rows)                                     # (N,)

    combined = HYBRID_ALPHA * sem + (1.0 - HYBRID_ALPHA) * lex
    top_idx = combined.argsort()[-top_k:][::-1]
    out = []
    for i in top_idx:
        if combined[i] >= min_score:
            u = dict(_state["units"][i])
            u["_score"] = float(combined[i])
            u["_sem"] = float(sem[i])
            u["_lex"] = float(lex[i])
            out.append(u)
    return out


# ---------------------------------------------------------------------------
# TF-IDF retrieval (FALLBACK) — aynı birleşik korpus üzerinde
# ---------------------------------------------------------------------------
def _init_tfidf() -> None:
    units = build_corpus()
    texts = [_normalize(u["text"]) for u in units]
    vectorizer = TfidfVectorizer(
        analyzer="word",
        ngram_range=(1, 2),
        max_features=20000,
        min_df=1,
        max_df=0.95,
        stop_words=list(TURKCE_STOPWORDS),
        token_pattern=r"(?u)\b\w\w+\b",
    )
    vectors = vectorizer.fit_transform(texts)
    _state["units"] = units
    _state["vectorizer"] = vectorizer
    _state["vectors"] = vectors


def _retrieve_tfidf(query: str, top_k: int, min_score: float) -> list[dict]:
    q_vec = _state["vectorizer"].transform([_normalize(query)])
    sims = cosine_similarity(q_vec, _state["vectors"]).flatten()
    top_idx = sims.argsort()[-top_k:][::-1]
    out = []
    for i in top_idx:
        if sims[i] >= min_score:
            u = dict(_state["units"][i])
            u["_score"] = float(sims[i])
            out.append(u)
    return out


# ---------------------------------------------------------------------------
# PUBLIC API — init_index / retrieve / cevapla (imzalar korundu)
# ---------------------------------------------------------------------------
def init_index() -> None:
    """Retrieval index'ini bir kez kur. Önce semantik denenir; başarısız olursa
    (model/torch yok ya da RAM hatası) TF-IDF'e otomatik düşülür."""
    if _state["ready"]:
        return
    try:
        _init_semantic()
        _state["active"] = "semantic"
        logger.info("Retrieval: SEMANTİK aktif (model=%s, %d birim)",
                    EMB_MODEL_NAME, len(_state["units"]))
    except Exception as e:
        logger.warning("Semantik retrieval kurulamadı (%s) — TF-IDF fallback'e düşülüyor.", e)
        _init_tfidf()
        _state["active"] = "tfidf"
        logger.info("Retrieval: TF-IDF FALLBACK aktif (%d birim)", len(_state["units"]))
    _state["ready"] = True


def active_retrieval() -> str | None:
    """Aktif retrieval türü ('semantic' | 'tfidf' | None) — debug/log için."""
    return _state["active"]


def retrieve(query: str, top_k: int = SEM_TOP_K, min_score: float | None = None) -> list[dict]:
    """En alakalı birimleri döndür. Dönen her birimde 'chunk_id', 'text',
    'source', 'lesson_id', '_score' bulunur (test uyumlu)."""
    if not _state["ready"]:
        init_index()
    if _state["active"] == "semantic":
        ms = SEM_MIN_SCORE if min_score is None else min_score
        return _retrieve_semantic(query, top_k, ms)
    ms = TFIDF_MIN_SCORE if min_score is None else min_score
    return _retrieve_tfidf(query, top_k, ms)


SYSTEM_PROMPT = """Sen Tavşan Uykusu uyku eğitimi danışmanlığının bilgi botusun. \
Annelere kısa, profesyonel, sıcak Türkçe cevap verirsin. \
SADECE sana sunulan bilgi parçalarını kullanırsın; dışına çıkmazsın. \
Ders ya da kayıt adı asla geçmez (anneye 'kayıt36'da bahsedildiği gibi' deme). \
Cevap yoksa 'bu konuda detaylı bilgim yok, danışmanlık sürecinde sorabilirsiniz' dersin."""


# ---------------------------------------------------------------------------
# CEVAP CACHE — iki katman (exact + semantik), yaş bandı anahtarlı
# ---------------------------------------------------------------------------
# LLM çağrısından ÖNCE kontrol edilir: aynı normalize soru + aynı yaş bandı daha
# önce cevaplandıysa API'ye gidilmez (exact). Semantik katman, mevcut retrieval
# embedding modelini kullanır (YENİ model yüklenmez); cosine >= eşik VE aynı bant
# ise döner. Yaş bandı yoksa semantik atlanır, yalnızca exact çalışır.
# Depolama: küçük JSON dosyası (Streamlit Cloud runtime'ında yazılabilir). Modül
# global olduğundan Streamlit rerun'larında korunur (@st.cache_resource'a gerek
# yok; chatbot streamlit'e bağımlı değil — testlerde de aynı kod çalışır). LRU:
# son CACHE_MAX kayıt tutulur. Cache HIT loglanır (debug), kullanıcıya gösterilmez.

CACHE_PATH = DATA_DIR / "answer_cache.json"
CACHE_MAX = 500                 # LRU sınırı (son N cevap)
SEM_CACHE_THRESHOLD = 0.95      # semantik cache cosine eşiği (spesifikasyon)

_cache_state: dict[str, Any] = {
    "loaded": False,
    "entries": [],       # [{"h","band","q","answer","emb":[float]|None}]
    "emb_matrix": None,  # (M, dim) float32 — emb'i olan kayıtların matrisi
    "emb_idx": [],       # emb_matrix satır -> entries index eşlemesi
}


def _cache_norm(soru: str) -> str:
    """Cache anahtarı için normalize (lowercase, noktalama/fazla boşluk temizle).
    Mevcut TF-IDF normalizer'ı yeniden kullanır — davranış tutarlı."""
    return _normalize(soru)


def _cache_hash(norm_q: str, band: str | None) -> str:
    return hashlib.sha256(f"{band or ''}||{norm_q}".encode("utf-8")).hexdigest()


def _rebuild_emb_matrix() -> None:
    """emb'i olan kayıtlardan (M, dim) matris kur — semantik arama için."""
    embs, idxs = [], []
    for i, e in enumerate(_cache_state["entries"]):
        if e.get("emb"):
            embs.append(e["emb"])
            idxs.append(i)
    _cache_state["emb_matrix"] = np.asarray(embs, dtype=np.float32) if embs else None
    _cache_state["emb_idx"] = idxs


def _load_cache() -> None:
    if _cache_state["loaded"]:
        return
    if CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                _cache_state["entries"] = data
        except Exception as e:
            logger.warning("answer_cache.json okunamadı, sıfırlanıyor: %s", e)
    _cache_state["loaded"] = True
    _rebuild_emb_matrix()


def _persist_cache() -> None:
    try:
        CACHE_PATH.write_text(
            json.dumps(_cache_state["entries"], ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning("answer_cache.json yazılamadı: %s", e)


def _embed_query(soru: str):
    """Sorguyu MEVCUT retrieval modeliyle embed et (yoksa None → semantik atlanır)."""
    model = _state.get("model")
    if model is None:
        return None
    v = model.encode([soru], normalize_embeddings=True)[0]
    return np.asarray(v, dtype=np.float32)


def _cache_lookup_entry(soru: str, yas_bandi: str | None) -> dict | None:
    """Eşleşen cache KAYDINI döndür (answer + h/hash + band). Katman 1 exact
    (norm soru + bant), Katman 2 semantik (yas_bandi varsa, cosine >= eşik ve
    aynı bant). Bulunmazsa None. Dönen kaydın 'h' alanı, ses cache dosya adıyla
    (aynı hash) hizalıdır — API bunu MP3 dosya adı olarak kullanır."""
    _load_cache()
    norm = _cache_norm(soru)

    # Katman 1 — exact
    h = _cache_hash(norm, yas_bandi)
    for e in _cache_state["entries"]:
        if e["h"] == h:
            logger.info("Cache HIT (exact) [bant=%s]: %r", yas_bandi, soru[:60])
            return e

    # Katman 2 — semantik (yaş bandı yoksa atla)
    if yas_bandi is None:
        return None
    mat = _cache_state["emb_matrix"]
    if mat is None:
        return None
    qv = _embed_query(soru)
    if qv is None:
        return None
    sims = mat @ qv                       # normalize edilmiş → dot = cosine
    best = int(sims.argmax())
    if sims[best] >= SEM_CACHE_THRESHOLD:
        entry = _cache_state["entries"][_cache_state["emb_idx"][best]]
        if entry.get("band") == yas_bandi:            # aynı bant şartı
            logger.info("Cache HIT (semantik, cos=%.3f) [bant=%s]: %r",
                        float(sims[best]), yas_bandi, soru[:60])
            return entry
    return None


def _cache_lookup(soru: str, yas_bandi: str | None) -> str | None:
    """Geriye dönük: eşleşen cevabı (str) döndür, yoksa None."""
    entry = _cache_lookup_entry(soru, yas_bandi)
    return entry["answer"] if entry is not None else None


def _cache_store(soru: str, yas_bandi: str | None, answer: str) -> None:
    """Cevabı cache'e ekle (LRU: son CACHE_MAX). Yaş bandı varsa embedding de
    saklanır — gelecekteki semantik aramalar için."""
    _load_cache()
    norm = _cache_norm(soru)
    h = _cache_hash(norm, yas_bandi)
    # Aynı anahtar varsa çıkar + sona ekle (recency)
    _cache_state["entries"] = [e for e in _cache_state["entries"] if e["h"] != h]
    emb = None
    if yas_bandi is not None:
        qv = _embed_query(soru)
        if qv is not None:
            emb = [float(x) for x in qv]
    _cache_state["entries"].append(
        {"h": h, "band": yas_bandi, "q": norm, "answer": answer, "emb": emb})
    if len(_cache_state["entries"]) > CACHE_MAX:      # LRU sınırı
        _cache_state["entries"] = _cache_state["entries"][-CACHE_MAX:]
    _rebuild_emb_matrix()
    _persist_cache()


def _kaynak_ozet(units: list[dict]) -> list[dict]:
    """Retrieval birimlerini API/istemci için sade kaynak listesine indir."""
    return [{"chunk_id": u.get("chunk_id"), "label": u.get("label"),
             "source": u.get("source"), "score": round(float(u.get("_score", 0.0)), 4)}
            for u in units]


def _cevap_uret(soru: str, yas_bandi: str | None = None) -> dict:
    """RAG cevabını YAPISAL üret — cevapla() ve API katmanı bunu ortak kullanır.
    Döner: {cevap, cache_hit, kaynaklar, anahtar(hash), llm, in_chars, out_chars}.

    Akış cevapla() ile BİREBİR aynıdır (davranış korunur): LLM'den ÖNCE cache
    (exact+semantik) → yoksa retrieval → yoksa/anahtarsız fallback → Haiku + store.
    'anahtar', cevabın kanonik hash'idir (ses cache dosya adıyla hizalı)."""
    h = _cache_hash(_cache_norm(soru), yas_bandi)

    entry = _cache_lookup_entry(soru, yas_bandi)
    if entry is not None:
        # cache hit: retrieval YAPILMAZ (davranış korunur). Ses, eşleşen kaydın
        # hash'iyle (entry['h']) hizalanır ki hazır MP3 yeniden kullanılabilsin.
        return {"cevap": entry["answer"], "cache_hit": True, "kaynaklar": [],
                "anahtar": entry["h"], "llm": False, "in_chars": 0,
                "out_chars": len(entry["answer"])}

    retrieved = retrieve(soru, top_k=SEM_TOP_K)

    if not retrieved:
        msg = (
            "Bu konuyla ilgili Tavşan Uykusu içeriklerimde net bir bilgi bulamadım. "
            "Lütfen sorunuzu farklı şekilde ifade etmeyi deneyin veya danışmanlık "
            "sürecinde detaylı sorabilirsiniz."
        )
        return {"cevap": msg, "cache_hit": False, "kaynaklar": [], "anahtar": h,
                "llm": False, "in_chars": 0, "out_chars": len(msg)}

    context = "\n\n".join([f"- {c['text']}" for c in retrieved])

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or not HAS_ANTHROPIC:
        # Fallback: doğrudan en alakalı snippet'i kısalt ve döndür
        snippet = retrieved[0]["text"][:800].strip()
        msg = (
            "*Not: API anahtarı bulunmadığı için Tavşan Uykusu içeriğinden doğrudan en "
            "alakalı kısa parça gösteriliyor. Tam cevap için ANTHROPIC_API_KEY eklendiğinde "
            "Claude tarafından özetlenir.*\n\n"
            + snippet
        )
        return {"cevap": msg, "cache_hit": False, "kaynaklar": _kaynak_ozet(retrieved),
                "anahtar": h, "llm": False, "in_chars": 0, "out_chars": len(msg)}

    user_prompt = f"""ANNE SORUSU: {soru}

İLGİLİ BİLGİ PARÇALARI (Tavşan Uykusu içeriği):
{context}

CEVAP KURALLARI:
- Sadece yukarıdaki bilgi parçalarından cevapla, başka kaynak ya da genel internet bilgisi kullanma.
- Ders/kayıt/dosya adı asla geçmesin.
- Sıcak ama profesyonel Türkçe ile cevap ver.
- Kısa: 1-3 paragraf. Markdown kullanabilirsin (madde işareti olabilir).
- Yetersiz bilgi varsa açıkça söyle: 'bu konuda detaylı bilgim yok, danışmanlık sürecinde sorabilirsiniz.'

CEVAP:"""

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=CHATBOT_MODEL,
        max_tokens=MAX_TOKENS,
        # Sabit system prompt'a cache_control ekli. NOT: Sistem prompt'u ~80 token,
        # Haiku 4.5 minimum cache eşiği 4096 token → bu blok şu an cache TETİKLEMEZ
        # (no-op, hata vermez). Asıl değişken maliyet RAG context'i olup soruya göre
        # değiştiğinden cache'lenemez. Marker yapısal doğruluk + ileride system büyürse
        # otomatik devreye girsin diye burada. Çıktı birebir aynı kalır.
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_prompt}],
    )

    answer = response.content[0].text
    _cache_store(soru, yas_bandi, answer)   # sonraki aynı/benzer soru için sakla
    return {"cevap": answer, "cache_hit": False, "kaynaklar": _kaynak_ozet(retrieved),
            "anahtar": h, "llm": True,
            "in_chars": len(SYSTEM_PROMPT) + len(user_prompt), "out_chars": len(answer)}


def cevapla(soru: str, yas_bandi: str | None = None) -> str:
    """RAG ile cevap üret (str). Anthropic key yoksa fallback verir.

    yas_bandi: 19 yaş bucket'ından biri (örn. '8_ay'). Cache anahtarına girer;
    None ise yalnızca exact-match cache kullanılır (semantik cache atlanır).
    LLM çağrısından ÖNCE cevap cache'i kontrol edilir (exact + semantik).
    NOT: Yapısal sürüm için _cevap_uret(); bu ince sarmalayıcı yalnız metni döner
    (Streamlit arayüzü ve 151-item suite ile davranış BİREBİR aynı)."""
    return _cevap_uret(soru, yas_bandi)["cevap"]
