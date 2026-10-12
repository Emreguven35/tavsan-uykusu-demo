"""
Sor — "bugünün kuralları" bloğu ve cevaptaki sayıların ROL bazlı doğrulaması.

İki bayrak (kapalıyken hiçbir şey çağrılmaz, davranış bugünkünün aynısı):
    SOR_KURAL_BLOGU     — bağlama blok + sistem kuralı ("süre ve sayı yalnız
                          bu bloktan alınır")
    SOR_SAYI_DOGRULAMA  — cevap anneye gitmeden önce doğrulama; ihlalde bir kez
                          yeniden üretim (ihlal modele söylenir), yine ihlalde
                          ihlalli cümle bloktaki karşılığıyla değiştirilir,
                          karşılığı yoksa çıkarılır.

NEDEN (2026-10-12 incelemesi): model başka plan tiplerinin tablolarını ("dış 10
+ iç 35"), yatır-çık dizisini 13. günden önce ve uydurma artışları ("6-7 dk,
8-9 dk") kullanıyordu. Sayılar artık YALNIZ yöntem tablosundan (engine.yontem)
gelir; blok ve izin listesi AYNI veriden kurulur.

İzinli olanlar: bloktaki değer-rol çiftleri; çizelge saatleri, "şu an" ve
bunlardan türeyen dakikalar; bebeğin yaş bandının uyanıklık/uyku süreleri.
Yasak: 13 günlük planda bugüne uymayan "dış X + iç Y"; yatır-çık dizisinin
13. günden önce kullanılması (o roller bugün için boş küme).

Rol çıkarımı engine.sayi_rol (denetim betiğiyle ortak, sezgisel).
"""
from __future__ import annotations

import re
from typing import Any

from api.config import _bayrak
from engine import sayi_rol, yontem

BLOK_BAYRAK = "SOR_KURAL_BLOGU"
DOGRULAMA_BAYRAK = "SOR_SAYI_DOGRULAMA"

SISTEM_KURALI = (
    "EK KURAL (süre ve sayı): Cevaptaki HER süre ve sayı (dakika, saniye, tekrar, "
    "bekleme) YALNIZ BEBEK VERİSİ'ndeki 'BUGÜNÜN KURALLARI' bloğundan alınır. Blokta "
    "karşılığı olmayan bir süre için sayı uydurma; 'bu süre için planınıza bakın' de. "
    "Saat olarak yalnız plan saatlerini ve 'Şu an'ı kullan. Başka plan tiplerinin "
    "tablolarını (ör. 'dış X + iç Y') ve yatır-çık dizisini 13. günden önce kullanma.")

_TOL = 1e-6
_MOTIVASYON_SAYILI = re.compile(r"ağlayan (?:bir )?(?:çocuk|bebek)|hafta sonra", re.IGNORECASE)
_SAAT = re.compile(r"(?<![\d:.])(\d{1,2})[:.](\d{2})(?![\d])(?!\s*(?:dakika|dk|sn|saniye))")
_CUMLE_SONU = re.compile(r"(?<=[.!?])\s+(?=\S)")
# "asla 5 dk altına inme", "en az 5 dk": gece bekleme TABANI. Taban, ilk bekleme
# süresi değildir (o alan İlayda kararı bekliyor) — taban değeri gece yalnız bu
# kalıpla geçer.
def _taban_ifadesi(cumle: str, taban: float) -> bool:
    t = _sayi(taban).replace(",", "[,.]")
    return bool(re.search(rf"en az\s+{t}\s*(?:dk|dakika)|(?<![\d,.]){t}\s*(?:dk|dakika)\w*\s+"
                          rf"(?:altın|aşağı)", cumle, re.IGNORECASE))
_BEKLE = re.compile(r"bekle", re.IGNORECASE)
_GUNDUZ = re.compile(r"gündüz", re.IGNORECASE)
_GECE = re.compile(r"gece", re.IGNORECASE)


def blok_acik_mi() -> bool:
    return _bayrak(BLOK_BAYRAK)


def dogrulama_acik_mi() -> bool:
    return _bayrak(DOGRULAMA_BAYRAK)


def _ss(dk: int) -> str:
    dk = int(dk) % (24 * 60)
    return f"{dk // 60:02d}:{dk % 60:02d}"


def _sayi(v: float) -> str:
    return (f"{v:g}").replace(".", ",")


# =============================================================================
# Kurallar (blok + izin listesi aynı veriden)
# =============================================================================
def kurallar(d: dict, baby: Any) -> dict | None:
    """`d`: sor_baglam.gun_durumu() çıktısı. Eğitim sürmüyorsa (yenidoğan,
    başlamadı, tamamlandı) None — blok ve doğrulama uygulanmaz."""
    if d.get("durum") != "egitim":
        return None
    # Gece kuralları GECESİ SAYILAN günden (02:30 → önceki gün), gündüz kuralları
    # (kısa gündüz uykusu) uygulamanın gösterdiği günden: 02:30'da sabahki
    # gündüz uykuları YENİ günündür.
    g = d["etkin_gun"] if d["sabah_once"] else d["app_gun"]
    g = max(1, int(g or 1))
    g_gunduz = max(1, int(d["app_gun"] or 1))
    gece = bool(d["gece"] or d["sabah_once"])
    asamalar = yontem.deger("merdiven.asamalar")
    asama = next((a for a in asamalar if a["gun"][0] <= g <= a["gun"][1]), asamalar[-1])

    akis = yontem.deger("gece_uyanma.akis")
    deneme = yontem.deger("seans.deneme_dk")
    mola, mola_max = yontem.deger("seans.mola_dk"), yontem.deger("seans.mola_en_fazla_dk")
    tekrar = yontem.deger("seans.tekrar_en_fazla")
    sonrasi = yontem.deger("seans.uc_tekrar_sonrasi")
    taban = yontem.deger("gece_uyanma.taban_dk")
    artis_std = yontem.deger("gece_uyanma.gunluk_artis_standart_dk")
    artis_min = yontem.deger("gece_uyanma.gunluk_artis_en_az_dk")
    kucak = [v / 60 for v in yontem.deger("kucak_araligi_sn.dizi")]
    rutin = yontem.deger("seans.rutin_suresi_dk")
    toplam_kg = yontem.deger("kisa_gunduz.seans_toplam_dk")
    geri_dalis = yontem.deger("kisa_gunduz.geri_dalis_esigi_dk")
    g13 = yontem.deger("kisa_gunduz.gun_1_3_dis_ic_dk")
    yc_kg = yontem.deger("kisa_gunduz.yatir_cik_ve_sonrasi_dis_ic_dk")
    yc_dizi = yontem.deger("yatir_cik.standart_dizi_dk")
    yc_max = yontem.deger("yatir_cik.en_fazla_dk")
    yc_21 = yontem.deger("yatir_cik.yirmi_bir_gun_kurali")

    # Yaş bandı (yas_bantlari — tabloya kopyalanmaz)
    bant = None
    try:
        if baby is not None and baby.birth_date is not None:
            from engine import yas_bantlari
            from engine.parameter_engine import hesapla_yas_ay
            ay = hesapla_yas_ay(baby.birth_date.isoformat(), bugun=d["gun"])["duzeltilmis_ay"]
            bant = yas_bantlari.yas_bandi_getir(float(ay))
    except Exception:
        bant = None
    kisa_esik = (bant or {}).get("kisa_uyku_esigi_dk")

    # --- Kısa gündüz dış/iç: gün 1-3 İlayda; 13+ yatır-çık; 4-12 bekliyor -----
    if g_gunduz <= 3:
        kg = g13[f"gun_{g_gunduz}"]
        kg_dis, kg_ic = {kg["dis"]}, {kg["ic"]}
        kg_metin = (f"dışarıda {kg['dis']} dk bekle, sonra içeride {kg['ic']} dk dene "
                    f"(toplam {toplam_kg} dk)")
        kg_karsilik = (f"Kısa gündüz uykusunda bugün dışarıda {kg['dis']} dakika bekleyip "
                       f"içeride {kg['ic']} dakika deneyin (toplam {toplam_kg} dakika).")
    elif g_gunduz >= 13:
        kg_dis, kg_ic = {yc_kg["dis"]}, {yc_kg["ic"]}
        kg_metin = (f"dışarıda {yc_kg['dis']} dk bekle, içeri girip 'uyku bitmedi' deyip çık, "
                    f"{yc_kg['ic']} dk daha dışarıda bekle; uyumazsa al")
        kg_karsilik = (f"Kısa gündüz uykusunda dışarıda {yc_kg['dis']} dakika bekleyin, içeri "
                       f"girip 'uyku bitmedi' deyip çıkın, {yc_kg['ic']} dakika daha bekleyin.")
    else:
        kg_dis, kg_ic = set(), set()                         # İlayda kararı bekliyor
        kg_metin = "bekleme süreleri için planına bak"
        kg_karsilik = "Kısa gündüz uykusundaki bekleme süreleri için planınıza bakın."

    yatir_cik_var = max(g, g_gunduz) >= 13
    yc_izinli = (set(yc_dizi) | set(yc_max) | {yc_21["en_fazla_dk"]}) if yatir_cik_var else set()

    # --- Gece içeride yöntem (aşamaya göre; kapı aşamaları bekliyor) ----------
    temas = yontem.deger("gece_uyanma.icerde_temas")
    icerde = temas.get(asama["kod"], "bu aşamanın yöntemiyle (ayrıntı için planına bak)")
    icerde_sayili = asama["kod"] == "yatir_cik"                 # "on saniye – bir dakika"

    satirlar = [
        f"BUGÜNÜN KURALLARI (süre ve sayıları YALNIZ buradan al; burada olmayan süre için "
        f"'planınıza bakın' de):",
        f"- Aşama: {asama['ad']} (eğitimin {g}. günü{', gece' if gece else ''}).",
        f"- Uykuya dalmazsa: {deneme} dk dene → uyumazsa {mola} dk rutin molası (çok dirençli "
        f"bebekte {mola_max} dk) → yeniden {deneme} dk; en fazla {tekrar} kez. {tekrar} kez "
        f"sonra gece: {sonrasi['gece']}; gündüz: {sonrasi['gunduz']}.",
        f"- Gece uyanırsa: önce dışarıda bekle (ilk bekleme süresi ve aynı gece içindeki "
        f"artış için planına bak); sonra içeride {icerde}, {akis['3_deneme_dk']} dk; uyumazsa "
        f"odada {akis['4_rutin_dk']} dk rutin (beslenme yok); en fazla "
        f"{akis['6_tekrar_en_fazla']} kez. Bekleme günden güne standart +{artis_std} dk, "
        f"çok dirençli bebekte en az +{_sayi(artis_min * 60)} sn artar; önceki günle aynı ya "
        f"da daha az olamaz; asla {taban} dk altına inmez.",
        f"- Kısa gündüz uykusu ({g_gunduz}. günün gündüz uykuları; uyku"
        f"{f' {kisa_esik} dk' if kisa_esik else ''} altında kalırsa): {kg_metin}. Bebek {geri_dalis} dk ve üzeri geri uyursa uzatma biter.",
        f"- Kucağa alma aralıkları: {' → '.join(_sayi(v) for v in kucak)} dk.",
        f"- Rutin süresi: {rutin['en_az']}–{rutin['en_fazla']} dk.",
        (f"- Yatır-çık: dışarıda {' → '.join(str(v) for v in yc_dizi)} dk, en fazla "
         f"{'–'.join(str(v) for v in yc_max)} dk; {yc_21['gun']} gün hiç dalmazsa en fazla "
         f"{yc_21['en_fazla_dk']} dk ya da doktor."
         if yatir_cik_var else
         f"- Yatır-çık 13. günde başlar; bugün yatır-çık bekleme dizisini kullanma."),
        "- Saatler: yalnız BEBEK VERİSİ'ndeki plan saatleri ve 'Şu an'.",
    ]

    # --- İzin listesi (aynı değerler) ----------------------------------------
    izinli: dict[str, Any] = {
        "seans": {deneme, toplam_kg},
        "mola": {mola, mola_max},
        "tekrar": {tekrar},
        "kucak": set(kucak),
        "rutin": ("aralik", rutin["en_az"], rutin["en_fazla"]),
        "artis": ("aralik", artis_min, artis_std),
        "gece_uyanma": {taban, akis["3_deneme_dk"], akis["4_rutin_dk"]},
        "dis": kg_dis,
        "ic": kg_ic | ({1} if icerde_sayili else set()),
        "yatir_cik": yc_izinli,
        "kestirme": {30, 60},
    }

    # --- Saatler ve türeyen dakikalar ----------------------------------------
    from api.services import plan_adapter
    sched = plan_adapter.normalize_schedule((d.get("content") or {}).get("schedule"))
    saatler: set[str] = {_ss(d["dk"]), "06:00"}
    bloklar: list[int] = []
    for b in sched:
        for alan in ("time", "end"):
            if b.get(alan):
                saatler.add(str(b[alan]).replace(".", ":").zfill(5))
        if b.get("start_minute") is not None:
            bloklar.append(int(b["start_minute"]))
            if b.get("end_minute") is not None:
                bloklar.append(int(b["end_minute"]))
    kest = (bant or {}).get("kestirme_protokolu") or {}
    for s in kest.get("saat_penceresi") or []:
        saatler.add(str(s))
    turev: set[float] = set()
    for m in bloklar:
        fark = abs(m - d["dk"])
        if fark <= 6 * 60:
            turev.add(float(fark))
    for b in sched:
        if b.get("start_minute") is not None and b.get("end_minute") is not None:
            turev.add(float(int(b["end_minute"]) - int(b["start_minute"])))
    if d.get("uyanis") is not None:
        turev.add(float(abs(d["dk"] - int(d["uyanis"]))))
    for alan in ("uyaniklik_penceresi_dk", "gunduz_uyku_toplam_dk", "gece_uykusu_dk"):
        turev.update(float(v) for v in ((bant or {}).get(alan) or []) if v)
    if kisa_esik:
        turev.add(float(kisa_esik))

    # Rolü çıkarılamayan sayılar için serbest küme: bütün izinli değerler + türevler
    serbest = set(turev)
    for v in izinli.values():
        if isinstance(v, set):
            serbest |= {float(x) for x in v}

    karsilik = {
        "gece_uyanma": (f"Gece uyandığında önce dışarıda bekleyin (süre için planınıza bakın); "
                        f"sonra içeri girip {akis['3_deneme_dk']} dakika deneyin, uyumazsa odada "
                        f"{akis['4_rutin_dk']} dakikalık rutin yapın; en fazla "
                        f"{akis['6_tekrar_en_fazla']} kez."),
        "dis": kg_karsilik, "ic": kg_karsilik,
        "seans": (f"Uyumazsa {deneme} dakika deneyin, sonra {mola} dakika rutin molası verin "
                  f"(çok dirençli bebekte {mola_max} dakika) ve yeniden deneyin; en fazla {tekrar} kez."),
        "mola": None, "tekrar": None,
        "kucak": f"Kucağa alma aralıkları {' → '.join(_sayi(v) for v in kucak)} dakika diye artar.",
        "rutin": f"Rutin en az {rutin['en_az']}, en fazla {rutin['en_fazla']} dakika sürsün.",
        "artis": (f"Bekleme süresi günden güne standart olarak {artis_std} dakika, çok dirençli "
                  f"bebekte en az {_sayi(artis_min * 60)} saniye artar."),
        "yatir_cik": (f"Yatır-çıkta dışarıdaki bekleme {' → '.join(str(v) for v in yc_dizi)} dakika "
                      f"diye artar, en fazla {'–'.join(str(v) for v in yc_max)} dakika."
                      if yatir_cik_var else None),
    }
    karsilik["mola"] = karsilik["tekrar"] = karsilik["seans"]
    karsilik["motivasyon"] = "Ağlama sabit kalmaz, her gün azalır."
    return {"gun": g, "gun_gunduz": g_gunduz, "gece": gece, "asama": asama, "blok": "\n".join(satirlar),
            "izinli": izinli, "serbest": serbest, "turev": turev, "taban": float(taban),
            "saatler": saatler, "karsilik": karsilik}


# =============================================================================
# Doğrulama
# =============================================================================
def _icinde(dk: float, kume) -> bool:
    return any(abs(dk - v) < _TOL for v in kume)


def _gece_akisi_uygun(rol: str, dk: float, kural: dict, cumle: str) -> bool:
    """Gece akışı: içeride deneme / odada rutin süreleri serbest; taban yalnız
    "en az / altına inme" kalıbıyla (ilk bekleme süresi İlayda kararı bekliyor)."""
    akis = kural["izinli"]["gece_uyanma"] - {kural["taban"]}
    if abs(dk - kural["taban"]) < _TOL:
        return _taban_ifadesi(cumle, kural["taban"])
    return rol != "dis" and _icinde(dk, akis)          # "dışarıda 45 dk bekle" gece akışı değil


def _uygun(rol: str, dk: float, kural: dict, cumle: str = "") -> bool:
    gece = kural["gece"] and not _GUNDUZ.search(cumle)
    if abs(dk - kural["taban"]) < _TOL and _taban_ifadesi(cumle, kural["taban"]):
        return True                                    # "asla 5 dk altına inme" her bağlamda doğru
    if rol == "belirsiz":
        if _BEKLE.search(cumle):
            # Bekleme cümlesinde rolü çıkmayan sayı bir bekleme süresi olmalı:
            # kucak aralığı (1,5 → 2 dk) ya da artış miktarı bekleme süresi değildir.
            iz = kural["izinli"]
            bekleme = set(kural["turev"])
            for r in ("dis", "ic", "seans", "mola", "yatir_cik"):
                bekleme |= {float(x) for x in iz[r]}
            if gece:
                return (_gece_akisi_uygun(rol, dk, kural, cumle)
                        or _icinde(dk, set(kural["turev"]) | set(iz["yatir_cik"])))
            return _icinde(dk, bekleme)
        return _icinde(dk, kural["serbest"])
    izin = kural["izinli"].get(rol)
    if izin is None:                                   # basari vb.: blokta karşılığı yok
        return False
    # Gece "dışarıda X dk / içeride Y dk" gece akışıdır (kısa gündüz değil).
    if gece and rol in ("dis", "ic", "gece_uyanma"):
        return _gece_akisi_uygun(rol, dk, kural, cumle)
    if rol == "gece_uyanma" and abs(dk - kural["taban"]) < _TOL:
        return False                                   # taban kalıbı yukarıda; yoksa ilk bekleme sayılır
    if isinstance(izin, tuple):
        return izin[1] - _TOL <= dk <= izin[2] + _TOL
    # Rol çıkarımı sezgisel: "15 dakika rutin molası" seans, "içeride 35 dakika
    # deneyin" seans okunabiliyor. Karışan roller birlikte kabul edilir; değer
    # yine BUGÜNÜN bloğunda olmalı (ör. 2. günde iç 40 hâlâ ihlal).
    if rol in _KARISAN:
        izin = set(izin)
        for r in _KARISAN[rol]:
            if isinstance(kural["izinli"].get(r), set):
                izin |= kural["izinli"][r]
    return any(abs(dk - v) < _TOL for v in izin)


_KARISAN = {"seans": ("mola", "ic"), "mola": ("seans",)}


def _cumleler(satir: str) -> list[str]:
    return [c for c in _CUMLE_SONU.split(satir) if c.strip()]


def cumle_ihlalleri(cumle: str, kural: dict) -> list[tuple[str, float]]:
    if cumle.strip() in {k for k in kural["karsilik"].values() if k}:
        return []                                      # bloğun kendi karşılık cümlesi
    sayilar = sayi_rol.satir_sayilari(cumle)
    if sayilar and _MOTIVASYON_SAYILI.search(cumle):
        # "İlk gün 45 dakika ağlayan bir çocuk iki hafta sonra 5 dakikada…" —
        # korpus istatistiği, plan sayısı değil; değerleri tesadüfen izinli olabilir.
        return [("motivasyon", dk) for _, dk in sayilar]
    ihlal = [(rol, dk) for rol, dk in sayilar if not _uygun(rol, dk, kural, cumle)]
    for m in _SAAT.finditer(cumle):
        saat = f"{int(m.group(1)):02d}:{m.group(2)}"
        if int(m.group(1)) < 24 and saat not in kural["saatler"]:
            ihlal.append(("saat", float(int(m.group(1)) * 60 + int(m.group(2)))))
    return ihlal


def dogrula(metin: str, kural: dict) -> list[dict]:
    """İhlaller: [{rol, deger}] — METİN DÖNMEZ (log ve yeniden üretim notu için)."""
    out = []
    for satir in (metin or "").splitlines():
        for c in _cumleler(satir):
            out.extend({"rol": r, "deger": v} for r, v in cumle_ihlalleri(c, kural))
    return out


def duzeltme_notu(ihlaller: list[dict]) -> str:
    parcalar = []
    for i in ihlaller:
        v = i["deger"]
        parcalar.append(f"{i['rol']}: {_ss(int(v))}" if i["rol"] == "saat"
                        else f"{i['rol']}: {_sayi(v)} dk")
    return ("DÜZELTME: Önceki cevabında 'BUGÜNÜN KURALLARI' bloğuyla uyuşmayan süre/sayı "
            "vardı (" + ", ".join(dict.fromkeys(parcalar)) + "). Bu cevapta süre ve "
            "sayıları YALNIZ o bloktan al; blokta karşılığı olmayan süre için 'planınıza "
            "bakın' de; saat olarak yalnız plan saatlerini ve 'Şu an'ı kullan."
            + (" Gecedeki 'altına inmez' sınırı bir TABANDIR, ilk bekleme süresi değildir; "
               "kucağa alma aralıkları da bekleme süresi değildir."
               if any(i["rol"] in ("dis", "gece_uyanma", "belirsiz") for i in ihlaller) else ""))


def duzelt(metin: str, kural: dict) -> str:
    """İhlalli cümleyi bloktaki karşılığıyla değiştir (aynı karşılık bir kez);
    karşılığı yoksa cümleyi çıkar. Satır yapısı (madde işaretleri) korunur."""
    kullanilan: set[str] = set()
    yeni_satirlar = []
    for satir in (metin or "").splitlines():
        onek = re.match(r"^(\s*(?:[-*•]|\d+\.)\s+)?", satir).group(0)
        govde = satir[len(onek):]
        parcalar = []
        for c in _cumleler(govde):
            ihlal = cumle_ihlalleri(c, kural)
            if not ihlal:
                parcalar.append(c)
                continue
            rol = ihlal[0][0]
            if kural["gece"] and rol in ("dis", "ic", "belirsiz") and not _GUNDUZ.search(c):
                rol = "gece_uyanma"                    # gece bekleme cümlesi → gece akışı
            elif not kural["gece"] and rol == "gece_uyanma" and not _GECE.search(c):
                rol = "dis"                            # gündüz "uyanırsa … bekle" → kısa gündüz
            k = kural["karsilik"].get(rol)
            if k and k not in kullanilan:
                kullanilan.add(k)
                parcalar.append(k)
        if govde.strip() and not parcalar:
            continue                                   # satırın tamamı çıktı
        yeni_satirlar.append(onek + " ".join(parcalar) if parcalar else satir)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(yeni_satirlar)).strip()
