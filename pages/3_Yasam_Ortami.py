"""Bölüm 4: Yaşam Ortamı (Sorular 22-27)."""
import sys
from pathlib import Path

import streamlit as st

# Engine import yolu
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.session_init import init_session_state

# Streamlit multipage'de her sayfa bağımsız çalışır — her sayfada init zorunlu
init_session_state()

st.set_page_config(page_title="Yaşam Ortamı", page_icon="🐰", layout="centered")
st.title("Bölüm 4: Yaşam Ortamı ve Beslenme")
st.caption("Sorular 22-27 — uyku odası ve beslenme alışkanlıkları")

profile = st.session_state.profile

with st.form("yasam_ortami_form"):
    st.subheader("🏠 Uyku Odası")
    c1, c2 = st.columns(2)
    with c1:
        karartma = st.radio(
            "22. Odada karartma perdesi var mı?",
            ["Evet, tam karartma", "Kısmen (loş)", "Hayır"],
            index=0,
        )
        beyaz_gurultu = st.radio(
            "23. Beyaz gürültü / ses desteği kullanıyor musunuz?",
            ["Evet, sürekli", "Sadece bazen", "Hayır"],
            index=2,
        )
    with c2:
        oda_sicakligi = st.number_input(
            "24. Oda sıcaklığı kaç °C? (ortalama)",
            min_value=15.0, max_value=30.0, value=21.0, step=0.5,
        )

    st.divider()
    st.subheader("🍼 Beslenme")
    beslenme = st.selectbox(
        "25. Beslenme tipi",
        [
            "Sadece anne sütü",
            "Sadece mama (biberon)",
            "Karışık (anne sütü + mama)",
            "Anne sütü + ek gıda",
            "Mama + ek gıda",
            "Karışık + ek gıda",
            "Sadece ek gıda (sütten kesilmiş)",
        ],
        index=0,
    )

    son_beslenme = st.selectbox(
        "26. Son beslenme uykudan ne kadar önce yapılıyor?",
        [
            "Uykudan hemen önce (memeyle/mamayla uyuyor)",
            "10-20 dakika önce",
            "30 dakika önce",
            "45-60 dakika önce",
            "1 saatten fazla önce",
        ],
        index=0,
        key="son_beslenme_select",
    )

    emzik = st.radio(
        "27. Emzik kullanıyor mu?",
        [
            "Hayır, hiç kullanmıyor",
            "Evet, uykuda kullanıyor (ağzına düşerse anne takıyor)",
            "Evet, uykuda kullanıyor (kendi takabiliyor)",
            "Evet, sadece uyku dışında kullanıyor",
        ],
        index=0,
    )

    submitted = st.form_submit_button("💾 Kaydet ve İleri ➡️", type="primary")

    if submitted:
        # Karartma kısaca normalize et
        karartma_norm = "Hayır" if karartma == "Hayır" else "Evet"
        profile.update({
            "karartma_perdesi": karartma_norm,
            "karartma_detay": karartma,
            "beyaz_gurultu": beyaz_gurultu,
            "oda_sicakligi": f"{oda_sicakligi}°C",
            "oda_sicakligi_num": float(oda_sicakligi),
            "beslenme": beslenme,
            "son_beslenme_zaman": son_beslenme,
            "emzik": emzik,
        })
        st.session_state.tamamlandi.add(3)
        st.success("✅ Kaydedildi! Sol menüden **4 Gelişim** sayfasına geçin.")
