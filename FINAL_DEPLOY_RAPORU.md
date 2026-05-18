# Tavşan Uykusu Demo — Final Deploy Raporu

**Tarih:** 2026-05-18
**Versiyon:** v1.0 (KVKK uyumlu, private repo'da)
**Commit:** `0fc4cd9` (anonimleştirme commit'i)

---

## ✅ Tamamlanan İşler

### Kod ve İçerik
- [x] Streamlit demo uygulaması (37 soruluk onboarding + plan generator + chatbot)
- [x] Parameter engine (89 karar kuralı, 19 yaş bucket)
- [x] RAG soru-cevap sistemi (TF-IDF + Claude)
- [x] 5 test senaryosu (12/12 başarılı)

### Güvenlik ve Uyum
- [x] **KVKK anonimleştirme** — 11 dosya, 82 değişiklik
  - Sevim/Sevim Yıldız/Sevim hanım → anne
  - Nur/Nur Uysal/Nur hanım → anne
  - Züleyha/Züleyha hanım → anne
  - Leren/Leren Aker → bebek
  - Duru/Duru Uysal → bebek
  - Ali Asaf/Ali Asaf Kılıç → bebek
- [x] Final isim taraması: **0 bulgu** (suspicious tokens kontrol edildi)
- [x] Orijinal dosyalar lokal yedekte: `_originals_KVKK_RISK_DO_NOT_PUSH/` (2.5 MB, push edilmedi)
- [x] `.gitignore` güçlendirildi (yedekler + secrets + ham veri hariç tutuldu)
- [x] Ana dizine de `.gitignore` eklendi (audio_files/, data/ilayda_input/, transcripts/raw/ vb. korunuyor)

### GitHub
- [x] Private repo'ya push: **https://github.com/Emreguven35/tavsan-uykusu-demo**
- [x] 2 commit:
  - `1f300ab` — Initial commit
  - `0fc4cd9` — KVKK uyumlu anonimleştirme
- [x] `main` branch, remote tracking aktif

### Deploy (Manuel — Sıradaki Adımlar)
- [ ] Streamlit Cloud deploy (kullanıcı manuel yapacak)
- [ ] Secrets ekleme (kullanıcı manuel yapacak — ANTHROPIC_API_KEY)
- [ ] Cloud test (kullanıcı manuel yapacak)
- [ ] İlayda'ya mesaj gönderme

---

## 📝 URL'ler

- **GitHub Repo:** https://github.com/Emreguven35/tavsan-uykusu-demo *(private)*
- **Demo App (deploy sonrası):** https://tavsan-uykusu-demo.streamlit.app

---

## 🔐 Güvenlik Notları

- API key ASLA repo'da değil — sadece Streamlit Cloud Secrets'ta olacak
- Repo private (sadece Emreguven35 erişebilir)
- Orijinal isimli dosyalar lokal `_originals_KVKK_RISK_DO_NOT_PUSH/` klasöründe, ana dizin `.gitignore`'unda hariç tutuldu
- Ham ses dosyaları (`audio_files/`) ve İlayda input klasörü (`data/ilayda_input/`) ana `.gitignore`'da

---

## 📋 Kullanıcının Yapacakları (Sırayla)

### 1️⃣ Anthropic API Key Al (~1 dakika)

1. https://console.anthropic.com/settings/keys
2. "Create Key" → "tavsan-uykusu-demo" adıyla key oluştur
3. **`sk-ant-...` formatındaki key'i KOPYALA** (sayfa kapanınca tekrar göremezsin)

### 2️⃣ Streamlit Cloud'a Deploy Et (~3 dakika)

1. https://share.streamlit.io adresine git
2. **"Sign in with GitHub"** → Emreguven35 ile giriş yap
3. İlk kezse Streamlit'e izin ver:
   - **ÖNEMLİ:** "All repositories" yerine **"Only select repositories"** seç
   - Sadece `tavsan-uykusu-demo` repo'sunu işaretle (güvenlik için)
4. Sağ üstte **"Create app"** → **"Yup, I have an app"** seç
5. App bilgilerini doldur:
   - **Repository:** `Emreguven35/tavsan-uykusu-demo`
   - **Branch:** `main`
   - **Main file path:** `app.py`
   - **App URL:** `tavsan-uykusu-demo`
6. **"Advanced settings"** tıkla:
   - **Python version:** `3.11` veya `3.12`
   - **Secrets:** ŞİMDİLİK BOŞ BIRAK (sonraki adımda)
7. **"Deploy!"** tıkla
8. 2-3 dakika bekle (pip install)

> ℹ️ Deploy bitince "ANTHROPIC_API_KEY not found" tarzı uyarı görebilirsin. Normal — bir sonraki adımda halledilecek.

### 3️⃣ API Key'i Secrets'a Ekle (~1 dakika)

1. Deploy edilen app'in sağ alt köşesinde **"⋯"** → **"Settings"**
2. Sol menüden **"Secrets"** sekmesi
3. Aşağıdaki içeriği yapıştır (`sk-ant-...` yerine 1. adımda aldığın key):

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
```

4. **"Save"** tıkla
5. App OTOMATİK YENİDEN BAŞLAR (30-60 sn)

### 4️⃣ Demoyu Test Et (~2 dakika)

1. URL: https://tavsan-uykusu-demo.streamlit.app
2. 37 soruyu örnek bir profil için doldur
3. 7. sayfada plan görmelisin (Claude tarafından üretilmiş ~1500-2500 kelime)
4. Chatbot'a 1-2 soru sor: "4 ay regresyonu nedir?", "Yatak geçişini ne zaman yapmalı?"
5. Cevaplar makulse → İlayda'ya gönder

### 5️⃣ İlayda'ya Mesaj Gönder

`MESAJ_ILAYDA.md` dosyasındaki şablonu WhatsApp/email ile gönder.

---

## 🐛 Bilinen Kısıtlamalar

| Kısıtlama | Etki | Çözüm |
|---|---|---|
| Anthropic key yoksa | Fallback mode (basit template) | Secrets ekle |
| Cold start | İlk açılış 30-60 sn | Streamlit Cloud free tier — kabul edilebilir |
| Görsel embed yok | Sadece yazılı plan | İlayda geri bildirimi sonrası eklenebilir |
| Kaynak gösterimi yok | Anneye kaynak chunk yok | Kullanıcı tercihi |
| TF-IDF Türkçe lemma | "uyu"/"uyudu" farklı eşleşir | Demo için yeterli; production'da Voyage embedding |

---

## 🔄 Sonraki Adımlar (İlayda Geri Bildirimi Sonrası)

1. **Parameter engine kuralları güncelleme** — İlayda'nın işaret ettiği yanlışlar
2. **Plan template revize** — eksik bölümler eklenebilir
3. **Chatbot prompt iyileştirme** — "ders adı geçmesin" gibi katı kuralları sağlamlaştırma
4. **Eksik içerikler için 4. batch** — ikiz/çoğul, anne sütü kesme, regresyon etiketli kayıtlar
5. **Tavşan Uykusu mobile app'e entegrasyon planı** — bu demo bir prototip, ana ürüne taşıma stratejisi
6. **Görsel embed seçeneği** — admin tercihiyle "anne görseli görsün" toggle

---

## 📁 Dosya Envanteri

```
tavsan_demo/                              ← GitHub repo (Emreguven35/tavsan-uykusu-demo)
├── app.py                                ← Streamlit ana
├── README.md                             ← Proje açıklaması
├── TESLIM_RAPORU.md                      ← Önceki rapor (deploy adımları)
├── FINAL_DEPLOY_RAPORU.md                ← BU dosya
├── MESAJ_ILAYDA.md                       ← İlayda mesaj şablonu
├── requirements.txt                      ← Bağımlılıklar
├── .gitignore                            ← Secrets + cache + yedekler hariç
├── .streamlit/config.toml                ← Pembe-mavi tema
├── engine/
│   ├── parameter_engine.py               ← 89 karar kuralı (deterministik)
│   ├── plan_generator.py                 ← Claude → markdown
│   └── chatbot.py                        ← TF-IDF + Claude
├── pages/                                ← 7 form sayfası
├── data/                                 ← 4 JSON (anonimleştirilmiş)
└── tests/
    ├── test_scenarios.py                 ← 5 profil + chatbot
    └── test_results.json                 ← Çalıştırılmış sonuçlar (12/12 başarılı)

_originals_KVKK_RISK_DO_NOT_PUSH/          ← LOKAL YEDEK — push edilmedi
├── txt_original/                         ← Anonim öncesi transcripts/txt
├── json_original/                        ← Anonim öncesi transcripts/json
├── analysis_original/                    ← Anonim öncesi transcripts/analysis
├── chunks_original.json
├── master_kb_original.json
└── decision_tree_original.json
```

---

## 🎯 Bir Bakışta Özet

```
✅ KVKK Anonimleştirme: 82 değişiklik, 11 dosya, 0 isim kaldı
📤 GitHub Push: https://github.com/Emreguven35/tavsan-uykusu-demo (private, 0fc4cd9)
📋 Kullanıcının yapacakları: 5 adım (~10 dakika toplam)
📱 İlayda mesajı: MESAJ_ILAYDA.md hazır
🔐 Güvenlik: API key sadece Secrets'ta, ham veri lokal yedekte
```

**Sonraki adım:** `share.streamlit.io` → Create app → Secrets → İlayda'ya gönder.
