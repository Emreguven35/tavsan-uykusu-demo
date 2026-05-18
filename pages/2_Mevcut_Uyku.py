"""Bölüm 3: Mevcut Uyku (Sorular 15-21)."""
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

st.set_page_config(page_title="Mevcut Uyku", page_icon="🐰", layout="centered")
st.title("Bölüm 3: Mevcut Uyku Düzeni")
st.caption("Sorular 15-21 — bebeğinizin şu anki uyku alışkanlıkları")

profile = st.session_state.profile

with st.form("mevcut_uyku_form"):
    st.subheader("😴 Mevcut Uyku Düzeni")

    destek = st.selectbox(
        "15. Bebek şu an nasıl uyuyor?",
        [
            "Memeyle (emerek)",
            "Mamayla (biberon emerek)",
            "Sallanarak (kucakta veya beşikte)",
            "Pat-pat / pış-pış ile",
            "Yürüyüş arabasıyla / gezdirerek",
            "Anne kucağında",
            "Tamamen kendi başına",
            "Karışık (birkaç yöntem)",
        ],
        index=0,
        key="destek_select",
    )
    destek_not = st.text_input(
        "Not (isteğe bağlı): Detay yazmak isterseniz",
        value=profile.get("destek_not", ""),
    )

    oda = st.selectbox(
        "16. Bebek nerede uyuyor?",
        [
            "Kendi odasında, kendi beşiğinde",
            "Anne-baba ile aynı odada, kendi beşiğinde",
            "Anne-baba ile aynı yatakta",
            "Anne-baba ile aynı odada, başka ortak yatakta",
        ],
        index=0,
        key="oda_select",
    )

    c1, c2 = st.columns(2)
    with c1:
        gunduz_uyku_sayisi = st.selectbox(
            "17. Bebek gündüz kaç uyku yapıyor?",
            ["1", "2", "3", "4", "5 veya daha fazla", "Düzensiz"],
            index=2,
        )
        gunduz_toplam = st.text_input(
            "18. Gündüz toplam ne kadar uyku yapıyor? (örn: 3 saat)",
            value=profile.get("gunduz_toplam", ""),
            placeholder="Örn: 2-3 saat",
        )
    with c2:
        gece_uyanma = st.selectbox(
            "19. Gece kaç kez uyanıyor?",
            ["Hiç (kesintisiz uyuyor)", "1 kez", "2 kez", "3-4 kez", "5-6 kez", "7+ kez (çok sık)"],
            index=3,
        )
        gune_baslama = st.time_input(
            "20. Bebek güne kaçta başlıyor? (uyandığı saat)",
            value=None,
        )

    yatis_saati = st.time_input(
        "21. Bebek gece kaçta uykuya geçiyor?",
        value=None,
    )

    submitted = st.form_submit_button("💾 Kaydet ve İleri ➡️", type="primary")

    if submitted:
        profile.update({
            "destek": destek,
            "destek_not": destek_not.strip(),
            "oda": oda,
            "gunduz_uyku_sayisi": gunduz_uyku_sayisi,
            "gunduz_toplam": gunduz_toplam.strip(),
            "gece_uyanma": gece_uyanma,
            "gune_baslama": gune_baslama.isoformat() if gune_baslama else "",
            "yatis_saati": yatis_saati.isoformat() if yatis_saati else "",
        })
        st.session_state.tamamlandi.add(2)
        st.success("✅ Kaydedildi! Sol menüden **3 Yaşam Ortamı** sayfasına geçin.")
