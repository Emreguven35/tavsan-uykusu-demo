"""
silinen_sleep_logs — silinen uyku kayıtlarının arşivi (K18.3).

NEDEN AYRI TABLO: kopya temizliği anne verisini SİLİYOR. `notes` alanına not
düşmek işe yaramaz — silinen satırla birlikte o not da gider. Geri alınabilmesi
için satırın TAMAMI JSON olarak buraya kopyalanır; temizlik yanlış bir çifti
eşlerse veri kaybı değil, geri yükleme işidir.

Tablo yalnız bakım betiği tarafından YAZILIR; hiçbir API ucu okumaz.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class SilinenSleepLog(Base):
    __tablename__ = "silinen_sleep_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    # Silinen satırın ÖZGÜN kimliği. FK YOK: hedef satır artık yok.
    sleep_log_id: Mapped[uuid.UUID] = mapped_column(GUID, index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    baby_id: Mapped[uuid.UUID] = mapped_column(GUID, index=True, nullable=False)

    # Satırın tamamı (JSON metni). JSONB yerine Text: bu tablo sorgulanmıyor,
    # yalnız geri yükleme için okunuyor; şema değişse bile eski kayıt okunabilir.
    veri: Mapped[str] = mapped_column(Text, nullable=False)
    sebep: Mapped[str] = mapped_column(String(200), nullable=False)
    # Hangi kaydın LEHİNE silindi (geri yüklerken karşılaştırmak için).
    korunan_log_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    silindi_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
