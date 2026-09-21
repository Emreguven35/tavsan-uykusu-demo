"""
voice_audios — anne sesiyle ÜRETİLMİŞ masal/ninni dosyalarının kaydı.

"Üret ve bırak" modeli: klonlama bitince paket anında üretilip depoya yazılır,
sonra ElevenLabs'teki ses SİLİNİR. Anneler kendi depomuzdan dinler. ElevenLabs
hesabının 10 klon slotu 94+ kullanıcıya yetmiyordu; slot artık yalnız üretim
süresince (dakikalar) tutuluyor.

Her satır bir (ses profili, içerik) çiftidir; dosyanın kendisi depoda
`storage_path`te durur. UNIQUE (voice_profile_id, content_id): aynı içerik bir
ses için iki kez üretilmez — yeniden deneme mevcut satırı günceller.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class VoiceAudio(Base):
    __tablename__ = "voice_audios"
    __table_args__ = (
        UniqueConstraint("voice_profile_id", "content_id",
                         name="uq_voice_audios_profile_content"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    voice_profile_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("voice_profiles.id", ondelete="CASCADE"),
        index=True, nullable=False)

    # data/stories.json içindeki içerik kimliği (masal_*/ninni_*).
    content_id: Mapped[str] = mapped_column(String(80), nullable=False)
    # Depo yolu: voice-audio/{user_id}/{voice_profile_id}/{content_id}.mp3
    # Tam URL DEĞİL: imzalı bağlantı her istekte yeniden üretilir (1 saat ömür),
    # URL'i saklamak süresi dolmuş bir bağlantıyı kalıcılaştırırdı.
    storage_path: Mapped[str] = mapped_column(String(400), nullable=False)

    duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    profile = relationship("VoiceProfile", back_populates="audios")
