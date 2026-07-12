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
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()

from engine import chatbot   # noqa: E402 — mevcut RAG/cache/LLM motoru
from api import tts          # noqa: E402 — ElevenLabs + ses cache
from api import avatar       # noqa: E402 — LiveAvatar LITE session token (görüntü katmanı)
from api.config import get_settings   # noqa: E402 — merkezi env config
from api.db import db_healthy         # noqa: E402 — DB sağlık kontrolü
from api.routers import auth          # noqa: E402 — /api/v1/auth/* (Faz 2)
from api.routers import babies, logs, plans, subscriptions  # noqa: E402 — Faz 3
from api.routers import chat, voice   # noqa: E402 — Faz 4 (RAG chat + ses)

# Yapılandırılmış logging: süre/durum/hata bilgisini tek biçimde ver.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("tavsan.api")
settings = get_settings()

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

    # DB erişilebilirlik kontrolü (şema migration'ı ayrı adım: alembic upgrade head).
    if db_healthy():
        logger.info("DB bağlantısı OK (%s)",
                    "sqlite" if settings.is_sqlite else "postgresql")
    else:
        logger.warning("DB'ye ulaşılamadı — DATABASE_URL'i kontrol edin.")

    if settings.jwt_secret_is_default:
        logger.warning("JWT_SECRET varsayılan/güvensiz — production'da SABİTLEYİN.")
    yield


app = FastAPI(title="Tavşan Uykusu API", version="1.0.0", lifespan=lifespan)

# --- CORS: izinli origin'ler env'den (ALLOWED_ORIGINS, virgülle ayrılmış). ------
# UYARI: production'da ALLOWED_ORIGINS'i gerçek EXPO_PUBLIC domain(ler)ine sabitleyin;
# "*" (default) herkese açıktır — geliştirme kolaylığı için, prod'da daraltın.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """İstek/yanıt süresi + durum kodu logla. KVKK: gövde/mesaj İÇERİĞİ loglanmaz,
    yalnız method + path + status + süre."""
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        dt = int((time.perf_counter() - t0) * 1000)
        logger.exception("%s %s → HATA (%d ms)", request.method, request.url.path, dt)
        raise
    dt = int((time.perf_counter() - t0) * 1000)
    logger.info("%s %s → %d (%d ms)",
                request.method, request.url.path, response.status_code, dt)
    return response


# --- Mobil sözleşmesi: tüm yeni endpoint'ler /api/v1 altında ------------------
# Mobil EXPO_PUBLIC_API_URL = https://<host>/api/v1 → auth çağrıları /api/v1/auth/*.
# Mevcut demo endpoint'leri (/ask, /avatar-session, /audio, /health) kök'te kalır
# (geriye dönük uyum + Railway healthcheck /health).
API_V1_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=API_V1_PREFIX)
app.include_router(babies.router, prefix=API_V1_PREFIX)
app.include_router(logs.router, prefix=API_V1_PREFIX)
app.include_router(plans.router, prefix=API_V1_PREFIX)
app.include_router(subscriptions.router, prefix=API_V1_PREFIX)
app.include_router(chat.router, prefix=API_V1_PREFIX)
app.include_router(voice.router, prefix=API_V1_PREFIX)


class AskReq(BaseModel):
    soru: str
    yas_bandi: str | None = None


@app.get("/health")
def health():
    """Altyapı sağlığı: DB bağlantısı + RAG index yüklü mü.

    rag_mode: "semantic" (embedding), "tfidf" (fallback) veya null (kurulamadı).
    Railway healthcheck bu endpoint'i kullanır (railway.json)."""
    rt = chatbot.active_retrieval()
    if rt is None:                     # startup atlanmışsa tembel kur
        try:
            chatbot.init_index()
            rt = chatbot.active_retrieval()
        except Exception:
            rt = None

    db_ok = db_healthy()
    status = "ok" if (db_ok and rt is not None) else "degraded"
    return {
        "status": status,
        "db": "ok" if db_ok else "down",
        "rag_mode": rt,                # "semantic" | "tfidf" | null
        "retrieval": rt,               # geriye dönük uyumluluk (eski alan adı)
        "model": chatbot.CHATBOT_MODEL,
    }


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
@app.post(f"{API_V1_PREFIX}/avatar-session")   # Faz 4: mobil sözleşme için /api/v1 altında da
def avatar_session():
    """LiveAvatar LITE mode oturum token'ı üret (frontend Web SDK bununla başlar).

    API key ASLA dönmez; yalnız kısa ömürlü session_token + avatar meta döner.
    Hata (key yok / kota / ağ) → anlamlı JSON hata + uygun HTTP kodu (ham 500 çökme yok).
    NOT: Mobil v1'de KULLANILMAYACAK; mevcut haliyle taşındı (root + /api/v1)."""
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
