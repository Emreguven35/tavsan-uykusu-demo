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

**Ses formatı (kesin):** `repeatAudio()` girdisi **ham 16-bit signed PCM, 24 kHz,
mono**'dur ve SDK bunu bir **"binary string"** olarak bekler (1 karakter = 1 bayt).
SDK içindeki `splitPcm24kStringToChunks()` diziyi **ham PCM bayt sınırlarına** göre
böler (ilk parça 400 ms = 19200 bayt, sonrası 1 s = 48000 bayt) ve her parçayı
WebSocket'e `{"type":"agent.speak","audio":<chunk>}` + kapanışta `agent.speak_end`
olarak gönderir.

**Bizim akış:** İlayda MP3'ünü tarayıcıda `OfflineAudioContext` ile 24 kHz mono'ya
indirip Float32→Int16LE→binary string'e çeviriyoruz, sonra `session.repeatAudio()`.
Avatar sesi kendi audio track'inde geri döner (`attach(video)` ile duyulur), video
dudak senkronludur.

> **⚠ Açık varsayım (doğrulanacak):** Resmî dokümanın *lifecycle* sayfası ham
> WebSocket protokolü için sesi **"PCM 16-bit 24 kHz, Base64-encoded"** diye
> tanımlıyor. Ancak **SDK'nın `repeatAudio()`'su base64 DEĞİL, ham binary string
> bekliyor** (parça boyutları ham PCM baytı olarak hesaplandığı için base64 ile
> zamanlama bozulurdu). Biz SDK sözleşmesine uyduk (binary string). Sunucu bu
> alanda base64 isterse `avatar.html` içindeki `mp3UrlToPcm24kString()` sonuna tek
> satır `btoa(out)` eklemek yeter — ama beklenen davranış binary string'tir.
> Bu nedenle **fallback** (aşağıda) her ihtimale karşı devrede.

**Fallback (sessiz kalma yok):** `repeatAudio` hata verirse **veya**
`AVATAR_SPEAK_STARTED` olayı `SPEAK_TIMEOUT_MS` (6 sn) içinde gelmezse, sayfa normal
`<audio>` ile İlayda MP3'ünü çalar ve **"Avatar senkron başarısız"** uyarısı gösterir.

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
python test_avatar_session.py   # YENİ — /avatar-session, upstream mock'lu, 7/7
python test_api.py              # mevcut — 7/7 (regresyonsuz)
python test_konusma_metni.py    # mevcut — 14/14
```
`test_avatar_session.py` senaryoları: sandbox default (Wayne), başarı, key yok (500),
kota (502), sandbox=false+avatar yok (500), sandbox=false+avatar var, **API key
sızmıyor** (upstream `X-API-KEY` header'ında, response'ta yok).

> Not: `test_api.py`/`test_avatar_session.py` çıktı yazdırırken `→`/Türkçe karakter
> için Windows'ta `PYTHONIOENCODING=utf-8` gerekir (bu **mevcut** bir konsol
> davranışı; test mantığı geçiyor).

---

## 6) Rapor özeti

**Ses besleme yöntemi:** LITE mode → `session.repeatAudio(pcmString)`; girdi ham
PCM16/24 kHz/mono binary string; SDK WS `agent.speak` parçalarına böler. MP3→PCM
dönüşümü tarayıcıda `OfflineAudioContext` ile. (Base64 vs binary belirsizliği ve
fallback için bkz. §1.)

**Oluşan / değişen dosyalar:**
- `api/avatar.py` *(yeni)* — token üretimi (sandbox-varsayılan, key gizli, graceful hata).
- `api/main.py` *(±)* — `POST /avatar-session` + `avatar` import (2 satır).
- `.env.example` *(±)* — `LIVEAVATAR_API_KEY` / `LIVEAVATAR_AVATAR_ID` / `LIVEAVATAR_SANDBOX`.
- `avatar.html` *(yeni)* — bağımsız test sayfası (index.html'e dokunulmadı; yok zaten).
- `test_avatar_session.py` *(yeni)* — 7 senaryo, upstream mock.
- `README_AVATAR.md` *(yeni)* — bu doküman.

**Commit hash'leri (branch `feature/liveavatar-lite`):**
- `5e79c2e` — feat(avatar): backend token endpoint + test
- `96959ed` — feat(avatar): avatar.html test sayfası
- *(bu doküman ayrı commit; `git log` ile en güncel hash)*

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
- **Ses formatı:** `repeatAudio` base64 mı binary mi belirsizliği (§1) — binary
  seçildi, fallback var. Canlı testte avatar sessiz "konuşuyorsa"
  `AVATAR_PROVIDES_AUDIO=false` yapıp fallback sesini her zaman açın.
- **Web SDK CDN:** `esm.sh` üzerinden yüklenir (200 + export'lar doğrulandı). İnternet
  yoksa / offline istenirse: `pnpm i @heygen/liveavatar-web-sdk` + local ESM'e geçin.
- **STT:** Web Speech API yalnız Chrome/Edge'de sağlıklı (tr-TR).
- **Canlı E2E testi yapılmadı** (kredi + mikrofon + gerçek avatar gerektirir) — §3'teki
  talimatla kullanıcı doğrulayacak.
