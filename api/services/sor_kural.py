"""
Sor — "bugünün kuralları" bloğu ve cevaptaki sayıların ROL bazlı doğrulaması.

İki bayrak (kapalıyken hiçbir şey çağrılmaz, davranış bugünkünün aynısı):
    SOR_KURAL_BLOGU     — bağlama blok + sistem kuralı ("süre ve sayı yalnız
                          bu bloktan alınır")
    SOR_SAYI_DOGRULAMA  — cevap anneye gitmeden önce doğrulama. Değerler
                          (dogrulama_modu):
                            kapali (tanımsız)  hiçbir şey yapılmaz
                            izle               cevap değişmez; log'a ihlal sayısı/türü
                            duzelt ("1/true")  İKİNCİ ÇAĞRI YOK: ihlalli cümle bloktaki
                                               karşılığıyla değişir; karşılık cevapta
                                               zaten varsa ya da yoksa cümle çıkar
                            yeniden            bir kez yeniden üretim (ihlal modele
                                               söylenir), yine ihlalde duzelt — ölçüm
                                               için (11–45 sn bekletiyordu)

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

import os
import re
from datetime import timedelta
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
# Sıra sayısındaki nokta ("3. günü") cümle sonu değildir.
_CUMLE_SONU = re.compile(r"(?<=[.!?])(?<!\b\d\.)(?<!\b\d\d\.)(?<!\bör\.)(?<!\börn\.)"
                         r"(?<!\bvb\.)(?<!\bvs\.)\s+(?=\S)")
# "asla 5 dk altına inme", "en az 5 dk": gece bekleme TABANI. Taban, ilk bekleme
# süresi değildir (o alan İlayda kararı bekliyor) — taban değeri gece yalnız bu
# kalıpla geçer.
def _taban_ifadesi(cumle: str, taban: float) -> bool:
    t = _sayi(taban).replace(",", "[,.]")
    return bool(re.search(rf"en az\s+{t}\s*(?:dk|dakika)|(?<![\d,.]){t}\s*(?:dk|dakika)\w*\s+"
                          rf"(?:altın|aşağı)", cumle, re.IGNORECASE))
_BEKLE = re.compile(r"bekle", re.IGNORECASE)
_GUNDEN_GUNE = re.compile(r"günden güne|her gün", re.IGNORECASE)
_GERI_DALIS_BAGLAMI = re.compile(r"(?:ve )?(?:üzeri|üstü)[^.]*uyu|uyu\w*[^.]*(?:ve )?(?:üzeri|üstü)",
                                 re.IGNORECASE)
_KISA_ESIK_BAGLAMI = re.compile(r"\d\s*(?:dakika|dk)\w*\s+(?:az|kısa|altı)|altında kal|"
                                r"\d\s*(?:dakika|dk)\w*\s+(?:veya|ya da)\s+(?:daha\s+)?(?:fazla|uzun)",
                                re.IGNORECASE)
# sayi_rol "45 dakika — en fazla 3 kez"i zincir sayıp 3'ü dakika okuyor (denetim
# betiğinin davranışı korunur; Sor denetim kopyasında tire noktalı virgüle döner)
_TIRE_EN_FAZLA = re.compile(r"\s*[—–-]\s*(?=en fazla\s+\d+\s*(?:kez|tekrar|kere|defa))", re.IGNORECASE)
_GUNDUZ = re.compile(r"gündüz", re.IGNORECASE)
_GECE = re.compile(r"gece", re.IGNORECASE)

# --- Yazıyla sayılar: birimle geçtiğinde rakamla AYNI kurala girer ----------
# ("beş dakika" → "5 dakika", "bir buçuk dakika" → "1,5 dakika", "yarım saat" →
# "30 dakika"). "bir şey" gibi birimsiz kullanım sayılmaz. Çeviri yalnız
# DENETİM kopyasında yapılır; anneye giden metin değişmez.
_BIRLER = {"bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5, "altı": 6, "yedi": 7,
           "sekiz": 8, "dokuz": 9}
_ONLAR = {"on": 10, "yirmi": 20, "otuz": 30, "kırk": 40, "elli": 50, "altmış": 60,
          "yetmiş": 70, "seksen": 80, "doksan": 90}
_B, _O = "|".join(_BIRLER), "|".join(_ONLAR)
_SOZ_SAYI = rf"(?:yüz\s+)?(?:(?:{_O})(?:\s+(?:{_B}))?|(?:{_B})|yüz)"
_SAYIM_BIRIM = r"(?:gün|gece|kez|defa|hafta)"
_YAZI = re.compile(rf"(?<!\w)({_SOZ_SAYI})(\s+buçuk)?(\s+)((?:dakika|dk\b|saniye|sn\b|saat|"
                   rf"{_SAYIM_BIRIM})\w*)", re.IGNORECASE)
_YARIM = re.compile(r"(?<!\w)(yarım|çeyrek)\s+(saat|dakika)\w*", re.IGNORECASE)
# Sayımlar (rakam ya da çevrilmiş yazı): "2 gün", "3 kez", "1 hafta". Sıra
# sayısı ("3. gün", "Gün 4") sayım değildir.
_SAYIM = re.compile(rf"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*({_SAYIM_BIRIM})\w*", re.IGNORECASE)
_SAYIM_ROL = {"gün": "gun", "gece": "gun", "kez": "kez", "defa": "kez", "hafta": "hafta"}


def _soz_deger(soz: str) -> float:
    v = 0.0
    for p in soz.replace("İ", "i").replace("I", "ı").lower().split():   # "İki" → "iki"
        v += 100 if p == "yüz" else _ONLAR.get(p, 0) + _BIRLER.get(p, 0)
    return v


def yaziyi_rakama(metin: str) -> str:
    def _yazi(m):
        v = _soz_deger(m.group(1)) + (0.5 if m.group(2) else 0)
        return f"{_sayi(v)}{m.group(3)}{m.group(4)}"
    def _yarim(m):
        tam = 60 if m.group(2).lower() == "saat" else 1
        return f"{_sayi(tam * (0.5 if m.group(1).lower() == 'yarım' else 0.25))} dakika"
    return _YAZI.sub(_yazi, _YARIM.sub(_yarim, metin))


# --- Bekleyen alanda YÖN: "aynı gece her uyanmada bekleme artar" ------------
# Gece içi artış İlayda kararı bekliyor → sayı da yön de yasak ("aynı gece içindeki
# ARTIŞ için planınıza bakın" da artışı varsayar). Günden güne artış (tabloda
# dolu) bu kalıba girmez: bağlam "uyanma / aynı gece" ister.
_GECE_ICI = re.compile(r"(?:her|sonraki|ikinci|üçüncü|yeni|diğer)\s+(?:\w+\s+)?uyan\w*|aynı gece\w*|"
                       r"gece içi\w*|gece içinde\w*|uyanmadan uyanmaya", re.IGNORECASE)
_YON = re.compile(r"\bart(?:a|ı|ış|tı)\w*|uzat\w*|uzar\b|\bfazla\w*|çoğal\w*|azal\w*|kısal\w*|"
                  r"kısalt\w*|aynı (?:kal|tut)\w*|bir öncekin\w*", re.IGNORECASE)
_SURE_BAGLAMI = re.compile(r"bekle|süre", re.IGNORECASE)

# --- 3 denemeden sonraki gece: tablo ne diyorsa o; yumuşatma ve suçlama yok ---
_YUMUSATMA = re.compile(r"başarısızlık değil|başarısız say(?:ıl)?ma(?:z|ması|mak|yın)|"
                        r"başarısız sayılmaz|bir hata değil|hata değildir|"
                        r"ilk (?:\w+ )?gün\w* (?:bu )?esnek|esnektir|esnek(?:siniz| davran)",
                        re.IGNORECASE)
_YUMUSATMA_BAGLAMLI = re.compile(r"normal karşılanır|sorun değil|sorun olmaz|önemli değil|"
                                 r"hiçbir şey (?:olmaz|kaybolmaz|kaybetmezsiniz)|neden olmaz|"
                                 r"etkilemez|endişe etmeyin|merak etmeyin|korkmayın|nadir|olası değil|"
                                 r"kaygı(?:lanmayın| kaynağı yapmayın)", re.IGNORECASE)
_BASARISIZLIK_BAGLAMI = re.compile(r"başarısız|destek\w* (?:verip|vererek) uyut|mecburen|"
                                   r"denemeden sonra|kez sonra|deneme de", re.IGNORECASE)
_SUCLAMA = re.compile(r"(?:sizin|senin) (?:hatanız|hatan|suçunuz|suçun|yüzünüzden)|"
                      r"hata yaptınız|yanlış yaptınız|tutarsız davrandınız|pes ettiniz|"
                      r"yeterince (?:kararlı|sabırlı|tutarlı|ısrarcı) (?:değilsiniz|olmadınız|"
                      r"davranmadınız)|siz (?:bozdunuz|alıştırdınız)", re.IGNORECASE)

# --- Aşama: annenin yeri ---------------------------------------------------
# Tablodaki merdiven.asamalar[].pozisyon metninin anneye dönük hâli.
_YER = {
    "besik_yani": "odada, beşiğin yanında (sandalyede ya da ayakta)",
    "oda_ortasi": "odada, odanın ortasında (gerekirse temas edip yerinize dönersiniz)",
    "kapi": "odanın içinde, kapının yanında; bebek sizi tam görür",
    "kapi_esigi": "kapı eşiğinde; bebek sizi tam görür",
    "yatir_cik": "yatırıp odadan çıkarsınız (yatır-çık)",
}
_ODADA = ("besik_yani", "oda_ortasi", "kapi")
# Odada durulan aşamada "içeri girmeyin" çelişkidir — gece uyanmasındaki
# dışarıda bekleme (uyanma / bekleme süresi bağlamı) hariç.
_ICERI_GIRME = re.compile(r"içeri (?:girmeyin|girmeyiniz|girme\b)|odaya girmeyin|odadan çık(?:ın|ıp)|"
                          r"odayı terk ed|odadan ayrıl", re.IGNORECASE)
_GECE_UYANMA_BAGLAMI = re.compile(r"uyan|bekleme süre|plan\w* bak", re.IGNORECASE)
_BESIK_YANINDA = re.compile(r"beşi(?:ğin|k)\w* yanında (?:otur|kal|dur)", re.IGNORECASE)

# --- Gün adları: model hesaplamaz, blokta hazır ---------------------------
_GUN_ADLARI = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
_GUN_ADI = re.compile(r"(?<!\w)(pazartesi|salı|çarşamba|perşembe|cumartesi|cuma|pazar)(?!\w)",
                      re.IGNORECASE)
# Sayı olmayan ihlal türleri (değer 0)
METIN_ROLLERI = ("yon", "yumusatma", "suclama", "asama", "gun_adi", "gun_no")


def blok_acik_mi() -> bool:
    return _bayrak(BLOK_BAYRAK)


MODLAR = ("kapali", "izle", "duzelt", "yeniden")


def dogrulama_modu() -> str:
    """SOR_SAYI_DOGRULAMA değeri; her çağrıda env'den. Eski "1/true" → duzelt."""
    v = (os.getenv(DOGRULAMA_BAYRAK) or "").strip().lower().replace("ü", "u")
    if v in MODLAR:
        return v
    return "duzelt" if _bayrak(DOGRULAMA_BAYRAK) else "kapali"


def dogrulama_acik_mi() -> bool:
    return dogrulama_modu() != "kapali"


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

    # --- Takvim: gün adlarını model hesaplamaz ---------------------------------
    from api.services.sor_baglam import AYLAR
    bugun_t = d["gun"]
    yarin_t, dun_t = bugun_t + timedelta(days=1), bugun_t - timedelta(days=1)
    toplam = int(d.get("toplam") or asamalar[-1]["gun"][1])

    def _t(gun_) -> str:
        return f"{_GUN_ADLARI[gun_.weekday()]} {gun_.day} {AYLAR[gun_.month - 1]}"

    def _gun_asama(n: int) -> str:
        if n > toplam:
            return "program tamamlanmış olur"
        a = next((x for x in asamalar if x["gun"][0] <= n <= x["gun"][1]), asamalar[-1])
        return f"eğitimin {n}. günü, aşama {a['ad']}"

    app_gun = max(1, int(d["app_gun"] or 1))
    if d["sabah_once"]:
        takvim = (f"- Takvim (gün adlarını kendin hesaplama, YALNIZ buradan al): şu an "
                  f"{_t(bugun_t)} gecesi, eğitimin {g}. gününün gecesi; sabah uyanınca "
                  f"{_t(bugun_t)}: {_gun_asama(app_gun)}; yarın {_t(yarin_t)}: "
                  f"{_gun_asama(app_gun + 1)}.")
    else:
        takvim = (f"- Takvim (gün adlarını kendin hesaplama, YALNIZ buradan al): bugün "
                  f"{_t(bugun_t)}: {_gun_asama(app_gun)}; yarın {_t(yarin_t)}: "
                  f"{_gun_asama(app_gun + 1)}.")

    takvim_karsilik = ((f"Bu sabah uyanınca {_t(bugun_t)}: " if d["sabah_once"] else
                        f"Bugün {_t(bugun_t)}: ") + f"{_gun_asama(app_gun)}; yarın {_t(yarin_t)}: "
                       f"{_gun_asama(app_gun + 1)}.")
    yer = _YER.get(asama["kod"], asama.get("pozisyon") or asama["ad"])
    bekleyen = ["ilk bekleme süresi",
                "aynı gece içinde bir uyanmadan ötekine bekleme süresinin değişimi"]
    if 4 <= g_gunduz <= 12:
        bekleyen.append("kısa gündüz uykusundaki dışarıda/içeride bekleme süreleri")
    if asama["kod"] in ("kapi", "kapi_esigi"):
        bekleyen.append("bu aşamada içeride temasın nasıl olacağı")

    satirlar = [
        f"BUGÜNÜN KURALLARI (süre ve sayıları YALNIZ buradan al; burada olmayan süre için "
        f"'planınıza bakın' de):",
        f"- Aşama: {asama['ad']} (eğitimin {g}. günü{', gece' if gece else ''}).",
        takvim,
        f"- Annenin yeri ({asama['ad']}; yatırırken ve içeri girdiğinde): {yer}. Bu aşamayla "
        f"çelişen yönlendirme yapma"
        + (" (ör. gece uyanmasındaki dışarıda bekleme dışında 'içeri girmeyin' deme)."
           if asama["kod"] in _ODADA else "."),
        f"- Uykuya dalmazsa: {deneme} dk dene → uyumazsa {mola} dk rutin molası (çok dirençli "
        f"bebekte {mola_max} dk) → yeniden {deneme} dk; en fazla {tekrar} kez. {tekrar} kez "
        f"sonra gece: {sonrasi['gece']}; gündüz: {sonrasi['gunduz']}.",
        f"- {tekrar} denemeden sonra da uyumayan GECE için tablodaki kural: {sonrasi['gece']}. "
        f"Bunu aynen söyle: yumuşatma ('başarısızlık değil', 'ilk günler esnek' gibi deme), "
        f"anneyi suçlama, tabloda olmayan bir sonuç (ör. ertesi günün nasıl değişeceği) ekleme.",
        f"- Gece uyanırsa: önce dışarıda bekle (ilk bekleme süresi ve aynı gece içindeki "
        f"bekleme süreleri için planına bak); sonra içeri gir ve {akis['3_deneme_dk']} dk dene; "
        f"uyumazsa odada {akis['4_rutin_dk']} dk rutin (beslenme yok); en fazla "
        f"{akis['6_tekrar_en_fazla']} kez. İçerideyken: {icerde}. "
        f"Bekleme günden güne standart +{artis_std} dk, "
        f"çok dirençli bebekte en az +{_sayi(artis_min * 60)} sn artar; önceki günle aynı ya "
        f"da daha az olamaz; asla {taban} dk altına inmez. Bu artış kuralı GÜNDEN GÜNEdir, "
        f"aynı gecenin uyanmaları için değil.",
        f"- Kararı bekleyen konular ({'; '.join(bekleyen)}): sayı da YÖN de söyleme. 'Aynı "
        f"gece her uyanmada bekleme artar / azalır / aynı kalır' DEME; yalnız 'bunun için "
        f"planınıza bakın' de.",
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
        # Sayımlar ("2 gün", "3 kez"): gün sayısı programdan/bugünden türeyenler;
        # "kez" 1..tekrar (ve BEBEK VERİSİ'ndeki gece uyanma sayısı); hafta yok.
        "gun": ({1, 3, toplam, g, g_gunduz, max(0, toplam - g),
                 max(0, toplam - g_gunduz), app_gun, app_gun + 1}
                | ({yc_21["gun"]} if yatir_cik_var else set())),
        "kez": set(range(1, int(tekrar) + 1)) | ({int(baby.night_wakes)} if getattr(
            baby, "night_wakes", None) else set()),
        "hafta": set(),
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
    for m in [int(b["start_minute"]) for b in sched if b.get("start_minute") is not None]:
        for once in {rutin["en_az"], rutin["en_fazla"], yontem.deger("bildirim.uyku_once_dk"),
                     yontem.deger("bildirim.gece_rutin_once_dk")}:
            saatler.add(_ss(m - int(once)))
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
    for r, v in izinli.items():
        if isinstance(v, set) and r not in ("gun", "kez", "hafta"):    # sayımlar dakika değil
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
    karsilik["yon"] = "Aynı gece içindeki bekleme süreleri için planınıza bakın."
    karsilik["yumusatma"] = f"{tekrar} denemeden sonra da uyumazsa o gece {sonrasi['gece']}."
    karsilik["asama"] = f"Bugün {asama['ad']} aşamasındasınız; yeriniz: {yer}."
    # Karşılık cevapta zaten var mı (aynı bilgi iki kez geçmesin): sayısız
    # karşılıklar için iz deseni; sayılılar için sayıların hepsi geçiyor mu.
    izler = {
        "yon": r"aynı gece[^.]*plan[^.]*bak|plan[^.]*bak[^.]*aynı gece",   # "planınızın gece bölümüne bakın"
        "yumusatma": r"başarısız sayılır",
        "asama": re.escape(asama["ad"].lower()),
        "motivasyon": r"ağlama[^.]*(?:azal|sabit kalma)",
    }
    if not kg_dis:
        izler["dis"] = izler["ic"] = r"gündüz[^.]*plan\w* bak|plan\w* bak[^.]*gündüz"
    takvim_d = {"bugun": bugun_t.weekday(), "yarin": yarin_t.weekday(),
                "dun": dun_t.weekday(), "sabah_once": bool(d["sabah_once"]),
                "app_gun": app_gun, "gece_gun": g}
    karsilik["gun_no"] = takvim_karsilik
    return {"gun": g, "gun_gunduz": g_gunduz, "gece": gece, "asama": asama, "blok": "\n".join(satirlar),
            "izinli": izinli, "serbest": serbest, "turev": turev, "taban": float(taban),
            "takvim": takvim_d, "izler": izler, "kisa_esik": kisa_esik,
            "geri_dalis": float(geri_dalis),
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
    esik = kural.get("kisa_esik")
    if esik and abs(dk - esik) < _TOL and _KISA_ESIK_BAGLAMI.search(cumle):
        return True                                    # "kısa uyku (60 dakikadan az)"
    if abs(dk - kural.get("geri_dalis", -1)) < _TOL and _GERI_DALIS_BAGLAMI.search(cumle):
        return True                                    # "5 dk ve üzeri geri uyursa uzatma biter"
    if abs(dk - kural["taban"]) < _TOL and _taban_ifadesi(cumle, kural["taban"]):
        return True                                    # "asla 5 dk altına inme" her bağlamda doğru
    if rol == "belirsiz":
        artis = kural["izinli"]["artis"]
        if _GUNDEN_GUNE.search(cumle) and artis[1] - _TOL <= dk <= artis[2] + _TOL:
            return True                                # "bekleme günden güne 5 dk artar"
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


_GUN_NO = {ad.lower(): i for i, ad in enumerate(_GUN_ADLARI)}


def _gun_adi_beklenen(cumle: str, bas: int, tk: dict) -> tuple[set[int], int | None]:
    """Gün adının izinli değerleri ve (bağlam belliyse) doğrusu. Yakındaki
    "yarın / bugün / dün" sözcüğüne bakılır; gece (sabah uyanışından önce)
    "yarın" bugünün sabahını, "bugün" dünü (gecenin başladığı gün) de kapsar."""
    on = cumle[max(0, bas - 30):bas].lower()
    konum = {k: on.rfind(k) for k in ("yarın", "bugün", "dün")}
    k, yer = max(konum.items(), key=lambda x: x[1])
    gece = tk["sabah_once"]
    if yer < 0:
        return {tk["bugun"], tk["yarin"]} | ({tk["dun"]} if gece else set()), None
    if k == "yarın":
        return {tk["yarin"]} | ({tk["bugun"]} if gece else set()), tk["yarin"]
    if k == "bugün":
        return {tk["bugun"]} | ({tk["dun"]} if gece else set()), tk["bugun"]
    return {tk["dun"]}, tk["dun"]


_SIRA_GUN = re.compile(r"(?<![\d.,])(\d{1,2})\.\s*gün|\bgün\s+(\d{1,2})\b(?![.,]\d|\s*(?:dk|dakika|sn|saniye|saat))",
                       re.IGNORECASE)
_DAYANAK = re.compile(r"(?<!\w)(yarın|bugün|pazartesi|salı|çarşamba|perşembe|cumartesi|cuma|pazar)(?!\w)",
                      re.IGNORECASE)


def _gun_no_celisir(cumle: str, tk: dict) -> bool:
    """"Yarın Salı, eğitimin 3. günü" gibi: sıra gün, ÖNCESİNDEKİ en yakın
    dayanağın (gün adı / yarın / bugün) eğitim günüyle tutmuyor mu."""
    app, gece = tk["app_gun"], tk["sabah_once"]
    for m in _SIRA_GUN.finditer(cumle):
        no = int(m.group(1) or m.group(2))
        dayanaklar = list(_DAYANAK.finditer(cumle[:m.start()]))
        if not dayanaklar:
            continue
        d = dayanaklar[-1].group(1).lower()
        if d == "yarın":
            izin = {app + 1} | ({app} if gece else set())
        elif d == "bugün":
            izin = {app} | ({tk["gece_gun"]} if gece else set())
        else:
            gno = _GUN_NO[d]
            izin = ({app} | ({tk["gece_gun"]} if gece else set()) if gno == tk["bugun"] else
                    {app + 1} if gno == tk["yarin"] else {app - 1} if gno == tk["dun"] else set())
            if not izin:
                continue                               # yanlış gün adı ayrıca gun_adi'dır
        if no not in izin:
            return True
    return False


def _metin_ihlalleri(cumle: str, kural: dict, onceki: str = "") -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    # Bekleyen alanda yön ("aynı gece her uyanmada bekleme artar")
    temiz = re.sub(r"\b(?:en|birden) fazla", " ", cumle, flags=re.IGNORECASE)
    if _GECE_ICI.search(temiz) and not re.search(r"günden güne", temiz, re.IGNORECASE):
        for m in _YON.finditer(temiz):
            if _SURE_BAGLAMI.search(temiz[max(0, m.start() - 50):m.end() + 50]):
                out.append(("yon", 0.0))
                break
    # 3 denemeden sonraki gece: yumuşatma / suçlama
    if _YUMUSATMA.search(cumle) or (_YUMUSATMA_BAGLAMLI.search(cumle)
                                    and _BASARISIZLIK_BAGLAMI.search(onceki + " " + cumle)):
        out.append(("yumusatma", 0.0))
    if _SUCLAMA.search(cumle):
        out.append(("suclama", 0.0))
    # Aşamayla çelişen yönlendirme
    kod = kural["asama"]["kod"]
    if kod in _ODADA and _ICERI_GIRME.search(cumle) and not _GECE_UYANMA_BAGLAMI.search(cumle):
        out.append(("asama", 0.0))
    elif kod != "besik_yani" and _BESIK_YANINDA.search(cumle):
        out.append(("asama", 0.0))
    # Gün numarası, yanındaki gün adı / "yarın" / "bugün" ile tutmalı
    if _gun_no_celisir(cumle, kural["takvim"]):
        out.append(("gun_no", 0.0))
    # Takvimle çelişen gün adı
    for m in _GUN_ADI.finditer(cumle):
        izin, _ = _gun_adi_beklenen(cumle, m.start(), kural["takvim"])
        if _GUN_NO[m.group(1).lower()] not in izin:
            out.append(("gun_adi", 0.0))
    return out


def cumle_ihlalleri(cumle: str, kural: dict, onceki: str = "") -> list[tuple[str, float]]:
    """`onceki`: bir önceki cümle (yalnız yumuşatmanın başarısızlık bağlamı için)."""
    if cumle.strip() in {k for k in kural["karsilik"].values() if k}:
        return []                                      # bloğun kendi karşılık cümlesi
    # Denetim kopyası: yazıyla sayılar rakama; "N defa" sayım kuralına (kez).
    n = re.sub(r"(\d)\s*defa", r"\1 kez", yaziyi_rakama(cumle), flags=re.IGNORECASE)
    n = _TIRE_EN_FAZLA.sub("; ", n)
    sayilar = sayi_rol.satir_sayilari(n)
    sayimlar = [(_SAYIM_ROL[m.group(2).lower()], float(m.group(1).replace(",", ".")))
                for m in _SAYIM.finditer(n)]
    if (sayilar or sayimlar) and _MOTIVASYON_SAYILI.search(n):
        # "İlk gün 45 dakika ağlayan bir çocuk iki hafta sonra 5 dakikada…" —
        # korpus istatistiği, plan sayısı değil; değerleri tesadüfen izinli olabilir.
        return [("motivasyon", v) for _, v in sayilar + sayimlar]
    ihlal = [(rol, dk) for rol, dk in sayilar if not _uygun(rol, dk, kural, n)]
    ihlal += [(rol, v) for rol, v in sayimlar
              if not _icinde(v, {float(x) for x in kural["izinli"][rol]})]
    for m in _SAAT.finditer(n):
        saat = f"{int(m.group(1)):02d}:{m.group(2)}"
        if int(m.group(1)) < 24 and saat not in kural["saatler"]:
            ihlal.append(("saat", float(int(m.group(1)) * 60 + int(m.group(2)))))
    return ihlal + _metin_ihlalleri(cumle, kural, onceki)


def dogrula(metin: str, kural: dict) -> list[dict]:
    """İhlaller: [{rol, deger}] — METİN DÖNMEZ (log ve yeniden üretim notu için)."""
    out = []
    onceki = ""
    for satir in (metin or "").splitlines():
        for c in _cumleler(satir):
            out.extend({"rol": r, "deger": v} for r, v in cumle_ihlalleri(c, kural, onceki))
            onceki = c
    return out


def ozet(ihlaller: list[dict]) -> str:
    """Log için içeriksiz özet: 'n=3 tur=dis,saat'."""
    turler = ",".join(dict.fromkeys(i["rol"] for i in ihlaller)) or "-"
    return f"n={len(ihlaller)} tur={turler}"


_METIN_ACIKLAMA = {
    "yon": "kararı bekleyen konuda yön (ör. aynı gece her uyanmada bekleme artar)",
    "yumusatma": "3 deneme sonrası gece kuralını yumuşatma",
    "suclama": "anneyi suçlayan dil",
    "asama": "bugünün aşamasıyla çelişen yönlendirme",
    "gun_adi": "takvimle uyuşmayan gün adı",
    "gun_no": "takvimle uyuşmayan eğitim günü numarası",
}


def duzeltme_notu(ihlaller: list[dict]) -> str:
    parcalar = []
    for i in ihlaller:
        v, rol = i["deger"], i["rol"]
        if rol in _METIN_ACIKLAMA:
            parcalar.append(_METIN_ACIKLAMA[rol])
        elif rol == "saat":
            parcalar.append(f"saat: {_ss(int(v))}")
        elif rol in ("gun", "kez", "hafta"):
            parcalar.append(f"{rol}: {_sayi(v)}")
        else:
            parcalar.append(f"{rol}: {_sayi(v)} dk")
    return ("DÜZELTME: Önceki cevabında 'BUGÜNÜN KURALLARI' bloğuyla uyuşmayan süre/sayı "
            "vardı (" + ", ".join(dict.fromkeys(parcalar)) + "). Bu cevapta süre ve "
            "sayıları YALNIZ o bloktan al; blokta karşılığı olmayan süre için 'planınıza "
            "bakın' de; saat olarak yalnız plan saatlerini ve 'Şu an'ı kullan."
            + (" Gecedeki 'altına inmez' sınırı bir TABANDIR, ilk bekleme süresi değildir; "
               "kucağa alma aralıkları da bekleme süresi değildir."
               if any(i["rol"] in ("dis", "gece_uyanma", "belirsiz") for i in ihlaller) else ""))


def _gun_adi_duzelt(cumle: str, kural: dict) -> str | None:
    """Yanlış gün adını bağlamdaki doğrusuyla değiştir; bağlam yoksa None."""
    tk = kural["takvim"]

    def _f(m):
        izin, dogru = _gun_adi_beklenen(cumle, m.start(), tk)
        if _GUN_NO[m.group(1).lower()] in izin:
            return m.group(0)
        if dogru is None:
            raise ValueError
        ad = _GUN_ADLARI[dogru]
        return ad if m.group(1)[0].isupper() else ad.lower()
    try:
        return _GUN_ADI.sub(_f, cumle)
    except ValueError:
        return None


def _karsilik_var(rol: str, k: str, metin: str, kural: dict) -> bool:
    """Karşılık cevabın geçerli kısmında zaten var mı (aynı bilgi iki kez geçmesin)."""
    low = metin.lower()
    if k.lower() in low:
        return True
    iz = kural.get("izler", {}).get(rol)
    if iz:
        return bool(re.search(iz, low))
    sayilar = _degerler(re.sub(r"\([^)]*\)", " ", k))     # "(çok dirençli bebekte 30 dk)" isteğe bağlı
    if not sayilar:
        return False
    return sayilar <= _degerler(metin) and ("planınıza bak" not in k
                                            or bool(re.search(r"plan\w* bak", low)))


def _degerler(metin: str) -> set[float]:
    """Metindeki süre (dakikaya çevrilmiş) ve sayım değerleri."""
    n = _TIRE_EN_FAZLA.sub("; ", yaziyi_rakama(metin))
    return ({round(v, 3) for _, v in sayi_rol.satir_sayilari(n)}
            | {round(float(m.group(1).replace(",", ".")), 3) for m in _SAYIM.finditer(n)}
            | {round(float(x.replace(",", ".")), 3) for x in re.findall(r"(?<![\d:])\d+(?:,\d+)?(?![\d:])", n)})


def duzelt_ayrinti(metin: str, kural: dict) -> tuple[str, int, int]:
    """İhlalli cümleyi bloktaki karşılığıyla değiştir. Karşılık cevapta zaten
    varsa (ya da bu düzeltmede bir kez kullanıldıysa) ya da karşılığı yoksa
    cümle çıkar. Yanlış gün adı yerinde düzeltilir. Satır yapısı korunur.
    Dönen: (metin, değiştirilen cümle sayısı, çıkarılan cümle sayısı)."""
    satirlar = (metin or "").splitlines()
    tum = [c for satir in satirlar for c in _cumleler(satir)]
    onceki_of = {i: (tum[i - 1] if i else "") for i in range(len(tum))}
    gecerli = " ".join(c for i, c in enumerate(tum) if not cumle_ihlalleri(c, kural, onceki_of[i]))
    sira = 0
    kullanilan: set[str] = set()
    degisen = cikan = 0
    yeni_satirlar = []
    for satir in satirlar:
        onek = re.match(r"^(\s*(?:[-*•]|\d+\.)\s+)?", satir).group(0)
        govde = satir[len(onek):]
        parcalar = []
        for c in _cumleler(govde):
            ihlal = cumle_ihlalleri(c, kural, onceki_of.get(sira, ""))
            sira += 1
            if not ihlal:
                parcalar.append(c)
                continue
            if all(r == "gun_adi" for r, _ in ihlal):
                duz = _gun_adi_duzelt(c, kural)
                if duz is not None and not cumle_ihlalleri(duz, kural):
                    parcalar.append(duz)
                    degisen += 1
                    continue
            rol = next((r for r, _ in ihlal if r != "gun_adi"), ihlal[0][0])
            if kural["gece"] and rol in ("dis", "ic", "belirsiz") and not _GUNDUZ.search(c):
                rol = "gece_uyanma"                    # gece bekleme cümlesi → gece akışı
            elif not kural["gece"] and rol == "gece_uyanma" and not _GECE.search(c):
                rol = "dis"                            # gündüz "uyanırsa … bekle" → kısa gündüz
            k = kural["karsilik"].get(rol)
            if k and k not in kullanilan and not _karsilik_var(rol, k, gecerli, kural):
                kullanilan.add(k)
                gecerli += " " + k
                parcalar.append(k)
                degisen += 1
            else:
                cikan += 1
        if govde.strip() and not parcalar:
            continue                                   # satırın tamamı çıktı
        yeni_satirlar.append(onek + " ".join(parcalar) if parcalar else satir)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(yeni_satirlar)).strip(), degisen, cikan


def duzelt(metin: str, kural: dict) -> str:
    return duzelt_ayrinti(metin, kural)[0]
