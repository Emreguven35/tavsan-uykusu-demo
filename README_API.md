# Tavşan Uykusu — API Katmanı (FastAPI + ElevenLabs TTS)

Mevcut RAG motorunun (`engine/chatbot.py`) üstünde **HTTP arayüzü**. Streamlit demosu
aynen çalışmaya devam eder; bu katman yalnızca REST + sesli cevap **ekler**, mevcut
davranışı **değiştirmez** (cevap cache mantığı korunur, üstüne ses cache eklenir).

## Mimari

```
İstek → FastAPI (api/main.py)
          └─ engine.chatbot._cevap_uret()   # cache(exact+semantik) → retrieval → Haiku
          └─ api.tts.ensure_audio()          # aynı hash'li MP3 varsa TTS yok; yoksa üret
```

- LLM/retrieval/cevap-cache: **mevcut motor import edilir** (kod çiftlenmez).
- Ses cache dosya adı = cevap cache anahtarıyla **AYNI hash** → cevap cache HIT
  olduğunda hazır MP3 TTS'siz döner.
- Model (sentence-transformers) uygulama **başlangıcında bir kez** yüklenir
  (istek başına değil). Yüklenemezse otomatik **TF-IDF fallback** (mevcut mekanizma).

## Local çalıştırma

```bash
pip install -r requirements.txt
# .env doldur (bkz. .env.example): ANTHROPIC_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
uvicorn api.main:app --host 0.0.0.0 --port 8000
# → http://localhost:8000/health
```

## Ortam değişkenleri

| Değişken | Zorunlu | Açıklama |
|----------|---------|----------|
| `ANTHROPIC_API_KEY` | evet (LLM için) | Yoksa chatbot fallback snippet döner |
| `ELEVENLABS_API_KEY` | hayır | Yoksa `ses_url=null` (cevap yine gelir) |
| `ELEVENLABS_VOICE_ID` | hayır | ElevenLabs ses kimliği |
| `ALLOWED_ORIGINS` | hayır | CORS; virgülle ayrılmış. Default `*` — **production'da sabitleyin** |
| `PORT` | Railway sağlar | uvicorn portu |

## Endpoint'ler

### `GET /health`
```bash
curl http://localhost:8000/health
# {"status":"ok","retrieval":"semantic","model":"claude-haiku-4-5"}
```

### `POST /ask`
```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"soru":"Beyaz gürültü zararlı mı?","yas_bandi":"8_ay"}'
```
```json
{
  "cevap": "…",
  "kaynaklar": [{"chunk_id":"…","label":"…","source":"…","score":0.83}],
  "cache_hit": false,
  "ses_url": "/audio/<hash>.mp3",
  "sure_ms": 812,
  "maliyet": {"llm_usd": 0.00041, "tts_usd": 0.05148}
}
```
- `yas_bandi` `null` olabilir → yalnızca exact-match cache (semantik atlanır).
- 2. kez aynı soru+bant → `cache_hit=true`, `llm_usd=0`, ses dosyadan (`tts_usd=0`).

### `GET /audio/{dosya}`
```bash
curl http://localhost:8000/audio/<hash>.mp3 --output cevap.mp3
```
MP3 (`audio/mpeg`) servis eder. Dosya adı yalnız hash kalıbıdır (path-traversal engelli).

## Ses cache

- Konum: `data/audio_cache/<hash>.mp3` (git'e girmez).
- LRU: en fazla **500 dosya** veya **100 MB**; aşılırsa en eski silinir.
- TTS hatası (kota/ağ/anahtar yok) → `ses_url=null`, endpoint **çökmez**, hata loglanır.

## Railway deploy

1. Repo'yu Railway'e bağla (New Project → Deploy from GitHub).
2. Başlatma komutu `railway.json` / `Procfile` ile tanımlı:
   `uvicorn api.main:app --host 0.0.0.0 --port $PORT` (healthcheck: `/health`).
3. **Variables** altına ekle: `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`,
   `ELEVENLABS_VOICE_ID`, `ALLOWED_ORIGINS` (gerçek domain).
4. Deploy. İlk açılışta embedding modeli (~470MB) indirilir; startup'ta bir kez.
   RAM yetmezse TF-IDF fallback devreye girer (uygulama çökmez).

> **Not:** Streamlit demosu ayrı çalışır (Streamlit Cloud). Bu API onu etkilemez;
> ikisi aynı `engine/` motorunu paylaşır.
