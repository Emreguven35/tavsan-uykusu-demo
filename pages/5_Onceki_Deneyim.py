"""Bölüm 6: Önceki Deneyim (Sorular 31-32)."""
import streamlit as st

st.set_page_config(page_title="Önceki Deneyim", page_icon="🐰", layout="centered")
st.title("Bölüm 6: Önceki Uyku Eğitimi Deneyimi")
st.caption("Sorular 31-32 — daha önce denediğiniz yöntemler")

if "profile" not in st.session_state:
    st.session_state.profile = {}
profile = st.session_state.profile

with st.form("onceki_deneyim_form"):
    onceki_denedi = st.radio(
        "31. Daha önce uyku eğitimi denediniz mi?",
        ["Hayır, ilk kez deneyeceğiz", "Evet, denedim ama başarısız oldu", "Evet, denedim ve kısmen başarılı"],
        index=0,
    )

    onceki_yontemler = st.text_area(
        "32. Daha önce hangi yöntemleri denediniz? (Yoksa boş bırakın)",
        value=profile.get("onceki_yontemler", ""),
        placeholder=(
            "Örn: Ferber metodu, yatır-bırak, ağlamayı dinlemek, "
            "anneanne tavsiyesi, internetteki yöntemler..."
        ),
        height=120,
    )

    submitted = st.form_submit_button("💾 Kaydet ve İleri ➡️", type="primary")

    if submitted:
        profile.update({
            "onceki_denedi": onceki_denedi,
            "onceki_yontemler": onceki_yontemler.strip(),
        })
        st.session_state.tamamlandi.add(5)
        st.success("✅ Kaydedildi! Sol menüden **6 AI Ek Sorular** sayfasına geçin.")
