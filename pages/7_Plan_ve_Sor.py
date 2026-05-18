"""Plan + Chatbot sayfası — 37 cevap üzerinden plan üret ve soru-cevap."""
import streamlit as st

# Streamlit multipage'de her sayfa bağımsız çalışır — inline session state init
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

import sys
from pathlib import Path

from dotenv import load_dotenv

# engine paketini import edebilmek için
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv()

from engine.parameter_engine import parametre_uret  # noqa: E402
from engine.plan_generator import plan_uret  # noqa: E402
from engine.chatbot import cevapla, init_index  # noqa: E402

st.set_page_config(page_title="Plan ve Sor", page_icon="🐰", layout="centered")
st.title("🐰 Kişisel Uyku Eğitimi Planınız")

if not st.session_state.profile.get("dogum_tarihi"):
    st.warning("⚠️ Önce diğer sayfalardaki 37 soruyu cevaplayın. *1 Bebek Bilgileri* sayfasından başlayın.")
    st.stop()

profile = st.session_state.profile

# ---------------------------------------------------------------------------
# Plan üretimi (cache)
# init_session_state() plan'ı None olarak kuruyor; bu yüzden "is None" kontrolü.
# ---------------------------------------------------------------------------
if st.session_state.plan is None:
    with st.spinner("📋 Kişisel planınız oluşturuluyor... (15-30 saniye)"):
        try:
            param = parametre_uret(profile)
            st.session_state.param = param
            plan_text = plan_uret(param)
            st.session_state.plan = plan_text
        except Exception as e:
            st.error(f"❌ Plan oluşturulurken hata: {e}")
            st.exception(e)
            st.stop()

param = st.session_state.param
if param is None:
    st.error("Plan parametreleri yüklenemedi. Lütfen sayfayı yenileyin.")
    st.stop()

# Üst bant: özet
c1, c2, c3 = st.columns(3)
c1.metric("Yaş (düzeltilmiş)", f"{param['yas']['duzeltilmis_ay']:.1f} ay")
c2.metric("Plan tipi", f"{param['plan_secimi']['gunler']} gün")
c3.metric("Uygunluk", "✅ Uygun" if param["uygun_mu"] else "⛔ Beklemeli")

st.divider()

# Plan markdown'unu göster
st.markdown(st.session_state.plan)

# Plan indirme
st.download_button(
    "📥 Planı indir (Markdown)",
    data=st.session_state.plan.encode("utf-8"),
    file_name=f"{profile.get('bebek_ad', 'bebek')}_uyku_plani.md",
    mime="text/markdown",
)

st.divider()

# ---------------------------------------------------------------------------
# Chatbot
# ---------------------------------------------------------------------------
st.subheader("💬 Soru-Cevap")
st.markdown(
    "Eğitim sürecinde aklınıza takılan soruları aşağıya yazın. "
    "Tavşan Uykusu içeriklerinden cevap üretilir."
)

# chat_history zaten init_session_state() ile [] olarak set edildi

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
