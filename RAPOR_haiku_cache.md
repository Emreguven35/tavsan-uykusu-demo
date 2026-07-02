# Rapor — Haiku Geçişi + Cevap Cache + Sabit Metin Analizi

**Branch:** `feature/haiku-cache` (main'e DOKUNULMADI, merge/push YOK)
**Backup tag:** `backup-oncesi-haiku-cache` → `ce64c60`
**Tarih:** 2026-07-02

Üç iş yapıldı: **(A)** chatbot Sonnet→Haiku, **(B)** iki katmanlı cevap cache,
**(C)** plan çıktısındaki sabit metin analizi (yalnızca tespit — kod değişmedi).

---

## A) CHATBOT MODEL: SONNET → HAIKU

**Ne değişti:** `engine/config.py` tek `MODEL_NAME` sabiti ikiye ayrıldı:
- `PLAN_MODEL = "claude-sonnet-4-6"` — plan üretici **AYNEN** (dokunulmadı).
- `CHATBOT_MODEL = "claude-haiku-4-5"` — chatbot/RAG.
- `MODEL_NAME = PLAN_MODEL` geriye dönük uyum için korundu (build_embeddings doc2query).

Model string formatı mevcut Sonnet formatıyla doğrulandı (tarih ekli değil):
`claude-sonnet-4-6` → `claude-haiku-4-5`.

### 151-item regresyon suite (`test_ilayda_duzeltmeleri.py`)

| Sonuç | Sayı |
|-------|------|
| GEÇTİ | **154** |
| KALDI | 2 |
| BİLGİ / atlandı | 8 |

- **Chatbot C bölümü: TÜM kontroller GEÇTİ** (araba/16:00/kucak — protokol + retrieval).
- KALAN 2 kontrol **önceden vardı ve modelden bağımsız**:
  1. `A-data`: `chunks.json`'da 'bebek arabası' sayımı (veri dosyası — değişmedi).
  2. `B-3_Defne`: **cache'lenmiş Sonnet planında** "uyandığı" ibaresi eksik
     (Jun 25 çıktısı — bu görevle ilgisiz). Haiku regresyonu DEĞİL.

### Haiku vs Sonnet — 10 soru yan yana (aynı retrieval + aynı prompt)

`test_haiku_vs_sonnet.py` — her iki model **birebir aynı** bağlam/prompt/system ile.

| # | Soru | Haiku kel. | Sonnet kel. | Haiku protokol | Sonnet protokol |
|---|------|-----------:|------------:|:--------------:|:---------------:|
| 1 | araba | 74 | 67 | PASS | **FAIL** (uyanık-tutma kuralı yok) |
| 2 | 16:00 istisna | 94 | 59 | PASS | PASS |
| 3 | kucak | 95 | 75 | PASS | PASS |
| 4 | beyaz gürültü | 125 | 117 | PASS | PASS |
| 5 | gece uyanma | 129 | 122 | PASS | PASS |
| 6 | kısa uyku | 118 | 121 | PASS | PASS |
| 7 | emzik | 71 | 48 | PASS | PASS |
| 8 | sadece kucak | 125 | 106 | PASS | PASS |
| 9 | ne zaman | 82 | 62 | PASS | PASS |
| 10 | oda/ışık | 93 | 61 | PASS | PASS |

- **Protokol sadakati: Haiku 10/10, Sonnet 9/10.** Sonnet'in tek "eksiği" araba
  sorusunda "3 denemede uyumazsa uyanık tut" kuralını atlaması — bir **eksiklik
  nüansı, güvenlik ihlali değil** (iki cevap da araba önerisi vermiyor). Haiku bu
  kuralı doğru verdi.
- **Ton:** her ikisi de sıcak-profesyonel Türkçe, emoji kullanımı benzer, yasak
  ifade (kucağa almayın / temas yok / yarı görünür) YOK, ders/kayıt sızıntısı YOK.
- **Uzunluk:** Haiku ort. **100 kelime**, Sonnet **83** (Haiku ≈ 1.20×). İkisi de
  "1-3 paragraf" kuralı içinde.
- Tam cevaplar: `test_outputs/haiku_vs_sonnet.md`.

**Karar önerisi:** Haiku'da ton/protokol düşüşü YOK; bu görevde biraz daha eksiksiz.
Geçiş güvenli görünüyor — nihai kararı siz verin.

---

## B) CEVAP CACHE (iki katman, yaş bandı anahtarlı)

**Akış:** `cevapla(soru, yas_bandi)` — LLM çağrısından **ÖNCE** cache kontrolü.

- **Katman 1 (exact):** soru normalize (lowercase, noktalama/fazla boşluk temizle)
  + yaş bandı → SHA-256 hash. Aynı normalize soru + aynı bant varsa cache'ten döner.
- **Katman 2 (semantik):** **mevcut** sentence-transformers modeli (YENİ model
  yüklenmez). Cosine **≥ 0.95** VE **aynı yaş bandı** ise döner.
- **Yaş bandı yoksa** semantik atlanır; yalnızca exact çalışır (spesifikasyon).
- Anahtar 19 yaş bucket'ından birini içerir (`param['bucket']`; page 7 geçirir).
- **Depolama:** `data/answer_cache.json` (LRU son **500**), modül-global →
  Streamlit rerun'larında korunur (`@st.cache_resource`'a gerek yok; chatbot
  streamlit'e bağımlı değil, testlerde de aynı kod çalışır). `.gitignore`'a eklendi.
- **Cache HIT loglanır** (`logger.info`), kullanıcıya **gösterilmez**.

### Test sonuçları (`test_cevap_cache.py`) — LLM mock, gerçek API sayımı

| # | Senaryo | Sonuç |
|---|---------|-------|
| 1 | Aynı soru + aynı bant 2× → 2. cache'ten | ✅ API çağrısı = 1 |
| 2 | Farklı ifade + aynı bant → semantik | ✅ cosine 0.976 ≥ 0.95, HIT |
| 3 | **Farklı yaş bandı** + aynı soru → MISS | ✅ yeni cevap üretildi |
| 4 | Bant YOK + aynı soru 2× → exact | ✅ API çağrısı = 1 |
| 5 | Bant YOK + farklı ifade → semantik atlanır | ✅ MISS (doğru) |

**5/5 geçti.** Görevdeki senaryoların hepsi karşılandı (aynı soru → 0 API; benzer
ifade → semantik; farklı bant → yeni cevap).

**Semantik eşik notu (dürüst):** görevdeki *çapraz-dilli* örnek
("beyaz gürültü zararlı mı" / "white noise bebeğe zarar verir mi") cosine = **0.807**
ölçüldü — spesifikasyondaki 0.95 eşiğinin ALTINDA, dolayısıyla bu TR/EN çifti
semantik cache'e **takılmaz**. Yakın Türkçe parafrazlar takılır (0.976) / anlamca
uzaklaşınca takılmaz (0.924). 0.95 bilinçli olarak **korundu** (yanlış-pozitif =
farklı soruya bayat cevap riski düşük). Eşiği ~0.90'a çekmek çapraz-dilli/uzak
parafraz yakalamayı artırır ama bayat-cevap riskini yükseltir — **kararı size
bırakıyorum, değiştirmedim.**

### Sorgu başı maliyet (chatbot; ölçülen ~1914 in / ~424 out token)

| Durum | Model | $/sorgu | Baza göre |
|-------|-------|--------:|-----------|
| Eski | Sonnet 4.6 | **$0.01210** | — |
| Yeni | Haiku 4.5 | **$0.00403** | **%67 ucuz** (A etkisi) |
| Cache HIT | — | **$0.00000** | **%100** (tekrar/benzer sorgu) |

A+B birlikte: tekil sorguda %67 ucuzluk, tekrarlanan/benzer sorularda %100 (API yok).

---

## C) SABİT METİN ANALİZİ (yalnızca tespit — HİÇBİR ŞEY ŞABLONA ALINMADI)

**Girdi:** 3 mevcut plan (`test_outputs/plan_*.md`): P1=Emir 8ay 13gün,
P2=Emir 8ay 1aylık, P3=Defne 11ay 5gün (2 farklı yaş bandı, 3 farklı plan tipi;
görev "geçmiş test çıktılarını kullan" izniyle — API maliyeti oluşturulmadı).

### Bölüm bazlı benzerlik + token

| Bölüm | Ort. çift benzerlik | Sınıf | Ort. token |
|-------|--------------------:|-------|-----------:|
| Bebek Profili Özeti | 0.31 | kişiye özel | 475 |
| Eğitim Uygunluğu | 0.37 | kişiye özel | 102 |
| Ön Hazırlık | 0.04 | kişiye özel | 488 |
| Günlük Program | 0.20 | kişiye özel* | 1158 |
| Eğitim Planı | 0.12 | kişiye özel** | 3221 |
| Gece Uyanmaları Protokolü | 0.11 | kişiye özel | 466 |
| Başarı Kriterleri | 0.11 | kişiye özel | 235 |
| Dikkat Edilmesi Gerekenler | 0.06 | kişiye özel | 576 |

*Günlük Program tablo-altı **uyanıklık açıklaması** sabit; tablo kişiye özel.
**Eğitim Planı gün-gün kişiye özel ama içindeki **protokol blokları yarı-sabittir**.

**Bulgu:** Bölüm BÜTÜNLERİ kişiye özel (isim/yaş/saat/gün-yapısı örülü) — bölüm
seviyesinde şablonlama ~0 kazandırır.

### İlayda boilerplate kalıpları (plan başına tekrar)

| Kalıp | P1 | P2 | P3 | Sınıf |
|-------|---:|---:|---:|-------|
| B-Planı protokolü (45dk→15dk→45dk, max 3) | 8 | 10 | 7 | **yarı-sabit** (her güne uyarlanır) |
| Yoğun direnç / B-Planı alt başlığı (her gün) | 15 | 16 | 12 | **yarı-sabit** |
| Kısa gündüz uykusu alt başlığı (her gün) | 5 | 5 | 4 | **yarı-sabit** |
| Kucağa alma kademeli kalıbı (30sn→1→1,5→2dk) | 4 | 5 | 4 | **yarı-sabit** |
| Uyanıklık süresi açıklaması (Günlük Program altı) | 2 | 2 | 2 | **sabit** (sayı hariç) |
| KATI saat notu (07:00 esneme) | 3 | 2 | 2 | **sabit** |
| Son gündüz uykusu 16:00 esneklik notu | 2 | 2 | 2 | **sabit** |
| Beyaz gürültü kademeli azaltma | 2 | 2 | 0 | koşullu |

Bu kalıplar İlayda kurallarından **prompt'a sabit enjekte** edilir; model her planda
near-verbatim yeniden üretir. Asıl tekrar burada — özellikle **gün-gün yinelenen
protokol blokları** (13-16×/plan).

### Blok-seviyesi tekrar (ölçülen)

| Plan | Toplam out-tok | Kesin tekrar (≥0.80) | Tekrar % |
|------|---------------:|---------------------:|---------:|
| P1 (13 gün) | 6892 | 174 | %3 |
| P2 (1 aylık) | 7522 | 785 | %10 |
| P3 (5 gün) | 6049 | 302 | %5 |

Kesin near-duplicate ~**%6 (~420 tok/plan)** çıkıyor; çünkü her günün bloğu pozisyon/
gün ile **kişiselleştirilmiş** (0.80 eşiğinin altında kalıyor). Yani bloklar
**tam-sabit değil, yarı-sabit** (aynı iskelet + gün/pozisyon değişkeni).

### $/plan tasarruf projeksiyonu (baz ~0.14$/plan)

- Ortalama plan çıktısı: **~6821 output token** (≈ **$0.102** output-only; input tarafı
  prompt-caching ile zaten ucuz → verilen ~$0.14/plan bazına yakın).
- **Kesin (tam-sabit) tekrar:** ~420 tok/plan → **~$0.006/plan** (bazın ~%4'ü). Alt sınır.
- **Yarı-sabit tavan** (protokol iskeleti tek sefer yazılıp gün/pozisyon değişkeniyle
  referanslanırsa, invariant sayısal kalıplar dahil): tahmini **%10–13 output
  (~$0.010–0.014/plan)** üst sınır.

### ⚠️ Kritik kısıt (öneri)

İlayda **F kuralı** protokolü **her günün altında, o güne uyarlanmış** yazmayı
**ZORUNLU** kılıyor ("AYRI genel protokol bölümü AÇMA"). Dolayısıyla blokları tek
generic şablona indirmek **içerik kuralını ihlal eder** ve gün-bazlı bağlamı kaybettirir.

**Öneri (uygulanmadı):** yalnızca **invariant sayısal sabitler** (45/15/45 dk, max 3
tekrar; KATI 07:00 notu; 16:00 esneklik notu; uyanıklık açıklaması iskeleti) tek
referans bloğa alınabilir; **gün-bazlı prose korunmalı**. Güvenli, gerçekçi tasarruf
bu yüzden **~%5–8 (~$0.007–0.011/plan)** ile sınırlı. Agresif şablonlama önerilmez.
**Bu aşamada hiçbir şey şablona alınmadı — sadece tespit.**

Detaylı çıktı: `test_outputs/sabit_metin_raporu.md`, `test_outputs/haiku_vs_sonnet.md`.

---

## COMMIT'LER + GERİ ALMA KOMUTLARI

| Aşama | Commit | Açıklama |
|-------|--------|----------|
| Backup tag | `backup-oncesi-haiku-cache` → `ce64c60` | özellik öncesi durum |
| wip | `ce64c60` | mevcut (commit'lenmemiş) durum snapshot'ı |
| **A** | `833dca5` | chatbot haiku-4-5 geçişi |
| **B** | `17769fc` | cevap cache (exact + semantik, yaş bandı anahtarlı) |
| **C** | *(bu commit — `git log` en üstteki)* | sabit metin analiz raporu (docs) |

**Geri alma (her aşama bağımsız — dry-run ile temiz doğrulandı):**

```bash
# C'yi geri al (yalnız döküman — her zaman temiz):
git revert <C-hash>

# B'yi geri al (cache'i kaldır, Haiku kalsın):
git revert 17769fc

# A'yı geri al (Haiku → Sonnet'e dön; auto-merge temiz):
git revert 833dca5
#   Alternatif tek-satır: engine/config.py → CHATBOT_MODEL = "claude-sonnet-4-6"

# Önerilen temiz sıra (dosya çakışması olmasın): önce C, sonra B, sonra A.

# Tüm özelliği topluca geri al (main zaten dokunulmadı):
git checkout main                       # main tertemiz
# veya branch'i tümden sıfırla:
git reset --hard backup-oncesi-haiku-cache
```

**main'e hiçbir şey geçmedi; merge/push yapılmadı.** Tüm iş `feature/haiku-cache`'te.
Testler: 151-suite 154 GEÇTİ (C bölümü tam), cache 5/5, Haiku protokol 10/10.
