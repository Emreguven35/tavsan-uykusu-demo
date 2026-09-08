"""Anne topluluğu şemaları (Faz T) — mobil sözleşmesi."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

CATEGORY_PATTERN = "^(uyku|beslenme|gelisim|anne_hali|oneri)$"
CATEGORIES = ["uyku", "beslenme", "gelisim", "anne_hali", "oneri"]
TARGET_PATTERN = "^(thread|reply)$"
REASON_PATTERN = "^(spam|hakaret|tibbi_risk|reklam|uygunsuz|diger)$"

DELETED_NICKNAME = "Silinmiş kullanıcı"


# --- Profil ------------------------------------------------------------------
class ProfileCreateReq(BaseModel):
    nickname: str = Field(min_length=2, max_length=24)


class ProfileUpdateReq(BaseModel):
    nickname: str = Field(min_length=2, max_length=24)


class ProfileResp(BaseModel):
    id: uuid.UUID
    nickname: str
    status: str
    post_count: int
    is_expert: bool
    is_moderator: bool
    rules_accepted_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Kategoriler -------------------------------------------------------------
class CategoryItem(BaseModel):
    key: str
    thread_count: int


class CategoriesResp(BaseModel):
    categories: list[CategoryItem]


# --- Konu / cevap ------------------------------------------------------------
class ThreadCreateReq(BaseModel):
    category: str = Field(pattern=CATEGORY_PATTERN)
    title: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=1000)


class ReplyCreateReq(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


class ThreadListItem(BaseModel):
    id: uuid.UUID
    author_id: uuid.UUID | None      # engelleme için; hesap silinmişse null
    nickname: str
    is_expert: bool
    category: str
    title: str
    body_preview: str            # body ilk 140 karakter
    reply_count: int
    like_count: int
    expert_replied: bool
    liked_by_me: bool
    status: str                  # visible | hidden (hidden yalnız sahibine döner)
    last_activity_at: datetime
    created_at: datetime


class ThreadListResp(BaseModel):
    items: list[ThreadListItem]
    next_cursor: str | None = None      # None → son sayfa


class ReplyItem(BaseModel):
    id: uuid.UUID
    author_id: uuid.UUID | None      # engelleme için; hesap silinmişse null
    nickname: str
    is_expert: bool
    body: str
    like_count: int
    liked_by_me: bool
    status: str                  # visible | hidden (hidden yalnız sahibine döner)
    created_at: datetime


class ThreadDetailResp(BaseModel):
    id: uuid.UUID
    author_id: uuid.UUID | None
    nickname: str
    is_expert: bool
    category: str
    title: str
    body: str
    reply_count: int
    like_count: int
    expert_replied: bool
    liked_by_me: bool
    status: str                  # visible | hidden
    last_activity_at: datetime
    created_at: datetime
    replies: list[ReplyItem]
    replies_next_cursor: str | None = None


# --- Etkileşim ---------------------------------------------------------------
class LikeReq(BaseModel):
    target_type: str = Field(pattern=TARGET_PATTERN)
    target_id: uuid.UUID


class LikeResp(BaseModel):
    liked: bool
    like_count: int


class ReportReq(BaseModel):
    target_type: str = Field(pattern=TARGET_PATTERN)
    target_id: uuid.UUID
    reason: str = Field(pattern=REASON_PATTERN)
    note: str | None = Field(default=None, max_length=500)


class BlockReq(BaseModel):
    user_id: uuid.UUID


class BlockItem(BaseModel):
    blocked_user_id: uuid.UUID
    nickname: str
    created_at: datetime


class MessageResp(BaseModel):
    detail: str


# --- Moderatör ---------------------------------------------------------------
class ModReportItem(BaseModel):
    id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    reason: str
    note: str | None
    resolved: bool
    created_at: datetime
    content_status: str | None = None    # hedef içeriğin güncel durumu
    content_body: str | None = None      # moderatör görsün (kısaltılmış)


class ModReportsResp(BaseModel):
    reports: list[ModReportItem]


class ModActionReq(BaseModel):
    target_type: str = Field(pattern=TARGET_PATTERN)
    target_id: uuid.UUID
    action: str = Field(pattern="^(hide|restore|remove)$")


class ModUserReq(BaseModel):
    user_id: uuid.UUID
    action: str = Field(pattern="^(mute|unmute|ban|unban)$")
