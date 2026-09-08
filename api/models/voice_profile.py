"""voice_profiles — kullanıcının ElevenLabs klon sesi durumu (Faz 4 /voice)."""
import uuid

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    elevenlabs_voice_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sample_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    created_at: Mapped["object"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="voice_profiles")
