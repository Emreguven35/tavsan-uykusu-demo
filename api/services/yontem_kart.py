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
    for a in asamalar:
        if a["gun"][0] <= g <= a["gun"][1]:
            return dict(a)
    return dict(asamalar[-1] if g > asamalar[-1]["gun"][1] else asamalar[0])


# Aşamaya göre "içeride ne yapılır" — sayılar tablodan; kapı aşamaları için
# İlayda kararı bekleniyor (gece_uyanma.icerde_temas_kapi) → yöntem tarif edilmez.
_ICERIDE = {
    "besik_yani": "beşik yanında pat-pat, pış-pış ile {dk} dakika uyutmayı deneyin; "
                  "histerik ağlama yoksa kucağa almayın",
    "oda_ortasi": "oda ortasından temassız pış-pış ya da ninniyle {dk} dakika deneyin; "
                  "çok yoğun ağlamada yanına gidip temas edin, sonra yerinize dönün",
    "yatir_cik": "kısa süre sakinleştirip çıkın; böyle {dk} dakika devam edin",
}


def gece_adimlari(asama_kod: str | None) -> list[dict]:
    """İlayda'nın gece akışı (gece_uyanma.akis + seans.uc_tekrar_sonrasi)."""
    akis = yontem.deger("gece_uyanma.akis")
    deneme, rutin, tekrar = akis["3_deneme_dk"], akis["4_rutin_dk"], akis["6_tekrar_en_fazla"]
    icerde = _ICERIDE.get(asama_kod or "", "bu aşamanın yöntemiyle {dk} dakika uyutmayı deneyin")
    gece_sonrasi = yontem.deger("seans.uc_tekrar_sonrasi")["gece"]
    # İlk bekleme süresi ve gece içi artış İlayda kararı bekliyor → sayı yok.
    return [
        {"metin": "Önce odaya girmeden dışarıda bekleyin; bu bekleme süresi için planınıza bakın."},
        {"metin": f"Sonra içeri girin, {icerde.format(dk=deneme)}."},
        {"metin": f"Uyumazsa odada {rutin} dakikalık kısa bir rutin yapın; rutinde beslenme olmasın."},
        {"metin": f"Sonra yeniden {deneme} dakika deneyin; bunu en fazla {tekrar} kez yapın."},
        # Tablodaki sonuç aynen ("o gün başarısız sayılır"); tabloda olmayan bir
        # sonuç ("yarın devam edersiniz") eklenmez.
        {"metin": "Yine uyumazsa mecburen destek vererek uyutun; "
                  f"{gece_sonrasi.split(';', 1)[1].strip()}."
                  if "destek" in gece_sonrasi and ";" in gece_sonrasi else gece_sonrasi},
    ]


def gece_karti(protokol: dict, asama_: dict | None) -> dict:
    """Saklanan karta baslik + adimlar ekle; diğer alanlar AYNEN kalır."""
    kart = dict(protokol)
    kart["baslik"] = "GECE UYANIRSA" + (f" · {asama_['ad']}" if asama_ else "")
    kart["adimlar"] = gece_adimlari((asama_ or {}).get("kod"))
    return kart


def plan_icerigi_isle(content: dict | None, user: Any, baby: Any, gun: date) -> dict | None:
    """_plan_yaniti'nda okuma anında çağrılır (bayrak açıkken). Kart yoksa
    içerik aynen döner. Build < 28 / bilinmiyor → kart çıkarılır."""
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
