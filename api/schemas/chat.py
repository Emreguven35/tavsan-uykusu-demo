"""Chat (RAG) şemaları — mobil sözleşmesi."""
import uuid
from datetime import datetime

from pydantic import AliasChoices, BaseModel, Field


class ChatMessageItem(BaseModel):
    role: str                        # user | assistant
    content: str
    # Opsiyonel (2026-10-12, SOR_GECMIS): mesajın zamanı. Varsa 12 saatten eski
    # geçmiş modele gönderilmez; yoksa mesaj olduğu gibi gider. Mobil bugün
    # göndermiyor (yerel kayıtta `createdAt` var) — iki yazım da kabul edilir.
    created_at: datetime | None = Field(
        default=None, validation_alias=AliasChoices("created_at", "createdAt"))


class ChatReq(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # SOR_GECMIS kapalıyken motor TEK TURLUdur (history alınır ama kullanılmaz).
    # Açıkken son 6 mesaj (takma adla gizlenmiş) o anki sorudan önce modele gider.
    history: list[ChatMessageItem] = Field(default_factory=list)
    # Opsiyonel yaş bandı → semantik cache katmanını etkinleştirir (yoksa yalnız exact).
    yas_bandi: str | None = None
    # Opsiyonel (Faz 6.5): verilirse bebeğin profili + son 3 günün logları + bugünün
    # planı bağlama eklenir ve cevap kişiselleştirilir (somut saatler, bebeğin adı).
    # DİKKAT: baby_id'li istekler cevap cache'ini BYPASS eder (kişisel veri
    # başka kullanıcıya sızmasın). Bebek çağırana ait değilse 404.
    baby_id: uuid.UUID | None = None


class ChatSource(BaseModel):
    chunk_id: str | None = None
    label: str | None = None
    source: str | None = None
    score: float | None = None


class ChatResp(BaseModel):
    answer: str
    cached: bool
    sources: list[ChatSource] | None = None
    # Hangi fallback katmanında cevaplandı (Faz 6.4):
    #   k1 = metodolojiden doğrudan
    #   k2 = en yakın bilgi (yaş bandı genişletme / düşük eşik)
    #   k3 = genel ilke + netleştirme sorusu (mobil bunu "sohbeti sürdür" olarak
    #        değerlendirebilir)
    #   k4 = kapsam dışı
    # Cache hit'te NULL (retrieval yapılmadı).
    retrieval_layer: str | None = None
    # B4 — premium değilse bugün kalan ücretsiz soru hakkı; premium'da None.
    ucretsiz_kalan: int | None = None
