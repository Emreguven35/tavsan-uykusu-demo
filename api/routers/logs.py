"""
logs router — /api/v1/logs

- POST /logs/batch: mobil SQLite sync-manager için toplu upsert. client_id ile
  idempotent: aynı (user_id, client_id) ikinci kez gelirse GÜNCELLENİR. client_id
  NULL ise idempotency'den MUAF — her zaman yeni kayıt olarak eklenir.
- GET /logs?from=&to=&baby_id=: tarih aralığı sorgusu (started_at'e göre).
- GET /logs/weekly-summary: haftalık agregasyon (mobil grafikleri tüketir).

Hepsi user_id scoped; baby_id kullanıcıya ait değilse o kayıt atlanır (skipped).
"""
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user
from api.models import Baby, SleepLog, User
from api.schemas.log import (
    BatchReq, BatchResult, DaySummary, SleepLogResp, WeeklySummaryResp,
)

router = APIRouter(prefix="/logs", tags=["logs"])


def _owned_baby_ids(db: Session, user: User) -> set:
    return {b.id for b in db.query(Baby.id).filter(Baby.user_id == user.id)}


@router.post("/batch", response_model=BatchResult)
def batch_upsert(req: BatchReq, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    owned = _owned_baby_ids(db, user)
    created = updated = skipped = 0
    out: list[SleepLog] = []

    for item in req.logs:
        if item.baby_id not in owned:            # başka kullanıcının bebeği → atla
            skipped += 1
            continue

        row = None
        # İdempotency yalnız client_id NULL DEĞİLKEN uygulanır (spec + kullanıcı notu).
        if item.client_id is not None:
            row = (db.query(SleepLog)
                   .filter(SleepLog.user_id == user.id,
                           SleepLog.client_id == item.client_id)
                   .one_or_none())

        if row is None:                          # yeni kayıt
            row = SleepLog(
                user_id=user.id, baby_id=item.baby_id, type=item.type,
                started_at=item.started_at, ended_at=item.ended_at,
                notes=item.notes, client_id=item.client_id,
            )
            db.add(row)
            created += 1
        else:                                    # mevcut → güncelle (idempotent)
            row.baby_id = item.baby_id
            row.type = item.type
            row.started_at = item.started_at
            row.ended_at = item.ended_at
            row.notes = item.notes
            updated += 1

        db.flush()                               # aynı batch'te sonraki aramalar görsün
        out.append(row)

    db.commit()
    for r in out:
        db.refresh(r)
    return BatchResult(created=created, updated=updated, skipped=skipped, logs=out)


@router.get("", response_model=list[SleepLogResp])
def list_logs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    baby_id: uuid.UUID | None = Query(default=None),
):
    q = db.query(SleepLog).filter(SleepLog.user_id == user.id)
    if baby_id is not None:
        q = q.filter(SleepLog.baby_id == baby_id)
    if from_ is not None:
        q = q.filter(SleepLog.started_at >= from_)
    if to is not None:
        q = q.filter(SleepLog.started_at <= to)
    return q.order_by(SleepLog.started_at.desc()).all()


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@router.get("/weekly-summary", response_model=WeeklySummaryResp)
def weekly_summary(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    baby_id: uuid.UUID = Query(...),
    week_start: date | None = Query(default=None),
):
    """Belirtilen bebeğin 7 günlük özeti. week_start verilmezse son 7 gün (bugün dahil).
    total uyku saati, gece uyanma/besleme sayısı ve gün bazında dağılım döner."""
    baby = db.get(Baby, baby_id)
    if baby is None or baby.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bebek bulunamadı")

    today = datetime.now(timezone.utc).date()
    start = week_start if week_start is not None else today - timedelta(days=6)
    end = start + timedelta(days=6)
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end, time.max, tzinfo=timezone.utc)

    rows = (db.query(SleepLog)
            .filter(SleepLog.user_id == user.id,
                    SleepLog.baby_id == baby_id,
                    SleepLog.started_at >= start_dt,
                    SleepLog.started_at <= end_dt)
            .all())

    # Gün bazında (started_at'in UTC tarihi) topla.
    buckets: dict[date, dict] = defaultdict(
        lambda: {"sleep_hours": 0.0, "naps": 0, "night_wakes": 0, "night_feeds": 0})
    for r in rows:
        d = _as_utc(r.started_at).date()
        b = buckets[d]
        if r.type in ("sleep", "nap") and r.ended_at is not None:
            hours = (_as_utc(r.ended_at) - _as_utc(r.started_at)).total_seconds() / 3600.0
            if hours > 0:
                b["sleep_hours"] += hours
            if r.type == "nap":
                b["naps"] += 1
        elif r.type == "night_wake":
            b["night_wakes"] += 1
        elif r.type == "feed":
            b["night_feeds"] += 1

    days: list[DaySummary] = []
    total_sleep = total_wakes = total_feeds = 0.0
    for i in range(7):
        d = start + timedelta(days=i)
        b = buckets.get(d, {"sleep_hours": 0.0, "naps": 0, "night_wakes": 0, "night_feeds": 0})
        days.append(DaySummary(
            date=d, sleep_hours=round(b["sleep_hours"], 2), naps=b["naps"],
            night_wakes=b["night_wakes"], night_feeds=b["night_feeds"]))
        total_sleep += b["sleep_hours"]
        total_wakes += b["night_wakes"]
        total_feeds += b["night_feeds"]

    return WeeklySummaryResp(
        baby_id=baby_id, from_date=start, to_date=end,
        total_sleep_hours=round(total_sleep, 2),
        total_night_wakes=int(total_wakes), total_night_feeds=int(total_feeds),
        days=days)
