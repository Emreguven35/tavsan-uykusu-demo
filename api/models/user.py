"""users — kimlik. Supabase auth yerine kendi JWT auth'umuz (Faz 2)."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base, JSONBType
from api.models._mixins import TimestampMixin, uuid_pk

# Bildirim tercihleri varsayılanı (Faz 6.2) — ikisi de AÇIK.
DEFAULT_NOTIFICATION_PREFS: dict[str, bool] = {
    "plan_reminders": True,
    "daily_summary": True,
    "community_replies": True,        # Faz T: kendi konuna cevap gelince bildir
    # Topluluk v2 (2026-10-01). `community_replies` eski istemci için kalır;
    # ikisinden biri kapalıysa cevap bildirimi gitmez.
    "topluluk_cevap_bildirimi": True,
    "topluluk_faydali_bildirimi": True,
    # v2.7 (2026-10-03) — sabah sorusu + uyku dizisi. `plan_reminders` eski
    # istemci için kalır; PATCH ikisini eşler, ikisinden biri kapalıysa gitmez.
    "uyku_hatirlatma_bildirimi": True,
}


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # Bildirim tercihleri (Faz 6.2). nullable=True + kodda varsayılana düşme →
    # migration mevcut satırları BOZMAZ (geriye uyumlu).
    notification_prefs: Mapped[dict[str, Any] | None] = mapped_column(
        JSONBType, nullable=True, default=lambda: dict(DEFAULT_NOTIFICATION_PREFS))

    # Denetim B3 — annenin SON görülen uygulama sürümü (X-App-Version, ör.
    # "1.0.0+21"). Denetim raporu eski sürümdeki (build < 20) anneleri sayar:
    # bir hatanın "düzeltildi" sayılması annenin o sürümü kullanmasına bağlı.
    app_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    app_version_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # B5 — son veri dışa aktarma (GET /account/export 24 saatte bir).
    son_export_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Topluluk v2 — tavsan | ayi | kedi | civciv | tilki. NULL → kimlikten sabit
    # seçim (api.services.topluluk.avatar_of); PATCH /users/me ile değişir.
    avatar: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # "Sesimi sil" (2026-10-07) ses profilini siler; aylık kayıt hakkı profilin
    # last_cloned_at'inden hesaplandığı için o an BURADA saklanır — silme hakkı
    # ne verir ne alır (api/routers/voice.py _klon_durumu).
    ses_son_kayit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    # KVKK silme hakkı: kullanıcı silinince ilişkili tüm veriler cascade ile gider.
    babies = relationship("Baby", back_populates="user",
                          cascade="all, delete-orphan", passive_deletes=True)
    sleep_logs = relationship("SleepLog", back_populates="user",
                             cascade="all, delete-orphan", passive_deletes=True)
    sleep_plans = relationship("SleepPlan", back_populates="user",
                              cascade="all, delete-orphan", passive_deletes=True)
    subscriptions = relationship("Subscription", back_populates="user",
                                cascade="all, delete-orphan", passive_deletes=True)
    chat_messages = relationship("ChatMessage", back_populates="user",
                                cascade="all, delete-orphan", passive_deletes=True)
    voice_profiles = relationship("VoiceProfile", back_populates="user",
                                 cascade="all, delete-orphan", passive_deletes=True)
    refresh_tokens = relationship("RefreshToken", back_populates="user",
                                 cascade="all, delete-orphan", passive_deletes=True)
    push_tokens = relationship("PushToken", back_populates="user",
                              cascade="all, delete-orphan", passive_deletes=True)
    sent_notifications = relationship("SentNotification", back_populates="user",
                                     cascade="all, delete-orphan", passive_deletes=True)
