"""
Maliyet takibi — dış servis çağrılarının GERÇEK kullanımını api_usage'a yazar.

Üç kural bu modülün tasarımını belirledi:

1. TAHMİN ETME. Anthropic yanıtındaki `usage` bloğu (input_tokens, output_tokens,
   cache_read_input_tokens, cache_creation_input_tokens) olduğu gibi alınır.
   Karakter sayısından token tahmini prompt caching'i hiç göremez: cache'ten
   okunan token normal fiyatın %10'u. Tahminle "cache bize ne kazandırdı"
   sorusunu cevaplamak mümkün değil.

2. ANA İSTEĞİ YAVAŞLATMA. Yazma tek bir arka plan iş parçacığında yapılır; çağıran
   kuyruğa bırakıp döner. DB yavaşsa/erişilemezse anne cevabını yine de alır.

3. SESSİZ YUT. Maliyet kaydı bir yan defterdir; başarısızlığı kullanıcıya
   yansımaz. Hata yalnız loglanır (warning), istisna YUKARI ÇIKMAZ.

KVKK: bu modüle içerik GEÇMEZ. İmzalarda metin parametresi yok — yalnız sayaçlar.
`characters` bile metnin kendisi değil, uzunluğudur.
"""
from __future__ import annotations

import logging
import queue
import threading
import uuid
from datetime import date, datetime, timezone
from typing import Any

from api.config import (
    ANTHROPIC_BILINMEYEN_FIYAT, ANTHROPIC_FIYATLARI, CACHE_OKUMA_CARPANI,
    CACHE_YAZMA_CARPANI, ELEVENLABS_BILINMEYEN_FIYAT, ELEVENLABS_FIYATLARI,
    GUNLUK_MALIYET_ESIGI_USD, VOICE_CLONE_USD,
)

logger = logging.getLogger("tavsan.usage")

SERVIS_ANTHROPIC = "anthropic"
SERVIS_ELEVENLABS = "elevenlabs"

# operation değerleri (api_usage.operation, String(32))
OP_CHAT = "chat"
OP_PLAN_GENERATE = "plan_generate"
OP_PLAN_ADAPT = "plan_adapt"
OP_MODERATION = "moderation"
OP_TTS = "tts"
OP_VOICE_CLONE = "voice_clone"

GECERLI_OPERASYONLAR = {
    OP_CHAT, OP_PLAN_GENERATE, OP_PLAN_ADAPT, OP_MODERATION, OP_TTS, OP_VOICE_CLONE,
}


# ---------------------------------------------------------------------------
# Anthropic usage bloğunu sözlüğe indir
# ---------------------------------------------------------------------------
def anthropic_usage(resp: Any) -> dict:
    """SDK yanıtındaki usage bloğunu düz sözlüğe çevir.

    Alan adları SDK sürümüne göre değişebildiği ve bazıları None gelebildiği için
    getattr + None-güvenli okunur. Yanıt/usage yoksa sıfırlarla döner — çağıran
    tarafta `if resp.usage` gibi kontrollere gerek kalmasın.
    """
    u = getattr(resp, "usage", None)
    if u is None:
        return {"input_tokens": 0, "output_tokens": 0,
                "cached_tokens": 0, "cache_write_tokens": 0}

    def _int(ad: str) -> int:
        try:
            return int(getattr(u, ad, 0) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "input_tokens": _int("input_tokens"),
        "output_tokens": _int("output_tokens"),
        "cached_tokens": _int("cache_read_input_tokens"),
        "cache_write_tokens": _int("cache_creation_input_tokens"),
    }


# ---------------------------------------------------------------------------
# Fiyatlandırma
# ---------------------------------------------------------------------------
def anthropic_maliyet(model: str | None, input_tokens: int, output_tokens: int,
                      cached_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Token sayaçlarından USD maliyet. Cache okuma indirimli, yazma zamlı."""
    fiyat = ANTHROPIC_FIYATLARI.get(model or "")
    if fiyat is None:
        logger.warning("Bilinmeyen model %r — maliyet ÜST SINIRDAN hesaplanıyor "
                       "(fiyat tablosuna eklenmeli).", model)
        fiyat = ANTHROPIC_BILINMEYEN_FIYAT
    giris = fiyat["in"] / 1_000_000
    cikis = fiyat["out"] / 1_000_000
    return (input_tokens * giris
            + output_tokens * cikis
            + cached_tokens * giris * CACHE_OKUMA_CARPANI
            + cache_write_tokens * giris * CACHE_YAZMA_CARPANI)


def elevenlabs_maliyet(model: str | None, characters: int) -> float:
    """Karakter sayısından USD maliyet (ses klonlama hariç — o işlem başına)."""
    birim = ELEVENLABS_FIYATLARI.get(model or "")
    if birim is None:
        logger.warning("Bilinmeyen TTS modeli %r — maliyet ÜST SINIRDAN.", model)
        birim = ELEVENLABS_BILINMEYEN_FIYAT
    return characters * birim


def maliyet_hesapla(service: str, model: str | None, *, input_tokens: int = 0,
                    output_tokens: int = 0, cached_tokens: int = 0,
                    cache_write_tokens: int = 0, characters: int = 0,
                    operation: str | None = None) -> float:
    if service == SERVIS_ANTHROPIC:
        return anthropic_maliyet(model, input_tokens, output_tokens,
                                 cached_tokens, cache_write_tokens)
    if service == SERVIS_ELEVENLABS:
        if operation == OP_VOICE_CLONE:
            return VOICE_CLONE_USD
        return elevenlabs_maliyet(model, characters)
    logger.warning("Bilinmeyen servis %r — maliyet 0 yazıldı.", service)
    return 0.0


def cache_kazanci(model: str | None, cached_tokens: int) -> float:
    """Cache'ten okunan token'lar tam fiyat ödenseydi ARADAKİ FARK ne olurdu."""
    fiyat = ANTHROPIC_FIYATLARI.get(model or "") or ANTHROPIC_BILINMEYEN_FIYAT
    giris = fiyat["in"] / 1_000_000
    return cached_tokens * giris * (1.0 - CACHE_OKUMA_CARPANI)


# ---------------------------------------------------------------------------
# Günlük eşik alarmı
# ---------------------------------------------------------------------------
# Sayaç süreç içidir; yeniden başlatmada sıfırlanmasın diye o günün toplamı ilk
# yazımda DB'den okunur. Birden fazla konteyner çalışırsa her biri kendi
# toplamını görür (bugün tek konteyner). Kesin rakam her zaman /admin/usage'da.
_gun_durumu: dict[str, Any] = {"gun": None, "toplam": 0.0, "uyarildi": False}


def _esik_kontrol(db, tutar: float) -> None:
    bugun = datetime.now(timezone.utc).date()
    if _gun_durumu["gun"] != bugun:
        _gun_durumu.update(gun=bugun, toplam=_gunun_toplami(db, bugun), uyarildi=False)
    _gun_durumu["toplam"] += tutar
    if (not _gun_durumu["uyarildi"]
            and _gun_durumu["toplam"] >= GUNLUK_MALIYET_ESIGI_USD):
        _gun_durumu["uyarildi"] = True
        logger.critical(
            "MALİYET EŞİĞİ AŞILDI: %s günü toplam $%.2f (eşik $%.2f). "
            "Anthropic/ElevenLabs kullanımını kontrol edin.",
            bugun.isoformat(), _gun_durumu["toplam"], GUNLUK_MALIYET_ESIGI_USD)


def _gunun_toplami(db, gun: date) -> float:
    """O günün DB'deki toplamı (yeniden başlatma sonrası sayacı doğru başlatır)."""
    from sqlalchemy import func as sa_func

    from api.models import ApiUsage
    bas = datetime.combine(gun, datetime.min.time(), tzinfo=timezone.utc)
    son = datetime.combine(gun, datetime.max.time(), tzinfo=timezone.utc)
    toplam = (db.query(sa_func.coalesce(sa_func.sum(ApiUsage.estimated_cost_usd), 0.0))
              .filter(ApiUsage.created_at >= bas, ApiUsage.created_at <= son).scalar())
    return float(toplam or 0.0)


def gun_durumu_sifirla() -> None:
    """Test yardımcısı — günlük sayaç durumunu temizle."""
    _gun_durumu.update(gun=None, toplam=0.0, uyarildi=False)


# ---------------------------------------------------------------------------
# Asenkron yazıcı
# ---------------------------------------------------------------------------
_KUYRUK: "queue.Queue[dict | None]" = queue.Queue(maxsize=1000)
_ISCI: threading.Thread | None = None
_ISCI_KILIT = threading.Lock()
# Testlerde iş parçacığı beklemek yerine senkron yazmak için (bkz. tests/test_usage.py).
SENKRON_MOD = False


def _isciyi_baslat() -> None:
    global _ISCI
    with _ISCI_KILIT:
        if _ISCI is not None and _ISCI.is_alive():
            return
        _ISCI = threading.Thread(target=_dongu, name="usage-writer", daemon=True)
        _ISCI.start()


def _dongu() -> None:
    while True:
        kayit = _KUYRUK.get()
        if kayit is None:                       # kapatma sinyali
            _KUYRUK.task_done()
            return
        try:
            _yaz(kayit)
        except Exception as e:                  # maliyet kaydı ana akışı bozamaz
            logger.warning("Kullanım kaydı yazılamadı (yutuldu): %s", e)
        finally:
            _KUYRUK.task_done()


def _yaz(kayit: dict) -> None:
    """Tek kaydı KENDİ DB oturumunda yaz (istek oturumu çoktan kapandı)."""
    from api.db import SessionLocal
    from api.models import ApiUsage

    db = SessionLocal()
    try:
        db.add(ApiUsage(**kayit))
        db.commit()
        _esik_kontrol(db, float(kayit.get("estimated_cost_usd") or 0.0))
    finally:
        db.close()


def kaydet(service: str, operation: str, *, model: str | None = None,
           usage: dict | None = None, characters: int = 0,
           user_id: uuid.UUID | str | None = None,
           duration_ms: int | None = None) -> None:
    """Bir dış servis çağrısını kaydet. ASLA istisna fırlatmaz, ASLA beklemez.

    usage: anthropic_usage(resp) çıktısı (Anthropic çağrıları için).
    characters: TTS'e giden metnin UZUNLUĞU (metnin kendisi değil).
    """
    try:
        u = usage or {}
        it = int(u.get("input_tokens", 0) or 0)
        ot = int(u.get("output_tokens", 0) or 0)
        ct = int(u.get("cached_tokens", 0) or 0)
        wt = int(u.get("cache_write_tokens", 0) or 0)
        if operation not in GECERLI_OPERASYONLAR:
            logger.warning("Bilinmeyen operation %r — yine de kaydediliyor.", operation)

        kayit = {
            "service": service,
            "operation": operation,
            "model": model,
            "input_tokens": it,
            "output_tokens": ot,
            "cached_tokens": ct,
            "cache_write_tokens": wt,
            "characters": int(characters or 0),
            "estimated_cost_usd": round(maliyet_hesapla(
                service, model, input_tokens=it, output_tokens=ot, cached_tokens=ct,
                cache_write_tokens=wt, characters=characters, operation=operation), 6),
            "duration_ms": int(duration_ms) if duration_ms is not None else None,
            "user_id": uuid.UUID(str(user_id)) if user_id else None,
        }

        if SENKRON_MOD:
            _yaz(kayit)
            return
        _isciyi_baslat()
        _KUYRUK.put_nowait(kayit)
    except queue.Full:
        # Kuyruk dolduysa kaydı DÜŞÜR — ana isteği bekletmektense veri kaybı.
        logger.warning("Kullanım kuyruğu dolu, kayıt düşürüldü (service=%s op=%s)",
                       service, operation)
    except Exception as e:
        logger.warning("Kullanım kaydı hazırlanamadı (yutuldu): %s", e)


def bekle(timeout: float = 5.0) -> None:
    """Test/kapatma yardımcısı — kuyruktaki kayıtlar yazılana kadar bekle."""
    try:
        _KUYRUK.join()
    except Exception:
        pass
