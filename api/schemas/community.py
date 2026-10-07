"""Anne topluluğu şemaları (Faz T) — mobil sözleşmesi."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# Eski 5 anahtar (build <= 23) VE v2 anahtarları kabul edilir.
CATEGORY_PATTERN = ("^(uyku|beslenme|gelisim|anne_hali|oneri|"
                    "gece_uyanmasi|gunduz_uykulari|egitim|diger)$")
KATEGORI_PATTERN = "^(gece_uyanmasi|gunduz_uykulari|egitim|beslenme|diger)$"
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
    # "Resmi" rozeti (Tavşan Uykusu Ekibi). `badge`: gösterilecek TEK rozet —
    # "resmi" | "uzman" | None; mobil iki bayrağı ayrı yorumlamak zorunda kalmaz.
    is_official: bool = False
    badge: str | None = None
    is_moderator: bool
    rules_accepted_at: datetime | None = None
    created_at: datetime
    # v2 — yazar görünümü (uzman: "Tavşan Uykusu" / "Uzman").
    yazar: "Yazar | None" = None

    model_config = {"from_attributes": True}


# --- Kategoriler -------------------------------------------------------------
class CategoryItem(BaseModel):
    key: str
    thread_count: int


class KategoriItem(BaseModel):
    """v2 kategori: gece_uyanmasi | gunduz_uykulari | egitim | beslenme | diger."""
    key: str
    ad: str
    konu_sayisi: int


class CategoriesResp(BaseModel):
    categories: list[CategoryItem]          # eski (build <= 23) — dokunulmaz
    kategoriler: list[KategoriItem] = []    # v2


# --- Konu / cevap ------------------------------------------------------------
class ThreadCreateReq(BaseModel):
    # Eski istemci `category` (eski anahtar) gönderir; v2 `kategori` gönderir.
    # İkisinden biri ZORUNLU (router doğrular).
    category: str | None = Field(default=None, pattern=CATEGORY_PATTERN)
    kategori: str | None = Field(default=None, pattern=KATEGORI_PATTERN)
    title: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=1000)
    anonim: bool = False


class ReplyCreateReq(BaseModel):
    body: str = Field(min_length=1, max_length=1000)
    anonim: bool = False
    # Cevaba cevap: aynı konudaki bir cevabın id'si ("Cevabınıza yanıt geldi").
    yanitlanan_cevap_id: uuid.UUID | None = None


class Yazar(BaseModel):
    """Mobil v2 yazar görünümü. Anonimde gorunen_ad "Anonim anne", avatar "anonim"."""
    gorunen_ad: str
    avatar: str                  # tavsan | ayi | kedi | civciv | tilki | anonim
    rozet: str | None = None     # "Uzman" | "Resmi" | None


class ThreadListItem(BaseModel):
    id: uuid.UUID
    author_id: uuid.UUID | None      # engelleme için; hesap silinmişse null
    nickname: str
    is_expert: bool
    # "Resmi" rozeti (Tavşan Uykusu Ekibi). `badge`: gösterilecek TEK rozet —
    # "resmi" | "uzman" | None; mobil iki bayrağı ayrı yorumlamak zorunda kalmaz.
    is_official: bool = False
    badge: str | None = None
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
    # --- v2 ---
    kategori: str = "diger"
    uzman_cevapladi: bool = False
    faydali_sayisi: int = 0      # = like_count ("faydalı")
    cevap_sayisi: int = 0        # = reply_count
    kaydedildi_mi: bool = False
    anonim: bool = False
    benim: bool = False          # konu bu kullanıcının mı (anonim olsa da)
    # `benim` ile aynı değer, İngilizce ad (2026-10-07). author_id'nin yerini
    # alacak: sahiplik için kullanıcı kimliğini istemciye vermek gerekmiyor.
    is_mine: bool = False
    yazar: Yazar | None = None


class ThreadListResp(BaseModel):
    items: list[ThreadListItem]
    next_cursor: str | None = None      # None → son sayfa
    # v2 — sabitlenmiş "haftanın konusu" (items'ta TEKRAR edilmez).
    haftanin_konusu: ThreadListItem | None = None


class ReplyItem(BaseModel):
    id: uuid.UUID
    author_id: uuid.UUID | None      # engelleme için; hesap silinmişse null
    nickname: str
    is_expert: bool
    # "Resmi" rozeti (Tavşan Uykusu Ekibi). `badge`: gösterilecek TEK rozet —
    # "resmi" | "uzman" | None; mobil iki bayrağı ayrı yorumlamak zorunda kalmaz.
    is_official: bool = False
    badge: str | None = None
    body: str
    like_count: int
    liked_by_me: bool
    status: str                  # visible | hidden (hidden yalnız sahibine döner)
    created_at: datetime
    # --- v2 ---
    faydali_sayisi: int = 0
    anonim: bool = False
    benim: bool = False
    is_mine: bool = False   # = benim
    yanitlanan_cevap_id: uuid.UUID | None = None
    yazar: Yazar | None = None


class ThreadDetailResp(BaseModel):
    id: uuid.UUID
    author_id: uuid.UUID | None
    nickname: str
    is_expert: bool
    # "Resmi" rozeti (Tavşan Uykusu Ekibi). `badge`: gösterilecek TEK rozet —
    # "resmi" | "uzman" | None; mobil iki bayrağı ayrı yorumlamak zorunda kalmaz.
    is_official: bool = False
    badge: str | None = None
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
    # --- v2 ---
    kategori: str = "diger"
    uzman_cevapladi: bool = False
    faydali_sayisi: int = 0
    cevap_sayisi: int = 0
    kaydedildi_mi: bool = False
    anonim: bool = False
    benim: bool = False
    is_mine: bool = False   # = benim
    yazar: Yazar | None = None


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


# --- v2: kaydetme, sabitleme, avatar --------------------------------------------
class BookmarkResp(BaseModel):
    kaydedildi: bool


class PinReq(BaseModel):
    thread_id: uuid.UUID
    sabit: bool = True


class AvatarUpdateReq(BaseModel):
    avatar: str = Field(pattern="^(tavsan|ayi|kedi|civciv|tilki)$")


class MeResp(BaseModel):
    id: uuid.UUID
    avatar: str                  # seçilmediyse kimlikten sabit seçim
    avatar_secildi: bool


ProfileResp.model_rebuild()
