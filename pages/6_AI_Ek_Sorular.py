"""Bölüm 7: AI Ek Sorular (Sorular 33-37) — son bölüm."""
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

st.set_page_config(page_title="AI Ek Sorular", page_icon="🐰", layout="centered")
st.title("Bölüm 7: Son Sorular")
st.caption("Sorular 33-37 — yaklaşım tercihi, dayanma sınırı, beklenti")

profile = st.session_state.profile

with st.form("ek_sorular_form"):
    yaklasim = st.selectbox(
        "33. Yaklaşım tercihiniz hangisi?",
        [
            "5 günlük standart plan (daha hızlı, daha çok ağlama olabilir)",
            "13 günlük kademeli plan (daha yumuşak, daha uzun süreç)",
            "İlayda Hanım'ın önereceği plan (bebeğe göre)",
        ],
        index=2,
    )

    dayanma = st.selectbox(
        "34. Bebeğinizin ağlamasına ne kadar süre dayanabilirsiniz? (gerçekçi)",
        [
            "10 dakikadan az (çok zorlanırım)",
            "10-20 dakika",
            "20-30 dakika",
            "30-45 dakika",
            "45-60 dakika",
            "1 saatten fazla (kararlıyım)",
        ],
        index=2,
    )

    en_zor = st.selectbox(
        "35. Sizin için en zor zaman hangisi?",
        [
            "Gündüz uykuları (kısa uyku, uzatma)",
            "Gece uykusuna geçiş",
            "Gece uyanmaları",
            "Sabah erken uyanma",
            "Hepsi aynı derecede zor",
        ],
        index=2,
    )

    ek_bilgi = st.text_area(
        "36. Eklemek istediğiniz başka bilgi var mı? (varsa)",
        value=profile.get("ek_bilgi", ""),
        placeholder="Örn: kreşe başlayacak, taşınıyoruz, kardeş doğdu, vb.",
        height=100,
    )

    beklenti = st.text_area(
        "37. Eğitimden temel beklentiniz nedir?",
        value=profile.get("beklenti", ""),
        placeholder="Örn: gece kesintisiz 6+ saat uyku, gündüz uykularının düzeli olması, anne dinlenmesi",
        height=100,
    )

    submitted = st.form_submit_button("💾 Kaydet ve Plana Geç ➡️", type="primary")

    if submitted:
        profile.update({
            "yaklasim_tercihi": yaklasim,
            "dayanma_siniri": dayanma,
            "en_zor_zaman": en_zor,
            "ek_bilgi": ek_bilgi.strip(),
            "beklenti": beklenti.strip(),
        })
        st.session_state.tamamlandi.add(6)
        st.success("✅ 37 sorunun hepsi tamamlandı! Sol menüden **7 Plan ve Sor** sayfasına geçin.")
        st.balloons()
