"""
Bildirim servisi — Expo Push + uygulama içi zamanlayıcı (Faz 6.2).

TASARIM KARARI: ayrı worker/queue YOK. v1 trafiği için uygulama içinde APScheduler
yeterli ve operasyonel yükü sıfır. Ölçek büyürse (çok instance) bu zamanlayıcı ayrı
bir servise taşınmalı — aksi halde her instance aynı bildirimi göndermeye çalışır.
Şimdilik mükerrerliği sent_notifications tablosundaki UNIQUE kısıt engeller.

Akış (her 15 dakikada bir):
    bugünün planı olan her bebek için → çizelgedeki uyku bloklarından, önümüzdeki
    15-30 dk penceresinde BAŞLAYANLARI bul → sahibinin tüm cihaz token'larına
    "🌙 {ad} için uyku vakti yaklaşıyor (19:30)" gönder → deftere yaz.

Hata politikası:
    DeviceNotRegistered → token SİLİNİR (cihaz uygulamayı kaldırmış).
    Diğer hatalar       → loglanır, token korunur (geçici olabilir).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.config import get_settings
from api.models import (
    Baby, PushToken, SentNotification, SleepPlan, User,
)
from api.models.user import DEFAULT_NOTIFICATION_PREFS
from api.services import plan_adapter

logger = logging.getLogger("tavsan.notifier")

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_TIMEOUT = 15

# Bildirim penceresi: blok başlangıcı "şimdi + 15dk" ile "şimdi + 30dk" arasındaysa.
# Zamanlayıcı 15 dakikada bir koştuğu için her blok bu pencereye TAM BİR KEZ girer;
# yine de defter (sent_notifications) mükerrerliği garantiye alır.
WINDOW_MIN_AHEAD = 15
WINDOW_MAX_AHEAD = 30
SCHEDULER_INTERVAL_MIN = 15


# =============================================================================
# Expo Push istemcisi
# =============================================================================
def send_expo_push(messages: list[dict]) -> list[dict]:
    """Expo Push API'ye toplu gönderim. Dönen 'data' listesi mesaj sırasıyla eşleşir.

    Ağ/HTTP hatasında boş liste döner (çağıran bunu 'gönderilemedi' sayar) —
    exception fırlatmaz ki zamanlayıcı tek bir hatada durmasın."""
    if not messages:
        return []
    try:
        r = requests.post(EXPO_PUSH_URL, json=messages, timeout=EXPO_TIMEOUT,
                          headers={"Content-Type": "application/json",
                                   "Accept": "application/json"})
    except Exception as e:
        logger.warning("Expo push isteği başarısız: %s", e)
        return []
    if not r.ok:
        logger.warning("Expo push HTTP %s: %s", r.status_code, r.text[:300])
        return []
    try:
        payload = r.json()
    except Exception:
        logger.warning("Expo push yanıtı JSON değil: %s", r.text[:200])
        return []
    data = payload.get("data")
    if isinstance(data, dict):          # tek mesaj gönderildiyse dict dönebilir
        data = [data]
    return data or []


def _is_device_not_registered(ticket: dict) -> bool:
    """Expo 'DeviceNotRegistered' → token ölü, silinmeli."""
    if ticket.get("status") != "error":
        return False
    details = ticket.get("details") or {}
    return details.get("error") == "DeviceNotRegistered"


def push_to_user(db: Session, user_id: Any, title: str, body: str,
                 data: dict | None = None) -> int:
    """Kullanıcının TÜM cihazlarına bildirim gönder. Dönen: başarılı gönderim sayısı.

    DeviceNotRegistered dönen token'lar silinir."""
    tokens = db.query(PushToken).filter(PushToken.user_id == user_id).all()
    if not tokens:
        return 0

    messages = [{
        "to": t.expo_token,
        "title": title,
        "body": body,
        "sound": "default",
        **({"data": data} if data else {}),
    } for t in tokens]

    tickets = send_expo_push(messages)
    ok_count = 0
    dead: list[PushToken] = []
    for tok, ticket in zip(tokens, tickets):
        if not isinstance(ticket, dict):
            continue
        if ticket.get("status") == "ok":
            ok_count += 1
        elif _is_device_not_registered(ticket):
            dead.append(tok)
        else:
            logger.warning("Expo push hatası (token=%s...): %s",
                           tok.expo_token[:18], ticket.get("message"))

    for tok in dead:
        logger.info("DeviceNotRegistered → push token siliniyor (user=%s)", tok.user_id)
        db.delete(tok)
    if dead:
        db.commit()
    return ok_count


# =============================================================================
# Pencere hesaplama
# =============================================================================
def _prefs(user: User) -> dict:
    """Kullanıcı tercihleri; NULL/eksik alanlar varsayılana düşer (geriye uyum)."""
    prefs = dict(DEFAULT_NOTIFICATION_PREFS)
    if isinstance(getattr(user, "notification_prefs", None), dict):
        prefs.update(user.notification_prefs)
    return prefs


def upcoming_blocks(schedule: list[dict], now_local_minute: int,
                    min_ahead: int = WINDOW_MIN_AHEAD,
                    max_ahead: int = WINDOW_MAX_AHEAD) -> list[dict]:
    """Çizelgeden, [şimdi+min_ahead, şimdi+max_ahead] penceresinde BAŞLAYAN uyku
    bloklarını döndür. Yalnız uyku blokları ('nap'/'sleep') — 'wake' bildirilmez."""
    lo, hi = now_local_minute + min_ahead, now_local_minute + max_ahead
    out = []
    # Eski şema (type="night") normalize edilmezse gece bloğu HİÇ bildirilmez.
    for b in plan_adapter.normalize_schedule(schedule):
        if b.get("type") not in ("nap", "sleep"):
            continue
        start = b.get("start_minute")
        if start is None:
            continue
        # Gece bloğu ertesi güne sarabilir; hem kendisini hem +24s halini dene.
        for candidate in (start, start + 24 * 60):
            if lo <= candidate <= hi:
                out.append(b)
                break
    return out


def _block_key(plan_date: date, block: dict) -> str:
    """Deftere yazılacak anahtar: aynı blok ertesi gün yeniden bildirilebilsin."""
    return f"{plan_date.isoformat()}:{block.get('key')}"


def _already_sent(db: Session, user_id: Any, plan_id: Any, block_key: str) -> bool:
    return db.query(SentNotification).filter(
        SentNotification.user_id == user_id,
        SentNotification.plan_id == plan_id,
        SentNotification.block_key == block_key).first() is not None


def _mark_sent(db: Session, user_id: Any, plan_id: Any, block_key: str) -> bool:
    """Defteri işaretle. UNIQUE ihlali → başka bir koşu aynı anda gönderdi (False)."""
    db.add(SentNotification(user_id=user_id, plan_id=plan_id, block_key=block_key))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


# =============================================================================
# Zamanlayıcı işi
# =============================================================================
def run_reminder_cycle(db: Session, now: datetime | None = None,
                       tz_offset_min: int = plan_adapter.TZ_OFFSET_MIN) -> dict:
    """Bir tur hatırlatma gönderimi. Test edilebilir olsun diye `now` enjekte edilir.

    Dönen: {'checked_plans': n, 'sent': n, 'skipped_duplicate': n}"""
    now = now or datetime.now(timezone.utc)
    local_now = now + timedelta(minutes=tz_offset_min)
    today_local = local_now.date()
    now_minute = local_now.hour * 60 + local_now.minute

    plans = (db.query(SleepPlan).filter(SleepPlan.plan_date == today_local).all())
    stats = {"checked_plans": len(plans), "sent": 0, "skipped_duplicate": 0}

    for plan in plans:
        content = plan.content or {}
        blocks = upcoming_blocks(content.get("schedule") or [], now_minute)
        if not blocks:
            continue

        user = db.get(User, plan.user_id)
        if user is None or not _prefs(user).get("plan_reminders", True):
            continue
        baby = db.get(Baby, plan.baby_id)
        baby_name = baby.name if baby is not None else "Bebeğiniz"

        for block in blocks:
            key = _block_key(plan.plan_date, block)
            if _already_sent(db, user.id, plan.id, key):
                stats["skipped_duplicate"] += 1
                continue
            # ÖNCE deftere yaz, SONRA gönder: çift gönderim, hiç göndermemekten
            # daha kötüdür (kullanıcıyı rahatsız eder ve geri alınamaz).
            if not _mark_sent(db, user.id, plan.id, key):
                stats["skipped_duplicate"] += 1
                continue

            title = "🌙 Uyku vakti yaklaşıyor"
            body = f"{baby_name} için uyku vakti yaklaşıyor ({block.get('time')})"
            sent = push_to_user(db, user.id, title, body,
                                data={"type": "plan_reminder",
                                      "plan_id": str(plan.id),
                                      "block_key": block.get("key")})
            stats["sent"] += sent
            logger.info("Hatırlatma: user=%s baby=%s blok=%s cihaz=%d",
                        user.id, plan.baby_id, block.get("key"), sent)
    return stats


# =============================================================================
# APScheduler kurulumu
# =============================================================================
_scheduler = None


def _job() -> None:
    """Zamanlayıcı işi — kendi DB oturumunu açar ve HER durumda kapatır."""
    from api.db import SessionLocal
    db = SessionLocal()
    try:
        stats = run_reminder_cycle(db)
        if stats["sent"] or stats["skipped_duplicate"]:
            logger.info("Hatırlatma turu: %s", stats)
    except Exception:
        logger.exception("Hatırlatma turu başarısız")
    finally:
        db.close()


def start_scheduler() -> bool:
    """Zamanlayıcıyı başlat. Yalnız ENVIRONMENT=production'da çalışır.

    Lokal/test ortamında başlatılmaz — geliştirme sırasında gerçek kullanıcılara
    bildirim gitmesini ve test kirliliğini önler. Dönen: başlatıldı mı."""
    global _scheduler
    settings = get_settings()
    if not settings.is_production:
        logger.info("Zamanlayıcı BAŞLATILMADI (ENVIRONMENT=%s, production değil)",
                    settings.environment)
        return False
    if _scheduler is not None:
        return True
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("APScheduler kurulu değil — bildirim zamanlayıcısı devre dışı")
        return False

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(_job, "interval", minutes=SCHEDULER_INTERVAL_MIN,
                       id="plan_reminders", max_instances=1, coalesce=True)
    _scheduler.start()
    logger.info("Bildirim zamanlayıcısı başladı (her %d dk)", SCHEDULER_INTERVAL_MIN)
    return True


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
