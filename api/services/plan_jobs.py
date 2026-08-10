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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger("tavsan.plan_jobs")

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}     # job_id -> {status,started,user_id,baby_id,plan_id,...}
MAX_JOBS = 1000                 # bellek şişmesi koruması: en eski işleri at

# Faz O2 — EŞZAMANLILIK SINIRI. Bir plan üretimi ~90-140 sn sürüyor ve bu süre
# boyunca bir thread tutuluyor. Sınırsız bırakılırsa yoğun anda hem Anthropic
# hız sınırına çarpılır hem de kuyruk görünmez şekilde uzar. Üretim ARTIK
# uvicorn threadpool'unda değil, bu ADANMIŞ havuzda koşar — böylece plan
# üretimi ne kadar yığılırsa yığılsın /health ve diğer uçlar etkilenmez.
MAX_ESZAMANLI = 3
_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_ESZAMANLI,
                               thread_name_prefix="plan-gen")

STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


def create_job(user_id, baby_id) -> str:
    """Yeni 'processing' işi kaydet, job_id döndür.

    started=False → iş henüz kuyrukta (slot bekliyor). run_generation başlarken
    True'ya çeker; kuyruk sırası bu bayraktan hesaplanır."""
    job_id = str(uuid.uuid4())
    with _LOCK:
        if len(_JOBS) >= MAX_JOBS:                      # LRU: en eski birkaçını at
            fazla = len(_JOBS) - MAX_JOBS + 1
            for old in sorted(_JOBS, key=lambda k: _JOBS[k]["created_at"])[:fazla]:
                _JOBS.pop(old, None)
        _JOBS[job_id] = {
            "status": STATUS_PROCESSING,
            "started": False,
            "user_id": str(user_id),
            "baby_id": str(baby_id),
            "plan_id": None,
            "error": None,
            "created_at": datetime.now(timezone.utc),
        }
    return job_id


def _kuyruk_sirasi(job_id: str) -> int:
    """Kaç iş bu işten ÖNCE slot bekliyor + 1. Çalışmaya başlamışsa 0.

    Çağıran _LOCK'u tutuyor olmalı."""
    job = _JOBS.get(job_id)
    if job is None or job["started"] or job["status"] != STATUS_PROCESSING:
        return 0
    onceki = sum(1 for j in _JOBS.values()
                 if not j["started"] and j["status"] == STATUS_PROCESSING
                 and j["created_at"] < job["created_at"])
    return onceki + 1


def submit(job_id: str, baby_id, req_overrides, dogum_haftasi) -> None:
    """İşi adanmış havuza ver. Havuz doluysa iş kuyrukta bekler (slot açılınca
    başlar) — çağıran thread BLOKE OLMAZ, istemci 202'yi hemen alır."""
    _EXECUTOR.submit(run_generation, job_id, baby_id, req_overrides, dogum_haftasi)


def get_job(job_id: str, user_id) -> dict | None:
    """İşi döndür — YALNIZ sahibi görebilir (başka kullanıcı → None → router 404).

    queue_position: 0 = üretim sürüyor (ya da bitti); >0 = önünde kaç iş var."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None or job["user_id"] != str(user_id):
            return None
        return dict(job, queue_position=_kuyruk_sirasi(job_id))  # kopya: dış mutasyon olmasın


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

    _set(job_id, started=True)          # kuyruktan çıktı, slotu tutuyor
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
