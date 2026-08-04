"""
community_profiles — anne topluluğu takma ad + durum + rozet (Faz T).

Hesap silinince bu profil CASCADE ile gider (user_id ondelete=CASCADE); ancak
kullanıcının thread/reply'leri SİLİNMEZ (onlarda user_id SET NULL → "Silinmiş
kullanıcı" olarak render edilir). Böylece akış bozulmaz.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import GUID, Base
from api.models._mixins import TimestampMixin, uuid_pk


class CommunityProfile(Base, TimestampMixin):
    __tablename__ = "community_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"),
        unique=True, index=True, nullable=False)

    nickname: Mapped[str] = mapped_column(String(24), unique=True, index=True, nullable=False)
    # active | muted (24s gönderi yasağı) | banned
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    muted_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)          # status=muted iken bitiş
    post_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rules_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    is_expert: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)   # İlayda/uzman rozeti
    is_moderator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
