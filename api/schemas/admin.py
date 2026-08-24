"""Admin (maliyet raporu) şemaları."""
from datetime import date

from pydantic import BaseModel, Field


class KirilimItem(BaseModel):
    ad: str                          # servis adı / operasyon adı
    usd: float
    cagri: int


class GunItem(BaseModel):
    gun: date
    usd: float
    cagri: int


class PromptCacheOzet(BaseModel):
    """Anthropic prompt cache'i — GERÇEK sayaçlardan, tahmin değil."""
    okunan_token: int                # cache'ten okunan (fiyatın %10'u)
    tam_fiyatli_token: int           # cache'e girmeyen girdi token'ı
    yazilan_token: int               # cache'e yazılan (fiyatın %125'i)
    oran: float = Field(description="okunan / (okunan + tam_fiyatli)")
    kazanc_usd: float = Field(description="Tam fiyat ödenseydi aradaki fark")


class CevapCacheOzet(BaseModel):
    """Uygulama içi cevap cache'i — LLM'e HİÇ gidilmeyen sorular.

    Kaynağı api_usage değil chat_messages: cache HIT'inde dış servis çağrısı
    olmadığı için maliyet defterinde satır yoktur. Kazanç, aynı dönemdeki
    LLM'li sohbetlerin ortalama maliyetiyle TAHMİN edilir."""
    toplam: int
    hit: int
    oran: float
    tahmini_kazanc_usd: float


class CacheOzet(BaseModel):
    prompt_cache: PromptCacheOzet
    cevap_cache: CevapCacheOzet


class UsageResp(BaseModel):
    baslangic: date
    bitis: date
    group_by: str
    toplam_usd: float
    cagri_sayisi: int
    gruplar: list[KirilimItem] | list[GunItem]
    servis: list[KirilimItem]
    operasyon: list[KirilimItem]
    gunluk: list[GunItem]
    cache: CacheOzet
    gunluk_esik_usd: float
    esigi_asan_gunler: list[date]
