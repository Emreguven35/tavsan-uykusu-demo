"""
Bebek bağlam derleyici — /chat kişiselleştirmesi (Faz 6.5).

baby_id verilen sohbet isteklerinde, Claude'a RAG chunk'larından AYRI bir
"BEBEK VERİSİ" bloğu geçilir: bebek profili + son 3 günün kompakt log özeti +
bugünün plan çizelgesi. Böylece cevap "Elif için dün 19:05'te yatmıştınız" gibi
somut konuşabilir.

TASARIM:
- HAFİF: üç sorgu (bebek, son 3 günün logları, bugünün planı). Sohbet gecikmesine
  eklenen yük ihmal edilebilir olmalı.
- Motor (engine/chatbot.py) DB bilmez; bu modül ORM'den okuyup DÜZ METİN üretir.
- Veri yoksa None döner → çağıran mevcut (genel metodoloji) davranışını sürdürür.
- Zaman dilimi: loglar UTC, özet YEREL duvar saati (plan_adapter.TZ_OFFSET_MIN).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from api.models import Baby, SleepLog, SleepPlan
from api.services import plan_adapter

logger = logging.getLogger("tavsan.baby_context")

LOOKBACK_DAYS = 3
GUN_ETIKET = {0: "bugün", 1: "dün", 2: "önceki gün"}


def _yerel(dt: datetime, tz: int) -> tuple[date, int]:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(timezone.utc) + timedelta(minutes=tz)
    return local.date(), local.hour * 60 + local.minute


def _ss(dakika: int) -> str:
    dakika = int(dakika) % (24 * 60)
    return f"{dakika // 60:02d}:{dakika % 60:02d}"


def _ay_hesapla(birth: date | None, bugun: date) -> int | None:
    if birth is None:
        return None
    return max(0, (bugun.year - birth.year) * 12 + (bugun.month - birth.month)
               - (1 if bugun.day < birth.day else 0))


def _planlanan_yatis(plan: SleepPlan | None) -> int | None:
    """Bugünün planındaki gece yatış dakikası (karşılaştırma için)."""
    if plan is None:
        return None
    for b in (plan.content or {}).get("schedule") or []:
        if b.get("key") == "bedtime":
            return b.get("start_minute")
    return None


def _gun_ozeti(gun_etiket: str, kayitlar: list[SleepLog], tz: int,
               planlanan_yatis: int | None) -> str | None:
    """Bir günün kayıtlarını tek cümlelik özete indir."""
    yatis: int | None = None
    uyanmalar: list[tuple[int, int | None]] = []      # (saat, süre_dk)
    sekerlemeler: list[tuple[int, int | None]] = []   # (başlangıç, bitiş)

    for lg in kayitlar:
        _, dk = _yerel(lg.started_at, tz)
        bitis_dk = _yerel(lg.ended_at, tz)[1] if lg.ended_at is not None else None
        sure = None
        if lg.ended_at is not None:
            sure = max(0, int((lg.ended_at - lg.started_at).total_seconds() // 60))

        if lg.type == "sleep" and dk >= 16 * 60:          # akşam yatışı
            yatis = dk if yatis is None else max(yatis, dk)
        elif lg.type == "night_wake":
            uyanmalar.append((dk, sure))
        elif lg.type == "nap":
            sekerlemeler.append((dk, bitis_dk))

    parcalar: list[str] = []
    if yatis is not None:
        s = f"gece yatış {_ss(yatis)}"
        if planlanan_yatis is not None:
            fark = yatis - planlanan_yatis
            if abs(fark) >= 10:
                yon = "geç" if fark > 0 else "erken"
                s += f" (planlanan {_ss(planlanan_yatis)}'den {abs(fark)}dk {yon})"
        parcalar.append(s)

    if uyanmalar:
        detay = ", ".join(
            _ss(saat) + (f", {sure}dk" if sure else "") for saat, sure in sorted(uyanmalar))
        parcalar.append(f"gece uyanma {len(uyanmalar)} kez ({detay})")

    if sekerlemeler:
        detay = ", ".join(
            _ss(bas) + (f"-{_ss(bit)}" if bit is not None else "")
            for bas, bit in sorted(sekerlemeler))
        parcalar.append(f"şekerleme {len(sekerlemeler)} ({detay})")

    if not parcalar:
        return None
    return f"{gun_etiket} " + ", ".join(parcalar)


def _plan_ozeti(plan: SleepPlan | None) -> str | None:
    """Bugünün plan çizelgesini kısa metne indir."""
    if plan is None:
        return None
    sched = (plan.content or {}).get("schedule") or []
    if not sched:
        return None
    parcalar = []
    for b in sched:
        t = b.get("type")
        if t == "wake":
            parcalar.append(f"{b.get('time')} uyanış")
        elif t == "nap":
            parcalar.append(f"{b.get('time')}-{b.get('end')} uyku")
        elif t == "sleep":
            parcalar.append(f"{b.get('time')} yatış")
    return ", ".join(parcalar) if parcalar else None


def build_baby_context(db: Session, baby: Baby, today: date | None = None,
                       tz: int = plan_adapter.TZ_OFFSET_MIN) -> str | None:
    """Bebek profili + son 3 gün log özeti + bugünün planı → düz metin blok.

    Dönen None ise çağıran BAĞLAM EKLEMEZ (mevcut genel metodoloji davranışı):
    ne log ne de bugünün planı varsa kişiselleştirecek veri yok demektir.
    """
    today = today or (datetime.now(timezone.utc) + timedelta(minutes=tz)).date()

    # Pencere bir gün geniş: yerel gün sınırı UTC'de kayar, gece kayıtları kaçmasın.
    baslangic = datetime.combine(today - timedelta(days=LOOKBACK_DAYS),
                                 datetime.min.time(), tzinfo=timezone.utc)
    kayitlar = (db.query(SleepLog)
                .filter(SleepLog.baby_id == baby.id,
                        SleepLog.started_at >= baslangic)
                .order_by(SleepLog.started_at)
                .all())
    plan = (db.query(SleepPlan)
            .filter(SleepPlan.baby_id == baby.id, SleepPlan.plan_date == today)
            .order_by(SleepPlan.created_at.desc())
            .first())

    if not kayitlar and plan is None:
        return None                       # kişiselleştirecek veri yok

    # --- Profil satırı ---
    ay = _ay_hesapla(baby.birth_date, today)
    profil = baby.name + (f", {ay} aylık" if ay is not None else "")
    ekler = []
    if baby.night_wakes is not None:
        ekler.append(f"kayıtlı başlangıç gece uyanma: {baby.night_wakes}")
    if baby.training_started_at is not None:
        ekler.append(f"eğitim başlangıcı {baby.training_started_at.isoformat()}")
    if baby.training_completed_at is not None:
        ekler.append(f"eğitim tamamlanma {baby.training_completed_at.isoformat()}")
    if ekler:
        profil += " (" + "; ".join(ekler) + ")"

    # --- Gün gün log özeti ---
    # Gece uyanmaları 12:00'den önceyse BİR ÖNCEKİ günün gecesine sayılır.
    gunluk: dict[date, list[SleepLog]] = {}
    for lg in kayitlar:
        g, dk = _yerel(lg.started_at, tz)
        if lg.type == "night_wake" and dk < 12 * 60:
            g = g - timedelta(days=1)
        gunluk.setdefault(g, []).append(lg)

    planlanan = _planlanan_yatis(plan)
    gun_ozetleri = []
    for fark in range(LOOKBACK_DAYS):
        g = today - timedelta(days=fark)
        ozet = _gun_ozeti(GUN_ETIKET.get(fark, g.isoformat()), gunluk.get(g, []),
                          tz, planlanan)
        if ozet:
            gun_ozetleri.append(ozet)

    satirlar = [profil + "."]
    if gun_ozetleri:
        satirlar.append(f"Son {LOOKBACK_DAYS} gün: " + "; ".join(gun_ozetleri) + ".")
    else:
        satirlar.append(f"Son {LOOKBACK_DAYS} günde uyku kaydı girilmemiş.")

    plan_ozeti = _plan_ozeti(plan)
    if plan_ozeti:
        satirlar.append(f"Bugünün planı: {plan_ozeti}.")

    return " ".join(satirlar)
