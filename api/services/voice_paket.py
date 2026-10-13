"""
Ses paketi tanımı — anne başına hangi içerikler üretilecek.

KARAR (2026-10-13): paket 5 ninni + 5 masal. Liste AYARDAN gelir:
    VOICE_PAKET_NINNILER  virgülle ayrılmış içerik kimlikleri (en fazla 5)
    VOICE_PAKET_MASALLAR  virgülle ayrılmış içerik kimlikleri (en fazla 5)
Ayar yoksa aşağıdaki varsayılanlar. Katalogda (data/stories.json) henüz
BULUNMAYAN kimlik üretilmez — o yer boş kalır (yeni ninni kayıtları gelince
kataloğa ve listeye eklenir; içerik uydurulmaz). Bugün katalogda 3 ninni var,
yani ninni tarafında 2 yer boş.

Eski `VOICE_PACKAGE=starter|full` seçimi KALKTI. Mevcut paket sahiplerinin
dosyaları (voice_audios) ve katalog kimlikleri değişmedi; dinledikleri içerik
aynen sunulur. Liste yalnız YENİ üretimleri belirler.

ÖNİZLEME (premium olmayan, deneme dahil): tek masalın en fazla
VOICE_ONIZLEME_SN saniyesi (varsayılan 30). Metnin yalnız baş kısmı
seslendirilir (maliyet), ses sunucuda bu sürede kesilir (services/mp3.kes);
STS yolunda referansın da yalnız bu kadarı dönüştürülür. Masal ayardan:
VOICE_ONIZLEME_ICERIK.
"""
import logging
import os
import re

from api.services import voice as voice_svc

logger = logging.getLogger("tavsan.voice.paket")

PAKET_NINNI_SAYISI = 5
PAKET_MASAL_SAYISI = 5
VARSAYILAN_NINNILER = (
    "ninni_dandini",
    "ninni_ay_isigi",
    "ninni_uyu_yavrum",
)
VARSAYILAN_MASALLAR = (
    "masal_aysecik_uyku_perisi",
    "masal_kelogan_sihirli_degnek",
    "masal_cirkin_ordek_yavrusu",
    "masal_uc_kucuk_domuzcuk",
    "masal_kirmizi_baslikli_kiz",
)

PAKET_ADI = "standart"

AMAC_PAKET = "paket"
AMAC_ONIZLEME = "onizleme"
ONIZLEME_SN = 30                      # varsayılan; ayar: VOICE_ONIZLEME_SN
ONIZLEME_SN_ARALIK = (5, 300)
VARSAYILAN_ONIZLEME = "masal_aysecik_uyku_perisi"
# Masal profili yavaş anlatım; 30 sn'yi rahat dolduracak kadar metin alınır,
# fazlası kesilir. Tam masal (~4.000 karakter) seslendirilmez. Süre ayarla
# değişirse metin de orantılı büyür (onizleme_karakter).
ONIZLEME_KARAKTER = 700


def _liste(env: str, varsayilan: tuple, ust: int) -> list[str]:
    ham = os.getenv(env)
    if ham is None or not ham.strip():
        idler = list(varsayilan)
    else:
        idler = [p.strip() for p in ham.split(",") if p.strip()]
    idler = list(dict.fromkeys(idler))
    if len(idler) > ust:
        logger.warning("%s %d içerik veriyor, ilk %d alındı", env, len(idler), ust)
    return idler[:ust]


def paket_listesi() -> dict[str, list[str]]:
    """Ayardaki paket: {"ninniler": [...], "masallar": [...]} (katalog süzülmeden)."""
    return {"ninniler": _liste("VOICE_PAKET_NINNILER", VARSAYILAN_NINNILER,
                               PAKET_NINNI_SAYISI),
            "masallar": _liste("VOICE_PAKET_MASALLAR", VARSAYILAN_MASALLAR,
                               PAKET_MASAL_SAYISI)}


def paket_adi() -> str:
    return PAKET_ADI


def paket_icerikleri() -> list[dict]:
    """Paket üretiminde seslendirilecek içerikler (katalogdaki tam kayıtlar).

    Sıra ÜRETİM sırasıdır: ninniler önce (kısa, anne ilk dakikalarda bir şey
    dinleyebilsin), sonra masallar. Katalogda BULUNMAYAN kimlik atlanır ve
    uyarı loglanır — yer boş kalır."""
    cat = voice_svc.load_stories()
    indeks = {x["id"]: x for x in
              list(cat.get("ninniler", [])) + list(cat.get("masallar", []))}
    liste = paket_listesi()
    out = []
    for cid in liste["ninniler"] + liste["masallar"]:
        item = indeks.get(cid)
        if item is None:
            logger.warning("Paket içeriği katalogda YOK: %s — yer boş kalır", cid)
            continue
        out.append(item)
    return out


def paket_boyutu() -> int:
    return len(paket_icerikleri())


def icerik_bul(content_id: str) -> dict | None:
    for x in paket_icerikleri():
        if x["id"] == content_id:
            return x
    return None


def onizleme_icerik_id() -> str:
    return (os.getenv("VOICE_ONIZLEME_ICERIK") or VARSAYILAN_ONIZLEME).strip()


def onizleme_sn() -> float:
    """Önizleme süresi (sn): VOICE_ONIZLEME_SN, yoksa 30. Geçersiz ya da aralık
    dışı değer uyarıyla varsayılana düşer. Her çağrıda env'den okunur."""
    ham = (os.getenv("VOICE_ONIZLEME_SN") or "").strip()
    if not ham:
        return float(ONIZLEME_SN)
    try:
        sn = float(ham.replace(",", "."))
    except ValueError:
        sn = -1
    if not ONIZLEME_SN_ARALIK[0] <= sn <= ONIZLEME_SN_ARALIK[1]:
        logger.warning("VOICE_ONIZLEME_SN=%r geçersiz (%s–%s sn) — %s sn kullanıldı",
                       ham, *ONIZLEME_SN_ARALIK, ONIZLEME_SN)
        return float(ONIZLEME_SN)
    return sn


def onizleme_karakter() -> int:
    return max(1, round(ONIZLEME_KARAKTER * onizleme_sn() / ONIZLEME_SN))


def onizleme_metni(metin: str, ust: int | None = None) -> str:
    """Metnin baş kısmı — cümle sınırında, en az bir cümle."""
    ust = onizleme_karakter() if ust is None else ust
    metin = (metin or "").strip()
    if len(metin) <= ust:
        return metin
    cumleler = re.split(r"(?<=[.!?…])\s+", metin)
    out = ""
    for c in cumleler:
        if out and len(out) + 1 + len(c) > ust:
            break
        out = f"{out} {c}".strip()
    return out or metin[:ust]


def onizleme_icerigi() -> dict | None:
    """Önizlemede seslendirilecek tek içerik (metni kısaltılmış kopya)."""
    item = voice_svc.find_story(onizleme_icerik_id())
    if item is None:
        logger.warning("Önizleme içeriği katalogda YOK: %s", onizleme_icerik_id())
        return None
    kopya = dict(item)
    kopya["text"] = onizleme_metni(item.get("text", ""))
    kopya["onizleme"] = True
    return kopya


def uretim_icerikleri(amac: str | None) -> list[dict]:
    """Profilin amacına göre üretilecek içerikler. Önizleme → tek kısa masal;
    paket ya da eski profiller (amac NULL) → paket."""
    if amac == AMAC_ONIZLEME:
        item = onizleme_icerigi()
        return [item] if item else []
    return paket_icerikleri()
