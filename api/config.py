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


class Settings:
    """Uygulama ayarları. Tümü env'den; test/lokal için makul varsayılanlar."""

    def __init__(self) -> None:
        # Veritabanı — lokal geliştirmede DATABASE_URL yoksa dosya-tabanlı SQLite'a
        # düşer (postgres kurmadan boot edebilmek için). Production'da DATABASE_URL
        # zorunlu (Railway PostgreSQL eklentisi otomatik sağlar).
        raw_db = os.getenv("DATABASE_URL", "sqlite:///./tavsan_local.db").strip()
        self.database_url = _normalize_db_url(raw_db)
        self.is_sqlite = self.database_url.startswith("sqlite")

        # JWT — Faz 2 auth. Lokal varsayılan GÜVENSİZDİR; production'da JWT_SECRET
        # env ile SABİTLENMELİDİR (yoksa loglanır/uyarılır).
        self.jwt_secret = os.getenv("JWT_SECRET", "dev-insecure-secret-CHANGE-ME")
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

        # CORS — virgülle ayrılmış origin listesi; "*" herkese açık (dev).
        self.allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").strip()

    @property
    def cors_origins(self) -> list[str]:
        if self.allowed_origins == "*":
            return ["*"]
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def jwt_secret_is_default(self) -> bool:
        return self.jwt_secret == "dev-insecure-secret-CHANGE-ME"


@lru_cache
def get_settings() -> Settings:
    """Süreç boyunca tek Settings örneği (env bir kez okunur)."""
    return Settings()
