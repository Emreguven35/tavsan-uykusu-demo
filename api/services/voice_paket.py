"""
Ses paketi tanımı — anne başına hangi içerikler üretilecek.

KARAR (2026-09-22, maliyet ölçümünden sonra): `starter` paketi 3 ninni + EN KISA
1 masal. Ölçüm — tam set (5 masal + 3 ninni) anne başına 24.233 karakter /
2,67 USD; ElevenLabs kotası 30.000 kredi/ay ise bu AYDA 2 ANNE demek. Starter
4.986 karakter / 0,55 USD → aynı kotada ayda ~12 anne. Masallar ninnilerden
12 kat pahalı; maliyetin tamamı masal sayısında.

Paket env ile seçilir (VOICE_PACKAGE=starter|full); içerik listesi KODDA durur
ki hangi annenin ne aldığı sürümlenebilsin.
"""
import logging
import os

from api.services import voice as voice_svc

logger = logging.getLogger("tavsan.voice.paket")

# Başlangıç paketi — 3 ninni + en kısa masal (Ayşecik ile Uyku Perisi, 3.870
# karakter). Sıra ÜRETİM sırasıdır: ninniler önce, çünkü kısa ve anne ilk
# dakikalarda bir şey dinleyebilsin.
STARTER_ICERIKLER = (
    "ninni_dandini",
    "ninni_ay_isigi",
    "ninni_uyu_yavrum",
    "masal_aysecik_uyku_perisi",
)

VARSAYILAN_PAKET = "starter"


def paket_adi() -> str:
    ad = (os.getenv("VOICE_PACKAGE") or VARSAYILAN_PAKET).strip().lower()
    return ad if ad in ("starter", "full") else VARSAYILAN_PAKET


def paket_icerikleri() -> list[dict]:
    """Bu kurulumda üretilecek içerikler (katalogdaki tam kayıtlar).

    `full`: katalogdaki her şey. `starter`: yukarıdaki dört kimlik.
    Katalogda BULUNMAYAN bir kimlik sessizce atlanmaz — uyarı loglanır, çünkü
    listeyle katalog ayrışırsa anne eksik paket alır ve kimse fark etmez."""
    cat = voice_svc.load_stories()
    tumu = list(cat.get("ninniler", [])) + list(cat.get("masallar", []))
    if paket_adi() == "full":
        return tumu

    indeks = {x["id"]: x for x in tumu}
    out = []
    for cid in STARTER_ICERIKLER:
        item = indeks.get(cid)
        if item is None:
            logger.warning("Paket içeriği katalogda YOK: %s — atlandı", cid)
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
