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

### `/health` — sürüm damgası ve deploy doğrulaması

```jsonc
GET /health
{
  "status": "ok", "db": "ok", "rag_mode": "semantic", "model": "claude-haiku-4-5",
  "version": "faz-e2",                     // KISA etiket — SHA DEĞİL
  "build_time": "2026-08-07T17:58:17+00:00",
  "corpus_units": 610                      // yüklü korpus birim sayısı
}
```

**Neden:** sağlığın 200 dönmesi **yeni kodun canlı olduğunu kanıtlamaz** — hiç
yeniden başlamamış eski bir konteyner de kesintisiz 200 döner. `version` +
`build_time` + `corpus_units` üçlüsü deploy'un gerçekten indiğini gösterir
(korpus büyüdüyse `corpus_units` artar).

**Tam SHA public'te VERİLMEZ** (altyapı parmak izi). Ayrıntı için:

```bash
curl -H "X-API-Key: $DEMO_API_KEY" "https://.../health?detail=1"
# → detail: { git_sha, git_sha_short, process_start, corpus_breakdown, embedding_model }
```

Anahtar yoksa/yanlışsa **401 dönmez, detay sessizce atlanır** — Railway
healthcheck'i anahtarsız çağırdığı için kırılmamalı.

Railway Variables: `APP_VERSION` (örn. `faz-e2`), `BUILD_TIME` (ISO-8601;
verilmezse süreç başlangıcına düşer), `GIT_SHA` (Railway `RAILWAY_GIT_COMMIT_SHA`
da okunur).

---

## Marka kuralı — üretilen cevaplarda kişi adı geçmez

Ürün **"Tavşan Uykusu"** adıyla konuşur. Üretilen hiçbir cevapta danışmanın ya
da bir eğitmenin adı geçmez. Savunma **iki katmanlı**:

1. **Kaynak:** `chatbot.marka_temizle()` korpus kurulurken her metni/etiketi
   temizler — ad modele HİÇ gitmez. Ham transkriptlerde anneler danışmana adıyla
   sesleniyor ("… Hanım, ben gündüz yirmi dakika bekleyemiyorum"); bu hitaplar
   düşer, iyelik ekleri "Tavşan Uykusu yönteminin …" olur.
2. **Talimat:** `SYSTEM_PROMPT` ayrıca kişi adı yasağını söyler (başka bir yol
   kalırsa diye).

`chunks.json` **değiştirilmez** — temizlik okuma anında yapılır, transkript
arşivi bozulmaz ve kural tek yerden değişir. KB anahtarları da isimsizdir çünkü
`chunk_id` `ChatResp.sources` ile **istemciye döner**.

Test: `tests/test_marka_ve_surum.py` — 20 çeşitli canlı soruda ihlal aranır
(yaş, ortam, gece, yöntem, ağlama/motivasyon, tıbbi sınır, kapsam dışı, kriz).

## Korpus filtreleri — eski numaralandırma ve danışmanlık lojistiği (Faz O3)

Marka kuralıyla **aynı desen**: `chunks.json` değiştirilmez, filtre okuma anında
uygulanır. İki sessiz sızıntı ölçümle yakalanıp kapatıldı.

**1. Eski 5 günlük numaralandırma.** Ham kayıtlar İlayda'nın ESKİ 5 günlük
programını anlatıyor ("üçüncü gün oda ortası", "beşinci gün yatır-çık") — **18 ayrı
kayıtta**. Uygulanan program 13 günlük. Retrieval bu cümleleri getirdiği için model
"3. gündeyim" sorusuna *oda ortası*, "6. gün" sorusuna *yatır-çık* diyordu.

- `chatbot.gun_asama_temizle()` gün numarasını bir merdiven aşamasına **bağlayan
  cümleleri** düşürür (75 cümle, korpusun %1,7'si). Teknik anlatım — bekleme
  süreleri, 45 dakika kuralı, kucak aralıkları — olduğu gibi kalır.
- Curated `kural_*` birimleri **muaftır** (gözden geçirilmiş içerik; cümle düşürmek
  anlamlarını bozuyor).
- Merdivenin **gün gün açık listesi** ayrı bir aranabilir birim olarak eklendi
  (`…kademeli_uzaklasma_13_gun_dirençli.gun_gun_liste`). KB'deki aralık
  anahtarlarından (`day_1_3`, `day_4_6`) **türetilir** — merdiven değişirse birim de
  değişir, ayrışamaz. Aralık gösterimi sınır günlerinde yanlış okunduğu için 13 gün
  tek tek yazılır (aynı ders `SYSTEM_PROMPT`'ta da alınmıştı).

Ölçüm: 1–13. gün sorusu → **11/13 → 13/13**.

**2. Danışmanlık lojistiği.** Rapor/video/tablo gönderme, iletişim saatleri, paket,
ücret iadesi metodolojiyle aynı kayıtta. "Ben beceremiyorum" gibi sorular
"danışmanınıza yazın" cevabına kayıyordu — **uygulama danışman değil, üründür.**

- `data/chunk_konulari.json` arşiv listesini **gerekçeleriyle** tutar; oradaki
  chunk'lar korpusa girmez (506 → 477 chunk birimi).
- Arşivde: `kayıt28` (ücret iadesi/erteleme kaydının tamamı), `kayıt19` (paket ve
  iletişim kuralları), `kayıt36`'nın lojistik kuyruğu (070–084, 086–087).
  `kayıt36_chunk_085` metodoloji olduğu için **arşivlenmedi**.

Ölçüm: 6 gerçek anne cümlesi × 2 örnek → **1/12 → 0/12** lojistik sapması.

Test: `tests/test_korpus_filtreleri.py` (29 kontrol) — filtre davranışı, aşırı
temizlik olmaması, kaynak dosyanın bozulmamış olması ve merdiven biriminin
KB'den türetildiği.

## Bekleme süresi artışı — esneklik + değişmez kural (İlayda düzeltmesi, 2026-08-25)

Cevaplar `5 → 10 → 15 → 20` ilerlemesini **katı bir kural** gibi sunuyordu. Doğrusu
bir standart + bir esneklik + bir bedelden oluşur:

| Parça | İçerik |
|---|---|
| **Standart** | Artış 5'er dakikadır (`5 → 10 → 15 → 20`) — cevap buradan başlar |
| **Esneklik** | Çocuk çok dirençliyse artış **1 dakikaya, hatta 30 saniyeye** inebilir (`5 → 6 → 7 → 8`) |
| **DEĞİŞMEZ KURAL** | Bekleme süresi **her gün MUTLAKA artar**: bir önceki günden düşük **de**, bir önceki günle aynı **da** olamaz — ikisi de alışkanlığa dönüşür |
| **Bedel** | Artış ne kadar küçükse öğrenme süreci o kadar **uzar**; esneklik verilirken bu da söylenir |

> **Esneklik artışın MİKTARINDADIR, artışın kendisinde değil.** Bu ayrım kaybolursa
> düzeltme kendi zıddına döner: "esneyebilir" cevabı "aynı kalabilir"e kayar.

Üç yerde birden karşılanır — biri eksik kalırsa retrieval eski katı metni tek başına
getirip yine dayatır:

- **KB:** `global_rules."bekleme_suresi_artis_esnekligi (ek 2026-08-25)"` (6 alt madde,
  hepsi ayrı aranabilir birim). Mevcut `bekleme_sureleri` kaydı da güncellendi:
  sayılar artık **STANDART** olarak sunulur ve yeni kurala geri referans verir.
- **`SYSTEM_PROMPT`:** standart + esneklik + bedel zorunlu; ayrıca anne **aynı** ya da
  **daha az** süre sorarsa cevaba "Evet" ile başlamak yasak (ölçümde cevap
  *"Evet, ikinci gecede de 5 dakika ile başlayabilirsiniz — ama…"* diye açılıyordu;
  gövdede düzeltmek yetmiyor, anne ilk cümleyi uyguluyor).
- **Plan motoru:** `bekleme_sureleri_planla()` her plan tipinde `artis_esnekligi`
  döndürür; hem LLM prompt'una hem yedek plana girer.

Test: `tests/test_bekleme_esnekligi.py` (36 kontrol). İki canlı soru **birlikte**
sabitlenir — yalnız birincisi test edilirse düzeltme değişmez kuralı yer:

| Soru | Beklenen |
|---|---|
| "1. gece 5 dk bekledim, 2. gece **6 dk** bekleyebilir miyim?" | **Evet** + sürecin uzayacağı uyarısı |
| "1. gece 5 dk bekledim, 2. gece **de 5 dk** bekleyebilir miyim?" | **Hayır** — açılış cümlesi bile onaylayıcı olmamalı |

---

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

# Gözlemlenebilirlik (2026-08-25)

## Sentry — hata izleme

`sentry-sdk[fastapi]`. **Yalnız** `ENVIRONMENT=production` **ve** `SENTRY_DSN`
tanımlıyken açılır; lokalde ve testlerde kapalıdır (geliştirici makinesinden
yanlışlıkla olay göndermek de bir sızıntıdır). `traces_sample_rate=0.1`.
Release etiketi `APP_VERSION` — hangi build'de patladığı Sentry'de görünür.

> **KVKK: asıl risk `send_default_pii` DEĞİL.** O bayrak istek gövdesini ve
> çerezleri kapatır, ama Sentry **varsayılan olarak stack frame'lerdeki yerel
> değişkenleri** gönderir. Bu uygulamada o değişkenler `req.message` (annenin
> gece 3'te yazdığı cümle), `text` (topluluk gönderisi), `baby.name`,
> `user.email` tutuyor. Tek bir 500 hatası anne verisini üçüncü bir servise
> taşıyabilirdi.

Üç katman birlikte uygulanır (`api/observability.py`):

| Katman | Ayar | Neyi keser |
|---|---|---|
| 1 | `include_local_variables=False` | Frame değişkenleri **hiç toplanmaz** |
| 2 | `max_request_body_size="never"` | İstek gövdesi eklenmez |
| 3 | `before_send=maskele` | Kalan her şey süzülür |

Katman 3 tek başına yetmez (bilinmeyen alan adları kaçar); 1–2 tek başına
yetmez (içerik istisna **mesajının içine** gömülü gelebilir:
`ValueError("geçersiz mesaj: <annenin cümlesi>")`).

**`maskele` ne yapar:** kullanıcıdan yalnız hash'li id bırakır; istek
gövdesi/çerez/IP/sorgu dizesini düşürür; `Authorization`/`X-API-Key` başlıklarını
maskeler; frame `vars` + kaynak satırlarını siler; istisna metninden
e-posta/token/DSN kalıplarını temizler ve 200 karakterde kırpar;
**breadcrumb'ları ve biçimlendirilmiş log mesajını tamamen düşürür.**

> **Breadcrumb'lar neden tamamen düşüyor?** Ölçümde şu kırıntı görüldü:
> `SQL INSERT chat_messages content=<annenin cümlesi>`. O cümlede ne e-posta var
> ne token, 200 karakterin de altında — hiçbir desene takılmıyor. Serbest metni
> süzmeye çalışmak yerine kaynağı kapatmak tek güvenli yol. Teşhis için hata
> tipi + stack trace + endpoint zaten yeterli. `logentry.message` **kalır**:
> o geliştiricinin yazdığı biçim dizesidir (`"chat: user=%s q_len=%d"`) — kod,
> veri değil; `params` düşürülür.

**Kullanıcı bağlamı:** `get_current_user` her istekte
`sha256(JWT_SECRET | user_id)[:16]` yazar. Tuzlu olduğu için Sentry'deki değerden
geri çözülemez ve başka sistemlerdeki id'lerle eşleştirilemez. E-posta gitmez.

Test: `tests/test_sentry_maskeleme.py` (49 kontrol) — gerçek sızıntı adaylarıyla
(anne mesajı, bebek adı, doğum tarihi, JWT, topluluk gönderisi) beslenip olayın
**içinde kalmadıkları** doğrulanır; teşhis için gerekenlerin (hata tipi, dosya,
endpoint) **kaldığı** da ayrıca kontrol edilir.

## Maliyet takibi — `api_usage`

Her **gerçek** Anthropic/ElevenLabs çağrısı `api_usage`'a yazılır. Sütunlar:
`service`, `operation`, `model`, `input_tokens`, `output_tokens`, `cached_tokens`,
`cache_write_tokens`, `characters`, `estimated_cost_usd`, `duration_ms`,
`user_id` (nullable), `created_at`. Migration `0008`.

**Tahmin yok, gerçek veri var.** Anthropic yanıtındaki `usage` bloğu olduğu gibi
alınır. Eski `/ask` tahmini (4 karakter ≈ 1 token) prompt caching'i **hiç
görmüyordu**: cache'ten okunan token normal fiyatın %10'u, cache'e yazılan
%125'i. O tahminle "cache bize ne kazandırdı" sorusu cevaplanamazdı.

Fiyatlar `api/config.py`'de **kod sabiti** (env değil — fiyat değişimi gözden
geçirme istesin): Haiku 4.5 $1/$5, Sonnet 4.6 $3/$15 (1M token),
Flash v2.5 $0.00011/karakter. Cache çarpanları: okuma ×0.10, yazma ×1.25.

> **Bilinmeyen model sessizce $0 yazmaz.** Fiyat tablosunda olmayan bir model
> gelirse maliyet **üst sınırdan** hesaplanır ve uyarı loglanır. Sıfır yazmak,
> maliyet tablosunu "her şey bedava" gösteren en tehlikeli hata olurdu.

**Yazma asenkron:** tek arka plan iş parçacığı + kuyruk; çağıran kuyruğa bırakıp
döner. Kuyruk dolarsa kayıt **düşürülür** (ana isteği bekletmektense veri kaybı).
Her hata sessizce yutulur — maliyet defteri bir yan defterdir, anne cevabını
yine alır.

**Cache HIT'te satır AÇILMAZ** (dış servis çağrısı yok). Uygulama cevap
cache'inin isabet oranı `chat_messages.cached`'ten raporlanır.

**Günlük eşik:** toplam `$20`'yi aşınca `CRITICAL` log düşer (günde bir kez,
uyarı yağmuru yok). Sayaç süreç içidir ve yeniden başlatmada o günün toplamını
DB'den okuyarak başlar.

### `GET /api/v1/admin/usage`

Yalnız **moderatör** (`community_profiles.is_moderator`; topluluk kapısıyla aynı
kontrol — yetki tek yerden yönetilsin). Parametreler: `from`, `to` (varsayılan
son 30 gün, max 366), `group_by=day|operation|service`.

```jsonc
{
  "toplam_usd": 3.42, "cagri_sayisi": 128,
  "servis":    [{"ad": "anthropic", "usd": 3.1, "cagri": 120}, ...],
  "operasyon": [{"ad": "chat", "usd": 1.2, "cagri": 98}, ...],
  "gunluk":    [{"gun": "2026-08-25", "usd": 0.4, "cagri": 12}, ...],
  "cache": {
    "prompt_cache": { "okunan_token": 900000, "oran": 0.33, "kazanc_usd": 0.81 },
    "cevap_cache":  { "toplam": 200, "hit": 60, "oran": 0.3,
                      "tahmini_kazanc_usd": 0.72 }
  },
  "gunluk_esik_usd": 20.0, "esigi_asan_gunler": []
}
```

`prompt_cache` **ölçülmüş** değerdir; `cevap_cache.tahmini_kazanc_usd` ise
tahmindir (cache HIT'te çağrı olmadığı için gerçek maliyeti yok — aynı dönemin
ortalama sohbet maliyetiyle çarpılır).

Test: `tests/test_maliyet_takibi.py` (58 kontrol).

**Geriye dönük tahmin:** `scripts/gecmis_maliyet_tahmini.py` — `api_usage` öncesi
dönem için chat/plan/voice sayaçlarından büyüklük mertebesi verir. Fatura
değildir (karakter→token oranı; prompt cache indirimini göremez).

> **Nasıl çalıştırılır:** üretim Postgres'i **dışarıya kapalıdır** — `DATABASE_URL`
> `postgres.railway.internal` adresini gösterir ve servise TCP proxy tanımlı
> değildir (`DATABASE_PUBLIC_URL` bu yüzden boş host/port taşır). Bu bilinçli ve
> doğru bir duruş; delmeyin. Betiği **konteynerin içinde** çalıştırın:
> ```
> railway ssh python scripts/gecmis_maliyet_tahmini.py
> ```
> İlk kullanımda SSH anahtarı gerekir (`ssh-keygen -t ed25519` → `railway ssh keys`);
> alternatif olarak Railway panelindeki servis terminalinden aynı komut çalışır.
> Betik salt-okunurdur. Yerelde ya da bir TCP proxy açıksa `DATABASE_PUBLIC_URL`
> otomatik tercih edilir.

---

# Yedekleme ve alarm (operasyon notu)

## Postgres yedeği — **OTOMATİK DEĞİL**

Railway'de yedekleme **opt-in**'dir; açılmadıysa hiç yedek alınmaz. CLI'da
`backup` komutu **yok** (`railway volume` yalnız list/add/delete/attach sunar),
yani durum ancak panelden görülür.

**Açma:** Railway → `tavsan-uykusu-api` → **Postgres** servisi → **Backups**
sekmesi → zamanlama seç. Seçenekler:

| Zamanlama | Sıklık | Saklama |
|---|---|---|
| Daily | 24 saatte bir | 6 gün |
| Weekly | 7 günde bir | 1 ay |
| Monthly | 30 günde bir | 3 ay |

Aynı volume'a birden fazla zamanlama uygulanabilir; elle yedek de alınabilir
(elle yedek volume kapasitesinin %50'siyle sınırlı). Ücret, yedeğin **artımlı**
boyutu üzerinden GB/dakika. Mevcut volume: **934 MB / 50 GB** — yani yedek
maliyeti şu ölçekte ihmal edilebilir.

**Öneri:** Daily + Monthly birlikte açılsın. Daily 6 gün tutuyor; tek başına
açılırsa bir haftadan eski bir hataya dönülemez. Monthly 3 aylık emniyet verir.

**Geri yükleme:** Backups sekmesinde zaman damgasına göre yedek seçilir →
restore. Railway volume'un zaman damgalı bir kopyasını oluşturur, orijinali
bağlantısız saklar, değişikliği deploy öncesi onaya sunar.

> ⚠️ **Geri yükleme, o yedekten SONRAKİ tüm yedekleri siler.** Yani "önce
> deneyeyim, olmazsa bugüne dönerim" YAPILAMAZ. Restore etmeden önce elle bir
> yedek alın.

## Yedek yoksa: günlük `pg_dump` (öneri — uygulanmadı)

Panelden backup açmak daha basit ve ucuz; aşağıdaki yalnız **panel yedeğine ek**
bir kopya isteniyorsa (örn. Railway dışında da bir kopya bulunsun diye) geçerli.

- Ayrı bir Railway **cron servisi** (`railway.json` → `cronSchedule: "0 3 * * *"`),
  aynı projede, Postgres'e private network üzerinden bağlanır.
- Komut: `pg_dump "$DATABASE_URL" | gzip > tavsan-$(date +%F).sql.gz`, ardından
  bir nesne deposuna (Railway bucket / S3 / Backblaze B2) yükleme.
- Saklama: 7 günlük + 4 haftalık kopya. Bugünkü boyut ~934 MB; gzip'li dump
  bunun çok altında kalır (dump veri dosyası değil, SQL metnidir).
- **Sırlar:** dump içinde kullanıcı e-postaları ve bcrypt hash'leri var — hedef
  bucket **private** olmalı ve KVKK saklama süresi tanımlanmalı.
- Doğrulama: haftada bir dump'ı boş bir DB'ye geri yükleyip
  `alembic upgrade head` + `SELECT count(*)` ile kontrol. **Test edilmemiş yedek,
  yedek değildir.**

## Uptime / sağlık alarmı

> ⚠️ **Railway healthcheck'i deploy'dan SONRA izlemez.** `/health` yalnız
> dağıtım sırasında sorgulanır; canlı servis çökerse Railway bunu healthcheck
> üzerinden fark etmez. Yani "healthcheck var" uptime alarmı değildir.

Railway'in verdiği alarmlar (Proje → **Settings** → **Webhooks** → URL + olay
filtresi → Save):

| Olay | Ne zaman |
|---|---|
| Deployment status | Deploy başarılı/başarısız olduğunda |
| Volume usage | Volume kapasiteye yaklaştığında |
| CPU/RAM monitor | Kaynak kullanımı eşiği aştığında |

Slack (`hooks.slack.com`) ve Discord URL'leri **otomatik biçimlendirilir**;
ara katman yazmaya gerek yok. Ayrıca konteyner tekrar tekrar çökerse yeniden
başlatma sınırına ulaşıldığında proje üyelerine e-posta + webhook gider.

**Uptime için dışarıdan bir izleyici gerekiyor** (Railway'de yok): UptimeRobot /
BetterStack / Cronitor gibi bir servis
`https://tavsan-api-production.up.railway.app/health` adresini 1–5 dakikada bir
çeksin, 200 dışında bir yanıtta ya da yanıt gecikmesinde bildirsin.

**Bonus — sürüm damgasıyla deploy doğrulaması:** aynı izleyici yanıt gövdesinde
`"version"` alanını da kontrol edebilir. Beklenen sürüm görünmüyorsa deploy
inmemiş demektir (`/health`'in 200 dönmesi tek başına bunu kanıtlamaz).

### Yapılacaklar listesi (panelden, elle)

1. Postgres → Backups → **Daily** + **Monthly** aç.
2. Elle bir yedek al (restore'un geri dönüşü olmadığı için taban kopya).
3. Project Settings → Webhooks → Slack URL'i ekle, üç olayı da seç.
4. UptimeRobot'ta `/health` için 5 dakikalık HTTP(s) monitörü kur.
5. `SENTRY_DSN`'i Railway Variables'a ekle (backend projesinin DSN'i).

---

# Faz 6 — Adaptif plan, bildirim, e-posta

Canlı: `https://tavsan-api-production.up.railway.app` · Mobil taban: `.../api/v1`

## 6.0 Plan üretimi — eşzamanlılık ve kuyruk (Faz O2)

Plan üretimi **~135 saniye** sürüyor. Süre neredeyse tamamen çıktı token sayısına
bağlı: Sonnet 4.6 ~53 token/sn üretiyor, plan ~7.200 token. Ölçüldü ve doğrulandı:

| Kurulum | Süre | Çıktı | Maliyet |
|---|---|---|---|
| Sonnet 4.6, tek çağrı (**üretimdeki**) | 137 sn | 7.167 tok | $0,133 |
| Haiku 4.5, tek çağrı | 107 sn | 10.450 tok | $0,060 |
| Sonnet, 3 parça paralel | 83 sn | 10.614 tok | $0,237 |
| Sonnet, 4 parça paralel | 80 sn | 15.124 tok | $0,279 |

**Model Sonnet 4.6'da kalıyor.** Haiku aynı prompt'la iki içerik kuralını ihlal etti
("temas yok", "hiç yaklaşmayın" — metodolojiye aykırı) ve prompt caching'i sessizce
devre dışı bıraktı (sabit ön-ek 3.707 token, Haiku 4.5'in cache eşiği 4.096).

**Paralelleştirme kapalı.** Her parça tek başına daha uzun yazdığı için toplam çıktı
şişiyor, duvar saati ~80 sn'de tıkanıyor ve maliyet ikiye katlanıyor.

**Eşzamanlılık:** üretim uvicorn threadpool'unda değil, **adanmış 3 kişilik havuzda**
koşar (`plan_jobs.MAX_ESZAMANLI`). Yığılma `/health` dahil hiçbir ucu etkilemez.

`POST /plans/generate` (202) ve `GET /plans/generate/{job_id}` yanıtları
**`queue_position`** taşır: `0` = üretim başladı (ya da bitti), `>0` = havuz dolu,
önünde kaç iş var. Alan varsayılan `0`'dır — eski istemciler etkilenmez.

> **Beta sonrasına not — streaming.** Toplam süreyi kısaltmadan algılanan gecikmeyi
> çözen tek seçenek planı akış olarak göndermek: ilk metin 2–3 saniyede ekrana düşer,
> anne planın yazılışını izler. İş/polling mimarisinin SSE'ye taşınmasını ve mobil
> tarafında karşılık gelen değişikliği gerektirir. Beta çıkışından sonra ele alınacak.
>
> **İlayda onayı bekliyor — plan kısaltma.** 60 sn'nin altına inmek ~950 kelime
> gerektiriyor (bugün 2.175). Kesilebilecek yer: her eğitim gününün altına yazılan
> "kısa gündüz uykusu" ve "B Planı" blokları aşama başına bir kez yazılabilir.

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
  "kestirme_protokolu": {             // v1.4 — evrensel kestirme (şekerleme) kuralı
    "tetik": "gündüz min süre tamamlanmadı",
    "sure_dk_min": 30, "sure_dk_max": 60,   // v1.4: tek 30 dk değil, ARALIK
    "gece_yatisina_kalan_min_dk": 150,      // ≥150 dk kaldıysa 60 dk yapılabilir
    "saat_penceresi": ["17:00", "19:00"],   // günün SONUNA eklenir
    "gece_uykusuna_gecis_dk": 60,     // kestirmeden sonra 1 saatte gece uykusuna geçilebilir
    "uyandirilir": true, "tum_bantlarda_gecerli": true, "aciklama": "..."
  },
  "yas_bandi": {                      // FAZ Y — çözülmüş İlayda yaş bandı (sayısal)
    "id": "9-12_ay", "ad": "9-12 ay", "varyant": null,
    "uyaniklik_penceresi_dk": [180, 240],
    "uyaniklik_penceresi_kaynak": null,   // devralındıysa kaynak bant yolu
    "gunduz_uyku_sayisi": [2, 2], "gunduz_uyku_sayisi_sabit": true,
    "gunduz_uyku_toplam_dk": [120, 180],  // ALT sınır = kestirme tetikleyicisi
    "kisa_uyku_esigi_dk": 60,             // v1.4 — K19/K20 eşiği (<6 ay: 45)
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
- **Evrensel kestirme (şekerleme) kuralı — v1.4 (tüm bantlar):** gündüz toplam
  uyku minimumu tamamlanamazsa **günün sonuna (17:00–19:00) ilave kestirme**
  eklenir. Süre **30 dk**, gece yatışına **≥150 dk** varsa **60 dk**. Süre
  dolunca uyandırılır ve bu kestirmeden **1 saat sonra bile** gece uykusuna
  geçilebilir. **Tetikleyici erken uyanma değil, gündüz uyku açığıdır.**
- **Beslenme kuralı — v1.4 (İlayda, Eyl 2026):** beslenme her uykudan **en az
  1 saat önce** bitmiş olmalı; ek gıdaya geçen bebekte **ek gıda da dahil**.
  (Eski "30-45 dakika" ve "ek gıda 2 saat" ifadeleri geçersizdir.)

| Endpoint | Açıklama |
|---|---|
| `POST /plans/adapt?baby_id=` | Bugünün çizelgesini kayıtlardan yeniden hesaplar (elle tetik). Kayıt yoksa/plan yoksa **409**. |
| `GET /plans/today?baby_id=` | Bugünün planı; **her çağrıda** şablon + bugünün kayıtlarından yeniden hesaplanır. Hiç plan yoksa **404**. |

**Tekillik:** `generate` ve `adapt` aynı güne yazarken o günün kaydını **günceller** (UPSERT) — satır yığılmaz.

### Gün içi kayma motoru v2 (deterministik, LLM yok) — K1-K9

> **v1'den ayrılık.** Eskiden çizelgenin tamamı son 3 günün **ortalama** sabah
> uyanışına göre **±45 dk** kaydırılıyor, kaydırılmış çizelge **ertesi günün
> tabanı** oluyor ve hesap günde **bir kez** çalışıp kilitleniyordu. Üçü de
> kaldırıldı. `shift_minutes` alanı **kullanımdan kalktı** (daima `0`).

| # | Kural | Davranış |
|---|---|---|
| K1 | **Sabah hedefi sabittir** | Üretimde belirlenen uyanış saati `content.schedule_template` içinde saklanır ve eğitim boyunca **değişmez**. Gerçek uyanış onu asla güncellemez, yarına taşımaz. |
| K2 | **Gün, o günün verisinden** | `content.schedule` her hesaplamada şablon + **bugünün** kayıtlarından kurulur. Yalnız bugünü bağlar; yarın yine K1 hedefinden başlar. |
| K3 | **Gündüz kayması** | Her uyku kaydında, o uykudan **sonraki** bloklar gerçek uyanma + uyanıklık penceresiyle yeniden hesaplanır. Geçmiş bloklara dokunulmaz. |
| K4 | **Yatış tavanı** | Yatış = son uyku bitişi + pencere. Bandın minimum gece uykusu sabah hedefine kadar sığmalıdır; tavan aşılırsa önce son gündüz uykusu kısaltılır/kaldırılır, sonra yatış tavana çekilir. Her müdahale `adaptation.uyarilar`a yazılır. |
| K5 | **Gece uyanması ≠ sabah** | Sabah uyanışı = günün **son** uyanışı (ilk gündüz uykusundan önceki). Ondan öncekiler **gece bölünmesi**dir (`adaptation.gece_bolunmeleri`). Bir `sleep` kaydı ancak sabah hedefinden **önce başlamışsa** gece uykusu sayılır — 70 dk'lık bir kayıt sabah uyanışı sanılmaz. Sabit `04:00` alt sınırı ve hedefe göreli **±90 dk toleransı kaldırıldı** (v2.1/K10). |
| **K10** | **Erken uyanma** | **Gün en erken 06:00'da başlar.** 06:00'dan önce uyanıp *tekrar uyumayan* bebekte gün 06:00'dan kurulur (`sabah_uyanis_gercek` gerçek saati tutar, `wake` bloğu 06:00 + açıklayıcı `note`) ve güne **30 dk şekerleme** eklenir: `key:"sekerleme"`, başlangıç = `06:00 + bandın MİNİMUM uyanıklık penceresi` (8 ay → 08:00). Sonrası normal zincir (`nap_1 = şekerleme bitişi + normal pencere`). Gün taşarsa **şekerleme asla kaldırılmaz**; önce son gündüz uykusu kısaltılır/kaldırılır. 06:00 öncesi uyanıp tekrar uyuduysa gece bölünmesi geçerli. **06:00 ve sonrası için ÜST SINIR YOK** — 09:00'da uyanan için gün 09:00'dan. Şablon hedefi değişmez; ertesi gün yine 07:00. |
| **K11** | **Uyarılar canlı** | `content.uyarilar` / `content.uygun_mu` / `content.yas` / `content.yas_bandi` / `content.bucket` **her GET'te** bebeğin güncel verisinden **yeniden türetilir**, plandan kopyalanmaz. **v2.3 — gece uyanma kartının ölçütü SAYI DEĞİL** (İlayda: "Sayı değil, kesinlikle"): `yaş ≥ 6 ay` **ve** son 7 gecenin **≥ 3**'ünde **20 dk+ süren, kendi dönemediği** uyanma. Kayıt hiç yoksa yalnızca o zaman annenin beyanına düşülür (eşik yine 5). Kart metni tektir ve sebebi söyler (gündüz uykusu yetersizliği). İz: `adaptation.gece_uyanma = {kaynak, deger, gece_sayisi}` + `adaptation.uzun_uyanma_gece_sayisi`. |
| K6 | **Kayıt yoksa plana uydu** | Zamanı geçtiği hâlde kaydı olmayan blok "planlandığı gibi oldu" sayılır ve `adaptation.varsayilan_bloklar` içinde listelenir. Hiç kayıt yoksa çizelge şablonun **birebir aynısıdır**. |
| K7 | **Varsayım bozulur** | Gerçek kayıt gelince (farklı saat, kısa uyku, `nap_skipped`, ya da uyanık geçen süre) blok gerçek veriye çevrilir ve K3 zinciri yeniden akar. Geçmişe dönük kayıtlar da aynı akışı tetikler. |
| K8 | **Kilit yok** | Hesap her `GET /plans/today` **ve** her `POST /logs/batch` sonrasında koşar; idempotenttir (aynı kayıtlar → aynı çizelge) ve LLM çağırmaz. İçerik değişmediyse DB'ye **yazılmaz**. Yaş bandı ihlali (`regenerate_required`) korundu — ama kontrol artık **şablona** uygulanır, bugün bir uyku atlandı diye plan yeniden üretilmez. |
| K9 | **Toplam uyku kontrolü** | Yeniden hesaplanan **günün** gündüz + gece toplamı bandın aralığıyla karşılaştırılır; eksikse kestirme/ilave uyku önerilir. Bu bir **öneri kartıdır, çizelgeyi bozmaz**. |
| **K9+K10.3 (v2.3)** | **Şekerleme — TEK mekanizma** | **Tetik: hesaplanan gündüz toplamı bandın MİNİMUMUNUN altında kalması.** Erken uyanma tek başına şekerleme ÜRETMEZ (v2.1'de üretiyordu). Yer: **son uykudan sonra, 17:00–19:00 arası**, gece yatışından **en az 60 dk önce** bitecek şekilde. Süre **30 dk**; gece yatışına **≥150 dk** varsa **60 dk**. 12-18 ay tek uyku varyantında öğle uykusu **<120 dk** ise 18:00–19:00 arası 30 dk. Gün taşarsa sıra: ① şekerleme kısaltılır/iptal edilir ② gece yatışı kırpılır — **gerçek uykular bandın minimumunun altına indirilmez** (bunun yerine uyarı düşülür). Blok başlığı süreye göre dinamiktir (`Şekerleme (30 dk)`). İz: `adaptation.sekerleme`. |
| **K19 (v2.2.2 → v2.3)** | **Gündüz/gece sınırı 19:00** | Uyku tipini **backend** belirler. Gündüz penceresi **19:00**'a kadardır (eskiden 17:00). 19:00'dan sonra başlayan **kapalı** bir kayıt, bandın `kisa_uyku_esigi_dk` değerinden (6 aydan küçükte **45**, 6 ay ve üstünde **60** dk) kısaysa **gündüz kısa uykusu**; açık kayıt ya da eşikten uzun kayıt **gece uykusudur**. |
| **K20 (v2.2.2 → v2.3)** | **Parça kayıt birleştirme — iki eşik** | Ardışık iki uyku kaydı arasındaki boşluk eşiğin altındaysa **tek uyku** sayılır. **İlk parça bandın kısa uyku eşiğinin altında kaldıysa eşik 45 dk** (anne hedef süreyi tutturmak için uğraşıyor), **ilk parça tam bir uykuysa 15 dk**. DB'de hiçbir şey değişmez; iz: `adaptation.birlesen_kayitlar`. |

**Regresyon protokolü** (İlayda) ayrı katmandır: `training_completed_at` dolu
**ve** üzerinden **≥13 gün** geçmiş **ve** son 3 gecenin **≥2**'sinde
*kendine dalamama* sinyali (`night_wake`) varsa → `regression_detected=true`.
**Otomatik hiçbir şey üretilmez.**

> **v2.3 KIRICI DEĞİŞİKLİK — `restart_program_suggested` KALDIRILDI.**
> İlayda "programı baştan başlatma" yolunu reddetti. Yerine **üç kademeli**
> `regresyon_karti` geldi (aşağıda). Mobil eski kartı **göstermemelidir**.

### `content.adaptation` — gün hesabının izi

```jsonc
"adaptation": {
  "hesaplandi_at": "2026-09-15T16:40:00+00:00",
  "sabah_uyanis_hedef": "07:00",        // K1 — şablondaki sabit hedef
  "sabah_uyanis_gercek": "08:00",       // bugün gerçekte kaçta kalktı
  "sabah_uyanis_kaynak": "kayit",       // kayit | varsayilan | erken_uyanma
  "varsayilan_bloklar": ["nap_1"],      // K6 — kaydı yok, plana uyduğu varsayıldı
  "yeniden_hesaplanan_bloklar": ["nap_2", "nap_3", "bedtime"],
  "atlanan_bloklar": [],                // K7 — nap_skipped ile düşürülenler
  "yok_sayilan_kayitlar": [{"id": "…", "sebep": "tanınmayan kayıt tipi: 'xyz'"}],
  "gece_bolunmeleri": [{"saat": "04:30", "dakika": 270, "sebep": "…"}],
  // v2.1/K10 — erken uyanma yoksa null
  "erken_uyanma": {"gercek_saat": "04:30", "gun_baslangici": "06:00",
                   "sekerleme_eklendi": true},
  // v2.1/K11 — gece uyanma sayısı ve kaynağı (artık kartın ölçütü DEĞİL)
  "gece_uyanma": {"kaynak": "olculen", "deger": 6, "gece_sayisi": 7},
  // v2.3/K11 — kartın ASIL ölçütü: son 7 gecede kaç gecede 20 dk+ süren,
  // kendi dönemediği uyanma oldu. null = kayıt yok (beyana düşüldü).
  "uzun_uyanma_gece_sayisi": 4,
  // v2.3 — şekerleme eklendiyse gerekçesi; yoksa null
  "sekerleme": {"tetik": "gunduz_acigi", "eksik_dk": 45, "sure_dk": 30},
  // v2.3/K20 — parça birleştirme eşikleri (ilk parça kısaysa 45, tamsa 15 dk)
  "parca_birlestirme_dk": 15, "parca_birlestirme_kisa_dk": 45,
  "uyaniklik_penceresi_dk": 150,
  "uyarilar": ["Bugün yatış saati bandın sınırına dayandı"],
  "regenerate_required": false, "regression_detected": false,
  // v2.3 — üç kademeli regresyon akışı (restart_program_suggested KALKTI)
  "regresyon_karti": null, "egitim_baslangic_gunu": 28,
  "kirkbes_gun_doldu": false,
  "reasons": [...], "log_summary": {...}
}
```

### v2.3 — regresyon kartı ve `POST /plans/regresyon-cevap`

`regression_detected=true` olduğunda `regresyon_karti` **üç kademeden birini**
taşır (İlayda S9). `null` ise gösterilecek kart yoktur.

```jsonc
"regresyon_karti": {
  "tip": "kendi_donuyor_mu",     // kendi_donuyor_mu | devam_45 | tibbi_yonlendirme
  "metin": "Bebeğiniz gece uyandığında 20 dakika beklerken kendi başına uykuya dönebiliyor mu?",
  "egitim_gunu": 28,             // eğitimin kaçıncı günü (1'den başlar)
  "kirkbes_gun_doldu": false
}
```

| Kademe | Ne zaman | Mobil ne yapar |
|---|---|---|
| `kendi_donuyor_mu` | Anne henüz cevap vermedi | Evet/Hayır sorusunu gösterir |
| `devam_45` | "Hayır" + 45 gün **dolmadı** | Bilgi kartı; eğitim aynen sürer |
| `tibbi_yonlendirme` | "Hayır" + 45 gün **doldu** | Pediatri/fizyoterapi önerisi kartı |

**`POST /api/v1/plans/regresyon-cevap?baby_id={uuid}`** → gövde
`{"kendi_donuyor": true|false}`

```jsonc
// 200
{"baby_id": "…", "kendi_donuyor": false, "cevap_at": "2026-09-22T…Z",
 "regresyon_karti": {"tip": "devam_45", "metin": "…", "egitim_gunu": 10,
                     "kirkbes_gun_doldu": false},
 "egitim_baslangic_gunu": 10, "kirkbes_gun_doldu": false}
```

- **"evet"** → kart **7 gün** kapanır (`regresyon_karti: null`), sonra durum
  yeniden değerlendirilir.
- **"hayır"** → süresi yoktur; akış 45 gün kapısına göre ilerler.
- Uç **plan üretmez**, çizelgeyi **değiştirmez**; yalnız cevabı saklar ve cevabın
  hemen sonraki kart durumunu döndürür.
- `404` başkasının bebeği, `422` gövde eksik, `401` kimliksiz.

`schedule[]` eleman şeması **değişmedi** (`key,type,time,end,title,note,start_minute,end_minute`);
yalnız `kaynak` (`kayit|plan|varsayilan`) ve gece bloğunda `gece_uykusu_dk` **eklendi**.

### Mobil sözleşmesi (yapılacaklar)

- **14 günlük eğitim modülü** `PATCH /babies/{id}` ile `training_started_at` (modül
  başlarken) ve `training_completed_at` (bitince) alanlarını set etmelidir.
  Bu tarihler set edilmezse regresyon tespiti **hiçbir zaman** çalışmaz.
- **v2.3:** `restart_program_suggested` **KALKTI**. Yerine `regresyon_karti`
  gösterilir ve anne cevabı `POST /plans/regresyon-cevap` ile gönderilir.
  "Programı baştan başlat" akışı **yoktur** — 45 gün dolana kadar eğitim sürer.
- Dashboard `GET /plans/today` çağırır (hesabı tetikler).
- **YENİ (v2):** `POST /logs/batch` yanıtındaki **`plan_updated: true`** geldiğinde
  mobil `plans/today` sorgusunu **invalidate etmelidir** — yoksa anne kaydı girer,
  plan sunucuda değişir ama ekranda eski saatler kalır.
- **YENİ (v2):** "bu uykuyu hiç yapmadı" için `type: "nap_skipped"` kaydı gönderilebilir
  (`ended_at` gerekmez). Motor o uykuyu çizelgeden düşürür ve sonrakileri öne çeker.
- **YENİ (v2):** `adaptation.varsayilan_bloklar` içindeki bloklar "kaydı yok, plana
  uyduğu varsayıldı" demektir — mobil bunları soluk gösterip "böyle mi oldu?" diye
  sorabilir. `adaptation.uyarilar` kullanıcıya gösterilmelidir.
- **YENİ (v2.1):** çizelgede **`key:"sekerleme"`** bloğu çıkabilir (erken uyanma
  telafisi). Normal gündüz uykularından biri **değildir**; mobil "3 uyku" rozetini
  sayarken bu bloğu saymamalıdır.
- **YENİ (v2.1):** `PATCH /babies/{id}` artık **`saglik_problemi`** ve
  **`dogum_haftasi`** kabul ediyor; onboarding bunları `profile_overrides` yerine
  doğrudan bebeğe yazabilir (yazılmazsa `POST /plans/generate` gövdesindeki
  `profile_overrides` yine bebeğe kalıcılaştırılır).
- **YENİ (v2.1):** `content.uyarilar` artık **canlıdır** — kart koşul düşünce
  kaybolur, koşul oluşunca gelir. Mobil listeyi önbelleğe almamalı, her
  `plans/today` yanıtında gelen listeyi göstermelidir.

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
| **k3_5** | Alan sözlüğü tutmadı ama soru bebek/ebeveynlik dünyasında | Eksikliği **dürüstçe söyler** ("bu konuda net bir kayıt yok, ama şu ilkeler geçerli…") + genel ilkelerden yardım + netleştirme sorusu. **"Kapsam dışı" DEMEZ** |
| **k4** | Soru **gerçekten** başka konuda (mama tarifi, vergi, hava durumu) | Kibar kapsam-dışı mesajı (**deterministik, LLM çağrılmaz**) |

### K4 SON ÇAREDİR (Faz E-2 — kalıcı kural)

Cevap üretilemeyen her durumda **önce K3.5 denenir.** K4 artık varsayılan değil,
**pozitif bir karardır**: yalnız açık kapsam-dışı işareti varsa (`tarif`, `vergi`,
`hava durumu`…) ya da hiçbir alan/ebeveynlik sinyali yoksa verilir.

**Neden değişti:** "Üçüncü gündeyiz hiç düzelmedi, bırakmak istiyorum" gibi
gerçek anne cümleleri hiçbir metodoloji terimi içermediği için K4'e düşüyordu —
tam da yardıma en çok ihtiyaç duyan kullanıcı kapıdan çevriliyordu. Artık
**duygusal/motivasyon sinyali TEK BAŞINA kapsam-içi sayılır**; metodoloji terimi
şartı aranmaz.

> **Türkçe kök tuzağı:** alan sözlüğündeki `"ağla"` kökü `"ağlıyor"`u YAKALAMAZ
> (ağla + ıyor → ağlıyor). "3 gündür ağlıyor hiç düzelmedi" bu yüzden alan dışı
> sayılıyordu. Kök `"ağl"` olarak düzeltildi; çekim biçimleri testte sabitlendi.

**Serbest yorum yok:** K3.5'te de cevap yalnız KB ilkelerinden kurulur. Bilgi
gerçekten yoksa bunu söylemek serbesttir, uydurmak değildir.

**Eşik kalibrasyonu ölçümle yapıldı:** kapsam içi sorular `0.63–0.89`, kapsam dışı
`0.21–0.53`. Skor tek başına yetmiyor (`"mama tarifi"` 0.526 ile `"odası kaç derece"`
0.629 çok yakın), bu yüzden K4 kapısı **skor + alan sözlüğü** birlikte değerlendirir.
Sözlük geniş tutulmuştur: yanlış K4 (geçerli soruyu reddetmek), gereksiz K3'ten
daha kötüdür. Skor `≥0.55` ise sözlük eşleşmese bile soru alan içi sayılır.

**Değişmezler:** tıbbi sınır hiçbir katmanda gevşemez (tıbbi terim içeren sorular
asla K4 sayılmaz, doktor yönlendirmesi kapısına düşer); Claude K3'te bile yalnız
KB ilkelerinden konuşur, serbest bilgi eklemez.

#### Özgüven/çaresizlik ailesi (Faz O4)

Faz O3 ölçümünde ikinci bir aile K4'e düşerken yakalandı: **"Yanlış mı yapıyorum
acaba, hiçbir şey yolunda gitmiyor"**. Bu cümlelerde ne metodoloji terimi ne de
klasik pes etme ifadesi ("vazgeç", "bırakıyorum") geçiyor — sözlüğün hiçbir grubu
tutmuyordu. Önemi zamanlamasında: anne *"bırakıyorum"* demeden **önce** bu cümleyi
kuruyor; burada karşılanmazsa müdahale edilecek an kaçırılıyor.

Düzeltme **iki ayrı mekanizmaya** birden yazıldı, çünkü ikisi ayrı iş yapıyor:

| Mekanizma | Ne sağlıyor |
|---|---|
| `MOTIVASYON_TERIMLERI` (sözlük) | Soruyu **alan içi** yapar → K1/K2/K3, K4 değil |
| `_RE_ZORLANMA` (duygu sinyali) | Cevabın **önce anneyi görmesini** zorunlu kılar (ton kuralı) |

Kapsanan kalıplar: "yanlış mı yapıyorum", "doğru mu yapıyorum", "hata mı ediyorum",
"yolunda gitmiyor", "kafam karıştı", "emin değilim", "ne yapacağımı bilmiyorum",
"yetersiz hissediyorum", "beceriksiz", "yeterince iyi değil".

> **Türkçe tuzağı — soru eki cümlenin ortasına giriyor.** `"yanlış yapıyor"` kökü
> `"yanlış MI yapıyorum"`u YAKALAMAZ. Sözlük tarafında ekli ve eksiz biçim ayrı
> ayrı yazılır; regex tarafında `yanlış\s*(mı|mi)?\s*yap` kullanılır.

**Sonuç:** bu cümleler artık **K3**'te cevaplanıyor — yani istenen K3.5 tabanının
bir üstünde, aynı "genel ilkelerden cevapla + netleştirme sorusu sor" davranışıyla.
Retrieval iyi eşleşme bulursa K2/K1'e de çıkabiliyorlar.

**Aşırı genişleme koruması:** aynı kalıp başka bir konuda geçerse K4 kalır
("Kek yaparken yanlış mı yapıyorum" → K4). `_katman_belirle` açık kapsam-dışı
işaretini sözlükten **önce** değerlendiriyor; bu sıralama testle sabitlendi.

Golden-set: `tests/test_kapsama.py` — 10 özgüven cümlesi + 3 aşırı-genişleme
tuzağı, ikisi canlı cevapla doğrulanıyor.

## 6.7 Faz E — duygusal ton ve annenin ruhsal durumu

`/chat` cevapları bilgi verirken İlayda'nın sıcaklığını da taşır. Ton kuralları
`SYSTEM_PROMPT`'ta; ancak duygusal sinyal yakalandığında aynı kural **sorunun
yanına** (user prompt'un kural listesinin BAŞINA) enjekte edilir — yalnız sistem
promptuna bırakıldığında empatik açılış ve somut veri örnekten örneğe düşüyordu.

**Dört kademe** (`ruhsal_durum_tespit` + `duygu_sinyali`):

| Kademe | Tetik | Davranış | `retrieval_layer` |
|---|---|---|---|
| **kriz** | Anne kendine/bebeğine zarar İFADE ediyor (birinci tekil şahıs) | **LLM çağrılmaz.** Sabit destek mesajı + profesyonel yardım. Uyku tekniği ANLATILMAZ, cache'e yazılmaz | `ruhsal_kriz` |
| **sıkıntı** | Derin çaresizlik / tükenmişlik | LLM çağrılır, prompt'a "önce duygusal destek + uzman yönlendirmesi, teknik anlatma" zorunlu eklenir. **K4'e düşmez** (K3'e çekilir) | k1..k3 |
| **ağlama endişesi** | Ağlamanın zararı / güven bağı kaygısı | Empatik açılış + İlayda'nın ŞARTLARI + somut umut verisi (45 dk → 5 dk) zorunlu | k1..k3 |
| **zorlanma** | Yorgunluk, pes etme eşiği | İlk cümle duygusal tanıma, ardından somut yönlendirme | k1..k3 |

> **Kritik ayrım:** zarar **ifadesi** ile zarar **sorusu** aynı şey değildir.
> "bebeğime zarar vereceğimden korkuyorum" → kriz. "ağlamanın bebeğime zararı
> olur mu" → sıradan ağlama sorusu. Kalıp birinci tekil şahıs çekimi zorunlu
> kılar; aksi hâlde en sık sorulan ağlama sorusu kriz kapısına düşer.

**Mutlak iddia yasağı:** "ağlama zarar vermez" MUTLAK olarak kurulmaz. Yalnız
İlayda'nın şartlarıyla verilir — *"tıbbi bir problem ve duygu regülasyon
bozukluğu yoksa genel olarak zarar oluşturmuyor"* + *"teknik olarak kesin bir
ifade kullanılamaz"*. 3-6 haftalık süreç çerçevesi de aynı şartlarla verilir.

**Alan sözlüğü genişledi:** ağlama/güven bağı/motivasyon artık KB'de küratörlü
bir bölüm (`global_rules.aglama_ve_motivasyon`), dolayısıyla bu sorular alan
içidir. Önceden "Üçüncü gündeyiz, bırakmak istiyorum" gibi bir cümle hiçbir
metodoloji terimi içermediği için **K4'e düşüyor** ve tam da motivasyona en çok
ihtiyaç duyan anne kapıdan çevriliyordu.

Test: `tests/test_duygusal_ton.py` (golden-set, senaryo başına 2 canlı örnek).

### Kapsama telemetrisi

`chat_messages` tablosuna `retrieval_layer` (indeksli) ve `top_score` eklendi
(migration `0005`). Cache hit'te ikisi de NULL (retrieval yapılmadı).

> ⚠️ **Kolon uzunluğu (migration `0007`):** `retrieval_layer` başlangıçta
> `String(2)` idi (`k1`..`k4`). Faz E `ruhsal_kriz` (11 karakter) yazmaya
> başlayınca **Postgres `StringDataRightTruncation` fırlatıyor** ve kriz
> anındaki anne destek mesajı yerine 500 alıyordu. **SQLite VARCHAR uzunluğunu
> ZORLAMAZ**, bu yüzden yerel testler bunu görmedi. Kolon `String(32)`'ye
> genişletildi ve `tests/test_kapsama.py` artık üretilen tüm katman adlarının
> kolona sığdığını şema seviyesinde doğruluyor. **Yeni katman adı eklerken bu
> testi kontrol edin.**

### Haftalık kapsama raporu

```bash
python scripts/kapsama_raporu.py --gun 7            # ekrana
python scripts/kapsama_raporu.py --gun 7 --json rapor.json
```

`k3` + `k3_5` satırları **korpusun eksik olduğu yerlerdir** — rapor bunları
konu başlıklarına göre gruplar (gece uyanma, ağlama/motivasyon, beslenme…) ve
örnek soruları listeler. Bu liste İlayda'ya gider ve korpus güncelleme turunun
girdisi olur; **kalıcı çözüm budur, sözlük yamamak değil.**

Rapor `k4`'e düşenleri de ayrı gösterir: aralarında alan içi bir soru varsa bu,
alan sözlüğünün eksik olduğunu gösterir (K3.5 kapısı kaçırmış demektir).

> **KVKK:** rapor soru metnini içerir (eksik konuyu görmenin tek yolu) ama
> `user_id` **hiç girmez**. Dışarı paylaşırken kişisel ayrıntı kontrolü yapılmalı.

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

### `type` — HANGİ EKRAN açılacak (Faz 0-3)

Plan artık tek bir çıktı türü değil. **Mobil `days` doluluğuna değil, `type`
alanına bakmalıdır.**

> **DEĞER SABİTTİR: `"yenidogan_ritim"`.** Bu alanın 0-3 ay değeri
> `yenidogan_ritim`'dir — `uyku_duzenlemesi` DEĞİL. Üretimde çalışan ve
> `/health` üzerinden doğrulanan sürüm bu değeri döndürür; sunucu tarafındaki
> sabit `api/services/plan_service.TYPE_YENIDOGAN`'dır. Mobil bu dizgiyi birebir
> beklemelidir.

| `type` | Ne zaman | `days` | `schedule` | Üretim |
|---|---|---|---|---|
| `egitim_plani` | Düzeltilmiş yaş ≥ 5 ay, eğitim uygun | **dolu** (5 aşama) | dolu | Claude |
| `yenidogan_ritim` | Düzeltilmiş yaş **< 3 ay** | boş | **boş** | deterministik (LLM yok) |
| `egitim_bekleme` | Düzeltilmiş yaş **3-5 ay** (ve doktor onayı gereken durumlar) | **önizleme** (`preview: true`) | dolu | Claude |

> Faz 0-3 öncesi üretilmiş planlarda `type` **yoktur**; alan gelmiyorsa
> `egitim_plani` varsayın (o dönemde tek tür buydu).

### `yenidogan_ritim` — 0-3 ay ritim rehberi

**Bu yaşta uyku eğitimi ÜRETİLMEZ.** 0-3 ayda katı uyku programı veya
yapılandırılmış uyku eğitimi uygulanmaz; 13 günlük merdiven yerine bir **ritim
rehberi** döner. `schedule` ve `days` bilerek boştur, `night_wake_protocol` ve
`kestirme_protokolu` **hiç eklenmez** — ikisi de eğitim protokolüdür.

```jsonc
{
  "type": "yenidogan_ritim",
  "headline": "Deniz için yenidoğan ritim rehberi — 1-2 ay, uyanıklık penceresi 45 dakika - 1 saat 15 dakika",
  "days": [], "schedule": [],
  "yenidogan": {
    "alt_bant": {"id": "1-2_ay", "ad": "1-2 ay", "uyaniklik_penceresi_dk": [45, 75]},
    "uyaniklik_penceresi": "45 dakika - 1 saat 15 dakika",
    "tum_alt_bantlar": [{"ad": "0-1 ay", "uyaniklik_penceresi": "30 dakika - 1 saat"}, ...],
    "uyku_sinyalleri": ["Esneme", "Bakışın donuklaşması, ...", ...],
    "mini_rutin": {"sure": "5-10 dakika", "sure_dk": [5, 10],
                   "sira": ["Alt değiştirme", "Tulum giydirme veya kundaklama",
                            "Ortamı loşlaştırma", "Beyaz gürültüyü açma",
                            "Kucakta sakinleştirme"]},
    "gece_gunduz_ayrimi": {"gunduz": [...], "gece": [...]},
    "guvenli_uyku": {"kaynak": "NHS güvenli uyku rehberi", "kurallar": [...]},
    "ritim_sabitleme": {"baslangic_hafta": [6, 8], "metin": "..."}
  },
  "egitim_baslangic": {"alt_sinir_ay": 5, "kalan_gun": 113,
                       "tahmini_tarih": "2027-01-05", "aciklama": "..."},
  "uygun_mu": false,
  "generated_with": "deterministik",
  "markdown": "..."          // aynı içeriğin metin hâli (eski istemciler için)
}
```

Davranış garantileri:

- **Adaptasyon ÇALIŞMAZ.** `/plans/adapt` bu yaşta `adjusted=false`,
  `shift_minutes=0` ve açıklayıcı bir `reasons` döner; çizelge üretilmez.
  (Üretilseydi rehbere fiilen bir saat programı basılmış olurdu.)
- **Bildirim gitmez** — zamanlayıcı boş `schedule`'dan blok çıkaramaz.
- `GET /plans/today` rehberi her gün **yeniden üretir** (deterministik ve
  ücretsiz), böylece bebek büyüdükçe alt bant 0-1 → 1-2 → 2-3 ay otomatik kayar.
- Bebek 3 ayı geçtiğinde rehber **olduğu gibi kalır** ve
  `"yenidogan_suresi_doldu": true` eklenir. Sunucu kendiliğinden ücretli bir
  eğitim planı üretmez; mobil bu bayrağı görüp kullanıcıya "yeni planınızı
  oluşturalım mı?" kartını göstermeli ve onay gelirse `POST /plans/generate`
  çağırmalıdır (sunucu bayrağı basar, üretimi kullanıcı onayı tetikler).

### `yas_ozel_notlar` — yaşa özel EK bölüm (Faz N-A)

**Yaş istisnası kalktı:** 24 ay ve üzeri çocuklar da artık 13 günlük programa
tabidir (`type: "egitim_plani"`, `days` 5 aşama, `gunler: 13`). Önceden bu yaşta
6 günlük `6_gun_buyuk_cocuk` planı üretiliyor ve ekranda "5/6 gün" görünüyordu.

Kaldırılan planın **içeriği kaybolmadı**: 2 yaş üstünde plana ek bir bölüm
giriyor ve aynı içerik `content.yas_ozel_notlar` altında **yapısal** olarak da
dönüyor.

```jsonc
{
  "type": "egitim_plani",
  "plan_secimi": {"tip": "13_gun_dirençli", "gunler": 13, ...},
  "yas_ozel_notlar": {
    "baslik": "2 Yaş Üstü İçin Ek Öneriler",
    "kb_anahtari": "buyuk_cocuk_24_ay_ustu",
    "alt_yas_ay": 24,
    "maddeler": [
      {"konu": "Bes oyuncak metodu", "metin": "..."},
      {"konu": "Motivasyon panosu",  "metin": "..."},
      {"konu": "Pozitif tesvik",     "metin": "..."},
      {"konu": "Bilincaltina konusma", "metin": "..."}
      // … 17 madde
    ]
  }
}
```

- Düzeltilmiş yaş **24 ayın altındaysa alan `null`** gelir.
- Aynı içerik `content.markdown` içinde de `## 2 Yaş Üstü İçin Ek Öneriler`
  başlıklı bölüm olarak bulunur (eski istemciler için). Bu bölüm `days`
  aşamalarının **dışındadır** — merdivenin yerine geçmez, üstüne eklenir.
- Bölümde **gün numarası geçmez.** Gün↔aşama eşlemesinin tek kaynağı 13 günlük
  merdivendir; eski 6 günlük numaralandırma (ör. "altıncı gün yatır-çık") hem
  plandan hem chat korpusundan çıkarıldı.

### `egitim_bekleme` — eğitim henüz uygun değil (3-5 ay)

Düzeltilmiş yaş **3-5 ay** arasındaki bebekler (ve doktor onayı gereken
durumlar). Yenidoğan rehberi 3 ayda biter, eğitim alt sınırı 5. aydır — bu bant
ikisinin arasıdır.

**Anneye verilen asıl içerik: günlük program + ön hazırlık.** Eğitim merdiveni
de döner ama **ÖNİZLEMEDİR** — bugün uygulanmaz.

```jsonc
{
  "type": "egitim_bekleme",
  "uygun_mu": false,
  "egitim_onizleme": true,              // ← mobil kilitli/soluk göstersin
  "egitim_baslangic": {
    "alt_sinir_ay": 5,
    "tahmini_tarih": "2026-10-14",      // düzeltilmiş yaş 5 ayı doldurduğu gün
    "kalan_gun": 30,
    "aciklama": "Uyku eğitimi düzeltilmiş yaşa göre 5. ayın dolmasıyla başlar. …"
  },
  "schedule": [ /* 5 blok — DOLU, bugün uygulanır */ ],
  "days": [                              // ÖNİZLEME — bugün uygulanmaz
    {"start": 1, "end": 3, "label": "Beşik yanı", "position": "…",
     "markdown": "…", "preview": true},
    {"start": 4, "end": 6, "…": "…", "preview": true}
    // … 5 aşama, hepsinde preview: true
  ],
  "plan_secimi": {
    "tip": "13_gun_dirençli", "gunler": 13,
    "onizleme": true,                    // ← "13 günlük program" rozeti BASMA
    "aciklama": "ÖNİZLEME — bu program bebek 5. ayını doldurduğunda başlayacak; bugün uygulanmaz. …"
  }
  // night_wake_protocol YOK — aşağıya bakın
}
```

**Mobil için kurallar:**

1. **`egitim_onizleme: true` ise eğitim ekranını kilitli/soluk göster.**
   Başlık önerisi: *"5. ayda başlayacak program — önizleme"*. Kullanıcı bugün
   uygulayacağını sanmamalı. `days[].preview` her kayıtta ayrıca işaretlidir.
2. **`plan_secimi.onizleme: true` iken "13 günlük program" rozeti basma.**
   `tip`/`gunler` bilerek korunuyor (5. ayda hangi programın başlayacağı
   görünsün diye) ama bugünün programı değil.
3. **`egitim_baslangic.tahmini_tarih` geri sayım için kullanılabilir.** Bu tarih
   **motordan** gelir ve **düzeltilmiş yaşa** göredir; kendi tarafınızda
   `doğum + 5 ay` hesabı YAPMAYIN — prematürede yanlış olur (ölçüldü: 34
   haftalık bebekte naif hesap **46 gün erken** çıkıyor).
4. **`night_wake_protocol` bu tipte GELMEZ.** 45 dk direnç / 15 dk rutin molası
   bir *eğitim* protokolüdür; eğitime uygun olmayan bebeğe verilmez (yenidoğan
   rehberindeki gerekçenin aynısı). Alanı zorunlu varsaymayın.
5. `schedule` **doludur ve bugün uygulanır** — bu bandın asıl değeri saat
   planlamasıdır.

> **Düzeltilmiş hata (Faz 0-3):** bu durumda plan metninde `## Eğitim Planı`
> bölümü hiç yazılmadığı için `build_days` `DayParseError` yükseltiyor, iki
> denemenin ardından **502** dönüyordu — yani **0-5 ay arasındaki her bebekte
> plan üretimi hata veriyordu**.
>
> **Faz P3 eki:** merdiven artık önizleme olarak ayrıştırılıyor. Ayrıştırma
> başarısız olursa **plan yine verilir** (`days: []`), 502 dönmez — bugün
> uygulanacak bir merdiven olmadığı için önizlemenin eksik kalması planı
> geçersiz kılmaz. `egitim_plani` tipinde ise ayrıştırma hatası hâlâ planı
> reddettirir (eğitim ekranı boş kalmasın).

```jsonc
{
  "headline": "Elif için 9 ay programı — 2 kısa uyku, 20:00 yatış",
  "type": "egitim_plani",
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
  "days": [                 // YENİ — eğitim programının aşamaları (yapısal)
    {"start": 1, "end": 3, "label": "Beşik yanı",
     "position": "Beşik yanı (sandalye veya ayakta)",
     "markdown": "### Gün 1-3: Beşik yanı

<o aşamanın tam metni>"},
    {"start": 4, "end": 6, "label": "Oda ortası", ...},
    {"start": 7, "end": 9, "label": "Kapı", ...},
    {"start": 10, "end": 12, "label": "Kapı eşiği", ...},
    {"start": 13, "end": 13, "label": "Yatır-çık", ...}
  ],
  "markdown": "...",        // KALDI — geriye uyumluluk + detay metni
  "bucket": "9_ay", "adapted": false, ...
}
```

### `days` — eğitim gün bölümleri

**Mobil artık markdown'ı REGEX'LEMEZ.** Gün başlıklarını LLM yazıyor ve biçimi
sabit değildi; aynı backend sürümünden 5 ayrı kalıp ölçüldü (emoji, "Günler",
"1. Gün", `|` ayracı, en-dash, "Gün 1 ve Gün 2") ve eğitim ekranı iki kez
sessizce boş kaldı. Ayrıştırma artık sunucuda, üretim anında yapılır.

Garantiler:
- Aşamaların **tamamı** döner (13 günlük programda 5 aşama) ve `1..gunler`
  arası **her gün tam bir aralığa düşer** — boşluk ya da çakışma yoktur.
- `start`/`end` tamsayıdır ve `start <= end`; tek günlük aşamada ikisi eşittir.
- `position` merdivenin resmî metnidir (KB'den), `label` başlıktaki kısa addır.
- `markdown` o aşamanın kendi metnidir; `content.markdown` bütün plandır ve
  **değişmeden** durur.
- Ayrıştırma başarısız olursa plan KAYDEDİLMEZ: üretim reddedilip yeniden
  ürettirilir (2 deneme, sonra **502**). Boş `days` dönmez.

`days` alanı **`type == "egitim_plani"` olan** planlarda `/plans/generate`,
`/plans/adapt`, `/plans/today`, `GET /plans` ve
`GET /plans/{date}` yanıtlarının hepsinde doludur. Faz O öncesi üretilmiş
planlarda alan yoktu; okuma yolunda markdown'dan bir kez türetilip DB'ye yazılır
(2026-09-08 ölçümü: üretimdeki 357 planın 357'si ayrışıyor). Çok eski bir plan
ayrıştırılamazsa istek **502 olmaz** — `days` alanı gelmez ve sunucu uyarı
loglar; mobil bu durumda `markdown`'a düşmelidir.

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
Elif, 16 aylık, 12-18 ay yaş bandı (kayıtlı başlangıç gece uyanma: 3; eğitim
başlangıcı 2026-07-01; eğitim tamamlanma 2026-07-15). Son 3 gün: bugün şekerleme 1
(12:30-13:15); dün gece yatış 19:05 (planlanan 20:00'den 55dk erken), gece uyanma
1 kez (03:10, 25dk); önceki gün gece yatış 20:30 (planlanan 20:00'den 30dk geç).
Bugünün planı: 07:00 uyanış, 12:30-15:00 uyku, 20:00 yatış.
```

**Blok BEBEK KAYDI VARSA kurulur — log/plan şartı YOKTUR.** Profil satırı (ad, yaş,
yaş bandı) `baby.birth_date`'ten hesaplanır; yaş bandı plan motoruyla aynı kaynaktan
gelir (`data/yas_bantlari.json`, Faz Y). Log ve plan bölümleri opsiyoneldir: veri
yoksa o satırlar yazılmaz, yalnız profil kalır. Henüz logu olmayan bebekte blok şöyle
kısalır (uyku kaydının YOKLUĞU yazılır ki model olmayan saati uydurmasın):

```
BEBEK VERİSİ (bu kullanıcının kendi kaydı):
Deniz, 11 aylık, 9-12 ay yaş bandı. Son 3 günde uyku kaydı girilmemiş.
```

> Eskiden blok **log ya da bugünün planı** varsa kurulurdu. Sonuç: yeni kayıt olan
> anne ilk sorusunda "bebeğinizin adını ve kaç aylık olduğunu yazmanız gerek" cevabı
> alıyordu — oysa iki bilgi de kayıtlıydı. Bloğun tamamı yerine yalnız log/plan
> bölümleri koşullu hale getirildi.

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
- Bağlam bloğu bebek kaydı varsa **her zaman** eklenir; yalnız profil bile
  kurulamıyorsa (adsız ve doğum tarihsiz kayıt) eklenmez → genel metodoloji cevabı.
- `baby_id` verilen her istek cache'i bypass eder; logsuz bebek de buna dahildir
  (bağlam artık her zaman kurulduğu için bypass da her zaman devrede).
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

### Anlatım tonu — iki ses profili (2026-08-25)

Masallar **çok hızlı** okunuyordu: `/voice/generate` ElevenLabs'e `voice_settings`
**hiç göndermiyordu**, yani her şey varsayılan hızdaydı. İki profil tanımlandı
(`tts.SES_PROFILLERI`):

| Profil | speed | stability | similarity_boost | style | Nerede |
|---|---|---|---|---|---|
| `masal` | 0.85 | 0.70 | 0.75 | 0.05 | `/voice/generate` **varsayılanı**, `/clone` örneği |
| `sohbet` | 1.00 | 0.50 | 0.75 | 0.00 | `/ask` TTS'i — ElevenLabs varsayılanlarıyla **aynı** |

`sohbet` profilinin bilerek varsayılanlarla aynı tutulması, mevcut chat ses
cache'inin sessizce geçersizleşmemesi içindir. İstemci `profile` alanıyla profil
seçebilir; geçersiz ad **422** döner.

**Ölçüldü, varsayılmadı** (canlı anahtar, flash v2.5): `speed` 1.0 → 0.85 aynı
cümleyi **%20** uzattı; `<break time="2.0s" />` sesi **+2,25 sn** uzattı. Yani
Flash v2.5 ikisini de uyguluyor, daha pahalı bir modele geçmek gerekmedi.

**Duraklamalar** (`konusma_metni.masal_metni_hazirla`): paragraf araları **0,8 sn**,
cümle araları **0,3 sn**. `konusma_metnine_cevir` paragrafları tek satıra indirdiği
için temizlik **paragraf paragraf** yapılır; etiketler **en sonda** eklenir ki
temizlik kuralları onları bozmasın.

> **Cümle duraklaması neden her masalda yok.** ElevenLabs "tek üretimde çok fazla
> break etiketi kararsızlık yapar (hızlı okuma, gürültü, artefakt)" diye uyarıyor.
> Korpustaki masallar 81–109 cümle → tek istekte ~100 etiket, üstelik etiketler
> karakter başına ücretlendirmeye girdiği için ~%50 fazla maliyet. Kural kendini
> sınırlar: paragraf araları **her zaman**, cümle araları **yalnız** toplam etiket
> `MASAL_MAX_BREAK`(=40) altında kalıyorsa. Pratikte ninniler (5–6 cümle) cümle
> duraklaması alır, uzun masallar almaz; genel tempoyu zaten `speed=0.85` sağlıyor.
> Ölçülen yük: masallarda +%7…+%15, ninnilerde +110…+132 karakter.

**Cache tuzağı:** profil adı ve `SES_AYAR_SURUMU` damgası cache anahtarına
**girer** — `sha256(voice_id || profil || ayar_sürümü || hazır_metin)`. Girmeseydi
kalibrasyondan sonra eski **hızlı okunmuş** MP3 sunulmaya devam eder ve düzeltme
kullanıcıya hiç ulaşmazdı. Ayar değiştirilirse `SES_AYAR_SURUMU` da artırılmalı.

Test: `tests/test_masal_tonu.py` (43 kontrol) — profil değerleri, `voice_settings`in
istek gövdesine gerçekten konduğu, duraklama kuralı, etiket/maliyet freni ve cache
tazelenmesi.

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

`voice_audio()` anahtarı
`sha256(voice_id || profil || ayar_sürümü || hazır_metin)`. Aynı kullanıcı
aynı masalı ikinci kez dinlerken **TTS'e gidilmez** — dosya diskten servis edilir,
`cached: true`, maliyet `0`. Farklı `voice_id` aynı metinde ayrı dosya üretir.

> **Maliyet notu:** 5 masal = **21.124 karakter/kullanıcı** (~**$2.32** @ flash
> v2.5 kredi fiyatı, $0.00011/karakter). Bu **tek seferliktir** — tekrar dinlemeler
> cache'ten gelir. LRU sınırı 500 dosya / 100 MB; Railway'de disk efemer
> olduğundan yeniden deploy sonrası cache boşalır ve ilk dinlemeler yeniden
> üretilir. Kalıcılık isteniyorsa `data/audio_cache` klasörüne volume mount edilmeli.


## Aylık ses klonlama limiti (`POST /voice/clone`)

Gizlilik politikası **"ses kaydı ayda bir kez yenilenebilir"** diyordu ama kodda
hiçbir kontrol yoktu: kullanıcı istediği kadar klon açabiliyordu. Her klon
ElevenLabs'te bir **slot + ücret** tutuyor ve gereksiz **biyometrik veri**
saklanıyordu. Politika ile davranış ayrışmıştı — sınır artık sunucuda zorlanıyor.

**Sınır: son klonlamadan itibaren 30 gün** (`voice_profiles.last_cloned_at`).

### Limit dolduğunda → `429`

```jsonc
// POST /api/v1/voice/clone   → 429 Too Many Requests
// Header: Retry-After: <kalan saniye>
{
  "detail": "Sesini ayda bir kez kaydedebilirsin. Bir sonraki hakkın: 14.10.2026",
  "retry_after_days": 30,
  "next_clone_available_at": "2026-10-14T19:17:03.706553+00:00"
}
```

- Kontrol **ses gövdesi okunmadan ÖNCE** yapılır: 15MB boşuna yüklenmez ve
  ElevenLabs'e **hiç gidilmez** (maliyet oluşmaz).
- `retry_after_days` **yukarı yuvarlanır** (asla 0 olmaz): 0,2 gün kalmışken
  "0 gün" demek kullanıcıya tekrar 429 aldırırdı.

### `GET /voice/voice-status` — genişletildi

```jsonc
{
  "status": "ready",                  // pending | ready | replaced | none
  "voiceId": "voice-2",
  "sampleUrl": "/audio/voice-2.mp3",
  "created_at": "2026-09-14T19:17:03Z",
  "last_cloned_at": "2026-09-14T19:17:03Z",   // YENİ
  "can_clone": false,                          // YENİ
  "next_clone_available_at": "2026-10-14T19:17:03Z",  // YENİ (can_clone=true iken null)
  "retry_after_days": 30                       // YENİ (can_clone=true iken 0)
}
```

**Mobil:** "Sesi yenile" düğmesini `can_clone=false` iken **kapat** ve
`next_clone_available_at`'i göster — kullanıcı 429 duvarına çarpmadan görsün.
Bu alanlar 429 kapısıyla **aynı fonksiyondan** hesaplanır; gösterilen tarih ile
uygulanan sınır ayrışamaz.

### Yeni klon alınınca ESKİ ses siliniyor

Slot, ücret ve biyometrik veri birikmesin diye önceki klon ElevenLabs'ten
silinir ve satır `status: "replaced"` olur.

- Silme **yeni klon kaydedildikten SONRA** yapılır (sıra önemli: önce silinip
  klonlama patlasaydı kullanıcı sessiz kalırdı).
- Silme **best-effort**: başarısız olursa yeni ses **yine geçerlidir**, satır
  `ready` kalır (yalan söylenmez) ve uyarı loglanır.
- ElevenLabs `404` (ses zaten yok) **başarı** sayılır — temizlik idempotent.

> **`410 Gone`:** `replaced` bir `voiceId` ile `POST /voice/generate` çağrılırsa
> artık net bir yanıt döner (`"Bu ses kaydı yenilendiği için artık
> kullanılamıyor…"`). Eskiden anlamsız bir upstream hatası dönerdi.
> Güncel kimliği `/voice/voice-status`'tan alın.

### Geriye uyum

`0009_voice_clone_limit` migration'ı `last_cloned_at`'i **nullable** ekler ve
**backfill ETMEZ**. Limit hesabı `COALESCE(last_cloned_at, created_at)` ile
yürür — yani mevcut kullanıcılara sessizce **fazladan bir klonlama hakkı
doğmaz** (`created_at` zaten klonlamanın yapıldığı andır).

> **Düzeltilen yan hata:** `/voice-status` güncel profili `created_at DESC` ile
> seçiyordu; `created_at` saniye hassasiyetinde yazıldığı için aynı saniyede
> açılmış iki profil berabere kalıp **eski (silinmiş) `voiceId`** dönebiliyordu.
> Limit gelmeden önce peş peşe klon açılabildiğinden üretimde böyle satırlar
> olabilir. Sıralama artık önce `replaced` olmayanı, sonra en yeni klonlama
> anını alıyor.

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

