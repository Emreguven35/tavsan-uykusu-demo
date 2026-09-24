"""
Plan geri bildirimi — kayıt + anlık görüntü.

Görüntü, geri bildirimin verildiği ANDAKİ durumdur: plan her kayıtta yeniden
hesaplandığı için sonradan "annenin itiraz ettiği çizelge" artık yoktur.
Günlük denetim (scripts/gunluk_denetim.py) bu görüntüyü okur.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.models import Baby, PlanFeedback, SleepLog, User
from api.services import plan_adapter, plan_service

logger = logging.getLogger("tavsan.geri_bildirim")


def _utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def yas_bant(baby: Baby) -> tuple[dict | None, dict | None]:
    """({"gercek_ay", "duzeltilmis_ay"}, {"id", "ad"}) — doğum tarihi yoksa None."""
    if baby.birth_date is None:
        return None, None
    from engine import yas_bantlari
    from engine.parameter_engine import hesapla_yas_ay
    yas = hesapla_yas_ay(baby.birth_date.isoformat(),
                         plan_service.etkin_dogum_haftasi(baby))
    try:
        b = yas_bantlari.yas_bandi_getir(yas["duzeltilmis_ay"])
        bant = {"id": b.get("id"), "ad": b.get("ad")}
    except Exception:
        bant = None
    return ({"gercek_ay": yas.get("gercek_ay"),
             "duzeltilmis_ay": yas.get("duzeltilmis_ay")}, bant)


def kayit_ozeti(r: SleepLog) -> dict:
    return {"type": r.type,
            "started_at": _utc(r.started_at).isoformat(),
            "ended_at": _utc(r.ended_at).isoformat() if r.ended_at else None,
            "sinif": (plan_adapter.uyku_sinifi_ham(r.started_at, r.ended_at,
                                                    plan_adapter.TZ_OFFSET_MIN)
                      if r.type in ("sleep", "nap", "sekerleme") else None)}


def anlik_goruntu(db: Session, user: User, baby: Baby, an: datetime) -> dict:
    """Plan (o günün ya da en son), o yerel günün kayıtları, yaş/bant, eğitim
    günü ve aşama."""
    from api.routers.logs import gun_filtresi
    from api.services import education

    an = _utc(an)
    from api.zaman import tr_gunu
    yerel_gun = tr_gunu(an)
    plan = (plan_service.plan_for_date(db, user, baby, an.date())
            or plan_service.latest_plan(db, user, baby))
    icerik = (plan.content or {}) if plan is not None else {}
    kayitlar = (db.query(SleepLog)
                .filter(SleepLog.user_id == user.id,
                        SleepLog.baby_id == baby.id,
                        gun_filtresi(yerel_gun))
                .order_by(SleepLog.started_at).all())
    yas, bant = yas_bant(baby)
    asama = education.asama_belirle(baby, yerel_gun)
    return {
        "plan_id": str(plan.id) if plan is not None else None,
        "plan_date": plan.plan_date.isoformat() if plan is not None else None,
        "type": icerik.get("type"),
        "schedule": icerik.get("schedule") or [],
        "adaptation": icerik.get("adaptation"),
        "uyarilar": icerik.get("uyarilar") or [],
        "kayitlar": [kayit_ozeti(r) for r in kayitlar],
        "yerel_gun": yerel_gun.isoformat(),
        "yas": yas,
        "bant": bant,
        "egitim_gunu": (plan_adapter.egitim_gunu(baby.training_started_at,
                                                 yerel_gun)
                        if plan_service.tip_turet(icerik) == plan_service.TYPE_EGITIM
                        else None),
        "asama": asama["kod"],
    }


def kaydet(db: Session, user: User, baby: Baby, req) -> tuple[PlanFeedback, bool]:
    """(satır, yeni_mi). Aynı (user, client_id) varsa YAZMADAN mevcut döner."""
    mevcut = (db.query(PlanFeedback)
              .filter(PlanFeedback.user_id == user.id,
                      PlanFeedback.client_id == req.client_id).one_or_none())
    if mevcut is not None:
        return mevcut, False

    an = _utc(req.created_at)
    anlik = anlik_goruntu(db, user, baby, an)
    satir = PlanFeedback(
        user_id=user.id, baby_id=baby.id, client_id=req.client_id,
        block_key=req.block_key, block_time=req.block_time,
        secenek=req.secenek, metin=req.metin,
        plan_id=(None if anlik["plan_id"] is None
                 else uuid.UUID(anlik["plan_id"])),
        plan_date=(None if anlik["plan_date"] is None
                   else datetime.fromisoformat(anlik["plan_date"]).date()),
        anlik=anlik, created_at=an)
    db.add(satir)
    try:
        db.commit()
    except IntegrityError:
        # Yarış: aynı client_id başka istekle az önce yazıldı → idempotent.
        db.rollback()
        mevcut = (db.query(PlanFeedback)
                  .filter(PlanFeedback.user_id == user.id,
                          PlanFeedback.client_id == req.client_id).one())
        return mevcut, False
    db.refresh(satir)
    logger.info("Plan geri bildirimi: baby=%s blok=%s secenek=%s",
                baby.id, req.block_key, req.secenek)
    return satir, True
