"""
Topluluk v2 (2026-10-01) — kaydetme + topluluk bildirim defteri.

community_bookmarks: kullanıcının kaydettiği konular (UNIQUE kullanıcı+konu).

topluluk_bildirimleri: her topluluk bildirim OLAYININ tek satırı.
  • anahtar UNIQUE (kullanıcı başına) → aynı olay iki kez gönderilmez
    (ör. "cevap:<reply_id>", "faydali:<thread_id>:<zaman>", "ozet:<gün>").
  • durum: gonderildi | bekliyor (23:00–07:00 sessizliğinde kuyrukta) |
    ozetlendi (07:00 özetine girdi) | atlandi (günlük tavan / tercih kapalı).
  • Günlük tavan (kullanıcı başına 5) `gonderildi` satırları sayılarak uygulanır.
sent_notifications KULLANILMADI: o tablo plan_id'yi ZORUNLU tutuyor (plan
hatırlatmasının defteri); topluluk olayının planı yok.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class Bookmark(Base):
    __tablename__ = "community_bookmarks"
    __table_args__ = (UniqueConstraint("user_id", "thread_id",
                                       name="uq_community_bookmarks_user_thread"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("threads.id", ondelete="CASCADE"), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)


class ToplulukBildirimi(Base):
    __tablename__ = "topluluk_bildirimleri"
    __table_args__ = (UniqueConstraint("user_id", "anahtar",
                                       name="uq_topluluk_bildirimleri_user_anahtar"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    anahtar: Mapped[str] = mapped_column(String(120), nullable=False)
    tur: Mapped[str] = mapped_column(String(20), nullable=False)       # cevap | uzman | yanit | faydali | ozet
    durum: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    reply_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    olusturuldu_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
