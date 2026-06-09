# -*- coding: utf-8 -*-
"""
Boşluk raporu regresyon testi.
Kapatılan her soruyu, 100-soru setindeki GERÇEK ifadesiyle TF-IDF retrieval'a
sokar; hedef chunk top-5'te mi diye bakar (eşanlamlı/sinonim dayanıklılık testi).
Sonra VAR/KISMEN/YOK sayımını yeniden hesaplar.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import chatbot

chatbot.init_index()  # chunks.json'u taze yükler (artık 49 yeni chunk dahil)

# Q# -> (soru metni, beklenen chunk_id)
CLOSED = {
    # --- YOK -> VAR (17) ---
    11: ("Bebeğim gece kabus mu görüyor, neden çığlıkla uyanıyor? (gece terörü farkı)", "kural_kabus_gece_teroru_001"),
    40: ("Parmak emerek uyuyor, engellemeli miyim?", "kural_parmak_emme_001"),
    56: ("Banyo her akşam şart mı?", "kural_banyo_her_aksam_001"),
    59: ("Saat değişiminde (yaz/kış saati) düzeni nasıl korurum?", "kural_yaz_kis_saati_001"),
    60: ("Tatilde/seyahatte uyku düzeni nasıl yönetilir? Zaman farkı (jet lag)?", "kural_tatil_jetlag_001"),
    62: ("İkiz bebeklerde uyku düzeni ve eğitim nasıl yapılır? Aynı odada birbirini uyandırıyorlar.", "kural_ikiz_uyku_001"),
    68: ("Aşı sonrası uyku düzeni değişir mi?", "kural_asi_sonrasi_uyku_001"),
    71: ("Kardeş doğunca büyük bebeğin uykusu bozuldu, ne yapmalıyım?", "kural_kardes_dogumu_uyku_001"),
    72: ("Taşınma/oda değişikliği sonrası uyku düzeni nasıl yeniden kurulur?", "kural_tasinma_oda_degisikligi_001"),
    74: ("Bebek ne zaman kendi odasına geçmeli?", "kural_kendi_odaya_gecis_001"),
    76: ("Yüzüstü dönüyor uykuda, sırtüstüne çevirmeli miyim? (SIDS riski)", "kural_sids_yuzustu_guvenli_uyku_001"),
    77: ("Kundak ne zamana kadar güvenli, nasıl bırakılır?", "kural_kundak_birakma_001"),
    78: ("Uyku tulumu mu battaniye mi? Battaniye ne zaman güvenli?", "kural_uyku_tulumu_battaniye_001"),
    86: ("Reflüsü/gazı olan bebekte uyku eğitimi yapılabilir mi?", "kural_reflu_gaz_egitim_001"),
    90: ("Yatmadan önce hangi besinler uykuyu destekler, hangileri bozar?", "kural_besinler_uyku_001"),
    93: ("Komşular ağlama sesinden şikayet ediyor, apartmanda eğitim nasıl yapılır?", "kural_komsu_apartman_aglama_001"),
    98: ("Bebeğim horluyor/ağzı açık uyuyor/nefesi duruyor gibi, ne zaman doktora gitmeliyim?", "kural_horlama_apne_001"),
    # --- KISMEN -> VAR (35) ---
    1: ("Bebeğim gece neden bu kadar sık uyanıyor?", "kural_gece_uyanma_nedenleri_001"),
    2: ("Gece kaç kez uyanması normal? (yaşa göre)", "kural_gece_uyanma_sikligi_normal_001"),
    5: ("Bebeğim gece uyanıp saatlerce uyumuyor, ne yapmalıyım? (split night)", "kural_split_night_001"),
    8: ("Gece uyanıp ağlamadan mızırdanıyorsa içeri girmeli miyim?", "kural_mizirdanma_aglama_001"),
    13: ("Uyku eğitimine başlamak için en uygun yaş nedir?", "kural_baslama_yasi_001"),
    17: ("Eğitim sırasında bebek kusarsa ne yapmalıyım?", "kural_egitimde_kusma_001"),
    18: ("Eğitime başladık ama 3. gün daha kötüye gitti, normal mi? (extinction burst)", "kural_3_gun_kotulesme_001"),
    19: ("Eğitimi yarıda bırakırsak ne olur, baştan mı başlarız?", "kural_yarida_birakma_001"),
    22: ("Eşim/kayınvalidem eğitime karşı çıkıyor, dayanamayıp kucağa alıyor, ne yapmalıyım?", "kural_aile_direnci_001"),
    24: ("Gündüz ve gece eğitimini aynı anda mı başlatmalıyım?", "kural_gunduz_gece_ayni_anda_001"),
    25: ("Bebeğim eğitim sırasında saçını çekiyor / başını vuruyor, durdurmalı mıyım?", "kural_kendine_zarar_sac_bas_001"),
    26: ("Kademeli yöntem mi hızlı yöntem mi daha iyi?", "kural_kademeli_hizli_yontem_001"),
    32: ("Bebeğim sadece üzerimde uyuyor, yatağa koyunca uyanıyor (transfer sorunu).", "kural_destekle_uyuma_birakma_001"),
    34: ("Bebeğim elimi tutmadan/saçımı çekmeden uyumuyor.", "kural_destekle_uyuma_birakma_001"),
    37: ("Uyku arkadaşı (battaniye, oyuncak) ne zaman ve nasıl verilir, güvenli mi?", "kural_uyku_arkadasi_guvenlik_001"),
    38: ("Bebeğim yatakta sürekli dönüyor/emekliyor, uyumak yerine oynuyor.", "kural_yatakta_oynama_001"),
    45: ("Gündüz uykusunu evde yatağında yapmıyor ama kreşte uyuyor (veya tersi).", "kural_evde_uyumuyor_kreste_001"),
    47: ("Gündüz çok uyursa gece uyumaz mı?", "kural_gunduz_fazla_az_uyku_001"),
    50: ("Aşırı yorgunluk (overtired) belirtileri neler, ne yapmalıyım?", "kural_asiri_yorgunluk_belirtileri_001"),
    52: ("Kreşe başlayınca uyku düzeni nasıl korunur?", "kural_krese_baslama_duzen_001"),
    58: ("Hafta sonu düzen bozulursa her şey baştan mı başlar?", "kural_hafta_sonu_bozulma_001"),
    61: ("Bayram/misafir yoğunluğunda düzen bozuldu, nasıl toparlarım?", "kural_bayram_misafir_001"),
    63: ("4 ay uyku gerilemesi nedir, ne kadar sürer, ne yapmalıyım?", "kural_regresyon_donemleri_001"),
    64: ("8-10 ay uyku gerilemesi ve ayrılık kaygısı döneminde nasıl davranmalıyım?", "kural_regresyon_donemleri_001"),
    65: ("12 ay ve 18 ay gerilemeleri gerçek mi?", "kural_regresyon_donemleri_001"),
    70: ("Büyüme atağı (growth spurt) döneminde gece sık uyanıp besleniyor, normal mi?", "kural_buyume_atagi_001"),
    75: ("Aynı yatakta uyumak (co-sleeping) güvenli mi? Nasıl bırakılır?", "kural_cosleeping_guvenlik_birakma_001"),
    81: ("Bebeğim yatakta oturuyor/ayağa kalkıyor, beşik güvenli mi hâlâ?", "kural_besik_guvenligi_001"),
    83: ("Ek gıdaya geçince uyku düzelir mi?", "kural_ek_gida_uyku_001"),
    84: ("Yatmadan önce doyurursam gece daha uzun uyur mu? (dream feed dahil)", "kural_dream_feed_001"),
    87: ("Anne sütü bebeği biberon bebeğinden daha mı sık uyanır?", "kural_anne_sutu_uyanma_001"),
    92: ("Ben dayanamıyorum, ağlamasını duyunca kalbim parçalanıyor, ne yapmalıyım?", "kural_aglamaya_dayanma_001"),
    95: ("Çalışan anneyim, akşam az görüyorum, eğitim yüzünden kalan vaktimiz ağlamayla mı geçecek?", "kural_calisan_anne_001"),
    96: ("Bebeğim gündüz bakıcıda/anneannede, herkes farklı uyutuyor, tutarlılığı nasıl sağlarım?", "kural_coklu_bakici_tutarlilik_001"),
    97: ("Uyku eğitimi başarısız oldu, bebeğim \"eğitilemez\" mi, tekrar ne zaman denemeliyim?", "kural_egitilemez_mi_001"),
}

# Bonus: master KB merdivenini de besleyen Q4 (zaten VAR, sayıma katılmaz)
BONUS = {4: ("Gece beslenmelerini ne zaman ve nasıl keserim?", "kural_gece_besleme_merdiveni_001")}

# KISMEN olarak KALAN sorular (kapatılmadı) + neden
KISMEN_KALAN = {
    42: "Gündüz hiç uyumayan bebek senaryosu yeni kayıtta doğrudan işlenmedi.",
    44: "Uyku sayısı geçişinde toparlama adımları yüzeysel kaldı.",
    55: "Geç yatışı erkene çekme ADIMLARI değil, sadece nedeni verildi.",
    66: "Diş + ağrı kesici (tıbbi kısım) bu turda gelmedi.",
    73: "Oda 22-23°C verildi ama TOG/giydirme TABLOSU İlayda tarafından iletilecek.",
    80: "Beşikten yatağa yaşı transkriptte şüpheli ('beşinci yaş'); doğrulanana dek atlandı.",
    99: "Demir/magnezyum/D-vit nedenleri zenginleşti ama tıbbi yönlendirme gereği KISMEN.",
    100: "Postpartum/lohusa emniyet cümlesini İlayda tıbbi sınır kaydında yarın gönderecek.",
}

def run(items, label):
    print(f"\n===== {label} =====")
    ok = miss = 0
    fails = []
    for q in sorted(items):
        soru, hedef = items[q]
        res = chatbot.retrieve(soru, top_k=5)
        ids = [c["chunk_id"] for c in res]
        if hedef in ids:
            rank = ids.index(hedef) + 1
            score = res[rank - 1]["_score"]
            print(f"  ✅ Q{q:<3} rank#{rank} score={score:.3f}  {hedef}")
            ok += 1
        else:
            top = ids[0] if ids else "(boş)"
            print(f"  ❌ Q{q:<3} HEDEF YOK → top1={top}  (beklenen: {hedef})")
            fails.append(q)
            miss += 1
    print(f"  -> {ok}/{ok+miss} hedef chunk top-5'te")
    return fails

fails = run(CLOSED, f"KAPATILAN SORULAR (retrieval doğrulama) — {len(CLOSED)} soru")
run(BONUS, "BONUS (Q4 gece besleme merdiveni — zaten VAR)")

# ---- VAR/KISMEN/YOK yeniden sayım ----
base_var, base_kismen, base_yok = 40, 43, 17
yok_to_var = 17
kismen_to_var = 35
new_var = base_var + yok_to_var + kismen_to_var
new_kismen = base_kismen - kismen_to_var
new_yok = base_yok - yok_to_var

print("\n===== VAR / KISMEN / YOK SAYIMI =====")
print(f"  ÖNCE :  VAR={base_var:>3}  KISMEN={base_kismen:>3}  YOK={base_yok:>3}  (toplam {base_var+base_kismen+base_yok})")
print(f"  SONRA:  VAR={new_var:>3}  KISMEN={new_kismen:>3}  YOK={new_yok:>3}  (toplam {new_var+new_kismen+new_yok})")
print(f"  Δ    :  VAR +{new_var-base_var}   KISMEN {new_kismen-base_kismen}   YOK {new_yok-base_yok}")

print("\n  KISMEN olarak KALAN sorular:")
for q in sorted(KISMEN_KALAN):
    print(f"    Q{q:<3} — {KISMEN_KALAN[q]}")

if fails:
    print(f"\n  ⚠️ Retrieval'da hedefe ulaşmayan: {fails}")
else:
    print("\n  ✅ Tüm kapatılan soruların hedef chunk'ı top-5'te bulundu.")
