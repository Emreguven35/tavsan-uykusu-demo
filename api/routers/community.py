"""
community router — /api/v1/community/* (Faz T)

Metin tabanlı anne topluluğu. Hepsi Bearer korumalı. Moderasyon hattı
(api/services/moderation.py) K0→K3 burada devreye girer.

Kapsam DIŞI (v1): DM, görsel, profil sayfası, kullanıcı kategorisi, iç içe cevap.
"""
import base64
import logging
import uuid
from datetime import datetime, timezone

from fastapi import (APIRouter, BackgroundTasks, Depends, HTTPException, Query,
                     status)
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user
from api.models import (Block, Bookmark, CommunityProfile, Like, Reply, Report,
                        Thread, User)
from api.schemas.community import (
    CATEGORIES, DELETED_NICKNAME, BlockItem, BlockReq, CategoriesResp,
    CategoryItem, IcerikEngelReq, IcerikEngelResp, KATEGORI_PATTERN, BookmarkResp,
    KategoriItem, LikeReq, LikeResp,
    MessageResp, ModActionReq, ModReportItem, ModReportsResp, ModUserReq, PinReq,
    ProfileCreateReq, ProfileResp, ProfileUpdateReq, ReplyCreateReq, ReplyItem,
    ReportReq, ThreadCreateReq, ThreadDetailResp, ThreadListItem, ThreadListResp,
    Yazar)
from api.services import moderation, topluluk, topluluk_bildirim

logger = logging.getLogger("tavsan.community")
router = APIRouter(prefix="/community", tags=["community"])

PAGE_DEFAULT = 20
PAGE_MAX = 50


# ===========================================================================
# Yardımcılar
# ===========================================================================
def _profile(db: Session, user: User) -> CommunityProfile | None:
    return db.query(CommunityProfile).filter(
        CommunityProfile.user_id == user.id).one_or_none()


def _require_profile(db: Session, user: User) -> CommunityProfile:
    prof = _profile(db, user)
    if prof is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Topluluk profili yok — önce takma ad belirleyin")
    return prof


def _blocked_ids(db: Session, user: User) -> set:
    return {r[0] for r in db.query(Block.blocked_user_id).filter(
        Block.user_id == user.id)}


def _author(prof: CommunityProfile | None, user_id) -> tuple[str, bool]:
    """Yazar görünümü. Hesap silinmişse (user_id NULL veya profil yok) 'Silinmiş kullanıcı'."""
    if user_id is None or prof is None:
        return DELETED_NICKNAME, False
    return prof.nickname, bool(prof.is_expert)


def _rozet(prof: CommunityProfile | None, user_id) -> dict:
    """Rozet alanları. Silinmiş yazarda rozet yok."""
    if user_id is None or prof is None:
        return {"is_official": False, "badge": None}
    resmi = bool(getattr(prof, "is_official", False))
    return {"is_official": resmi,
            "badge": "resmi" if resmi else ("uzman" if prof.is_expert else None)}


def _resp_status(internal: str) -> str:
    """İç durum → mobil sözleşme durumu. published → visible; diğerleri aynen."""
    return "visible" if internal == "published" else internal


def _encode_cursor(dt: datetime, id_: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{dt.isoformat()}|{id_}".encode()).decode()


def _decode_cursor(cur: str | None):
    if not cur:
        return None
    try:
        raw = base64.urlsafe_b64decode(cur.encode()).decode()
        iso, id_ = raw.split("|", 1)
        return datetime.fromisoformat(iso), uuid.UUID(id_)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Geçersiz cursor")


def _liked_set(db: Session, user: User, target_type: str, ids: list) -> set:
    if not ids:
        return set()
    return {r[0] for r in db.query(Like.target_id).filter(
        Like.user_id == user.id, Like.target_type == target_type,
        Like.target_id.in_(ids))}


def _content_blocked(reason: str) -> HTTPException:
    """K0 içerik reddi — içerik KAYDEDİLMEZ."""
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                         detail={"code": "content_blocked", "reason": reason})


def _guard_posting(db: Session, prof: CommunityProfile) -> None:
    """muted/banned kullanıcı gönderemez (süre dolmuş mute otomatik kalkar)."""
    reason = moderation.posting_block_reason(prof)      # gerekirse prof.status'ü aktife çeker
    if reason is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail={"code": "posting_blocked", "reason": reason})


# ===========================================================================
# Profil
# ===========================================================================
def _profil_yaniti(prof: CommunityProfile, user: User) -> ProfileResp:
    resp = ProfileResp.model_validate(prof)
    resp.badge = _rozet(prof, user.id)["badge"]
    resp.yazar = Yazar(**topluluk.yazar(prof, user.id, user.avatar, False))
    return resp


@router.get("/profile", response_model=ProfileResp)
def get_profile(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _profil_yaniti(_require_profile(db, user), user)


@router.post("/profile", response_model=ProfileResp, status_code=status.HTTP_201_CREATED)
def create_profile(req: ProfileCreateReq, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    if _profile(db, user) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Topluluk profili zaten var")
    nick = req.nickname.strip()
    if moderation.check_content(nick) is not None:      # takma ad da K0'dan geçer
        raise _content_blocked("hakaret")
    prof = CommunityProfile(user_id=user.id, nickname=nick,
                            rules_accepted_at=datetime.now(timezone.utc))
    db.add(prof)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Bu takma ad kullanılıyor")
    db.refresh(prof)
    return _profil_yaniti(prof, user)


@router.patch("/profile", response_model=ProfileResp)
def update_profile(req: ProfileUpdateReq, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    prof = _require_profile(db, user)
    nick = req.nickname.strip()
    if moderation.check_content(nick) is not None:
        raise _content_blocked("hakaret")
    prof.nickname = nick
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Bu takma ad kullanılıyor")
    db.refresh(prof)
    return _profil_yaniti(prof, user)


# ===========================================================================
# Kategoriler
# ===========================================================================
@router.get("/categories", response_model=CategoriesResp)
def categories(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Eski `categories` (build <= 23) + v2 `kategoriler` [{key, ad, konu_sayisi}]."""
    counts = dict(db.query(Thread.category, func.count(Thread.id))
                  .filter(Thread.status == "published")
                  .group_by(Thread.category).all())
    yeni = dict(db.query(Thread.kategori, func.count(Thread.id))
                .filter(Thread.status == "published")
                .group_by(Thread.kategori).all())
    return CategoriesResp(
        categories=[CategoryItem(key=c, thread_count=int(counts.get(c, 0)))
                    for c in CATEGORIES],
        kategoriler=[KategoriItem(key=k, ad=ad, konu_sayisi=int(yeni.get(k, 0)))
                     for k, ad in topluluk.KATEGORILER])


# ===========================================================================
# Konular — ortak görünüm
# ===========================================================================
def _kayitli_set(db: Session, user: User, ids: list) -> set:
    if not ids:
        return set()
    return {r[0] for r in db.query(Bookmark.thread_id).filter(
        Bookmark.user_id == user.id, Bookmark.thread_id.in_(ids))}


def _thread_q(db: Session, user: User):
    """Konu + yazar profili + yazarın avatar seçimi; görünürlük kuralıyla."""
    blocked = _blocked_ids(db, user)
    q = (db.query(Thread, CommunityProfile, User.avatar)
         .outerjoin(CommunityProfile, CommunityProfile.user_id == Thread.user_id)
         .outerjoin(User, User.id == Thread.user_id)
         # published herkese; hidden YALNIZ sahibine; removed hiç kimseye.
         .filter(or_(Thread.status == "published",
                     and_(Thread.status == "hidden", Thread.user_id == user.id))))
    if blocked:                              # engellenenlerin konuları gizli (NULL yazar kalır)
        q = q.filter(or_(Thread.user_id.is_(None), Thread.user_id.notin_(blocked)))
    return q


def _madde(t: Thread, prof, avatar_secimi, user: User, liked: set,
           kayitli: set) -> ThreadListItem:
    anonim = bool(t.anonim)
    return ThreadListItem(
        # Anonim konuda yazar kimliği HİÇ dönmez (moderasyon için DB'de durur).
        id=t.id, author_id=None if anonim else t.user_id,
        nickname=topluluk.eski_takma_ad(prof, t.user_id, anonim),
        is_expert=False if anonim else bool(prof is not None and prof.is_expert),
        **(_rozet(None, None) if anonim else _rozet(prof, t.user_id)),
        category=t.category, title=t.title, body_preview=t.body[:140],
        reply_count=t.reply_count, like_count=t.like_count,
        expert_replied=t.expert_replied, liked_by_me=t.id in liked,
        status=_resp_status(t.status), last_activity_at=t.last_activity_at,
        created_at=t.created_at,
        kategori=t.kategori, uzman_cevapladi=t.expert_replied,
        faydali_sayisi=t.like_count, cevap_sayisi=t.reply_count,
        kaydedildi_mi=t.id in kayitli, anonim=anonim, benim=t.user_id == user.id,
        is_mine=t.user_id == user.id,
        yazar=Yazar(**topluluk.yazar(prof, t.user_id, avatar_secimi, anonim)))


def _maddeler(db: Session, user: User, rows) -> list[ThreadListItem]:
    ids = [t.id for t, _p, _a in rows]
    liked, kayitli = _liked_set(db, user, "thread", ids), _kayitli_set(db, user, ids)
    return [_madde(t, p, a, user, liked, kayitli) for t, p, a in rows]


def _detay(t: Thread, ust: ThreadListItem, replies: list, rnext) -> ThreadDetailResp:
    alanlar = {k: v for k, v in ust.model_dump().items()
               if k in ThreadDetailResp.model_fields}
    return ThreadDetailResp(**alanlar, body=t.body, replies=replies,
                            replies_next_cursor=rnext)


# ===========================================================================
# Konular
# ===========================================================================
@router.get("/threads", response_model=ThreadListResp)
def list_threads(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                 category: str | None = Query(default=None),
                 kategori: str | None = Query(default=None, pattern=KATEGORI_PATTERN),
                 filtre: str | None = Query(default=None, pattern="^(uzman|benim)$"),
                 q: str | None = Query(default=None, max_length=100),
                 cursor: str | None = Query(default=None),
                 limit: int = Query(default=PAGE_DEFAULT, ge=1, le=PAGE_MAX)):
    """Konu listesi. v2 filtreleri: kategori, filtre=uzman|benim, q (metin).

    Sabitlenmiş "haftanın konusu" `haftanin_konusu`nda döner, items'ta tekrar
    etmez. Eski `category` filtresi eski anahtarla çalışmaya devam eder."""
    sorgu = _thread_q(db, user)
    if category is not None:
        sorgu = sorgu.filter(Thread.category == category)
    if kategori is not None:
        sorgu = sorgu.filter(Thread.kategori == kategori)
    if filtre == "uzman":
        sorgu = sorgu.filter(Thread.expert_replied.is_(True))
    elif filtre == "benim":
        sorgu = sorgu.filter(Thread.user_id == user.id)
    if q and q.strip():
        desen = f"%{q.strip()}%"
        sorgu = sorgu.filter(or_(Thread.title.ilike(desen), Thread.body.ilike(desen)))

    sabit_row = (_thread_q(db, user).filter(Thread.sabit.is_(True),
                                            Thread.status == "published")
                 .order_by(Thread.created_at.desc()).first())
    if sabit_row is not None:
        sorgu = sorgu.filter(Thread.id != sabit_row[0].id)

    cur = _decode_cursor(cursor)
    if cur is not None:
        cdt, cid = cur
        sorgu = sorgu.filter(or_(Thread.last_activity_at < cdt,
                                 and_(Thread.last_activity_at == cdt, Thread.id < cid)))
    rows = (sorgu.order_by(Thread.last_activity_at.desc(), Thread.id.desc())
            .limit(limit + 1).all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = _maddeler(db, user, rows)
    next_cursor = None
    if has_more and items:
        last = rows[-1][0]
        next_cursor = _encode_cursor(last.last_activity_at, last.id)
    return ThreadListResp(
        items=items, next_cursor=next_cursor,
        haftanin_konusu=(_maddeler(db, user, [sabit_row])[0] if sabit_row else None))


def _cevap(r: Reply, rp, avatar_secimi, user: User, rliked: set) -> ReplyItem:
    anonim = bool(r.anonim)
    return ReplyItem(
        id=r.id, author_id=None if anonim else r.user_id,
        nickname=topluluk.eski_takma_ad(rp, r.user_id, anonim),
        is_expert=False if anonim else bool(rp is not None and rp.is_expert),
        **(_rozet(None, None) if anonim else _rozet(rp, r.user_id)),
        body=r.body, like_count=r.like_count, liked_by_me=r.id in rliked,
        status=_resp_status(r.status), created_at=r.created_at,
        faydali_sayisi=r.like_count, anonim=anonim, benim=r.user_id == user.id,
        is_mine=r.user_id == user.id,
        yanitlanan_cevap_id=r.yanitlanan_id,
        yazar=Yazar(**topluluk.yazar(rp, r.user_id, avatar_secimi, anonim)))


@router.get("/threads/{thread_id}", response_model=ThreadDetailResp)
def get_thread(thread_id: uuid.UUID, db: Session = Depends(get_db),
               user: User = Depends(get_current_user),
               cursor: str | None = Query(default=None),
               limit: int = Query(default=PAGE_DEFAULT, ge=1, le=PAGE_MAX)):
    t = db.get(Thread, thread_id)
    # published herkese; hidden yalnız sahibine; removed/pending hiç kimseye → 404.
    gorunur = t is not None and (
        t.status == "published" or (t.status == "hidden" and t.user_id == user.id))
    if not gorunur:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Konu bulunamadı")
    tprof = _profile_of(db, t.user_id)
    tyazar = db.get(User, t.user_id) if t.user_id is not None else None
    ust = _madde(t, tprof, tyazar.avatar if tyazar else None, user,
                 _liked_set(db, user, "thread", [t.id]), _kayitli_set(db, user, [t.id]))

    blocked = _blocked_ids(db, user)
    rq = (db.query(Reply, CommunityProfile, User.avatar)
          .outerjoin(CommunityProfile, CommunityProfile.user_id == Reply.user_id)
          .outerjoin(User, User.id == Reply.user_id)
          # published herkese; hidden yalnız sahibine; removed hiç.
          .filter(Reply.thread_id == thread_id,
                  or_(Reply.status == "published",
                      and_(Reply.status == "hidden", Reply.user_id == user.id))))
    if blocked:
        rq = rq.filter(or_(Reply.user_id.is_(None), Reply.user_id.notin_(blocked)))
    cur = _decode_cursor(cursor)
    if cur is not None:
        cdt, cid = cur
        rq = rq.filter(or_(Reply.created_at > cdt,
                           and_(Reply.created_at == cdt, Reply.id > cid)))
    rrows = rq.order_by(Reply.created_at.asc(), Reply.id.asc()).limit(limit + 1).all()
    has_more = len(rrows) > limit
    rrows = rrows[:limit]
    rliked = _liked_set(db, user, "reply", [r.id for r, _p, _a in rrows])
    replies = [_cevap(r, rp, ra, user, rliked) for r, rp, ra in rrows]
    rnext = _encode_cursor(rrows[-1][0].created_at, rrows[-1][0].id) if (has_more and rrows) else None
    return _detay(t, ust, replies, rnext)


def _profile_of(db: Session, user_id) -> CommunityProfile | None:
    if user_id is None:
        return None
    return db.query(CommunityProfile).filter(CommunityProfile.user_id == user_id).one_or_none()


def _run_moderation_create(db: Session, prof: CommunityProfile, text_for_check: str,
                           background: BackgroundTasks, target_type: str,
                           target_id, review_text: str) -> None:
    """Ortak K0→K2 orkestrasyonu. K0 zaten çağrılmadıysa BURADA çağrılmaz; bu fonksiyon
    K1 (risk) + K2 (async Haiku planlama) yapar. K0 ve rate router'da önden çalışır."""
    flagged, reasons = moderation.risk_flags(text_for_check, prof.post_count)
    if flagged:
        # İçerik ANINDA published; Haiku arka planda değerlendirir (K2).
        background.add_task(moderation.review_async, target_type, target_id, review_text)
        logger.info("K1 flagged (%s) → K2 kuyruğa: %s %s", ",".join(reasons),
                    target_type, target_id)


def _anonim_izni(prof: CommunityProfile, istek: bool) -> bool:
    """Uzman ve resmi hesap anonim yazamaz (rozeti olan hesabın anonimliği yok)."""
    return bool(istek) and not prof.is_expert and not getattr(prof, "is_official", False)


@router.post("/threads", response_model=ThreadDetailResp, status_code=status.HTTP_201_CREATED)
def create_thread(req: ThreadCreateReq, background: BackgroundTasks,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prof = _require_profile(db, user)
    _guard_posting(db, prof)
    if req.category is None and req.kategori is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Kategori seçin (kategori ya da category)")
    limited, retry = moderation.check_rate(user.id, "thread")   # B4: konu sayacı (60 sn)
    if limited:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail={"code": "rate_limited", "reason": "cok_sik_konu"},
                            headers={"Retry-After": str(retry)})
    combined = f"{req.title}\n{req.body}"
    reason = moderation.check_content(combined)          # K0
    if reason is not None:
        raise _content_blocked(reason)

    title, body = req.title.strip(), req.body.strip()
    # v2 kategori: açıkça verildiyse o; yoksa eski anahtardan (başlık/metinle) türet.
    kategori = req.kategori or topluluk.kategori_siniflandir(req.category, title, body)
    # Eski `category` sütunu daima eski 5 anahtardan biri kalır (build <= 23 okur).
    category = (req.category if req.category in CATEGORIES
                else topluluk.eski_kategori(kategori))
    t = Thread(user_id=user.id, category=category, kategori=kategori, title=title,
               body=body, status="published", anonim=_anonim_izni(prof, req.anonim),
               last_activity_at=datetime.now(timezone.utc))
    db.add(t)
    prof.post_count += 1
    db.commit()
    db.refresh(t)
    _run_moderation_create(db, prof, combined, background, "thread", t.id, combined)   # K1+K2
    return _detay(t, _madde(t, prof, user.avatar, user, set(), set()), [], None)


@router.post("/threads/{thread_id}/replies", response_model=ReplyItem,
             status_code=status.HTTP_201_CREATED)
def create_reply(thread_id: uuid.UUID, req: ReplyCreateReq, background: BackgroundTasks,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prof = _require_profile(db, user)
    _guard_posting(db, prof)
    t = db.get(Thread, thread_id)
    if t is None or t.status != "published":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Konu bulunamadı")
    ust_cevap = None
    if req.yanitlanan_cevap_id is not None:
        ust_cevap = db.get(Reply, req.yanitlanan_cevap_id)
        if (ust_cevap is None or ust_cevap.thread_id != thread_id
                or ust_cevap.status != "published"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Yanıtlanan cevap bulunamadı")
    limited, retry = moderation.check_rate(user.id, "reply")   # B4: cevap sayacı (15 sn, ayrı)
    if limited:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail={"code": "rate_limited", "reason": "cok_sik_cevap"},
                            headers={"Retry-After": str(retry)})
    reason = moderation.check_content(req.body)          # K0
    if reason is not None:
        raise _content_blocked(reason)

    r = Reply(thread_id=thread_id, user_id=user.id, body=req.body.strip(),
              status="published", anonim=_anonim_izni(prof, req.anonim),
              yanitlanan_id=ust_cevap.id if ust_cevap is not None else None)
    db.add(r)
    t.reply_count += 1
    t.last_activity_at = datetime.now(timezone.utc)
    if prof.is_expert:
        t.expert_replied = True
    prof.post_count += 1
    db.commit()
    db.refresh(r)
    _run_moderation_create(db, prof, req.body, background, "reply", r.id, req.body)  # K1+K2

    # Topluluk bildirimleri (konu sahibi + yanıtlanan cevabın sahibi). Kendi
    # eylemine bildirim yok; cevap verenin kimliği bildirimde yazmaz.
    try:
        topluluk_bildirim.cevap_olayi(db, t, r, bool(prof.is_expert), ust_cevap)
    except Exception:
        logger.exception("Topluluk cevap bildirimi gönderilemedi")

    return _cevap(r, prof, user.avatar, user, set())


# ===========================================================================
# Kaydetme (v2)
# ===========================================================================
@router.post("/threads/{thread_id}/bookmark", response_model=BookmarkResp)
def bookmark_ekle(thread_id: uuid.UUID, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    t = db.get(Thread, thread_id)
    if t is None or t.status != "published":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Konu bulunamadı")
    if db.query(Bookmark).filter(Bookmark.user_id == user.id,
                                 Bookmark.thread_id == thread_id).first() is None:
        db.add(Bookmark(user_id=user.id, thread_id=thread_id))
        try:
            db.commit()
        except IntegrityError:               # yarış: aynı anda iki kaydet — sonuç aynı
            db.rollback()
    return BookmarkResp(kaydedildi=True)


@router.delete("/threads/{thread_id}/bookmark", response_model=BookmarkResp)
def bookmark_sil(thread_id: uuid.UUID, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    db.query(Bookmark).filter(Bookmark.user_id == user.id,
                              Bookmark.thread_id == thread_id).delete()
    db.commit()
    return BookmarkResp(kaydedildi=False)


@router.get("/bookmarks", response_model=ThreadListResp)
def bookmark_listesi(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Kaydedilen konular, en son kaydedilen önce (en fazla 100)."""
    rows = (_thread_q(db, user).join(Bookmark, and_(Bookmark.thread_id == Thread.id,
                                                    Bookmark.user_id == user.id))
            .order_by(Bookmark.created_at.desc()).limit(100).all())
    return ThreadListResp(items=_maddeler(db, user, rows), next_cursor=None)


@router.delete("/threads/{thread_id}", response_model=MessageResp)
def delete_thread(thread_id: uuid.UUID, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    t = db.get(Thread, thread_id)
    if t is None or t.user_id != user.id:        # yalnız sahibi; 404 → varlık sızmaz
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Konu bulunamadı")
    t.status = "removed"
    db.commit()
    return MessageResp(detail="Konu silindi")


@router.delete("/replies/{reply_id}", response_model=MessageResp)
def delete_reply(reply_id: uuid.UUID, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    r = db.get(Reply, reply_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cevap bulunamadı")
    if r.status == "published":
        t = db.get(Thread, r.thread_id)
        if t is not None and t.reply_count > 0:
            t.reply_count -= 1
    r.status = "removed"
    db.commit()
    return MessageResp(detail="Cevap silindi")


# ===========================================================================
# Beğeni / şikayet / engelleme
# ===========================================================================
def _target_obj(db: Session, target_type: str, target_id):
    return db.get(Thread if target_type == "thread" else Reply, target_id)


@router.post("/like", response_model=LikeResp)
def toggle_like(req: LikeReq, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    obj = _target_obj(db, req.target_type, req.target_id)
    if obj is None or obj.status != "published":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="İçerik bulunamadı")
    existing = db.query(Like).filter(
        Like.user_id == user.id, Like.target_type == req.target_type,
        Like.target_id == req.target_id).one_or_none()
    if existing is not None:                      # toggle OFF
        db.delete(existing)
        obj.like_count = max(0, obj.like_count - 1)
        liked = False
    else:                                          # toggle ON
        db.add(Like(user_id=user.id, target_type=req.target_type, target_id=req.target_id))
        obj.like_count += 1
        liked = True
    db.commit()
    return LikeResp(liked=liked, like_count=obj.like_count)


@router.post("/report", response_model=MessageResp)
def report(req: ReportReq, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    """K3: şikayet. Anında değerlendirilir.
      - Aynı şikayetçi aynı hedefi tekrar edemez (409).
      - 2 FARKLI kullanıcı şikayet ederse Haiku ne derse desin oto-hide.
      - Aksi halde senkron Haiku: izin=false & güven yeterli → hide."""
    obj = _target_obj(db, req.target_type, req.target_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="İçerik bulunamadı")

    db.add(Report(target_type=req.target_type, target_id=req.target_id,
                  reporter_id=user.id, reason=req.reason, note=req.note))
    try:
        db.commit()
    except IntegrityError:                       # aynı kullanıcı + aynı hedef
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Bu içeriği zaten şikayet ettiniz")

    # Kaç FARKLI kullanıcı şikayet etti?
    distinct_reporters = (db.query(func.count(func.distinct(Report.reporter_id)))
                          .filter(Report.target_type == req.target_type,
                                  Report.target_id == req.target_id).scalar() or 0)
    if distinct_reporters >= 2:
        moderation.hide_content(db, req.target_type, req.target_id, "report",
                                "coklu_sikayet")
        return MessageResp(detail="Şikayet alındı, içerik incelemeye alındı")

    # Tek şikayet → senkron Haiku değerlendirmesi.
    text = getattr(obj, "body", "") or ""
    verdict = moderation.classify(text)
    if moderation.should_hide(verdict):
        moderation.hide_content(db, req.target_type, req.target_id, "report",
                                verdict["sebep"])
    return MessageResp(detail="Şikayet alındı")


@router.post("/block", response_model=MessageResp)
def block_user(req: BlockReq, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    # B1: kendini engelleme reddedilir (mobil butonu gizlese de backend garantiler).
    if req.user_id == user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Kendinizi engelleyemezsiniz")
    # B2: var olmayan kullanıcı → 404 (önceden FK ihlali → 500 idi).
    if db.get(User, req.user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Kullanıcı bulunamadı")
    exists = db.query(Block).filter(Block.user_id == user.id,
                                    Block.blocked_user_id == req.user_id).one_or_none()
    if exists is None:
        db.add(Block(user_id=user.id, blocked_user_id=req.user_id))
        db.commit()
    return MessageResp(detail="Kullanıcı engellendi")


@router.post("/block/icerik", response_model=IcerikEngelResp)
def block_by_content(req: IcerikEngelReq, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """İçerikten engelle (2026-10-07). Mobil kullanıcı kimliği bilmiyordu ve
    takma adla /block deniyor, 422 alıyordu — engelleme HİÇ çalışmıyordu (App
    Store 1.2). Yazarı sunucu konu/cevap kaydından bulur.

    Anonim içerikten açılan engelde yanıt ve liste yazarı GÖSTERMEZ
    ("Anonim anne", kimlik yok). Görünürlük kuralı /block ile aynı: engellenen
    kişinin konuları listelerde, cevapları konu detayında gizlenir."""
    obj = _target_obj(db, req.target_type, req.target_id)
    if obj is None or obj.status == "removed":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="İçerik bulunamadı")
    if obj.user_id is None:                      # yazarın hesabı silinmiş
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Bu içeriğin yazarı artık uygulamada değil")
    if obj.user_id == user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Kendinizi engelleyemezsiniz")
    anonim = bool(getattr(obj, "anonim", False))
    engel = db.query(Block).filter(Block.user_id == user.id,
                                   Block.blocked_user_id == obj.user_id).one_or_none()
    if engel is None:
        engel = Block(user_id=user.id, blocked_user_id=obj.user_id,
                      anonim_kaynak=anonim)
        db.add(engel)
        db.commit()
        db.refresh(engel)
    if engel.anonim_kaynak:
        ad = topluluk.ANONIM_AD
    else:
        ad = topluluk.eski_takma_ad(_profile_of(db, obj.user_id), obj.user_id, False)
    return IcerikEngelResp(detail="Kullanıcı engellendi", engel_id=engel.id,
                           gorunen_ad=ad)


@router.delete("/block/kayit/{engel_id}", response_model=MessageResp)
def unblock_by_id(engel_id: uuid.UUID, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Engeli engel kaydının kimliğiyle kaldır (anonim kaynaklı engellerde
    kullanıcı kimliği istemciye verilmediği için gerekli). İdempotent."""
    row = db.query(Block).filter(Block.id == engel_id,
                                 Block.user_id == user.id).one_or_none()
    if row is not None:
        db.delete(row)
        db.commit()
    return MessageResp(detail="Engel kaldırıldı")


@router.delete("/block/{blocked_user_id}", response_model=MessageResp)
def unblock_user(blocked_user_id: uuid.UUID, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    row = db.query(Block).filter(Block.user_id == user.id,
                                 Block.blocked_user_id == blocked_user_id).one_or_none()
    if row is not None:
        db.delete(row)
        db.commit()
    return MessageResp(detail="Engel kaldırıldı")


@router.get("/blocks", response_model=list[BlockItem])
def list_blocks(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (db.query(Block, CommunityProfile)
            .outerjoin(CommunityProfile, CommunityProfile.user_id == Block.blocked_user_id)
            .filter(Block.user_id == user.id)
            .order_by(Block.created_at.desc()).all())
    return [BlockItem(blocked_user_id=None if b.anonim_kaynak else b.blocked_user_id,
                      nickname=(topluluk.ANONIM_AD if b.anonim_kaynak
                                else topluluk.eski_takma_ad(p, b.blocked_user_id, False)),
                      created_at=b.created_at, engel_id=b.id,
                      anonim=bool(b.anonim_kaynak)) for b, p in rows]


# ===========================================================================
# Moderatör
# ===========================================================================
def _require_moderator(db: Session, user: User) -> CommunityProfile:
    prof = _profile(db, user)
    if prof is None or not prof.is_moderator:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Moderatör yetkisi gerekli")
    return prof


@router.get("/mod/reports", response_model=ModReportsResp)
def mod_reports(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                resolved: bool = Query(default=False)):
    _require_moderator(db, user)
    rows = (db.query(Report).filter(Report.resolved == resolved)
            .order_by(Report.created_at.desc()).limit(200).all())
    items = []
    for rep in rows:
        obj = _target_obj(db, rep.target_type, rep.target_id)
        items.append(ModReportItem(
            id=rep.id, target_type=rep.target_type, target_id=rep.target_id,
            reason=rep.reason, note=rep.note, resolved=rep.resolved,
            created_at=rep.created_at,
            content_status=(obj.status if obj is not None else None),
            content_body=((obj.body[:300]) if obj is not None else None)))
    return ModReportsResp(reports=items)


@router.post("/mod/action", response_model=MessageResp)
def mod_action(req: ModActionReq, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    _require_moderator(db, user)
    obj = _target_obj(db, req.target_type, req.target_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="İçerik bulunamadı")
    if req.action == "hide":
        moderation.hide_content(db, req.target_type, req.target_id, "admin",
                                "mod_hide", actor_id=user.id, commit=False)
    elif req.action == "restore":
        obj.status = "published"
        moderation.log_action(db, req.target_type, req.target_id, "restore", "admin",
                              "mod_restore", actor_id=user.id)
    elif req.action == "remove":
        obj.status = "removed"
        moderation.log_action(db, req.target_type, req.target_id, "remove", "admin",
                              "mod_remove", actor_id=user.id)
    # ilgili şikayetleri çözüldü işaretle
    db.query(Report).filter(Report.target_type == req.target_type,
                            Report.target_id == req.target_id,
                            Report.resolved == False).update(  # noqa: E712
        {"resolved": True})
    db.commit()
    return MessageResp(detail=f"Uygulandı: {req.action}")


@router.post("/mod/pin", response_model=MessageResp)
def mod_pin(req: PinReq, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    """"Haftanın konusu"nu sabitle/kaldır. Aynı anda TEK sabit konu olur."""
    _require_moderator(db, user)
    t = db.get(Thread, req.thread_id)
    if t is None or t.status != "published":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Konu bulunamadı")
    if req.sabit:
        db.query(Thread).filter(Thread.sabit.is_(True), Thread.id != t.id).update(
            {"sabit": False})
    t.sabit = bool(req.sabit)
    db.commit()
    return MessageResp(detail="Haftanın konusu güncellendi" if req.sabit
                       else "Sabitleme kaldırıldı")


@router.post("/mod/user", response_model=MessageResp)
def mod_user(req: ModUserReq, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    _require_moderator(db, user)
    prof = db.query(CommunityProfile).filter(
        CommunityProfile.user_id == req.user_id).one_or_none()
    if prof is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Kullanıcı profili bulunamadı")
    if req.action == "mute":
        from datetime import timedelta
        prof.status = "muted"
        prof.muted_until = datetime.now(timezone.utc) + timedelta(hours=moderation.MUTE_HOURS)
    elif req.action == "unmute":
        prof.status = "active"
        prof.muted_until = None
    elif req.action == "ban":
        prof.status = "banned"
        prof.muted_until = None
    elif req.action == "unban":
        prof.status = "active"
    moderation.log_action(db, "thread", req.user_id, req.action, "admin",
                          f"mod_{req.action}", actor_id=user.id)
    db.commit()
    return MessageResp(detail=f"Uygulandı: {req.action}")
