"""
Topluluk v2 (2026-10-01) — kategori, yazar görünümü, avatar. Tek kaynak.

KALICI KURAL: kullanıcının gördüğü hiçbir metinde kişi adı ("İlayda") geçmez.
Uzman hesabı topluluğa DAİMA "Tavşan Uykusu" adıyla ve "Uzman" rozetiyle çıkar;
takma adı (DB'deki nickname) yanıtlara hiç yazılmaz.

GERİYE UYUM: build <= 23 eski 5 kategori anahtarını (`category`) okuyup yazıyor.
v2 `kategori` alanı ayrı sütundur; eski anahtarla gelen konu başlık/metinden
sınıflandırılır. Eski alanlar (nickname, is_expert, badge, category) aynen durur.
"""
from __future__ import annotations

import hashlib
import re

# --- Kategoriler ------------------------------------------------------------
KATEGORILER: list[tuple[str, str]] = [
    ("gece_uyanmasi", "Gece uyanması"),
    ("gunduz_uykulari", "Gündüz uykuları"),
    ("egitim", "Uyku eğitimi"),
    ("beslenme", "Beslenme"),
    ("diger", "Diğer"),
]
KATEGORI_ANAHTARLARI = [k for k, _ in KATEGORILER]
KATEGORI_ADI = dict(KATEGORILER)
ESKI_KATEGORILER = ["uyku", "beslenme", "gelisim", "anne_hali", "oneri"]
# v2 → eski (eski istemcinin rozeti/filtresi için): uyku alt başlıkları "uyku".
YENI_ESKI = {"gece_uyanmasi": "uyku", "gunduz_uykulari": "uyku", "egitim": "uyku",
             "beslenme": "beslenme", "diger": "oneri"}

_GUNDUZ = re.compile(r"gündüz|gunduz|şekerleme|sekerleme|kestirme|öğle uyku|ogle uyku|"
                     r"uyku sayısı|kısa uyku|kisa uyku|30 dakika", re.I)
_EGITIM = re.compile(r"eğitim|egitim|program|aşama|asama|yöntem|yontem|ağlat|aglat|"
                     r"uzaklaş|uzaklas|merdiven|kendi kendine", re.I)
_GECE = re.compile(r"gece|uyan|uyku düzen|uyku duzen", re.I)


def kategori_siniflandir(category: str | None, title: str = "", body: str = "") -> str:
    """Eski ya da v2 anahtar → v2 kategori. v2 anahtar olduğu gibi döner."""
    if category in KATEGORI_ADI:
        return category
    if category == "beslenme":
        return "beslenme"
    if category == "uyku":
        metin = f"{title} {body}"
        if _GUNDUZ.search(metin):
            return "gunduz_uykulari"
        if _EGITIM.search(metin):
            return "egitim"
        if _GECE.search(metin):
            return "gece_uyanmasi"
        return "egitim"              # genel uyku sorusu en çok eğitim sürecine ait
    return "diger"                   # gelisim | anne_hali | oneri | bilinmeyen


def eski_kategori(kategori: str) -> str:
    return YENI_ESKI.get(kategori, "oneri")


# --- Avatar -------------------------------------------------------------------
AVATARLAR = ["tavsan", "ayi", "kedi", "civciv", "tilki"]
AVATAR_PATTERN = "^(" + "|".join(AVATARLAR) + ")$"
ANONIM_AVATAR = "anonim"


def avatar_of(user_id, secilen: str | None = None) -> str:
    """Kullanıcının avatarı: seçtiyse o, yoksa kimliğinden SABİT seçim."""
    if secilen in AVATARLAR:
        return secilen
    h = int(hashlib.md5(str(user_id).encode()).hexdigest(), 16)
    return AVATARLAR[h % len(AVATARLAR)]


# --- Yazar görünümü ------------------------------------------------------------
MARKA_ADI = "Tavşan Uykusu"
UZMAN_ROZETI = "Uzman"
RESMI_ROZETI = "Resmi"
ANONIM_AD = "Anonim anne"
SILINMIS_AD = "Silinmiş kullanıcı"


def yazar(prof, user_id, avatar_secimi: str | None, anonim: bool) -> dict:
    """{gorunen_ad, avatar, rozet} — mobil birebir bunu gösterir.

    Anonim içerikte uzman/resmi hesap da olsa yazar gizlenir (anonimlik yalnız
    anne hesabında anlamlı; uzman anonim yazamaz — router bunu engeller)."""
    if anonim:
        return {"gorunen_ad": ANONIM_AD, "avatar": ANONIM_AVATAR, "rozet": None}
    if user_id is None or prof is None:
        return {"gorunen_ad": SILINMIS_AD, "avatar": ANONIM_AVATAR, "rozet": None}
    if getattr(prof, "is_expert", False):
        return {"gorunen_ad": MARKA_ADI, "avatar": "tavsan", "rozet": UZMAN_ROZETI}
    if getattr(prof, "is_official", False):
        return {"gorunen_ad": prof.nickname, "avatar": "tavsan", "rozet": RESMI_ROZETI}
    return {"gorunen_ad": prof.nickname, "avatar": avatar_of(user_id, avatar_secimi),
            "rozet": None}


def eski_takma_ad(prof, user_id, anonim: bool) -> str:
    """Eski `nickname` alanı (build <= 23). Uzman adı burada da gizlenir."""
    return yazar(prof, user_id, None, anonim)["gorunen_ad"]
