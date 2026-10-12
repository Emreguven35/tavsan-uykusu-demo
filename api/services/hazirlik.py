"""
İlayda'nın hazırlık kuralları (2026-10-12) — HAZIRLIK_KURALLARI bayrağıyla.

NEDEN VAR: mobil onboarding cevaplarını KOD olarak gönderiyor (sleep_method
"nursing", sleep_environment "loc:parent_bed;a:pacifier;d:1;w:1"); motorun
on_hazirlik_belirle'si ise Türkçe anahtar kelime arıyordu ("emerek", "aynı
yatak", emzik "evet"). Prod'daki 26 bebeğin hiçbirinde ön hazırlık
tetiklenmiyordu. Bu modül iki katmandır:

  1. Kod eşlemesi (`esle`): eski ve yeni mobil biçimini birlikte okur.
       • Ses: yeni build w:1 / w:0 ("Uyumadan önce ses kullanıyor musunuz?");
         eski veride a:white_noise ya da w:1 → ses kullanıyor.
       • Oda: yeni build d:1 (karanlık) / d:0 (karanlık değil); d yoksa
         (eski veri) cevapsız.
  2. Hazırlık adımları (`hazirlik`): sabit kimlikli, başlık + açıklama.
     HEPSİ ÖNERİDİR — hiçbiri eğitimin başlamasını geciktirmez, anneyi
     bekletmez; çizelgeye, eğitim başlangıç tarihine dokunmaz.

Kullanıldığı yerler (bayrak açıkken):
  • plan_service.generate_content: yeni üretilen planın ön hazırlık listesi
    (yaş bandı geçişiyle yeniden üretilen dahil; yenidoğan HARİÇ). Mevcut
    planlar yeniden üretilmez.
  • GET /plans/today: content.egitim_oncesi — eğitime henüz başlamamış bebek
    için her istekte hesaplanan kart; başlamışsa ve yenidoğan planında null.
Bayrak kapalıyken hiçbiri çağrılmaz: davranış bugünkünün aynısı.
"""
from __future__ import annotations

from datetime import date

from api.config import _bayrak

BAYRAK = "HAZIRLIK_KURALLARI"


def acik_mi() -> bool:
    """Her çağrıda env'den okunur (önbelleksiz): açıp kapatmak için yeniden
    başlatma yeterli, testler de bayrağı değiştirebilir."""
    return _bayrak(BAYRAK)


# ---------------------------------------------------------------------------
# 1. Kod eşlemesi
# ---------------------------------------------------------------------------
# Mobil kodu → motorun Türkçe karşılığı (eşleme raporundaki tablo).
DESTEK_KARSILIK = {"nursing": "emzirerek", "rocking": "sallayarak",
                   "holding": "kucakta", "stroller": "pusette",
                   "independent": "kendi başına"}
KONUM_KARSILIK = {"parent_bed": "ortak yatak", "own_bed": "kendi yatağı",
                  "crib": "beşik", "swing": "salıncak", "car_seat": "araba koltuğu"}


def _evet_hayir(deger: str | None) -> bool | None:
    if deger == "1":
        return True
    if deger == "0":
        return False
    return None


def ortam_coz(ham: str | None) -> dict:
    """"loc:parent_bed;a:pacifier,white_noise;d:1;w:1" → parçalar.

    d / w YOKSA None döner (eski build hiç göndermiyordu); "none" aksesuarı
    boş küme demektir."""
    out = {"loc": None, "a": set(), "d": None, "w": None}
    for parca in (ham or "").split(";"):
        anahtar, _, deger = parca.strip().partition(":")
        if anahtar == "loc":
            out["loc"] = deger or None
        elif anahtar == "a":
            out["a"] = {x.strip() for x in deger.split(",") if x.strip() and x.strip() != "none"}
        elif anahtar in ("d", "w"):
            out[anahtar] = deger.strip()
    return out


def esle(sleep_method: str | None, sleep_environment: str | None) -> dict:
    """Mobil kodlarını kuralların okuduğu tek biçime çevir.

    Dönen: {destek, destek_tr, konum, konum_tr, aksesuarlar, ses, oda_karanlik}
      ses          True (kullanıyor) | False (yeni build "hayır") | None (bilinmiyor)
      oda_karanlik True | False | None (cevapsız — eski veride d yok)"""
    o = ortam_coz(sleep_environment)
    w = _evet_hayir(o["w"])
    ses = True if (w is True or "white_noise" in o["a"]) else w
    destek = (sleep_method or "").strip() or None
    return {
        "destek": destek,
        "destek_tr": DESTEK_KARSILIK.get(destek or ""),
        "konum": o["loc"],
        "konum_tr": KONUM_KARSILIK.get(o["loc"] or ""),
        "aksesuarlar": sorted(o["a"]),
        "ses": ses,
        "oda_karanlik": _evet_hayir(o["d"]),
    }


# ---------------------------------------------------------------------------
# 2. Hazırlık adımları — metinler İlayda'nın (değiştirmeden)
# ---------------------------------------------------------------------------
EMZIK_YAS_AY = 8           # bu yaştan küçükte emzik bırakılır
PELUS_YAS_AY = 12          # bu yaştan küçükte yatakta peluş/battaniye uyarısı

ADIM_METINLERI: dict[str, tuple[str, str]] = {
    "beslenme_1_saat": (
        "Beslenme uykudan en az 1 saat önce bitsin",
        "Uykudan önceki son beslenme en geç 1 saat önce bitmeli; bu kural her "
        "durumda geçerli."),
    "emzirerek_uyutmayi_birak": (
        "Emzirerek uyutmayı bırak",
        "Eğitimden önceki 3 gün sallayarak uyut. Her gün sallamayı kısalt, "
        "beklemeyi uzat: 1 dk salla 1 dk bekle, 50 sn salla 2 dk bekle, 40 sn "
        "salla 3 dk bekle. Böyle yaparsan bebek eğitime daha kolay uyum sağlar; "
        "beklemeden de başlayabilirsin."),
    "sallamayi_birak": (
        "Sallamayı bırak",
        "Eğitimin ilk gününde ya sallama süresini her gün kısaltıp beklemeyi "
        "uzat ya da doğrudan yatakta uyutmaya geç."),
    "kucaktan_yataga": (
        "Yatakta uyumaya geç",
        "Kucakta uyutmayı bırakıp doğrudan yatakta uyut."),
    "pusetten_kucaga_yataga": (
        "Önce kucağa, sonra yatağa",
        "Önce kucakta uyumaya alıştır, sonra yatakta uyutmaya geç."),
    "salincaktan_yataga": (
        "Yatakta uyumaya geç",
        "Doğrudan yatakta başla ya da önce kucakta biraz sallayıp mayıştır, "
        "sonra yatağa yatır."),
    "ortak_yataktan_besige": (
        "Önce beşiğe geç",
        "Önce kendi yatağına ya da beşiğe geçir; gündüz uykularıyla başla, "
        "eğitime sonra başla."),
    "emzigi_birak": (
        "Emziği bırakmaya başla",
        "Ağladığında emziği takmadan önce 1 dk bekle, her gün süreyi uzat (2 dk, "
        "3 dk…). İstersen emziği tamamen kaldırarak başla; eğitim daha kısa "
        "sürer."),
    "emzik_kullanimi": (
        "Emzik kullanımı",
        "Bebek emziğini kendisi takabiliyorsa kullanabilir; düşen emziği sen "
        "takma."),
    "sesi_kademeli_kapat": (
        "Sesi kademeli kapat",
        "Uyumadan önce kullandığın sesi her uykuda bir kademe kıs, sonunda "
        "tamamen kapat."),
    "odayi_karart": (
        "Odayı karart",
        "Karartma perdesi ya da cama yapıştırılan siyah bir çöp poşeti yeterli."),
}
# Eski veride oda sorusu cevapsız: aynı adım (aynı kimlik), koşullu metinle.
ODAYI_KARART_KOSULLU = ("Oda karanlık değilse karart: karartma perdesi ya da "
                        "cama yapıştırılan siyah bir çöp poşeti yeterli.")
UYARI_METINLERI: dict[str, tuple[str, str]] = {
    "yatakta_pelus_battaniye": (
        "Yatakta peluş ve battaniye olmasın",
        "12 aydan küçük bebeklerde güvenlik için. Mutlaka uyku tulumu kullan."),
}

# --- Eğitim durumuna göre plan bölümü (2026-10-12) ---------------------------
# Plan istemi eğitimin durumunu bilir (bkz. plan_service.egitim_durumu):
#   "ilk_gun"  — eğitim bugün başlıyor: bölüm "Eğitimin ilk günü", geçiş
#                adımları BUGÜNDEN başlanacak adımlar olarak anlatılır.
#   "suruyor"  — eğitim bugünden önce başladı (yeniden üretim): bölüm
#                "Eğitim sürerken", GEÇİŞ adımları DÜŞER.
# Bekleme ve yenidoğan planlarında durum yoktur, bölüm eskisi gibi.
#
# SINIF — TEK LİSTE (İlayda'nın cevabına göre burada değişir): geçiş
# adımları. Listede olmayan her adım eğitim boyunca geçerli sayılır
# (beslenme, emzik kullanımı, ses, oda, peluş uyarısı).
GECIS_ADIMLARI = (
    "emzirerek_uyutmayi_birak",
    "sallamayi_birak",
    "kucaktan_yataga",
    "pusetten_kucaga_yataga",
    "salincaktan_yataga",
    "ortak_yataktan_besige",
    "emzigi_birak",
)
EGITIM_DURUMU_BOLUM = {"ilk_gun": "Eğitimin ilk günü", "suruyor": "Eğitim sürerken"}
# İlk gün metinleri: İlayda metninde eğitimi ERTELEYEN ifade geçen adımlar
# ("Eğitimden önceki 3 gün…", "eğitime sonra başla") için bugünden başlanan
# karşılığı. Diğer adımların metni aynen kalır. İLAYDA ONAYI BEKLİYOR.
ILK_GUN_METINLERI = {
    "emzirerek_uyutmayi_birak": (
        "Bugünden itibaren emzirerek değil sallayarak uyut. Her gün sallamayı "
        "kısalt, beklemeyi uzat: 1 dk salla 1 dk bekle, 50 sn salla 2 dk bekle, "
        "40 sn salla 3 dk bekle."),
    "ortak_yataktan_besige": (
        "Bugünden itibaren kendi yatağında ya da beşiğinde uyut; geçişe gündüz "
        "uykularıyla başla."),
}


_DESTEK_ADIMI = {"nursing": "emzirerek_uyutmayi_birak", "rocking": "sallamayi_birak",
                 "holding": "kucaktan_yataga", "stroller": "pusetten_kucaga_yataga"}
_KONUM_ADIMI = {"swing": "salincaktan_yataga", "car_seat": "salincaktan_yataga",
                "parent_bed": "ortak_yataktan_besige"}


def _kalem(kimlik: str, metinler: dict, aciklama: str | None = None) -> dict:
    baslik, metin = metinler[kimlik]
    return {"kimlik": kimlik, "baslik": baslik, "aciklama": aciklama or metin}


def hazirlik(eslenmis: dict, yas_ay: float | None) -> dict:
    """Eşlenmiş profil + düzeltilmiş yaş → {adimlar, uyarilar}.

    Yaşa bağlı iki kural (emzik, peluş) yaş bilinmiyorsa uygulanmaz."""
    adimlar = [_kalem("beslenme_1_saat", ADIM_METINLERI)]
    if eslenmis.get("destek") in _DESTEK_ADIMI:
        adimlar.append(_kalem(_DESTEK_ADIMI[eslenmis["destek"]], ADIM_METINLERI))
    if eslenmis.get("konum") in _KONUM_ADIMI:
        adimlar.append(_kalem(_KONUM_ADIMI[eslenmis["konum"]], ADIM_METINLERI))
    aks = set(eslenmis.get("aksesuarlar") or [])
    if "pacifier" in aks and yas_ay is not None:
        adimlar.append(_kalem("emzigi_birak" if yas_ay < EMZIK_YAS_AY
                              else "emzik_kullanimi", ADIM_METINLERI))
    if eslenmis.get("ses") is True:
        adimlar.append(_kalem("sesi_kademeli_kapat", ADIM_METINLERI))
    if eslenmis.get("oda_karanlik") is False:
        adimlar.append(_kalem("odayi_karart", ADIM_METINLERI))
    elif eslenmis.get("oda_karanlik") is None:
        adimlar.append(_kalem("odayi_karart", ADIM_METINLERI, ODAYI_KARART_KOSULLU))

    uyarilar = []
    if aks & {"stuffed_animal", "blanket"} and yas_ay is not None and yas_ay < PELUS_YAS_AY:
        uyarilar.append(_kalem("yatakta_pelus_battaniye", UYARI_METINLERI))
    return {"adimlar": adimlar, "uyarilar": uyarilar}


def bebek_hazirligi(baby, yas_ay: float | None) -> dict:
    return hazirlik(esle(baby.sleep_method, baby.sleep_environment), yas_ay)


# ---------------------------------------------------------------------------
# Kullanım yerleri
# ---------------------------------------------------------------------------
def on_hazirlik_listesi(baby, yas_ay: float | None,
                        egitim_durumu: str | None = None) -> list[dict]:
    """Plan üretiminin ön hazırlık listesi (istem + yedek motor biçimi:
    konu / sure / aksiyon). Kimlik de taşınır.

    egitim_durumu "suruyor": geçiş adımları düşer; "ilk_gun": geçiş adımları
    bugünden başlanacak metinleriyle (ILK_GUN_METINLERI) gelir."""
    h = bebek_hazirligi(baby, yas_ay)
    adimlar = h["adimlar"]
    if egitim_durumu == "suruyor":
        adimlar = [a for a in adimlar if a["kimlik"] not in GECIS_ADIMLARI]
    elif egitim_durumu == "ilk_gun":
        adimlar = [dict(a, aciklama=ILK_GUN_METINLERI.get(a["kimlik"], a["aciklama"]))
                   for a in adimlar]
    sure = {"ilk_gun": "Bugünden", "suruyor": "Eğitim boyunca"}.get(egitim_durumu, "Öneri")
    return ([{"kimlik": a["kimlik"], "konu": a["baslik"], "sure": sure,
              "aksiyon": a["aciklama"]} for a in adimlar]
            + [{"kimlik": u["kimlik"], "konu": u["baslik"], "sure": "Güvenlik uyarısı",
                "aksiyon": u["aciklama"]} for u in h["uyarilar"]])


def egitim_oncesi_karti(baby, content: dict | None, bugun: date) -> dict | None:
    """"Eğitime başlamadan önce" kartı — her istekte hesaplanır, DB'ye yazmaz.

    null: yenidoğan planı, eğitimi başlamış bebek (training_started_at ≤ bugün),
    doğum tarihi olmayan bebek. Başlangıç tarihi: ileri tarihli
    training_started_at, yoksa bekleme planının tahmini tarihi (taze), yoksa
    null. training_started_at'e DOKUNMAZ (egitim_baslangicini_tamamla
    çağrılmaz)."""
    from api.services import plan_service
    from engine.parameter_engine import hesapla_yas_ay

    if baby is None or baby.birth_date is None:
        return None
    if plan_service.tip_turet(content or {}) == plan_service.TYPE_YENIDOGAN:
        return None
    if baby.training_started_at is not None and baby.training_started_at <= bugun:
        return None
    hafta = plan_service.etkin_dogum_haftasi(baby, (content or {}).get("dogum_haftasi"))
    if baby.training_started_at is not None:
        baslangic = baby.training_started_at.isoformat()
    elif (content or {}).get("egitim_baslangic"):
        baslangic = plan_service.egitim_baslangic_hesapla(baby, bugun, hafta).get(
            "tahmini_tarih")
    else:
        baslangic = None
    yas_ay = hesapla_yas_ay(baby.birth_date.isoformat(), hafta, bugun=bugun)["duzeltilmis_ay"]
    return {"egitim_baslangic": baslangic, **bebek_hazirligi(baby, yas_ay)}
