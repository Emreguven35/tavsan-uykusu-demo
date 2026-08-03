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

---

# Faz 6 — Adaptif plan, bildirim, e-posta

Canlı: `https://tavsan-api-production.up.railway.app` · Mobil taban: `.../api/v1`

## 6.1 Adaptif plan

Plan içeriği artık markdown'a **ek olarak** yapısal alanlar taşır:

```jsonc
"content": {
  "markdown": "...",                  // değişmedi (mobil gösterim)
  "schedule": [                       // YENİ — saat saat çizelge
    {"key":"wake","type":"wake","start":"07:00","end":"07:00","label":"Sabah uyanış",
     "start_minute":420,"end_minute":420},
    {"key":"nap_1","type":"nap","start":"10:00","end":"11:30","label":"1. gündüz uykusu", ...},
    {"key":"bedtime","type":"night","start":"19:00","end":"07:00","label":"Gece uykusu", ...}
  ],
  "night_wake_protocol": {            // YENİ — 45-15-45 gece direnme protokolü
    "resist_minutes": 45, "routine_minutes": 15, "repeat": true, "aciklama": "..."
  },
  "adapted": true, "base_plan_id": "<uuid>", "adaptation": { ... }
}
```

| Endpoint | Açıklama |
|---|---|
| `POST /plans/adapt?baby_id=` | Son 3 günün kayıtlarına göre çizelgeyi kaydırır. Kayıt yoksa/plan yoksa **409**. |
| `GET /plans/today?baby_id=` | Bugünün planı; yoksa en güncel plan bugüne adapte edilir (lazy). Hiç plan yoksa **404**. |

**Tekillik:** `generate` ve `adapt` aynı güne yazarken o günün kaydını **günceller** (UPSERT) — satır yığılmaz.

### Kurallar (deterministik, LLM yok)

İki **ayrı katman** vardır, karıştırılmamalıdır:

1. **Günlük ritim kaydırma** (eğitim dışı dönem): gerçek uyanış plandakinden **≥30 dk**
   saparsa çizelgenin tamamı sapma kadar kaydırılır, **maks ±45 dk**. Kaydırılmış
   yatış yaş bandının `yatma_vakti` aralığı veya uyanıklık penceresi dışına düşerse
   kaydırma yapılmaz, plan **tam yeniden üretilir** (`regenerate_required=true`).
2. **Regresyon protokolü** (İlayda): `training_completed_at` dolu **ve** üzerinden
   **≥13 gün** geçmiş **ve** son 3 gecenin **≥2**'sinde **≥20 dk** süren `night_wake`
   varsa → `regression_detected=true`, `restart_program_suggested=true`.
   **Otomatik hiçbir şey üretilmez.**

### Mobil sözleşmesi (yapılacaklar)

- **14 günlük eğitim modülü** `PATCH /babies/{id}` ile `training_started_at` (modül
  başlarken) ve `training_completed_at` (bitince) alanlarını set etmelidir.
  Bu tarihler set edilmezse regresyon tespiti **hiçbir zaman** çalışmaz.
- `restart_program_suggested=true` geldiğinde kullanıcıya *"Programı baştan başlatmak
  ister misiniz?"* kartı gösterilir. Onaylanırsa mobil: `POST /plans/generate` +
  `PATCH /babies/{id}` ile `training_started_at=bugün`.
- Dashboard `GET /plans/today` çağırır (adaptasyonu tetikler).

## 6.2 Bildirimler (Expo Push)

| Endpoint | Açıklama |
|---|---|
| `POST /notifications/register-token` | `{expo_token, platform?, device_name?}` — upsert. Cihaz başka hesaba geçerse token devredilir. |
| `DELETE /notifications/token` | `{expo_token}` — çıkışta. Token yoksa da 200 (idempotent). |
| `GET /notifications/preferences` | `{plan_reminders, daily_summary}` — ikisi de varsayılan `true`. |
| `PATCH /notifications/preferences` | Kısmi güncelleme. |

**Zamanlayıcı:** uygulama içi APScheduler, **15 dakikada bir** (ayrı worker yok).
Bugünün planı olan her bebek için, önümüzdeki **15–30 dk** penceresinde başlayan uyku
bloklarına bildirim gönderir. Mükerrerlik `sent_notifications` tablosundaki UNIQUE
kısıtla engellenir. `DeviceNotRegistered` → token silinir.
**Yalnız `ENVIRONMENT=production`'da başlar** (lokal test kirliliği önlenir).

> **Ölçek notu:** birden çok instance'a çıkılırsa zamanlayıcı ayrı bir servise
> taşınmalıdır; şu an mükerrerliği yalnız DB kısıtı engelliyor.

## 6.3 E-posta

`MAIL_PROVIDER` üç moddan biri:

| Mod | Davranış |
|---|---|
| `resend` | Gerçek gönderim (`RESEND_API_KEY` gerekir). |
| `console` | Gönderim yok, içerik **loglanır**. Yalnız lokal geliştirme — token log'a düşer. |
| `disabled` | Gönderim yok, içerik **hiçbir yere** yazılmaz. Endpoint yine 200 döner. |

Boş bırakılırsa: anahtar varsa `resend`, yoksa production'da `disabled`,
geliştirmede `console`. **Şu an production `disabled`** — Resend bağlanınca tek env
değişikliğiyle (`RESEND_API_KEY` + `MAIL_PROVIDER` silinmesi) aktifleşir.

`POST /auth/reset-password-request` → 200 `{detail}`. Token **yanıtta dönmez**;
`resend` modunda derin bağlantı ile e-postaya gider:
`tavsan-uykusu://reset-password?token=...` (+ elle girme için düz metin token).

> **Mobil:** Resend bağlanana kadar "Şifremi unuttum" akışı **"yakında"** olarak
> işaretlenmelidir — `disabled` modda token kullanıcıya ulaşmaz.
