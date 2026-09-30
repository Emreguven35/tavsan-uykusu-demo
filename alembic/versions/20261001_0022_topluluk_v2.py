"""topluluk v2: kategori, anonim, sabit konu, avatar, kaydetme, bildirim defteri

Revision ID: 0022_topluluk_v2
Revises: 0021_is_dayanikliligi
Create Date: 2026-10-01

Tasarım v2 desteği. Eski sütunlara DOKUNULMAZ (build <= 23 `category`'yi okuyup
yazıyor); v2 `kategori` yeni sütundur ve mevcut konular için başlık/metinden
sınıflandırılarak doldurulur (api.services.topluluk.kategori_siniflandir ile
aynı kural — migration uygulama koduna bağımlı olmasın diye burada kopyası).
"""
import re

from alembic import op
import sqlalchemy as sa

revision = "0022_topluluk_v2"
down_revision = "0021_is_dayanikliligi"
branch_labels = None
depends_on = None

from api.db.base import GUID as _GUID   # önceki migrasyonlarla aynı tip (PG: UUID)

_GUNDUZ = re.compile(r"gündüz|gunduz|şekerleme|sekerleme|kestirme|öğle|ogle|tek uyku|"
                     r"uyku sayısı|kısa uyku|kisa uyku|30 dakika", re.I)
_EGITIM = re.compile(r"eğitim|egitim|program|aşama|asama|yöntem|yontem|ağlat|aglat|"
                     r"uzaklaş|uzaklas|merdiven|kendi kendine", re.I)
_GECE = re.compile(r"gece|uyan|uyku düzen|uyku duzen", re.I)
_SIRA = (("gunduz_uykulari", _GUNDUZ), ("egitim", _EGITIM), ("gece_uyanmasi", _GECE))


def _kelime(m):
    for k, d in _SIRA:
        if d.search(m or ""):
            return k
    return None


def _siniflandir(category, title, body):
    """api.services.topluluk.kategori_siniflandir ile aynı: başlık önce."""
    if category == "beslenme":
        return "beslenme"
    b = _kelime(title)
    if b:
        return b
    if category == "uyku":
        return _kelime(body) or "egitim"
    return "diger"


def upgrade() -> None:
    op.add_column("threads", sa.Column("kategori", sa.String(length=20), nullable=False,
                                       server_default="diger"))
    op.create_index("ix_threads_kategori", "threads", ["kategori"])
    op.add_column("threads", sa.Column("anonim", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))
    op.add_column("threads", sa.Column("sabit", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))
    op.add_column("threads", sa.Column("faydali_bildirim_at", sa.DateTime(timezone=True),
                                       nullable=True))
    op.add_column("replies", sa.Column("anonim", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))
    op.add_column("replies", sa.Column("yanitlanan_id", _GUID, nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key("fk_replies_yanitlanan", "replies", "replies",
                              ["yanitlanan_id"], ["id"], ondelete="SET NULL")
    op.add_column("users", sa.Column("avatar", sa.String(length=10), nullable=True))

    op.create_table(
        "community_bookmarks",
        sa.Column("id", _GUID, primary_key=True),
        sa.Column("user_id", _GUID, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("thread_id", _GUID, sa.ForeignKey("threads.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("user_id", "thread_id", name="uq_community_bookmarks_user_thread"),
    )
    op.create_table(
        "topluluk_bildirimleri",
        sa.Column("id", _GUID, primary_key=True),
        sa.Column("user_id", _GUID, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("anahtar", sa.String(length=120), nullable=False),
        sa.Column("tur", sa.String(length=20), nullable=False),
        sa.Column("durum", sa.String(length=12), nullable=False, index=True),
        sa.Column("thread_id", _GUID, nullable=True),
        sa.Column("reply_id", _GUID, nullable=True),
        sa.Column("olusturuldu_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False, index=True),
        sa.UniqueConstraint("user_id", "anahtar", name="uq_topluluk_bildirimleri_user_anahtar"),
    )

    # Mevcut konuların v2 kategorisi (eski `category` korunur).
    bag = op.get_bind()
    satirlar = bag.execute(sa.text("SELECT id, category, title, body FROM threads")).fetchall()
    for id_, cat, title, body in satirlar:
        bag.execute(sa.text("UPDATE threads SET kategori = :k WHERE id = :i"),
                    {"k": _siniflandir(cat, title, body), "i": id_})


def downgrade() -> None:
    op.drop_table("topluluk_bildirimleri")
    op.drop_table("community_bookmarks")
    op.drop_column("users", "avatar")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("fk_replies_yanitlanan", "replies", type_="foreignkey")
    op.drop_column("replies", "yanitlanan_id")
    op.drop_column("replies", "anonim")
    op.drop_column("threads", "faydali_bildirim_at")
    op.drop_column("threads", "sabit")
    op.drop_column("threads", "anonim")
    op.drop_index("ix_threads_kategori", table_name="threads")
    op.drop_column("threads", "kategori")
