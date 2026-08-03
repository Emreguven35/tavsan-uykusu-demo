"""
plans router — /api/v1/plans

POST /plans/generate: bebek profili (+opsiyonel ek profil) → mevcut parameter_engine
(deterministik sayısal parametreler) + plan_generator (Claude "yazar" / API yoksa
deterministik fallback) → JSONB olarak kaydeder.
POST /plans/adapt:   son 3 günün uyku kayıtlarına göre çizelgeyi kaydırır (Faz 6.1).
GET  /plans/today:   bugünün planı; yoksa en güncel planı bugüne adapte eder (lazy).
GET  /plans, GET /plans/{plan_date}: kullanıcının planlarını döndürür.

Mevcut engine modülleri AYNEN import edilir (kod çiftlenmez).

TEKİLLİK (Faz 6.1): generate ve adapt aynı güne yazarken önce o günün kaydını
GÜNCELLER (upsert) — aynı tarihe satır yığılmaz.
"""
import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user, get_owned_baby
from api.models import Baby, SleepLog, SleepPlan, User
from api.schemas.plan import PlanAdaptResp, PlanGenerateReq, PlanResp
from api.services import plan_adapter
from engine import plan_generator
from engine.parameter_engine import (
    hesapla_yas_ay, load_kb, parametre_uret, yas_bucket_sec,
)

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


def _bucket_params(baby, dogum_haftasi: int | None = None) -> tuple[str, dict]:
    """Bebeğin yaşına karşılık gelen yaş bandı parametreleri (KB'den).

    Adaptasyon için tam parametre motorunu koşturmaya gerek yok — yalnız yaş bandı
    gerekir (deterministik, ucuz)."""
    yas = hesapla_yas_ay(baby.birth_date.isoformat(), int(dogum_haftasi or 40))
    key = yas_bucket_sec(yas["duzeltilmis_ay"])
    return key, load_kb()["yas_buckets"].get(key, {})


def _upsert_plan(db: Session, user: User, baby, plan_date: date,
                 content: dict) -> SleepPlan:
    """Aynı (user, baby, plan_date) varsa İÇERİĞİ GÜNCELLE, yoksa oluştur.

    Aynı güne plan yığılmasını önler (Faz 6.1). JSONB alanının değiştiğini
    SQLAlchemy'nin görmesi için content YENİ bir sözlük olarak atanır."""
    plan = (db.query(SleepPlan)
            .filter(SleepPlan.user_id == user.id,
                    SleepPlan.baby_id == baby.id,
                    SleepPlan.plan_date == plan_date)
            .order_by(SleepPlan.created_at.desc())
            .first())
    if plan is None:
        plan = SleepPlan(user_id=user.id, baby_id=baby.id,
                         plan_date=plan_date, content=content)
        db.add(plan)
    else:
        plan.content = dict(content)
    db.commit()
    db.refresh(plan)
    return plan


def _generate_content(baby, req_overrides: dict | None,
                      dogum_haftasi: int | None) -> dict:
    """parameter_engine + plan_generator ile plan içeriği üret (+ yapısal çizelge)."""
    profile = _profile_from_baby(baby, req_overrides, dogum_haftasi)
    param = parametre_uret(profile)                 # deterministik parametreler
    markdown = plan_generator.plan_uret(param)      # Claude (varsa) / fallback
    used_claude = bool(os.getenv("ANTHROPIC_API_KEY")) and plan_generator.HAS_ANTHROPIC

    # Faz 6.1: markdown'a EK OLARAK yapısal çizelge saklanır — adaptasyon ve
    # bildirim zamanlayıcısı bunu tüketir. Markdown aynen korunur (mobil gösterim).
    schedule = plan_adapter.build_schedule(
        param.get("parametreler", {}), plan_adapter.DEFAULT_WAKE_MIN)

    return {
        "markdown": markdown,                       # KALIR (geriye uyum + detay metni)
        "headline": plan_adapter.headline(baby.name, param["bucket"], schedule),
        "bucket": param["bucket"],
        "yas": param["yas"],
        "plan_secimi": param["plan_secimi"],
        "uygun_mu": param["uygun_mu"],
        "uyarilar": param["uyarilar"],
        "generated_with": "claude" if used_claude else "fallback",
        # --- Faz 6.1 alanları ---
        "schedule": schedule,
        "dogum_haftasi": int(dogum_haftasi or 40),
        "baseline_night_wakes": baby.night_wakes,
        "adapted": False,
        # --- Faz 6.1R: 45-15-45 gece direnme protokolü ------------------------
        # Hesaplama kuralı DEĞİL, içerik kuralıdır — mobil bunu plan ekranında
        # gösterir. markdown'a dokunulmaz (Claude prompt cache'i ve 151-test
        # regresyonu korunur).
        "night_wake_protocol": dict(plan_adapter.NIGHT_WAKE_PROTOCOL),
    }


@router.post("/generate", response_model=PlanResp, status_code=status.HTTP_201_CREATED)
def generate_plan(req: PlanGenerateReq, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    baby = get_owned_baby(req.baby_id, db, user)
    if baby.birth_date is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Plan için bebeğin doğum tarihi gereklidir")
    try:
        content = _generate_content(baby, req.profile_overrides, req.dogum_haftasi)
    except Exception as e:
        logger.warning("Plan üretimi başarısız (baby=%s): %s", baby.id, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Plan üretilemedi: {e}")

    today = datetime.now(timezone.utc).date()
    plan = _upsert_plan(db, user, baby, today, content)
    logger.info("Plan üretildi: plan_id=%s baby=%s (%s)",
                plan.id, baby.id, content["generated_with"])
    return plan


def _recent_logs(db: Session, user: User, baby, today: date,
                 lookback_days: int = plan_adapter.LOOKBACK_DAYS) -> list[SleepLog]:
    """Son `lookback_days` günün kayıtları. Yerel gün sınırı kayması ve gece
    uykusunun bitişi için pencere bir gün geniş tutulur."""
    start = datetime.combine(today - timedelta(days=lookback_days),
                             datetime.min.time(), tzinfo=timezone.utc)
    return (db.query(SleepLog)
            .filter(SleepLog.user_id == user.id,
                    SleepLog.baby_id == baby.id,
                    SleepLog.started_at >= start)
            .order_by(SleepLog.started_at)
            .all())


def _latest_plan(db: Session, user: User, baby) -> SleepPlan | None:
    return (db.query(SleepPlan)
            .filter(SleepPlan.user_id == user.id, SleepPlan.baby_id == baby.id)
            .order_by(SleepPlan.plan_date.desc(), SleepPlan.created_at.desc())
            .first())


def _ensure_current_schema(db: Session, plan: SleepPlan | None) -> SleepPlan | None:
    """Saklanmış planı GÜNCEL sözleşmeye yükselt (okuma yolunda, kalıcı).

    Şema değişikliğinden önce üretilmiş planlarda bloklar {start, label,
    type:"night"} biçimindeydi ve headline/night_wake_protocol yoktu. Mobil
    time/title okuduğu için bunlar null görünüyordu. Burada bir kez yükseltilip
    DB'ye yazılır; sonraki okumalar zaten günceldir (değişiklik yoksa yazılmaz)."""
    if plan is None:
        return None
    content = dict(plan.content or {})
    eski = content.get("schedule") or []
    yeni = plan_adapter.normalize_schedule(eski)
    degisti = yeni != eski

    if yeni and not content.get("headline"):
        baby = db.get(Baby, plan.baby_id)
        content["headline"] = plan_adapter.headline(
            baby.name if baby is not None else "Bebeğiniz",
            content.get("bucket"), yeni)
        degisti = True
    if not content.get("night_wake_protocol"):
        content["night_wake_protocol"] = dict(plan_adapter.NIGHT_WAKE_PROTOCOL)
        degisti = True

    if degisti:
        content["schedule"] = yeni
        plan.content = content
        db.commit()
        db.refresh(plan)
        logger.info("Plan güncel şemaya yükseltildi: plan_id=%s", plan.id)
    return plan


def _adaptation_meta(result: dict, summary: dict, adjusted: bool, shift: int,
                     required: bool) -> dict:
    """Plan içeriğine gömülen adaptasyon izi (mobil/denetim için)."""
    return {
        "adjusted": adjusted,
        "shift_minutes": shift,
        "regenerate_required": required,
        "regression_detected": result["regression_detected"],
        "restart_program_suggested": result["restart_program_suggested"],
        "reasons": result["reasons"],
        "log_summary": summary,
    }


def _run_adaptation(db: Session, user: User, baby, base_plan: SleepPlan,
                    logs: list[SleepLog], today: date) -> tuple[SleepPlan, dict]:
    """Adaptasyon motorunu koştur, sonucu bugünün planı olarak upsert et.

    regenerate_required (yaş bandı ihlali) → çizelgeyi kaydırmak yerine planı
    TAM YENİDEN ÜRETİR (spec 6.1)."""
    base_content = dict(base_plan.content or {})
    dogum_haftasi = base_content.get("dogum_haftasi", 40)
    _, params = _bucket_params(baby, dogum_haftasi)

    summary = plan_adapter.summarize_logs(logs, today=today)
    result = plan_adapter.adapt(
        base_content, params, summary,
        training_completed_at=baby.training_completed_at, today=today)

    if result["regenerate_required"]:
        try:
            content = _generate_content(baby, None, dogum_haftasi)
        except Exception as e:
            logger.warning("Yeniden üretim başarısız (baby=%s): %s", baby.id, e)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                                detail=f"Plan yeniden üretilemedi: {e}")
        content.update({
            "adapted": True,
            "regenerated": True,
            "base_plan_id": str(base_plan.id),
            "adaptation": _adaptation_meta(result, summary, adjusted=False,
                                           shift=0, required=True),
        })
    else:
        content = base_content
        content.update({
            "schedule": result["schedule"],
            # Çizelge kaydıysa başlıktaki yatış saati de güncellenmeli.
            "headline": plan_adapter.headline(
                baby.name, base_content.get("bucket"), result["schedule"]),
            "adapted": True,
            "regenerated": False,
            "base_plan_id": str(base_plan.id),
            # 45-15-45 protokolü eski planlarda yoksa burada eklenir (geriye uyum).
            "night_wake_protocol": base_content.get("night_wake_protocol")
            or dict(plan_adapter.NIGHT_WAKE_PROTOCOL),
            "adaptation": _adaptation_meta(result, summary,
                                           adjusted=result["adjusted"],
                                           shift=result["shift_minutes"],
                                           required=False),
        })

    plan = _upsert_plan(db, user, baby, today, content)
    return plan, result


@router.post("/adapt", response_model=PlanAdaptResp)
def adapt_plan(baby_id: uuid.UUID = Query(...), db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    """Son 3 günün uyku kayıtlarına göre planı adapte et ve bugünün planı olarak kaydet.

    409: son 3 günde hiç kayıt yoksa ya da adapte edilecek bir plan yoksa."""
    baby = get_owned_baby(baby_id, db, user)
    today = datetime.now(timezone.utc).date()

    base_plan = _latest_plan(db, user, baby)
    if base_plan is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Adapte edilecek plan yok — önce POST /plans/generate çağırın")

    logs = _recent_logs(db, user, baby, today)
    if not logs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Son {plan_adapter.LOOKBACK_DAYS} günde uyku kaydı yok — "
                    "adaptasyon için önce uyku kaydı girin"))

    plan, result = _run_adaptation(db, user, baby, base_plan, logs, today)
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

    Mobil dashboard bunu çağırır. Kayıt yoksa adaptasyon yapılmaz — en güncel plan
    olduğu gibi bugüne kopyalanır (kullanıcı planı görmeye devam eder)."""
    baby = get_owned_baby(baby_id, db, user)
    today = datetime.now(timezone.utc).date()

    plan = (db.query(SleepPlan)
            .filter(SleepPlan.user_id == user.id, SleepPlan.baby_id == baby.id,
                    SleepPlan.plan_date == today)
            .order_by(SleepPlan.created_at.desc())
            .first())
    if plan is not None:
        return _ensure_current_schema(db, plan)

    base_plan = _latest_plan(db, user, baby)
    if base_plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Bu bebek için henüz plan üretilmemiş")

    logs = _recent_logs(db, user, baby, today)
    if not logs:
        # Kayıt yok → adaptasyon yok; en güncel planı bugüne taşı (bozmadan).
        # Eski şemayla üretilmiş planlar bu vesileyle güncel sözleşmeye yükseltilir.
        content = dict(base_plan.content or {})
        sched = plan_adapter.normalize_schedule(content.get("schedule"))
        content.update({
            "adapted": False,
            "base_plan_id": str(base_plan.id),
            "schedule": sched,
            "headline": content.get("headline") or plan_adapter.headline(
                baby.name, content.get("bucket"), sched),
            "night_wake_protocol": content.get("night_wake_protocol")
            or dict(plan_adapter.NIGHT_WAKE_PROTOCOL),
        })
        return _upsert_plan(db, user, baby, today, content)

    plan, _ = _run_adaptation(db, user, baby, base_plan, logs, today)
    return plan


@router.get("", response_model=list[PlanResp])
def list_plans(db: Session = Depends(get_db), user: User = Depends(get_current_user),
               baby_id: uuid.UUID | None = Query(default=None)):
    q = db.query(SleepPlan).filter(SleepPlan.user_id == user.id)
    if baby_id is not None:
        q = q.filter(SleepPlan.baby_id == baby_id)
    return [_ensure_current_schema(db, p)
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
    return _ensure_current_schema(db, plan)
