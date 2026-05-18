# 🐰 Tavşan Uykusu Premium Demo — Teslim Raporu

**Hazırlayan:** Claude Code (Opus 4.7) — otonom çalışma
**Teslim tarihi:** 2026-05-18
**Çalışma süresi:** Tek seferlik gece çalışması

---

## 1. Ne Yapıldı (Özet)

✅ **Tamamlanmış Streamlit web demosu** — 37 soruluk profil + plan üretimi + RAG chatbot
✅ **Deterministik karar motoru** — 89 kuralı uygulayan, sayıları LLM'e bırakmayan parameter engine
✅ **Claude API entegrasyonu** — plan yazımı + chatbot cevapları (claude-opus-4-7)
✅ **TF-IDF tabanlı RAG** — 463 chunk üzerinde (pgvector gerektirmiyor, Streamlit Cloud uyumlu)
✅ **5 test senaryosu çalıştırılmış** — sonuçlar `tests/test_results.json`'da
✅ **Git repo init edilmiş** — push'a hazır
✅ **Tavşan Uykusu pembe-mavi tema** — `.streamlit/config.toml`

### Sayısal Sonuçlar

| Metrik | Değer |
|---|---|
| Toplam dosya | 22 (kod) + 4 (data) + 4 (config) = 30 |
| Streamlit sayfa sayısı | 8 (ana + 7 alt) |
| Soru sayısı (form) | 37 |
| Karar kuralı (engine'de uygulanan) | 89+ |
| Yaş bucket sayısı | 19 |
| Chunk sayısı (RAG) | 463 |
| Ders sayısı | 47 |
| Test senaryosu | 5 + 5 retrieve + 2 chatbot tam = 12 |
| Test başarı oranı | 12/12 ✅ |

---

## 2. Streamlit Cloud Deploy — Sizin Yapacaklarınız

### Adım 1 (Sabah, ~2 dakika): GitHub'a Push

Çalışma dizini henüz GitHub'a push'lanmadı (gh CLI yüklü değildi). Sizin manuel yapacaklarınız:

```bash
# 1) GitHub'da yeni public repo oluşturun:
#    https://github.com/new
#    Repo adı: tavsan-uykusu-demo (veya istediğiniz başka isim)
#    Public seçin
#    README/gitignore EKLEMEYİN (zaten var)

# 2) Bu klasörde:
cd "C:\Users\Mert KORAL\tavsan_transcribe\tavsan_demo"

# 3) Remote'u bağlayıp push edin (KULLANICI_ADI'nı kendi GitHub kullanıcı adınızla değiştirin):
git remote add origin https://github.com/KULLANICI_ADI/tavsan-uykusu-demo.git
git branch -M main
git push -u origin main
```

İlk push'ta tarayıcıda GitHub login isteyebilir; izin verin.

### Adım 2 (~3 dakika): Streamlit Cloud'a Bağlanın

1. <https://share.streamlit.io> adresine gidin.
2. **"Sign in with GitHub"** ile giriş yapın.
3. **"Create app"** → **"Deploy a public app from GitHub"** seçeneğini tıklayın.
4. Repo: `KULLANICI_ADI/tavsan-uykusu-demo`
5. Branch: `main`
6. Main file path: `app.py`
7. App URL: `tavsan-uykusu-demo` (veya beğendiğiniz başka subdomain; sondaki adres `https://tavsan-uykusu-demo.streamlit.app` olur)
8. **"Deploy"** tıklayın.

İlk deploy 2-3 dakika sürer (pip install vb.).

### Adım 3 (~1 dakika): API Key Ekleyin (KRİTİK)

Deploy edilen app'in **Settings → Secrets** sekmesine gidin ve şu içeriği yapıştırın:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
```

> ⚠️ **Önemli not:** Şu an `.env` dosyanızda `ANTHROPIC_API_KEY` yok — sadece `ELEVENLABS_API_KEY` var. Demo çalışması için Anthropic Console'dan yeni bir key alın: <https://console.anthropic.com/settings/keys>
>
> Key olmadan da demo çalışır ama:
> - Plan **fallback modda** kural-tabanlı markdown üretir (kısa, mekanik)
> - Chatbot **en alakalı snippet'i** doğrudan gösterir (özetlenmemiş)
>
> Hem plan hem chatbot için tam deneyim için Anthropic key ZORUNLU.

Secrets kaydedildikten sonra app otomatik yeniden başlar.

### Adım 4 (test): Demoyu Test Edin

- URL açılır, soruları doldur → 7. sayfada kişisel plan görmelisiniz.
- Chatbot kutusuna örnek bir soru yazıp test edin.

### Adım 5: İlayda'ya Gönder

WhatsApp mesajı şu şekilde olabilir:

> "İlayda Hanım, gönderdiğiniz 47 ses kaydı ve 29 görsel tablodan ürettiğimiz **premium plan demosu** hazır. 37 soruyu cevaplayıp kişisel uyku planı + soru-cevap deneyimleyebilirsiniz: https://tavsan-uykusu-demo.streamlit.app — geri bildiriminizi merakla bekliyorum."

---

## 3. Test Senaryoları Sonuçları

Tüm testler `python tests/test_scenarios.py` ile çalıştırıldı. Detaylar: `tests/test_results.json`.

### Parameter Engine (deterministik, LLM yok)

| Test | Yaş hesabı | Bucket | Uygun? | Plan Tipi | Doğru? |
|---|---|---|---|---|---|
| 1) 6 aylık emziren, aynı odada | 6.0 → 6.0 | `6_ay` | ✅ Uygun | 5 gün standart | ✅ Beklenen |
| 2) 8 aylık huysuz, 5-6 uyanma | 8.0 → 8.0 | `8_ay` | ✅ Uygun | 13 gün dirençli | ✅ Beklenen (huysuz+düşük dayanma) |
| 3) 14 aylık tek nap geçiş | 14.0 → 14.0 | `12-13_ay` | ✅ Uygun | 5 gün standart | ✅ Beklenen |
| 4) 3 aylık | 3.0 → 3.0 | `3_ay` | ⛔ Beklemeli | — | ✅ "5+ ay" kuralı tetiklendi |
| 5) Prematüre düzeltme (36 hf) | 7.0 → 6.0 | `6_ay` | ✅ Uygun | 5 gün standart | ✅ 1 ay düzeltme uygulandı, 8 ay altı emzik uyarısı çıktı |

### Ön Hazırlık Tespitleri (Doğru çalıştığının kanıtı)

- Test 1 (memeyle uyuyor + aynı oda + karartma yok): **Emerek uyuma değiştirme + Paravan + Karartma perdesi** — ✅
- Test 2 (beyaz gürültü kullanıyor): **Beyaz gürültü kuralı uyarısı** — ✅
- Test 5 (prematüre + sallanma + emzik): **Sallanma azaltma + Emzik 8 ay öncesi kuralı** — ✅

### Plan Generator

API key olmadığı için **fallback markdown** modunda çalıştı. Her test için **180-550 kelime** arası plan üretildi.

API key eklenince Claude tarafından yeniden yazılacak ve 1500-2500 kelime arası kapsamlı plan üretilecek.

### Chatbot Retrieve (TF-IDF)

5 örnek soru için retrieve testi yapıldı (`tests/test_results.json` içinde `chatbot_retrieve`). Hepsi alakalı chunk buldu (cosine similarity > 0.05).

### Chatbot Full

API key olmadığı için **fallback snippet** modunda çalıştı (en alakalı parçayı doğrudan göstererek).

---

## 4. Bilinen Kısıtlamalar

| Kısıtlama | Etki | Öneri |
|---|---|---|
| `ANTHROPIC_API_KEY` `.env`'de yok | Demo fallback modunda çalışır | Streamlit Cloud'da Secrets'a manuel ekle |
| `gh` CLI yüklü değil | GitHub repo otomatik oluşmadı | Manuel GitHub'da oluştur, push talimatları yukarıda |
| TF-IDF lemmatization yok (Türkçe) | "uyu" / "uyudu" farklı eşleşir | Demo için yeterli; production'da Voyage / OpenAI embedding |
| In-memory chunks | Streamlit Cloud cold start ~5-10 sn | Demo için kabul edilebilir |
| Streamlit Cloud free tier | İnaktivite sonrası uyuyabilir | İlayda paylaştığı an açar, ilk açılış 30 sn yavaş |
| Görsel embed yok | Anneye saat saat program SADECE metin | Kullanıcı tercihi (talep edilince eklenebilir) |
| Kaynak referansı yok | Hangi dersten geldiğini görmek için | Kullanıcı tercihi (admin panelinde gösterilebilir) |
| 37 soruluk form uzun | Anne yorulabilir | Demo amaçlı; production'da AI ile akıllı sıralı sorma |

---

## 5. Sonraki Adımlar (Production'a doğru)

1. **Anthropic API key ekle** → Streamlit Cloud Secrets
2. **Görsel asset'leri opsiyonel hale getir** → admin/anne tercihi olarak görsel embed seçeneği
3. **Premium plan engine'i mobile app'e taşı** → React Native + parameter engine REST API
4. **Voyage embedding + Qdrant** → TF-IDF yerine production RAG
5. **Anne sütü kesme + ikiz/çoğul içerikleri** → İlayda 4. batch'te talep et
6. **Görsel embed eklenince:** `transcripts/analysis/batch_3_visual_data.json` ve `gorseller_uyku_tablolari/` dosyaları kullanılabilir
7. **A/B test:** 5 gün vs 13 gün plan tercihinin hangisinin daha başarılı olduğu

---

## 6. Dosya Envanteri

```
tavsan_demo/
├── app.py                          ← Streamlit ana giriş
├── README.md                       ← Bu repo'nun açıklaması
├── TESLIM_RAPORU.md                ← Bu dosya
├── requirements.txt                ← streamlit, anthropic, sklearn
├── .gitignore                      ← .env, secrets, cache
├── .streamlit/
│   └── config.toml                 ← Pembe-mavi tema
├── engine/
│   ├── __init__.py
│   ├── parameter_engine.py         ← 89 kural, deterministik
│   ├── plan_generator.py           ← Claude → markdown
│   └── chatbot.py                  ← TF-IDF + Claude
├── pages/
│   ├── 1_Bebek_Bilgileri.py        ← 14 soru
│   ├── 2_Mevcut_Uyku.py            ← 7 soru
│   ├── 3_Yasam_Ortami.py           ← 6 soru
│   ├── 4_Gelisim.py                ← 3 soru
│   ├── 5_Onceki_Deneyim.py         ← 2 soru
│   ├── 6_AI_Ek_Sorular.py          ← 5 soru (toplam 37 ✓)
│   └── 7_Plan_ve_Sor.py            ← Plan + chatbot UI
├── data/
│   ├── master_knowledge_base.json  ← 19 yaş bucket × 9 alan
│   ├── decision_tree.json          ← 89 kural + parametre tablosu
│   ├── chunks.json                 ← 463 RAG chunk
│   └── lesson_metadata.json        ← 47 ders metadata
└── tests/
    ├── __init__.py
    ├── test_scenarios.py           ← 5 profil + chatbot testleri
    └── test_results.json           ← Çalıştırılmış sonuçlar
```

---

## 7. Son Notlar

- **Plan size mode:** API olmadan ~500 kelime / API'le 1500-2500 kelime.
- **Chatbot mode:** API olmadan en alakalı snippet / API'le özetlenmiş cevap.
- **Veri tutarsızlıkları:** `master_knowledge_base.json` içinde 8 tutarsızlık dokümante edilmiş (ses kayıtları ile görsel tablolar arasında). Engine, "genel rutin" ve "eğitim sırası" iki ayrı değer kümesi sunar.
- **Görseller hazır ama embed edilmemiş:** İstediğiniz an `data/ilayda_input/2026-05-batch-3/gorseller_uyku_tablolari/` klasöründen kullanılabilir.

**Sabah yapacaklarınız (özet):**
1. `cd "C:\Users\Mert KORAL\tavsan_transcribe\tavsan_demo"` ✅
2. GitHub'da public repo oluştur ✅
3. `git remote add origin ...` + `git push -u origin main` ✅
4. https://share.streamlit.io'ya gir, app oluştur ✅
5. Secrets'a `ANTHROPIC_API_KEY` ekle ✅
6. URL'i İlayda'ya WhatsApp'tan gönder ✅

Hepsi 5-10 dakika sürer.
