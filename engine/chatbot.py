"""
RAG soru-cevap motoru — TF-IDF retrieval + Claude generation.

Embedding olarak TF-IDF kullanılıyor (deploy dostu, pgvector gerek yok).
"""
import os
import json
import re
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

MODEL_NAME = "claude-opus-4-7"
MAX_TOKENS = 1024
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Lazy-loaded global cache
_state: dict[str, Any] = {
    "chunks": None,
    "vectorizer": None,
    "vectors": None,
}


# Türkçe için basit stopword listesi
TURKCE_STOPWORDS = {
    "ve", "ile", "için", "bu", "şu", "o", "bir", "iki", "üç", "da", "de",
    "ki", "mi", "mı", "mu", "mü", "ne", "var", "yok", "olan", "ama", "ancak",
    "fakat", "ya", "veya", "hem", "hiç", "çok", "az", "daha", "en", "gibi",
    "kadar", "sonra", "önce", "sırasında", "üzerine", "altına", "yani", "şey",
}


def _normalize(text: str) -> str:
    """Türkçe karakterleri normalize et, küçük harfe çevir."""
    text = text.lower()
    text = text.replace("i̇", "i")
    text = re.sub(r"[^\wçğıöşü\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_chunks() -> list[dict]:
    with open(DATA_DIR / "chunks.json", "r", encoding="utf-8") as f:
        return json.load(f)


def init_index() -> None:
    """Index'i (TF-IDF) sadece bir kere kur."""
    if _state["chunks"] is not None:
        return

    chunks = load_chunks()
    texts = [_normalize(c["text"]) for c in chunks]

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

    _state["chunks"] = chunks
    _state["vectorizer"] = vectorizer
    _state["vectors"] = vectors


def retrieve(query: str, top_k: int = 5, min_score: float = 0.05) -> list[dict]:
    """En alakalı chunk'ları döndür."""
    if _state["chunks"] is None:
        init_index()

    q_norm = _normalize(query)
    q_vec = _state["vectorizer"].transform([q_norm])
    sims = cosine_similarity(q_vec, _state["vectors"]).flatten()
    top_idx = sims.argsort()[-top_k:][::-1]

    return [
        {**_state["chunks"][i], "_score": float(sims[i])}
        for i in top_idx
        if sims[i] >= min_score
    ]


SYSTEM_PROMPT = """Sen Tavşan Uykusu uyku eğitimi danışmanlığının bilgi botusun. \
Annelere kısa, profesyonel, sıcak Türkçe cevap verirsin. \
SADECE sana sunulan bilgi parçalarını kullanırsın; dışına çıkmazsın. \
Ders ya da kayıt adı asla geçmez (anneye 'kayıt36'da bahsedildiği gibi' deme). \
Cevap yoksa 'bu konuda detaylı bilgim yok, danışmanlık sürecinde sorabilirsiniz' dersin."""


def cevapla(soru: str) -> str:
    """RAG ile cevap üret. Anthropic key yoksa fallback verir."""
    retrieved = retrieve(soru, top_k=5)

    if not retrieved:
        return (
            "Bu konuyla ilgili Tavşan Uykusu içeriklerimde net bir bilgi bulamadım. "
            "Lütfen sorunuzu farklı şekilde ifade etmeyi deneyin veya danışmanlık "
            "sürecinde detaylı sorabilirsiniz."
        )

    context = "\n\n".join([f"- {c['text']}" for c in retrieved])

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or not HAS_ANTHROPIC:
        # Fallback: doğrudan en alakalı snippet'i kısalt ve döndür
        snippet = retrieved[0]["text"][:800].strip()
        return (
            "*Not: API anahtarı bulunmadığı için Tavşan Uykusu içeriğinden doğrudan en "
            "alakalı kısa parça gösteriliyor. Tam cevap için ANTHROPIC_API_KEY eklendiğinde "
            "Claude tarafından özetlenir.*\n\n"
            + snippet
        )

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
        model=MODEL_NAME,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return response.content[0].text
