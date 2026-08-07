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
  "kestirme_protokolu": {             // FAZ Y — evrensel 30dk kestirme kuralı
    "tetik": "gündüz min süre tamamlanmadı",
    "sure_dk": 30,                    // kestirme süresi; dolunca bebek UYANDIRILIR
    "gece_uykusuna_gecis_dk": 60,     // kestirmeden sonra 1 saatte gece uykusuna geçilebilir
    "uyandirilir": true, "tum_bantlarda_gecerli": true, "aciklama": "..."
  },
  "yas_bandi": {                      // FAZ Y — çözülmüş İlayda yaş bandı (sayısal)
    "id": "9-12_ay", "ad": "9-12 ay", "varyant": null,
    "uyaniklik_penceresi_dk": [180, 240],
    "uyaniklik_penceresi_kaynak": null,   // devralındıysa kaynak bant yolu
    "gunduz_uyku_sayisi": [2, 2], "gunduz_uyku_sayisi_sabit": true,
    "gunduz_uyku_toplam_dk": [120, 180],  // ALT sınır = kestirme tetikleyicisi
    "gece_uykusu_dk": [600, 720],
    "toplam_gunluk_uyku_dk": [840, 840],  // 24s toplam ihtiyaç (gündüz + gece)
    "notlar": ["..."]
  },
  "kestirme_degerlendirme": {         // FAZ Y — yalnız adaptasyon sonrası dolar
    "gerekli": true, "eksik_dk": 60, "min_gunduz_dk": 180,
    "gerceklesen_dk": 120, "sure_dk": 30, "gece_uykusuna_gecis_dk": 60
  },
  "toplam_uyku_degerlendirme": {      // "bebeğim yeterince uyuyor mu?" (v1.1)
    "yeterli": false, "gerceklesen_dk": 780, "hedef_dk": [840, 840],
    "eksik_dk": 60, "fazla_dk": 0, "durum": "az"
  },
  "adapted": true, "base_plan_id": "<uuid>", "adaptation": { ... }
}
```

### Faz Y — yaş bandı tablosu tek kaynaktır

Çizelgenin **tüm sayıları** `data/yas_bantlari.json`'dan gelir (İlayda tablosu):
uyanıklık penceresi, gündüz uyku sayısı, gündüz toplam uyku, gece uykusu.
`master_knowledge_base.json`'ın serbest metinleri artık **ayrıştırılmaz** (yalnız
Faz Y öncesi saklanmış planlar için geriye uyumluluk yolu korunur).
**0-36 ay arasındaki her ay bir banda düşer** — ara yaş yoktur.

İlayda'nın **resmi** tablosu (`yas_bantlari.json` v1.1):

| Bant | Uyanıklık penceresi | Gündüz uyku | Gündüz toplam | Gece | **24s TOPLAM** |
|---|---|---|---|---|---|
| 0-2 ay | 40 dk – 1 s 20 dk | 4-5 | 5-7 saat | 8-10 saat | 15-18 saat |
| 3-5 ay | 1 s 30 dk – 2 s 15 dk | 3-4 | 4-5 saat | 10-11 saat | 14-16 saat |
| 6-8 ay | 2-3 saat | **3 (SABİT)** | 3-4 saat | 10-11 saat | 14 saat |
| 9-12 ay | 3-4 saat | 2 | 2-3 saat | 10-12 saat | 14 saat |
| 12-18 ay (2 uyku) | 3-4 saat | 2 | en az 2 saat | 11-12 saat | 13-14 saat |
| 12-18 ay (tek uyku) | 4-6 saat | 1 | en az 2 saat | 11-12 saat | 13-14 saat |
| 18-24 ay | 5-6 saat | 1 | en az 2 saat | 10-11 saat | 12-13 saat |
| 24-36 ay | 5 s 30 dk – 7 saat | 1 | en az 1 saat | 10-11 saat | 11-12,5 saat |

**Tablo okuma kuralı (İlayda teyidi):** gündüz aralığının **ALT SINIRI** kestirme
tetikleyicisidir. Üst sınır hedefin tavanıdır, tetikleyici değildir — ör. 9-12 ay
bandında 2 saatin altı kestirme üretir, 3 saatin üstü üretmez.

**24 saatlik toplam** (`toplam_gunluk_uyku_dk`) "bebeğim yeterince uyuyor mu?"
ölçütüdür ve **çizelge çözücüsünü de kısıtlar**: çizelge kimliği gereği
`toplam = 1440 − (uyku_sayısı + 1) × pencere` olduğundan bu alan doğrudan bir
pencere kısıtıdır. Adaptasyon çıktısında `content.toplam_uyku_degerlendirme`
olarak raporlanır (`durum`: `yeterli` | `az` | `fazla` | `veri_yok`); gündüz
**veya** gece verisi eksikse değerlendirme yapılmaz (yarım veriden yanlış alarm
üretilmez).

- **6-8 ay:** uyku sayısı sabittir; **8. ayda 2'ye düşürülmez.**
- **12-18 ay tek uykuya geçiş** (ÜÇÜ BİRDEN gerekir): ① öğlen uykusuna 12:00'den
  önce yatmamak, ② tek öğünde en az 2 saat uyku, ③ uyanıklık penceresi 4-6 saat.
  Üçü sağlanmıyorsa çocuk **hâlâ 2 uyku bandındadır** (varsayılan da budur).
- **24-36 ay öğlen uykusu reddi:** güne başlama 07:00 → hâlâ reddediyorsa 06:00 →
  hâlâ reddediyorsa öğlen uykusu kademeli kaldırılabilir.
- **Evrensel kestirme kuralı (tüm bantlar):** gündüz toplam uyku minimumu
  tamamlanamazsa **ilave 30 dakikalık kestirme**; 30 dk dolunca uyandırılır ve bu
  kestirmeden **1 saat sonra bile** gece uykusuna geçilebilir.

| Endpoint | Açıklama |
|---|---|
| `POST /plans/adapt?baby_id=` | Son 3 günün kayıtlarına göre çizelgeyi kaydırır. Kayıt yoksa/plan yoksa **409**. |
| `GET /plans/today?baby_id=` | Bugünün planı; yoksa en güncel plan bugüne adapte edilir (lazy). Hiç plan yoksa **404**. |

**Tekillik:** `generate` ve `adapt` aynı güne yazarken o günün kaydını **günceller** (UPSERT) — satır yığılmaz.

### Kurallar (deterministik, LLM yok)

İki **ayrı katman** vardır, karıştırılmamalıdır:

1. **Günlük ritim kaydırma** (eğitim dışı dönem): gerçek uyanış plandakinden **≥30 dk**
   saparsa çizelgenin tamamı sapma kadar kaydırılır, **maks ±45 dk**. Çizelge yaş
   bandına aykırı düşerse kaydırma yapılmaz, plan **tam yeniden üretilir**
   (`regenerate_required=true`). Faz Y'den sonra bandın üç ölçütü kontrol edilir:
   gündüz uyku **sayısı**, son uyku ile yatış arası **uyanıklık penceresi**, ve
   yatıştan sabah uyanışına **gece uykusu süresi**. Çizelgenin tamamı eşit
   kaydığında bu üçü değişmez — yani günlük ±45 dk kaydırma **tek başına** yeniden
   üretim tetiklemez; asıl tetikleyici bebeğin **bant atlamasıdır** (ör. 8 aylık
   3 uykuluk çizelge, 9. ayda 2 uyku bandına düşer).
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
  "headline": "Elif için 9 ay programı — 2 kısa uyku, 20:00 yatış",
  "schedule": [
    {"time": "07:00", "end": "07:00", "type": "wake",  "title": "Sabah uyanışı",
     "key": "wake", "start_minute": 420, "end_minute": 420},
    {"time": "10:20", "end": "11:50", "type": "nap",   "title": "1. gündüz uykusu",
     "note": "Uyanıklık penceresi ~200 dk sonra", "key": "nap_1", ...},
    {"time": "15:10", "end": "16:40", "type": "nap",   "title": "2. gündüz uykusu", ...},
    {"time": "20:00", "end": "07:00", "type": "sleep", "title": "Gece uykusu", ...}
  ],
  "night_wake_protocol": {"resist_minutes": 45, "routine_minutes": 15, "repeat": true, "aciklama": "..."},
  "kestirme_protokolu": {"tetik": "gündüz min süre tamamlanmadı", "sure_dk": 30,
                         "gece_uykusuna_gecis_dk": 60, "aciklama": "..."},
  "yas_bandi": {"id": "9-12_ay", "ad": "9-12 ay", "uyaniklik_penceresi_dk": [180, 240], ...},
  "markdown": "...",        // KALDI — geriye uyumluluk + detay metni
  "bucket": "9_ay", "adapted": false, ...
}
```

> **Faz Y:** çizelgedeki saatler `data/yas_bantlari.json`'dan türetilir. 9-12 ay
> bandında pencere 3-4 saat aralığındadır; 24 saatlik toplam uyku 14 saat olmak
> zorunda olduğundan (`toplam = 1440 − 3 × pencere`) pencere **200 dk**'ya oturur
> ve ilk uyku **10:20** olur. Faz Y öncesi KB metninden 10:00 çıkıyordu.

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

---

# Faz T — Anne Topluluğu API (`/api/v1/community/*`)

Metin tabanlı topluluk. **v1 kapsamı:** yalnız metin + düz cevap listesi.
Kapsam DIŞI: DM, görsel, profil sayfası, kullanıcı-tanımlı kategori, iç içe cevap.

## ⚠️ MOBİL BAĞLANTI — önce bunu oku (kategoriler yüklenmiyor sorunu)

Uç canlıda **çalışıyor**; `GET /api/v1/community/categories` gerçek token'la **200**
döner (aşağıda kanıt). "Kategoriler yüklenemedi" hatası neredeyse kesin **istemci
tarafı** üç nedenden biri (canlı loglarla doğrulandı):

| Belirti | Sunucu yanıtı | Sebep | Çözüm (mobil) |
|---|---|---|---|
| **401 Unauthorized** | `{"detail":"Geçersiz veya süresi dolmuş oturum"}` | Token yok / süresi dolmuş / `Authorization` başlığı eksik | Community sekmesini **giriş sonrası** çağır; `Authorization: Bearer <access_token>` ekle. Süre dolmuşsa `POST /api/v1/auth/refresh`. |
| **404 Not Found** | `{"detail":"Not Found"}` | Yol **`/api/v1`** ön-ekini içermiyor (ör. `/community/categories`) | Taban URL `https://<host>/api/v1`; yol `community/categories`. Tam yol: `/api/v1/community/categories`. |
| **307 Temporary Redirect** | (gövde yok) | Yolun **sonunda `/`** var (`…/categories/`). FastAPI `redirect_slashes` 307 döndürür ve bazı HTTP istemcileri redirect'te `Authorization`'ı düşürür → sonraki istek 401. | Yol sonuna `/` **koyma**. `…/categories` (slash yok). |

> **KURAL:** Tüm uçlar **`/api/v1` ön-ekli**, **sonda slash yok**, **hepsi `Authorization: Bearer <token>` zorunlu** (health/community dahil değil — community %100 auth'lu). `/openapi.json` production'da **kapalıdır** (404, bilinçli — Faz G4); şema doğrulaması için bu bölüm resmî kaynaktır.

**Ortak hata zarfları:**
- **401** (auth): `{"detail":"Geçersiz veya süresi dolmuş oturum"}`
- **400** (K0 moderasyon): `{"detail":{"code":"content_blocked","reason":"hakaret|iletisim_bilgisi|spam"}}`
- **403** (gönderi yasağı): `{"detail":{"code":"posting_blocked","reason":"muted|banned"}}`
- **429** (hız limiti): `{"detail":{"code":"rate_limited","reason":"cok_sik_konu | cok_sik_cevap"}}` + `Retry-After` (sn). **AYRI sayaçlar:** konu açma **60 sn/1**, cevap **15 sn/1** — konu açıp hemen cevap yazma akışı bloklanmaz.
- **404** (`/block` var olmayan kullanıcı): `{"detail":"Kullanıcı bulunamadı"}`
- **422** (Pydantic doğrulama): `{"detail":[{"type":"...","loc":["body","<alan>"],"msg":"..."}]}`

> **🔴 Engelleme akışı (Apple 1.2):** thread/reply yanıtları artık **`author_id`** (uuid, silinmiş kullanıcıda `null`) taşır. Bir gönderiden kullanıcı engellemek için `POST /block {"user_id": <author_id>}`. Kendi `author_id`'ini engelleme → **400**. Var olmayan uuid → **404**.
> **`status` alanı:** her thread/reply yanıtında `status` ∈ `visible | hidden`. Moderasyonla gizlenen içerik listede/detayda **yalnız SAHİBİNE** `status:"hidden"` olarak döner (mobil "kurallara aykırı bulundu" etiketi gösterir); başkasına hiç görünmez (listede yok, detayda 404).

---

## Profil

### `GET /api/v1/community/profile`
Kendi topluluk profili. **Profil yoksa 404** → mobil takma ad ekranını açmalı.
```jsonc
// 200
{"id":"24ddce0d-…","nickname":"DocAnne","status":"active","post_count":0,
 "is_expert":false,"is_moderator":false,
 "rules_accepted_at":"2026-08-04T22:09:19.548921Z","created_at":"2026-08-04T22:09:19.544893Z"}
// 404 (profil yok)
{"detail":"Topluluk profili yok — önce takma ad belirleyin"}
```
`status`: `active | muted | banned`. `is_expert` = İlayda/uzman rozeti. `is_moderator` = mod yetkisi.

### `POST /api/v1/community/profile`
Body: `{"nickname": "<2-24 karakter>"}` → **201** (yukarıdaki `ProfileResp`).
Takma ad **K0 filtresinden geçer** (küfür → 400). `rules_accepted_at` otomatik set edilir.
- **409** `{"detail":"Bu takma ad kullanılıyor"}` (çakışma) veya `{"detail":"Topluluk profili zaten var"}`.
- **400** `{"detail":{"code":"content_blocked","reason":"hakaret"}}` (uygunsuz takma ad).

### `PATCH /api/v1/community/profile`
Body: `{"nickname": "<yeni>"}` → **200** güncellenmiş `ProfileResp`. Aynı 409/400 kuralları.

---

## Kategoriler

### `GET /api/v1/community/categories`
Sabit 5 kategori + her birinde **published** konu sayısı.
```jsonc
// 200 — CANLI DOĞRULANMIŞ GERÇEK YANIT
{"categories":[
  {"key":"uyku","thread_count":0},
  {"key":"beslenme","thread_count":0},
  {"key":"gelisim","thread_count":0},
  {"key":"anne_hali","thread_count":0},
  {"key":"oneri","thread_count":0}]}
```
Kategori anahtarları (enum, sabit): **`uyku` `beslenme` `gelisim` `anne_hali` `oneri`**.

---

## Konular (threads)

### `GET /api/v1/community/threads`
**Cursor pagination.** Query:
- `category` (opsiyonel): yukarıdaki 5 anahtardan biri. Verilmezse tüm kategoriler.
- `cursor` (opsiyonel): önceki yanıtın `next_cursor` değeri (opak base64). İlk sayfada gönderme.
- `limit` (opsiyonel, default **20**, max **50**).

Sıralama `last_activity_at` **DESC**. Engellenen kullanıcıların ve `hidden/removed`
içerik **gizli**. Yanıt zarfı:
```jsonc
// 200 — GERÇEK YANIT
{
  "items": [
    {
      "id": "e871bcf5-…",
      "author_id": "24ddce0d-…",         // engelleme için (POST /block user_id). Silinmişte null
      "nickname": "DocAnneX",
      "is_expert": false,
      "category": "uyku",
      "title": "Gece uyanmalari nasil azalir",
      "body_preview": "6 aylik bebegim gece 4-5 kez uyaniyor…",  // body ilk 140 karakter
      "reply_count": 1,
      "like_count": 1,
      "expert_replied": false,
      "liked_by_me": true,
      "status": "visible",               // visible | hidden (hidden yalnız sahibine döner)
      "last_activity_at": "2026-08-04T22:10:11.421690Z",
      "created_at": "2026-08-04T22:10:10.475063Z"
    }
  ],
  "next_cursor": null    // null → son sayfa. Doluysa bir sonraki GET'te ?cursor=<bu değer>
}
```
**Sayfalama akışı:** `next_cursor` `null` olana kadar `?cursor=<next_cursor>&limit=20` ile devam et.
Kendi **gizlenmiş** (moderasyon) gönderin listede `status:"hidden"` ile döner (başkasına görünmez).

### `GET /api/v1/community/threads/{thread_id}`
Konu + cevaplar (cevaplar **created_at ASC**, sayfalı). Query: `cursor`, `limit` (cevap sayfalama).
Konu görünür değilse **404** (published herkese; `hidden` yalnız sahibine; `removed`/pending hiç).
```jsonc
// 200 — GERÇEK YANIT
{
  "id":"e871bcf5-…","author_id":"24ddce0d-…","nickname":"DocAnneX","is_expert":false,
  "category":"uyku","title":"Gece uyanmalari nasil azalir","body":"6 aylik bebegim…",
  "reply_count":1,"like_count":1,"expert_replied":false,"liked_by_me":true,"status":"visible",
  "last_activity_at":"2026-08-04T22:10:11.421690Z","created_at":"2026-08-04T22:10:10.475063Z",
  "replies":[
    {"id":"f77ae6e0-…","author_id":"…","nickname":"DocAnneY","is_expert":false,
     "body":"Uyaniklik penceresine dikkat cok yardimci oldu",
     "like_count":0,"liked_by_me":false,"status":"visible","created_at":"2026-08-04T22:10:11.416506Z"}
  ],
  "replies_next_cursor": null    // cevap sayfalama cursor'u (null → tüm cevaplar geldi)
}
```
> **"Silinmiş kullanıcı":** hesabı silinmiş yazarın konusu/cevabı KALIR; `nickname` = `"Silinmiş kullanıcı"`, `is_expert:false`, **`author_id:null`** döner.
> **`status:"hidden"`:** moderasyonla gizlenmiş; yalnız sahibi görür. Mobil "kurallara aykırı bulundu" etiketi gösterir. Sahibinin cevapları da aynı kuralla (`hidden` yalnız sahibine).

### `POST /api/v1/community/threads`
Body: `{"category":"uyku","title":"<1-100>","body":"<1-1000>"}` → **201** (tam `ThreadDetail`
zarfı, `replies:[]`). Moderasyon hattı K0→K1→K2 uygulanır (bkz. altta).
```jsonc
// 201 — GERÇEK YANIT
{"id":"e871bcf5-…","author_id":"24ddce0d-…","nickname":"DocAnneX","is_expert":false,
 "category":"uyku","title":"Gece uyanmalari nasil azalir","body":"6 aylik bebegim…",
 "reply_count":0,"like_count":0,"expert_replied":false,"liked_by_me":false,"status":"visible",
 "last_activity_at":"…","created_at":"…","replies":[],"replies_next_cursor":null}
```
Hatalar: **400** content_blocked (K0), **403** posting_blocked (muted/banned),
**429** rate_limited (**konu: 60 sn/1**), **404** profil yok, **422** geçersiz alan.

### `DELETE /api/v1/community/threads/{thread_id}`
Yalnız **sahibi** (status=`removed`). Başkasının / yok → **404** `{"detail":"Konu bulunamadı"}`.
Başarı: **200** `{"detail":"Konu silindi"}`.

---

## Cevaplar (replies)

### `POST /api/v1/community/threads/{thread_id}/replies`
Body: `{"body":"<1-1000>"}` → **201**.
```jsonc
// 201 — GERÇEK YANIT
{"id":"f77ae6e0-…","author_id":"…","nickname":"DocAnneY","is_expert":false,
 "body":"Uyaniklik penceresine dikkat cok yardimci oldu",
 "like_count":0,"liked_by_me":false,"status":"visible","created_at":"2026-08-04T22:10:11.416506Z"}
```
Yazan **uzman (is_expert)** ise konunun `expert_replied` alanı `true` olur + konu sahibine
**"İlayda konuna cevap verdi 🐰"** bildirimi gider (kendi cevabına gitmez).
K0/K1/K2/403 aynı; **hız limiti AYRI** (cevap **15 sn/1**, konu sayacından bağımsız). Konu yoksa/`published` değilse **404**.

### `DELETE /api/v1/community/replies/{reply_id}`
Yalnız sahibi (status=`removed`, konu `reply_count` düşer). Başkası/yok → **404**.

---

## Etkileşim

### `POST /api/v1/community/like`  (toggle)
Body: `{"target_type":"thread|reply","target_id":"<uuid>"}` → **200**.
```jsonc
// 200 — beğenildi
{"liked":true,"like_count":1}
// tekrar çağır → beğeni geri alınır
{"liked":false,"like_count":0}
```
İçerik yok/`published` değil → **404** `{"detail":"İçerik bulunamadı"}`.

### `POST /api/v1/community/report`
Body: `{"target_type":"thread|reply","target_id":"<uuid>","reason":"<enum>","note":"<opsiyonel ≤500>"}`.
`reason` enum: **`spam` `hakaret` `tibbi_risk` `reklam` `uygunsuz` `diger`**.
```jsonc
// 200
{"detail":"Şikayet alındı"}
// 200 — 2. farklı kullanıcı şikayeti → içerik otomatik gizlendi
{"detail":"Şikayet alındı, içerik incelemeye alındı"}
// 409 — aynı kullanıcı aynı içeriği tekrar şikayet edemez
{"detail":"Bu içeriği zaten şikayet ettiniz"}
```

### `POST /api/v1/community/block`
Body: `{"user_id":"<uuid>"}` → **200** `{"detail":"Kullanıcı engellendi"}` (idempotent).
`user_id` = engellenecek gönderinin **`author_id`**'si (thread/reply yanıtından alınır).
- **400** `{"detail":"Kendinizi engelleyemezsiniz"}` (kendi author_id'in).
- **404** `{"detail":"Kullanıcı bulunamadı"}` (var olmayan uuid — artık 500 değil).
Engellenen kullanıcının içeriği listelerde/detayda gizlenir.

### `DELETE /api/v1/community/block/{blocked_user_id}` → **200** `{"detail":"Engel kaldırıldı"}` (idempotent).

### `GET /api/v1/community/blocks`
```jsonc
// 200
[]  // veya [{"blocked_user_id":"<uuid>","nickname":"…","created_at":"…"}]
```

---

## Moderatör uçları  (`is_moderator` şart; değilse **403** `{"detail":"Moderatör yetkisi gerekli"}`)

### `GET /api/v1/community/mod/reports?resolved=false`
Bekleyen şikayetler (içerikle):
```jsonc
// 200
{"reports":[{"id":"…","target_type":"thread","target_id":"…","reason":"uygunsuz",
  "note":"…","resolved":false,"created_at":"…",
  "content_status":"published","content_body":"…(≤300)"}]}
```

### `POST /api/v1/community/mod/action`
Body: `{"target_type":"thread|reply","target_id":"<uuid>","action":"hide|restore|remove"}`
→ **200** `{"detail":"Uygulandı: hide"}`. İlgili şikayetler `resolved=true` yapılır.

### `POST /api/v1/community/mod/user`
Body: `{"user_id":"<uuid>","action":"mute|unmute|ban|unban"}` → **200**.
`mute` = 24 saat gönderi yasağı; `ban` = kalıcı + içerik gizli.

---

## Moderasyon davranışı (mobilin bilmesi gereken)

- **K0 (senkron):** küfür/hakaret, iletişim bilgisi (URL/tel/IBAN/e-posta), spam → **400
  content_blocked**, içerik KAYDEDİLMEZ. Kullanıcıya `reason`'a göre mesaj göster.
- **Hız limiti (AYRI sayaç):** konu açma **60 sn/1**, cevap yazma **15 sn/1** → **429**
  (+`Retry-After` sn). Konu açıp hemen cevap yazma akışı bloklanmaz. Mobil gönder butonunu
  ilgili süreyle kısıtlayabilir (ya da 429'da `Retry-After`'ı kullanır).
- **K1+K2 (asenkron):** işaretli içerik **anında yayınlanır** (201 döner), arka planda
  Haiku değerlendirir; uygunsuzsa sonradan gizlenir. Yani 201 = "yayınlandı", ama içerik
  moderasyonla **`status:"hidden"`**e düşebilir — sahibi görmeye devam eder (etiketli),
  başkasından gizlenir. Mobil bunu normal karşılamalı.
- **muted/banned:** gönderi denemesi **403 posting_blocked**; `reason` = `muted`/`banned`.
- **Engelleme (Apple 1.2):** her gönderi `author_id` taşır → `POST /block {"user_id":<author_id>}`.
  Kendi author_id → 400; var olmayan → 404. Engellenen kullanıcının içeriği listelerde/detayda gizlenir.

## Bildirim tercihi
`GET/PATCH /api/v1/notifications/preferences` artık **`community_replies`** (default `true`)
alanını da içerir: `{"plan_reminders":true,"daily_summary":true,"community_replies":true}`.
Kapatmak: `PATCH {"community_replies": false}`.

