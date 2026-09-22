"""sleep_plans (user_id, baby_id, plan_date) tekillik kısıtı

Revision ID: 0015_plan_tekilligi
Revises: 0014_egitim_videolari
Create Date: 2026-09-22

NEDEN: `upsert_plan` "önce SEÇ, yoksa EKLE" deseniyle çalışıyordu ve tabloda
hiçbir tekillik kısıtı YOKTU. Aynı bebeğin bugünkü planını üç yol kurabiliyor
(GET /plans/today, bildirim turu, POST /logs/batch sonrası tazeleme); ikisi
aynı anda koşarsa AYNI GÜNE İKİ SATIR yazılıyor ve hata da vermiyordu —
sessiz çoğalma. Sonrasında `plan_for_date` iki satırdan birini seçiyor,
hangisi geldiği yazım sırasına kalıyordu.

Kısıt iki iş yapar: çoğalmayı DB seviyesinde durdurur ve
`INSERT ... ON CONFLICT DO UPDATE`'in tutunacağı dalı verir.

ÖNCE TEMİZLİK: kısıt eklenmeden varsa kopyalar ayıklanır — EN YENİ satır
(created_at, eşitse id) korunur, eskiler silinir. Üretimde 2026-09-22
taramasında kopya YOKTU (852 planda 0), bu yüzden temizlik pratikte boş
geçecek; yine de betik kendi başına doğru olsun diye duruyor.
"""
from alembic import op

revision = "0015_plan_tekilligi"
down_revision = "0014_egitim_videolari"
branch_labels = None
depends_on = None

KISIT = "uq_sleep_plans_user_baby_date"


def upgrade() -> None:
    bind = op.get_bind()
    # Kopyaları ayıkla: her (user, baby, gün) için en yeni satır kalsın.
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            """
            DELETE FROM sleep_plans s
            USING sleep_plans t
            WHERE s.user_id = t.user_id
              AND s.baby_id = t.baby_id
              AND s.plan_date = t.plan_date
              AND (s.created_at, s.id) < (t.created_at, t.id)
            """
        )
    else:                                  # sqlite (yerel/test)
        bind.exec_driver_sql(
            """
            DELETE FROM sleep_plans
            WHERE id NOT IN (
                SELECT id FROM (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY user_id, baby_id, plan_date
                               ORDER BY created_at DESC, id DESC) AS sira
                    FROM sleep_plans
                ) x WHERE x.sira = 1
            )
            """
        )
    # SQLite ALTER TABLE ADD CONSTRAINT bilmez; batch modu tabloyu yeniden
    # kurar. Üretim Postgres olduğu için orada düz ALTER koşar.
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("sleep_plans") as toplu:
            toplu.create_unique_constraint(
                KISIT, ["user_id", "baby_id", "plan_date"])
    else:
        op.create_unique_constraint(KISIT, "sleep_plans",
                                    ["user_id", "baby_id", "plan_date"])


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("sleep_plans") as toplu:
            toplu.drop_constraint(KISIT, type_="unique")
    else:
        op.drop_constraint(KISIT, "sleep_plans", type_="unique")
