"""
threads — topluluk konusu (Faz T). Düz metin; iç içe cevap yok.

user_id ondelete=SET NULL: hesap silinince konu KALIR, yazarı "Silinmiş kullanıcı"
olarak render edilir (community_profiles CASCADE ile gitse de thread durur).
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base
from api.models._mixins import uuid_pk


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True)

    # uyku | beslenme | gelisim | anne_hali | oneri
    category: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    body: Mapped[str] = mapped_column(String(1000), nullable=False)
    # published | pending | hidden | removed
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="published", index=True)

    reply_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    like_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expert_replied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- Topluluk v2 (2026-10-01) -------------------------------------------
    # Tasarım v2 kategorisi: gece_uyanmasi | gunduz_uykulari | egitim | beslenme |
    # diger. `category` (eski 5 anahtar) DOKUNULMADAN durur: build <= 23 onu okuyup
    # yazıyor. Eski anahtarla açılan konuda bu alan başlık/metinden türetilir.
    kategori: Mapped[str] = mapped_column(String(20), nullable=False,
                                          default="diger", index=True)
    # Anonim paylaşım: yanıtta "Anonim anne"; GERÇEK yazar (user_id) moderasyon
    # için DB'de kalır.
    anonim: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # "Haftanın konusu" — liste yanıtında ayrı alanda döner (moderatör sabitler).
    sabit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Son "faydalı" toplu bildiriminin zamanı (3 saatte en fazla bir).
    faydali_bildirim_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
