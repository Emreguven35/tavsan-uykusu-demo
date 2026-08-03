"""Chat (RAG) şemaları — mobil sözleşmesi."""
from pydantic import BaseModel, Field


class ChatMessageItem(BaseModel):
    role: str                        # user | assistant
    content: str


class ChatReq(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # Sözleşme gereği alınır; mevcut RAG motoru TEK TURLUdur (cevap current message'a
    # dayanır, cache tutarlılığı için). history ileride multi-turn'e açık bırakıldı.
    history: list[ChatMessageItem] = Field(default_factory=list)
    # Opsiyonel yaş bandı → semantik cache katmanını etkinleştirir (yoksa yalnız exact).
    yas_bandi: str | None = None


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
