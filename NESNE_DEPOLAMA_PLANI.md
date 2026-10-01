# /data → Cloudflare R2 geçiş planı · 2026-10-01 (UYGULANMADI)

**Amaç:** (1) kesintisiz deploy, (2) medya için CDN.

**Neden:** servise bağlı `/data` volume'u yüzünden Railway eski konteyneri yenisi
hazır olmadan durduruyor. `overlapSeconds` etkisiz kaldı; ölçülen kesinti deploy
başına 10–23 sn (v2.6.0, v2.6.1, v2.6.2). Volume kalkınca örtüşme çalışır.

## 1. Envanter (prod, 2026-10-01 ölçümü)

| Klasör | Boyut | Dosya | Erişim | Gizlilik | Yazan | Okuyan / sunan |
|---|---|---|---|---|---|---|
| `media/videos` | 372 MB | 28 mp4 | `GET /media/videos/{slug}.mp4`, Range | public | `scripts/video_yukle.py` (ssh + base64) | `api/routers/media.py:122` |
| `media/sounds` | 99 MB | 14 m4a | `GET /media/sounds/{slug}.m4a`, Range | public | `scripts/ses_yukle.py` | `api/routers/media.py:137` |
| `media/posters` | 2,1 MB | 28 jpg | `GET /media/posters/{slug}.jpg` | public | `scripts/video_yukle.py` | `api/routers/media.py:129` |
| `media/voice-audio/{user}/{profil}/` | 54 MB | 32 mp3 (8 kullanıcı) | imzalı `GET /media/{yol}?exp&sig` (1 saat) | **ÖZEL** (anne sesi, biyometrik türevi) | `api/services/voice_uretim.py:138-146` → `depo().yaz` | `api/main.py:450-466`; silme `voice.py:265`, `voice_temizlik.py:115` |
| `media/genel-sesler` | 0,3 MB | 1 mp3 | imzalı (aynı uç) | kişisel değil | `api/services/genel_ses.py:45` | `genel_ses.py:33`, `api/main.py:450` |
| `denetim` | 0,3 MB | 7 html | imzalı `GET /denetim/{tarih}.html` (7 gün) | iç | `api/services/denetim.py:42-62, 466` | `api/routers/denetim.py:30` |
| `arsiv` | 24 KB | 3 json | yok (bakım) | iç | `scripts/acik_sayac_kapat.py:257`, `scripts/topluluk_ekip_duzenleme.py:144` | elle |

**Toplam yaklaşık 530 MB.** Volume'da olmayanlar: `api/tts.py` `audio_cache` ve
sohbet önbelleği `/app/data` altında. Bunlar konteynerin geçici diskinde; her
deploy'da zaten sıfırlanıyorlar ve geçişi etkilemiyorlar.

## 2. Hedef yapı

İki kova, ikisi de **EU yetki alanında** (KVKK; bugünkü Railway US-West'ten de iyi):

- **`tavsan-medya`** (public, özel alan adı + Cloudflare CDN, örn. `medya.<alan-adi>`):
  `videos/`, `sounds/`, `posters/`. Dosya adı slug'a sabit ve içerik değişmez;
  `Cache-Control: public, max-age=31536000, immutable`.
- **`tavsan-ozel`** (private, yalnız ön-imzalı S3 bağlantısı): `voice-audio/`,
  `genel-sesler/`, `denetim/`, `arsiv/`.

## 3. Kod değişiklikleri

Soyutlama hazır: yazma, okuma ve silme `storage.depo()` üzerinden geçiyor.

1. **`api/services/storage.py`**
   - `R2Depo` eklenecek (boto3 S3 istemcisi, endpoint `https://<hesap>.r2.cloudflarestorage.com`): `yaz`, `oku`, `var_mi`, `sil`, `klasor_sil`, `boyut`.
   - `depo()`: `MEDIA_PROVIDER=r2` ise `R2Depo`, değilse `YerelDepo`.
   - `video_url`, `poster_url`, `uyku_sesi_url`: `MEDIA_PUBLIC_BASE` tanımlıysa mutlak CDN adresi.
   - `imzali_url`: R2'de 1 saatlik S3 ön-imzalı bağlantı.
   - `yerel_dosya`: R2'de zaten `None` dönecek şekilde tasarlanmış.
2. **`api/routers/media.py:49, 122, 129, 137`** — R2'de dosyayı akıtmak yerine CDN adresine **302** yönlendirme. Eski build'ler ve cihazda saklanan göreli `/media/...` adresleri çalışmaya devam eder.
3. **`api/main.py:450-466`** (`/media/{yol}` imzalı) — önce bizim imzamız doğrulanacak, sonra R2 ön-imzalı adrese 302 yönlendirme. Eski imzalı bağlantılar 1 saat içinde zaten ölüyor.
4. **`api/services/denetim.py:42-62, 466`** ve **`api/routers/denetim.py:30`** — HTML `depo().yaz/oku` ile `denetim/{tarih}.html` yoluna yazılıp okunacak.
5. **`scripts/acik_sayac_kapat.py:257`**, **`scripts/topluluk_ekip_duzenleme.py:144`** — arşiv `depo().yaz("arsiv/…")` ile yazılacak.
6. **`scripts/video_yukle.py`**, **`scripts/ses_yukle.py`** — `railway ssh` + base64 (~2,5 MB/dk) yerine yerelden doğrudan R2'ye S3 yüklemesi; dakikalar yerine saniyeler.
7. **Değişmeyenler:** `api/services/education.py:322-323`, `sounds.py:83`, `voice_uretim.py`, `voice_temizlik.py`, `voice.py`, `genel_ses.py` hepsi `storage` üzerinden gidiyor.
8. **`requirements.txt`:** `boto3`.
9. **Testler:** `R2Depo` için moto/sahte S3 ile birim testi; medya ve imzalı uç yönlendirme testleri; `test_voice_*`, `test_egitim_videolari` yeni sağlayıcıyla.

**Mobil:** değişiklik gerekmiyor. Ses ve video adreslerini `http` ile başlıyorsa
olduğu gibi kullanıyor (`lib/audio/audio-url.ts`, `app/education/video/[id].tsx:353`);
backend zaten mutlak adres döndürüyor.

## 4. Geçiş adımları (kesintisiz)

1. **Kod deploy'u** (`MEDIA_PROVIDER` tanımsız): davranış değişmez.
2. **Kopyalama:** konteyner içinden tek seferlik betikle `/data` → R2 (boto3, aynı yollar). Yaklaşık 530 MB, birkaç dakika; `--kuru` ile önce say ve karşılaştır (adet + boyut + md5).
3. **Geçiş:** `MEDIA_PROVIDER=r2`, `MEDIA_PUBLIC_BASE=https://medya.<alan-adi>` env'leri. Railway yeniden başlatır.
4. **Doğrulama:** video Range isteği, uyku sesi, anne sesi imzalı bağlantısı, denetim sayfası, ses paketi üretimi ve silme uçtan uca.
5. **Bir hafta bekle.** Volume dokunulmadan duruyor ve geri dönüş tek env değişikliği.
6. **Volume'u ayır** (Railway panel; son bir yedek arşiv alındıktan sonra).
7. **Kesinti ölçümü:** sonraki gece deploy'u `kesinti_olc` ile ölçülür; hedef 0 beklenmeyen yanıt.

## 5. Benden gerekenler (hesap bilgileri)

1. **Cloudflare hesabı ve R2'nin etkinleştirilmesi.** Ücretsiz katman için bile ödeme yöntemi tanımlı olmalı.
2. **Alan adı Cloudflare DNS'inde mi?** Public kovaya özel alt alan adı bağlamak için gerekli. `r2.dev` adresi üretim için önerilmiyor (hız sınırlı, CDN önbelleği yok).
3. **R2 hesap kimliği (Account ID).**
4. **R2 API token'ı:** Access Key ID + Secret, yalnız bu iki kovada "Object Read & Write". Railway env'e girilecek, ben görmek zorunda değilim.
5. **Kova adları ve EU yetki alanı onayı.**
6. **KVKK:** anne sesi dosyalarının Cloudflare'de (EU) tutulması aydınlatma metnine yansıtılmalı. Bugün zaten yurt dışında (Railway US).

## 6. Süre ve maliyet

- **Süre:** geliştirme ve test ~1 gün, kopya ve geçiş ~1 saat (gece), gözlem 1 hafta, volume ayırma 10 dk.
- **Aylık maliyet:**
  - **R2: ~0 $.** Depolama 0,53 GB (ilk 10 GB ücretsiz). İstek sayısı ayda ~15–20 bin GET (ilk 10 milyon ücretsiz). **Çıkış trafiği ücretsiz.**
  - **Railway'den düşen:** volume (~0,05–0,10 $/ay) ve medya çıkış trafiği (~0,15–0,5 $/ay). Tasarruf küçük; asıl kazanç kesintisiz deploy ve CDN.
- **Hız:** medya Cloudflare'in İstanbul POP'undan sunulur. Bugün Railway US-West'ten geliyor; 372 MB'lik videolar için gecikme ve ilk bayt süresi belirgin iyileşir.
