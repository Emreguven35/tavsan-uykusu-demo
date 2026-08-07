"""
Parameter Engine — 37 cevap → kişisel parametreler.

Sayısal değerler ASLA LLM'den gelmez (deterministik):
  • Yaş bandı sayıları (uyanıklık penceresi, uyku sayısı, gündüz/gece uyku süresi)
    → data/yas_bantlari.json (İlayda tablosu, Faz Y). TEK KAYNAK.
  • Diğer bant içeriği (örnek program, gece beslenme notları, görsel referanslar)
    → master_knowledge_base.json.
"""
import json
from datetime import datetime, date
from pathlib import Path
from typing import Any

from engine import yas_bantlari

# ---------------------------------------------------------------------------
# Veri yükleme — data/ klasörü app.py'nin yanında
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_json(name: str) -> dict:
    with open(DATA_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


def load_kb() -> dict:
    """master_knowledge_base.json — audio + visual + coverage birleşik veritabanı."""
    return _load_json("master_knowledge_base.json")


def load_decision_tree() -> dict:
    """decision_tree_extraction.json — 89 karar kuralı + parameter table."""
    return _load_json("decision_tree.json")


# ---------------------------------------------------------------------------
# Yaş hesabı
# ---------------------------------------------------------------------------
def hesapla_yas_ay(dogum_tarihi: str, dogum_haftasi: int = 40) -> dict:
    """
    Bebeğin gerçek yaşını ve düzeltilmiş yaşını ay olarak hesapla.
    Prematüre ise: her 4 hafta erkenlik = 1 ay geri (kayıt40 kuralı).
    """
    dt = datetime.strptime(dogum_tarihi, "%Y-%m-%d").date()
    today = date.today()
    gercek_ay = max(0, (today - dt).days / 30.44)

    duzeltilmis_ay = gercek_ay
    if dogum_haftasi < 40:
        erken_hafta = 40 - dogum_haftasi
        duzeltilmis_ay = max(0, gercek_ay - (erken_hafta / 4))

    return {
        "gercek_ay": round(gercek_ay, 1),
        "duzeltilmis_ay": round(duzeltilmis_ay, 1),
        "prematüre_mi": dogum_haftasi < 37,
        "dogum_haftasi": dogum_haftasi,
    }


def yas_bucket_sec(yas_ay: float) -> str:
    """master_knowledge_base'deki uygun bucket'ı seç (düzeltilmiş ay üzerinden)."""
    if yas_ay < 1.5:
        return "0-6_hafta"
    elif yas_ay < 3:
        return "7-12_hafta"
    elif yas_ay < 4:
        return "3_ay"
    elif yas_ay < 5:
        return "4_ay"
    elif yas_ay < 6:
        return "5_ay"
    elif yas_ay < 7:
        return "6_ay"
    elif yas_ay < 8:
        return "7_ay"
    elif yas_ay < 9:
        return "8_ay"
    elif yas_ay < 10:
        return "9_ay"
    elif yas_ay < 11:
        return "10_ay"
    elif yas_ay < 12:
        return "11_ay"
    elif yas_ay < 13:
        return "12_ay"
    elif yas_ay < 15:
        return "12-13_ay"
    elif yas_ay < 18:
        return "15-17_ay"
    elif yas_ay < 19:
        return "18_ay"
    elif yas_ay < 24:
        return "18-24_ay"
    elif yas_ay < 36:
        return "2-3_yas"
    else:
        return "40_ay_buyuk_cocuk"


# ---------------------------------------------------------------------------
# Eğitim uygunluğu — Red Flag kontrolü
# ---------------------------------------------------------------------------
KRITIK_HASTALIKLAR = ["kalp", "ritim", "epilepsi", "astım", "reflü", "nefes", "solunum"]


def _lowstr(v: Any) -> str:
    return str(v or "").lower().strip()


def egitim_uygunlugu_kontrol(profile: dict, yas: dict) -> tuple[bool, list[str]]:
    """
    Red flag tarama. Karar ağacındaki "yas_alt_siniri" ve "red_flags" kuralları.
    """
    uyarilar: list[str] = []
    uygun = True

    # 5. ay alt sınırı (kayıt37 — kademeli kavram bu yaşta gelişir)
    if yas["duzeltilmis_ay"] < 5:
        uygun = False
        uyarilar.append(
            f"⛔ Bebeğiniz düzeltilmiş yaşa göre {yas['duzeltilmis_ay']:.1f} aylık. "
            "Uyku eğitimi 5. ayını dolduran bebekler için uygundur. "
            "Şimdilik sadece saat planlaması yapabilirsiniz, eğitim ilerideki haftalarda."
        )

    # Prematüre düzeltme uyarısı
    if yas["prematüre_mi"]:
        uyarilar.append(
            f"ℹ️ Bebeğiniz prematüre (doğum {yas['dogum_haftasi']} haftalık). "
            f"Tüm hesaplar düzeltilmiş yaş üzerinden ({yas['duzeltilmis_ay']:.1f} ay) yapıldı."
        )

    # Sağlık problemleri
    saglik = _lowstr(profile.get("saglik_problemi"))
    if saglik and saglik not in ("yok", "hayır", "yoktur", "none", "-"):
        if any(k in saglik for k in KRITIK_HASTALIKLAR):
            uyarilar.append(
                "⚠️ Sağlık sorununuz var. Eğitime başlamadan önce çocuk doktorunuzdan "
                "ONAY almanız ZORUNLU. Doktor onayı olmadan eğitim verilmez."
            )
            uygun = False
        else:
            uyarilar.append(
                f"ℹ️ Sağlık notu: «{profile.get('saglik_problemi')}». "
                "Şiddetli ise doktor onayı almanız önerilir."
            )

    # Histerik ağlama profili — gece beslenme sayısı çok yüksekse
    gece_uyanma = _lowstr(profile.get("gece_uyanma"))
    if any(n in gece_uyanma for n in ["5", "6", "7", "8", "9", "10"]):
        if yas["duzeltilmis_ay"] >= 6:
            uyarilar.append(
                "ℹ️ Bebeğiniz 6+ aylık ve gece çok uyanıyor. Önce emerek uyuma "
                "alışkanlığını değiştirme ve 3 gündüz uykusu (toplam min 3 saat) "
                "düzeni eğitim öncesi hazırlık olarak ele alınmalı."
            )

    return uygun, uyarilar


# ---------------------------------------------------------------------------
# Ön hazırlık — eğitim başlamadan önce yapılması gerekenler
# ---------------------------------------------------------------------------
def on_hazirlik_belirle(profile: dict, yas: dict) -> list[dict]:
    """Eğitim öncesi 3-5 günlük hazırlık adımları."""
    hazirliklar: list[dict] = []

    # Emerek uyuma → sallanarak geçiş (kayıt40)
    destek = _lowstr(profile.get("destek"))
    if any(k in destek for k in ["meme", "emerek", "emzir"]):
        hazirliklar.append({
            "konu": "Emerek uyuma alışkanlığını değiştirme",
            "sure": "3-5 gün",
            "aksiyon": (
                "Memeyle uyutmayı bırakıp sallanarak uyutmaya geç. "
                "En az 3 başarılı seans sonra eğitime başlayabilirsiniz. "
                "Beslenme uykudan en az 30-45 dakika önce bitsin."
            ),
        })
    elif "salla" in destek:
        hazirliklar.append({
            "konu": "Sallanma desteğini azaltma",
            "sure": "Eğitimle birlikte",
            "aksiyon": "Eğitimin 1. gününde sallanmayı bırakıp pat-pat/pış-pış'a geç.",
        })

    # Oda durumu (kayıt40)
    oda = _lowstr(profile.get("oda"))
    if "aynı yatak" in oda or "beraber yat" in oda or "ortak yatak" in oda:
        hazirliklar.append({
            "konu": "Ortak yataktan beşiğe geçiş",
            "sure": "İlk hazırlık (3-5 gün)",
            "aksiyon": (
                "Önce bebeği kendi beşiğine geçirin. Kademeli olarak (gündüz uykularıyla "
                "başlayıp gece uykusuna). Eğitime ondan SONRA başlayın."
            ),
        })
    elif "aynı oda" in oda or "ortak oda" in oda:
        hazirliklar.append({
            "konu": "Aynı odada görsel ayrım",
            "sure": "Hemen",
            "aksiyon": (
                "Bebek anneyi GÖRMEYECEK pozisyonda olmalı. Paravan veya tavandan "
                "korniş + perde ile görsel ayrım sağlayın. Bu olmadan eğitim verim alamaz."
            ),
        })

    # Karartma perdesi
    if _lowstr(profile.get("karartma_perdesi")) in ("hayır", "yok", "no", "false"):
        hazirliklar.append({
            "konu": "Karartma perdesi",
            "sure": "Hemen",
            "aksiyon": "Karartma perdesi ekleyin. Gündüz uykularında loş, gece uykusunda zifiri karanlık olmalı.",
        })

    # Oda sıcaklığı (kayıt37)
    sicaklik_str = _lowstr(profile.get("oda_sicakligi"))
    try:
        sicaklik = float("".join(c for c in sicaklik_str if c.isdigit() or c == "."))
        if sicaklik < 19 or sicaklik > 25:
            hazirliklar.append({
                "konu": "Oda sıcaklığı düzenleme",
                "sure": "Hemen",
                "aksiyon": "Kış 19-22°C, yaz max 25°C aralığına getirin. Termometre kullanın.",
            })
    except (ValueError, TypeError):
        pass

    # Emzik kullanımı + 8 ay öncesi (kayıt40)
    emzik = _lowstr(profile.get("emzik"))
    if "evet" in emzik or "var" in emzik or "kullanıyor" in emzik:
        if yas["duzeltilmis_ay"] < 8:
            hazirliklar.append({
                "konu": "Emzik kullanımı (8 ay öncesi)",
                "sure": "Eğitim sırasında",
                "aksiyon": (
                    "Bebek 8 ay altı: emzikle eğitim verilmez. Eğitim sırasında emzik "
                    "kullanılmayacak. Düşen emziği ANNE TAKMAYACAK. Sadece uyku DIŞI "
                    "saatlerde emzik kullanın."
                ),
            })
        else:
            hazirliklar.append({
                "konu": "Emzik kullanımı (8 ay+)",
                "sure": "Eğitim sırasında",
                "aksiyon": (
                    "Bebek 8+ ay. Düşen emziği KENDİ TAKABİLİYORSA emzikle eğitim olur. "
                    "Aksi halde uyku saatlerinde emzik askıya bağlanmış olmalı; düşerse "
                    "anne takmaz."
                ),
            })

    # Beslenme zamanlaması
    beslenme_zaman = _lowstr(profile.get("son_beslenme_zaman"))
    if beslenme_zaman and ("uyumadan hemen" in beslenme_zaman or "uyurken" in beslenme_zaman):
        hazirliklar.append({
            "konu": "Beslenme zamanlaması",
            "sure": "Hemen",
            "aksiyon": (
                "Beslenmeyi uykudan en az 30 dakika, ideal 45-60 dakika ÖNCE bitirin. "
                "Beslenme rutinden ayrı bir aktivite olmalı. Memeyle/mamayla uyutmayın."
            ),
        })

    # Beyaz gürültü
    beyaz_gurultu = _lowstr(profile.get("beyaz_gurultu"))
    if "evet" in beyaz_gurultu or "kullanıyor" in beyaz_gurultu:
        hazirliklar.append({
            "konu": "Beyaz gürültü kullanımı",
            "sure": "Eğitim sırasında",
            "aksiyon": (
                "Beyaz gürültü her uykuda bir kademe kısılarak azaltılır. İhtiyaç varsa "
                "kullanılmaya devam edilebilir; ancak her uykuda ses bir önceki uykudan daha "
                "kısık olmalı, bir sonraki uykuda daha da azalmalıdır."
            ),
        })

    return hazirliklar


# ---------------------------------------------------------------------------
# Plan seçimi — 5 günlük standart mı 13 günlük dirençli mi
# ---------------------------------------------------------------------------
def _otomatik_yontem_sec(mizac: str, dayanma: str) -> dict:
    """Mizaç + dayanma sınırına göre 5 mi 13 günlük yöntem mi (1 aylık programın 3-4. haftası için)."""
    inatci = any(k in mizac for k in ["inatçı", "huysuz", "zor", "kararlı"])
    hassas = any(k in mizac for k in ["hassas", "duyarlı"])
    dusuk_dayanma = any(d in dayanma for d in ["10", "15 dakika", "20 dakika", "hiç", "az"])
    if (inatci and dusuk_dayanma) or hassas:
        return {
            "tip": "13_gun_dirençli",
            "gunler": 13,
            "aciklama": "13 günlük kademeli yöntem: yatak yanı 1-3 gün, oda ortası 4-6, kapı 7-9, eşik 10-12, yatır-çık 13.",
        }
    return {
        "tip": "5_gun_standart",
        "gunler": 5,
        "aciklama": "5 günlük standart yöntem: yatak yanı 1-2, oda ortası 3, kapı eşiği 4, yatır-çık 5.",
    }


def egitim_plani_secimi(profile: dict, yas: dict) -> dict:
    """5_gun_standart / 13_gun_dirençli / 6_gun_buyuk_cocuk / 1_ay_program."""
    # Büyük çocuk (24+ ay)
    if yas["duzeltilmis_ay"] >= 24:
        return {
            "tip": "6_gun_buyuk_cocuk",
            "gunler": 6,
            "aciklama": "Büyük çocuk planı: 6 günlük (motivasyon panosu + 5 oyuncak + pozitif teşvik dahil).",
        }

    tercih = _lowstr(profile.get("yaklasim_tercihi"))
    mizac = _lowstr(profile.get("mizac"))
    dayanma = _lowstr(profile.get("dayanma_siniri"))

    # 1 aylık yumuşak geçiş programı: ilk 2 hafta destekle uyku (sadece düzen), 3-4. hafta eğitim.
    if "1 ay" in tercih or "aylık program" in tercih:
        alt = _otomatik_yontem_sec(mizac, dayanma)
        return {
            "tip": "1_ay_program",
            "gunler": 28,
            "alt_yontem_tip": alt["tip"],
            "alt_yontem_gunler": alt["gunler"],
            "alt_yontem_aciklama": alt["aciklama"],
            "aciklama": (
                "1 aylık yumuşak geçiş programı. Hafta 1-2 'Düzen Oturtma Dönemi': "
                "destekle uyku devam eder, yalnızca uyku/beslenme saatleri ve rutinler "
                "uygulanır (uyku eğitimi tekniği YOK, biyolojik saat oturtulur). "
                "Hafta 3-4 'Eğitim Dönemi': bebeğe uygun yöntem devreye girer — "
                f"{alt['aciklama']}"
            ),
        }

    # Kullanıcı net istek belirttiyse
    if "13" in tercih or "yumuşak" in tercih or "kademeli" in tercih or "uzun" in tercih:
        return {
            "tip": "13_gun_dirençli",
            "gunler": 13,
            "aciklama": "13 günlük kademeli plan: yatak yanı 1-3 gün, oda ortası 4-6, kapı 7-9, eşik 10-12, yatır-çık 13.",
        }
    if "5" in tercih or "hızlı" in tercih or "standart" in tercih:
        return {
            "tip": "5_gun_standart",
            "gunler": 5,
            "aciklama": "5 günlük standart plan: yatak yanı 1-2, oda ortası 3, kapı eşiği 4, yatır-çık 5.",
        }

    # Otomatik karar: mizaç + dayanma sınırı
    inatci = any(k in mizac for k in ["inatçı", "huysuz", "zor", "kararlı"])
    hassas = any(k in mizac for k in ["hassas", "duyarlı"])
    dusuk_dayanma = any(d in dayanma for d in ["10", "15 dakika", "20 dakika", "hiç", "az"])

    if (inatci and dusuk_dayanma) or hassas:
        return {
            "tip": "13_gun_dirençli",
            "gunler": 13,
            "aciklama": "Mizaç + dayanma sınırı dikkate alınarak 13 günlük kademeli plan önerildi.",
        }

    return {
        "tip": "5_gun_standart",
        "gunler": 5,
        "aciklama": "Standart 5 günlük plan önerildi. Direnç görülürse 13 güne uzatılır.",
    }


# ---------------------------------------------------------------------------
# Spesifik kural uygulayıcılar
# ---------------------------------------------------------------------------
def gece_beslenme_planla(profile: dict, yas: dict) -> dict | None:
    """Gece beslenme yaşa ve kiloya göre."""
    ay = yas["duzeltilmis_ay"]
    if ay < 6:
        return {
            "yas_grup": "6 ay altı",
            "ogun_sayisi": "İhtiyaca göre (uykudayken besle)",
            "saatler": "Bebek talep ettiğinde",
            "kural": "Uyandığında DEĞİL, uykudayken besle. 30-45 dk öncesinden bitir.",
        }
    elif ay < 9:
        kilo_iyi = _lowstr(profile.get("kilo_durumu")).startswith("iyi") or "normal" in _lowstr(profile.get("kilo_durumu"))
        return {
            "yas_grup": "6-9 ay",
            "ogun_sayisi": "Max 2 öğün" if not kilo_iyi else "1-2 öğün (kilo iyi ise 1)",
            "saatler": "00:00 ve 04:00 (kilo iyi ise sadece 00:00 veya 02:00)",
            "kural": "UYKUDAYKEN besle. Uyandığında verme. 6+ ay biyolojik gece beslenme ihtiyacı yoktur, ebeveynlik tercihi.",
        }
    elif ay < 12:
        return {
            "yas_grup": "9-12 ay",
            "ogun_sayisi": "0-1 öğün (doktor onaylı kesme)",
            "saatler": "Tercihen kesilmiş",
            "kural": "Doktor onayıyla gece beslenme kesilir. Su uyutmak için verilmez (su bağımlılığı oluşur).",
        }
    else:
        return {
            "yas_grup": "12+ ay",
            "ogun_sayisi": "0 öğün",
            "saatler": "Yok",
            "kural": "Gece beslenme tamamen kesilmiş olmalı. Su bile uyutmak amacıyla verilmez.",
        }


def bekleme_sureleri_planla(plan_tipi: str) -> dict:
    """Bekleme sürelerini plan tipine göre belirle."""
    if plan_tipi == "13_gun_dirençli":
        kademeli_yer = {
            "gun_1_3": "Beşik yanı (sandalye veya ayakta)",
            "gun_4_6": "Oda ortası (temas ihtiyacını kademeli azaltma; gerekirse temas edip sandalyeye geri dönün)",
            "gun_7_9": "Kapı (içerden, anneye tam görünür)",
            "gun_10_12": "Kapı eşiği (anneye tam görünür — oda müsaitse)",
            "gun_13": "Yatır-çık",
        }
        # 13 günlük dirençli planda kısa gündüz uykusunda dışarıda bekleme daha kısa kademelenir.
        kisa_gunduz = "Dış bekleme kademesi: 1 dakika → 1,5 dakika → 2 dakika (içeri girince o günün pozisyonundan beklemeye devam, toplam ~45 dk hedefi)."
    elif plan_tipi == "6_gun_buyuk_cocuk":
        kademeli_yer = {
            "gun_1_2": "Yatak yanı",
            "gun_3": "Oda ortası (sandalye)",
            "gun_4": "Kapı (içerden, gözler kapalı, uyuyor numarası)",
            "gun_5": "Kapı eşiği (çocuğa tam görünür)",
            "gun_6": "Yatır-çık + anne kendi yatağına gider",
        }
        kisa_gunduz = "Gün 1: dış 5 dk + iç 40 dk. Gün 2: 10+35. Gün 3: 15+30. Toplam her seferinde 45 dk."
    else:  # 5_gun_standart
        kademeli_yer = {
            "gun_1_2": "Beşik yanı (sandalye veya ayakta)",
            "gun_3": "Oda ortası (temas ihtiyacını kademeli azaltma; gerekirse temas edip sandalyeye geri dönün)",
            "gun_4": "Kapı eşiği (içerden, anneye tam görünür, ses desteği azaltılır)",
            "gun_5": "Yatır-çık (odadan tamamen çıkış)",
        }
        kisa_gunduz = "Gün 1: dış 5 dk + iç 40 dk. Gün 2: 10+35. Gün 3: 15+30. Toplam her seferinde 45 dk."

    return {
        "kademeli_uzaklasma": kademeli_yer,
        "kucaktan_almak": "30 saniye → 1 dakika → 1.5 dakika → 2 dakika diye artış",
        "egitim_seans_max": "45 dakika denenme + 15-30 dakika rutin molası + 45 dakika daha (uykuya kadar)",
        "gece_uyanma_dis_bekleme": "1. uyanma 5 dk, sonra her uyanmada +5 dk. Asla 5 dk altına inme.",
        "kisa_gunduz_uykusu": kisa_gunduz,
        "yatir_cik_sonrasi": "5 → 10 → 15 → 20 dk. Max 20-25 dk. 21 gün hiç dalmazsa max 45 dk veya tıbbi yönlendirme.",
        "B_plan_direnç": "45 dakika direnç olursa → 15 dakika rutin molası → 45 dakika yeniden deneme. Çok dirençli bebeklerde rutin molası 30 dakikaya çıkarılabilir. Maksimum 3 tekrar. 3 tekrar sonrasında da uyumuyorsa: o uyku denemesi sonlandırılır, bebek yaşına uygun uyanıklık süresi kadar uyanık tutulur ve bir sonraki uyku denemesinde eğitime devam edilir.",
    }


# ---------------------------------------------------------------------------
# Faz Y — yaş bandı tablosu köprüsü
# ---------------------------------------------------------------------------
def _tek_uyku_mu(profile: dict) -> bool | None:
    """Profilden 12-18 ay tek/çift uyku ayrımını çöz.

    Profil `tek_uyku` (bool) taşıyorsa doğrudan kullanılır. Aksi halde üç geçiş
    şartı ölçülebiliyorsa (ogle_yatis_dk / tek_ogun_uyku_dk / uyaniklik_penceresi_dk)
    değerlendirilir; hiçbiri yoksa None → varsayılan (2 uyku) uygulanır."""
    if isinstance(profile.get("tek_uyku"), bool):
        return profile["tek_uyku"]
    alanlar = ("ogle_yatis_dk", "tek_ogun_uyku_dk", "uyaniklik_penceresi_dk")
    if not any(profile.get(a) is not None for a in alanlar):
        return None
    return yas_bantlari.tek_uykuya_gecis_degerlendir(
        **{a: profile.get(a) for a in alanlar})["tek_uyku"]


def _bant_parametreleri(bant: dict) -> dict:
    """Çözülmüş bandı, plan prompt'unun beklediği METİN alanlarına çevir.

    KB'nin aynı adlı (ve tutarsız) alanlarının ÜSTÜNE yazılır — plan yazarına
    giden sayılar artık yalnız İlayda tablosundan gelir."""
    ww = yas_bantlari._aralik(bant["uyaniklik_penceresi_dk"])
    if bant.get("uyaniklik_penceresi_kaynak"):
        ww += f" (komşu banttan devralındı: {bant['uyaniklik_penceresi_kaynak']})"
    proto = bant["kestirme_protokolu"]
    return {
        "yas_bandi_adi": bant["ad"],
        "uyaniklik_penceresi": ww,
        "uyku_sayisi": yas_bantlari._sayi_aralik(bant["gunduz_uyku_sayisi"],
                                                 bant["gunduz_uyku_sayisi_sabit"]),
        "gunduz_uyku_total": yas_bantlari._aralik(bant["gunduz_uyku_toplam_dk"]),
        "gece_uyku": yas_bantlari._aralik(bant["gece_uykusu_dk"]),
        # KB'nin toplam_uyku_24h değeri (ör. 6 ay için "12-15 Saat") tabloyla
        # çelişiyor (İlayda: 14 saat) — tablo üstüne yazar.
        "toplam_uyku_24h": yas_bantlari._aralik(bant["toplam_gunluk_uyku_dk"]),
        "kestirme_protokolu": (
            f"Gündüz toplam uyku minimumu ({yas_bantlari._aralik(bant['gunduz_uyku_toplam_dk'])}) "
            f"tamamlanamazsa ilave {proto['sure_dk']} dakikalık kestirme uykusu "
            f"yaptırılır; {proto['sure_dk']} dakika dolunca bebek uyandırılır. Bu "
            f"kestirmeden uyandıktan {proto['gece_uykusuna_gecis_dk']} dakika "
            "(1 saat) sonra bile gece uykusuna geçilebilir."
        ),
        "yas_bandi_notlari": list(bant.get("notlar") or []),
    }


# ---------------------------------------------------------------------------
# Ana fonksiyon
# ---------------------------------------------------------------------------
def parametre_uret(profile: dict) -> dict:
    """
    37 cevap → kişisel plan parametreleri. Bu fonksiyon DETERMİNİSTİKTİR (LLM kullanmaz).
    """
    kb = load_kb()

    yas = hesapla_yas_ay(
        profile["dogum_tarihi"],
        int(profile.get("dogum_haftasi", 40)),
    )

    bucket_key = yas_bucket_sec(yas["duzeltilmis_ay"])
    bucket = dict(kb["yas_buckets"].get(bucket_key, {}))

    # --- Faz Y: yaş bandı sayıları TABLODAN gelir (KB metinlerinin üstüne yazar).
    # 12-18 ay tek/çift uyku ayrımı profilden gelen `tek_uyku` ile çözülür; profil
    # bilgisi yoksa İlayda kuralı gereği çocuk 2 uyku bandında sayılır.
    bant = yas_bantlari.yas_bandi_getir(yas["duzeltilmis_ay"],
                                        tek_uyku=_tek_uyku_mu(profile))
    bucket.update(_bant_parametreleri(bant))

    uygun, uyarilar = egitim_uygunlugu_kontrol(profile, yas)
    on_hazirlik = on_hazirlik_belirle(profile, yas)
    plan_secimi = egitim_plani_secimi(profile, yas)
    gece_beslenme = gece_beslenme_planla(profile, yas)
    # 1 aylık programda eğitim (3-4. hafta) alt yöntemin bekleme sürelerini kullanır.
    bekleme_tip = plan_secimi.get("alt_yontem_tip", plan_secimi["tip"])
    bekleme = bekleme_sureleri_planla(bekleme_tip)

    return {
        "yas": yas,
        "bucket": bucket_key,
        "parametreler": bucket,
        # Faz Y: çizelge kurucusunun ve mobilin okuduğu YAPILANDIRILMIŞ bant.
        "yas_bandi": bant,
        "kestirme_protokolu": yas_bantlari.kestirme_protokolu(),
        "uygun_mu": uygun,
        "uyarilar": uyarilar,
        "on_hazirlik": on_hazirlik,
        "plan_secimi": plan_secimi,
        "gece_beslenme": gece_beslenme,
        "bekleme_sureleri": bekleme,
        "profile_summary": profile,
        "global_rules": kb.get("global_rules", {}),
    }
