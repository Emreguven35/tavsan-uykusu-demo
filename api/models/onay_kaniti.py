"""
onay_kanitlari — silinmiş hesapların KVKK onay İSPATI (2026-10-07).

Hesap silinince `consents` satırları (CASCADE) silinirdi ve "bu kişi hangi metnin
hangi sürümüne ne zaman onay verdi" sorusu cevapsız kalırdı (ispat yükü veri
sorumlusunda). Hesap silmede onaylar buraya TAŞINIR:

  • kullanıcı kimliği YOK, e-posta YOK — yalnız e-postanın tuzlu özeti
    (HMAC-SHA256, kvkk.eposta_ozeti): bir talep geldiğinde aynı e-postanın
    özeti hesaplanıp eşlenebilir, özetten e-posta geri çıkarılamaz;
  • HESAP SİLİNDİKTEN 10 YIL SONRA silinir (gece temizliği, api/services/saklama.py).
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base
from api.models._mixins import uuid_pk

SAKLAMA_YIL = 10


class OnayKaniti(Base):
    __tablename__ = "onay_kanitlari"

    id: Mapped[uuid.UUID] = uuid_pk()
    tur: Mapped[str] = mapped_column(String(30), nullable=False)
    metin_surumu: Mapped[str] = mapped_column(String(40), nullable=False)
    onay: Mapped[bool] = mapped_column(Boolean, nullable=False)
    zaman: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kaynak: Mapped[str] = mapped_column(String(20), nullable=False)
    eposta_ozeti: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hesap_silindi_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True)
