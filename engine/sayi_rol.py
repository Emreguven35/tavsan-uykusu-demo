"""
Metindeki süreleri ROLLERİYLE çıkarır — yöntem tablosu denetimi ve Sor sayı
doğrulaması bu tek modülü kullanır (2026-10-12; önce scripts/yontem_denetim.py
içindeydi, davranış aynen taşındı).

    satir_sayilari("45 dakika direnç → 15 dakika rutin molası")
        → [("seans", 45.0), ("mola", 15.0)]

Roller: kucak, mola, rutin, kestirme, basari, artis, yatir_cik, gece_uyanma,
dis, ic, seans, tekrar; rolü çıkarılamayan sayı "belirsiz". Değerler DAKİKA
(saniye → dk/60). Saat (09:30) ve birimsiz sayılar süre sayılmaz; saat birimli
süreler (uyanıklık penceresi) alınmaz.

Rol çıkarımı SEZGİSELDİR (Türkçe sözdizimine göre öbek → cümle → satır);
sınırları tests/test_yontem.py'deki gerçek cümlelerle kilitli.
"""
from __future__ import annotations

import re

_SAYI = r"\d+(?:[.,]\d+)?"
_BIRIM = r"(?:dakika\w*|dk\b|saniye\w*|sn\b|s\b)"
_OK = r"(?:→|->|–|—|-)"
# Zincir: "5 → 10 → 15 → 20 → max 25 dakika", "15-30 dakika", "30s → 1dk → 1.5dk"
_ZINCIR = re.compile(
    rf"({_SAYI})\s*({_BIRIM})?((?:\s*{_OK}\s*(?:max|maks\w*|en fazla)?\s*{_SAYI}\s*(?:{_BIRIM})?)+)",
    re.IGNORECASE)
_TEK = re.compile(rf"(?<![\d.,])({_SAYI})\s*({_BIRIM})", re.IGNORECASE)
_ARTI = re.compile(rf"(?<![\d.,])(\d+)\s*\+\s*(\d+)(?!\s*{_BIRIM})(?!\d)(?![.,]\d)", re.IGNORECASE)
_DIS_ARTI_IC = re.compile(rf"dış\w*\s*({_SAYI})\s*({_BIRIM})?\s*\+\s*iç\w*\s*({_SAYI})",
                          re.IGNORECASE)
_TEKRAR = re.compile(r"(?<![\d.,])(\d+)\s*(?:tekrar|kere|defa)\b", re.IGNORECASE)
_SAAT = re.compile(r"\b\d{1,2}[:.]\d{2}\b(?!\s*(?:dakika|dk|sn|saniye))")
_CUMLE = re.compile(r"(?<!\d)[.!?;](?!\d)|\n")
# Öbek sınırı: ok, artı, virgül, parantez, iki nokta, başka bir sayı.
_OBEK_SINIRI = re.compile(r"[→+,(){}\[\]:;=]|->|\d")

ROL_SOZCUKLERI: tuple[tuple[str, re.Pattern], ...] = tuple(
    (rol, re.compile(d, re.IGNORECASE)) for rol, d in (
        ("kucak", r"kucağ|kucak"),
        ("mola", r"mola"),
        # Rutin süresi tabloyla (seans.rutin_suresi_dk) karşılaştırılır; kestirme
        # (yas_bantlari'nın konusu) ve başarı ölçütü KAPSAM DIŞIDIR.
        ("rutin", r"rutin(?!\s*mola)"),
        ("kestirme", r"kestirme|şekerleme"),
        ("basari", r"düş(?:müş|meye|tü|mesi|er)\w*|başarı"),
        ("artis", r"artışı\b|aralıklarla"),
        ("yatir_cik", r"yat[ıi]r[\s-]*[çc][ıi]k"),
        ("gece_uyanma", r"uyanma|uyanış|uyandığında|uyanırsa|uyanınca"),
        ("dis", r"\bdış|dışar"),
        ("ic", r"\biç\b|\biç\s+bekle|içeri|içerde"),
        ("seans", r"kural|direnç(?!li)|dene|uğraş"),
    ))


def _dk(sayi: str, birim: str | None) -> float:
    v = float(sayi.replace(",", "."))
    b = (birim or "").lower()
    return v / 60 if b.startswith(("saniye", "sn")) or b == "s" else v


def _ilk_rol(metin: str, son_eslesme: bool = False) -> str | None:
    """Metindeki ilk (ya da son) rol sözcüğü."""
    bulunan = [(m.start(), rol) for rol, d in ROL_SOZCUKLERI for m in d.finditer(metin)]
    if not bulunan:
        return None
    return (max if son_eslesme else min)(bulunan)[1]


def _en_yakin_rol(metin: str, konum: int) -> str | None:
    en_iyi, uzaklik = None, None
    for rol, desen in ROL_SOZCUKLERI:
        for m in desen.finditer(metin):
            u = konum - m.end() if m.end() <= konum else m.start() - konum + 0.5
            if uzaklik is None or abs(u) < uzaklik:
                en_iyi, uzaklik = rol, abs(u)
    return en_iyi


# 45 dk için ek seans ipuçları ("toplam ~45 dk hedefi"); genel listede yoklar
# çünkü "1 dakika … hedeflenir" gibi kademe satırlarını seansa çekiyorlardı.
_SEANS = re.compile(r"kural|direnç(?!li)|dene|hedef|toplam", re.IGNORECASE)
# "Kısa gündüz uykusundan uyanırsa …" — uyanma sözcüğü burada GECE değildir.
_KISA_BAGLAM = re.compile(r"kısa\s+(?:gündüz|uyku)", re.IGNORECASE)
_BEKLE_FIILI = re.compile(r"\bbekle(?!me\b)\w*", re.IGNORECASE)
KAPSAM_DISI = ("kestirme", "basari")


def _rol(metin: str, bas: int, son: int, cumle: tuple[int, int],
         dk: float | None = None, yalniz_obek: bool = False) -> str | None:
    """Sayının rolü (Türkçe sözdizimine göre sırayla):
    1. sayıdan SONRAKİ öbek ("15 dakika rutin molası" → mola) — "bekle…"
       fiilinde kesilir: süre o fiile bağlıdır, sonrası yeni eylemdir
       ("2 dakika bekleyip içeri girin" → 'içeri' iç bekleme değil),
    2. sayıdan ÖNCEKİ öbek ("Dış bekleme kademesi: 1 dakika" → dış),
    3. öbekten rol çıkmayan 45 dk, satırda seans sözcüğü varsa seans,
    4. cümlede en yakın sözcük, 5. satırda en yakın sözcük.
    `yalniz_obek`: yalnız 1-2 (ok zincirinde öğenin kendi öbeği)."""
    ca, cb = cumle
    i = son
    while i < cb and not _OBEK_SINIRI.match(metin, i):
        i += 1
    ileri = metin[son:i]
    fiil = _BEKLE_FIILI.search(ileri)
    rol = _ilk_rol(ileri[:fiil.start()] if fiil else ileri)
    if rol:
        return rol
    j = bas
    while j > ca and not _OBEK_SINIRI.match(metin, j - 1):
        j -= 1
    rol = _ilk_rol(metin[j:bas], son_eslesme=True)
    if rol or yalniz_obek:
        return rol
    if dk == 45 and _SEANS.search(metin):
        return "seans"
    return _en_yakin_rol(metin[ca:cb], bas - ca) or _en_yakin_rol(metin, bas)


def satir_sayilari(satir: str) -> list[tuple[str, float]]:
    """Bir satırdaki süreler: [(rol, dakika)]. Rol bulunamazsa 'belirsiz'.
    Saat (09:30), gün numarası ve birimsiz sayılar alınmaz; saat birimli
    süreler (uyanıklık penceresi) bu denetimin konusu değildir. 'rutin' rolü
    (mola olmayan rutin süresi) tabloyla karşılaştırılmaz."""
    out: list[tuple[str, float]] = []
    metin = _SAAT.sub(lambda m: " " * len(m.group(0)), satir)
    dolu = [False] * len(metin)

    def isaretle(a, b):
        for i in range(a, b):
            dolu[i] = True

    sinirlar, bas = [], 0
    for m in _CUMLE.finditer(metin):
        sinirlar.append((bas, m.start()))
        bas = m.end()
    sinirlar.append((bas, len(metin)))

    def cumle(i):
        return next(((a, b) for a, b in sinirlar if a <= i <= b), (0, len(metin)))

    kisa = bool(_KISA_BAGLAM.search(metin))

    def duzelt(rol):
        return "dis" if kisa and rol == "gece_uyanma" else rol

    def ekle(bas_, son_, rol_zorla, dk):
        out.append((rol_zorla or duzelt(_rol(metin, bas_, son_, cumle(bas_), dk)) or "belirsiz", dk))

    for m in _DIS_ARTI_IC.finditer(metin):               # "dış 5dk + iç 40dk"
        ekle(m.start(1), m.end(1), "dis", _dk(m.group(1), m.group(2) or "dk"))
        ekle(m.start(3), m.end(3), "ic", _dk(m.group(3), "dk"))
        isaretle(m.start(), m.end())
    for m in _ARTI.finditer(metin):                      # "Gün 2: 10+35"
        if any(dolu[m.start():m.end()]):
            continue
        ekle(m.start(1), m.end(1), "dis", float(m.group(1)))
        ekle(m.start(2), m.end(2), "ic", float(m.group(2)))
        isaretle(m.start(), m.end())
    for m in _ZINCIR.finditer(metin):
        if any(dolu[m.start():m.end()]):
            continue
        # öğe: (baş, son, sayı, birim, önündeki ayraç)
        ogeler = [(m.start(1), m.end(2) if m.group(2) else m.end(1), m.group(1), m.group(2), None)]
        for e in re.finditer(rf"\s*({_OK})\s*(?:max|maks\w*|en fazla)?\s*({_SAYI})\s*({_BIRIM})?",
                             m.group(3), re.IGNORECASE):
            o = m.start(3)
            ogeler.append((o + e.start(2), o + (e.end(3) if e.group(3) else e.end(2)),
                           e.group(2), e.group(3), e.group(1)))
        birimler, son_b = [], None
        for oge in reversed(ogeler):                     # birimsiz öğe sağdakinin birimini alır
            son_b = oge[3] or son_b
            birimler.append(son_b)
        birimler.reverse()
        if birimler[0] is None:                          # "Gün 1-3", "0-3 ay": süre değil
            continue
        # Aralık ("15-30 dakika") tek öbektir: rolü aralığın SON öğesinden okunur.
        gruplar: list[list[int]] = []
        for i, oge in enumerate(ogeler):
            if i and oge[4] in ("-", "–", "—"):
                gruplar[-1].append(i)
            else:
                gruplar.append([i])
        # Ok zinciri: öğe önce KENDİ öbeğine bakar ("45 dk direnç → 15 dk rutin
        # molası"); bulamazsa zincirin rolünü alır ("Dış bekleme: 1 → 1,5 → 2 dk").
        roller = [_rol(metin, ogeler[g[0]][0], ogeler[g[-1]][1], cumle(ogeler[g[0]][0]),
                       yalniz_obek=True) for g in gruplar]
        zincir_rolu = next((r for r in roller if r), None) or _rol(
            metin, ogeler[0][0], ogeler[0][1], cumle(ogeler[0][0]),
            _dk(ogeler[0][2], birimler[0]))
        for g, rol in zip(gruplar, roller):
            for i in g:
                out.append((duzelt(rol or zincir_rolu) or "belirsiz", _dk(ogeler[i][2], birimler[i])))
        isaretle(m.start(), m.end())
    for m in _TEK.finditer(metin):
        if any(dolu[m.start():m.end()]):
            continue
        ekle(m.start(1), m.end(), None, _dk(m.group(1), m.group(2)))
        isaretle(m.start(), m.end())
    for m in _TEKRAR.finditer(metin):
        out.append(("tekrar", float(m.group(1))))
    return out
