"""
Paylaşım kartları — sunucu hesabı (2026-11-02).

Kartlardaki her sayı burada, TEK yerde hesaplanır; mobil yalnız gösterir.
Gün temizliği plan motorunun kendisidir (`plan_adapter.gun_kayitlari`) — timeline
ve weekly-summary ile aynı çağrı: gece uykusu ve gece uyanması BİTTİĞİ güne
(gecenin sabahına) bağlıdır, gün Europe/Istanbul takvim günüdür.

TANIMLAR
  Güvenilir oturum   bitmiş, süresi bilinen ve şüpheli olmayan uyku. Hariç:
                     açık (devam), sunucunun kapattığı (bitiş bilinmiyor,
                     k16_1/bakim), 16 sa üstü (asiri_uzun) ve ŞÜPHELİ kayıt.
  Şüpheli            12 saati (SUPHELI_SURE_DK) aşan uyku ya da 12:00'den
                     (SUPHELI_GECE_BITIS_DK) sonra biten gece uykusu. Büyük
                     olasılıkla sabah durdurulmamış sayaç.
  Tam gün            güvenilir gece_dk > 0 VE güvenilir gunduz_dk > 0.
  Tam gece           güvenilir gece_dk > 0.
  Kayıtsız alan      null — 0 DEĞİL ("kaydedilmedi" ile "uyumadı" karışmasın).

Ortalamalar YALNIZ tam günlerden; en uzun uyku penceredeki tüm güvenilir
oturumlardan. Gece uyanması, bebeğin `night_wake` kaydı kullanılmıyorsa
(UYANMA_KULLANIM_GUN içinde hiç yoksa) null: o durumda 0 "uyanmadı" değil
"girilmedi" demektir.
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any, Iterable

from api.services import plan_adapter

logger = logging.getLogger("tavsan.paylasim")

HAFTA_GUN = 7
MIN_TAM_GUN = 4                    # paylaşılabilir haftalık kart için
SUPHELI_SURE_DK = 12 * 60
SUPHELI_GECE_BITIS_DK = 12 * 60
UYANMA_KULLANIM_GUN = 14           # bu kadar günde hiç night_wake yoksa uyanma null

# vurgu_metni eşikleri
VURGU_UYANMA_ONCEKI_MIN = 1.0      # önceki hafta en az 1 uyanma/gece
VURGU_UYANMA_ORAN = 0.25           # en az %25 azalma
VURGU_UYANMA_MUTLAK = 0.5          # ve en az 0,5 uyanma/gece
VURGU_SIFIR_TAM_GECE = 5           # 0'a iniş için bu hafta en az 5 tam gece
VURGU_EN_UZUN_DK = 30
VURGU_EN_UZUN_ORAN = 0.10
VURGU_ORT_DK = 30
# Aynı göreli iyileşmede öncelik (küçük önce).
_ONCELIK = {"gece_uyanma": 0, "en_uzun": 1, "ort_uyku": 2}

# Mezuniyet
MEZUN_MIN_TAM_GECE = 2

YETERSIZ_MESAJ = "Henüz yeterli veri yok, {n} gün daha kaydet"


# --- Gün özeti -----------------------------------------------------------------
def _oturum_sebebi(k: dict, gece: bool) -> str | None:
    """Oturum neden sayılmaz; sayılırsa None."""
    if k.get("_devam") or k.get("bit_dk_lin") is None:
        return "acik"
    if k.get("_bitis_bilinmiyor"):
        return "otomatik_kapandi"
    if k.get("_asiri_uzun") or (k.get("sure_dk") or 0) > SUPHELI_SURE_DK:
        return "supheli_uzun"
    if gece and k.get("bit_dk") is not None and k["bit_dk"] > SUPHELI_GECE_BITIS_DK:
        return "supheli_gec_bitis"
    if not k.get("sure_dk"):
        return "sure_yok"                       # süresiz kapalı kayıt — sayılmaz
    return None


def gun_ozeti(rows: Iterable[Any], gun: date, bant: dict | None = None) -> dict:
    """Bir Türkiye gününün kart özeti (plan motorunun temizlediği kayıtlardan)."""
    k = plan_adapter.gun_kayitlari(rows, gun, plan_adapter.DEFAULT_WAKE_MIN,
                                   tz_offset_min=plan_adapter.TZ_OFFSET_MIN, bant=bant)
    gece_dk = gunduz_dk = 0
    gece_n = gunduz_n = 0
    sureler: list[int] = []
    haric: list[dict] = []
    for liste, gece in ((k["gece_uykulari"], True), (k["gunduz_uykulari"], False)):
        for o in liste:
            sebep = _oturum_sebebi(o, gece)
            if sebep is not None:
                haric.append({
                    "tarih": gun.isoformat(),
                    "baslangic": _tr_an(o["bas_gun"], o["bas_dk"]).isoformat(),
                    "bitis": (None if o.get("bit_dk_lin") is None
                              else _tr_an(o["bas_gun"], o["bit_dk_lin"]).isoformat()),
                    "sure_dk": o.get("sure_dk") if o.get("bit_dk_lin") is not None else None,
                    "sinif": "gece" if gece else "gunduz",
                    "sebep": sebep,
                })
                continue
            sure = int(o["sure_dk"])
            sureler.append(sure)
            if gece:
                gece_dk += sure
                gece_n += 1
            else:
                gunduz_dk += sure
                gunduz_n += 1
    uyanma_kayitli = sum(1 for w in k["gece_uyanmalari"] if w.get("type") == "night_wake")
    g = gece_dk if gece_n else None
    d = gunduz_dk if gunduz_n else None
    return {
        "tarih": gun,
        "gece_dk": g,
        "gunduz_dk": d,
        "toplam_dk": None if g is None and d is None else (g or 0) + (d or 0),
        "tam": bool(g) and bool(d),
        "tam_gece": bool(g),
        "gunduz_adet": gunduz_n,
        "uyanma": len(k["gece_uyanmalari"]),
        "night_wake_sayisi": uyanma_kayitli,
        "en_uzun_dk": max(sureler) if sureler else None,
        "haric": haric,
    }


def _tr_an(gun: date, dakika: int):
    from api.zaman import tr_gun_araligi
    return tr_gun_araligi(gun)[0] + timedelta(minutes=int(dakika))


def _ort(degerler: list[float], basamak: int | None) -> float | int | None:
    if not degerler:
        return None
    v = sum(degerler) / len(degerler)
    return int(round(v)) if basamak is None else round(v, basamak)


def pencere_ozeti(gunler: list[dict], uyanma_kullaniliyor: bool) -> dict:
    """Gün özetlerinden pencere metrikleri (haftalık kart / mezuniyet)."""
    tam = [g for g in gunler if g["tam"]]
    en_uzun = [g["en_uzun_dk"] for g in gunler if g["en_uzun_dk"] is not None]
    return {
        "kayitli_gun_sayisi": sum(1 for g in gunler if g["toplam_dk"] is not None),
        "tam_gun_sayisi": len(tam),
        "tam_gece_sayisi": sum(1 for g in gunler if g["tam_gece"]),
        "ort_uyku_dk_gun": _ort([g["toplam_dk"] for g in tam], None),
        "gece_uyanma": (_ort([g["uyanma"] for g in tam], 1)
                        if uyanma_kullaniliyor else None),
        "en_uzun_uyku_dk": max(en_uzun) if en_uzun else None,
        "gunduz_uyku_ort_adet": _ort([g["gunduz_adet"] for g in tam], 1),
    }


# --- Vurgu ----------------------------------------------------------------------
def vurgu_metni(simdi: dict, onceki: dict | None) -> str | None:
    """Geçen haftaya göre EN İYİ gerçek gelişme; yoksa None.

    Şart: iki hafta da minimum veriyi geçmiş (`onceki` None değil, bu hafta
    paylaşılabilir) ve ilgili alan iki tarafta da dolu. Adaylar:
      gece_uyanma  önceki ≥ 1,0/gece; azalma ≥ %25 VE ≥ 0,5/gece. 0'a iniş
                   yalnız bu hafta ≥ 5 tam gece varsa.
      en_uzun      artış ≥ 30 dk VE ≥ %10
      ort_uyku     artış ≥ 30 dk/gün
    Birden çok aday → göreli iyileşmesi en büyük olan; eşitlikte öncelik
    gece_uyanma > en_uzun > ort_uyku. Yüzde AŞAĞI yuvarlanır. Hesap kartta
    görünen (yuvarlanmış) değerlerle yapılır — kart kendi içinde tutarlı kalsın."""
    if onceki is None or simdi.get("tam_gun_sayisi", 0) < MIN_TAM_GUN:
        return None
    adaylar: list[tuple[float, int, str]] = []

    s, o = simdi.get("gece_uyanma"), onceki.get("gece_uyanma")
    if s is not None and o is not None and o >= VURGU_UYANMA_ONCEKI_MIN:
        fark = o - s
        oran = fark / o
        sifir_ok = s > 0 or simdi.get("tam_gece_sayisi", 0) >= VURGU_SIFIR_TAM_GECE
        if oran >= VURGU_UYANMA_ORAN and fark >= VURGU_UYANMA_MUTLAK - 1e-9 and sifir_ok:
            adaylar.append((oran, _ONCELIK["gece_uyanma"],
                            f"Gece uyanmaları %{math.floor(oran * 100 + 1e-9)} azaldı"))

    s, o = simdi.get("en_uzun_uyku_dk"), onceki.get("en_uzun_uyku_dk")
    if s is not None and o:
        fark = s - o
        oran = fark / o
        if fark >= VURGU_EN_UZUN_DK and oran >= VURGU_EN_UZUN_ORAN:
            adaylar.append((oran, _ONCELIK["en_uzun"],
                            f"En uzun uyku %{math.floor(oran * 100 + 1e-9)} uzadı"))

    s, o = simdi.get("ort_uyku_dk_gun"), onceki.get("ort_uyku_dk_gun")
    if s is not None and o:
        fark = s - o
        if fark >= VURGU_ORT_DK:
            oran = fark / o
            adaylar.append((oran, _ONCELIK["ort_uyku"],
                            f"Günlük ortalama uyku %{math.floor(oran * 100 + 1e-9)} arttı"))

    if not adaylar:
        return None
    adaylar.sort(key=lambda a: (-a[0], a[1]))
    return adaylar[0][2]


# --- Haftalık kart ----------------------------------------------------------------
def _uyanma_kullaniliyor(gunler_genis: list[dict]) -> bool:
    return any(g["night_wake_sayisi"] > 0 for g in gunler_genis)


def haftalik_hesapla(rows: list, bitis: date, bant: dict | None = None) -> dict:
    """Haftalık kartın veri gövdesi (bebek_adi / egitim_gunu HARİÇ — router ekler).

    `rows`: bitis−(2×7+UYANMA_KULLANIM_GUN) gün öncesinden bitis sonuna kadar
    olan ham kayıtlar (bir gün öncesinden başlamalı: gece önceki akşam başlar)."""
    bas = bitis - timedelta(days=HAFTA_GUN - 1)
    onceki_bitis = bas - timedelta(days=1)
    onceki_bas = onceki_bitis - timedelta(days=HAFTA_GUN - 1)
    en_eski = min(onceki_bas, onceki_bitis - timedelta(days=UYANMA_KULLANIM_GUN - 1))
    ozet = {en_eski + timedelta(days=i): None
            for i in range((bitis - en_eski).days + 1)}
    for g in ozet:
        ozet[g] = gun_ozeti(rows, g, bant)

    def _aralik(a: date, b: date) -> list[dict]:
        return [ozet[a + timedelta(days=i)] for i in range((b - a).days + 1)]

    def _kullanim(son: date) -> bool:
        return _uyanma_kullaniliyor(_aralik(son - timedelta(days=UYANMA_KULLANIM_GUN - 1), son))

    hafta = _aralik(bas, bitis)
    simdi = pencere_ozeti(hafta, _kullanim(bitis))
    onceki_p = pencere_ozeti(_aralik(onceki_bas, onceki_bitis), _kullanim(onceki_bitis))
    onceki = None
    if onceki_p["tam_gun_sayisi"] >= MIN_TAM_GUN:
        onceki = {k: onceki_p[k] for k in ("ort_uyku_dk_gun", "gece_uyanma", "en_uzun_uyku_dk")}

    tam = simdi["tam_gun_sayisi"]
    paylasilabilir = tam >= MIN_TAM_GUN
    return {
        "paylasilabilir": paylasilabilir,
        "yetersiz_veri_mesaji": None if paylasilabilir else YETERSIZ_MESAJ.format(n=MIN_TAM_GUN - tam),
        "hafta_baslangic": bas,
        "hafta_bitis": bitis,
        "kayitli_gun_sayisi": simdi["kayitli_gun_sayisi"],
        "tam_gun_sayisi": tam,
        "ort_uyku_dk_gun": simdi["ort_uyku_dk_gun"],
        "gunluk": [{"tarih": g["tarih"], "toplam_dk": g["toplam_dk"], "gece_dk": g["gece_dk"],
                    "gunduz_dk": g["gunduz_dk"], "tam": g["tam"]} for g in hafta],
        "gece_uyanma": simdi["gece_uyanma"],
        "en_uzun_uyku_dk": simdi["en_uzun_uyku_dk"],
        "gunduz_uyku_ort_adet": simdi["gunduz_uyku_ort_adet"],
        "onceki_hafta": onceki,
        "vurgu_metni": vurgu_metni(simdi, onceki) if paylasilabilir else None,
        "haric_tutulan": [h for g in hafta for h in g["haric"]],
    }


def haftalik_sorgu_baslangici(bitis: date) -> date:
    """haftalik_hesapla'nın ihtiyaç duyduğu en eski gün (bir gün payı dahil)."""
    bas = bitis - timedelta(days=HAFTA_GUN - 1)
    onceki_bitis = bas - timedelta(days=1)
    onceki_bas = onceki_bitis - timedelta(days=HAFTA_GUN - 1)
    en_eski = min(onceki_bas, onceki_bitis - timedelta(days=UYANMA_KULLANIM_GUN - 1))
    return en_eski - timedelta(days=1)


# --- Mezuniyet kartı ----------------------------------------------------------------
def _mezun_pencere(gunler: list[dict], uyanma_kullaniliyor: bool) -> dict | None:
    """Gece-merkezli pencere: ≥ MEZUN_MIN_TAM_GECE tam gece yoksa None.

    Metrikler tam gecelerden: uyanma ortalaması ve en uzun güvenilir uyku."""
    geceler = [g for g in gunler if g["tam_gece"]]
    if len(geceler) < MEZUN_MIN_TAM_GECE:
        return None
    en_uzun = [g["en_uzun_dk"] for g in geceler if g["en_uzun_dk"] is not None]
    return {
        "gece_uyanma": _ort([g["uyanma"] for g in geceler], 1) if uyanma_kullaniliyor else None,
        "en_uzun_uyku_dk": max(en_uzun) if en_uzun else None,
    }


def mezuniyet_hesapla(rows: list, egitim_baslangic: date, mezun_gun: date,
                      beyan_gece_uyanma: int | None, bant: dict | None = None) -> dict:
    """once / simdi karşılaştırması.

    Gün = gecenin SABAHI (motorun kuralı): X akşamı başlayan gece X+1'e bağlı.
      egitim_oncesi   eğitim başlangıcından önceki 3 gece → bas−2, bas−1, bas
      egitim_1_2_gun  eğitimin 1. ve 2. gecesi           → bas+1, bas+2
      son_3_gun       mezuniyetten önceki son 3 gece      → mezun−2 … mezun
    once: egitim_oncesi yeterliyse (≥ 2 tam gece) o, değilse egitim_1_2_gun.
    gece_uyanma için son çare onboarding beyanı (kaynak "beyan").
    Her metrik: iki pencerede de ≥ 2 tam gece yoksa null (beyan bu şartın
    yerine geçer, çünkü beyanda gece sayısı yoktur)."""
    def _gunler(a: date, b: date) -> list[dict]:
        return [gun_ozeti(rows, a + timedelta(days=i), bant) for i in range((b - a).days + 1)]

    oncesi = _gunler(egitim_baslangic - timedelta(days=2), egitim_baslangic)
    ilk2 = _gunler(egitim_baslangic + timedelta(days=1), egitim_baslangic + timedelta(days=2))
    son3 = _gunler(mezun_gun - timedelta(days=2), mezun_gun)
    kullanim = _uyanma_kullaniliyor(oncesi + ilk2 + son3)

    simdi_p = _mezun_pencere(son3, kullanim)
    once_p, once_kaynak = _mezun_pencere(oncesi, kullanim), "egitim_oncesi"
    if once_p is None:
        once_p, once_kaynak = _mezun_pencere(ilk2, kullanim), "egitim_1_2_gun"
    if once_p is None:
        once_kaynak = None

    simdi = {"kaynak": "son_3_gun" if simdi_p else None,
             "gece_uyanma": simdi_p["gece_uyanma"] if simdi_p else None,
             "en_uzun_uyku_dk": simdi_p["en_uzun_uyku_dk"] if simdi_p else None}

    once_en_uzun = once_p["en_uzun_uyku_dk"] if once_p else None
    once_uyanma = once_p["gece_uyanma"] if once_p else None
    uyanma_kaynak = once_kaynak if once_uyanma is not None else None
    if once_uyanma is None and beyan_gece_uyanma is not None:
        once_uyanma, uyanma_kaynak = float(beyan_gece_uyanma), "beyan"

    # Karşılaştırma ancak iki taraf da doluysa anlamlı.
    if simdi["en_uzun_uyku_dk"] is None or once_en_uzun is None:
        simdi["en_uzun_uyku_dk"] = once_en_uzun = None
    if simdi["gece_uyanma"] is None or once_uyanma is None:
        simdi["gece_uyanma"] = once_uyanma = None
        uyanma_kaynak = None

    if once_en_uzun is None and uyanma_kaynak is None:
        kaynak = None
    elif once_en_uzun is None:
        kaynak = uyanma_kaynak
    else:
        kaynak = once_kaynak
    if simdi["en_uzun_uyku_dk"] is None and simdi["gece_uyanma"] is None:
        simdi["kaynak"] = None
    once = {"kaynak": kaynak, "gece_uyanma": once_uyanma, "en_uzun_uyku_dk": once_en_uzun,
            "gece_uyanma_kaynak": uyanma_kaynak}
    return {"once": once, "simdi": simdi}


def mezuniyet_sorgu_baslangici(egitim_baslangic: date) -> date:
    return egitim_baslangic - timedelta(days=3)
