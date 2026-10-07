"""
Saklama ve silme kuralları — SİSTEMİN İÇ KAYITLARI için (2026-10-07).

Annelerin kendi verisi (uyku kayıtları, planlar, program, sohbet, ölçümler,
Topluluk) bu kurallara GİRMEZ; hesap açık kaldığı sürece durur.

Hesap silmede (hesap_silme_oncesi, `db.delete(user)`'dan ÖNCE çağrılır):
  • consents → onay_kanitlari'na taşınır (kimliksiz, e-posta özetiyle; 10 yıl),
  • plan_uretim_isleri: kullanıcının bütün işleri silinir,
  • revenuecat_olaylari: kullanıcının satırları silinir.

Her gece (gece_temizligi, 03:30 UTC, çok worker'da yalnız biri):
  • plan_uretim_isleri: sahibi (hesap ya da bebek) silinmiş işler; 30 günden eski
    tamamlanmış/başarısız işler — HER BEBEĞİN EN SON BAŞARILI İŞİ HARİÇ,
  • /data/denetim ve /data/arsiv: 30 günden eski dosyalar,
  • onay_kanitlari: hesap silineli 10 yılı geçenler,
  • Anne Sesi klasörü: sahibi silinmiş kullanıcı klasörleri (hesap silmede dosya
    silme başarısız olduysa burada yeniden denenir).

Bağımlılık denetimi (silmeden önce, 2026-10-07): plan iş tablosunu okuyan akışlar
— iş yoklama (GET /plans/generate/{id}, dakikalar), idempotency (son 15 dk'nın
"processing" işi), bakim (yetim "processing" işler), GET /plans/today 404
açıklaması (bebeğin en son işi) — 30 günlük, bitmiş işlere dayanmıyor. Plan
gösterimi, yeniden üretim ve yaş bandı geçişi bu tabloyu OKUMUYOR. Denetim ve
arşiv dosyalarını hiçbir akış okumuyor (denetim bağlantısı 7 gün geçerli).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("tavsan.saklama")

IS_SAKLAMA_GUN = 30
DOSYA_SAKLAMA_GUN = 30
KILIT_ID = 7_351_204          # pg advisory lock (denetim'inkinden ayrı)


# ---------------------------------------------------------------------------
# Hesap silme
# ---------------------------------------------------------------------------
def hesap_silme_oncesi(db, user) -> dict:
    """Onayları ispat tablosuna taşı, kullanıcının iç kayıtlarını sil. Commit
    ETMEZ — hesap silmeyle aynı işlemde yazılır (biri olmazsa hiçbiri olmasın)."""
    from api.models import Consent, OnayKaniti, PlanUretimIsi, RevenueCatOlayi
    from api.services.kvkk import eposta_ozeti

    simdi = datetime.now(timezone.utc)
    ozet = eposta_ozeti(user.email)
    onaylar = db.query(Consent).filter(Consent.user_id == user.id).all()
    for c in onaylar:
        db.add(OnayKaniti(tur=c.tur, metin_surumu=c.metin_surumu, onay=c.onay,
                          zaman=c.zaman, kaynak=c.kaynak, eposta_ozeti=ozet,
                          hesap_silindi_at=simdi))
    isler = (db.query(PlanUretimIsi)
             .filter(PlanUretimIsi.user_id == str(user.id))
             .delete(synchronize_session=False))
    rc = (db.query(RevenueCatOlayi)
          .filter((RevenueCatOlayi.user_id == user.id)
                  | (RevenueCatOlayi.app_user_id == str(user.id)))
          .delete(synchronize_session=False))
    sonuc = {"onay_tasindi": len(onaylar), "is_silindi": isler, "rc_silindi": rc}
    logger.info("Hesap silme iç kayıtları: user=%s %s", user.id, sonuc)
    return sonuc


# ---------------------------------------------------------------------------
# Gece temizliği
# ---------------------------------------------------------------------------
def _utc(t: datetime | None) -> datetime | None:
    return None if t is None else (t if t.tzinfo else t.replace(tzinfo=timezone.utc))


def plan_islerini_temizle(db, simdi: datetime | None = None) -> dict:
    from api.models import Baby, PlanUretimIsi, User
    simdi = simdi or datetime.now(timezone.utc)
    esik = simdi - timedelta(days=IS_SAKLAMA_GUN)
    kullanicilar = {str(u) for (u,) in db.query(User.id).all()}
    bebekler = {str(b) for (b,) in db.query(Baby.id).all()}
    isler = db.query(PlanUretimIsi).all()
    son_basarili: dict[str, tuple] = {}
    for i in isler:
        # Koruma yalnız SAHİBİ OLAN işler arasında: sahipsiz bir iş "en son
        # başarılı" sayılıp bebeğin gerçek son işini korumasız bırakmasın.
        if (i.status == "done" and i.user_id in kullanicilar
                and i.baby_id in bebekler):
            anahtar = (_utc(i.created_at) or simdi, i.id)
            if i.baby_id not in son_basarili or anahtar > son_basarili[i.baby_id]:
                son_basarili[i.baby_id] = anahtar
    korunan = {v[1] for v in son_basarili.values()}
    sahipsiz = eski = 0
    for i in isler:
        if i.user_id not in kullanicilar or i.baby_id not in bebekler:
            db.delete(i)
            sahipsiz += 1
        elif (i.status in ("done", "failed") and i.id not in korunan
              and (_utc(i.created_at) or simdi) < esik):
            db.delete(i)
            eski += 1
    return {"is_sahipsiz": sahipsiz, "is_eski": eski}


def eski_dosyalari_sil(klasorler: list[Path], gun: int = DOSYA_SAKLAMA_GUN,
                       simdi_ts: float | None = None) -> int:
    """Klasörlerdeki (alt klasörler dahil) `gun`den eski DOSYALARI sil."""
    simdi_ts = simdi_ts if simdi_ts is not None else time.time()
    sinir = simdi_ts - gun * 86400
    silinen = 0
    for k in klasorler:
        if not k.is_dir():
            continue
        for f in k.rglob("*"):
            try:
                if f.is_file() and f.stat().st_mtime < sinir:
                    f.unlink()
                    silinen += 1
            except OSError:
                logger.exception("Eski dosya silinemedi: %s", f)
    return silinen


def denetim_arsiv_klasorleri() -> list[Path]:
    from api.services.denetim import denetim_koku
    kok = denetim_koku()
    return [kok, kok.parent / "arsiv"]


def onay_kanitlarini_temizle(db, simdi: datetime | None = None) -> int:
    from api.models import OnayKaniti
    from api.models.onay_kaniti import SAKLAMA_YIL
    simdi = simdi or datetime.now(timezone.utc)
    sinir = simdi - timedelta(days=365 * SAKLAMA_YIL + SAKLAMA_YIL // 4)
    return (db.query(OnayKaniti).filter(OnayKaniti.hesap_silindi_at < sinir)
            .delete(synchronize_session=False))


def sahipsiz_ses_klasorleri(db) -> int:
    """voice-audio/{user_id} klasörlerinden sahibi silinmiş olanları sil."""
    from api.models import User
    from api.services import storage
    depo = storage.depo()
    kok = getattr(depo, "kok", None)
    if kok is None:
        return 0
    ses_kok = Path(kok) / storage.KOVA_SES
    if not ses_kok.is_dir():
        return 0
    kullanicilar = {str(u) for (u,) in db.query(User.id).all()}
    silinen = 0
    for k in ses_kok.iterdir():
        if k.is_dir() and k.name not in kullanicilar:
            try:
                depo.klasor_sil(storage.ses_klasoru(k.name))
                silinen += 1
            except Exception:
                logger.exception("Sahipsiz ses klasörü silinemedi: %s", k.name)
    return silinen


def _temizle() -> dict:
    from api.db.session import SessionLocal
    db = SessionLocal()
    sonuc: dict = {}
    try:
        sonuc.update(plan_islerini_temizle(db))
        sonuc["onay_kaniti"] = onay_kanitlarini_temizle(db)
        db.commit()
        sonuc["ses_klasoru"] = sahipsiz_ses_klasorleri(db)
    except Exception:
        db.rollback()
        logger.exception("Gece temizliği (DB) başarısız")
    finally:
        db.close()
    try:
        sonuc["dosya"] = eski_dosyalari_sil(denetim_arsiv_klasorleri())
    except Exception:
        logger.exception("Gece temizliği (dosya) başarısız")
    logger.info("Gece temizliği: %s", sonuc)
    return sonuc


def gece_temizligi() -> None:
    """APScheduler işi. Çok worker'da yalnız biri koşar (Postgres advisory lock);
    SQLite'ta doğrudan. ASLA istisna fırlatmaz."""
    from sqlalchemy import text
    from api.db.session import engine
    conn = None
    try:
        if engine.dialect.name == "postgresql":
            conn = engine.connect()
            if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                {"k": KILIT_ID}).scalar():
                conn.close()
                conn = None
                return
        _temizle()
    except Exception:
        logger.exception("Gece temizliği çöktü")
    finally:
        if conn is not None:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": KILIT_ID})
            finally:
                conn.close()


