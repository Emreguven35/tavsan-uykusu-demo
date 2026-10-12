"""
Yöntem tablosundan okuma ANINDA üretilen içerik — YONTEM_TABLOSU bayrağıyla.

Bayrak kapalıyken hiçbir fonksiyon çağrılmaz: davranış bugünkünün aynısı.

1) GECE KARTI v2 (content.night_wake_protocol)
   Saklanan kart (plan_adapter.NIGHT_WAKE_PROTOCOL) "45 dk bekleyip kendi kendine
   dalmasına izin verin" diyor; İlayda'nın akışı farklı (kayıt11, kayıt37): önce
   dışarıda bekleme → içeride o aşamanın yöntemiyle 45 dk → uyumazsa odada 15 dk
   rutin (beslenme yok) → en fazla 3 kez → yine uyumazsa destek vererek uyutma.
   Okuma anında karta `baslik` ve `adimlar` eklenir; resist_minutes /
   routine_minutes AYNEN kalır (küre bunları kullanıyor). İlayda kararı bekleyen
   alanlar (ilk bekleme süresi, gece içi artış, kapı aşamalarında temas) için
   sayı YAZILMAZ.
   Build < 28 (ve sürümü bilinmeyen) istekte kart HİÇ gönderilmez: eski uygulama
   alanı görünce eski metni çiziyor, alan yoksa kartı çizmiyor.

2) BİLDİRİM SÜRELERİ: gece rutin hatırlatması 15 dk (İlayda 25 Eylül), gündüz
   30 dk — ikisi de tablodan.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from api.config import _bayrak
from engine import yontem

BAYRAK = "YONTEM_TABLOSU"
GECE_KARTI_MIN_BUILD = 28


def acik_mi() -> bool:
    """Her çağrıda env'den okunur (önbelleksiz) — bkz. hazirlik.acik_mi."""
    return _bayrak(BAYRAK)


# =============================================================================
# Aşama
# =============================================================================
def asama(training_started_at: date | None, training_completed_at: date | None,
          gun: date) -> dict | None:
    """Bugünün (uygulamanın gösterdiği gün) merdiven aşaması: {gun, kod, ad}.
    Eğitim başlamamışsa None; 13. günü geçmiş ya da tamamlanmışsa son aşama."""
    from api.services import plan_adapter
    asamalar = yontem.deger("merdiven.asamalar")
    if training_completed_at is not None:
        return dict(asamalar[-1])
    g = plan_adapter.egitim_gunu(training_started_at, gun)
    if g is None:
        return None
    return asama_gunden(g)


def asama_gunden(g: int) -> dict:
    """Eğitim günü numarasından merdiven aşaması (egitim_gunu=g ile)."""
    asamalar = yontem.deger("merdiven.asamalar")
    for a in asamalar:
        if a["gun"][0] <= g <= a["gun"][1]:
            return dict(a, egitim_gunu=g)
    return dict(asamalar[-1] if g > asamalar[-1]["gun"][1] else asamalar[0], egitim_gunu=g)


# =============================================================================
# İlayda 12 Ekim (tablo v0.4) — kart, Sor bloğu ve plan düzeltmesi bu
# fonksiyonları ORTAK kullanır (tek kaynak).
# =============================================================================
def gece_ilk_bekleme_dk(gece_no: int) -> int:
    """Bu gecenin ilk uyanmasında dışarıda bekleme: 1. gece ilk_gece_ilk_bekleme_dk,
    her gece +gunluk_artis_standart_dk (5, 10, 15 …)."""
    ilk = int(yontem.deger("gece_uyanma.ilk_gece_ilk_bekleme_dk"))
    artis = int(yontem.deger("gece_uyanma.gunluk_artis_standart_dk"))
    return ilk + artis * (max(1, int(gece_no)) - 1)


def gece_ici_artis_dk() -> int:
    return int(yontem.deger("gece_uyanma.gece_ici_artis_dk"))


def destek_gecesi_mi(gece_no: int | None) -> bool:
    """3 denemeden sonra destekle uyutulan gece mi (tek yer:
    seans.destek_gecesi_egitim_gunleri). Gece bilinmiyorsa 1. gece sayılır."""
    return int(gece_no or 1) in set(yontem.deger("seans.destek_gecesi_egitim_gunleri"))


def kisa_gunduz_dis_ic(gun_no: int) -> tuple[int, int]:
    """Kısa gündüz uykusu (dış, iç) dk — gün 1-3 tablodan; 4. günden itibaren dış
    her gün +5, iç = 45 − dış; dış 45'te durur (hepsi dışarıda). 13+ çağıran
    tarafta yatır-çık kuralıyla ayrı ele alınır."""
    g13 = yontem.deger("kisa_gunduz.gun_1_3_dis_ic_dk")
    if gun_no <= 3:
        k = g13[f"gun_{max(1, int(gun_no))}"]
        return int(k["dis"]), int(k["ic"])
    p = yontem.deger("kisa_gunduz.gun_4_12_dis_ic_dk")
    dis = min(int(p["dis_ust_sinir_dk"]),
              int(g13["gun_3"]["dis"]) + int(p["gunluk_dis_artis_dk"]) * (int(gun_no) - 3))
    return dis, int(p["toplam_dk"]) - dis


# Aşamaya göre "içeride ne yapılır" — sayılar tablodan; kapı aşamaları
# gece_uyanma.icerde_temas_kapi (v0.4) ile.
_ICERIDE = {
    "besik_yani": "beşik yanında pat-pat, pış-pış ile {dk} dakika uyutmayı deneyin; "
                  "histerik ağlama yoksa kucağa almayın",
    "oda_ortasi": "oda ortasından temassız pış-pış ya da ninniyle {dk} dakika deneyin; "
                  "çok yoğun ağlamada yanına gidip temas edin, sonra yerinize dönün",
    "kapi": "sesle sakinleştirip çıkın; yalnız histerik (nefes nefese) ağlamada temas edin; "
            "böyle {dk} dakika deneyin",
    "kapi_esigi": "sesle sakinleştirip çıkın; yalnız histerik (nefes nefese) ağlamada temas edin; "
                  "böyle {dk} dakika deneyin",
    "yatir_cik": "kısa süre sakinleştirip çıkın; böyle {dk} dakika devam edin",
}


def gece_adimlari(asama_kod: str | None, gece_no: int | None = None) -> list[dict]:
    """İlayda'nın gece akışı (gece_uyanma.akis, protokol_karti, seans.uc_tekrar_sonrasi).
    Dış bekleme o gecenin sayısıyla; son adım destek gecesi / diğer geceler ayrı."""
    akis = yontem.deger("gece_uyanma.akis")
    deneme, rutin, tekrar = akis["3_deneme_dk"], akis["4_rutin_dk"], akis["6_tekrar_en_fazla"]
    icerde = _ICERIDE.get(asama_kod or "", "bu aşamanın yöntemiyle {dk} dakika uyutmayı deneyin")
    sonrasi = yontem.deger("seans.uc_tekrar_sonrasi")
    gece_sonrasi = sonrasi["gece"]
    if gece_no:
        ilk = gece_ilk_bekleme_dk(gece_no)
        dis = (f"Önce odaya girmeden dışarıda {ilk} dakika bekleyin; aynı gece yeniden uyanırsa "
               f"her seferinde {gece_ici_artis_dk()} dakika daha uzun bekleyin.")
    else:
        dis = "Önce odaya girmeden dışarıda bekleyin; bu bekleme süresi için planınıza bakın."
    adimlar = [
        {"metin": dis},
        {"metin": f"Sonra içeri girin, {icerde.format(dk=deneme)}."},
        {"metin": f"Uyumazsa odada {rutin} dakikalık kısa bir rutin yapın; rutinde beslenme olmasın."},
    ]
    if destek_gecesi_mi(gece_no):
        adimlar += [
            {"metin": f"Sonra yeniden {deneme} dakika deneyin; bunu en fazla {tekrar} kez yapın."},
            # Tablodaki sonuç aynen ("o gün başarısız sayılır").
            {"metin": "Yine uyumazsa mecburen destek vererek uyutun; "
                      f"{gece_sonrasi.split(';', 1)[1].strip()}."
                      if "destek" in gece_sonrasi and ";" in gece_sonrasi else gece_sonrasi},
        ]
    else:
        adimlar += [
            {"metin": f"Sonra yeniden {deneme} dakika deneyin."},
            {"metin": f"Uyumazsa {rutin} dakika rutin ve {deneme} dakika deneme döngüsünü bebek uyuyana "
                      f"kadar sürdürün; bu gece destek vererek uyutma yok."},
        ]
    return adimlar


def gece_karti(protokol: dict, asama_: dict | None) -> dict:
    """Saklanan karta baslik + adimlar ekle; diğer alanlar AYNEN kalır."""
    kart = dict(protokol)
    kart["baslik"] = "GECE UYANIRSA" + (f" · {asama_['ad']}" if asama_ else "")
    kart["adimlar"] = gece_adimlari((asama_ or {}).get("kod"), (asama_ or {}).get("egitim_gunu"))
    return kart


# =============================================================================
# Mevcut planlar: kısa gündüz cümlesi (okuma anında; YONTEM_TABLOSU)
# =============================================================================
# 92d5c97 ile 13 günlük plan istemine giren "dış 1 → 1,5 → 2 dk" kuralı, saklı
# planlarda iki biçimde duruyor: zincir ("1 dakika → 1,5 dakika → 2 dakika") ya
# da aşama başına tek değer ("Dışarıda **1,5 dakika** bekleyin"). Yalnız kısa
# gündüz paragrafındaki dış bekleme ifadesi değişir; cümlenin geri kalanı kalır.
_SURE = r"(?:\*\*)?(?:1|1[,.]5|2)\s*(?:dk|dakika)(?:\*\*)?"
# Zincir: "(1 dakika → 1,5 dakika → 2 dakika)", "**1 dakika → 1,5 dakika → 2 dakika.**"
# (öğeler ayrı ayrı kalın da olabilir: "**1 dakika** → **1,5 dakika** → **2 dakika**")
_ZINCIR_YANLIS = re.compile(r"(\()?(?:\*\*)?1\s*(?:dk|dakika)?(?:\*\*)?\s*(?:→|->)\s*(?:\*\*)?1[,.]5\s*"
                            r"(?:dk|dakika)?(?:\*\*)?\s*(?:→|->)\s*(?:\*\*)?2\s*(?:dk|dakika)(?(1)\)|)(\.)?(?:\*\*)?",
                            re.IGNORECASE)
# Bölünmüş zincirin kuyruğu: "1 dakika bekleyin → 1,5 dakika → 2 dakika (şeklinde kademeli artırın)",
# "; ardından **1,5 dakika**, **2 dakika** diye artırın"
_ZINCIR_KUYRUK = re.compile(r"(?:\s*;\s*ardından|\s*(?:→|->|,))\s*(?:\*\*)?1[,.]5\s*(?:dk|dakika)(?:\*\*)?\s*"
                            r"(?:→|->|,)\s*(?:\*\*)?2\s*(?:dk|dakika)(?:\*\*)?"
                            r"(?:\s+(?:şeklinde|diye)(?:\s+kademeli)?\s+artırın)?", re.IGNORECASE)
# Tek değer: "**1,5 dakika**" — zincirin parçası değil
_TEK_YANLIS = re.compile(rf"(?<![→>\d,.])(?<![→>]\s){_SURE}(?!\s*(?:→|->))", re.IGNORECASE)
_KG_BASLIK = re.compile(r"kısa gündüz uykusu olursa|kısa gündüz uykusu uzatma", re.IGNORECASE)
_KG_BITIS = re.compile(r"yoğun direnç olursa|\n#{1,4} |\n\*\*[^*\n]+:\*\*", re.IGNORECASE)
_DIS_BAGLAMI = re.compile(r"dışar|dışında|kapıya çık|odadan çık|kapı önünde|kapının önünde|kapıda|"
                          r"kapıyı kapalı|kapıyı kapat|kademe",
                          re.IGNORECASE)
# Zincirin ayrı cümleye taşmış kuyruğu ("Hâlâ ağlıyorsa 1,5 dakika, ardından 2 dakika bekleyin.",
# "İlk bekleme 1 dakika, sonraki 1,5 dakika, devamı 2 dakika olacak şekilde artırın.")
_KUYRUK_CUMLE = re.compile(r"(?<![\d,.])1[,.]5\s*(?:dk|dakika)\b.{0,60}?(?<![\d,.])2\s*(?:dk|dakika)\b",
                           re.IGNORECASE | re.DOTALL)
_KUYRUK_YERINE = "Dışarıdaki süre dolunca içeri girin."
_LISTE_ONEKI = re.compile(r"^(\s*(?:\d+\.|[-*•])\s+|\s*)")
_KUCAK_BAGLAMI = re.compile(r"kucağ|kucak|saniye|\b30\s*s", re.IGNORECASE)
_CUMLE = re.compile(r"[^.!?\n]*(?:[.!?](?!\d)|\n|$)")


def _kg_dogru_ifade(bas: int | None, bit: int | None) -> str:
    """Aşamanın gün aralığı için doğru dış/iç ifadesi (cümlenin içine oturur)."""
    if bas is None:
        d1, i1 = kisa_gunduz_dis_ic(1)
        p = yontem.deger("kisa_gunduz.gun_4_12_dis_ic_dk")
        return (f"**{d1} dakika** (1. gün; 2. gün {kisa_gunduz_dis_ic(2)[0]}, 3. gün {kisa_gunduz_dis_ic(3)[0]}; "
                f"4. günden itibaren her gün {p['gunluk_dis_artis_dk']} dakika artar, kalan süre içeride, "
                f"toplam {p['toplam_dk']} dakika)")
    if bas >= 13:
        yc = yontem.deger("kisa_gunduz.yatir_cik_ve_sonrasi_dis_ic_dk")
        return (f"**{yc['dis']} dakika** (sonra içeri girip 'uyku bitmedi' deyip çıkın, "
                f"{yc['ic']} dakika daha dışarıda)")
    gunler = list(range(bas, min(bit or bas, 12) + 1))
    toplam_dk = yontem.deger("kisa_gunduz.gun_4_12_dis_ic_dk")["toplam_dk"]
    if all(kisa_gunduz_dis_ic(gn)[1] == 0 for gn in gunler):
        aralik = f"{gunler[0]}–{gunler[-1]}. gün" if len(gunler) > 1 else f"{gunler[0]}. gün"
        return f"**{toplam_dk} dakika** ({aralik}: {toplam_dk} dakikanın hepsi dışarıda, içeride deneme yok)"
    parca = []
    for i, gn in enumerate(gunler):
        dis, ic = kisa_gunduz_dis_ic(gn)
        parca.append(f"{gn}. gün {dis}" + (f"+{ic}" if ic else " (hepsi dışarıda)"))
    d0, i0 = kisa_gunduz_dis_ic(gunler[0])
    return (f"**{d0} dakika** (" + ", ".join(parca) + "; dış + iç toplam "
            f"{yontem.deger('kisa_gunduz.gun_4_12_dis_ic_dk')['toplam_dk']} dakika)")


# Değişen ifade "hepsi dışarıda" (ya da 13. gün yatır-çık) diyor ama cümlenin geri
# kalanı içeri girip devam etmeyi söylüyorsa cümle kendi içinde çelişir → tamamı
# tablodan kurulan cümleyle değişir.
_ICERI_DEVAM = re.compile(r"içeri\s+gir|devam\s+ed|konumuna\s+geç|eşiğine\s+geç|pozisyonunu\s+al",
                          re.IGNORECASE)
# Aynı çelişki çoğu planda bir SONRAKİ cümlede: "… bekleyin. İçeri girince kapı eşiği
# pozisyonundan devam edin." Hemen ardından gelen bu tür cümleler de değişir; süre
# dolunca bebeği almayı söyleyen (tabloyla uyumlu) cümleler kalır.
_TAKIP = re.compile(r"içeri\s+gir|odaya\s+gir|girdiğinizde|girince|devam\s+ed|geri\s+dön|"
                    r"konumuna\s+geç|eşiğine\s+geç|eşiğinize\s+geç|pozisyon\w*\s+(?:al|geç)|"
                    r"protokol\w*\s+geç", re.IGNORECASE)
_TAKIP_UYUMLU = re.compile(r"uyumazsa|uyumadıysa|bebeği\s+(?:al|kaldır)|sonlandır", re.IGNORECASE)


def _kg_tam_cumle(bas: int, bit: int | None) -> str:
    """Çelişen cümlenin yerine geçen tam cümle; yalnız tablodaki gündüz kuralı:
    gün 1-12 kisa_gunduz_dis_ic; süre dolunca kisa_gunduz.yatir_cik_ve_sonrasi_dis_ic_dk
    ('45 dk dışarıda bekleyip al' / 'uyumazsa al'); içerideki pozisyon merdiven.asamalar."""
    yc = yontem.deger("kisa_gunduz.yatir_cik_ve_sonrasi_dis_ic_dk")
    if bas >= 13:
        return (f"Dışarıda **{yc['dis']} dakika** bekleyin; sonra içeri girip 'uyku bitmedi' deyip "
                f"çıkın ve {yc['ic']} dakika daha dışarıda bekleyin; uyumazsa bebeği alın.")
    toplam = yontem.deger("kisa_gunduz.gun_4_12_dis_ic_dk")["toplam_dk"]
    gunler = list(range(bas, min(bit or bas, 12) + 1))
    hepsi = [g for g in gunler if kisa_gunduz_dis_ic(g)[1] == 0]
    icli = [g for g in gunler if kisa_gunduz_dis_ic(g)[1] > 0]

    def _aralik(gs: list[int]) -> str:
        return f"{gs[0]}–{gs[-1]}. gün" if len(gs) > 1 else f"{gs[0]}. gün"
    son = (f"{_aralik(hepsi)}: dışarıda **{toplam} dakika** bekleyin, içeride deneme yok; "
           f"{toplam} dakika dolunca uyumadıysa bebeği alın.")
    if not icli:
        return son
    asama_ = next((a for a in yontem.deger("merdiven.asamalar")
                   if a["gun"][0] <= icli[0] <= a["gun"][1]), None)
    yer = f" **{asama_['ad'].lower()}** pozisyonundan" if asama_ else ""
    parca = ", ".join(f"{g}. gün {kisa_gunduz_dis_ic(g)[0]}+{kisa_gunduz_dis_ic(g)[1]}" for g in icli)
    return (f"{_aralik(icli)}: dışarıda **{kisa_gunduz_dis_ic(icli[0])[0]} dakika** bekleyin, sonra "
            f"içeri girip kalan süreyi{yer} deneyin ({parca}; toplam {toplam} dakika). " + son)


def _kg_sonraki_adim(bas: int, bit: int | None) -> str:
    """İfadeden sonraki 'içeri girip devam edin' cümlesinin yerine: aynı tablo kuralının
    dış süre dolduktan sonraki adımı (dış süre ifadede zaten yazılı)."""
    if bas >= 13:
        return "Uyumazsa bebeği alın."
    toplam = yontem.deger("kisa_gunduz.gun_4_12_dis_ic_dk")["toplam_dk"]
    gunler = list(range(bas, min(bit or bas, 12) + 1))
    hepsi = [g for g in gunler if kisa_gunduz_dis_ic(g)[1] == 0]
    icli = [g for g in gunler if kisa_gunduz_dis_ic(g)[1] > 0]
    son = f"içeride deneme yok; {toplam} dakika dolunca uyumadıysa bebeği alın."
    if not icli:
        return "İ" + son[1:]        # str.upper("i") Türkçe "İ" vermez

    def _aralik(gs: list[int]) -> str:
        return f"{gs[0]}–{gs[-1]}. gün" if len(gs) > 1 else f"{gs[0]}. gün"
    asama_ = next((a for a in yontem.deger("merdiven.asamalar")
                   if a["gun"][0] <= icli[0] <= a["gun"][1]), None)
    yer = f" **{asama_['ad'].lower()}** pozisyonundan" if asama_ else ""
    return (f"{_aralik(icli)}: içeri girip kalan süreyi{yer} deneyin; "
            f"{_aralik(hepsi)}: " + son)


def _kg_tam_cumle_yerlestir(cumle: str, tam: str) -> str:
    """Liste öneki ve satır sonu korunur; cümle dışarıdan açılmış/kapanacak bir kalın
    içindeyse (tek sayıda '**') yeni cümle kalın işaretsiz girer, dengesi bozulmaz."""
    onek = _LISTE_ONEKI.match(cumle).group(0)
    # Paragrafın ilk cümlesi başlığın kuyruğuyla başlar (":** ") → o da korunur
    baslik = re.match(r":\*\*\s*|\*\*:\s*|:\s*", cumle[len(onek):])
    if baslik:
        onek += baslik.group(0)
    govde = cumle[len(onek):].rstrip("\n")
    if govde.count("**") % 2:
        tam = tam.replace("**", "")
        tam = "**" + tam if govde.lstrip().startswith("**") else tam + "**"
    return onek + tam + ("\n" if cumle.endswith("\n") else "")


def kisa_gunduz_paragraf_duzelt(metin: str, bas: int | None, bit: int | None) -> tuple[str, list[tuple[str, str]]]:
    """Metindeki kısa gündüz paragraflarında yanlış dış bekleme ifadesini değiştir.
    Dönen: (yeni metin, [(önceki cümle, sonraki cümle)])."""
    degisen: list[tuple[str, str]] = []
    out, konum = [], 0
    for m in _KG_BASLIK.finditer(metin):
        if m.start() < konum:
            continue
        son = _KG_BITIS.search(metin, m.end())
        bitis = son.start() if son else len(metin)
        # "**… → 2 dakika.**" → "**… → 2 dakika**." (cümle noktada bölünür; kapanış ayrı kalmasın)
        paragraf = re.sub(r"(1[,.]5\s*(?:dk|dakika)?\s*(?:→|->)\s*2\s*(?:dk|dakika))\.\*\*", r"\1**.",
                          metin[m.end():bitis])
        yeni_par = []
        takip = None        # "ifade" | "tam": önceki cümle hepsi-dışarıda/13. gün ifadesiyle değişti
        for cm in _CUMLE.finditer(paragraf):
            cumle = cm.group(0)
            if not cumle:
                continue
            if not cumle.strip() or re.fullmatch(r"\s*\d+\.\s*", cumle):
                yeni_par.append(cumle)      # boş satır / tek başına liste numarası ("2.")
                continue
            if (takip and _TAKIP.search(cumle) and not _TAKIP_UYUMLU.search(cumle)
                    and not _KUCAK_BAGLAMI.search(cumle)):
                onek = _LISTE_ONEKI.match(cumle).group(0)
                # Tam cümleden sonra (ya da ikinci takip cümlesi) adım zaten yazılı → cümle
                # boşalır; liste maddesi boş kalmasın diye maddede adım yinelenir.
                if takip == "ifade" or onek.strip():
                    yeni = _kg_tam_cumle_yerlestir(cumle, _kg_sonraki_adim(bas, bit))
                else:
                    yeni = "\n" if cumle.endswith("\n") else ""
                degisen.append((cumle.strip(), yeni.strip() or "(cümle kalkar; adım önceki cümlede)"))
                yeni_par.append(yeni)
                takip = "tam"
                continue
            takip = None
            yeni, n = cumle, 0
            ifade = _kg_dogru_ifade(bas, bit)
            if not _KUCAK_BAGLAMI.search(cumle):
                # Tam zincir kısa gündüz paragrafında yeterince belirgin (dış sözcüğü gerekmez)
                yeni, n = _ZINCIR_YANLIS.subn(lambda z: ifade + (z.group(2) or ""), cumle, count=1)
            if not n and _DIS_BAGLAMI.search(cumle) and not _KUCAK_BAGLAMI.search(cumle):
                def _tek(z, c_=cumle, i_=ifade):
                    # "**1 dakika bekleyin.**": kalın değerden önce açılıp cümle sonunda
                    # kapanıyor → ifade kendi kalın işaretleri olmadan girer
                    if not z.group(0).startswith("**") and c_[max(0, z.start() - 2):z.start()] == "**":
                        return i_.replace("**", "")
                    if z.group(0).startswith("**") and not z.group(0).endswith("**"):
                        return "**" + i_.replace("**", "")      # kalın açılışı kalır
                    return i_
                yeni, n = _TEK_YANLIS.subn(_tek, cumle, count=1)
                if n:
                    yeni = _ZINCIR_KUYRUK.sub("", yeni, count=1)
            if n and bas is not None and ("hepsi dışarıda" in ifade or bas >= 13):
                kalan = yeni.replace(ifade, "").replace(ifade.replace("**", ""), "")
                if _ICERI_DEVAM.search(kalan):
                    yeni = _kg_tam_cumle_yerlestir(cumle, _kg_tam_cumle(bas, bit))
                    takip = "tam"
                else:
                    takip = "ifade"
            if not n and _KUYRUK_CUMLE.search(cumle) and not _KUCAK_BAGLAMI.search(cumle):
                # Ayrı cümleye taşmış yanlış kademe → tabloyla uyumlu nötr cümle
                onek = _LISTE_ONEKI.match(cumle).group(0)
                son = "\n" if cumle.endswith("\n") else ""
                yeni, n = onek + _KUYRUK_YERINE + son, 1
            if n:
                yeni = re.sub(r"[ \t]{2,}", " ", yeni)
                if yeni != cumle:
                    degisen.append((cumle.strip(), yeni.strip()))
            yeni_par.append(yeni)
        out.append(metin[konum:m.end()] + "".join(yeni_par))
        konum = bitis
    out.append(metin[konum:])
    return "".join(out), degisen


def kisa_gunduz_plan_duzelt(content: dict, baby: Any) -> tuple[dict, list[tuple[str, str]]]:
    """13 günlük planda, eğitimi süren bebek için: content['days'][].markdown ve
    content['markdown'] içindeki yanlış kısa gündüz ifadesi okuma anında düzelir.
    Saklanan plan DEĞİŞMEZ."""
    if (not content or (content.get("plan_secimi") or {}).get("gunler") != 13 or baby is None
            or getattr(baby, "training_started_at", None) is None
            or getattr(baby, "training_completed_at", None) is not None):
        return content, []
    c = dict(content)
    md = c.get("markdown") or ""
    tum: list[tuple[str, str]] = []
    gunler = []
    for d_ in c.get("days") or []:
        eski = d_.get("markdown") or ""
        yeni, deg = kisa_gunduz_paragraf_duzelt(eski, d_.get("start"), d_.get("end"))
        if deg:
            tum += deg
            if eski and eski in md:
                md = md.replace(eski, yeni, 1)
            d_ = dict(d_, markdown=yeni)
        gunler.append(d_)
    if gunler:
        c["days"] = gunler
    # Gün bloklarının DIŞINDAKİ genel satır ("Kısa gündüz uykusu uzatma: …"); gün
    # blokları (düzeltilmiş hâlleriyle) bu geçişe girmez — iki kez işlenmesin.
    bloklar = sorted((md.find(d_["markdown"]), len(d_["markdown"])) for d_ in gunler
                     if d_.get("markdown") and md.find(d_["markdown"]) >= 0)
    parcalar, konum = [], 0
    for bas_, uz in bloklar:
        if bas_ < konum:
            continue
        dis_, deg2 = kisa_gunduz_paragraf_duzelt(md[konum:bas_], None, None)
        tum += deg2
        parcalar += [dis_, md[bas_:bas_ + uz]]
        konum = bas_ + uz
    son_, deg2 = kisa_gunduz_paragraf_duzelt(md[konum:], None, None)
    tum += deg2
    parcalar.append(son_)
    if tum and "markdown" in c:
        c["markdown"] = "".join(parcalar)
    return c, tum


def plan_icerigi_isle(content: dict | None, user: Any, baby: Any, gun: date,
                      gece_gunu: int | None = None) -> dict | None:
    """_plan_yaniti'nda okuma anında çağrılır (bayrak açıkken). Önce kısa gündüz
    cümlesi düzelir; sonra kart (yoksa içerik o hâliyle döner). Build < 28 /
    bilinmiyor → kart çıkarılır.

    `gece_gunu`: gecesi sayılan eğitim günü (sor_baglam.gece_sayilan_gun — Sor ile
    aynı hesap; 02:30 → önceki akşamın gecesi). Verilmezse takvim günü `gun`."""
    if content:
        content, _ = kisa_gunduz_plan_duzelt(content, baby)
    if not content or "night_wake_protocol" not in content:
        return content
    from api.routers.app_config import istemci_build
    c = dict(content)
    build = istemci_build(user, None)
    if build is None or build < GECE_KARTI_MIN_BUILD:
        c.pop("night_wake_protocol", None)
        return c
    if not c.get("night_wake_protocol"):
        return c
    if gece_gunu is not None:
        a = asama_gunden(gece_gunu)
    else:
        a = asama(getattr(baby, "training_started_at", None),
                  getattr(baby, "training_completed_at", None), gun) if baby is not None else None
    c["night_wake_protocol"] = gece_karti(c["night_wake_protocol"], a)
    return c


# =============================================================================
# Bildirim
# =============================================================================
def once_dk(sinif: str) -> int:
    """Uyku öncesi bildirimin kaç dakika önce gideceği: gece rutin 15 (İlayda),
    gündüz 30 — tablodan."""
    if sinif == "gece":
        return int(yontem.deger("bildirim.gece_rutin_once_dk"))
    return int(yontem.deger("bildirim.uyku_once_dk"))


def once_govde(sinif: str) -> str:
    """Uyku öncesi bildirim gövdesi — tablo şablonu, süre yerleştirilmiş.
    {ad} yer tutucusu çağıranda doldurulur."""
    sab = yontem.deger("bildirim.metin_sablonlari")
    if sinif == "gece":
        return sab["once_gece"].replace("{gece_rutin_once_dk}", str(once_dk("gece")))
    return sab["once_gunduz"].replace("{uyku_once_dk}", str(once_dk("gunduz")))
