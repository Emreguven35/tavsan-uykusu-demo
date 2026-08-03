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
