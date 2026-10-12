"""
Yöntem tablosu denetimi — eğitim planlarındaki sayıları tabloyla karşılaştırır.

SALT OKUR: DB'ye yazmaz (okuma tek bir transaction'da, sonunda rollback). Plan
METNİ YAZDIRILMAZ; çıktı yalnız sayı, rol ve satır sayısıdır (KVKK: plan metni
bebeğin adını taşıyabilir).

Her eğitim planının (content.type == egitim_plani) markdown satırlarındaki
süreler rolleriyle çıkarılır — dış, iç, mola, seans, gece_uyanma, kucak,
yatir_cik, tekrar — ve data/yontem_tablosu.json ile karşılaştırılır:

    uyumlu      değer tablonun dolu alanıyla uyuşuyor
    uyumsuz     değer tablonun dolu alanıyla çelişiyor
    bekliyor    rolün değeri İlayda kararı bekleyen bir alana bağlı (karşılaştırılmaz)
    kapsam_disi tablonun konusu olmayan süre (kestirme, başarı ölçütü)
    belirsiz    sayının rolü satırdan çıkarılamadı

Satır durumu: herhangi bir sayı uyumsuzsa uyumsuz, değilse bekliyor, değilse
uyumlu; yalnız belirsiz sayı içeren satır belirsiz.

Rol çıkarımı SEZGİSELDİR: aynı cümlede sayıya en yakın anahtar sözcük (önce ya
da sonra) rolü belirler. Sonuçlar "tahmini" okunmalı.

Çalıştırma:
    python scripts/yontem_denetim.py                 # DATABASE_URL'deki DB (salt okur)
    python scripts/yontem_denetim.py --json plan.json  # [{content, bant?}, …] dosyası
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import yontem  # noqa: E402

# =============================================================================
# Sayı + rol çıkarımı
# =============================================================================
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


# =============================================================================
# Tabloyla karşılaştırma
# =============================================================================
def kurallar() -> dict:
    """Rol → karşılaştırma kuralı. Değerler YALNIZ dolu alanlardan okunur;
    bekleyen alana bağlı durumlar 'bekliyor' döner (karşılaştırılmaz)."""
    b = set(yontem.bekleyen_alanlar())
    g13 = yontem.deger("kisa_gunduz.gun_1_3_dis_ic_dk")
    yc_kd = yontem.deger("kisa_gunduz.yatir_cik_ve_sonrasi_dis_ic_dk")
    yc = (set(yontem.deger("yatir_cik.standart_dizi_dk"))
          | set(yontem.deger("yatir_cik.en_fazla_dk"))
          | {yontem.deger("yatir_cik.yirmi_bir_gun_kurali")["en_fazla_dk"]})
    rutin = yontem.deger("seans.rutin_suresi_dk")
    return {
        "kucak": {"izinli": {v / 60 for v in yontem.deger("kucak_araligi_sn.dizi")}},
        "mola": {"izinli": {yontem.deger("seans.mola_dk"), yontem.deger("seans.mola_en_fazla_dk")}},
        "seans": {"izinli": {yontem.deger("seans.deneme_dk"), yontem.deger("kisa_gunduz.seans_toplam_dk")}},
        "tekrar": {"izinli": {yontem.deger("seans.tekrar_en_fazla")}},
        "rutin": {"aralik": (rutin["en_az"], rutin["en_fazla"])},
        "yatir_cik": {"izinli": yc},
        "artis": {"en_az": yontem.deger("gece_uyanma.gunluk_artis_en_az_dk")},
        "gece_uyanma": {"taban": yontem.deger("gece_uyanma.taban_dk"),
                        "bekleyen": [y for y in ("gece_uyanma.ilk_gece_ilk_bekleme_dk",
                                                  "gece_uyanma.gece_ici_artis_dk") if y in b]},
        # Kısa gündüz dış/iç: gün 1-3 ve yatır-çık dolu (İlayda), gün 4-12 bekliyor.
        "dis": {"gun": {g: v["dis"] for g, v in ((1, g13["gun_1"]), (2, g13["gun_2"]), (3, g13["gun_3"]))},
                "yatir_cik": yc_kd["dis"],
                "bekleyen": "kisa_gunduz.gun_4_12_dis_ic_dk" in b},
        "ic": {"gun": {g: v["ic"] for g, v in ((1, g13["gun_1"]), (2, g13["gun_2"]), (3, g13["gun_3"]))},
               "yatir_cik": yc_kd["ic"],
               "bekleyen": "kisa_gunduz.gun_4_12_dis_ic_dk" in b},
    }


def sayi_durumu(rol: str, dk: float, k: dict, gun: tuple[int, int] | None = None) -> str:
    """`gun`: sayının geçtiği plan gün bölümü (başlangıç, bitiş); genel bölümde None."""
    if rol in KAPSAM_DISI:
        return "kapsam_disi"
    if rol == "belirsiz" or rol not in k:
        return "belirsiz"
    kural = k[rol]
    if rol == "artis":                             # artış miktarı: en az 30 sn
        return "uyumlu" if dk >= kural["en_az"] - 1e-9 else "uyumsuz"
    if rol == "rutin":
        a, b = kural["aralik"]
        return "uyumlu" if a - 1e-9 <= dk <= b + 1e-9 else "uyumsuz"
    if rol == "gece_uyanma":
        if dk < kural["taban"] - 1e-9:
            return "uyumsuz"                       # taban altı: dolu alanla çelişir
        return "bekliyor" if kural["bekleyen"] else "uyumlu"
    if rol in ("dis", "ic"):
        gunluk = set(kural["gun"].values()) | {kural["yatir_cik"]}
        if gun is not None and gun[1] <= 3:        # gün 1-3 bölümü: o günlerin değerleri
            izinli = {kural["gun"][g] for g in range(gun[0], gun[1] + 1) if g in kural["gun"]}
            return "uyumlu" if dk in izinli else "uyumsuz"
        if gun is not None and gun[0] >= 13:       # yatır-çık
            return "uyumlu" if dk == kural["yatir_cik"] else "uyumsuz"
        if dk in gunluk:
            return "uyumlu"
        return "bekliyor" if kural["bekleyen"] else "uyumsuz"
    return "uyumlu" if any(abs(dk - v) < 1e-6 for v in kural["izinli"]) else "uyumsuz"


def satir_durumu(durumlar: list[str]) -> str | None:
    if not durumlar:
        return None
    for d in ("uyumsuz", "bekliyor", "uyumlu", "kapsam_disi"):
        if d in durumlar:
            return d
    return "belirsiz"


def plan_denetle(content: dict, k: dict | None = None) -> dict:
    """Bir planın satır sayımları — METİN DÖNMEZ."""
    k = k or kurallar()
    content = content or {}
    gun_satirlari: dict[str, tuple[int, int]] = {}
    for d in content.get("days") or []:
        try:
            aralik = (int(d.get("start")), int(d.get("end") or d.get("start")))
        except (TypeError, ValueError):
            continue
        for s in (d.get("markdown") or "").splitlines():
            if s.strip():
                gun_satirlari.setdefault(s.strip(), aralik)
    sonuc = {"satir": Counter(), "bolum": defaultdict(Counter), "uyumsuz": Counter(),
             "bekliyor": Counter()}
    for satir in (content.get("markdown") or "").splitlines():
        s = satir.strip()
        if not s:
            continue
        bulunan = satir_sayilari(s)
        gun = gun_satirlari.get(s)
        durumlar = [(rol, dk, sayi_durumu(rol, dk, k, gun)) for rol, dk in bulunan]
        sd = satir_durumu([d for _, _, d in durumlar])
        if sd is None:
            continue
        bolum = "gun" if s in gun_satirlari else "genel"
        sonuc["satir"][sd] += 1
        sonuc["bolum"][bolum][sd] += 1
        for rol, dk, d in durumlar:
            if d == "uyumsuz":
                sonuc["uyumsuz"][f"{rol}:{dk:g}"] += 1
            elif d == "bekliyor":
                sonuc["bekliyor"][rol] += 1
    return sonuc


# =============================================================================
# Çalıştırma
# =============================================================================
def _planlari_db() -> list[dict]:
    from sqlalchemy import text
    from api.db.session import engine
    with engine.connect() as c:
        tx = c.begin()
        try:
            rows = c.execute(text("""
              select distinct on (p.baby_id) p.content, b.training_started_at
              from sleep_plans p join babies b on b.id = p.baby_id
              where b.training_started_at is not null
              order by p.baby_id, p.plan_date desc, p.created_at desc""")).all()
        finally:
            tx.rollback()                          # salt okur
    rows = sorted(rows, key=lambda r: str(r[1]))
    return [{"content": r[0] or {}} for r in rows]


def rapor(planlar: list[dict]) -> str:
    k = kurallar()
    satirlar = [f"yontem_tablosu surum {yontem.surum()} — bekleyen alanlar: "
                + ", ".join(yontem.bekleyen_alanlar()), "",
                "plan | tip | bant | uyumlu | uyumsuz | bekliyor | kapsam dışı | belirsiz | uyumsuz (rol:dk ×n)"]
    toplam = Counter(); rol_uyumsuz = Counter(); rol_bekliyor = Counter(); tip_disi = 0
    for i, p in enumerate(planlar, 1):
        ct = p.get("content") or {}
        if (ct.get("type") or "egitim_plani") != "egitim_plani":
            continue
        tip = (ct.get("plan_secimi") or {}).get("tip") or "?"
        bant = (ct.get("yas_bandi") or {}).get("id") or ct.get("bucket") or "?"
        s = plan_denetle(ct, k)
        if tip != yontem.tablo().get("plan_tipi"):
            tip_disi += 1
            satirlar.append(f"B{i:02d} | {tip} | {bant} | — tablo dışı plan tipi (karşılaştırılmadı) —")
            continue
        toplam.update(s["satir"]); rol_uyumsuz.update(s["uyumsuz"]); rol_bekliyor.update(s["bekliyor"])
        uy = ", ".join(f"{a}×{n}" for a, n in s["uyumsuz"].most_common())
        satirlar.append(f"B{i:02d} | {tip} | {bant} | {s['satir']['uyumlu']} | {s['satir']['uyumsuz']} | "
                        f"{s['satir']['bekliyor']} | {s['satir']['kapsam_disi']} | "
                        f"{s['satir']['belirsiz']} | {uy or '-'}")
    satirlar += ["", f"TOPLAM satır: uyumlu {toplam['uyumlu']} · uyumsuz {toplam['uyumsuz']} · "
                 f"İlayda bekliyor {toplam['bekliyor']} · kapsam dışı {toplam['kapsam_disi']} · "
                 f"belirsiz {toplam['belirsiz']} · "
                 f"tablo dışı plan {tip_disi}",
                 "Uyumsuz sayılar (rol:dk): " + (", ".join(f"{a}×{n}" for a, n in rol_uyumsuz.most_common()) or "-"),
                 "İlayda bekleyen sayılar (rol): " + (", ".join(f"{a}×{n}" for a, n in rol_bekliyor.most_common()) or "-")]
    return "\n".join(satirlar)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--json", help="[{content}, …] dosyası (DB yerine)")
    a = ap.parse_args(argv)
    if a.json:
        planlar = json.loads(Path(a.json).read_text(encoding="utf-8"))
    else:
        planlar = _planlari_db()
    print(rapor(planlar))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
