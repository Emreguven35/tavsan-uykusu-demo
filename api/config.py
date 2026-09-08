"""
Merkezi API yapılandırması — TÜM sırlar ve ayarlar env'den okunur.

Kullanım:
    from api.config import get_settings
    settings = get_settings()

Streamlit tarafı bu modülü kullanmaz; yalnızca FastAPI backend içindir.
Hiçbir API anahtarı koda GÖMÜLMEZ (KVKK/güvenlik). Değerler .env / Railway
Variables'tan gelir. .env.example örnek şablondur.
"""
import os
from functools import lru_cache


def _normalize_db_url(url: str) -> str:
    """Railway/Heroku bazen 'postgres://' verir; SQLAlchemy 'postgresql://' bekler.
    Ayrıca sürücüyü psycopg2'ye sabitleriz (requirements: psycopg2-binary)."""
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://") and "+psycopg" not in url:
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


# ---------------------------------------------------------------------------
# FİYAT TABLOSU — maliyet takibinin tek kaynağı (api_usage.estimated_cost_usd)
# ---------------------------------------------------------------------------
# Burası KOD SABİTİ, env değil: fiyat değişince kod değişikliği + gözden geçirme
# olsun isteniyor (yanlışlıkla boş env ile maliyeti sıfır saymak istemiyoruz).
#
# Anthropic: 1M token başına USD. Cache çarpanları resmî oranlar —
#   cache YAZMA  = girdi fiyatı × 1.25  (yazmak pahalıdır)
#   cache OKUMA  = girdi fiyatı × 0.10  (asıl tasarruf burada)
# Kaynak: Anthropic fiyatlandırma, 2026-08-25.
ANTHROPIC_FIYATLARI: dict[str, dict[str, float]] = {
    "claude-haiku-4-5":  {"in": 1.0, "out": 5.0},     # /chat + moderasyon
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},    # plan üretimi
    "claude-sonnet-5":   {"in": 3.0, "out": 15.0},
    "claude-opus-5":     {"in": 5.0, "out": 25.0},
}
# Tabloda olmayan bir model gelirse maliyet sıfır sayılmaz; en pahalı bilinen
# değerle üst sınırdan hesaplanır ve uyarı loglanır. Sessizce 0 yazmak, maliyet
# tablosunu "her şey bedava" gösteren en tehlikeli hata olurdu.
ANTHROPIC_BILINMEYEN_FIYAT = {"in": 5.0, "out": 25.0}
CACHE_YAZMA_CARPANI = 1.25
CACHE_OKUMA_CARPANI = 0.10

# ElevenLabs: karakter başına USD. Flash v2.5 = 0.5 kredi/karakter, Creator planı
# $22 / 100.000 kredi → 0.5 × 0.00022 ≈ 0.00011 $/karakter (api/tts.py ile aynı
# değer; oradaki sabit bu tablodan okunur, iki yerde ayrışmasın).
ELEVENLABS_FIYATLARI: dict[str, float] = {
    "eleven_flash_v2_5": 0.00011,
    "eleven_multilingual_v2": 0.00022,
}
ELEVENLABS_BILINMEYEN_FIYAT = 0.00022
# Ses klonlama karakter değil, işlem başına ücretlendirilir (plan kotasından
# düşer). Kotadan düşen bir işlemi 0 yazmak yerine ölçülebilir bir değer
# tutuyoruz ki "kaç klon yapıldı" raporda görünsün.
VOICE_CLONE_USD = 0.0

# Günlük harcama eşiği — aşılırsa CRITICAL log (alarm bu log satırına kurulur).
GUNLUK_MALIYET_ESIGI_USD = 20.0


class ConfigError(RuntimeError):
    """Zorunlu bir ayar eksik/geçersiz — uygulama BAŞLAMAMALI (sessizce güvensiz
    varsayılana düşmek yerine gürültülü şekilde dur)."""


class Settings:
    """Uygulama ayarları. Tümü env'den; test/lokal için makul varsayılanlar."""

    def __init__(self) -> None:
        # Veritabanı — lokal geliştirmede DATABASE_URL yoksa dosya-tabanlı SQLite'a
        # düşer (postgres kurmadan boot edebilmek için). Production'da DATABASE_URL
        # zorunlu (Railway PostgreSQL eklentisi otomatik sağlar).
        raw_db = os.getenv("DATABASE_URL", "sqlite:///./tavsan_local.db").strip()
        self.database_url = _normalize_db_url(raw_db)
        self.is_sqlite = self.database_url.startswith("sqlite")

        # Ortam: "production" | "development" (varsayılan). Production'da CORS "*"
        # kabul EDİLMEZ (aşağıda cors_origins'e bakınız).
        self.environment = os.getenv("ENVIRONMENT", "development").strip().lower()
        self.is_production = self.environment == "production"

        # JWT — Faz 2 auth. ZORUNLU: varsayılana DÜŞMEZ. Tanımsız/boş ise uygulama
        # başlamaz (Faz 5R). Üretmek için: openssl rand -hex 32
        self.jwt_secret = os.getenv("JWT_SECRET", "").strip()
        if not self.jwt_secret:
            raise ConfigError(
                "JWT_SECRET tanımlı değil. Zorunludur (varsayılana düşülmez). "
                "Üretin: openssl rand -hex 32 — sonra env/Railway Variables'a ekleyin."
            )
        if len(self.jwt_secret) < 32:
            raise ConfigError(
                "JWT_SECRET çok kısa (min 32 karakter). openssl rand -hex 32 kullanın."
            )
        self.jwt_algorithm = os.getenv("JWT_ALGORITHM", "HS256")
        self.access_token_expire_minutes = int(
            os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))          # 1 saat
        self.refresh_token_expire_days = int(
            os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))            # 30 gün

        # Dış servis anahtarları (mevcut engine/api modülleri env'den okur; burada
        # sadece merkezi erişim + health raporu için tutulur).
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        self.elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
        self.heygen_api_key = os.getenv("HEYGEN_API_KEY")            # avatar (LiveAvatar) — Faz 4

        # Demo endpoint anahtarı (Faz 5R): /ask ve /avatar-session korumasız LLM/kredi
        # tetikleyicileriydi. Tanımsızsa bu endpoint'ler KAPALIDIR (503).
        self.demo_api_key = (os.getenv("DEMO_API_KEY") or "").strip()

        # --- E-posta (Faz 6.3) ----------------------------------------------
        # MAIL_PROVIDER üç moddan biri:
        #   "resend"   → gerçek gönderim (RESEND_API_KEY gerekir)
        #   "console"  → gönderim yok, içerik LOGLANIR (yalnız lokal geliştirme)
        #   "disabled" → gönderim yok, içerik HİÇBİR YERE yazılmaz; akış sessizce
        #                sonlanır. Token ne e-postaya ne loga gider (production
        #                varsayılanı — sıfırlama token'ı log'a sızmaz).
        # Açıkça verilmezse: anahtar varsa resend, yoksa production'da disabled,
        # geliştirmede console.
        self.resend_api_key = (os.getenv("RESEND_API_KEY") or "").strip()
        self.mail_from = (os.getenv("MAIL_FROM")
                          or "Tavşan Uykusu <onboarding@resend.dev>").strip()
        _provider = (os.getenv("MAIL_PROVIDER") or "").strip().lower()
        if not _provider:
            if self.resend_api_key:
                _provider = "resend"
            else:
                _provider = "disabled" if self.is_production else "console"
        self.mail_provider = _provider
        # Mobil derin bağlantı şeması (parola sıfırlama e-postasındaki link).
        self.app_deep_link_scheme = (os.getenv("APP_DEEP_LINK_SCHEME")
                                     or "tavsan-uykusu").strip()

        # --- Public taban URL (Faz 6.7) --------------------------------------
        # Ses dosyası bağlantıları MUTLAK döner (https://.../audio/<hash>.mp3) —
        # mobilin göreli path birleştirme riski ortadan kalkar. Tanımsızsa
        # göreli path'e düşülür (lokal geliştirme + mevcut testler bozulmaz).
        self.public_base_url = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")

        # --- Abonelik / beta (Faz G5) ---------------------------------------
        # Gerçek Apple/Google IAP doğrulaması ayrı sprint. Beta süresince premium'u
        # SUNUCU TARAFI bu flag ile aç — istemcinin gönderdiği bir "premium" alanına
        # ASLA güvenme. true → GET /subscriptions/status herkese premium döner.
        self.beta_premium_all = (os.getenv("BETA_PREMIUM_ALL") or "").strip().lower() \
            in ("1", "true", "yes", "on")

        # CORS — virgülle ayrılmış origin listesi. "*" YALNIZ development'ta geçerli;
        # production'da yok sayılır (aşağıda).
        self.allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").strip()

    @property
    def cors_origins(self) -> list[str]:
        """Tarayıcı origin'leri. Mobil native istekler Origin başlığı GÖNDERMEZ —
        CORS onları etkilemez, bu yüzden production'da liste boş olabilir.

        Production + "*" → boş liste (deny-all). Yanlışlıkla herkese açık bırakmayı
        engeller; gerçek web origin'i varsa ALLOWED_ORIGINS'e açıkça yazılır."""
        if self.allowed_origins == "*":
            return [] if self.is_production else ["*"]
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def cors_wildcard_in_production(self) -> bool:
        """Production'da "*" bırakılmış mı (startup'ta uyarı için)."""
        return self.is_production and self.allowed_origins == "*"


@lru_cache
def get_settings() -> Settings:
    """Süreç boyunca tek Settings örneği (env bir kez okunur)."""
    return Settings()
