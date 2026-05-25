"""Tavşan Uykusu Premium Demo — ana giriş sayfası (karşılama + soru-cevap)."""
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Tavşan Uykusu Premium Demo",
    page_icon="🐰",
    layout="centered",
)

# Inline session state init (multipage'de her sayfa kendi başına yapıyor)
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

# engine paketini import edebilmek için (pages/'deki desen)
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.chatbot import cevapla, init_index  # noqa: E402

st.title("🐰 Tavşan Uykusu")

st.markdown(
    "Bebeğinizin uykusuyla ilgili sorularınızı aşağıdan sorabilir veya "
    "sol menüden kişiselleştirilmiş uyku planı oluşturabilirsiniz. "
    "Cevaplar, İlayda Hanım'ın eğitim içeriklerinden üretilir."
)

st.divider()

# ---------------------------------------------------------------------------
# Soru-Cevap (ana sayfada doğrudan erişim, plandan bağımsız)
# ---------------------------------------------------------------------------
st.subheader("💬 Soru-Cevap")

# Index'i sayfa açılışında initialize et (ilk soruda spinner görünmesin)
try:
    init_index()
except Exception as e:
    st.warning(f"Soru-cevap motoru yüklenemedi: {e}")

# Mevcut konuşma
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Yeni soru
if soru := st.chat_input("Sorunuzu yazın..."):
    st.session_state.chat_history.append({"role": "user", "content": soru})
    with st.chat_message("user"):
        st.markdown(soru)
    with st.chat_message("assistant"):
        with st.spinner("Düşünüyor..."):
            try:
                cevap = cevapla(soru)
            except Exception as e:
                cevap = f"❌ Cevap üretilemedi: {e}"
        st.markdown(cevap)
        st.session_state.chat_history.append({"role": "assistant", "content": cevap})

if st.session_state.chat_history:
    if st.button("🧹 Sohbeti temizle"):
        st.session_state.chat_history = []
        st.rerun()

st.divider()

st.caption(
    "ℹ️ Kişisel uyku planı için sol menüden **1 Bebek Bilgileri** ile başlayın "
    "(37 soruluk profil, yaklaşık 5 dakika)."
)

if st.session_state.profile:
    st.success(f"📋 Şu an kayıtlı **{len(st.session_state.profile)} cevap** var.")
    if st.button("🔄 Profili sıfırla ve yeniden başla"):
        st.session_state.profile = {}
        st.session_state.tamamlandi = set()
        st.session_state.plan = None
        st.session_state.param = None
        st.session_state.chat_history = []
        st.rerun()
