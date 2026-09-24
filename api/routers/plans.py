"""
plans router — /api/v1/plans

POST /plans/generate: bebek profili → parameter_engine + plan_generator → JSONB.
POST /plans/adapt:   bugünün çizelgesini kayıtlardan yeniden hesaplar (elle tetik).
POST /plans/regresyon-cevap: regresyon kartındaki "kendi dönüyor mu?" cevabı.
GET  /plans/today:   bugünün planı; şablon + bugünün kayıtlarından HER çağrıda
                     yeniden hesaplanır (v2/K8 — "günde bir kez" kilidi yok).
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
from api.schemas.plan import (
    PlanAdaptResp, PlanGenerateReq, PlanJobResp, PlanJobStatusResp, PlanResp,
    RegresyonCevapReq, RegresyonCevapResp,
)
from api.services import plan_adapter, plan_jobs, plan_service

logger = logging.getLogger("tavsan.plans")
router = APIRouter(prefix="/plans", tags=["plans"])


def _plan_yaniti(db: Session, user: User, plan: SleepPlan | None) -> PlanResp | None:
    """PlanResp + B4 kilidi. Premium değilse eğitim programı çıkarılır
    (services.erisim.plan_icerigi_kilitle); günlük çizelge kalır."""
    if plan is None:
        return None
    from api.deps import premium_karari
    from api.services import erisim
    premium, _k = premium_karari(db, user)
    resp = PlanResp.model_validate(plan)
    icerik, kilitli = erisim.plan_icerigi_kilitle(resp.content or {}, premium)
    if kilitli:
        resp.content = icerik
        resp.locked = kilitli
        resp.premium_required = True
    return resp


def _eksik_profil_yaniti(eksik: list[str]):
    from fastapi.responses import JSONResponse
    etiketler = [plan_service.ALAN_ETIKETI.get(a, a) for a in eksik]
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={
        "detail": ("Planı hazırlayabilmemiz için profilde eksik bilgi var: "
                   + ", ".join(etiketler) + ". Lütfen bebeğinizin profilini "
                   "tamamlayın."),
        "eksik_alanlar": eksik,
        "eksik_alan_etiketleri": etiketler,
    })


@router.post("/generate", status_code=status.HTTP_202_ACCEPTED,
             response_model=None,
             responses={202: {"model": PlanJobResp}, 201: {"model": PlanResp}})
def generate_plan(req: PlanGenerateReq,
                  sync: bool = Query(default=False,
                                     description="true → senkron 201+PlanResp "
                                                 "(geriye uyum/test); default async 202"),
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Plan üret. Faz G1: üretim ~129 sn sürüyordu ve senkron istek worker'ı
    bloke ediyordu → artık VARSAYILAN ASENKRON.

    - Async (default): 202 + {job_id, status:"processing"}. Üretim arka planda
      koşar; istemci GET /plans/generate/{job_id} ile yoklar.
    - Sync (?sync=true): eski davranış — 201 + PlanResp (blocking). Test suite ve
      hızlı script'ler için korunur; mobil async akışı kullanır.

    Sahiplik + profil bütünlüğü 202/201'den ÖNCE senkron doğrulanır (erken
    404 / 422 + eksik_alanlar)."""
    baby = get_owned_baby(req.baby_id, db, user)
    # D-3 — eksik profille üretim 202 dönüp arka planda boşa gidiyordu; mobil
    # sonsuza dek "hazırlanıyor" gösteriyordu. Artık istek anında Türkçe 422 +
    # eksik alan listesi (mobil profil ekranına yönlendirebilsin).
    eksik = plan_service.eksik_profil_alanlari(baby, req.profile_overrides,
                                                req.dogum_haftasi)
    if eksik:
        return _eksik_profil_yaniti(eksik)

    # v2.1 — İstekle gelen kalıcı profil alanları (saglik_problemi, dogum_haftasi,
    # gece uyanma sayısı) BEBEĞE yazılır. Eskiden yalnız bu isteğin gövdesinde
    # yaşıyorlardı; bant atlaması sonrası yeniden üretimde sağlık uyarısı sessizce
    # kayboluyordu (ölçüldü). Async yolda da geçerli olsun diye 202'den ÖNCE.
    plan_service.profili_kalicilastir(db, baby, req.profile_overrides,
                                      req.dogum_haftasi)

    if sync:
        try:
            content = plan_service.generate_content(
                baby, req.profile_overrides, req.dogum_haftasi)
        except plan_service.PlanError as e:
            logger.warning("Plan üretimi başarısız (baby=%s): %s", baby.id, e)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                                detail=f"Plan üretilemedi: {e}")
        today = datetime.now(timezone.utc).date()
        plan = plan_service.upsert_plan(db, user, baby, today, content)
        logger.info("Plan üretildi (sync): plan_id=%s baby=%s (%s)",
                    plan.id, baby.id, content["generated_with"])
        from fastapi.responses import JSONResponse
        from fastapi.encoders import jsonable_encoder
        return JSONResponse(status_code=status.HTTP_201_CREATED,
                            content=jsonable_encoder(_plan_yaniti(db, user, plan)))

    # Async: iş kaydet + ADANMIŞ havuzda üret (Faz O2 — uvicorn threadpool'u
    # değil; havuz doluysa iş kuyrukta bekler, istemci 202'yi yine hemen alır).
    # baby.id'yi geçiriyoruz (Session yanıttan sonra kapanacak).
    job_id = plan_jobs.create_job(user.id, baby.id)
    plan_jobs.submit(job_id, baby.id, req.profile_overrides, req.dogum_haftasi)
    sira = plan_jobs.get_job(job_id, user.id)["queue_position"]
    logger.info("Plan job kuyruğa alındı: job=%s baby=%s sıra=%s", job_id, baby.id, sira)
    return PlanJobResp(job_id=job_id, status=plan_jobs.STATUS_PROCESSING,
                       queue_position=sira)


@router.get("/generate/{job_id}", response_model=PlanJobStatusResp)
def generate_status(job_id: str, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Async plan üretim işinin durumu. status='done' ise plan dolu döner.

    İş yalnız SAHİBİNE görünür (başka kullanıcı / bilinmeyen id → 404)."""
    job = plan_jobs.get_job(job_id, user.id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="İş bulunamadı")
    resp = PlanJobStatusResp(job_id=job_id, status=job["status"],
                             queue_position=job["queue_position"],
                             error=job.get("error"))
    if job["status"] == plan_jobs.STATUS_DONE and job.get("plan_id"):
        plan = db.get(SleepPlan, uuid.UUID(job["plan_id"]))
        if plan is not None:
            resp.plan = _plan_yaniti(db, user, plan)
    return resp


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

    # v2: "kaydırma" yok; bugünün çizelgesi şablondan farklıysa yeniden hesaplandı.
    adjusted = bool((result.get("adaptation") or {}).get("yeniden_hesaplanan_bloklar"))
    logger.info("Plan yeniden hesaplandı: plan=%s baby=%s adjusted=%s regen=%s "
                "regression=%s", plan.id, baby.id, adjusted,
                result["regenerate_required"], result["regression_detected"])
    return PlanAdaptResp(
        plan=_plan_yaniti(db, user, plan),
        adjusted=adjusted,
        shift_minutes=0,                      # kullanımdan kaldırıldı (K1)
        regenerate_required=result["regenerate_required"],
        regression_detected=result["regression_detected"],
        regresyon_karti=result.get("regresyon_karti"),
        egitim_baslangic_gunu=result.get("egitim_baslangic_gunu"),
        kirkbes_gun_doldu=bool(result.get("kirkbes_gun_doldu")),
        reasons=result["reasons"],
        adaptation=result.get("adaptation"),
    )


@router.post("/regresyon-cevap", response_model=RegresyonCevapResp)
def regresyon_cevapla(req: RegresyonCevapReq,
                      baby_id: uuid.UUID = Query(...),
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Regresyon kartının 1. kademesine verilen cevabı kaydet (v1.4, İlayda S9).

    - evet  → kart KAPANIR; `REGRESYON_SESSIZLIK_GUN` (7) gün boyunca aynı soru
              sorulmaz, sonra durum yeniden değerlendirilir.
    - hayır → 45 gün dolmadıysa "eğitime devam", dolduysa tıbbi yönlendirme.

    Plan ÜRETİLMEZ ve çizelge DEĞİŞMEZ: bu uç yalnız cevabı saklar ve cevabın
    hemen sonraki kart durumunu döndürür (mobil tek istekle kartı tazeler)."""
    baby = get_owned_baby(baby_id, db, user)
    simdi = datetime.now(timezone.utc)
    baby.regresyon_kendi_donuyor = bool(req.kendi_donuyor)
    baby.regresyon_cevap_at = simdi
    db.commit()
    db.refresh(baby)

    today = simdi.date()
    kart = plan_adapter.regresyon_karti(
        baby.training_started_at, plan_service.regresyon_cevabi(baby, today),
        today)
    logger.info("Regresyon cevabı: baby=%s kendi_donuyor=%s kart=%s",
                baby.id, req.kendi_donuyor, kart["tip"])
    return RegresyonCevapResp(
        baby_id=baby.id,
        kendi_donuyor=bool(req.kendi_donuyor),
        cevap_at=simdi,
        # tip None → "evet" cevabı, gösterilecek kart yok.
        regresyon_karti=kart if kart["tip"] else None,
        egitim_baslangic_gunu=kart["egitim_gunu"],
        kirkbes_gun_doldu=bool(kart["kirkbes_gun_doldu"]),
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
    return _plan_yaniti(db, user, plan)


@router.get("", response_model=list[PlanResp])
def list_plans(db: Session = Depends(get_db), user: User = Depends(get_current_user),
               baby_id: uuid.UUID | None = Query(default=None)):
    q = db.query(SleepPlan).filter(SleepPlan.user_id == user.id)
    if baby_id is not None:
        q = q.filter(SleepPlan.baby_id == baby_id)
    return [_plan_yaniti(db, user, plan_service.ensure_current_schema(db, p))
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
    return _plan_yaniti(db, user, plan_service.ensure_current_schema(db, plan))
