"""
plans router — /api/v1/plans

POST /plans/generate: bebek profili → parameter_engine + plan_generator → JSONB.
POST /plans/adapt:   son 3 günün uyku kayıtlarına göre çizelgeyi kaydırır (Faz 6.1).
GET  /plans/today:   bugünün planı; yoksa en güncel planı bugüne adapte eder (lazy).
GET  /plans, GET /plans/{plan_date}: kullanıcının planlarını döndürür.

İŞ MANTIĞI BURADA DEĞİL: üretim/adaptasyon/lazy-adapt api/services/plan_service.py
içindedir — bildirim zamanlayıcısı da AYNI kod yolunu kullanır (Faz 6.6, kod
çiftlenmez). Bu router yalnız HTTP kabuğudur (auth, sahiplik, durum kodları).
"""
import logging
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user, get_owned_baby
from api.models import SleepPlan, User
from api.schemas.plan import PlanAdaptResp, PlanGenerateReq, PlanResp
from api.services import plan_adapter, plan_service

logger = logging.getLogger("tavsan.plans")
router = APIRouter(prefix="/plans", tags=["plans"])


@router.post("/generate", response_model=PlanResp, status_code=status.HTTP_201_CREATED)
def generate_plan(req: PlanGenerateReq, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    baby = get_owned_baby(req.baby_id, db, user)
    if baby.birth_date is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Plan için bebeğin doğum tarihi gereklidir")
    try:
        content = plan_service.generate_content(
            baby, req.profile_overrides, req.dogum_haftasi)
    except plan_service.PlanError as e:
        logger.warning("Plan üretimi başarısız (baby=%s): %s", baby.id, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Plan üretilemedi: {e}")

    today = datetime.now(timezone.utc).date()
    plan = plan_service.upsert_plan(db, user, baby, today, content)
    logger.info("Plan üretildi: plan_id=%s baby=%s (%s)",
                plan.id, baby.id, content["generated_with"])
    return plan


@router.post("/adapt", response_model=PlanAdaptResp)
def adapt_plan(baby_id: uuid.UUID = Query(...), db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    """Son 3 günün uyku kayıtlarına göre planı adapte et ve bugünün planı olarak kaydet.

    409: son 3 günde hiç kayıt yoksa ya da adapte edilecek bir plan yoksa."""
    baby = get_owned_baby(baby_id, db, user)
    today = datetime.now(timezone.utc).date()

    base_plan = plan_service.latest_plan(db, user, baby)
    if base_plan is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Adapte edilecek plan yok — önce POST /plans/generate çağırın")

    logs = plan_service.recent_logs(db, user, baby, today)
    if not logs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Son {plan_adapter.LOOKBACK_DAYS} günde uyku kaydı yok — "
                    "adaptasyon için önce uyku kaydı girin"))

    try:
        plan, result = plan_service.run_adaptation(db, user, baby, base_plan,
                                                   logs, today)
    except plan_service.PlanError as e:
        logger.warning("Yeniden üretim başarısız (baby=%s): %s", baby.id, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Plan yeniden üretilemedi: {e}")

    logger.info("Plan adapte edildi: plan=%s baby=%s adjusted=%s shift=%s regen=%s "
                "regression=%s", plan.id, baby.id, result["adjusted"],
                result["shift_minutes"], result["regenerate_required"],
                result["regression_detected"])
    return PlanAdaptResp(
        plan=plan,
        adjusted=result["adjusted"],
        shift_minutes=result["shift_minutes"],
        regenerate_required=result["regenerate_required"],
        regression_detected=result["regression_detected"],
        restart_program_suggested=result["restart_program_suggested"],
        reasons=result["reasons"],
    )


@router.get("/today", response_model=PlanResp)
def get_today_plan(baby_id: uuid.UUID = Query(...), db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Bugünün planı. Yoksa en güncel plan bugüne adapte edilip döndürülür (lazy).

    Mobil dashboard bunu çağırır. Bildirim zamanlayıcısı da AYNI servisi kullanır."""
    baby = get_owned_baby(baby_id, db, user)
    try:
        plan = plan_service.ensure_today_plan(db, user, baby)
    except plan_service.PlanError as e:
        logger.warning("Plan yeniden üretilemedi (baby=%s): %s", baby.id, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Plan yeniden üretilemedi: {e}")
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Bu bebek için henüz plan üretilmemiş")
    return plan


@router.get("", response_model=list[PlanResp])
def list_plans(db: Session = Depends(get_db), user: User = Depends(get_current_user),
               baby_id: uuid.UUID | None = Query(default=None)):
    q = db.query(SleepPlan).filter(SleepPlan.user_id == user.id)
    if baby_id is not None:
        q = q.filter(SleepPlan.baby_id == baby_id)
    return [plan_service.ensure_current_schema(db, p)
            for p in q.order_by(SleepPlan.created_at.desc()).all()]


@router.get("/{plan_date}", response_model=PlanResp)
def get_plan_by_date(plan_date: date, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user),
                     baby_id: uuid.UUID | None = Query(default=None)):
    q = db.query(SleepPlan).filter(SleepPlan.user_id == user.id,
                                   SleepPlan.plan_date == plan_date)
    if baby_id is not None:
        q = q.filter(SleepPlan.baby_id == baby_id)
    plan = q.order_by(SleepPlan.created_at.desc()).first()   # o günün en yenisi
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Bu tarihte plan bulunamadı")
    return plan_service.ensure_current_schema(db, plan)
