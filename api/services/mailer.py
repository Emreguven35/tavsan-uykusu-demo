"""
E-posta servisi — Resend + console fallback (Faz 6.3).

ÜÇ MOD (MAIL_PROVIDER):
  "resend"   → gerçek gönderim (RESEND_API_KEY gerekir).
  "console"  → gönderim yok, içerik LOGLANIR. YALNIZ lokal geliştirme içindir:
               sıfırlama token'ı uygulama loguna düşer.
  "disabled" → gönderim yok, içerik HİÇBİR YERE yazılmaz (ne e-posta ne log).
               Akış sessizce sonlanır; endpoint yine 200 döner (e-posta enumeration
               önlenir). PRODUCTION VARSAYILANI — Resend bağlanana kadar token
               hiçbir kanala sızmaz. Mobil bu süreçte "yakında" gösterir.

Resend bağlanınca TEK env değişikliğiyle aktifleşir: RESEND_API_KEY tanımlayın
(MAIL_PROVIDER'ı silin ya da "resend" yapın).

send_email() ASLA exception fırlatmaz — e-posta bir yan etkidir, ana akışı
(kayıt olma, parola sıfırlama talebi) düşürmemelidir.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from api.config import get_settings

logger = logging.getLogger("tavsan.mailer")

RESEND_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT = 15


def _console_send(to: str, subject: str, text: str) -> dict:
    """Gönderim yok — içeriği logla. Geliştirme/doğrulama öncesi mod."""
    logger.warning(
        "MAIL_PROVIDER=console — e-posta GÖNDERİLMEDİ, içerik aşağıda:\n"
        "  Alıcı : %s\n  Konu  : %s\n  Gövde :\n%s", to, subject, text)
    return {"ok": True, "provider": "console", "id": None}


def _disabled_send(subject: str) -> dict:
    """Gönderim yok ve İÇERİK LOGLANMAZ. Alıcı (KVKK) ve token (güvenlik) yazılmaz —
    yalnız hangi tür e-postanın atlandığı görünür."""
    logger.info("MAIL_PROVIDER=disabled — e-posta atlandı (konu=%r)", subject)
    return {"ok": True, "provider": "disabled", "id": None}


def send_email(to: str, subject: str, text: str, html: str | None = None) -> dict:
    """E-posta gönder. Dönen: {ok, provider, id?, error?}. Exception FIRLATMAZ.

    ok=False yalnız gerçek bir gönderim denemesi başarısız olduğunda döner;
    console/disabled modlarında ok=True'dur (akış kırılmaz)."""
    settings = get_settings()

    if settings.mail_provider == "disabled":
        return _disabled_send(subject)
    if settings.mail_provider != "resend" or not settings.resend_api_key:
        return _console_send(to, subject, text)

    payload: dict[str, Any] = {
        "from": settings.mail_from,
        "to": [to],
        "subject": subject,
        "text": text,
    }
    if html:
        payload["html"] = html

    try:
        r = requests.post(
            RESEND_URL, json=payload, timeout=RESEND_TIMEOUT,
            headers={"Authorization": f"Bearer {settings.resend_api_key}",
                     "Content-Type": "application/json"})
    except Exception as e:
        # Ağ hatası → akış kırılmaz, yalnız raporlanır. API KEY loglanmaz.
        logger.warning("Resend isteği başarısız (alıcı=%s): %s", to, e)
        return {"ok": False, "provider": "resend", "error": str(e)}

    if not r.ok:
        # Yanıt gövdesi domain doğrulama hatalarını içerebilir — kısaltarak logla.
        logger.warning("Resend HTTP %s (alıcı=%s): %s", r.status_code, to, r.text[:300])
        return {"ok": False, "provider": "resend", "error": f"HTTP {r.status_code}"}

    try:
        msg_id = r.json().get("id")
    except Exception:
        msg_id = None
    logger.info("E-posta gönderildi (Resend): alıcı=%s id=%s", to, msg_id)
    return {"ok": True, "provider": "resend", "id": msg_id}


# =============================================================================
# Şablonlar
# =============================================================================
def send_password_reset(to: str, raw_token: str) -> dict:
    """Parola sıfırlama e-postası.

    6 haneli kod DEĞİL, derin bağlantı gönderilir:
        tavsan-uykusu://reset-password?token=...
    Ayrıca düz metin token da yazılır — derin bağlantı açılmazsa kullanıcı
    mobil ekranda elle girebilsin (alternatif yol)."""
    settings = get_settings()
    link = f"{settings.app_deep_link_scheme}://reset-password?token={raw_token}"
    subject = "Tavşan Uykusu — Şifre Sıfırlama"
    text = (
        "Merhaba,\n\n"
        "Tavşan Uykusu hesabınız için parola sıfırlama talebinde bulundunuz.\n\n"
        "Aşağıdaki bağlantıya dokunarak yeni parolanızı belirleyebilirsiniz:\n"
        f"{link}\n\n"
        "Bağlantı açılmazsa, uygulamadaki 'Şifre Sıfırlama' ekranına şu kodu "
        "elle girebilirsiniz:\n\n"
        f"{raw_token}\n\n"
        "Bu bağlantı 1 saat geçerlidir ve yalnızca bir kez kullanılabilir.\n"
        "Bu talebi siz yapmadıysanız bu e-postayı yok sayabilirsiniz; "
        "parolanız değişmez.\n\n"
        "İyi uykular,\nTavşan Uykusu"
    )
    html = (
        "<p>Merhaba,</p>"
        "<p>Tavşan Uykusu hesabınız için parola sıfırlama talebinde bulundunuz.</p>"
        f'<p><a href="{link}">Yeni parolamı belirle</a></p>'
        "<p>Bağlantı açılmazsa, uygulamadaki <strong>Şifre Sıfırlama</strong> "
        "ekranına şu kodu elle girebilirsiniz:</p>"
        f"<p><code>{raw_token}</code></p>"
        "<p>Bu bağlantı 1 saat geçerlidir ve yalnızca bir kez kullanılabilir.<br>"
        "Bu talebi siz yapmadıysanız bu e-postayı yok sayabilirsiniz; "
        "parolanız değişmez.</p>"
        "<p>İyi uykular,<br>Tavşan Uykusu</p>"
    )
    return send_email(to, subject, text, html)


def send_welcome(to: str) -> dict:
    """Kayıt sonrası hoş geldin e-postası (opsiyonel).

    YALNIZ gerçek sağlayıcı aktifken gönderilir — console'da log kirliliği,
    disabled'da anlamsız iş yaratmasın diye. Parola sıfırlamadan farkı budur
    (o kritik akış her modda çağrılır, mod kararını send_email verir)."""
    settings = get_settings()
    if settings.mail_provider != "resend" or not settings.resend_api_key:
        return {"ok": True, "provider": "skipped", "id": None}

    subject = "Tavşan Uykusu'na hoş geldiniz 🌙"
    text = (
        "Merhaba,\n\n"
        "Tavşan Uykusu'na hoş geldiniz! Bebeğinizin profilini oluşturarak "
        "size özel uyku planınızı hazırlayabilirsiniz.\n\n"
        "Uygulamadaki uyku kayıtlarınız plana geri beslenir; planınız gerçek "
        "uyku düzeninize göre kendini günceller.\n\n"
        "İyi uykular,\nTavşan Uykusu"
    )
    html = (
        "<p>Merhaba,</p>"
        "<p><strong>Tavşan Uykusu</strong>'na hoş geldiniz! Bebeğinizin profilini "
        "oluşturarak size özel uyku planınızı hazırlayabilirsiniz.</p>"
        "<p>Uygulamadaki uyku kayıtlarınız plana geri beslenir; planınız gerçek "
        "uyku düzeninize göre kendini günceller.</p>"
        "<p>İyi uykular,<br>Tavşan Uykusu</p>"
    )
    return send_email(to, subject, text, html)
