"""
Uyku sesleri servisi — katalog ve CSV → DB upsert.

İŞ MANTIĞI BURADA: router yalnız HTTP kabuğudur; `scripts/ses_yukle.py` de
kataloğu bu dosyadaki `katalog_upsert` ile günceller (education.py ile aynı
ayrım — upsert kuralı iki yerde yazılırsa betik ile API ayrışır).

PREMIUM: `is_free` olmayan sesler premium'dur. Karar `deps.premium_karari`dan
gelir (BETA_MODE'da herkes premium). Dosyanın kendisi public'tir — kilit
KATALOGDA (`locked`), mobil kilitli kartı çalmaz.
"""
from __future__ import annotations

import logging
import uuid
from typing import Iterable

from sqlalchemy.orm import Session

from api.models import SleepSound
from api.models.sleep_sound import SES_KATEGORILERI
from api.services import storage

logger = logging.getLogger("tavsan.sounds")

def _ses_sozlugu(s: SleepSound, premium: bool) -> dict:
    return {
        "id": str(s.id),
        "slug": s.slug,
        "title": s.title,
        "category": s.category,
        "duration_sec": int(s.duration_sec or 0),
        "audio_url": s.audio_url,
        "bytes": int(s.bytes or 0),
        "loop": True,
        "is_free": bool(s.is_free),
        "locked": not (s.is_free or premium),
    }


def katalog(db: Session, premium: bool) -> dict:
    """GET /sounds gövdesi — kategoriler SES_KATEGORILERI sırasıyla."""
    sesler = (db.query(SleepSound)
              .order_by(SleepSound.category, SleepSound.order_in_category,
                        SleepSound.title).all())
    kategoriler = []
    for kod, baslik in SES_KATEGORILERI:
        icerik = [_ses_sozlugu(s, premium) for s in sesler if s.category == kod]
        if icerik:
            kategoriler.append({"key": kod, "title": baslik, "sounds": icerik})
    # Sözlükte olmayan kategori (yeni eklenmiş) kaybolmasın.
    bilinen = {k for k, _ in SES_KATEGORILERI}
    for kod in dict.fromkeys(s.category for s in sesler
                             if s.category not in bilinen):
        kategoriler.append({"key": kod, "title": kod,
                            "sounds": [_ses_sozlugu(s, premium) for s in sesler
                                       if s.category == kod]})
    return {"categories": kategoriler, "is_premium": premium}


def katalog_upsert(db: Session, satirlar: Iterable[dict],
                   olculer: dict[str, dict] | None = None) -> dict:
    """Slug bazlı upsert — satırlar scripts/ses_uret.manifest() biçiminde.
    `olculer`: slug → {"duration_sec", "bytes"}.
    Ölçüsü verilmeyen slug'ın süre/boyutu DEĞİŞTİRİLMEZ."""
    olculer = olculer or {}
    eklenen, guncellenen = [], []
    for s in satirlar:
        v = (db.query(SleepSound)
             .filter(SleepSound.slug == s["slug"]).one_or_none())
        yeni = v is None
        if yeni:
            v = SleepSound(id=uuid.uuid4(), slug=s["slug"], duration_sec=0,
                           bytes=0)
            db.add(v)
        onceki = None if yeni else (v.title, v.category, v.order_in_category,
                                    v.is_free, v.duration_sec, v.bytes)
        v.title = s["title"]
        v.category = s["category"]
        v.order_in_category = s["order_in_category"]
        v.is_free = bool(s["is_free"])
        v.audio_url = storage.uyku_sesi_url(s["slug"])
        o = olculer.get(s["slug"]) or {}
        if "duration_sec" in o:
            v.duration_sec = int(o["duration_sec"])
        if "bytes" in o:
            v.bytes = int(o["bytes"])
        if yeni:
            eklenen.append(s["slug"])
        elif onceki != (v.title, v.category, v.order_in_category, v.is_free,
                        v.duration_sec, v.bytes):
            guncellenen.append(s["slug"])
    db.commit()
    logger.info("Ses kataloğu upsert: %d eklendi, %d güncellendi",
                len(eklenen), len(guncellenen))
    return {"eklenen": eklenen, "guncellenen": guncellenen}
