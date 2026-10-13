"""
Hesap silmede RevenueCat müşteri kaydının silinmesi (KVKK, 2026-10-13).

DELETE https://api.revenuecat.com/v1/subscribers/{app_user_id} — app_user_id
bizim users.id. RevenueCat'teki satın alma geçmişi ve müşteri öznitelikleri
gider. Mağaza aboneliğini İPTAL ETMEZ (o Apple/Google hesabındadır).

SİLMEYİ ASLA ENGELLEMEZ: anahtar yoksa ya da çağrı başarısızsa yalnız loglanır,
hesap silme sürer. Exception fırlatmaz.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("tavsan.revenuecat")

RC_SUBSCRIBER_URL = "https://api.revenuecat.com/v1/subscribers/{}"
TIMEOUT_SN = 10


def aboneyi_sil(user_id) -> str:
    """Dönen: "silindi" | "anahtar_yok" | "hata"."""
    try:
        from api.config import get_settings
        anahtar = get_settings().revenuecat_api_key
        if not anahtar:
            logger.warning("Hesap silme: REVENUECAT_API_KEY yok — RevenueCat "
                           "müşteri kaydı silinmedi (user=%s)", user_id)
            return "anahtar_yok"
        import requests
        r = requests.delete(RC_SUBSCRIBER_URL.format(user_id), timeout=TIMEOUT_SN,
                            headers={"Authorization": f"Bearer {anahtar}",
                                     "Content-Type": "application/json"})
        if 200 <= r.status_code < 300 or r.status_code == 404:
            # 404: RevenueCat'te hiç müşteri kaydı yok — silinecek bir şey yok.
            logger.info("Hesap silme: RevenueCat müşteri kaydı silindi "
                        "(user=%s, HTTP %s)", user_id, r.status_code)
            return "silindi"
        logger.warning("Hesap silme: RevenueCat silme başarısız (user=%s, HTTP %s)",
                       user_id, r.status_code)
        return "hata"
    except Exception:
        logger.exception("Hesap silme: RevenueCat çağrısı çöktü (user=%s)", user_id)
        return "hata"
