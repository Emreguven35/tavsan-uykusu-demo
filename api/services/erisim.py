"""
Erişim kuralları (B4) — hangi özellik hangi kademede açık.

Kural TABLOSU api/config.py ERISIM_KURALLARI'ndadır; burası onu uygulayan
küçük yardımcılardır. Kademe services.premium.durum'dan gelir —
BETA_MODE açıkken herkes premium olduğu için hiçbir kilit ETKİN DEĞİLDİR
(BETA_HARIC_EMAILS'teki hesaplar hariç).

KADEME (2026-10-13): "ucretsiz" < "deneme" < "premium". Fonksiyonlar kademe
yerine bool da kabul eder (True → premium, False → ucretsiz): kademeyi henüz
bilmeyen çağıranlar (premium_karari) eskisi gibi çalışır.

Kilitli yanıtlarda iki alan döner:
  locked            — bu öğe/özellik bu kullanıcıya kapalı
  premium_required  — açmak için premium gerekiyor (mobil paywall gösterir)
"""
from __future__ import annotations

from typing import Any

from api.config import (DENEME_SON_GUN, ERISIM_KURALLARI, KADEME_SIRASI,
                        UCRETSIZ_NINNI, UCRETSIZ_VIDEO_KATEGORILERI)

UCRETSIZ, DENEME, PREMIUM = "ucretsiz", "deneme", "premium"


def kademe_of(kademe: str | bool) -> str:
    """bool (eski premium kararı) → kademe adı; kademe adı aynen döner."""
    if isinstance(kademe, bool):
        return PREMIUM if kademe else UCRETSIZ
    return kademe if kademe in KADEME_SIRASI else UCRETSIZ


def kilitli_mi(ozellik: str, kademe: str | bool) -> bool:
    """Özelliğin açıldığı en düşük kademe kullanıcınınkinden yüksekse True.
    Bilinmeyen özellik AÇIKTIR (yeni bir özellik yanlışlıkla kilitlenmesin; kural
    eklenince kapanır)."""
    gerekli = ERISIM_KURALLARI.get(ozellik, UCRETSIZ)
    return KADEME_SIRASI[kademe_of(kademe)] < KADEME_SIRASI.get(gerekli, 0)


def video_kilitli_mi(kategori: str, kademe: str | bool) -> bool:
    ozellik = ("video_baslarken" if kategori in UCRETSIZ_VIDEO_KATEGORILERI
               else "video_diger")
    return kilitli_mi(ozellik, kademe)


def ses_kilitli_mi(is_free: bool, kademe: str | bool) -> bool:
    return kilitli_mi("ses_ucretsiz" if is_free else "ses_diger", kademe)


def hikaye_kilitli_mi(icerik_id: str, kademe: str | bool) -> bool:
    """Anne Sesi içerikleri: UCRETSIZ_NINNI dışında hepsi premium."""
    ozellik = "ninni_ucretsiz" if icerik_id == UCRETSIZ_NINNI else "anne_sesi"
    return kilitli_mi(ozellik, kademe)


EGITIM_PLANI = "egitim_plani"
EGITIM_BEKLEME = "egitim_bekleme"
_KADEMEDEN = object()          # plan_icerigi_kilitle: izinli verilmedi → kademeden


def izinli_son_gun(kademe: str | bool, egitim_gunu: int | None) -> int | None:
    """Eğitim programında gösterilebilecek son gün. None → hepsi (premium).
    Deneme: min(bugünkü eğitim günü, DENEME_SON_GUN) — eğitim başlamadıysa 1.
    Ücretsiz: 0 (program metni hiç gitmez; çizelge ve kayıtlar kalır)."""
    k = kademe_of(kademe)
    if k == PREMIUM:
        return None
    if kilitli_mi("egitim_programi", k):
        return 0
    return min(max(1, int(egitim_gunu or 1)), DENEME_SON_GUN)


def _toplam_gun(icerik: dict[str, Any], days: list[dict]) -> int:
    secim = icerik.get("plan_secimi") or {}
    try:
        return int(secim.get("gunler") or 0) or max((int(d["end"]) for d in days), default=13)
    except (TypeError, ValueError):
        return max((int(d.get("end") or 0) for d in days), default=13) or 13


def plan_icerigi_kilitle(icerik: dict[str, Any], kademe: str | bool,
                         izinli: Any = _KADEMEDEN) -> tuple[dict[str, Any], list[str]]:
    """(içerik, kilitli özellikler). İçerik KOPYALANIR — ORM satırının içeriği
    asla değiştirilmez (yoksa kilit DB'ye yazılırdı).

    `izinli`: eğitim programında gösterilecek son gün (izinli_son_gun). Verilmezse
    kademeden: premium → hepsi, deneme → 1. gün, ücretsiz → 0.

    - Eğitim planı: yalnız `start <= izinli` olan gün bölümleri (days) kalır;
      `markdown` bu bölümlerden YENİDEN KURULUR (uzun metin kırpılmaz — kırpma
      gelecek günü sızdırabilirdi). `kilitli_gunler`: içeriği gönderilmeyen gün
      numaraları. Tipsiz eski planlar da eğitim planı sayılır (tip_turet).
    - Bekleme planı: 13 günlük önizleme (days + metindeki Eğitim Planı bölümü)
      premium değilse gönderilmez.
    - Gece kartı (night_wake_protocol): ücretsizde çıkarılır.
    Premium'da içerik AYNEN döner (ek alan yok)."""
    k = kademe_of(kademe)
    if izinli is _KADEMEDEN:
        izinli = izinli_son_gun(k, None)
    kilitli: list[str] = []
    kopya = dict(icerik)

    if kilitli_mi("gece_karti", k) and kopya.get("night_wake_protocol") is not None:
        kopya.pop("night_wake_protocol", None)
        kilitli.append("gece_karti")

    if izinli is not None:
        from api.services.plan_service import tip_turet
        tip = tip_turet(icerik)
        if tip == EGITIM_PLANI:
            days = list(icerik.get("days") or [])
            if not days and icerik.get("markdown"):
                from engine.plan_gunleri import days_from_content
                days = days_from_content(icerik)
            izinli_days = [d for d in days if int(d.get("start") or 0) <= izinli] if izinli else []
            kapsanan = {g for d in izinli_days
                        for g in range(int(d["start"]), int(d.get("end") or d["start"]) + 1)}
            kilitli_gunler = [g for g in range(1, _toplam_gun(icerik, days) + 1)
                              if g not in kapsanan]
            kopya["days"] = izinli_days
            kopya["markdown"] = "\n\n".join(d.get("markdown") or "" for d in izinli_days)
            kopya["kilitli_gunler"] = kilitli_gunler
            # Eski istemci bu bayrağı "programın tamamı kilitli" diye okur.
            kopya["egitim_programi_kilitli"] = not izinli_days
            if kilitli_gunler:
                kilitli.append("egitim_programi")
        elif tip == EGITIM_BEKLEME:
            from engine.plan_gunleri import egitim_bolumunu_cikar
            if icerik.get("days") or icerik.get("markdown"):
                kopya["days"] = []
                kopya["markdown"] = egitim_bolumunu_cikar(icerik.get("markdown") or "")
                kilitli.append("egitim_onizleme")

    if not kilitli:
        return icerik, []
    return kopya, kilitli
