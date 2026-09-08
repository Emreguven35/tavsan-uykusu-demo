# LiveAvatar (HeyGen) — LITE Mode Entegrasyonu (Hızlı Test)

Kullanıcı mikrofona konuşur → **mevcut backend** cevabı + İlayda MP3'ünü üretir →
**LiveAvatar avatarı** bu sesle dudak senkronlu "konuşur". LiveAvatar **yalnız
görüntü katmanıdır**; LLM / RAG / TTS boru hattımız (`engine.chatbot` + `api.tts`)
**hiç değişmedi**.

> Ayrı test sayfası: `avatar.html`. Mevcut `/ask`, `/audio`, TTS, cevap-cache
> davranışı **aynen** korunur. (Not: repoda bir `index.html` yoktur — proje
> Streamlit + FastAPI'dir; bu yüzden `avatar.html` sıfırdan, bağımsız yazıldı.)

---

## 1) Keşif bulgusu — LITE modda ses avatara NASIL besleniyor?

LiveAvatar LITE mode WebRTC taşımasını **LiveKit** ile yapar. Resmî SDK
(`@heygen/liveavatar-web-sdk`) kaynağını inceledim; **avatarı konuşturmanın LITE
moddaki tek yolu `session.repeatAudio(audio)`** metodudur:

| Yöntem (SDK) | LITE'ta? | Not |
|---|---|---|
| `repeatAudio(pcm)` | ✅ **evet** | `AVATAR_SPEAK_AUDIO` → WS `agent.speak` |
| `repeat(text)` / `message(text)` | ❌ hayır | Kaynakta *"Not permitted in LITE mode"* fırlatır |
| Audio track publish (LiveKit) | dolaylı | Kendi agent'ını kuran ileri senaryo için |

**Ses formatı (KESİN — SDK demosuyla doğrulandı):** `repeatAudio()` girdisi
**BASE64( ham 16-bit signed little-endian PCM, 24 kHz, mono )**'dur. Kanıt: SDK'nın
kendi demosu (`apps/demo/src/liveavatar/useTextChat.ts` + `useAvatarActions.ts`)
ElevenLabs'i `output_format=pcm_24000` ile çağırıp dönen **`audio_base64`** alanını
**doğrudan** `session.repeatAudio(audio)`'ya veriyor. SDK bu base64 dizgesini
`splitPcm24kStringToChunks()` ile parçalayıp WebSocket'e
`{"type":"agent.speak","audio":<base64 chunk>}` + kapanışta `agent.speak_end` yollar;
sunucu parçaları birleştirip base64 çözer.

**Bizim akış:** İlayda MP3'ünü tarayıcıda `OfflineAudioContext` ile 24 kHz mono'ya
indirip **Float32 → Int16LE → base64** (`avatar_audio.js › pcmToSpeakString`)
çevirerek `session.repeatAudio()`'ya veriyoruz. Avatar sesi kendi audio track'inde
geri döner (`attach(video)` ile duyulur), video dudak senkronludur.

> ### 🔧 DÜZELTME (canlı test regresyonu)
> **Belirti:** oturum açıldı, soru-cevap çalıştı ama `repeatAudio` avatarı
> konuşturmadı; fallback `<audio>` devreye girdi (ses duyuldu, dudak oynamadı).
>
> **Kök neden:** İlk sürüm `repeatAudio()`'ya **ham "binary string"** (1 char = 1
> bayt) veriyordu. Doğru sözleşme **base64**. Ham binary string JSON/WebSocket
> üzerinden gönderilince 0x80–0xFF baytları **UTF-8'e kaçıyor**, sunucunun base64
> çözümü bozuluyor → geçerli ses üretilemiyor → `AVATAR_SPEAK_STARTED` hiç gelmiyor
> → timeout → fallback. (base64 yalnız ASCII `[A-Za-z0-9+/=]` içerdiği için bu
> sorundan bağışıktır; bkz. `test_avatar_audio.mjs` test 8.)
>
> **Çözüm:** dönüşüm `Float32→Int16LE→**base64**`'e çevrildi. Format (24 kHz / 16-bit
> LE / mono) zaten doğruydu; yalnız **base64 sarmalaması** eksikti.

**Fallback (sessiz kalma yok):** `repeatAudio` hata verirse **veya**
`AVATAR_SPEAK_STARTED` olayı `SPEAK_TIMEOUT_MS` (4 sn) içinde gelmezse, sayfa normal
`<audio>` ile İlayda MP3'ünü çalar ve fallback'e düşüş **NEDENİYLE** hem ekrana
(uyarı) hem console'a loglanır (örn. `↩ FALLBACK <audio>: timeout: avatar 4 sn
içinde konuşmaya başlamadı` veya `repeatAudio hata: …`).

**Debug — Yöntem A/B:** avatar.html'de iki düğme, son cevabın sesini iki formatla
yeniden besler: **A = base64** (doğru), **B = binary** (eski/yanlış). Canlı testte
tek denemede hangisinin avatarı konuşturduğu görülür.

---

## 2) Sandbox (kredi yakmadan test)

- Token isteğinde `is_sandbox: true` → **kredi tüketmez**.
- Kısıt: **yalnız "Wayne" avatarı** (`dd73ea75-1218-4ef3-92ce-606d5f7fbc0a`) ve oturum
  **~1 dk** sonra sunucuda otomatik kapanır.
- Backend varsayılanı **güvenli**: `LIVEAVATAR_SANDBOX` tanımsız → **sandbox açık**.
  Geliştirme/entegrasyon testleri sandbox'ta yapılır; gerçek kredi son doğrulamaya saklanır.

Gerçek **İlayda** avatarıyla canlı test için `.env`:
```
LIVEAVATAR_SANDBOX=false
LIVEAVATAR_AVATAR_ID=<İlayda avatar id>
```

---

## 3) Local'de çalıştırma

**Terminal A — backend (mevcut):**
```bash
pip install -r requirements.txt          # değişmedi
# .env: LIVEAVATAR_API_KEY dolu; LIVEAVATAR_SANDBOX=true (varsayılan, ücretsiz)
uvicorn api.main:app --host 0.0.0.0 --port 8000
# → http://localhost:8000/health
```

**Terminal B — statik sayfa (CORS/`file://` sorunlarından kaçınmak için):**
```bash
python -m http.server 5500
# tarayıcıda: http://localhost:5500/avatar.html   (Chrome veya Edge — Web Speech API)
```
> `avatar.html` içinde `API_URL = "http://localhost:8000"` sabittir; backend farklı
> host/porttaysa burayı değiştirin. CORS: backend `ALLOWED_ORIGINS` default `*`.

**Test adımları / nelere bakılmalı:**
1. **"Avatarı Başlat"** → durum "oturum AÇIK", videoda (sandbox'ta Wayne) avatar görünür.
   Üstte mod etiketi: `LITE · SANDBOX (ücretsiz) · avatar dd73ea75…`.
2. **"🎤 Konuş"** → Türkçe soru söyleyin (örn. *"Beyaz gürültü zararlı mı?"*).
   - Log'da `STT: …` → `PCM üretildi (… bayt) → repeatAudio()` görünmeli.
   - Cevap **balonda** yazılmalı.
   - Avatar **dudak senkronlu konuşmalı** (log: *"avatar konuşmaya başladı"*).
   - Konuşmazsa: sarı **"Avatar senkron başarısız"** uyarısı + ses normal çalar (fallback).
3. **Sayaç**: "süre: Ns (oto-kapanış Ms)". **90 sn**'de oturum otomatik kapanır →
   *"oturum yenilendi (kredi korundu)"*; yeni soruda otomatik yeni oturum açılır.
   (Sandbox'ta sunucu ~60 sn'de zaten kapatır → `SESSION_DISCONNECTED` ile UI güncellenir.)
4. **"Oturumu Kapat"** → `session.stop()`, kredi sayacı durur, durum "oturum kapalı".

> **⚠ Canlı oturumu (SANDBOX=false) bu repoda AÇMADIM — kredi sınırlı.** Gerçek
> İlayda avatarıyla son doğrulamayı **kullanıcı** yapacak: yukarıdaki `.env`
> değişikliği + aynı adımlar. Sandbox akışı geliştirme için yeterlidir.

---

## 4) Backend endpoint

`POST /avatar-session` → LiveAvatar `/v1/sessions/token`'a `X-API-KEY` ile gider,
LITE token üretir:
```json
{ "session_token":"…", "session_id":"…", "avatar_id":"…", "is_sandbox":true, "mode":"LITE" }
```
- **API key ASLA** frontend'e / log'a / hata mesajına gitmez (yalnız kısa ömürlü token).
- Hata: key yok → **500**; kota/ağ/upstream → **502**; her ikisi de **anlamlı JSON**
  (`{"detail":"…"}`), ham çökme yok.
- Yeni CORS middleware yok — mevcut `ALLOWED_ORIGINS` ayarı geçerli.

---

## 5) Testler / regresyon

```bash
# Windows konsolunda Türkçe/ok karakteri için:  export PYTHONIOENCODING=utf-8
node   test_avatar_audio.mjs    # YENİ — PCM/format birim testi (base64/24k/16-bit LE), 9/9
python test_avatar_session.py   # YENİ — /avatar-session, upstream mock'lu, 7/7
python test_api.py              # mevcut — 7/7 (regresyonsuz)
python test_konusma_metni.py    # mevcut — 14/14
```
`test_avatar_audio.mjs` (Node) `avatar_audio.js`'i doğrular: 24 kHz/16-bit/mono
sabitleri, n örnek→n*2 bayt, Int16 **little-endian** değerleri, ±1 clamp, 1 sn→48000
bayt, **base64 round-trip**, base64 uzunluğu, ve **kök-neden guard'ı** (base64
ASCII-güvenli, binary değil). MP3→Float32 çözümü tarayıcı işi (Web Audio) olduğundan
canlı testte doğrulanır.

`test_avatar_session.py` senaryoları: sandbox default (Wayne), başarı, key yok (500),
kota (502), sandbox=false+avatar yok (500), sandbox=false+avatar var, **API key
sızmıyor** (upstream `X-API-KEY` header'ında, response'ta yok).

---

## 5b) Sorun Giderme (ses → dudak senkron)

Avatar konuşmuyorsa **F12 → Console** ve sayfadaki log kutusuna bakın. Akış sağlıklıysa
şu sırayı görürsünüz:
`token alındı` → `stream hazır → video attach` → `SESSION_STATE_CHANGED: CONNECTED`
→ (soru) → `ses hazır: N örnek @24000Hz mono → base64 …` → `repeatAudio çağrıldı`
→ `✅ AVATAR_SPEAK_STARTED` → dudaklar oynar.

| Belirti (log) | Olası neden | Çözüm |
|---|---|---|
| `↩ FALLBACK: timeout: avatar 4 sn içinde konuşmaya başlamadı` ama **base64** kullanıldı | Sunucu sesi/ws farklı; ya da avatar audio track sessiz | **Yöntem A/B** düğmeleriyle dene; hâlâ olmuyorsa aşağıdaki maddeler |
| `↩ FALLBACK: timeout` ve **binary** kullanıldı | Yanlış format (eski hata) | Varsayılan zaten base64; `SPEAK_ENCODING="base64"` olduğundan emin olun |
| `repeatAudio hata: … supported mode` / `Session needs to be connected` | LITE oturumda **WebSocket yok** (`ws_url` null) — `repeatAudio` WS ister | Token yanıtında ws desteği gerekiyor; sandbox yerine gerçek avatar/plan deneyin |
| `repeatAudio hata: oturum durumu CONNECTING…` | Ses **erken** beslendi | `SESSION_STATE_CHANGED: CONNECTED` beklenmeli (kod bunu kontrol eder) |
| Avatar dudakları oynuyor ama **ses yok** | Avatar audio track sessiz | `avatar.html` içinde `AVATAR_PROVIDES_AUDIO=false` → fallback sesi hep açılır |
| `avatar-session` 502 / `Insufficient credits` | Kota/kredi | Backend `LIVEAVATAR_SANDBOX=true` (ücretsiz) ile test edin |

**Yöntem A/B ile 1 denemede teşhis:** bir soru sorun (otomatik base64 çalışır), sonra
**Yöntem B: binary**'ye basın — B'de avatar susup A'da konuşuyorsa kök neden kesinleşir
(format = base64).

> Not: `test_api.py`/`test_avatar_session.py` çıktı yazdırırken `→`/Türkçe karakter
> için Windows'ta `PYTHONIOENCODING=utf-8` gerekir (bu **mevcut** bir konsol
> davranışı; test mantığı geçiyor).

---

## 6) Rapor özeti

**Ses besleme yöntemi (düzeltilmiş):** LITE mode → `session.repeatAudio(speakStr)`;
`speakStr = BASE64(PCM16 / 24 kHz / mono)`. MP3→PCM dönüşümü tarayıcıda
`OfflineAudioContext` ile; kodlama `avatar_audio.js › pcmToSpeakString` (base64).
SDK bunu WS `agent.speak` parçalarına böler. (Ayrıntı: §1.)

**Kök neden adayları (teşhis) ve sonuç:**
- **(a) base64 vs binary** — ✅ **GERÇEK KÖK NEDEN.** SDK demosu `repeatAudio`'ya
  ElevenLabs `audio_base64`'ü doğrudan veriyor → base64 gerekiyordu; biz binary string
  gönderiyorduk. WS/JSON'da 0x80+ baytları UTF-8'e kaçıp base64 çözümünü bozuyordu.
- **(b) örnekleme/derinlik** — elenmiş: 24 kHz / 16-bit LE / mono zaten doğruydu
  (`pcm_24000` ile aynı); `test_avatar_audio.mjs` doğruluyor.
- **(c) chunk boyutu/tempo** — elenmiş: parçalama SDK'nın içinde (`splitPcm24kStringToChunks`);
  bizim işimiz tek doğru dizgeyi vermek.
- **(d) erken çağrı/state** — savunmaya alındı: `speakOnAvatar` artık
  `state === CONNECTED` kontrol ediyor; WS yoksa (`ws_url` null) `repeatAudio` fırlatır
  ve **neden** loglanır (bkz. Sorun Giderme).

**Yapılan değişiklik:** `avatar_audio.js` (yeni, paylaşılan format modülü) —
`Float32→Int16LE→base64`. `avatar.html` bunu kullanır (base64), tüm SDK event'lerini
console'a bağlar, fallback'e düşüşü **nedeniyle** loglar, ve **Yöntem A/B** debug
düğmeleri ekler. `test_avatar_audio.mjs` (yeni) formatı 9/9 doğrular.

**Oluşan / değişen dosyalar:**
- `api/avatar.py` *(yeni)* — token üretimi (sandbox-varsayılan, key gizli, graceful hata).
- `api/main.py` *(±)* — `POST /avatar-session` + `avatar` import (2 satır).
- `.env.example` *(±)* — `LIVEAVATAR_API_KEY` / `LIVEAVATAR_AVATAR_ID` / `LIVEAVATAR_SANDBOX`.
- `avatar.html` *(yeni→düzeltildi)* — base64 besleme + A/B + event logları + fallback nedeni.
- `avatar_audio.js` *(yeni)* — paylaşılan PCM/base64 format modülü (tek doğru kaynak).
- `test_avatar_session.py` *(yeni)* — 7 senaryo, upstream mock.
- `test_avatar_audio.mjs` *(yeni)* — PCM/format birim testi, 9/9.
- `README_AVATAR.md` *(yeni→güncellendi)* — bu doküman + Sorun Giderme.

**Commit hash'leri (branch `feature/liveavatar-lite`):**
- `5e79c2e` — feat(avatar): backend token endpoint + test
- `96959ed` — feat(avatar): avatar.html test sayfası (ilk sürüm — binary)
- `f62e959` — docs(avatar): README_AVATAR.md ilk sürüm
- `271fe25` — fix(avatar): repeatAudio **base64** düzeltmesi (kök neden) + A/B + birim test

**Geri alma (rollback):**
```bash
# Tüm özelliği geri al (main'e hiç dokunulmadı; merge/push YOK):
git checkout main
git branch -D feature/liveavatar-lite          # branch'i komple sil
# — veya tek commit geri almak için:
git revert <hash>
```
`main` bu iş boyunca **değişmedi**; feature branch'i silmek her şeyi eski haline getirir.

**Bilinen sınırlar / varsayımlar:**
- **Sandbox:** yalnız Wayne avatarı, oturum ~1 dk, watermark olabilir; gerçek İlayda
  için `LIVEAVATAR_SANDBOX=false`.
- **Free plan:** oturum max 2 dk → sayfa 90 sn'de proaktif kapatır (kredi disiplini).
- **Ses formatı:** çözüldü → `repeatAudio` = **base64**(PCM16/24k/mono) (§1, düzeltme).
  Avatar dudak oynatıp **ses** çıkmıyorsa `AVATAR_PROVIDES_AUDIO=false` yapıp fallback
  sesini her zaman açın. Yöntem A/B düğmeleri canlı doğrulama için.
- **LITE + WebSocket:** `repeatAudio` bir WS bağlantısı (`ws_url`) ister; token yanıtı
  ws desteği vermezse metod fırlatır (neden loglanır). Gerçek avatar/planla ws beklenir.
- **Web SDK CDN:** `esm.sh` üzerinden yüklenir (200 + export'lar doğrulandı). İnternet
  yoksa / offline istenirse: `pnpm i @heygen/liveavatar-web-sdk` + local ESM'e geçin.
- **STT:** Web Speech API yalnız Chrome/Edge'de sağlıklı (tr-TR).
- **Canlı E2E testi yapılmadı** (kredi + mikrofon + gerçek avatar gerektirir) — §3'teki
  talimatla kullanıcı doğrulayacak.
