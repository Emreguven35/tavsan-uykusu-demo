"""
Topluluk dürüstlüğü (denetim B3) — kurgusal tohum hesaplarının içeriğini TEK
resmi hesaba taşı, uzman rozetini gerçek uzmana indir, tohum hesaplarını sil.

    python scripts/topluluk_resmi_hesap.py                   # yalnız rapor
    python scripts/topluluk_resmi_hesap.py --uygula          # uygula
    python scripts/topluluk_resmi_hesap.py --uygula --uzman-kalsin ilaydakani

NEDEN: topluluktaki 9 konunun 5'i, 34 yanıtın 25'i "Ayşe Anne", "Uzman Zeynep"
gibi KURGUSAL hesaplardandı; ikisi "uzman" rozeti taşıyordu. Beta anneleri
bunları gerçek anne/uzman sanıyordu. İçerik (değerli: sık sorulan sorular ve
cevapları) silinmez — "Tavşan Uykusu Ekibi" (rozet: Resmi) hesabına taşınır;
yazarı artık dürüst. Sonra tohum hesapları hesap silme akışıyla silinir.

UZMAN ROZETİ: yalnız `--uzman-kalsin` ile verilen takma adlarda KALIR (gerçek,
doğrulanmış uzman). Diğer herkesten kaldırılır; moderatörlük dokunulmaz.
`expert_replied` ve beğeni sayaçları gerçek verilerden YENİDEN hesaplanır.

Tohum hesabı ölçütü: e-posta `tavsan-seed-%@example.com`.
Prod'da konteyner içinde: railway ssh "cd /app && /opt/venv/bin/python scripts/topluluk_resmi_hesap.py"
"""
from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESMI_EPOSTA = "ekip@tavsan-uykusu.invalid"   # .invalid: teslim edilemez, giriş yok
RESMI_TAKMA_AD = "Tavşan Uykusu Ekibi"
TOHUM_DESENI = "tavsan-seed-%@example.com"


def resmi_hesap(db):
    """Resmi hesabı bul ya da kur. Parola kullanılamaz (rastgele, hash değil)."""
    from api.models import CommunityProfile, User
    u = db.query(User).filter(User.email == RESMI_EPOSTA).one_or_none()
    if u is None:
        u = User(email=RESMI_EPOSTA,
                 password_hash="!giris-yok!" + secrets.token_hex(16))
        db.add(u)
        db.flush()
    p = db.query(CommunityProfile).filter(CommunityProfile.user_id == u.id).one_or_none()
    if p is None:
        p = CommunityProfile(user_id=u.id, nickname=RESMI_TAKMA_AD, status="active",
                             post_count=0, is_official=True, is_expert=False,
                             is_moderator=False,
                             rules_accepted_at=datetime.now(timezone.utc))
        db.add(p)
        db.flush()
    p.is_official = True
    p.is_expert = False
    return u, p


def tasi(db, uygula: bool, uzman_kalsin: set[str]) -> dict:
    """Rapor sözlüğü döner. uygula=False → hiçbir şey yazılmaz (rollback)."""
    from sqlalchemy import func
    from api.models import CommunityProfile, Like, Reply, Thread, User
    from api.services import voice_temizlik

    tohumlar = db.query(User).filter(User.email.like(TOHUM_DESENI)).all()
    tohum_id = [u.id for u in tohumlar]
    rapor = {"tohum_hesap": len(tohumlar),
             "tohum_takma_adlar": sorted(
                 p.nickname for p in db.query(CommunityProfile)
                 .filter(CommunityProfile.user_id.in_(tohum_id)).all()) if tohum_id else []}

    resmi_u, resmi_p = resmi_hesap(db)
    konular = (db.query(Thread).filter(Thread.user_id.in_(tohum_id)).all()
               if tohum_id else [])
    yanitlar = (db.query(Reply).filter(Reply.user_id.in_(tohum_id)).all()
                if tohum_id else [])
    for t in konular:
        t.user_id = resmi_u.id
    for r in yanitlar:
        r.user_id = resmi_u.id
    rapor["tasinan_konu"] = len(konular)
    rapor["tasinan_yanit"] = len(yanitlar)
    resmi_p.post_count = (db.query(Thread).filter(Thread.user_id == resmi_u.id).count()
                          + db.query(Reply).filter(Reply.user_id == resmi_u.id).count())

    # Tohum beğenileri gerçek ilgi değil → silinir, sayaçlar yeniden hesaplanır.
    rapor["silinen_tohum_begeni"] = (db.query(Like).filter(Like.user_id.in_(tohum_id))
                                     .delete(synchronize_session=False)
                                     if tohum_id else 0)

    # Uzman rozeti: yalnız izin verilen gerçek uzmanlarda.
    kaldirilan = []
    for p in db.query(CommunityProfile).filter(CommunityProfile.is_expert.is_(True)).all():
        if p.nickname not in uzman_kalsin:
            p.is_expert = False
            kaldirilan.append(p.nickname)
    rapor["uzman_rozeti_kaldirilan"] = sorted(kaldirilan)
    rapor["uzman_rozeti_kalan"] = sorted(
        p.nickname for p in db.query(CommunityProfile)
        .filter(CommunityProfile.is_expert.is_(True)).all()
        if p.nickname in uzman_kalsin)
    db.flush()

    # Sayaçlar ve "uzman cevapladı" GERÇEK veriden.
    uzmanlar = {p.user_id for p in db.query(CommunityProfile)
                .filter(CommunityProfile.is_expert.is_(True)).all()}
    degisen_sayac = degisen_uzman_bayragi = 0
    for t in db.query(Thread).all():
        begeni = db.query(func.count(Like.id)).filter(
            Like.target_type == "thread", Like.target_id == t.id).scalar() or 0
        uzman_cevap = bool(uzmanlar) and db.query(Reply).filter(
            Reply.thread_id == t.id, Reply.status == "published",
            Reply.user_id.in_(uzmanlar)).first() is not None
        if t.like_count != begeni:
            t.like_count, degisen_sayac = begeni, degisen_sayac + 1
        if bool(t.expert_replied) != uzman_cevap:
            t.expert_replied, degisen_uzman_bayragi = uzman_cevap, degisen_uzman_bayragi + 1
    for r in db.query(Reply).all():
        begeni = db.query(func.count(Like.id)).filter(
            Like.target_type == "reply", Like.target_id == r.id).scalar() or 0
        if r.like_count != begeni:
            r.like_count, degisen_sayac = begeni, degisen_sayac + 1
    rapor["duzeltilen_begeni_sayaci"] = degisen_sayac
    rapor["duzeltilen_uzman_cevapladi"] = degisen_uzman_bayragi

    if not uygula:
        db.rollback()
        rapor["uygulandi"] = False
        return rapor

    db.commit()
    silinen = 0
    for u in db.query(User).filter(User.email.like(TOHUM_DESENI)).all():
        voice_temizlik.kullanici_seslerini_sil(db, u)
        db.delete(u)
        db.commit()
        silinen += 1
    rapor["silinen_tohum_hesap"] = silinen
    rapor["uygulandi"] = True
    return rapor


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from api.db import SessionLocal

    ap = argparse.ArgumentParser(description="Tohum içeriğini resmi hesaba taşı")
    ap.add_argument("--uygula", action="store_true")
    ap.add_argument("--uzman-kalsin", default="",
                    help="uzman rozeti KALACAK takma adlar (virgülle)")
    a = ap.parse_args()
    kalsin = {x.strip() for x in a.uzman_kalsin.split(",") if x.strip()}
    db = SessionLocal()
    try:
        rapor = tasi(db, a.uygula, kalsin)
    finally:
        db.close()
    for k, v in rapor.items():
        print(f"{k:28s}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
