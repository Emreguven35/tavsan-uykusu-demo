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
from api.deps import get_current_user
from api.models import ChatMessage, User
from api.schemas.chat import ChatReq, ChatResp, ChatSource
from engine import chatbot

logger = logging.getLogger("tavsan.chat")
router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResp)
def chat(req: ChatReq, db: Session = Depends(get_db),
         user: User = Depends(get_current_user)):
    # Mevcut RAG + cache motoru (yeniden yazılmadı — import edildi).
    r = chatbot._cevap_uret(req.message, req.yas_bandi)

    # Her mesaj çifti kaydedilir (ürün özelliği: geçmiş). İçerik DB'de tutulur ama
    # uygulama LOGUNA yazılmaz (KVKK).
    db.add(ChatMessage(user_id=user.id, role="user", content=req.message, cached=False))
    db.add(ChatMessage(user_id=user.id, role="assistant",
                       content=r["cevap"], cached=r["cache_hit"]))
    db.commit()

    logger.info("chat: user=%s q_len=%d a_len=%d cached=%s",
                user.id, len(req.message), len(r["cevap"]), r["cache_hit"])

    sources = None
    if r["kaynaklar"]:
        sources = [ChatSource(**s) for s in r["kaynaklar"]]
    return ChatResp(answer=r["cevap"], cached=r["cache_hit"], sources=sources)
