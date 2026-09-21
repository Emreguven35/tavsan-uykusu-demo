"""Voice (ses) şemaları — mobil sözleşmesi."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class VoiceCloneResp(BaseModel):
    voiceId: str
    sampleUrl: str | None = None


class Progress(BaseModel):
    """Paket üretim ilerlemesi — mobil çember göstergesi."""
    done: int = 0
    total: int = 0


class VoiceStatusResp(BaseModel):
    # v2.3 "üret ve bırak": recording | cloning | generating | ready | released
    #                      | failed | none  (eski: pending | replaced)
    status: str
    # Paket ilerlemesi. `generating` sırasında done < total; `ready`/`released`
    # olduğunda done = üretilebilen içerik sayısı (kısmi başarıda < total).
    progress: Progress = Progress()
    # Dinlenmeye HAZIR içerik sayısı (depoda dosyası olan).
    hazir_icerik: int = 0
    # Kısmi başarıda hangi içeriklerin üretilemediği (insan okur).
    error: str | None = None
    # Ses ElevenLabs'ten ne zaman silindi. NULL + ready → silme başarısız,
    # günlük temizlik tekrar deneyecek.
    released_at: datetime | None = None
    voiceId: str | None = None
    sampleUrl: str | None = None
    created_at: datetime | None = None
    # --- Aylık klonlama hakkı (gizlilik politikası: ayda bir kez) -------------
    # Mobil "Sesi yenile" düğmesini can_clone=false iken kapatmalı ve
    # next_clone_available_at'i göstermeli. Sunucu sınırı zaten zorluyor (429),
    # bu alanlar kullanıcının duvara çarpmadan görmesi için.
    last_cloned_at: datetime | None = None
    can_clone: bool = True
    next_clone_available_at: datetime | None = None
    retry_after_days: int = 0


class StoryItem(BaseModel):
    id: str
    type: str                        # masal | ninni
    title: str
    duration_hint: str | None = None
    # v2.3 — bu içerik ANNENİN SESİYLE hazır mı?
    hazir: bool = False
    # Hazırsa 1 saatlik imzalı bağlantı; değilse None.
    audio_url: str | None = None
    # Mobilin basacağı durum metni.
    durum: str = "hazirlaniyor"      # hazir | hazirlaniyor | uretilemedi


class StoriesResp(BaseModel):
    masallar: list[StoryItem]
    ninniler: list[StoryItem]
    # Pakette OLMAYAN içerikler de listelenir (hazir=False) ki mobil katalogun
    # tamamını gösterebilsin; bu alan hangilerinin üretileceğini söyler.
    paket: str = "starter"
    paket_icerikleri: list[str] = []


class VoiceGenerateReq(BaseModel):
    voiceId: str = Field(min_length=1)
    text: str | None = Field(default=None, max_length=4000)
    storyId: str | None = None
    # Ses profili: 'masal' (yavaş anlatım — varsayılan) | 'sohbet' (normal hız).
    # Kalibrasyon için açık bırakıldı; mobil göndermezse masal kullanılır.
    profile: str | None = None

    @model_validator(mode="after")
    def _need_text_or_story(self):
        if not self.text and not self.storyId:
            raise ValueError("text veya storyId gereklidir")
        return self

    @model_validator(mode="after")
    def _profil_gecerli(self):
        from api.tts import SES_PROFILLERI          # döngüsel import önleme
        if self.profile is not None and self.profile not in SES_PROFILLERI:
            raise ValueError(
                f"profile geçersiz: {sorted(SES_PROFILLERI)} içinden biri olmalı")
        return self


class VoiceGenerateResp(BaseModel):
    """Şema DEĞİŞMEDİ (eski istemciler kırılmasın) ama anlamı değişti:
    v2.3'te bu uç ÜRETİM YAPMAZ, hazır dosyanın imzalı bağlantısını döner.
    `cached` her zaman True'dur — dosya zaten üretilmiştir."""
    audio_url: str
    cached: bool
    profile: str                     # üretimde kullanılan ses profili
