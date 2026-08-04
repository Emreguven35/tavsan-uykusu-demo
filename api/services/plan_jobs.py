"""
Plan üretim işleri — asenkron job registry (Faz G1).

SORUN: POST /plans/generate senkron çalışıyordu ve tek istek ~129 sn sürüyordu
(Sonnet uzun markdown çıktısı). Sync endpoint'ler uvicorn threadpool'unda koşar;
birkaç eşzamanlı üretim tüm thread'leri bloke edip /health dahil her şeyi
kilitliyordu (denetim Bölüm 2.3/3.3). Artık üretim ARKA PLANDA koşar, istemci
job_id ile durumu yoklar.

TASARIM: tek-instance (uvicorn tek process) için IN-MEMORY registry yeterli ve
operasyonel yükü sıfır. Süreç yeniden başlarsa devam eden işler kaybolur —
istemci job'ı bulamazsa (404) yeniden tetikler; plan yine de üretilmişse
GET /plans/today onu döndürür. ÇOK-INSTANCE'A geçilirse job durumu DB/Redis'e
taşınmalı (bir instance'ın job'ını diğeri göremez) — bkz. denetim mimari sınırlar.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("tavsan.plan_jobs")

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}     # job_id -> {status,user_id,baby_id,plan_id,error,created_at}
MAX_JOBS = 1000                 # bellek şişmesi koruması: en eski işleri at

STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


def create_job(user_id, baby_id) -> str:
    """Yeni 'processing' işi kaydet, job_id döndür."""
    job_id = str(uuid.uuid4())
    with _LOCK:
        if len(_JOBS) >= MAX_JOBS:                      # LRU: en eski birkaçını at
            fazla = len(_JOBS) - MAX_JOBS + 1
            for old in sorted(_JOBS, key=lambda k: _JOBS[k]["created_at"])[:fazla]:
                _JOBS.pop(old, None)
        _JOBS[job_id] = {
            "status": STATUS_PROCESSING,
            "user_id": str(user_id),
            "baby_id": str(baby_id),
            "plan_id": None,
            "error": None,
            "created_at": datetime.now(timezone.utc),
        }
    return job_id


def get_job(job_id: str, user_id) -> dict | None:
    """İşi döndür — YALNIZ sahibi görebilir (başka kullanıcı → None → router 404)."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None or job["user_id"] != str(user_id):
            return None
        return dict(job)                                # kopya: dışarıda mutasyon olmasın


def _set(job_id: str, **fields) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def run_generation(job_id: str, baby_id, req_overrides, dogum_haftasi) -> None:
    """Arka plan işi: planı üret + upsert et. KENDİ DB oturumunu açar (istek
    oturumu yanıt gönderilince kapandı) ve HER durumda kapatır — notifier._job
    ile aynı desen. Sahiplik router'da 202'den ÖNCE doğrulandı; burada yalnız üretir.
    """
    from api.db import SessionLocal
    from api.models import Baby, User
    from api.services import plan_service

    db = SessionLocal()
    try:
        baby = db.get(Baby, uuid.UUID(str(baby_id)))
        if baby is None:                               # araya silme girdiyse
            _set(job_id, status=STATUS_FAILED, error="Bebek bulunamadı")
            return
        user = db.get(User, baby.user_id)
        content = plan_service.generate_content(baby, req_overrides, dogum_haftasi)
        today = datetime.now(timezone.utc).date()
        plan = plan_service.upsert_plan(db, user, baby, today, content)
        _set(job_id, status=STATUS_DONE, plan_id=str(plan.id))
        logger.info("Plan job tamam: job=%s plan=%s baby=%s (%s)",
                    job_id, plan.id, baby.id, content.get("generated_with"))
    except Exception as e:                              # PlanError dahil → failed
        logger.exception("Plan job başarısız: job=%s baby=%s", job_id, baby_id)
        _set(job_id, status=STATUS_FAILED, error=str(e))
    finally:
        db.close()


def reset() -> None:
    """Test yardımcısı — kayıtları temizle."""
    with _LOCK:
        _JOBS.clear()
