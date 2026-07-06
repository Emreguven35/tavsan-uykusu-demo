"""
FastAPI backend — Tavşan Uykusu motorunun üstünde REST katmanı.

MEVCUT motoru IMPORT ederek kullanır (engine.chatbot) — kod/mantık ÇİFTLENMEZ.
Streamlit tarafı ve cevap cache mantığı AYNEN korunur; buraya yalnızca HTTP arayüzü
ve ElevenLabs ses cache'i EKLENİR.

Çalıştırma (local):
    uvicorn api.main:app --host 0.0.0.0 --port 8000
Railway:
    uvicorn api.main:app --host 0.0.0.0 --port $PORT
"""
import os
import time
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()

from engine import chatbot   # noqa: E402 — mevcut RAG/cache/LLM motoru
from api import tts          # noqa: E402 — ElevenLabs + ses cache
from api import avatar       # noqa: E402 — LiveAvatar LITE session token (görüntü katmanı)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tavsan.api")

# --- LLM maliyet tahmini (Haiku 4.5: $1/1M in, $5/1M out) --------------------
# Türkçe için ~4 karakter ≈ 1 token (yaklaşık). count_tokens çağrısı eklemeden
# hızlı bir tahmin; kesin fatura Anthropic konsolundan. Cache HIT'te llm_usd=0.
LLM_IN_USD_PER_1M = 1.0
LLM_OUT_USD_PER_1M = 5.0
CHARS_PER_TOKEN = 4.0


def _llm_cost(in_chars: int, out_chars: int) -> float:
    it = in_chars / CHARS_PER_TOKEN
    ot = out_chars / CHARS_PER_TOKEN
    return round(it / 1e6 * LLM_IN_USD_PER_1M + ot / 1e6 * LLM_OUT_USD_PER_1M, 6)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Model + embedding index'i BİR KEZ yükle (istek başına değil). Başarısızsa
    # chatbot içindeki TF-IDF fallback devreye girer — API yine çalışır.
    try:
        chatbot.init_index()
        logger.info("Retrieval hazır: %s (model=%s)",
                    chatbot.active_retrieval(), chatbot.CHATBOT_MODEL)
    except Exception as e:
        logger.warning("init_index başarısız: %s", e)
    yield


app = FastAPI(title="Tavşan Uykusu API", version="1.0.0", lifespan=lifespan)

# --- CORS: izinli origin'ler env'den (virgülle ayrılmış). Default "*" ---------
# UYARI: production'da ALLOWED_ORIGINS'i gerçek domain(ler)e sabitleyin; "*"
# herkese açıktır (rapora bakınız).
_origins = os.getenv("ALLOWED_ORIGINS", "*").strip()
_allow = ["*"] if _origins == "*" else [o.strip() for o in _origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskReq(BaseModel):
    soru: str
    yas_bandi: str | None = None


@app.get("/health")
def health():
    rt = chatbot.active_retrieval()
    if rt is None:                     # startup atlanmışsa tembel kur
        try:
            chatbot.init_index()
            rt = chatbot.active_retrieval()
        except Exception:
            rt = None
    return {"status": "ok", "retrieval": rt, "model": chatbot.CHATBOT_MODEL}


@app.post("/ask")
def ask(req: AskReq):
    if not req.soru or not req.soru.strip():
        raise HTTPException(status_code=400, detail="soru boş olamaz")

    t0 = time.perf_counter()
    # Mevcut motoru kullan: cache (exact+semantik) → retrieval → Haiku → store.
    r = chatbot._cevap_uret(req.soru, req.yas_bandi)

    # Ses: aynı hash'li MP3 varsa TTS'e gitme; yoksa üret (hata olursa ses_url=null).
    audio = tts.ensure_audio(r["anahtar"], r["cevap"])

    sure_ms = int((time.perf_counter() - t0) * 1000)
    llm_usd = 0.0 if (r["cache_hit"] or not r["llm"]) else _llm_cost(
        r["in_chars"], r["out_chars"])

    return {
        "cevap": r["cevap"],
        "kaynaklar": r["kaynaklar"],
        "cache_hit": r["cache_hit"],
        "ses_url": audio["ses_url"],
        "sure_ms": sure_ms,
        "maliyet": {"llm_usd": llm_usd, "tts_usd": audio["tts_usd"]},
    }


@app.post("/avatar-session")
def avatar_session():
    """LiveAvatar LITE mode oturum token'ı üret (frontend Web SDK bununla başlar).

    API key ASLA dönmez; yalnız kısa ömürlü session_token + avatar meta döner.
    Hata (key yok / kota / ağ) → anlamlı JSON hata + uygun HTTP kodu (ham 500 çökme yok).
    """
    r = avatar.create_session_token()
    if not r.get("ok"):
        # HTTPException gövdesi {"detail": "..."} → anlamlı JSON. status: 500/502.
        raise HTTPException(status_code=r.get("status", 502),
                            detail=r.get("error", "avatar oturumu açılamadı"))
    return {
        "session_token": r["session_token"],
        "session_id": r["session_id"],
        "avatar_id": r["avatar_id"],
        "is_sandbox": r["is_sandbox"],
        "mode": r["mode"],
    }


@app.get("/audio/{dosya}")
def audio(dosya: str):
    if not tts.is_safe_name(dosya):              # yalnız hash.mp3 (traversal engeli)
        raise HTTPException(status_code=400, detail="geçersiz dosya adı")
    path = tts.AUDIO_DIR / dosya
    if not path.exists():
        raise HTTPException(status_code=404, detail="ses bulunamadı")
    return FileResponse(path, media_type="audio/mpeg")
