"""Bölüm 5: Gelişim (Sorular 28-30)."""
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

st.set_page_config(page_title="Gelişim", page_icon="🐰", layout="centered")
st.title("Bölüm 5: Gelişim ve Mizaç")
st.caption("Sorular 28-30 — motor gelişim, diş ve mizaç")

profile = st.session_state.profile

with st.form("gelisim_form"):
    st.subheader("👶 Motor Gelişim ve Mizaç")

    motor = st.multiselect(
        "28. Bebek aşağıdakilerden hangilerini yapabiliyor? (Birden fazla seçebilirsiniz)",
        [
            "Sırtüstü → yan dönüyor",
            "Sırtüstü → yüzüstü tam dönüyor",
            "Yüzüstü kafasını kaldırıyor",
            "Destekli oturuyor",
            "Tek başına oturuyor",
            "Emekliyor",
            "Tutunup ayağa kalkıyor",
            "Tutunarak yürüyor",
            "Bağımsız yürüyor",
            "Henüz hiçbiri (sırtüstü kalıyor)",
        ],
        default=profile.get("motor", []),
    )

    dis = st.selectbox(
        "29. Diş çıkarma durumu",
        [
            "Hiç dişi yok",
            "1-2 dişi var, şu an çıkarmıyor",
            "1-2 dişi var, şu an çıkarıyor (ağrılı)",
            "Birkaç dişi var, şu an çıkarmıyor",
            "Birkaç dişi var, şu an çıkarıyor (ağrılı)",
        ],
        index=0,
    )

    mizac = st.selectbox(
        "30. Bebeğin mizacını nasıl tanımlarsınız?",
        [
            "Sakin / uyumlu",
            "Aktif / enerjik ama uyumlu",
            "Hassas / kolay üzülen",
            "İnatçı / kararlı",
            "Huysuz / sürekli ağlayan",
            "Karışık (bazen sakin, bazen huysuz)",
        ],
        index=0,
    )

    submitted = st.form_submit_button("💾 Kaydet ve İleri ➡️", type="primary")

    if submitted:
        profile.update({
            "motor": motor,
            "motor_str": ", ".join(motor) if motor else "henüz hiçbiri",
            "dis": dis,
            "mizac": mizac,
        })
        st.session_state.tamamlandi.add(4)
        st.success("✅ Kaydedildi! Sol menüden **5 Önceki Deneyim** sayfasına geçin.")
