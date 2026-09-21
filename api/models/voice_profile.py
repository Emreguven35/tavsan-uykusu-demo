"""
voice_profiles — kullanıcının klon sesi ve ses paketi durumu.

"ÜRET VE BIRAK" (v2.3): ElevenLabs'te klon sesi KALICI TUTULMAZ. Akış —
  recording → cloning → generating → ready(released)
Klonlama biter bitmez paket üretilir, depoya yazılır ve ElevenLabs'teki ses
SİLİNİR (`elevenlabs_voice_id` NULL olur, `released_at` dolar). Sebep: hesabın
10 klon slotu var, 94+ kullanıcı; slot artık yalnız üretim süresince tutuluyor.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    # Üretim BİTİNCE NULL olur — ses ElevenLabs'ten silinir (slot geri döner).
    # NULL olması "ses kayboldu" DEĞİL, "paket hazır, kaynak serbest" demektir.
    elevenlabs_voice_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sample_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # recording  : kayıt alınıyor (mobil tarafı; backend bu durumu yazmaz)
    # cloning    : ElevenLabs'e gönderildi, voice_id bekleniyor
    # generating : paket üretiliyor (progress_done/progress_total)
    # ready      : paket dinlenebilir (ses ElevenLabs'ten silinmiş olabilir)
    # released   : paket hazır ve ses KESİN silindi (ready'nin kesinleşmiş hâli)
    # failed     : üretim %50'nin altında kaldı, hak iade edildi
    # Eski değerler (pending/replaced) KORUNUR: mevcut satırlar geçerli kalsın.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    # Paket ilerlemesi — mobil çember göstergesi bunu okur.
    progress_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Ses ElevenLabs'ten silindiği an. NULL + status=ready → silme başarısız
    # olmuş demektir; günlük temizlik işi tekrar dener (Faz 4.1).
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    # Kısmi başarıda hangi içeriklerin üretilemediği + sebebi (insan okusun diye
    # düz metin). Başarılı üretimde NULL.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped["object"] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Aylık klonlama limitinin kaynağı (gizlilik politikası: "ayda bir kez
    # yenilenebilir"). Politika yazılıydı ama kodda ZORLANMIYORDU — kullanıcı
    # istediği kadar klon açabiliyordu (ElevenLabs slotu + ücret + biyometrik
    # veri birikmesi). ESKİ SATIRLARDA NULL: limit hesabı COALESCE ile
    # created_at'e düşer (bkz. voice router `_son_klonlama`), böylece mevcut
    # kullanıcılara sessizce fazladan hak doğmaz.
    last_cloned_at: Mapped["object | None"] = mapped_column(
        DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="voice_profiles")
    audios = relationship("VoiceAudio", back_populates="profile",
                          cascade="all, delete-orphan")
