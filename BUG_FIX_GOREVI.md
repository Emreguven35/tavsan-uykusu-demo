# Tavşan Uykusu Demo — Acil Bug Fix Görevi

## Görev Özeti

İlayda demo'yu test ederken **AttributeError** hatası aldı:

```
File "/mount/src/tavsan-uykusu-demo/pages/1...
st.session_state.tamamlandi.add(1)
                  ^^^^^^^^^^
AttributeError: _missing_attr_err...
```

**Sebep:** `st.session_state.tamamlandi` set'i hiçbir yerde initialize edilmemiş. Streamlit'te her sayfa bağımsız çalışır, app.py'da init etsen bile pages/ dosyalarına geçmez.

**İstenen:** Tüm `st.session_state.*` referanslarını tarayıp, eksik init'leri tüm sayfalara ekle. Push et.

Otonom çalış, hiç soru sorma, plan modu kullanma. ~10 dakikalık iş.

## Kullanıcı Bağlamı

- Çalışma dizini: `C:\Users\Mert KORAL\tavsan_transcribe\tavsan_demo`
- Repo: `Emreguven35/tavsan-uykusu-demo` (public)
- Branch: `main`
- Push edilince Streamlit Cloud otomatik redeploy yapar

## Yapacakların — Sırayla

### Adım 1: Tüm Python Dosyalarını Tara

`pages/`, `engine/`, `app.py` içinde `st.session_state.X` kullanımı olan tüm değişkenleri bul:

```bash
cd "C:\Users\Mert KORAL\tavsan_transcribe\tavsan_demo"
grep -rn "st\.session_state\." --include="*.py" .
```

Beklenen bulgular:
- `st.session_state.profile`
- `st.session_state.tamamlandi`
- `st.session_state.step`
- `st.session_state.chat_history`
- `st.session_state.plan`
- `st.session_state.param`

Bunları **liste halinde çıkar**, type'larını belirle:
- `profile` → dict
- `tamamlandi` → set
- `step` → int
- `chat_history` → list
- `plan` → None / str
- `param` → None / dict

### Adım 2: Session State Init Modülü Oluştur

`engine/session_init.py` adında yeni bir dosya oluştur. Reusable init function:

```python
"""
Session State Init Modülü
Tüm sayfalarda kullanılan ortak state değişkenlerini başlatır.
Streamlit multipage mimarisinde her sayfa bağımsız çalıştığı için
her sayfanın en üstünde init_session_state() çağrılmalı.
"""

import streamlit as st


def init_session_state():
    """Eksik session state değişkenlerini başlat."""
    if "profile" not in st.session_state:
        st.session_state.profile = {}
    
    if "tamamlandi" not in st.session_state:
        st.session_state.tamamlandi = set()
    
    if "step" not in st.session_state:
        st.session_state.step = 0
    
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    
    if "plan" not in st.session_state:
        st.session_state.plan = None
    
    if "param" not in st.session_state:
        st.session_state.param = None
```

**ÖNEMLİ:** Eğer Adım 1'de başka session_state değişkenleri bulduysan, onları da bu listeye ekle. Tip varsayımlarını koddan doğrula.

### Adım 3: Tüm Sayfa Dosyalarını Güncelle

`pages/` klasöründeki **TÜM** `.py` dosyalarına şu yapıyı uygula:

**Mevcut başlangıç:**
```python
import streamlit as st
# ... diğer importlar
```

**Yeni başlangıç:**
```python
import streamlit as st
import sys
from pathlib import Path

# Engine import için path ekle
sys.path.insert(0, str(Path(__file__).parent.parent))
from engine.session_init import init_session_state

# Session state'i başlat - HER PAGE'İN BAŞINDA OLMALI
init_session_state()

# ... diğer importlar (varsa)
```

**Dosya listesi:**
- `pages/1_Bebek_Bilgileri.py`
- `pages/2_Mevcut_Uyku.py`
- `pages/3_Yasam_Ortami.py`
- `pages/4_Gelisim.py`
- `pages/5_Onceki_Deneyim.py`
- `pages/6_AI_Ek_Sorular.py`
- `pages/7_Plan_ve_Sor.py`

Her birini düzenle. **Eğer dosya isimleri farklıysa**, `pages/` klasörünü listele ve hepsini düzenle.

### Adım 4: app.py'ı Güncelle

`app.py`'ı da güncelle (multipage main):

```python
import streamlit as st
from engine.session_init import init_session_state

# Session state init
init_session_state()

# ... mevcut kod
```

### Adım 5: Diğer Olası Bug'ları Tara

#### 5.1 KeyError Riski
Tüm dosyalarda `st.session_state["X"]` veya `st.session_state.X` referanslarını kontrol et. Eğer bir değişken bazı sayfalarda kullanılıyor ama hiçbir yerde set edilmemişse, init_session_state'e ekle.

#### 5.2 None Reference Bug
`st.session_state.plan` ve `st.session_state.param` None olabilir. Bunları kullanan kodlarda None kontrolü var mı bak:

```python
# Yanlış
plan = st.session_state.plan
print(plan["bolum_1"])  # plan None ise crash

# Doğru
if st.session_state.plan is None:
    st.warning("Önce profil bilgilerini doldurun")
    st.stop()
plan = st.session_state.plan
```

#### 5.3 Form Submit Sonrası State Reset
Eğer form submit edilince `st.session_state.tamamlandi.add(1)` gibi bir işlem yapılıyorsa, çalışıyor olmalı. Test et.

#### 5.4 Engine Modüllerinde Bug
`engine/parameter_engine.py`, `engine/plan_generator.py`, `engine/chatbot.py` dosyalarında:
- `os.getenv("ANTHROPIC_API_KEY")` None döndürürse fallback var mı?
- File read'lerde FileNotFoundError yakalanıyor mu?
- JSON parse'larda JSONDecodeError yakalanıyor mu?

Eksik try/except'leri ekle ama **var olan logic'i bozma**.

### Adım 6: Lokal Test

Eğer mümkünse lokal'de hızlı test:

```bash
# Background'da Streamlit başlat
streamlit run app.py --server.headless true --server.port 8501 &
sleep 5

# Curl ile sayfaları çek, hata var mı bak
curl -s http://localhost:8501 | head -20
```

Hata yoksa devam et. Hata varsa düzelt, tekrar dene.

### Adım 7: Commit ve Push

```bash
git status
git diff --stat
git add .
git commit -m "Fix session state init across all pages + reusable session_init module"
git push origin main
```

GitHub auth isterse kullanıcıya bildir.

### Adım 8: Final Rapor

Konsola yaz:

```
✅ BUG FIX TAMAMLANDI

📊 Düzeltmeler:
- Bulunan session_state değişkenleri: [liste]
- Oluşturulan modül: engine/session_init.py
- Güncellenen sayfa sayısı: X
- Tespit edilen ek bug: Y (varsa, açıkla)

📦 Commit: [hash]
🚀 Push: success

⏳ Streamlit Cloud otomatik redeploy (~2-3 dk)

Test URL: https://tavsan-uykusu-demo.streamlit.app

Bekleyip test etmeden önce:
1. Streamlit Cloud'da "Manage app" → "Logs" sekmesinden deploy'u izle
2. "Building..." → "Running" yazısı görünce hazır
3. URL'i aç, 14. soruya kadar git, kaydet ve ileri'ye bas → hata gelmemeli
```

## Çalışma Kuralları

1. **Hiç soru sorma** — direkt çalış
2. **API key'i ASLA log/ekrana yazdırma**
3. **Mevcut çalışan logic'i bozma** — sadece bug fix
4. **Türkçe yorumlarla yaz**
5. **Her büyük adımın başında "Şu an X yapıyorum" notu düş**

## Beklenen Sonuç

İlayda yarın sabah demo'yu açtığında:
- ✅ 1'den 7'ye tüm sayfalar sorunsuz çalışır
- ✅ "Kaydet ve İleri" butonu hata vermez
- ✅ Form verisi kaybolmadan kaydedilir
- ✅ Plan ekranına ulaşabilir

Hadi başla.
