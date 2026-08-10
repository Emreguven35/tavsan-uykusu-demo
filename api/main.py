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

import secrets
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()

from engine import chatbot   # noqa: E402 — mevcut RAG/cache/LLM motoru
from api import tts          # noqa: E402 — ElevenLabs + ses cache
from api import avatar       # noqa: E402 — LiveAvatar LITE session token (görüntü katmanı)
from api.config import get_settings   # noqa: E402 — merkezi env config
from api.db import db_healthy         # noqa: E402 — DB sağlık kontrolü
from api.deps import require_demo_key # noqa: E402 — /ask + /avatar-session koruması
from api.routers import auth          # noqa: E402 — /api/v1/auth/* (Faz 2)
from api.routers import babies, logs, plans, subscriptions  # noqa: E402 — Faz 3
from api.routers import chat, voice   # noqa: E402 — Faz 4 (RAG chat + ses)
from api.routers import notifications # noqa: E402 — Faz 6.2 (push token + tercihler)
from api.routers import community      # noqa: E402 — Faz T (anne topluluğu)
from api.services import notifier     # noqa: E402 — Faz 6.2 (bildirim zamanlayıcısı)

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

    # JWT_SECRET artık ZORUNLU (config.py) — eksikse buraya hiç gelinmez.
    if settings.cors_wildcard_in_production:
        logger.warning(
            "ALLOWED_ORIGINS='*' ve ENVIRONMENT=production → tarayıcı origin'leri "
            "REDDEDİLİYOR (deny-all). Web istemcisi varsa origin'leri açıkça yazın. "
            "Mobil native istekler Origin göndermediği için etkilenmez.")
    if not settings.demo_api_key:
        logger.info("DEMO_API_KEY yok → /ask ve /avatar-session KAPALI (503).")

    # Bildirim zamanlayıcısı (Faz 6.2) — yalnız production'da başlar.
    notifier.start_scheduler()
    try:
        yield
    finally:
        notifier.shutdown_scheduler()


# Faz G4: production'da interaktif dokümantasyon + OpenAPI şeması KAPALI —
# tüm uç/şema yapısını ifşa edip saldırı yüzeyi keşfini kolaylaştırıyordu.
# Development'ta açık kalır (geliştirici deneyimi). ENVIRONMENT ile kontrol edilir.
_docs_kapali = settings.is_production
app = FastAPI(
    title="Tavşan Uykusu API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if _docs_kapali else "/docs",
    redoc_url=None if _docs_kapali else "/redoc",
    openapi_url=None if _docs_kapali else "/openapi.json",
)

# Faz T2: production'da OpenAPI şeması X-API-Key (DEMO_API_KEY) ARKASINDA açılır —
# mobil şema doğrulaması/codegen için. Yapı (public keşif) yalnız anahtar sahibine
# görünür. DEMO_API_KEY tanımsız → require_demo_key 503 döner (kapalı kalır); anahtar
# yanlış/eksik → 401. Development'ta zaten açık (built-in openapi_url) olduğundan bu
# gate yalnız production'da eklenir (built-in ile çakışma olmaz).
# NOT: /docs (Swagger UI) kapalı kalır — tarayıcı openapi.json'ı header'sız çeker.
# Şema doğrulaması için openapi.json'ı X-API-Key ile doğrudan tüketin (Postman/codegen).
if _docs_kapali:
    @app.get("/openapi.json", include_in_schema=False,
             dependencies=[Depends(require_demo_key)])
    def _gated_openapi():
        return app.openapi()

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
app.include_router(notifications.router, prefix=API_V1_PREFIX)
app.include_router(community.router, prefix=API_V1_PREFIX)


class AskReq(BaseModel):
    soru: str
    yas_bandi: str | None = None


# --- Sürüm damgası -----------------------------------------------------------
# NEDEN: sağlık kontrolünün 200 dönmesi YENİ KODUN CANLI OLDUĞUNU KANITLAMAZ —
# hiç yeniden başlamamış eski bir konteyner de kesintisiz 200 döner. Deploy'un
# gerçekten indiğini doğrulayabilmek için sürüm + korpus boyutu yayınlanır.
#
# GİZLİLİK: public /health'te TAM SHA verilmez (altyapı parmak izi). Yalnız kısa
# sürüm etiketi görünür; tam SHA X-API-Key ile /health?detail=1'de döner.
APP_VERSION = os.getenv("APP_VERSION", "faz-o3")
# BUILD_TIME derleme/deploy anında env ile verilir (Railway Variables). Yoksa
# sürecin başlama anına düşülür — bu da "bu instance ne zaman ayağa kalktı"
# sorusunu cevaplar ve yeniden başlamayan konteyneri ele verir.
_SUREC_BASLANGIC = datetime.now(timezone.utc).isoformat(timespec="seconds")
BUILD_TIME = os.getenv("BUILD_TIME") or _SUREC_BASLANGIC
# Tam SHA yalnız detay modunda; Railway bunu otomatik enjekte eder.
_GIT_SHA = (os.getenv("GIT_SHA") or os.getenv("RAILWAY_GIT_COMMIT_SHA") or "")


@app.get("/health")
def health(detail: int = 0, x_api_key: str | None = Header(default=None,
                                                           alias="X-API-Key")):
    """Altyapı sağlığı: DB bağlantısı + RAG index yüklü mü + hangi sürüm çalışıyor.

    rag_mode: "semantic" (embedding), "tfidf" (fallback) veya null (kurulamadı).
    corpus_units: yüklü korpus birim sayısı — deploy'un doğru veriyi aldığının
      kanıtı (korpus büyüdüyse bu sayı artar).
    version/build_time: çalışan sürüm damgası.

    detail=1 + geçerli X-API-Key → tam commit SHA ve ek ayrıntı. Anahtar yoksa
    veya yanlışsa DETAY SESSİZCE ATLANIR (401 dönmez): healthcheck'i kırmamak
    için — Railway bu endpoint'i anahtarsız çağırır.

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
    out = {
        "status": status,
        "db": "ok" if db_ok else "down",
        "rag_mode": rt,                # "semantic" | "tfidf" | null
        "retrieval": rt,               # geriye dönük uyumluluk (eski alan adı)
        "model": chatbot.CHATBOT_MODEL,
        "version": APP_VERSION,        # kısa etiket (SHA DEĞİL)
        "build_time": BUILD_TIME,      # ISO-8601
        "corpus_units": chatbot.yuklu_birim_sayisi(),
    }

    if detail:
        settings = get_settings()
        yetkili = bool(settings.demo_api_key and x_api_key
                       and secrets.compare_digest(x_api_key, settings.demo_api_key))
        if yetkili:
            out["detail"] = {
                "git_sha": _GIT_SHA or None,
                "git_sha_short": (_GIT_SHA[:7] or None) if _GIT_SHA else None,
                "process_start": _SUREC_BASLANGIC,
                "corpus_breakdown": chatbot.yuklu_birim_dagilimi(),
                "embedding_model": chatbot.EMB_MODEL_NAME,
            }
    return out


@app.post("/ask", dependencies=[Depends(require_demo_key)])
def ask(req: AskReq):
    """Demo soru-cevap. Faz 5R: X-API-Key (DEMO_API_KEY) ZORUNLU — public'te
    korumasız LLM tetikleyici bırakılmaz. Mobil v1 bunun yerine /api/v1/chat kullanır."""
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


@app.post("/avatar-session", dependencies=[Depends(require_demo_key)])
@app.post(f"{API_V1_PREFIX}/avatar-session", dependencies=[Depends(require_demo_key)])
def avatar_session():
    """LiveAvatar LITE mode oturum token'ı üret (frontend Web SDK bununla başlar).

    Faz 5R: X-API-Key (DEMO_API_KEY) ZORUNLU — her çağrı HeyGen kredisi harcatabilir.

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
