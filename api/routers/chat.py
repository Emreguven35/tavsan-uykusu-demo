"""
chat router — /api/v1/chat  (RAG soru-cevap)

MEVCUT motor AYNEN kullanılır: engine.chatbot._cevap_uret → iki katmanlı cevap
cache (exact + semantik ≥0.95, LRU 500) → retrieval → Haiku (prompt caching yapısı
korunur) → store. Tıbbi sınır davranışı (SYSTEM_PROMPT: "çocuk doktoruna başvurun")
motorda; burada değiştirilmez.

Her mesaj çifti chat_messages'a yazılır. KVKK: loglara mesaj İÇERİĞİ yazılmaz —
yalnız uzunluk + cache durumu.
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user, get_owned_baby
from api.models import ChatMessage, User
from api.schemas.chat import ChatReq, ChatResp, ChatSource
from api.services import baby_context as baby_ctx
from engine import chatbot

logger = logging.getLogger("tavsan.chat")
router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResp)
def chat(req: ChatReq, db: Session = Depends(get_db),
         user: User = Depends(get_current_user)):
    # Faz 6.5: baby_id verilmişse bebeğin profili + son 3 gün logu + bugünün planı
    # bağlama eklenir. Bebek çağırana ait değilse get_owned_baby 404 döner
    # (başka kullanıcının verisi sızmaz).
    ctx = None
    if req.baby_id is not None:
        baby = get_owned_baby(req.baby_id, db, user)
        ctx = baby_ctx.build_baby_context(db, baby)
        # ctx None ise (hiç log ve plan yok) mevcut genel davranış korunur.

    # Mevcut RAG + cache motoru (yeniden yazılmadı — import edildi).
    # NOT: ctx doluysa motor cevap cache'ini BYPASS eder (kişisel veri paylaşılmaz).
    r = chatbot._cevap_uret(req.message, req.yas_bandi, baby_context=ctx)

    # Her mesaj çifti kaydedilir (ürün özelliği: geçmiş). İçerik DB'de tutulur ama
    # uygulama LOGUNA yazılmaz (KVKK).
    # Kapsama telemetrisi (Faz 6.4): katman + skor SORU satırına yazılır — haftalık
    # "hangi sorular korpusta karşılıksız kaldı" analizi user mesajları üzerinden
    # yapılır (k3/k4). Cevap satırına da yazılır ki tek satırdan da okunabilsin.
    layer = r.get("retrieval_layer")
    top_score = r.get("top_score")
    db.add(ChatMessage(user_id=user.id, role="user", content=req.message,
                       cached=False, retrieval_layer=layer, top_score=top_score))
    db.add(ChatMessage(user_id=user.id, role="assistant",
                       content=r["cevap"], cached=r["cache_hit"],
                       retrieval_layer=layer, top_score=top_score))
    db.commit()

    # KVKK: bebek verisi İÇERİĞİ loglanmaz — yalnız bağlamın eklenip eklenmediği.
    logger.info("chat: user=%s q_len=%d a_len=%d cached=%s katman=%s skor=%s bebek=%s",
                user.id, len(req.message), len(r["cevap"]), r["cache_hit"],
                layer, f"{top_score:.3f}" if top_score is not None else "-",
                "var" if ctx else "yok")

    sources = None
    if r["kaynaklar"]:
        sources = [ChatSource(**s) for s in r["kaynaklar"]]
    return ChatResp(answer=r["cevap"], cached=r["cache_hit"], sources=sources,
                    retrieval_layer=layer)
