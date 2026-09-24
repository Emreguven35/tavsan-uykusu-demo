"""
Erişim kuralları (B4) — hangi özellik ücretsiz, hangisi premium.

Kural TABLOSU api/config.py ERISIM_KURALLARI'ndadır; burası onu uygulayan
küçük yardımcılardır. Premium kararı services.premium.durum'dan gelir —
BETA_MODE açıkken herkes premium olduğu için hiçbir kilit ETKİN DEĞİLDİR.

Kilitli yanıtlarda iki alan döner:
  locked            — bu öğe/özellik bu kullanıcıya kapalı
  premium_required  — açmak için premium gerekiyor (mobil paywall gösterir)
"""
from __future__ import annotations

from typing import Any

from api.config import (ERISIM_KURALLARI, UCRETSIZ_NINNI,
                        UCRETSIZ_VIDEO_KATEGORILERI)


def kilitli_mi(ozellik: str, premium: bool) -> bool:
    """Kural 'premium' ve kullanıcı premium değilse True. Bilinmeyen özellik
    AÇIKTIR (yeni bir özellik yanlışlıkla kilitlenmesin; kural eklenince kapanır)."""
    return ERISIM_KURALLARI.get(ozellik, "ucretsiz") == "premium" and not premium


def video_kilitli_mi(kategori: str, premium: bool) -> bool:
    ozellik = ("video_baslarken" if kategori in UCRETSIZ_VIDEO_KATEGORILERI
               else "video_diger")
    return kilitli_mi(ozellik, premium)


def ses_kilitli_mi(is_free: bool, premium: bool) -> bool:
    return kilitli_mi("ses_ucretsiz" if is_free else "ses_diger", premium)


def hikaye_kilitli_mi(icerik_id: str, premium: bool) -> bool:
    """Anne Sesi içerikleri: UCRETSIZ_NINNI dışında hepsi premium."""
    ozellik = "ninni_ucretsiz" if icerik_id == UCRETSIZ_NINNI else "anne_sesi"
    return kilitli_mi(ozellik, premium)


EGITIM_PLANI = "egitim_plani"


def plan_icerigi_kilitle(icerik: dict[str, Any], premium: bool
                         ) -> tuple[dict[str, Any], list[str]]:
    """(içerik, kilitli özellikler). Eğitim programı (13 günlük adaptif program:
    gün bölümleri + tam metin) premium değilse ÇIKARILIR; günlük çizelge
    (schedule, uyarılar) ücretsiz kalır.

    İçerik KOPYALANIR — ORM satırının içeriği asla değiştirilmez (yoksa kilit
    DB'ye yazılırdı)."""
    if icerik.get("type") != EGITIM_PLANI or not kilitli_mi("egitim_programi", premium):
        return icerik, []
    kopya = dict(icerik)
    kopya["days"] = []
    kopya["markdown"] = ""
    kopya["egitim_programi_kilitli"] = True
    return kopya, ["egitim_programi"]
