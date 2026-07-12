"""
voice router — /api/v1/voice/*

- POST /clone: multipart ses (30sn) → ElevenLabs voice clone → {voiceId, sampleUrl};
  voice_profiles'a kaydeder + kısa bir örnek seslendirir (sampleUrl).
- GET /voice-status: kullanıcının klon durumu.
- GET /stories: masal/ninni kataloğu (5 masal + 3 ninni).
- POST /generate: {voiceId, text|storyId} → ElevenLabs flash v2.5 + mevcut TTS
  metin temizliği → {audio_url}. Ses data/audio_cache'e yazılır, /audio ile sunulur.

Hepsi auth korumalı. Dış servis hatası (key yok/kota) → anlamlı JSON + uygun kod.
"""
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from api import tts
from api.db import get_db
from api.deps import get_current_user
from api.models import User, VoiceProfile
from api.schemas.voice import (
    StoriesResp, VoiceCloneResp, VoiceGenerateReq, VoiceGenerateResp, VoiceStatusResp,
)
from api.services import voice as voice_svc

logger = logging.getLogger("tavsan.voice.router")
router = APIRouter(prefix="/voice", tags=["voice"])

MAX_CLONE_BYTES = 15 * 1024 * 1024        # ~30sn ses için bol; kötüye kullanımı sınırla
SAMPLE_TEXT = "Merhaba, ben senin sesinim. İyi geceler, tatlı rüyalar."


@router.post("/clone", response_model=VoiceCloneResp)
async def clone(audio: UploadFile = File(...), name: str = Form("Kullanıcı Sesi"),
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Ses dosyası boş")
    if len(data) > MAX_CLONE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="Ses dosyası çok büyük (max ~15MB)")

    r = voice_svc.clone_voice(name, data, audio.filename or "sample.mp3",
                              audio.content_type or "audio/mpeg")
    if not r.get("ok"):
        raise HTTPException(status_code=r.get("status", 502),
                            detail=r.get("error", "Ses klonlanamadı"))
    voice_id = r["voice_id"]

    # Kısa örnek seslendir (klonun çalıştığının kanıtı + mobil önizleme).
    sample = tts.voice_audio(voice_id, SAMPLE_TEXT)
    sample_url = sample.get("audio_url")

    profile = VoiceProfile(user_id=user.id, elevenlabs_voice_id=voice_id,
                           sample_url=sample_url, status="ready")
    db.add(profile)
    db.commit()
    logger.info("Voice clone tamam: user=%s voice_id=%s", user.id, voice_id)
    return VoiceCloneResp(voiceId=voice_id, sampleUrl=sample_url)


@router.get("/voice-status", response_model=VoiceStatusResp)
def voice_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    profile = (db.query(VoiceProfile).filter(VoiceProfile.user_id == user.id)
               .order_by(VoiceProfile.created_at.desc()).first())
    if profile is None:
        return VoiceStatusResp(status="none")
    return VoiceStatusResp(status=profile.status, voiceId=profile.elevenlabs_voice_id,
                           sampleUrl=profile.sample_url, created_at=profile.created_at)


@router.get("/stories", response_model=StoriesResp)
def stories(user: User = Depends(get_current_user)):
    """Masal/ninni kataloğu. Metinler /generate'e storyId ile verilir (yanıtta değil)."""
    cat = voice_svc.load_stories()
    return StoriesResp(masallar=cat.get("masallar", []),
                       ninniler=cat.get("ninniler", []))


@router.post("/generate", response_model=VoiceGenerateResp)
def generate(req: VoiceGenerateReq, user: User = Depends(get_current_user)):
    # storyId verildiyse katalogdan metni çöz; yoksa doğrudan text.
    if req.storyId:
        story = voice_svc.find_story(req.storyId)
        if story is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Masal/ninni bulunamadı")
        text = story["text"]
    else:
        text = req.text

    result = tts.voice_audio(req.voiceId, text)
    if result.get("audio_url") is None:
        # TTS anahtarı yok / upstream hata → ses üretilemedi.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Ses üretilemedi (TTS yapılandırması/kota)")
    logger.info("Voice generate: user=%s voice=%s cached=%s",
                user.id, req.voiceId, result["cached"])
    return VoiceGenerateResp(audio_url=result["audio_url"], cached=result["cached"])
