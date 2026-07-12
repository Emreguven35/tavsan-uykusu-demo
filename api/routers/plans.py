"""
plans router — /api/v1/plans

POST /plans/generate: bebek profili (+opsiyonel ek profil + son loglar bağlamı) →
mevcut parameter_engine (deterministik sayısal parametreler) + plan_generator
(Claude "yazar" / API yoksa deterministik fallback) → JSONB olarak kaydeder.
GET /plans, GET /plans/{plan_date}: kullanıcının planlarını döndürür.

Mevcut engine modülleri AYNEN import edilir (kod çiftlenmez).
"""
import logging
import os
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user, get_owned_baby
from api.models import SleepPlan, User
from api.schemas.plan import PlanGenerateReq, PlanResp
from engine import plan_generator
from engine.parameter_engine import parametre_uret

logger = logging.getLogger("tavsan.plans")
router = APIRouter(prefix="/plans", tags=["plans"])


def _profile_from_baby(baby, overrides: dict | None, dogum_haftasi: int | None) -> dict:
    """Baby satırından motorun beklediği profil sözlüğünü kur. profile_overrides
    (mobil onboarding'in 37 cevabı) üzerine yazılır."""
    profile = {
        "bebek_ad": baby.name,
        "dogum_tarihi": baby.birth_date.isoformat(),   # çağıran öncesinde doğruladı
        "dogum_haftasi": dogum_haftasi or 40,
        "beslenme": baby.feeding_type or "",
        "destek": baby.sleep_method or "",
        "oda": baby.sleep_environment or "",
        # crying_tolerance = ebeveynin ağlamaya dayanma sınırı → motor 'dayanma_siniri'.
        "dayanma_siniri": baby.crying_tolerance or "",
        "deneyim": baby.parent_experience or "",
        "gece_uyanma": str(baby.night_wakes) if baby.night_wakes is not None else "",
    }
    if overrides:
        profile.update(overrides)                       # zengin profil override eder
    return profile


@router.post("/generate", response_model=PlanResp, status_code=status.HTTP_201_CREATED)
def generate_plan(req: PlanGenerateReq, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    baby = get_owned_baby(req.baby_id, db, user)
    if baby.birth_date is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Plan için bebeğin doğum tarihi gereklidir")

    profile = _profile_from_baby(baby, req.profile_overrides, req.dogum_haftasi)
    try:
        param = parametre_uret(profile)                 # deterministik parametreler
        markdown = plan_generator.plan_uret(param)      # Claude (varsa) / fallback
    except Exception as e:
        logger.warning("Plan üretimi başarısız (baby=%s): %s", baby.id, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Plan üretilemedi: {e}")

    used_claude = bool(os.getenv("ANTHROPIC_API_KEY")) and plan_generator.HAS_ANTHROPIC
    content = {
        "markdown": markdown,
        "bucket": param["bucket"],
        "yas": param["yas"],
        "plan_secimi": param["plan_secimi"],
        "uygun_mu": param["uygun_mu"],
        "uyarilar": param["uyarilar"],
        "generated_with": "claude" if used_claude else "fallback",
    }

    plan = SleepPlan(user_id=user.id, baby_id=baby.id,
                     plan_date=datetime.now(timezone.utc).date(), content=content)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    logger.info("Plan üretildi: plan_id=%s baby=%s (%s)",
                plan.id, baby.id, content["generated_with"])
    return plan


@router.get("", response_model=list[PlanResp])
def list_plans(db: Session = Depends(get_db), user: User = Depends(get_current_user),
               baby_id: uuid.UUID | None = Query(default=None)):
    q = db.query(SleepPlan).filter(SleepPlan.user_id == user.id)
    if baby_id is not None:
        q = q.filter(SleepPlan.baby_id == baby_id)
    return q.order_by(SleepPlan.created_at.desc()).all()


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
    return plan
