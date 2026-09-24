"""
Plan üretim işleri — asenkron job registry (Faz G1).

SORUN: POST /plans/generate senkron çalışıyordu ve tek istek ~129 sn sürüyordu
(Sonnet uzun markdown çıktısı). Sync endpoint'ler uvicorn threadpool'unda koşar;
birkaç eşzamanlı üretim tüm thread'leri bloke edip /health dahil her şeyi
kilitliyordu (denetim Bölüm 2.3/3.3). Artık üretim ARKA PLANDA koşar, istemci
job_id ile durumu yoklar.

TASARIM: işi KOŞTURAN süreçte in-memory registry (kuyruk sırası ve havuz o
süreçte) + her durum değişikliğinde `plan_uretim_isleri` tablosuna YAZIM
ORTAKLIĞI. D-3 (2026-09-24): uvicorn 4 worker ile koşuyor; yalnız bellek
varken yoklamaların ~3/4'ü başka worker'a düşüp 404 alıyordu ve mobil planın
"hazırlanamadığını" sanıyordu. Artık bellekte bulunmayan iş DB'den okunur.

DB yazımı EN İYİ ÇABADIR: yazılamazsa (tablo yok, bağlantı koptu) loglanır,
bellek kaydı yine çalışır — üretimin kendisi durum defteri yüzünden düşmez.

YENİDEN BAŞLATMAYA DAYANIKLILIK (2026-09-25 olayı): deploy sırasında konteyner
SIGTERM aldı, LLM çağrısındaki iş öldü ve satırı sonsuza dek "processing"
kaldı; mobil 6 dakika bekleyip vazgeçti. Artık:
  • Kapanışta (lifespan) süreç kendi yarım işlerinin `sahip`ini boşaltır →
    iş SAHİPSİZ (yetim) olur.
  • `bakim()` (üretimde dakikada bir, her worker'da) yetim işi ATOMİK olarak
    sahiplenir ve BİR KEZ yeniden koşar (deneme sayacı; ikinci kesintide failed).
  • BAYAT_IS_DK'yı aşan "processing" iş DB'ye GERÇEKTEN failed yazılır
    (eskiden yalnız okurken öyle gösteriliyordu) — Türkçe "tekrar dene" mesajıyla.
  • Hesap silinince kullanıcının işleri iptal edilir; üretim bittiğinde bebek
    ya da kullanıcı yoksa plan YAZILMAZ (FK hatası yerine temiz iptal).
Hata mesajları anneye gösterilir: teknik ayrıntı yalnız loga gider.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

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


# Süreç yeniden başlarsa DB'de "processing" kalan iş sonsuza dek beklemesin.
# Üretim en kötü ~2 × 140 sn; 15 dk bol bol üstü.
BAYAT_IS_DK = 15
BAYAT_IS_MESAJ = ("Planınız hazırlanırken bir aksilik oldu. "
                  "Lütfen planı yeniden oluşturmayı deneyin.")
HATA_MESAJ = "Plan hazırlanamadı. Lütfen birkaç dakika sonra tekrar deneyin."
IPTAL_MESAJ = "Hesap ya da bebek silindiği için plan hazırlığı iptal edildi."
# Yetim iş en fazla bu kadar kez yeniden koşulur (sürekli kesilen iş döngüye girmesin).
MAX_DENEME = 1

# Bu sürecin kimliği — işin hangi süreçte koştuğu (konteyner:pid).
SUREC_KIMLIGI = f"{socket.gethostname()}:{os.getpid()}"


def _db_yaz(job_id: str, **alanlar) -> None:
    """Durumu paylaşılan tabloya yaz (en iyi çaba). `api.db.session`dan
    okunur — birim testleri `api.db.SessionLocal`ı bozarak üretim hatası
    simüle ediyor; defter o sahte hataya takılmamalı."""
    try:
        from api.db.session import SessionLocal
        from api.models import PlanUretimIsi
        db = SessionLocal()
        try:
            satir = db.get(PlanUretimIsi, job_id)
            if satir is None:
                satir = PlanUretimIsi(id=job_id, **alanlar)
                db.add(satir)
            else:
                for k, v in alanlar.items():
                    setattr(satir, k, v)
            db.commit()
        finally:
            db.close()
    except Exception as e:                      # defter yazılamadı → bellek yeter
        logger.warning("Plan işi durumu DB'ye yazılamadı (job=%s): %s", job_id, e)


def _db_oku(job_id: str, user_id) -> dict | None:
    """Başka worker'ın işi. Sahibi değilse None (router 404)."""
    try:
        from api.db.session import SessionLocal
        from api.models import PlanUretimIsi
        db = SessionLocal()
        try:
            s = db.get(PlanUretimIsi, job_id)
            if s is None or s.user_id != str(user_id):
                return None
            job = {"status": s.status, "started": s.started,
                   "user_id": s.user_id, "baby_id": s.baby_id,
                   "plan_id": s.plan_id, "error": s.error,
                   "created_at": s.created_at}
        finally:
            db.close()
    except Exception as e:
        logger.warning("Plan işi durumu DB'den okunamadı (job=%s): %s", job_id, e)
        return None
    olusma = job["created_at"]
    if olusma is not None and olusma.tzinfo is None:
        olusma = olusma.replace(tzinfo=timezone.utc)
    if (job["status"] == STATUS_PROCESSING and olusma is not None
            and (datetime.now(timezone.utc) - olusma).total_seconds()
            > BAYAT_IS_DK * 60):
        job.update(status=STATUS_FAILED, error=BAYAT_IS_MESAJ)
        _db_yaz(job_id, status=STATUS_FAILED, error=BAYAT_IS_MESAJ)
    # Kuyruk sırası yalnız işi koşturan süreçte kesin; burada kaba tahmin.
    return dict(job, queue_position=0 if job["started"] else 1)


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
        kayit = dict(_JOBS[job_id])
    _db_yaz(job_id, status=kayit["status"], started=False,
            user_id=kayit["user_id"], baby_id=kayit["baby_id"],
            created_at=kayit["created_at"], sahip=SUREC_KIMLIGI, deneme=0)
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


def submit(job_id: str, baby_id, req_overrides, dogum_haftasi,
           ek_icerik: dict | None = None) -> None:
    """İşi adanmış havuza ver. Havuz doluysa iş kuyrukta bekler (slot açılınca
    başlar) — çağıran thread BLOKE OLMAZ, istemci 202'yi hemen alır.

    Parametreler DB'ye de yazılır: süreç ölürse iş başka bir süreçte AYNI
    girdilerle yeniden koşulabilsin (bakim → yetimleri devral)."""
    _db_yaz(job_id, parametreler={"req_overrides": req_overrides,
                                  "dogum_haftasi": dogum_haftasi,
                                  "ek_icerik": ek_icerik})
    _EXECUTOR.submit(run_generation, job_id, baby_id, req_overrides,
                     dogum_haftasi, ek_icerik)


def get_job(job_id: str, user_id) -> dict | None:
    """İşi döndür — YALNIZ sahibi görebilir (başka kullanıcı → None → router 404).

    queue_position: 0 = üretim sürüyor (ya da bitti); >0 = önünde kaç iş var."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            if job["user_id"] != str(user_id):
                return None
            return dict(job, queue_position=_kuyruk_sirasi(job_id))  # kopya
    # Bu süreçte yok → başka worker'ın işi olabilir (D-3).
    return _db_oku(job_id, user_id)


def _set(job_id: str, **fields) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(fields)
    _db_yaz(job_id, **fields)


def run_generation(job_id: str, baby_id, req_overrides, dogum_haftasi,
                   ek_icerik: dict | None = None) -> None:
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
            _set(job_id, status=STATUS_FAILED, error=IPTAL_MESAJ)
            return
        user = db.get(User, baby.user_id)
        bebek_id, kullanici_id = baby.id, baby.user_id
        content = plan_service.generate_content(baby, req_overrides, dogum_haftasi)
        # Üretim ~2 dk sürüyor; bu sürede hesap silinmiş ya da iş iptal
        # edilmiş olabilir. Silinmiş bebeğe plan yazmak FK hatası verirdi.
        db.expire_all()
        baby, user = db.get(Baby, bebek_id), db.get(User, kullanici_id)
        if baby is None or user is None or _iptal_mi(job_id):
            _set(job_id, status=STATUS_FAILED, error=IPTAL_MESAJ)
            logger.info("Plan job iptal: job=%s baby=%s (silindi/iptal)",
                        job_id, bebek_id)
            return
        # `ek_icerik` üretimi TETİKLEYEN olayın izini içeriğe taşır (ör. 5 ay
        # geçişi). Bildirim katmanı "bu plan neden üretildi" sorusunu ancak
        # buradan cevaplayabilir; iş sözlüğü istek bitince kaybolur.
        if ek_icerik:
            content.update(ek_icerik)
        today = datetime.now(timezone.utc).date()
        plan = plan_service.upsert_plan(db, user, baby, today, content)
        _set(job_id, status=STATUS_DONE, plan_id=str(plan.id))
        logger.info("Plan job tamam: job=%s plan=%s baby=%s (%s)",
                    job_id, plan.id, baby.id, content.get("generated_with"))
    except Exception:                                   # PlanError dahil → failed
        # Teknik ayrıntı LOGDA; anneye gösterilen metin Türkçe ve eyleme dönük.
        logger.exception("Plan job başarısız: job=%s baby=%s", job_id, baby_id)
        _set(job_id, status=STATUS_FAILED, error=HATA_MESAJ)
    finally:
        db.close()


def _iptal_mi(job_id: str) -> bool:
    """İş dışarıdan (hesap silme) sonlandırıldı mı? Bellek ya da DB."""
    with _LOCK:
        j = _JOBS.get(job_id)
        if j is not None and j.get("status") == STATUS_FAILED:
            return True
    try:
        from api.db.session import SessionLocal
        from api.models import PlanUretimIsi
        db = SessionLocal()
        try:
            satir = db.get(PlanUretimIsi, job_id)
            return satir is not None and satir.status != STATUS_PROCESSING
        finally:
            db.close()
    except Exception:
        return False


def kullanici_islerini_iptal(user_id) -> int:
    """Hesap silinirken: kullanıcının süren işlerini iptal et. Koşan iş LLM
    çağrısını bitirir ama sonucu YAZMAZ (run_generation iptali görür)."""
    n = 0
    with _LOCK:
        for j in _JOBS.values():
            if j["user_id"] == str(user_id) and j["status"] == STATUS_PROCESSING:
                j.update(status=STATUS_FAILED, error=IPTAL_MESAJ)
    try:
        from api.db.session import SessionLocal
        from api.models import PlanUretimIsi
        db = SessionLocal()
        try:
            for satir in (db.query(PlanUretimIsi)
                          .filter(PlanUretimIsi.user_id == str(user_id),
                                  PlanUretimIsi.status == STATUS_PROCESSING).all()):
                satir.status, satir.error = STATUS_FAILED, IPTAL_MESAJ
                n += 1
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("Kullanıcı işleri iptal edilemedi (user=%s): %s", user_id, e)
    return n


def bebek_son_isi(baby_id) -> dict | None:
    """Bebeğin EN SON plan işi (GET /plans/today 404'ünü anlamlı kılmak için)."""
    try:
        from api.db.session import SessionLocal
        from api.models import PlanUretimIsi
        db = SessionLocal()
        try:
            s_ = (db.query(PlanUretimIsi)
                  .filter(PlanUretimIsi.baby_id == str(baby_id))
                  .order_by(PlanUretimIsi.created_at.desc()).first())
            return None if s_ is None else {"job_id": s_.id, "status": s_.status,
                                            "error": s_.error}
        finally:
            db.close()
    except Exception:
        return None


def sahipsizlestir() -> int:
    """Kapanışta (SIGTERM): bu süreçteki yarım işleri YETİM işaretle ki başka
    bir süreç devralabilsin."""
    with _LOCK:
        yarim = [k for k, j in _JOBS.items() if j["status"] == STATUS_PROCESSING]
    for k in yarim:
        _db_yaz(k, sahip=None)
    if yarim:
        logger.info("Kapanış: %d yarım plan işi yetim bırakıldı", len(yarim))
    return len(yarim)


def bakim(simdi: datetime | None = None) -> dict:
    """(1) BAYAT_IS_DK'yı aşan processing işleri DB'de failed yaz; (2) yetim
    işleri atomik sahiplenip yeniden koş (en fazla MAX_DENEME kez)."""
    from sqlalchemy import update
    from api.db.session import SessionLocal
    from api.models import PlanUretimIsi

    simdi = simdi or datetime.now(timezone.utc)
    sinir = simdi - timedelta(minutes=BAYAT_IS_DK)
    sonuc = {"bayat": 0, "devralinan": 0, "vazgecilen": 0}
    db = SessionLocal()
    try:
        bayatlar = (db.query(PlanUretimIsi)
                    .filter(PlanUretimIsi.status == STATUS_PROCESSING,
                            PlanUretimIsi.created_at < sinir).all())
        for b in bayatlar:
            b.status, b.error = STATUS_FAILED, BAYAT_IS_MESAJ
        db.commit()
        sonuc["bayat"] = len(bayatlar)

        yetimler = (db.query(PlanUretimIsi)
                    .filter(PlanUretimIsi.status == STATUS_PROCESSING,
                            PlanUretimIsi.sahip.is_(None)).all())
        for y in yetimler:
            alindi = db.execute(
                update(PlanUretimIsi)
                .where(PlanUretimIsi.id == y.id, PlanUretimIsi.sahip.is_(None),
                       PlanUretimIsi.status == STATUS_PROCESSING)
                .values(sahip=SUREC_KIMLIGI, deneme=PlanUretimIsi.deneme + 1,
                        started=False)
                .execution_options(synchronize_session=False)).rowcount
            db.commit()
            if alindi != 1:
                continue                                # başka worker aldı
            db.refresh(y)
            if (y.deneme or 0) > MAX_DENEME:
                y.status, y.error = STATUS_FAILED, BAYAT_IS_MESAJ
                db.commit()
                sonuc["vazgecilen"] += 1
                continue
            p = y.parametreler or {}
            with _LOCK:
                _JOBS[y.id] = {"status": STATUS_PROCESSING, "started": False,
                               "user_id": y.user_id, "baby_id": y.baby_id,
                               "plan_id": None, "error": None,
                               # SQLite saat dilimsiz döner; kuyruk sırası
                               # karşılaştırması aware tarihlerle yapılıyor.
                               "created_at": (y.created_at if y.created_at.tzinfo
                                              else y.created_at.replace(tzinfo=timezone.utc))}
            _EXECUTOR.submit(run_generation, y.id, y.baby_id, p.get("req_overrides"),
                             p.get("dogum_haftasi"), p.get("ek_icerik"))
            sonuc["devralinan"] += 1
            logger.info("Yetim plan işi devralındı: job=%s deneme=%s", y.id, y.deneme)
    finally:
        db.close()
    return sonuc


_bakim_zamanlayici: threading.Timer | None = None
BAKIM_ARALIGI_SN = 60


def bakim_baslat() -> None:
    """Üretimde dakikada bir bakim(). Her worker koşar; sahiplenme atomik."""
    global _bakim_zamanlayici

    def _tur():
        global _bakim_zamanlayici
        try:
            s_ = bakim()
            if any(s_.values()):
                logger.info("Plan işi bakımı: %s", s_)
        except Exception:
            logger.exception("Plan işi bakımı başarısız")
        _bakim_zamanlayici = threading.Timer(BAKIM_ARALIGI_SN, _tur)
        _bakim_zamanlayici.daemon = True
        _bakim_zamanlayici.start()

    _bakim_zamanlayici = threading.Timer(5, _tur)
    _bakim_zamanlayici.daemon = True
    _bakim_zamanlayici.start()


def bakim_durdur() -> None:
    if _bakim_zamanlayici is not None:
        _bakim_zamanlayici.cancel()


def reset() -> None:
    """Test yardımcısı — kayıtları temizle."""
    with _LOCK:
        _JOBS.clear()
