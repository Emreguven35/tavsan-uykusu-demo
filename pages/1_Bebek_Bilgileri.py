"""Bölüm 1-2: Bebek Bilgileri (Sorular 1-14)."""
import sys
from pathlib import Path

import streamlit as st
from datetime import date

# Engine import yolu
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.session_init import init_session_state

# Streamlit multipage'de her sayfa bağımsız çalışır — her sayfada init zorunlu
init_session_state()

st.set_page_config(page_title="Bebek Bilgileri", page_icon="🐰", layout="centered")
st.title("Bölüm 1-2: Bebek Bilgileri")
st.caption("Sorular 1-14 — yaklaşık 1 dakika")

profile = st.session_state.profile

with st.form("bebek_bilgileri_form"):
    st.subheader("🪪 İletişim Bilgileri")
    c1, c2 = st.columns(2)
    with c1:
        bebek_ad = st.text_input("1. Bebeğin adı soyadı *", value=profile.get("bebek_ad", ""))
        anne_ad = st.text_input("2. Annenin adı soyadı *", value=profile.get("anne_ad", ""))
        baba_ad = st.text_input("3. Babanın adı soyadı", value=profile.get("baba_ad", ""))
        baba_tel = st.text_input("4. Babanın cep telefonu", value=profile.get("baba_tel", ""))
    with c2:
        baba_durum = st.radio(
            "5. Baba ile birliktelik durumu",
            ["Birlikte", "Ayrı"],
            index=0 if profile.get("baba_durum", "Birlikte") == "Birlikte" else 1,
        )
        anne_meslek = st.text_input("6. Anne mesleği", value=profile.get("anne_meslek", ""))
        baba_meslek = st.text_input("7. Baba mesleği", value=profile.get("baba_meslek", ""))
        adres = st.text_input("8. Adres (il / ilçe)", value=profile.get("adres", ""))
        instagram = st.text_input("9. Instagram kullanıcı adı", value=profile.get("instagram", ""))

    st.divider()
    st.subheader("👶 Bebek Bilgileri")
    c3, c4 = st.columns(2)
    with c3:
        dogum_tarihi = st.date_input(
            "10. Bebeğin doğum tarihi *",
            max_value=date.today(),
            value=date.fromisoformat(profile["dogum_tarihi"]) if profile.get("dogum_tarihi") else date(2025, 11, 18),
        )
        dogum_haftasi = st.number_input(
            "11. Bebek kaç haftalık doğdu? (40 = miadında)",
            min_value=24, max_value=42,
            value=int(profile.get("dogum_haftasi", 40)),
        )
        egitim_tarihi = st.date_input(
            "12. Planladığınız eğitim başlangıç tarihi",
            min_value=date.today(),
            value=(
                date.fromisoformat(profile["egitim_tarihi"])
                if profile.get("egitim_tarihi") and date.fromisoformat(profile["egitim_tarihi"]) >= date.today()
                else date.today()
            ),
        )
    with c4:
        saglik = st.text_area(
            "13. Bebeğin sağlık problemi var mı? (yoksa 'yok' yazın)",
            value=profile.get("saglik_problemi", "yok"),
            height=80,
        )
        ayri_kalma = st.text_area(
            "14. Doğum sırasında veya sonrasında bebekle ayrı kaldınız mı?",
            value=profile.get("ayri_kalma", ""),
            placeholder="Örn: yenidoğan yoğun bakım, hastane farkı, kuvöz...",
            height=80,
        )

    submitted = st.form_submit_button("💾 Kaydet ve İleri ➡️", type="primary")

    if submitted:
        if not bebek_ad.strip() or not anne_ad.strip():
            st.error("Zorunlu alanları doldurun (*).")
        else:
            profile.update({
                "bebek_ad": bebek_ad.strip(),
                "anne_ad": anne_ad.strip(),
                "baba_ad": baba_ad.strip(),
                "baba_tel": baba_tel.strip(),
                "baba_durum": baba_durum,
                "anne_meslek": anne_meslek.strip(),
                "baba_meslek": baba_meslek.strip(),
                "adres": adres.strip(),
                "instagram": instagram.strip(),
                "dogum_tarihi": dogum_tarihi.isoformat(),
                "dogum_haftasi": int(dogum_haftasi),
                "egitim_tarihi": egitim_tarihi.isoformat(),
                "saglik_problemi": saglik.strip(),
                "ayri_kalma": ayri_kalma.strip(),
            })
            st.session_state.tamamlandi.add(1)
            st.success("✅ Kaydedildi! Sol menüden **2 Mevcut Uyku** sayfasına geçin.")
