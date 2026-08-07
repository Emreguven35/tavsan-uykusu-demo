"""
Yaş bandı servisi (Faz Y) — İlayda yaş bandı tablosunun TEK erişim noktası.

data/yas_bantlari.json YAPILANDIRILMIŞ MOTOR VERİSİDİR (RAG metni değil).
Plan motoru (build_schedule, parametre_uret) ve chat retrieval bu modülden okur;
master_knowledge_base.json'ın tutarsız serbest metinlerini AYRIŞTIRMAZ.

Neden ayrı bir tablo: KB'de 12-13/14/15-17 ay bantlarında `uyaniklik_penceresi`,
`gunduz_uyku_total`, `gece_uyku` alanları HİÇ YOK; çizelge kurucusu bu yaşlarda
sessizce varsayılana (2-3 saat) düşüyordu — 15 aylık bir çocuk için yanlış.
Bu tablo 0-36 ay arasındaki HER ayı tam olarak bir banda eşler; ara yaş kalmaz.

Ana API:
    yas_bandi_getir(ay, tek_uyku=None) -> dict   # çözülmüş bant (tüm sayılar dolu)
    cizelge_parametreleri(bant, wake_minute)     # çizelge için n/pencere/uyku boyu
    kestirme_degerlendir(bant, gunduz_uyku_dk)   # evrensel 30dk kestirme kuralı
    tek_uykuya_gecis_degerlendir(...)            # 12-18 ay üç şart kontrolü
    ogle_uykusu_reddi_adimi(...)                 # 24-36 ay reddi protokolü
    bant_metinleri()                             # KB/korpus için metin formu
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TABLO_PATH = DATA_DIR / "yas_bantlari.json"

# Çizelge çözücüsünün yuvarlama adımı — "09:38" yerine "09:30" gibi saatler üretir.
YUVARLAMA_DK = 15
# Bir gündüz uykusu bu değerin altına indirilmez.
MIN_UYKU_DK = 30


class YasBandiHatasi(RuntimeError):
    """Tablo okunamadı veya yaş hiçbir banda düşmedi (olmamalı — kapsam 0-36+)."""


# =============================================================================
# Yükleme
# =============================================================================
@lru_cache(maxsize=1)
def _tablo() -> dict:
    with open(TABLO_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def tablo() -> dict:
    """Ham tablo (salt okunur kullanın)."""
    return _tablo()


def kestirme_protokolu() -> dict:
    """Evrensel kestirme kuralı — plan içeriğine `content.kestirme_protokolu`
    olarak gömülür. Her çağrıda YENİ sözlük döner (çağıran mutasyonu tabloyu
    bozmasın)."""
    return dict(_tablo()["evrensel_kurallar"]["kestirme_protokolu"])


def bant_idleri() -> list[str]:
    return [b["id"] for b in _tablo()["bantlar"]]


# =============================================================================
# Bant çözümü — 0-36 ay arasındaki HER ay bir banda düşer
# =============================================================================
def _ham_bant(ay: float) -> dict:
    """Yaşa karşılık gelen ham bant tanımı. Bantlar yarı açık: [ay_min, ay_max)."""
    bantlar = _tablo()["bantlar"]
    if ay < 0:
        ay = 0.0
    for b in bantlar:
        if b["ay_min"] <= ay < b["ay_max"]:
            return b
    # 36 ay ve üzeri → son bant (tabloda son_bant: true ile işaretli).
    son = bantlar[-1]
    if ay >= son["ay_min"]:
        return son
    raise YasBandiHatasi(f"{ay} ay hiçbir banda düşmedi (tablo bozuk)")


def _varyant_sec(b: dict, tek_uyku: bool | None) -> tuple[dict, str | None]:
    """Varyantlı bantta (12-18 ay) etkin varyantı seç.

    tek_uyku None ise tablodaki varsayılan kullanılır. İlayda kuralı: üç geçiş
    şartı birden sağlanmıyorsa çocuk HÂLÂ 2 uyku bandındadır → varsayılan
    'iki_uyku'dur."""
    varyantlar = b.get("varyantlar")
    if not varyantlar:
        return {}, None
    if tek_uyku is True:
        ad = "tek_uyku"
    elif tek_uyku is False:
        ad = "iki_uyku"
    else:
        ad = b.get("varsayilan_varyant") or next(iter(varyantlar))
    return dict(varyantlar.get(ad) or {}), ad


def _pencere_devral(b: dict) -> tuple[list[int], str | None]:
    """`uyaniklik_penceresi_devir` alanını çöz ('12-18_ay.tek_uyku' gibi).

    Tabloda verilmemiş pencereyi komşu bandın penceresinden devralır ve KAYNAĞI
    döndürür — uydurma değil, izlenebilir devralma."""
    yol = b.get("uyaniklik_penceresi_devir")
    if not yol:
        return list(DEFAULT_PENCERE), None
    bant_id, _, varyant = yol.partition(".")
    kaynak = next((x for x in _tablo()["bantlar"] if x["id"] == bant_id), None)
    if kaynak is None:
        return list(DEFAULT_PENCERE), None
    ww = kaynak.get("uyaniklik_penceresi_dk")
    if varyant:
        ww = ((kaynak.get("varyantlar") or {}).get(varyant) or {}).get(
            "uyaniklik_penceresi_dk", ww)
    return (list(ww) if ww else list(DEFAULT_PENCERE)), yol


# Tabloda pencere hiç çözülemezse (olmamalı) son çare.
DEFAULT_PENCERE = (120, 180)


def yas_bandi_getir(ay: float, tek_uyku: bool | None = None) -> dict:
    """`ay` aylık bebeğin ÇÖZÜLMÜŞ yaş bandı. Sayısal alanların hepsi doludur.

    Dönen sözlük:
      id, ad, ay_min, ay_max, varyant (12-18 ay için 'iki_uyku'|'tek_uyku'|None)
      uyaniklik_penceresi_dk : [alt, ust]
      uyaniklik_penceresi_kaynak : devralındıysa kaynak yolu, değilse None
      gunduz_uyku_sayisi     : [alt, ust]   (sabitse alt == ust)
      gunduz_uyku_sayisi_sabit : bool
      gunduz_uyku_toplam_dk  : [alt, ust|None]   (ust None → 'en az alt')
      gece_uykusu_dk         : [alt, ust]
      notlar                 : [str]
      kestirme_protokolu     : evrensel kural (her bantta aynı)
      tek_uykuya_gecis_sartlari / ogle_uykusu_reddi_protokolu : varsa

    tek_uyku: yalnız 12-18 ay bandında anlamlıdır (None → varsayılan 'iki_uyku').
    """
    b = _ham_bant(ay)
    varyant_veri, varyant_ad = _varyant_sec(b, tek_uyku)

    def al(alan: str, varsayilan: Any = None) -> Any:
        if alan in varyant_veri and varyant_veri[alan] is not None:
            return varyant_veri[alan]
        return b.get(alan, varsayilan)

    ww = al("uyaniklik_penceresi_dk")
    ww_kaynak = None
    if not ww:
        ww, ww_kaynak = _pencere_devral(b)

    cozulmus: dict[str, Any] = {
        "id": b["id"],
        "ad": varyant_veri.get("ad") or b["ad"],
        "ay_min": b["ay_min"],
        "ay_max": b["ay_max"],
        "varyant": varyant_ad,
        "uyaniklik_penceresi_dk": list(ww),
        "uyaniklik_penceresi_kaynak": ww_kaynak,
        "gunduz_uyku_sayisi": list(al("gunduz_uyku_sayisi") or [1, 1]),
        "gunduz_uyku_sayisi_sabit": bool(al("gunduz_uyku_sayisi_sabit", False)),
        "gunduz_uyku_toplam_dk": list(al("gunduz_uyku_toplam_dk") or [0, None]),
        "gece_uykusu_dk": list(al("gece_uykusu_dk") or [600, 660]),
        # 24 saatlik toplam uyku ihtiyacı (gündüz + gece) — "bebeğim yeterince
        # uyuyor mu?" ölçütü. Çizelge çözücüsünü de kısıtlar (bkz. cizelge_parametreleri).
        "toplam_gunluk_uyku_dk": list(al("toplam_gunluk_uyku_dk") or [0, None]),
        "notlar": list(b.get("notlar") or []),
        "kestirme_protokolu": kestirme_protokolu(),
    }
    if b.get("tek_uykuya_gecis_sartlari"):
        cozulmus["tek_uykuya_gecis_sartlari"] = b["tek_uykuya_gecis_sartlari"]
        cozulmus["varsayilan_varyant"] = b.get("varsayilan_varyant")
    if b.get("ogle_uykusu_reddi_protokolu"):
        cozulmus["ogle_uykusu_reddi_protokolu"] = b["ogle_uykusu_reddi_protokolu"]
    return cozulmus


def bant_mi(bant: Any) -> bool:
    """Verilen sözlük bu servisin ürettiği çözülmüş bant mı? (build_schedule
    hangi kaynağı kullanacağına buna bakarak karar verir.)"""
    return (isinstance(bant, dict) and "uyaniklik_penceresi_dk" in bant
            and "gece_uykusu_dk" in bant and "id" in bant)


# =============================================================================
# Çizelge parametreleri — 24 saatlik döngüyü tablodan KAPATARAK çöz
# =============================================================================
# Gün uzunluğu D = 1440 - gece_uykusu. Bir günde (n+1) uyanıklık penceresi ve
# n gündüz uykusu vardır:            (n+1) * pencere + gunduz_toplam = D
# Bant, pencere/uyku sayısı/gündüz toplamı için ARALIK verir; bu denklemi
# sağlayan (n, pencere) ikilisi aranır. Böylece çizelge tablonun TÜM sayılarıyla
# tutarlı olur ve gece yatışı kendiliğinden gece uykusu aralığına oturur.
#
# TOPLAM UYKU KİMLİĞİ (v1.1): toplam = gunduz_toplam + gece
#                                    = (D - (n+1)*pencere) + gece
#                                    = 1440 - (n+1) * pencere
# Yani 24 saatlik toplam uyku YALNIZCA (n+1)*pencere'ye bağlıdır ve tablodaki
# toplam_gunluk_uyku_dk doğrudan bir PENCERE KISITIDIR. Bu kısıt olmadan 9-12 ay
# bandı 13,5 saatlik bir çizelge üretiyordu — İlayda'nın tablosu 14 saat diyor.
def cizelge_parametreleri(bant: dict) -> dict:
    """Bir bant için çizelge kurucu parametreleri.

    Dönen: {uyku_sayisi, uyaniklik_penceresi_dk, uyku_suresi_dk,
            gunduz_toplam_dk, toplam_uyku_dk, gun_uzunlugu_dk, cozuldu, not}
    cozuldu False ise denklem bant aralıklarıyla kapanmamıştır; en yakın değerler
    kullanılır ve `not` alanı sebebi açıklar (sessiz sapma olmasın)."""
    gece_lo, gece_hi = bant["gece_uykusu_dk"]
    D = 1440 - (gece_lo + gece_hi) // 2                 # sabah uyanışı → gece yatışı
    ww_lo, ww_hi = bant["uyaniklik_penceresi_dk"]
    g_lo, g_hi = bant["gunduz_uyku_toplam_dk"]
    n_lo, n_hi = bant["gunduz_uyku_sayisi"]
    t_lo, t_hi = (bant.get("toplam_gunluk_uyku_dk") or [0, None])

    secim: tuple[int, int] | None = None
    for n in range(int(n_lo), int(n_hi) + 1):
        if n <= 0:
            continue
        # gunduz_toplam >= g_lo  →  pencere <= (D - g_lo) / (n+1)
        ust = (D - g_lo) / (n + 1)
        # gunduz_toplam <= g_hi  →  pencere >= (D - g_hi) / (n+1)   (g_hi None → sınırsız)
        alt = (D - g_hi) / (n + 1) if g_hi else float(ww_lo)
        lo, hi = max(float(ww_lo), alt), min(float(ww_hi), ust)
        # toplam uyku kısıtı: t_lo <= 1440 - (n+1)*pencere <= t_hi
        if t_lo:
            hi = min(hi, (1440 - t_lo) / (n + 1))
        if t_hi:
            lo = max(lo, (1440 - t_hi) / (n + 1))
        if lo <= hi:
            secim = (n, _yuvarla_araliga(lo, hi))
            break                                       # en AZ uyku sayısı tercih edilir

    if secim is not None:
        n, ww = secim
        aciklama = None
        cozuldu = True
    else:
        # Aralıklar kapanmadı: uyku sayısının alt sınırını ve pencerenin gün
        # uzunluğuna en yakın değerini kullan; sapmayı raporla.
        n = max(1, int(n_lo))
        ham = (D - g_lo) / (n + 1)
        ww = int(min(max(ham, ww_lo), ww_hi))
        cozuldu = False
        aciklama = (f"Bant aralıkları 24 saatlik döngüyü kapatmadı "
                    f"(gün {D} dk, {n} uyku); pencere {ww} dk'ya sabitlendi")

    gunduz_toplam = max(int(g_lo), D - (n + 1) * ww)
    uyku_suresi = max(MIN_UYKU_DK, gunduz_toplam // n) if n else 0
    return {
        "uyku_sayisi": n,
        "uyaniklik_penceresi_dk": int(ww),
        "uyku_suresi_dk": int(uyku_suresi),
        "gunduz_toplam_dk": int(uyku_suresi * n),
        # 24 saatlik toplam: gündüz + gece (çizelge kimliği, yukarıdaki nota bak).
        "toplam_uyku_dk": int(1440 - (n + 1) * ww),
        "gun_uzunlugu_dk": int(D),
        "cozuldu": cozuldu,
        "not": aciklama,
    }


def _yuvarla_araliga(lo: float, hi: float) -> int:
    """[lo, hi] aralığındaki en büyük 15'in katı; yoksa aralığın en büyük değeri.

    En BÜYÜK pencere tercih edilir: gündüz uyku toplamı bandın belirttiği
    MİNİMUMA yaklaşır (tablo çoğu bantta yalnız minimum verir)."""
    aday = math.floor(hi / YUVARLAMA_DK) * YUVARLAMA_DK
    if aday >= lo:
        return int(aday)
    aday = math.ceil(lo / YUVARLAMA_DK) * YUVARLAMA_DK
    if aday <= hi:
        return int(aday)
    return int(hi)


def yatma_araligi(bant: dict, wake_minute: int) -> tuple[int, int]:
    """Sabah uyanışına göre gece yatışının düşmesi gereken aralık (dakika).

    Gece uykusu süresinden TÜRETİLİR: yatış = ertesi sabah uyanışı - gece uykusu.
    Tabloda mutlak 'yatma vakti' YOKTUR; tek kaynak ilkesi gereği uydurulmaz."""
    gece_lo, gece_hi = bant["gece_uykusu_dk"]
    return (wake_minute + 1440 - gece_hi, wake_minute + 1440 - gece_lo)


# =============================================================================
# Evrensel kural — 30 dakikalık kestirme
# =============================================================================
def kestirme_degerlendir(bant: dict, gunduz_uyku_dk: float | None) -> dict:
    """Gündüz toplam uyku minimumu tutmadıysa ilave kestirme gerekir mi?

    Dönen: {gerekli, eksik_dk, min_gunduz_dk, gerceklesen_dk, sure_dk,
            gece_uykusuna_gecis_dk, aciklama}
    gunduz_uyku_dk None (kayıt yok) → gerekli False, `gerceklesen_dk` None."""
    proto = kestirme_protokolu()
    min_dk = int(bant["gunduz_uyku_toplam_dk"][0] or 0)
    sonuc = {
        "gerekli": False,
        "eksik_dk": 0,
        "min_gunduz_dk": min_dk,
        "gerceklesen_dk": None if gunduz_uyku_dk is None else int(gunduz_uyku_dk),
        "sure_dk": proto["sure_dk"],
        "gece_uykusuna_gecis_dk": proto["gece_uykusuna_gecis_dk"],
        "aciklama": proto["aciklama"],
    }
    if gunduz_uyku_dk is None or min_dk <= 0:
        return sonuc
    eksik = min_dk - int(gunduz_uyku_dk)
    if eksik > 0:
        sonuc["gerekli"] = True
        sonuc["eksik_dk"] = eksik
    return sonuc


# =============================================================================
# "Bebeğim yeterince uyuyor mu?" — 24 saatlik toplam uyku değerlendirmesi
# =============================================================================
def toplam_uyku_degerlendir(bant: dict, gunduz_uyku_dk: float | None,
                            gece_uyku_dk: float | None) -> dict:
    """Bebeğin 24 saatlik toplam uykusu yaş bandının ihtiyacını karşılıyor mu?

    Dönen: {yeterli, gerceklesen_dk, hedef_dk, eksik_dk, fazla_dk, durum}
    durum: 'yeterli' | 'az' | 'fazla' | 'veri_yok'
    Gündüz VEYA gece verisi eksikse değerlendirme YAPILMAZ (yarım veriden
    "yetersiz uyuyor" sonucu çıkarmak yanlış alarm üretir)."""
    hedef = bant.get("toplam_gunluk_uyku_dk") or [0, None]
    lo, hi = hedef[0], hedef[1] if hedef[1] is not None else hedef[0]
    sonuc = {
        "yeterli": None,
        "gerceklesen_dk": None,
        "hedef_dk": [lo, hi] if lo else None,
        "eksik_dk": 0,
        "fazla_dk": 0,
        "durum": "veri_yok",
    }
    if not lo or gunduz_uyku_dk is None or gece_uyku_dk is None:
        return sonuc

    toplam = int(gunduz_uyku_dk) + int(gece_uyku_dk)
    sonuc["gerceklesen_dk"] = toplam
    if toplam < lo:
        sonuc.update({"yeterli": False, "eksik_dk": lo - toplam, "durum": "az"})
    elif toplam > hi:
        sonuc.update({"yeterli": True, "fazla_dk": toplam - hi, "durum": "fazla"})
    else:
        sonuc.update({"yeterli": True, "durum": "yeterli"})
    return sonuc


# =============================================================================
# 12-18 ay — tek uykuya geçiş (ÜÇÜ BİRDEN)
# =============================================================================
def tek_uykuya_gecis_degerlendir(ogle_yatis_dk: int | None = None,
                                 tek_ogun_uyku_dk: int | None = None,
                                 uyaniklik_penceresi_dk: int | None = None) -> dict:
    """12-18 ay tek uykuya geçiş şartları. ÜÇÜ BİRDEN sağlanmalıdır.

      1) Öğlen uykusuna 12:00'den önce yatmamak  (ogle_yatis_dk >= 720)
      2) Tek öğünde en az 2 saat uyku            (tek_ogun_uyku_dk >= 120)
      3) Uyanık kalma penceresi 4-6 saat         (240 <= pencere <= 360)

    Ölçülmemiş (None) şart SAĞLANMAMIŞ sayılır — İlayda kuralı gereği şüphede
    çocuk 2 uyku bandında kalır.

    Dönen: {tek_uyku, sartlar: [{id, metin, saglandi, deger}], gerekce}"""
    bant = _ham_bant(12)
    tanim = bant["tek_uykuya_gecis_sartlari"]
    degerler = {
        "ogle_yatis_dk": ogle_yatis_dk,
        "tek_ogun_uyku_dk": tek_ogun_uyku_dk,
        "uyaniklik_penceresi_dk": uyaniklik_penceresi_dk,
    }

    sartlar = []
    for s in tanim["sartlar"]:
        deger = degerler.get(s["alan"])
        saglandi = deger is not None
        if saglandi and s.get("min_dk") is not None:
            saglandi = deger >= s["min_dk"]
        if saglandi and s.get("max_dk") is not None:
            saglandi = deger <= s["max_dk"]
        sartlar.append({"id": s["id"], "metin": s["metin"],
                        "saglandi": bool(saglandi), "deger": deger})

    hepsi = all(s["saglandi"] for s in sartlar)
    return {
        "tek_uyku": hepsi,
        "sartlar": sartlar,
        "gerekce": ("Üç şart da sağlanıyor — tek uykuya geçilebilir."
                    if hepsi else tanim["saglanmazsa"]),
    }


# =============================================================================
# 24-36 ay — öğlen uykusu reddi protokolü
# =============================================================================
def ogle_uykusu_reddi_adimi(red_sayisi: int) -> dict | None:
    """Kaçıncı redde hangi adım uygulanır? red_sayisi 1 → 07:00, 2 → 06:00,
    3+ → öğlen uykusunun kademeli kaldırılması. red_sayisi < 1 → None."""
    bant = _ham_bant(24)
    adimlar = bant["ogle_uykusu_reddi_protokolu"]["adimlar"]
    if red_sayisi < 1:
        return None
    return dict(adimlar[min(int(red_sayisi), len(adimlar)) - 1])


def ogle_uykusu_reddi_protokolu() -> dict:
    return dict(_ham_bant(24)["ogle_uykusu_reddi_protokolu"])


# =============================================================================
# Metin formu — KB korpusu ve chat bağlamı (SAYILARDAN türetilir, elle yazılmaz)
# =============================================================================
def _sure(dk: int) -> str:
    """420 → '7 saat', 80 → '1 saat 20 dakika', 45 → '45 dakika'."""
    s, d = divmod(int(dk), 60)
    if s and d:
        return f"{s} saat {d} dakika"
    if s:
        return f"{s} saat"
    return f"{d} dakika"


def _aralik(rng: list) -> str:
    lo, hi = rng[0], rng[1]
    if hi is None:
        return f"en az {_sure(lo)}"
    if lo == hi:
        return _sure(lo)
    return f"{_sure(lo)} - {_sure(hi)}"


def _sayi_aralik(rng: list, sabit: bool = False) -> str:
    lo, hi = rng[0], rng[1]
    temel = f"{lo}" if lo == hi else f"{lo}-{hi}"
    return f"{temel} uyku" + (" (SABİT)" if sabit and lo == hi else "")


def bant_ozet_satirlari(bant: dict) -> list[str]:
    """Çözülmüş bandın sayısal özetini satır satır ver (chat bağlamı için)."""
    ww_not = ""
    if bant.get("uyaniklik_penceresi_kaynak"):
        ww_not = f"  (komşu bandan devralındı: {bant['uyaniklik_penceresi_kaynak']})"
    satirlar = [
        f"- Uyanıklık penceresi: {_aralik(bant['uyaniklik_penceresi_dk'])}{ww_not}",
        f"- Gündüz uyku sayısı: {_sayi_aralik(bant['gunduz_uyku_sayisi'], bant['gunduz_uyku_sayisi_sabit'])}",
        f"- Gündüz toplam uyku: {_aralik(bant['gunduz_uyku_toplam_dk'])}",
        f"- Gece uykusu: {_aralik(bant['gece_uykusu_dk'])}",
    ]
    toplam = bant.get("toplam_gunluk_uyku_dk") or [0, None]
    if toplam[0]:
        satirlar.append(
            f"- 24 saatlik TOPLAM uyku ihtiyacı (gündüz + gece): {_aralik(toplam)}")
    satirlar += [f"- {n}" for n in bant.get("notlar", [])]
    return satirlar


def bant_metni(bant_tanimi: dict, varyant: str | None = None) -> tuple[str, str]:
    """Bir bant (veya varyantı) için (baslik, metin) — korpusa girecek metin formu."""
    ay = bant_tanimi["ay_min"]
    tek = None
    if varyant:
        tek = varyant == "tek_uyku"
    b = yas_bandi_getir(ay, tek_uyku=tek)
    baslik = f"{b['ad']} yaş bandı uyku tablosu"
    govde = "\n".join(bant_ozet_satirlari(b))
    return baslik, f"{baslik} ({b['ad']} bebekler için):\n{govde}"


def bant_metinleri() -> list[dict]:
    """KB korpusuna eklenecek metin birimleri (chunk_id, label, text).

    Sayılar tablodan DETERMİNİSTİK üretilir — metin ve motor asla ayrışmaz.
    Ayrıca evrensel kestirme kuralı, 12-18 ay geçiş şartları ve 24-36 ay öğlen
    uykusu reddi protokolü birer birim olarak eklenir."""
    birimler: list[dict] = []

    for b in _tablo()["bantlar"]:
        varyantlar = list((b.get("varyantlar") or {}).keys()) or [None]
        for v in varyantlar:
            baslik, metin = bant_metni(b, v)
            cid = f"yas_bandi:{b['id']}" + (f".{v}" if v else "")
            birimler.append({"chunk_id": cid, "label": baslik, "text": metin})

    proto = kestirme_protokolu()
    birimler.append({
        "chunk_id": "yas_bandi:evrensel.kestirme_protokolu",
        "label": "Kestirme uykusu kuralı (tüm yaşlarda)",
        "text": (
            "Kestirme uykusu kuralı (tüm yaş bantlarında geçerli): "
            f"{proto['aciklama']} Tetik: {proto['tetik']}. "
            f"Kestirme süresi {proto['sure_dk']} dakikadır ve süre dolunca bebek "
            f"uyandırılır. Bu kestirmeden uyandıktan {proto['gece_uykusuna_gecis_dk']} "
            "dakika (1 saat) sonra bile gece uykusuna geçilebilir."
        ),
    })

    gecis = _ham_bant(12)["tek_uykuya_gecis_sartlari"]
    birimler.append({
        "chunk_id": "yas_bandi:12-18_ay.tek_uykuya_gecis",
        "label": "12-18 ay tek uykuya geçiş şartları",
        "text": (
            "12-18 ay tek uykuya geçiş şartları — ÜÇÜ BİRDEN sağlanmalıdır: "
            + "; ".join(f"{i}) {s['metin']}" for i, s in enumerate(gecis["sartlar"], 1))
            + f". {gecis['saglanmazsa']}"
        ),
    })

    red = _ham_bant(24)["ogle_uykusu_reddi_protokolu"]
    birimler.append({
        "chunk_id": "yas_bandi:24-36_ay.ogle_uykusu_reddi",
        "label": "24-36 ay öğlen uykusu reddi protokolü",
        "text": (
            "24-36 ay öğlen uykusu reddi protokolü: " + red["aciklama"] + " "
            + " ".join(f"{a['sira']}) {a['metin']}" for a in red["adimlar"])
        ),
    })
    return birimler
