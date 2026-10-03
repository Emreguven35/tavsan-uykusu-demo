"""
Bildirim servisi — Expo Push + uygulama içi zamanlayıcı (Faz 6.2).

TASARIM KARARI: ayrı worker/queue YOK. v1 trafiği için uygulama içinde APScheduler
yeterli ve operasyonel yükü sıfır. Ölçek büyürse (çok instance) bu zamanlayıcı ayrı
bir servise taşınmalı — aksi halde her instance aynı bildirimi göndermeye çalışır.
Şimdilik mükerrerliği sent_notifications tablosundaki UNIQUE kısıt engeller.

Akış (her 5 dakikada bir, v2.7):
    planı olan her bebek için →
      1. plan_service.ensure_today_plan() ile BUGÜNÜN planını hazırla/adapte et
         (GET /plans/today ile AYNI kod yolu; bugün zaten adapte edildiyse yazmaz),
      2. SABAH KURALI (İlayda, 2026-10-03): bugün sabah uyanışı girilmemişse
         HİÇBİR uyku bildirimi gitmez; yerine yaşa göre hedef uyanış + 30 dk'da
         "Günaydın! {ad} uyandı mı? ☀️", girilmezse 1 saat sonra bir kez daha,
      3. sabah uyanışı girildiyse her uyku bloğu için UYKU DİZİSİ: 30 dk önce
         (gündüz: yorulmaya başladı / gece: rutin zamanı), zamanında (uyku
         zamanı), 30 dk sonra hâlâ uyumadıysa (biraz daha uyanık kalmak
         istiyor). Uyku başlayınca o bloğun kalan adımları düşer.
    Saatler güncel (adapte edilmiş) plandan gelir: plan kayınca dizi de kayar.

Hata politikası:
    DeviceNotRegistered → token SİLİNİR (cihaz uygulamayı kaldırmış).
    Diğer hatalar       → loglanır, token korunur (geçici olabilir).
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.config import get_settings
from api.models import (
    Baby, PushToken, SentNotification, SleepLog, SleepPlan, User,
)
from api.models.user import DEFAULT_NOTIFICATION_PREFS
from api.services import plan_adapter, plan_service

logger = logging.getLogger("tavsan.notifier")

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_TIMEOUT = 15

# GÖNDERİM YAYMA (v2.4.1) — sabit saatli bildirim binlerce anneyi aynı anda
# uygulamaya sokuyor ve /plans/today kuyruğa giriyordu (yayın öncesi ölçüm:
# 50 eşzamanlıda p95 15 sn). Her kullanıcı kendi DETERMİNİSTİK kaymasını alır:
# aynı anne her gün aynı dakikada alır, anneler arasında gönderim yayılır.
# v2.7: uyku dizisi ±5 dk istediği için kayma `% 5` ile 0-4 dk'ya daraltılır.
BILDIRIM_YAYMA_DK = 10

# v2.7 — uyku dizisi ±5 dk hassasiyet istiyor; 15 dk'lık tur bunu veremez.
SCHEDULER_INTERVAL_MIN = 5

# --- SABAH KURALI + UYKU DİZİSİ (v2.7, 2026-10-03) ---------------------------
# Kategori adları mobil sözleşmesidir (data.type == data.kategori).
KAT_SABAH = "sabah_uyanis"
KAT_UYKU_ONCESI = "uyku_oncesi"          # 30 dk önce (gündüz/gece metni ayrı)
KAT_UYKU_ZAMANI = "uyku_zamani"          # tam zamanında
KAT_UYKU_HATIRLATMA = "uyku_hatirlatma"  # 30 dk sonra hâlâ uyumadıysa
TERCIH_UYKU = "uyku_hatirlatma_bildirimi"

SABAH_ILK_DK = 30                 # hedef uyanış + 30 dk
SABAH_TEKRAR_DK = 60              # girilmezse 1 saat sonra bir kez daha
SABAH_GUNLUK_LIMIT = 2
UYKU_ONCE_DK = 30
UYKU_SONRA_DK = 30
UYKU_GUNLUK_LIMIT = int(os.getenv("UYKU_BILDIRIM_GUNLUK_LIMIT") or 10)
# Adım zamanı geldikten sonra bu kadar dakika içinde gönderilebilir. Tur 5 dk;
# bir tur kaçsa (deploy, yavaş tur) bildirim yine gider ama bayatlamaz.
DIZI_GEC_TOLERANS_DK = 10
# Bu kadar önce başlamış uyku kaydı o bloğun uykusudur (biraz erken yatırılmış).
UYKU_BASLADI_ERKEN_DK = 45

SABAH_BASLIK = "Günaydın! {ad} uyandı mı? ☀️"
SABAH_GOVDE = ("Uyanış saatini girdiğinde günün uyku programını ona göre "
               "hazırlıyoruz.")
DIZI_METIN = {
    ("once", "gunduz"): ("{ad} yorulmaya başladı 🧸",
                         "Uykuya 30 dakika var; sakin oyunlara geç, uyku "
                         "işaretlerine bak."),
    ("once", "gece"): ("Rutin zamanı 🛁",
                       "Banyo, pijama, kitap; yatışa 30 dakika var."),
    ("zaman", None): ("Uyku zamanı ✨",
                      "Hazır olduğunuzda {ad_i} yatırabilirsin; Uyudu'ya "
                      "basmayı unutma."),
    ("sonra", None): ("{ad} biraz daha uyanık kalmak istiyor 🐣",
                      "Sorun değil, hazır olduğunuzda buradayız."),
}
# Build < 26 mobil, gece yatışından 15 dk önce KENDİ yerel rutin hatırlatmasını
# gösteriyor; backend'in "Rutin zamanı 🛁"u ona ikinci bildirim olurdu. Sürüm
# bilinmiyorsa da gönderilmez (eski istemci olma ihtimali yüksek).
GECE_RUTINI_MIN_BUILD = 26
DIZI_KATEGORI = {"once": KAT_UYKU_ONCESI, "zaman": KAT_UYKU_ZAMANI,
                 "sonra": KAT_UYKU_HATIRLATMA}

# --- "BEBEĞİNİZ UYANDI MI?" (v2.4.3) ----------------------------------------
# Açık kalan uyku kaydı yalnız o kaydı bozmaz: çizelge bir zincir olduğu için
# GÜNÜN GERİ KALANI tahmine döner (bkz. adaptation.siradaki_blok.guven).
# Anne genelde sayacı kapatmayı unutmuştur; tek ihtiyacı küçük bir dürtme.
UYANDI_MI_GECIKME_DK = int(os.getenv("UYANDI_MI_GECIKME_DK") or 20)
UYANDI_MI_GUNLUK_LIMIT = int(os.getenv("UYANDI_MI_GUNLUK_LIMIT") or 3)
# Gece sessizliği: 23:00-06:00 arasında sorulmaz. Uyuyan anneyi uyandıran
# bildirim, çözdüğü sorundan büyük bir sorundur.
UYANDI_MI_SESSIZ_BAS = 23 * 60
UYANDI_MI_SESSIZ_BIT = 6 * 60
# UYGULAMA AÇIK MI? Backend bunu KESİN bilemez. Elimizdeki tek sinyal
# PushToken.last_seen_at: mobil uygulamayı her açılışta token'ı tazeliyor
# (POST /notifications/register-token). Bu kadar dakika içinde tazelenmişse
# uygulama fiilen canlı sayılır ve YEREL bildirimi kendisi gösterebilir —
# backend araya girmez, anne iki kez dürtülmez. Sinyal bir TAHMİNDİR:
# 0 verilirse backend her zaman gönderir (mobil yerel bildirimi hiç
# kurmuyorsa doğru ayar budur).
UYANDI_MI_UYGULAMA_ACIK_DK = int(os.getenv("UYANDI_MI_UYGULAMA_ACIK_DK") or 30)
UYANDI_MI_BASLIK = "Bebeğiniz uyandı mı?"
UYANDI_MI_GOVDE = ("Bir sonraki uykuyu hesaplayabilmemiz için uyanma saatini "
                   "girin.")


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
# Topluluk cevap bildirimi (Faz T4)
# =============================================================================
# Eski notify_community_reply (Faz T4) KALDIRILDI (2026-10-01): metni kişi adı
# içeriyordu ve tekrar/sessiz saat/günlük tavan yoktu. Yerine
# api.services.topluluk_bildirim.cevap_olayi.


# =============================================================================
# Pencere hesaplama
# =============================================================================
def _prefs(user: User) -> dict:
    """Kullanıcı tercihleri; NULL/eksik alanlar varsayılana düşer (geriye uyum)."""
    prefs = dict(DEFAULT_NOTIFICATION_PREFS)
    if isinstance(getattr(user, "notification_prefs", None), dict):
        prefs.update(user.notification_prefs)
    return prefs


def yayma_dakikasi(user_id: Any) -> int:
    """Kullanıcıya özel, DEĞİŞMEYEN gönderim kayması (0..BILDIRIM_YAYMA_DK).

    `hash()` KULLANILMAZ: PYTHONHASHSEED süreçten sürece değişir ve aynı anne
    her gün başka dakikaya düşerdi. md5 süreçten bağımsızdır."""
    h = hashlib.md5(str(user_id).encode("utf-8")).digest()
    return h[0] % (BILDIRIM_YAYMA_DK + 1)


def geciken_acik_bloklar(schedule: list[dict], now_local_minute: int,
                         gecikme_dk: int = UYANDI_MI_GECIKME_DK) -> list[dict]:
    """Açık (sayacı kapatılmamış) uyku bloklarından planlanan bitişini
    `gecikme_dk` aşanlar.

    `devam=True` işaretini plan_adapter koyar: kayıt GERÇEKTEN açıktır
    (ended_at null) ve K17 eşiğini de aşmamıştır — eşiği aşanı motor zaten
    kendisi kapatıp `otomatik_kapandi` yazar, onu sormanın anlamı yok.

    Gece uykusu ertesi güne sarktığı için `end_minute` 1440'ı aşabilir; o blok
    bugünün turunda gecikmiş sayılmaz (karşılaştırma yerel dakikada yapılır).
    Zaten 23:00-06:00 arası hiç sorulmuyor."""
    esik = now_local_minute - gecikme_dk
    out = []
    for b in plan_adapter.normalize_schedule(schedule):
        if b.get("type") not in ("nap", "sleep") or not b.get("devam"):
            continue
        bitis = b.get("end_minute")
        if bitis is None or bitis > esik:
            continue
        out.append(b)
    return out


def sessiz_saat(now_local_minute: int) -> bool:
    """23:00-06:00 arası mı? (gece sessizliği)"""
    return (now_local_minute >= UYANDI_MI_SESSIZ_BAS
            or now_local_minute < UYANDI_MI_SESSIZ_BIT)


def _uygulama_acik_mi(db: Session, user_id: Any, now: datetime) -> bool:
    """Mobil uygulama YAKIN ZAMANDA görüldü mü? (UYANDI_MI_UYGULAMA_ACIK_DK)

    Hiç token yoksa zaten push gönderemeyiz; False dönmek akışı bozmaz."""
    if UYANDI_MI_UYGULAMA_ACIK_DK <= 0:
        return False
    son = (db.query(PushToken.last_seen_at)
           .filter(PushToken.user_id == user_id)
           .order_by(PushToken.last_seen_at.desc()).first())
    if son is None or son[0] is None:
        return False
    gorulme = son[0]
    if gorulme.tzinfo is None:                 # sqlite naive döndürebilir
        gorulme = gorulme.replace(tzinfo=timezone.utc)
    return (now - gorulme) <= timedelta(minutes=UYANDI_MI_UYGULAMA_ACIK_DK)


def _uyandi_mi_gunluk_sayi(db: Session, user_id: Any, gun: date) -> int:
    """Bugün bu kullanıcıya kaç kez "uyandı mı?" soruldu (tüm bebekleri dahil)."""
    return (db.query(SentNotification)
            .filter(SentNotification.user_id == user_id,
                    SentNotification.block_key.like(
                        f"{gun.isoformat()}:uyandimi:%"))
            .count())


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
    from api.zaman import simdi_utc
    now = now or simdi_utc()
    local_now = now + timedelta(minutes=tz_offset_min)       # Türkiye günü (B6)
    today_local = local_now.date()
    now_minute = local_now.hour * 60 + local_now.minute

    # Planı OLAN her bebek taranır (yalnız bugünün planı olanlar DEĞİL): kullanıcı
    # uygulamayı hiç açmamışsa bugünün planı henüz oluşmamış olabilir.
    baby_ids = [row[0] for row in
                db.query(SleepPlan.baby_id).distinct().all()]
    stats = {"checked_plans": 0, "sent": 0, "skipped_duplicate": 0, "adapted": 0}

    for baby_id in baby_ids:
        baby = db.get(Baby, baby_id)
        if baby is None:
            continue
        user = db.get(User, baby.user_id)
        if user is None or not _prefs(user).get("plan_reminders", True):
            continue

        # KRİTİK (Faz 6.6): bildirimden ÖNCE gün içi hesaplama — GET /plans/today
        # ile AYNI kod yolu (plan_service). Kullanıcı uygulamayı hiç açmasa da
        # bildirim güncel hesaplanmış saate göre gider.
        # v2/K8: "bugün zaten adapte edildi" kilidi kaldırıldı; hesap her turda
        # koşar ama içerik değişmediyse upsert_plan DB'ye yazmaz. Zamanlayıcıya
        # `now_minute` geçilir ki K6 ("zamanı geçen blok varsayılan sayılır")
        # turun gerçek saatine göre değerlendirilsin.
        onceki_cizelge = ((plan_service.plan_for_date(db, user, baby, today_local)
                           or SleepPlan()).content or {}).get("schedule")
        try:
            plan = plan_service.ensure_today_plan(db, user, baby, today=today_local,
                                                  now_minute=now_minute)
        except Exception:
            logger.exception("Bildirim öncesi plan hazırlanamadı (baby=%s)", baby.id)
            continue
        if plan is None:
            continue
        stats["checked_plans"] += 1
        if (plan.content or {}).get("schedule") != onceki_cizelge:
            stats["adapted"] += 1

        content = plan.content or {}

        # 5 AY GEÇİŞİ — plan arka planda üretilip HAZIR olduğunda bir kez haber
        # ver. `egitim_gecisi` işaretini plan_jobs üretim sırasında içeriğe
        # yazıyor; böylece baştan beri eğitim planı olan bebeklere bu bildirim
        # GİTMEZ. Dedupe defteri blok bildirimiyle aynı (aynı plan için bir kez).
        if content.get("egitim_gecisi") and content.get("type") == "egitim_plani":
            if _mark_sent(db, user.id, plan.id, "egitim_hazir"):
                _n = push_to_user(
                    db, user.id, "🎉 Uyku eğitimi programınız hazır",
                    f"{baby.name} 5 ayını doldurdu, uyku eğitimi programınız "
                    f"hazır.",
                    data={"type": "egitim_hazir", "plan_id": str(plan.id)})
                stats["sent"] += _n
                stats["egitim_hazir"] = stats.get("egitim_hazir", 0) + 1
                logger.info("Eğitim programı hazır bildirimi: user=%s baby=%s "
                            "cihaz=%d", user.id, baby.id, _n)

        # "BEBEĞİNİZ UYANDI MI?" — açık kayıt planlanan bitişini aşmışsa ve
        # mobil yerel bildirimi gösteremeyecekse (uygulama kapalı) sor.
        # Blok hatırlatmasından ÖNCE gelir: gecikmiş açık kayıt sıradaki
        # uykunun saatini de tahmine çevirdiği için daha aciltir.
        _uyandi_mi_sor(db, user, baby, plan, content, now_minute,
                       today_local, now, stats)

        if not _uyku_bildirimi_acik(user):
            continue
        adapt = content.get("adaptation")
        if not isinstance(adapt, dict):
            # Yenidoğan rehberi / eğitim geçişi: sabit saatli program yok.
            continue
        if sessiz_saat(now_minute):
            continue
        # SABAH KURALI — uyanış girilmeden çizelge tahmindir; o tahmine göre
        # "uyku zamanı" demek anneyi yanlış saate yönlendirir.
        if adapt.get("sabah_uyanis_kaynak") in (None, "varsayilan"):
            _sabah_sor(db, user, baby, plan, adapt, now_minute, today_local, stats)
            continue
        _uyku_dizisi(db, user, baby, plan, content, now_minute, today_local,
                     stats, tz_offset_min)
    return stats


def _uyku_bildirimi_acik(user: User) -> bool:
    """Uyku dizisi + sabah sorusu tercihi. Eski anahtar (plan_reminders) da
    kapalıysa gönderilmez — PATCH ikisini eşliyor."""
    p = _prefs(user)
    return bool(p.get(TERCIH_UYKU, True)) and bool(p.get("plan_reminders", True))


def belirtme_hali(ad: str) -> str:
    """Türkçe belirtme hâli: Emre → Emre'yi, Ali → Ali'yi, Can → Can'ı,
    Umut → Umut'u, Gül → Gül'ü. Son ünlüye göre (dört yönlü uyum)."""
    ad = (ad or "").strip()
    unluler = "aıoueiöüAIOUEİÖÜâîû"
    son = next((c for c in reversed(ad) if c in unluler), "e")
    ek = {"a": "ı", "ı": "ı", "A": "ı", "I": "ı", "â": "ı",
          "o": "u", "u": "u", "O": "u", "U": "u", "û": "u",
          "e": "i", "i": "i", "E": "i", "İ": "i", "î": "i",
          "ö": "ü", "ü": "ü", "Ö": "ü", "Ü": "ü"}.get(son, "i")
    kaynastirma = "y" if ad and ad[-1] in unluler else ""
    return f"{ad}'{kaynastirma}{ek}"


def gece_rutini_gonderilir(user: User) -> bool:
    """Gece "Rutin zamanı 🛁" yalnız build ≥ 26 kullanıcılara (users.app_version)."""
    from api.services.denetim import surum_build
    build = surum_build(getattr(user, "app_version", None))
    return build is not None and build >= GECE_RUTINI_MIN_BUILD


def _hhmm(deger: Any) -> int | None:
    try:
        sa, dk = str(deger).split(":")[:2]
        return int(sa) * 60 + int(dk)
    except Exception:
        return None


def _vakti_geldi(hedef_dk: int, now_minute: int, kayma: int) -> bool:
    """Adım zamanı [hedef − kayma, hedef − kayma + tolerans) içinde mi?

    `kayma` 0-4 dk ERKENE: aynı dakikaya düşen anneler turlara yayılır ve
    sapma ±5 dk'yı aşmaz."""
    bas = hedef_dk - kayma
    return bas <= now_minute < bas + DIZI_GEC_TOLERANS_DK


def _gunun_anahtar_sayisi(db: Session, user_id: Any, gun: date, tur: str) -> int:
    return (db.query(SentNotification)
            .filter(SentNotification.user_id == user_id,
                    SentNotification.block_key.like(f"{gun.isoformat()}:{tur}:%"))
            .count())


def _defterde_var(db: Session, user_id: Any, key: str) -> bool:
    """Plan kimliğinden BAĞIMSIZ: gün içinde plan yeniden üretilse de aynı
    adım ikinci kez gitmez."""
    return db.query(SentNotification.id).filter(
        SentNotification.user_id == user_id,
        SentNotification.block_key == key).first() is not None


def _bebek_kisa(baby: Baby) -> str:
    return str(baby.id).replace("-", "")[:8]


def _gonder(db: Session, user: User, baby: Baby, plan: SleepPlan, key: str,
            kategori: str, baslik: str, govde: str, stats: dict,
            ek: dict | None = None) -> bool:
    """Deftere yaz → gönder. Defter yazılamazsa (yarış) gönderilmez."""
    if _defterde_var(db, user.id, key) or not _mark_sent(db, user.id, plan.id, key):
        stats["skipped_duplicate"] += 1
        return False
    data = {"type": kategori, "kategori": kategori, "plan_id": str(plan.id),
            "baby_id": str(baby.id), **(ek or {})}
    n = push_to_user(db, user.id, baslik, govde, data=data)
    stats["sent"] += n
    stats[kategori] = stats.get(kategori, 0) + 1
    logger.info("Bildirim %s: user=%s baby=%s key=%s cihaz=%d",
                kategori, user.id, baby.id, key, n)
    return True


def sabah_hedef_dk(adapt: dict) -> int:
    """Yaşa göre hedef uyanış (adaptation.sabah_hedefi), yoksa şablonunki."""
    return (_hhmm((adapt.get("sabah_hedefi") or {}).get("hedef"))
            or _hhmm(adapt.get("sabah_uyanis_hedef"))
            or plan_adapter.DEFAULT_WAKE_MIN)


def _sabah_sor(db: Session, user: User, baby: Baby, plan: SleepPlan,
               adapt: dict, now_minute: int, today_local: date,
               stats: dict) -> None:
    """Sabah uyanışı girilmemiş gün: hedef + 30 dk ve + 90 dk (günde en çok 2)."""
    hedef = sabah_hedef_dk(adapt)
    kayma = yayma_dakikasi(user.id) % 5
    for sira, dk in ((1, hedef + SABAH_ILK_DK),
                     (2, hedef + SABAH_ILK_DK + SABAH_TEKRAR_DK)):
        if not _vakti_geldi(dk, now_minute, kayma):
            continue
        if _gunun_anahtar_sayisi(db, user.id, today_local, "sabah") >= SABAH_GUNLUK_LIMIT:
            stats["sabah_kota"] = stats.get("sabah_kota", 0) + 1
            return
        key = f"{today_local.isoformat()}:sabah:{_bebek_kisa(baby)}:{sira}"
        _gonder(db, user, baby, plan, key, KAT_SABAH,
                SABAH_BASLIK.format(ad=baby.name), SABAH_GOVDE, stats,
                ek={"sira": sira})
        return


def _uyku_basladi_mi(db: Session, baby: Baby, blok: dict, today_local: date,
                     tz_offset_min: int) -> bool:
    """Bu bloğun uykusu başladı mı? Motor bloğu kayda bağladıysa (kaynak
    'kayit' / devam) ya da bloktan en fazla 45 dk önce başlamış bir uyku
    kaydı varsa (açık ya da kapalı)."""
    if blok.get("kaynak") == "kayit" or blok.get("devam"):
        return True
    gun_bas = datetime(today_local.year, today_local.month, today_local.day,
                       tzinfo=timezone.utc) - timedelta(minutes=tz_offset_min)
    esik = gun_bas + timedelta(minutes=int(blok["start_minute"])
                               - UYKU_BASLADI_ERKEN_DK)
    return db.query(SleepLog.id).filter(
        SleepLog.baby_id == baby.id,
        SleepLog.type.in_(plan_adapter.UYKU_TIPLERI),
        SleepLog.started_at >= esik).first() is not None


def _uyku_dizisi(db: Session, user: User, baby: Baby, plan: SleepPlan,
                 content: dict, now_minute: int, today_local: date,
                 stats: dict, tz_offset_min: int) -> None:
    """Her uyku bloğu için 30 dk önce / zamanında / 30 dk sonra (uyumadıysa)."""
    kayma = yayma_dakikasi(user.id) % 5
    ad = baby.name
    for blok in plan_adapter.normalize_schedule(content.get("schedule") or []):
        if blok.get("type") not in ("nap", "sleep") or blok.get("start_minute") is None:
            continue
        bas = int(blok["start_minute"])
        sinif = "gece" if blok.get("type") == "sleep" else "gunduz"
        for adim, dk in (("once", bas - UYKU_ONCE_DK), ("zaman", bas),
                         ("sonra", bas + UYKU_SONRA_DK)):
            if not _vakti_geldi(dk, now_minute, kayma) or sessiz_saat(dk % 1440):
                continue
            if adim == "once" and sinif == "gece" and not gece_rutini_gonderilir(user):
                # Mobil kendi yerel rutin hatırlatmasını gösteriyor; deftere
                # yazılmaz, bloğun sonraki adımları normal devam eder.
                stats["gece_rutini_eski_surum"] = stats.get("gece_rutini_eski_surum", 0) + 1
                continue
            key = (f"{today_local.isoformat()}:uyku:{_bebek_kisa(baby)}:"
                   f"{blok.get('key')}:{adim}")
            if _defterde_var(db, user.id, key):
                stats["skipped_duplicate"] += 1
                continue
            if _uyku_basladi_mi(db, baby, blok, today_local, tz_offset_min):
                stats["uyku_basladi_iptal"] = stats.get("uyku_basladi_iptal", 0) + 1
                break                      # bloğun kalan adımları da düşer
            if _gunun_anahtar_sayisi(db, user.id, today_local, "uyku") >= UYKU_GUNLUK_LIMIT:
                stats["uyku_kota"] = stats.get("uyku_kota", 0) + 1
                return
            baslik, govde = DIZI_METIN.get((adim, sinif)) or DIZI_METIN[(adim, None)]
            _gonder(db, user, baby, plan, key, DIZI_KATEGORI[adim],
                    baslik.format(ad=ad), govde.format(ad=ad, ad_i=belirtme_hali(ad)),
                    stats,
                    ek={"block_key": blok.get("key"), "adim": adim,
                        "sinif": sinif, "saat": blok.get("time")
                        or plan_adapter._fmt(bas)})


def _uyandi_mi_sor(db: Session, user: User, baby: Baby, plan: SleepPlan,
                   content: dict, now_minute: int, today_local: date,
                   now: datetime, stats: dict) -> None:
    """Gecikmiş açık uyku kaydı için tek bir hatırlatma gönder.

    Sıra ucuzdan pahalıya: sessiz saat → gecikmiş blok var mı → günlük kota →
    uygulama açık mı (DB sorgusu) → defter → gönderim."""
    if sessiz_saat(now_minute):
        return
    geciken = geciken_acik_bloklar(content.get("schedule") or [], now_minute)
    if not geciken:
        return
    if _uyandi_mi_gunluk_sayi(db, user.id, today_local) >= UYANDI_MI_GUNLUK_LIMIT:
        stats["uyandimi_kota"] = stats.get("uyandimi_kota", 0) + 1
        return
    if _uygulama_acik_mi(db, user.id, now):
        stats["uyandimi_uygulama_acik"] = stats.get("uyandimi_uygulama_acik", 0) + 1
        return

    # En ESKİ gecikmiş blok sorulur: zinciri asıl o kilitliyor.
    blok = min(geciken, key=lambda b: b.get("end_minute") or 0)
    key = f"{today_local.isoformat()}:uyandimi:{blok.get('key')}"
    if _already_sent(db, user.id, plan.id, key) or not _mark_sent(
            db, user.id, plan.id, key):
        stats["skipped_duplicate"] += 1
        return

    sent = push_to_user(db, user.id, UYANDI_MI_BASLIK, UYANDI_MI_GOVDE,
                        data={"type": "uyandi_mi", "plan_id": str(plan.id),
                              "baby_id": str(baby.id),
                              "block_key": blok.get("key")})
    stats["sent"] += sent
    stats["uyandimi"] = stats.get("uyandimi", 0) + 1
    logger.info("Uyandı mı? hatırlatması: user=%s baby=%s blok=%s cihaz=%d",
                user.id, baby.id, blok.get("key"), sent)


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
    # Topluluk v2: gece özeti + faydalı toplu bildirimi. Ayrı try: biri
    # patlarsa diğeri yine koşar.
    try:
        from api.services import topluluk_bildirim
        t_stats = topluluk_bildirim.tur_calistir(db)
        if any(t_stats.values()):
            logger.info("Topluluk bildirim turu: %s", t_stats)
    except Exception:
        logger.exception("Topluluk bildirim turu başarısız")
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

    # ÇOKLU WORKER (v2.4.1) — uvicorn birden fazla süreçle koşuyor ve her
    # süreç kendi APScheduler'ını başlatırdı: aynı tur N kez koşar, her bebek
    # için adaptasyon N kez hesaplanırdı. Bildirimin KENDİSİ zaten
    # `SentNotification` tekil kısıtıyla korunuyor (çift push gitmez), ama
    # boşa hesap gider. Postgres ADVISORY LOCK ile yalnız bir süreç lideri
    # olur; lider ölürse bağlantı kapanır ve kilit kendiliğinden serbest kalır.
    if not _zamanlayici_kilidi_al():
        logger.info("Zamanlayıcı BAŞLATILMADI — başka bir worker lider")
        return False

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(_job, "interval", minutes=SCHEDULER_INTERVAL_MIN,
                       id="plan_reminders", max_instances=1, coalesce=True)
    _scheduler.start()
    logger.info("Bildirim zamanlayıcısı başladı (her %d dk)", SCHEDULER_INTERVAL_MIN)
    return True


# Kilit BAĞLANTI ÖMRÜ boyunca tutulur; bu yüzden bağlantı global tutulur
# (garbage collect edilirse kilit düşer ve iki lider oluşur).
_kilit_baglantisi = None
ZAMANLAYICI_KILIT_ID = 776699001          # projeye özel sabit


def _zamanlayici_kilidi_al() -> bool:
    """Postgres advisory lock — yalnız bir worker True alır.

    SQLite'ta (lokal/test) kilit KAVRAMI YOK: tek süreç koştuğu için doğrudan
    True döner. Kilit alınamazsa zamanlayıcı başlatılmaz."""
    global _kilit_baglantisi
    from sqlalchemy import text
    from api.db.session import engine
    if engine.dialect.name != "postgresql":
        return True
    try:
        conn = engine.connect()
        alindi = conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"),
            {"k": ZAMANLAYICI_KILIT_ID}).scalar()
        if alindi:
            _kilit_baglantisi = conn          # AÇIK kalmalı
            return True
        conn.close()
        return False
    except Exception:
        # Kilit alınamıyorsa (ör. bağlantı sorunu) zamanlayıcıyı BAŞLATMA:
        # iki lider, hiç lider olmamasından kötüdür (çift hesap + çift push
        # riski). Bir sonraki deploy/restart yeniden dener.
        logger.exception("Zamanlayıcı kilidi alınamadı")
        return False


def shutdown_scheduler() -> None:
    global _scheduler, _kilit_baglantisi
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
    if _kilit_baglantisi is not None:
        try:
            _kilit_baglantisi.close()         # advisory lock serbest kalır
        except Exception:
            pass
        _kilit_baglantisi = None
        _scheduler = None
