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

## 6.4 Kademeli fallback zinciri (K1→K4) + kapsama telemetrisi

`/chat` artık "bilgim yok" duvarı örmez; sırayla dener ve hangi katmanda
cevapladığını raporlar (`ChatResp.retrieval_layer`):

| Katman | Ne zaman | Davranış |
|---|---|---|
| **k1** | Alan içi, `top_score ≥ 0.55` | Metodolojiden doğrudan cevap |
| **k2** | Alan içi, `top_score ≥ 0.40` **veya** yaş bandı çözüldü | Eşik bir kademe düşer (−0.05) + yaş bandı genişletme; "en yakın bilgiye göre" çerçevelenir |
| **k3** | Alan içi ama skor düşük | Yaş-bağımsız **genel ilkeler** (`global_rule:*`) havuza girer + cevabın sonunda **1 netleştirme sorusu** sorulur |
| **k4** | Alan sinyali yok ve skor düşük | Kibar kapsam-dışı mesajı (**deterministik, LLM çağrılmaz**) |

**Eşik kalibrasyonu ölçümle yapıldı:** kapsam içi sorular `0.63–0.89`, kapsam dışı
`0.21–0.53`. Skor tek başına yetmiyor (`"mama tarifi"` 0.526 ile `"odası kaç derece"`
0.629 çok yakın), bu yüzden K4 kapısı **skor + alan sözlüğü** birlikte değerlendirir.
Sözlük geniş tutulmuştur: yanlış K4 (geçerli soruyu reddetmek), gereksiz K3'ten
daha kötüdür. Skor `≥0.55` ise sözlük eşleşmese bile soru alan içi sayılır.

**Değişmezler:** tıbbi sınır hiçbir katmanda gevşemez (tıbbi terim içeren sorular
asla K4 sayılmaz, doktor yönlendirmesi kapısına düşer); Claude K3'te bile yalnız
KB ilkelerinden konuşur, serbest bilgi eklemez.

### Kapsama telemetrisi

`chat_messages` tablosuna `retrieval_layer` (k1..k4, indeksli) ve `top_score`
eklendi (migration `0005`). Cache hit'te ikisi de NULL (retrieval yapılmadı).

Haftalık korpus boşluğu analizi — İlayda ile güncelleme turlarının girdisi:

```sql
SELECT content, top_score, created_at
  FROM chat_messages
 WHERE role = 'user'
   AND retrieval_layer IN ('k3', 'k4')
   AND created_at >= now() - interval '7 days'
 ORDER BY created_at DESC;
```

## Plan `content` şeması (resmî)

```jsonc
{
  "headline": "Elif için 9 ay programı — 2 kısa uyku, 19:00 yatış",
  "schedule": [
    {"time": "07:00", "end": "07:00", "type": "wake",  "title": "Sabah uyanışı",
     "key": "wake", "start_minute": 420, "end_minute": 420},
    {"time": "10:00", "end": "11:30", "type": "nap",   "title": "1. gündüz uykusu",
     "note": "Uyanıklık penceresi ~180 dk sonra", "key": "nap_1", ...},
    {"time": "19:00", "end": "07:00", "type": "sleep", "title": "Gece uykusu", ...}
  ],
  "night_wake_protocol": {"resist_minutes": 45, "routine_minutes": 15, "repeat": true, "aciklama": "..."},
  "markdown": "...",        // KALDI — geriye uyumluluk + detay metni
  "bucket": "9_ay", "adapted": false, ...
}
```

`schedule` **hem** `/plans/generate` **hem** `/plans/adapt` yanıtında doludur.
`type` enum'u: `wake | nap | sleep | feed | routine` — v1'de yalnız `wake/nap/sleep`
üretilir (`feed`/`routine` şemada ayrıldı; KB'de bu blokları türetecek veri yok).
`key`/`start_minute`/`end_minute` dahilidir (kaydırma + bildirim penceresi); mobil
`time`/`end`/`type`/`title`/`note` alanlarını kullanır.

## 6.5 `/chat` bebek log bağlamı (kişiselleştirme)

`ChatReq`'e opsiyonel `baby_id` eklendi. Verildiğinde bebeğin profili + son 3 günün
logları + bugünün plan çizelgesi kompakt bir özet olarak Claude'a **RAG
chunk'larından ayrı** bir blokla geçilir:

```
BEBEK VERİSİ (bu kullanıcının kendi kaydı):
Elif, 16 aylık (kayıtlı başlangıç gece uyanma: 3; eğitim başlangıcı 2026-07-01;
eğitim tamamlanma 2026-07-15). Son 3 gün: bugün şekerleme 1 (12:30-13:15);
dün gece yatış 19:05 (planlanan 20:00'den 55dk erken), gece uyanma 1 kez
(03:10, 25dk); önceki gün gece yatış 20:30 (planlanan 20:00'den 30dk geç).
Bugünün planı: 07:00 uyanış, 12:30-15:00 uyku, 20:00 yatış.
```

Sistem promptuna kural eklendi: *"Bebek verisi mevcutsa cevabını bu veriyle
ilişkilendir — bebeğin adıyla, somut saatlerle konuş; veriyle metodolojiyi
birleştir. Veride olmayan şeyi UYDURMA."* Kural **system** bloğundadır (statik,
cache prefix'i bozmaz); **veri** ise `messages` içinde, yani cache
breakpoint'inden **sonra** gider.

### ⚠️ Cache davranışı (güvenlik kritiği)

`baby_id` verilen istekler cevap cache'ini **tamamen bypass eder** — ne okur ne
yazar. Aksi halde bir bebeğin saatleri başka kullanıcıya cevap olarak dönerdi.
`baby_id`'siz genel sorularda exact + semantik cache aynen çalışmaya devam eder.

Diğer davranışlar:
- Bebek çağırana ait değilse **404** (varlık sızdırmaz — `get_owned_baby`).
- Log **ve** bugünün planı yoksa bağlam bloğu eklenmez → mevcut genel metodoloji
  cevabı korunur.
- Gece uyanmaları 12:00'den önceyse **bir önceki günün gecesine** yazılır.
- KVKK: bebek verisi içeriği uygulama loguna yazılmaz (yalnız `bebek=var|yok`).

## 6.7 Masal kütüphanesi + mutlak ses URL'leri

### Masal kataloğu

`data/stories.json` **statik**tir — metinler `scripts/build_stories.py` ile Claude
API üzerinden **bir kez** üretilip commit'lenir. `/voice/stories` ve
`/voice/generate` çalışma zamanında LLM çağırmaz (maliyet + gecikme + tutarlılık).

```sh
python scripts/build_stories.py           # eksik masalları üret
python scripts/build_stories.py --force   # hepsini yeniden üret
```

5 masal (511-629 kelime, ort. 589; `duration_hint: "5 dk"`):
Keloğlan ile Sihirli Değnek · Kırmızı Başlıklı Kız · Üç Küçük Domuzcuk ·
Çirkin Ördek Yavrusu · Ayşecik ile Uyku Perisi. **3 ninni değişmedi.**

Uyku öncesi ton kuralları üretim prompt'unda sabit: kısa cümleler, sakin ritim,
**şiddet/korku yok**, mutlu-sakin son, düz metin (markdown/emoji yok — mevcut TTS
temizleme katmanından sorunsuz geçer). Kırmızı Başlıklı Kız ve Üç Küçük Domuzcuk
**yumuşatılmıştır**: kurt kimseyi yemez, ev yıkılmaz, kovalama/avcı/balta yoktur.

### Uzun metin ve `eleven_flash_v2_5`

**Bölme gerekmedi.** `eleven_flash_v2_5` istek başına **40.000 karakter** kabul
ediyor; 700 kelimelik Türkçe masal ~5.000 karakter — limitin çok altında. Kod
değiştirilmedi. (Karşılaştırma: `eleven_multilingual_v2` 10.000, `eleven_v3` 5.000.)

### Mutlak ses URL'leri

`PUBLIC_BASE_URL` tanımlıysa `audio_url` ve `sampleUrl` **mutlak** döner:

```
https://tavsan-api-production.up.railway.app/audio/<hash>.mp3
```

Tanımsızsa göreli path (`/audio/<hash>.mp3`) — lokal geliştirme davranışı korunur.
Mobilin göreli path'i yanlış tabanla birleştirme riski böylece kalkar.

`/audio/{dosya}` **auth'suz** erişilebilir: dosya adı tahmin edilemez bir
SHA-256 hash'idir ve route yalnız hash kalıbını kabul eder (path-traversal
engelli). Beta için yeterli koruma; kamuya açık ama listelenemez.

### Ses cache (maliyet)

`voice_audio()` anahtarı `sha256(voice_id || temizlenmiş_metin)`. Aynı kullanıcı
aynı masalı ikinci kez dinlerken **TTS'e gidilmez** — dosya diskten servis edilir,
`cached: true`, maliyet `0`. Farklı `voice_id` aynı metinde ayrı dosya üretir.

> **Maliyet notu:** 5 masal = **21.124 karakter/kullanıcı** (~**$2.32** @ flash
> v2.5 kredi fiyatı, $0.00011/karakter). Bu **tek seferliktir** — tekrar dinlemeler
> cache'ten gelir. LRU sınırı 500 dosya / 100 MB; Railway'de disk efemer
> olduğundan yeniden deploy sonrası cache boşalır ve ilk dinlemeler yeniden
> üretilir. Kalıcılık isteniyorsa `data/audio_cache` klasörüne volume mount edilmeli.
