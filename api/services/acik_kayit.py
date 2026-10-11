"""
Terk edilmiş açık uyku kayıtlarının kapanış kuralı — TEK KAYNAK (2026-10-12).

İki kullanıcısı var:
  • saklama.gece_temizligi (her gece 03:30 UTC): başlangıcının üzerinden
    24 saat geçmiş açık kayıtları kapatır,
  • scripts/acik_sayac_kapat.py: elle tarama/onarım (16 saat eşiği).

Kural (bkz. betiğin başlığı ve acik-sayac-kapatma-tuzagi): kapsayan manuel
kaydın bitişi; yoksa sonraki kaydın başlangıcı / bant süresi / +16 saat'in EN
ERKENİ. Kapatılan kayda kapanis_kaynagi='bakim' yazılır: bitişi TAHMİNDİR —
motor onu "bitiş bilinmiyor" sayar (sabah uyanışı değil, toplamlara girmez),
bitişi boş yeniden gönderim kaydı AÇMAZ ve batch yanıtının `kapatilanlar`ında
mobile bildirilir (logs._kaydi_guncelle).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api.models import Baby, SleepLog
from api.services.plan_service import uyku_sureleri

UYKU_TIPLERI = ("sleep", "nap")
KAPATMA_NOTU = "otomatik kapatıldı"
# v1.7 (2026-10-07): kapatılan/onarılan kayda sleep_logs.kapanis_kaynagi yazılır.
# Dolu kaynak, mobilin bitişi boş yeniden gönderiminin kaydı AÇMASINI engeller
# (logs._kaydi_guncelle) ve batch yanıtında `kapatilanlar`a girer.
KAPANIS_KAYNAGI = "bakim"
TERK_SAAT = 16                 # mutlak tavan: bundan uzun açık kayıt "terk edilmiş"
ASGARI_DK = 60                 # kapanış başlangıcın gerisine düşerse
TZ_OFFSET_MIN = 180            # UTC+3 — motorla aynı


def _bant_sureleri(db, baby_id, _onbellek: dict = {}) -> tuple[int, int]:
    """(planlanan gündüz uykusu dk, gece uykusu ÜST sınırı dk).

    Hesabın TEK kaynağı plan_service.uyku_sureleri — batch (K16.1) ve motor
    (K17) da aynı fonksiyonu kullanıyor. Burada yalnız bebek başına önbellek
    tutulur (betik yüzlerce satır tarıyor)."""
    if baby_id not in _onbellek:
        _onbellek[baby_id] = uyku_sureleri(db.get(Baby, baby_id))
    return _onbellek[baby_id]


def _sonraki_kayit_bas(kayitlar: list, bas: datetime) -> datetime | None:
    """Aynı bebeğin bu andan SONRA başlayan ilk kaydının başlangıcı.

    Yeni bir kayıt açılmışsa bebek o an uyanıktır — açık sayaç en geç o zaman
    bitmiştir. Elimizdeki en güçlü kanıt bu."""
    sonrakiler = [_utc(r.started_at) for r in kayitlar
                  if _utc(r.started_at) > bas]
    return min(sonrakiler) if sonrakiler else None


def _utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _adaylar(db, gun: int, terk_saat: int = TERK_SAAT,
             simdi: datetime | None = None):
    """(açık kayıt, kapanış, gerekçe) üçlüleri: son `gun` günde başlamış ve
    üzerinden `terk_saat` saat geçmiş açık uyku kayıtları."""
    simdi = simdi or datetime.now(timezone.utc)
    sinir = simdi - timedelta(hours=terk_saat)
    basla = simdi - timedelta(days=gun)

    acik = (db.query(SleepLog)
            .filter(SleepLog.type.in_(UYKU_TIPLERI),
                    SleepLog.ended_at.is_(None),
                    SleepLog.started_at >= basla)
            .order_by(SleepLog.baby_id, SleepLog.started_at)
            .all())
    acik = [a for a in acik if _utc(a.started_at) < sinir]
    if not acik:
        return []

    kapali: dict = {}
    tum: dict = {}
    for r in (db.query(SleepLog)
              .filter(SleepLog.started_at >= basla - timedelta(days=1))
              .all()):
        tum.setdefault(r.baby_id, []).append(r)
        if r.type in UYKU_TIPLERI and r.ended_at is not None:
            kapali.setdefault(r.baby_id, []).append(r)

    out = []
    for a in acik:
        bas = _utc(a.started_at)
        ortusen = [_utc(r.ended_at) for r in kapali.get(a.baby_id, [])
                   if _utc(r.started_at) <= bas <= _utc(r.ended_at)
                   and _utc(r.ended_at) > bas]
        if ortusen:
            out.append((a, min(ortusen), "kapsayan manuel kayıt"))
            continue
        out.append((a,) + _tahmini_kapanis(db, a, bas, tum.get(a.baby_id, [])))
    return out


def _tahmini_kapanis(db, kayit, bas: datetime, bebek_kayitlari: list):
    """Kapsayan kayıt yokken kapanış anı ve gerekçesi.

    `sleep` tipi TEK BAŞINA "gece uykusu" demek DEĞİLDİR: mobil gündüz
    uykularını da `sleep` olarak gönderebiliyor. Gece uykusu ölçütü motorunkiyle
    (plan_adapter._gece_uykusu_mu) aynı olmalı — yerel saate bakılır. Aksi hâlde
    sabah 09:50'de başlayan bir gündüz uykusuna 11 saatlik gece tavanı
    uygulanıyor ve kayıt "sonraki kaydın başlangıcına" kadar uzuyordu."""
    nap_dk, gece_dk = _bant_sureleri(db, kayit.baby_id)
    # v1.6: sınıf MOTORUN K19 kuralıyla (tipten bağımsız, gece 19:00+). Eski
    # yerel kural yalnız `sleep`e bakıyordu: 00:15'te açılmış `nap` 60 dk'lık
    # gündüz uykusu diye kapanıyordu.
    from api.services.plan_adapter import GECE_UYKUSU, uyku_sinifi_ham
    gece_mi = uyku_sinifi_ham(bas, None, TZ_OFFSET_MIN) == GECE_UYKUSU
    sure_dk = gece_dk if gece_mi else nap_dk
    adaylar = [(bas + timedelta(minutes=sure_dk),
                f"bant süresi +{sure_dk} dk"),
               (bas + timedelta(hours=TERK_SAAT), f"tavan +{TERK_SAAT} saat")]
    sonraki = _sonraki_kayit_bas(bebek_kayitlari, bas)
    if sonraki is not None:
        adaylar.append((sonraki, "sonraki kaydın başlangıcı"))
    kapanis, gerekce = min(adaylar, key=lambda x: x[0])
    if kapanis <= bas:
        return bas + timedelta(minutes=ASGARI_DK), f"asgari +{ASGARI_DK} dk"
    return kapanis, gerekce


def _not_ekle(row, metin: str) -> None:
    """notes alanına not EKLE (annenin notunu ezme) — logs._not_ekle ile aynı."""
    mevcut = (row.notes or "").strip()
    if metin in mevcut:
        return
    row.notes = f"{mevcut} | {metin}".strip(" |") if mevcut else metin


def _arsivle(satirlar: list) -> Path:
    """Değişiklikten ÖNCE satırların tam hâlini JSON'a yaz (geri alınabilir)."""
    from api.services.denetim import denetim_koku
    from api.services.kvkk import _satir
    klasor = denetim_koku().parent / "arsiv"
    klasor.mkdir(parents=True, exist_ok=True)
    zaman = datetime.now(timezone.utc)
    yol = klasor / f"acik-sayac-kapat-{zaman:%Y%m%dT%H%M%S}.json"
    yol.write_text(json.dumps({"zaman": zaman.isoformat(),
                               "sebep": f"{TERK_SAAT} saatten eski açık uyku sayacı kapatıldı",
                               "kayitlar": [_satir(r) for r in satirlar]},
                              ensure_ascii=False, indent=1, default=str), "utf-8")
    return yol
