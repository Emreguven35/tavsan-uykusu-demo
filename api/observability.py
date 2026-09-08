"""
Sentry hata izleme — KVKK maskeleme katmanıyla.

AÇILMA KOŞULU: yalnız ENVIRONMENT=production VE SENTRY_DSN tanımlıysa. Lokal
geliştirmede ve testlerde KAPALI (yanlışlıkla geliştirici makinesinden olay
göndermeyi de, testlerin ağa çıkmasını da engeller).

--- KVKK: asıl risk send_default_pii DEĞİL ---
`send_default_pii=False` istek gövdesini ve çerezleri kapatır ama Sentry
VARSAYILAN OLARAK stack frame'lerdeki YEREL DEĞİŞKENLERİ gönderir. Bu uygulamada
yerel değişkenler şunları tutuyor: `req.message` (annenin sorusu), `text`
(topluluk gönderisi), `content` (cevap metni), `baby.name`, `user.email`.
Yani sadece PII bayrağına güvenmek, annenin gece 3'te yazdığı cümleyi üçüncü bir
servise göndermek demekti. Bu yüzden üç katman birlikte uygulanır:

  1. include_local_variables=False  → frame değişkenleri HİÇ toplanmaz (kök çözüm)
  2. max_request_body_size="never"  → istek gövdesi hiç eklenmez
  3. before_send (aşağıdaki `maskele`) → geriye ne kalırsa süzülür (emniyet kemeri)

Katman 3 tek başına yeterli değil (bilinmeyen alan adları kaçar), katman 1-2 tek
başına yeterli değil (istisna MESAJININ içine gömülü içerik kalır: örneğin
`ValueError("geçersiz mesaj: <annenin cümlesi>")`). Üçü birlikte gerekiyor.

Giden veri: hata tipi, stack trace (değişkensiz), endpoint yolu, HTTP metodu,
sürüm damgası ve HASH'LENMİŞ user_id. E-posta, token, içerik GİTMEZ.
Breadcrumb'lar ve biçimlendirilmiş log mesajı da gitmez (bkz. `maskele` madde 4).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any

logger = logging.getLogger("tavsan.observability")

MASKE = "[maskelendi]"

# Değeri KOŞULSUZ maskelenecek alan adları (küçük harfe indirilip aranır —
# alt dize eşleşmesi: "baby_name", "user_email", "access_token" hepsi yakalanır).
HASSAS_ANAHTARLAR = (
    # kimlik & yetki
    "password", "parola", "token", "authorization", "auth", "secret", "api_key",
    "apikey", "x-api-key", "cookie", "session", "jwt", "credential", "dsn",
    # kişisel veri
    "email", "e-posta", "eposta", "mail", "phone", "telefon",
    "name", "ad", "isim", "nickname", "birth", "dogum", "doğum",
    # içerik
    "message", "mesaj", "content", "icerik", "içerik", "body", "text", "metin",
    "soru", "question", "cevap", "answer", "prompt", "story", "masal", "title",
    "baslik", "başlık", "note", "not",
)

# İçerik gövdeye gömülmüşse (istisna mesajı, breadcrumb) desenle yakalanır.
_EPOSTA = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_BEARER = re.compile(r"(?i)\b(bearer|x-api-key|api[-_]?key)\s*[:=]?\s*\S+")
_UZUN_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")     # jwt/sk_/hash benzeri
_SENTRY_DSN = re.compile(r"https://[^@\s]+@[^\s]+")

# İstisna mesajları bu uzunluğu aşarsa büyük olasılıkla içerik taşıyor (SQL
# parametresi, prompt parçası). Kırpılır — tip ve stack trace zaten elde.
MAX_MESAJ = 200


def _hassas_anahtar(anahtar: Any) -> bool:
    a = str(anahtar).lower()
    return any(h in a for h in HASSAS_ANAHTARLAR)


def metni_temizle(deger: Any) -> Any:
    """Serbest metinden e-posta/token/DSN kalıplarını sil, aşırı uzunsa kırp."""
    if not isinstance(deger, str):
        return deger
    s = _SENTRY_DSN.sub(MASKE, deger)
    s = _BEARER.sub(MASKE, s)
    s = _EPOSTA.sub(MASKE, s)
    s = _UZUN_TOKEN.sub(MASKE, s)
    if len(s) > MAX_MESAJ:
        s = s[:MAX_MESAJ] + "…" + MASKE
    return s


def _derin_maskele(obj: Any, derinlik: int = 0) -> Any:
    """Sözlük/liste ağacında hassas anahtarları maskele, metinleri temizle."""
    if derinlik > 12:                       # kendine referanslı yapıya karşı
        return MASKE
    if isinstance(obj, dict):
        return {k: (MASKE if _hassas_anahtar(k) else _derin_maskele(v, derinlik + 1))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_derin_maskele(v, derinlik + 1) for v in obj]
    return metni_temizle(obj)


def maskele(event: dict, hint: dict | None = None) -> dict | None:
    """Sentry before_send kancası. Olayı KVKK'ya uygun hâle getirir.

    Saf fonksiyondur (Sentry SDK'sına bağımlı değil) — test edilebilir olsun diye.
    """
    if not isinstance(event, dict):
        return event

    # 1) Kullanıcı: yalnız hash'li id kalır. E-posta/ip ASLA.
    kullanici = event.get("user")
    if isinstance(kullanici, dict):
        event["user"] = {"id": kullanici.get("id")} if kullanici.get("id") else {}

    # 2) İstek: yol + metot kalır; gövde, çerez, hassas başlık ve sorgu düşer.
    istek = event.get("request")
    if isinstance(istek, dict):
        istek.pop("data", None)
        istek.pop("cookies", None)
        istek.pop("env", None)
        basliklar = istek.get("headers")
        if isinstance(basliklar, dict):
            istek["headers"] = {k: (MASKE if _hassas_anahtar(k) else v)
                                for k, v in basliklar.items()}
        if istek.get("query_string"):
            istek["query_string"] = MASKE
        if isinstance(istek.get("url"), str):
            istek["url"] = istek["url"].split("?")[0]     # sorgu değerleri düşsün

    # 3) İstisna: mesaj metni temizlenir, frame yerel değişkenleri SİLİNİR.
    #    (include_local_variables=False zaten toplamıyor; bu ikinci kemer.)
    for deger in (event.get("exception") or {}).get("values", []) or []:
        if isinstance(deger, dict):
            if isinstance(deger.get("value"), str):
                deger["value"] = metni_temizle(deger["value"])
            frames = (deger.get("stacktrace") or {}).get("frames") or []
            for f in frames:
                if isinstance(f, dict):
                    f.pop("vars", None)
                    f.pop("pre_context", None)
                    f.pop("context_line", None)
                    f.pop("post_context", None)

    # 4) Serbest metin: BREADCRUMB'LAR TAMAMEN DÜŞER.
    #
    # Ölçümle bulundu: kalıp temizliği (e-posta/token regex'i) serbest metinde
    # YETMİYOR. Breadcrumb'lar SQL sorgularını ve log satırlarını taşıyor; örnek
    # gerçek bir kırıntı: "SQL INSERT chat_messages content=<annenin cümlesi>".
    # O cümlede ne e-posta var ne token — hiçbir desene uymuyor, 200 karakterin
    # de altında, yani kırpmaya da takılmıyor. Süzmeye çalışmak yerine kaynağı
    # kapatmak tek güvenli yol: teşhis için hata tipi + stack trace + endpoint
    # zaten yeterli.
    event.pop("breadcrumbs", None)

    # Biçimlendirilmiş log mesajı da AYNI nedenle düşer (%s'ler yerine oturmuş
    # hâli içerik taşıyabilir). logentry.message KALIR: o, geliştiricinin yazdığı
    # BİÇİM DİZESİDİR ("chat: user=%s q_len=%d") — koddur, veri değil; params
    # ise düşürülür.
    event.pop("message", None)
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        logentry.pop("params", None)         # %s parametreleri içerik taşır
        if isinstance(logentry.get("formatted"), str):
            logentry.pop("formatted", None)  # biçimlenmiş hâli = veri

    # 5) Serbest alanlar
    for alan in ("extra", "tags"):
        if isinstance(event.get(alan), dict):
            event[alan] = _derin_maskele(event[alan])

    return event


# ---------------------------------------------------------------------------
# Kullanıcı bağlamı — yalnız HASH'li id
# ---------------------------------------------------------------------------
def kullanici_hash(user_id: Any) -> str:
    """user_id'yi tuzlanmış hash'e çevir (16 hex). Tuz JWT_SECRET'tan gelir, yani
    hash Sentry'de duran bir değerden geri çözülemez ve başka sistemlerdeki
    id'lerle eşleştirilemez."""
    tuz = os.getenv("JWT_SECRET", "")
    return hashlib.sha256(f"{tuz}|{user_id}".encode("utf-8")).hexdigest()[:16]


def kullaniciyi_isaretle(user_id: Any) -> None:
    """İsteği açan kullanıcıyı Sentry bağlamına HASH'li olarak yaz. Sentry kapalıysa
    sessizce döner (izleme yokken uygulama akışı etkilenmemeli)."""
    if not _acik:
        return
    try:
        import sentry_sdk
        sentry_sdk.set_user({"id": kullanici_hash(user_id)})
    except Exception:                        # izleme hatası isteği bozmaz
        pass


# ---------------------------------------------------------------------------
# Başlatma
# ---------------------------------------------------------------------------
_acik = False
TRACES_ORANI = 0.1


def sentry_acik() -> bool:
    return _acik


def sentry_baslat(surum: str | None = None) -> bool:
    """Sentry'yi başlat. Açıldıysa True. Koşullar sağlanmazsa sessizce False."""
    global _acik
    dsn = (os.getenv("SENTRY_DSN") or "").strip()
    ortam = (os.getenv("ENVIRONMENT") or "development").strip().lower()

    if not dsn:
        logger.info("SENTRY_DSN yok → hata izleme kapalı.")
        return False
    if ortam != "production":
        logger.info("ENVIRONMENT=%s (production değil) → hata izleme kapalı.", ortam)
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning("sentry-sdk kurulu değil → hata izleme kapalı.")
        return False

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=ortam,
            release=surum,
            traces_sample_rate=TRACES_ORANI,
            # --- KVKK ayarları (bkz. modül başlığı) ---
            send_default_pii=False,
            include_local_variables=False,       # frame değişkenleri TOPLANMAZ
            max_request_body_size="never",
            before_send=maskele,
            before_send_transaction=maskele,
            # Starlette/FastAPI entegrasyonlarında istek gövdesi hiç okunmasın.
            integrations=[
                StarletteIntegration(transaction_style="endpoint"),
                FastApiIntegration(transaction_style="endpoint"),
            ],
        )
    except Exception as e:
        logger.warning("Sentry başlatılamadı (uygulama etkilenmez): %s", e)
        return False

    _acik = True
    logger.info("Sentry açık (environment=%s, traces=%.2f, PII kapalı).",
                ortam, TRACES_ORANI)
    return True
