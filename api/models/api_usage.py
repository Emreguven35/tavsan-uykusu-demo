"""
api_usage — her dış servis çağrısının GERÇEK kullanım kaydı (maliyet takibi).

Neden tahmin değil gerçek: /ask yanıtındaki `maliyet.llm_usd` karakter sayısından
tahmin ediyordu (4 karakter ≈ 1 token). Bu tahmin prompt caching'i hiç görmüyor —
cache'ten okunan token normal fiyatın %10'u, cache'e yazılan %125'i. Yani tahmin
hem yanlış hem de cache'in ne kadar kazandırdığını ölçmeyi imkânsız kılıyor.
Burada Anthropic yanıtındaki `usage` bloğu ve ElevenLabs'e giden karakter sayısı
OLDUĞU GİBİ saklanır; fiyat çarpanı api.config.MODEL_FIYATLARI'ndan gelir.

KVKK: bu tabloda İÇERİK YOK. Ne soru, ne cevap, ne masal metni — yalnız sayaçlar,
model adı ve (varsa) user_id. İçerik chat_messages'ta zaten var; burada tekrarı
hem gereksiz hem risk.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class ApiUsage(Base):
    __tablename__ = "api_usage"

    id: Mapped[uuid.UUID] = uuid_pk()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    # anthropic | elevenlabs
    service: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # chat | plan_generate | plan_adapt | moderation | tts | voice_clone
    operation: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Model/motor adı: "claude-haiku-4-5", "eleven_flash_v2_5", "voice-clone" ...
    # UZUNLUK: model adları uzayabilir (SQLite uzunluk zorlamaz, Postgres zorlar —
    # bkz. migration 0007'nin acil düzeltmesi). 64 bol geniş.
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- Anthropic sayaçları (yanıttaki usage bloğundan AYNEN) ---------------
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Cache'ten OKUNAN token (indirimli fiyatlanır). Yazma ayrı tutulur çünkü
    # yazma pahalıdır (1.25x) ve ikisini toplamak cache kârını yanlış gösterir.
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- ElevenLabs sayacı ---------------------------------------------------
    characters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Anonim/sistem çağrıları (moderasyon, zamanlanmış iş) için NULL.
    # ondelete=SET NULL: kullanıcı hesabını silince maliyet geçmişi KAYBOLMAZ
    # (muhasebe kaydı), ama kime ait olduğu düşer — KVKK açısından da doğrusu bu.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    __table_args__ = (
        # Admin raporunun ana sorgusu: tarih aralığı + kırılım.
        Index("ix_api_usage_created_service", "created_at", "service"),
        Index("ix_api_usage_created_operation", "created_at", "operation"),
    )
