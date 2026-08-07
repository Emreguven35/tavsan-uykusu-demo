"""
Plan servisi — plan üretimi, adaptasyon ve "bugünün planı" mantığının TEK yeri.

Faz 6.6: bu mantık önce plans router'ında yaşıyordu; bildirim zamanlayıcısı da
aynı lazy-adapt yolunu koşturması gerektiği için ortak servise çıkarıldı.
KOD ÇİFTLENMEZ: hem GET /plans/today hem notifier ensure_today_plan()'ı çağırır.

HTTP bilinmez: hatalar PlanError olarak yükselir, router bunu 502'ye çevirir.
Zamanlayıcı ise yakalayıp loglar (tek bebek yüzünden tur düşmesin).
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from api.models import Baby, SleepLog, SleepPlan, User
from api.services import plan_adapter
from engine import plan_generator, yas_bantlari
from engine.parameter_engine import hesapla_yas_ay, load_kb, parametre_uret, yas_bucket_sec

logger = logging.getLogger("tavsan.plan_service")


class PlanError(RuntimeError):
    """Plan üretilemedi/yeniden üretilemedi (dış servis, motor hatası vb.)."""


# =============================================================================
# Profil / parametre yardımcıları
# =============================================================================
def profile_from_baby(baby: Baby, overrides: dict | None,
                      dogum_haftasi: int | None) -> dict:
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
        profile.update(overrides)
    return profile


def bucket_params(baby: Baby, dogum_haftasi: int | None = None
                  ) -> tuple[str, dict, float]:
    """Bebeğin yaşına karşılık gelen bant parametreleri.

    Dönen: (kb_bucket_key, kb_bucket, duzeltilmis_ay). Sayısal çizelge kararları
    duzeltilmis_ay üzerinden yas_bantlari.json'dan alınır (Faz Y); kb_bucket
    yalnız yardımcı içerik (örnek program, görsel referans) taşır."""
    yas = hesapla_yas_ay(baby.birth_date.isoformat(), int(dogum_haftasi or 40))
    key = yas_bucket_sec(yas["duzeltilmis_ay"])
    return key, load_kb()["yas_buckets"].get(key, {}), yas["duzeltilmis_ay"]


def tek_uyku_bayragi(content: dict | None) -> bool | None:
    """Plan içeriğinden 12-18 ay tek/çift uyku bayrağını çöz.

    True → tek uyku; None → bandın varsayılanı (2 uyku). False DÖNMEZ: varsayılan
    zaten 2 uyku olduğu için ikisi aynı sonucu verir, None niyeti daha nettir
    ("bilgi yok" ile "iki uyku ölçüldü" karışmasın)."""
    return ((content or {}).get("yas_bandi") or {}).get("varyant") == "tek_uyku" or None


# =============================================================================
# Sorgular
# =============================================================================
def recent_logs(db: Session, user: User, baby: Baby, today: date,
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


def latest_plan(db: Session, user: User, baby: Baby) -> SleepPlan | None:
    return (db.query(SleepPlan)
            .filter(SleepPlan.user_id == user.id, SleepPlan.baby_id == baby.id)
            .order_by(SleepPlan.plan_date.desc(), SleepPlan.created_at.desc())
            .first())


def plan_for_date(db: Session, user: User, baby: Baby, gun: date) -> SleepPlan | None:
    return (db.query(SleepPlan)
            .filter(SleepPlan.user_id == user.id, SleepPlan.baby_id == baby.id,
                    SleepPlan.plan_date == gun)
            .order_by(SleepPlan.created_at.desc())
            .first())


def upsert_plan(db: Session, user: User, baby: Baby, plan_date: date,
                content: dict) -> SleepPlan:
    """Aynı (user, baby, plan_date) varsa İÇERİĞİ GÜNCELLE, yoksa oluştur.

    Aynı güne plan yığılmasını önler. JSONB değişikliğinin görülmesi için content
    YENİ bir sözlük olarak atanır."""
    plan = plan_for_date(db, user, baby, plan_date)
    if plan is None:
        plan = SleepPlan(user_id=user.id, baby_id=baby.id,
                         plan_date=plan_date, content=content)
        db.add(plan)
    else:
        plan.content = dict(content)
    db.commit()
    db.refresh(plan)
    return plan


# =============================================================================
# Şema yükseltme (geriye uyumluluk)
# =============================================================================
def ensure_current_schema(db: Session, plan: SleepPlan | None) -> SleepPlan | None:
    """Saklanmış planı GÜNCEL sözleşmeye yükselt (okuma yolunda, kalıcı).

    Şema değişikliğinden önce üretilmiş planlarda bloklar {start, label,
    type:"night"} biçimindeydi ve headline/night_wake_protocol yoktu. Burada bir
    kez yükseltilip DB'ye yazılır (değişiklik yoksa yazılmaz)."""
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
    # Faz Y: evrensel kestirme kuralı tüm planlarda bulunmalı (eski planlarda yok).
    if not content.get("kestirme_protokolu"):
        content["kestirme_protokolu"] = yas_bantlari.kestirme_protokolu()
        degisti = True

    if degisti:
        content["schedule"] = yeni
        plan.content = content
        db.commit()
        db.refresh(plan)
        logger.info("Plan güncel şemaya yükseltildi: plan_id=%s", plan.id)
    return plan


# =============================================================================
# Üretim + adaptasyon
# =============================================================================
def generate_content(baby: Baby, req_overrides: dict | None,
                     dogum_haftasi: int | None) -> dict:
    """parameter_engine + plan_generator ile plan içeriği üret (+ yapısal çizelge)."""
    profile = profile_from_baby(baby, req_overrides, dogum_haftasi)
    try:
        param = parametre_uret(profile)                 # deterministik parametreler
        markdown = plan_generator.plan_uret(param)      # Claude (varsa) / fallback
    except Exception as e:
        raise PlanError(str(e)) from e
    used_claude = bool(os.getenv("ANTHROPIC_API_KEY")) and plan_generator.HAS_ANTHROPIC

    # Faz Y: çizelge YAŞ BANDI TABLOSUNDAN kurulur (düzeltilmiş ay üzerinden).
    schedule = plan_adapter.build_schedule(
        param.get("parametreler", {}), plan_adapter.DEFAULT_WAKE_MIN,
        yas_ay=param["yas"]["duzeltilmis_ay"],
        tek_uyku=tek_uyku_bayragi(param))

    return {
        "markdown": markdown,                       # KALIR (geriye uyum + detay metni)
        "headline": plan_adapter.headline(baby.name, param["bucket"], schedule),
        "bucket": param["bucket"],
        "yas": param["yas"],
        # Faz Y — mobilin gösterdiği yapılandırılmış bant + evrensel kestirme kuralı.
        "yas_bandi": param["yas_bandi"],
        "kestirme_protokolu": param["kestirme_protokolu"],
        "plan_secimi": param["plan_secimi"],
        "uygun_mu": param["uygun_mu"],
        "uyarilar": param["uyarilar"],
        "generated_with": "claude" if used_claude else "fallback",
        "schedule": schedule,
        "dogum_haftasi": int(dogum_haftasi or 40),
        "baseline_night_wakes": baby.night_wakes,
        "adapted": False,
        "night_wake_protocol": dict(plan_adapter.NIGHT_WAKE_PROTOCOL),
    }


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


def run_adaptation(db: Session, user: User, baby: Baby, base_plan: SleepPlan,
                   logs: list[SleepLog], today: date) -> tuple[SleepPlan, dict]:
    """Adaptasyon motorunu koştur, sonucu bugünün planı olarak upsert et.

    regenerate_required (yaş bandı ihlali) → çizelgeyi kaydırmak yerine planı
    TAM YENİDEN ÜRETİR."""
    base_content = dict(base_plan.content or {})
    dogum_haftasi = base_content.get("dogum_haftasi", 40)
    _, params, yas_ay = bucket_params(baby, dogum_haftasi)
    # 12-18 ay tek/çift uyku ayrımı planla birlikte saklanır; bant değişmedikçe korunur.
    tek_uyku = tek_uyku_bayragi(base_content)

    summary = plan_adapter.summarize_logs(logs, today=today)
    result = plan_adapter.adapt(
        base_content, params, summary,
        training_completed_at=baby.training_completed_at, today=today,
        yas_ay=yas_ay, tek_uyku=tek_uyku)

    if result["regenerate_required"]:
        content = generate_content(baby, None, dogum_haftasi)     # PlanError yükselebilir
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
            # Kestirme kuralı her adaptasyonda yeniden değerlendirilir (gündüz
            # uyku minimumu tutmadıysa mobil kartı gösterir).
            "kestirme_protokolu": (base_content.get("kestirme_protokolu")
                                   or yas_bantlari.kestirme_protokolu()),
            "kestirme_degerlendirme": result["kestirme"],
            # Çizelge kaydıysa başlıktaki yatış saati de güncellenmeli.
            "headline": plan_adapter.headline(
                baby.name, base_content.get("bucket"), result["schedule"]),
            "adapted": True,
            "regenerated": False,
            "base_plan_id": str(base_plan.id),
            "night_wake_protocol": base_content.get("night_wake_protocol")
            or dict(plan_adapter.NIGHT_WAKE_PROTOCOL),
            "adaptation": _adaptation_meta(result, summary,
                                           adjusted=result["adjusted"],
                                           shift=result["shift_minutes"],
                                           required=False),
        })

    plan = upsert_plan(db, user, baby, today, content)
    return plan, result


# =============================================================================
# ORTAK GİRİŞ NOKTASI — GET /plans/today ve bildirim zamanlayıcısı bunu kullanır
# =============================================================================
def already_adapted_today(plan: SleepPlan | None) -> bool:
    """Bugünün planı zaten adapte edilmiş mi? (gereksiz DB yazımını önler)"""
    return bool(plan is not None and (plan.content or {}).get("adapted"))


def ensure_today_plan(db: Session, user: User, baby: Baby,
                      today: date | None = None) -> SleepPlan | None:
    """Bugünün planını döndür; gerekiyorsa lazy adaptasyonu ÇALIŞTIR.

    Akış:
      1. Bugünün planı var ve ZATEN ADAPTE EDİLMİŞ → olduğu gibi dön (yazma YOK).
      2. Bugünün planı var ama adapte değil → son 3 günün logları varsa adapte et.
      3. Bugünün planı yok → en güncel planı bugüne taşı; log varsa adapte ederek.
      4. Hiç plan yok → None (çağıran 404/atlama kararını verir).

    Zamanlayıcı da bunu çağırır: kullanıcı uygulamayı hiç açmasa bile bildirim
    güncel kaydırılmış saate göre gider."""
    today = today or datetime.now(timezone.utc).date()
    bugunku = plan_for_date(db, user, baby, today)

    # 1) Bugün zaten adapte edilmiş → ikinci kez adapt YOK.
    if already_adapted_today(bugunku):
        return ensure_current_schema(db, bugunku)

    base_plan = bugunku or latest_plan(db, user, baby)
    if base_plan is None:
        return None                                   # hiç plan üretilmemiş

    logs = recent_logs(db, user, baby, today)
    if logs:
        plan, _ = run_adaptation(db, user, baby, base_plan, logs, today)
        return plan

    # 3b) Kayıt yok → adaptasyon yok. Bugünün planı varsa dokunma; yoksa taşı.
    if bugunku is not None:
        return ensure_current_schema(db, bugunku)

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
        "kestirme_protokolu": (content.get("kestirme_protokolu")
                               or yas_bantlari.kestirme_protokolu()),
    })
    return upsert_plan(db, user, baby, today, content)
