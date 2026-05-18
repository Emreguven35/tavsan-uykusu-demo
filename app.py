"""Tavşan Uykusu Premium Demo — ana giriş sayfası."""
import streamlit as st
from dotenv import load_dotenv

from engine.session_init import init_session_state

load_dotenv()

st.set_page_config(
    page_title="Tavşan Uykusu Premium Demo",
    page_icon="🐰",
    layout="centered",
)

# Tüm session_state değişkenlerini tek yerde başlat
# Multipage'de her sayfa bağımsız çalıştığı için pages/ dosyaları da aynı init'i çağırıyor
init_session_state()

st.title("🐰 Tavşan Uykusu Premium Demo")

st.markdown(
    """
### İlayda Kani için hazırlanan kişisel uyku eğitimi planı demosu

Bu sistem, sizin **47 ses eğitim kaydınızdan** ve **29 görsel tablonuzdan** oluşturulan
karar mekanizmasını kullanarak kişiselleştirilmiş uyku planı üretir.

**Akış:**
1. **37 soruluk profil formu** — yaklaşık 5 dakika sürer (6 bölüm)
2. **Otomatik plan üretimi** — yaş, mizaç, beslenme bilgisine göre
3. **Soru-cevap (chatbot)** — eğitim sırasında aklınıza takılan her şey

> Sol menüden adımları takip edin. Her bölümü tamamladıktan sonra "İleri ➡️" butonuna basın.

---
"""
)

if st.session_state.profile:
    st.success(f"📋 Şu an kayıtlı **{len(st.session_state.profile)} cevap** var.")
    if st.button("🔄 Sıfırla ve yeniden başla"):
        # Plan + chatbot cache'lerini de temizle ki tekrar üretilsin
        st.session_state.profile = {}
        st.session_state.tamamlandi = set()
        st.session_state.plan = None
        st.session_state.param = None
        st.session_state.chat_history = []
        st.rerun()

st.info(
    "**Başlamak için sol menüden** *1 Bebek Bilgileri* sayfasına gidin."
)

with st.expander("ℹ️ Bu demonun mimarisi"):
    st.markdown(
        """
- **Parameter Engine** — `master_knowledge_base.json` üzerinden lookup + 89 karar kuralı (deterministik). Sayısal değerler asla değiştirilmez.
- **Plan Generator** — Claude API ile parametreleri Türkçe plana dönüştürme. LLM sadece "yazar" rolünde.
- **Chatbot** — TF-IDF + Claude ile RAG soru-cevap (463 chunk, 47 ders).

**Önemli:** Bu demo İlayda Hanım'ın gerçek metodolojisine dayalıdır. Görsel embed yok, kaynak referansı gösterilmiyor (anneye tek tip profesyonel cevap).
"""
    )
