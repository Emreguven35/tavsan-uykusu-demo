# 🐰 Tavşan Uykusu Premium Demo

İlayda Kani'nin uyku eğitimi metodolojisinin AI-destekli web demosu. 47 ses kaydı, 29 görsel tablo ve 89 karar kuralından oluşturulan karar mekanizmasını kullanarak kişiselleştirilmiş uyku planı üretir.

## Özellikler

- 📋 **37 soruluk profil formu** (6 bölüm halinde)
- 🧠 **Deterministik Parameter Engine** — sayısal değerler `master_knowledge_base.json`'dan gelir (LLM değiştiremez)
- ✍️ **Claude-tabanlı Plan Generator** — parametreleri Türkçe markdown plana çevirir
- 💬 **RAG Chatbot** — TF-IDF + Claude ile 463 chunk üzerinde soru-cevap
- 🪶 **Streamlit Community Cloud uyumlu** — pgvector/DB yok, in-memory

## Hızlı Başlangıç (Yerel)

```bash
git clone https://github.com/KULLANICI/tavsan-uykusu-demo.git
cd tavsan-uykusu-demo
pip install -r requirements.txt

# .env dosyası oluştur:
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

streamlit run app.py
```

Tarayıcıda <http://localhost:8501> açılır.

## Mimari

```
tavsan_demo/
├── app.py                          # Streamlit ana giriş
├── pages/                          # Çok sayfalı UI
│   ├── 1_Bebek_Bilgileri.py        # Sorular 1-14
│   ├── 2_Mevcut_Uyku.py            # Sorular 15-21
│   ├── 3_Yasam_Ortami.py           # Sorular 22-27
│   ├── 4_Gelisim.py                # Sorular 28-30
│   ├── 5_Onceki_Deneyim.py         # Sorular 31-32
│   ├── 6_AI_Ek_Sorular.py          # Sorular 33-37
│   └── 7_Plan_ve_Sor.py            # Plan + Chatbot
├── engine/
│   ├── parameter_engine.py         # Deterministik karar motoru (89 kural)
│   ├── plan_generator.py           # Claude ile plan yazımı (sayılar değişmez)
│   └── chatbot.py                  # TF-IDF + Claude RAG
├── data/
│   ├── master_knowledge_base.json  # 19 yaş bucket × 9 alan
│   ├── decision_tree.json          # Karar ağacı + 4 parametre tablosu
│   ├── chunks.json                 # 463 RAG chunk (47 ders)
│   └── lesson_metadata.json        # Ders metadata
├── tests/
│   ├── test_scenarios.py           # 5 profil + chatbot test runner
│   └── test_results.json           # Çalıştırılmış sonuçlar
├── requirements.txt
├── .gitignore
└── .streamlit/config.toml          # Tavşan Uykusu pembe-mavi tema
```

## Veri Kaynakları

- **47 ders ses transkripti** (yaklaşık 405 dakika ses, ElevenLabs Scribe ile)
- **29 markalı görsel uyku tablosu** (12 yaş tablosu, 5 master, 7 program, 5 detaylı)
- **89 karar kuralı** (eğitim öncesi 8 maddelik checklist, bekleme süreleri, red flag'ler)
- **19 yaş bucket × 9 parametre** (uyanıklık, uyku sayısı, gece beslenme, vb.)

## Çalışma Prensibi

1. Anne 37 soruyu cevaplar.
2. `parametre_uret()` → yaş hesabı (prematüre düzeltme dahil), yaş bucket seçimi, red flag tarama, ön hazırlık tespiti, plan tipi seçimi (5 / 13 / 6 gün), gece beslenme planı, bekleme süreleri.
3. `plan_uret()` → parametreleri Claude'a (claude-opus-4-7) verir. Claude SADECE yazar — sayıları asla değiştirmez.
4. Chatbot, anne sorduğunda 463 chunk arasında TF-IDF ile en alakalı 5'i bulur, Claude'a verir. Yine sayısal değer üretmez, sadece yeniden ifade eder.

## Önemli Tasarım Kararları

- **Görsel embed YOK** — Demo kullanıcı tercihi gereği görseller plana eklenmiyor.
- **Kaynak referansı YOK** — Anneye "kayıt36'dan geldi" gibi bilgi gösterilmiyor.
- **İlayda ses tonu simülasyonu YOK** — Düz, profesyonel Türkçe.
- **Deterministik sayılar** — Tüm sayısal değerler `master_knowledge_base.json` lookup; LLM hallüsinasyonu önlenir.

## Test

```bash
python tests/test_scenarios.py
```

5 profil senaryosu çalıştırır:
1. 6 aylık emziren bebek (aynı odada)
2. 8 aylık huysuz, sık gece uyanma
3. 14 aylık tek uyku geçişi
4. 3 aylık (eğitim alamayacak yaşta) — red flag testi
5. Prematüre düzeltme (7 ay → 6 ay) — kural testi

Sonuçlar `tests/test_results.json`'a yazılır.

## Önemli

Bu **demo** uygulamasıdır — production değildir. Tavşan Uykusu mobil app ana ürün olacak. Kişisel veri saklanmaz (her oturum bağımsız). Medikal tavsiye değildir; İlayda Kani'nin metodolojisine dayalıdır.
