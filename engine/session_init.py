"""
Session State Init Modülü

Tüm sayfalarda kullanılan ortak state değişkenlerini başlatır.
Streamlit multipage mimarisinde her sayfa bağımsız çalıştığı için,
her sayfanın ve app.py'nin EN BAŞINDA init_session_state() çağrılmalı.

Aksi halde örnek hata:
    AttributeError: st.session_state has no attribute "tamamlandi"
"""
import streamlit as st


def init_session_state() -> None:
    """Eksik session state değişkenlerini başlat (var olanlara dokunma)."""
    # 37 sorudan toplanan tüm profil cevapları (dict)
    if "profile" not in st.session_state:
        st.session_state.profile = {}

    # Hangi form bölümlerinin tamamlandığı (set, 1-7 arası)
    if "tamamlandi" not in st.session_state:
        st.session_state.tamamlandi = set()

    # Aktif adım sırası (int) — gelecek kullanım için
    if "step" not in st.session_state:
        st.session_state.step = 0

    # Plan + chatbot ekranındaki cache değerler
    if "plan" not in st.session_state:
        st.session_state.plan = None  # str (üretilmişse markdown), yoksa None

    if "param" not in st.session_state:
        st.session_state.param = None  # dict (parametre_uret çıktısı), yoksa None

    # Chatbot konuşma geçmişi (list[dict])
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
