"""
logs router — /api/v1/logs

- POST /logs/batch: KAYIT BAZLI kabul/ret. Her kalem ayrı doğrulanır;
  geçerliler yazılır, geçersizler `skipped` listesinde sebebiyle döner. Yanıt
  daima 200 — 422 yalnız gövdenin kendisi bozuksa (bkz. BatchReq). Eskiden tek
  bozuk kayıt bütün batch'i düşürüyordu.
- POST /logs/batch: mobil SQLite sync-manager için toplu upsert. client_id ile
  idempotent: aynı (user_id, client_id) ikinci kez gelirse GÜNCELLENİR.
  client_id YOKSA (eski istemci) aynı bebek + aynı type + başlangıcı ±3 dk
  içindeki kayıt KOPYA sayılır ve güncellenir (K18.2) — zayıf ağda yeniden
  gönderim her seferinde yeni client_id ürettiği için tek başına client_id
  yetmiyordu (prod'da 41 kopya çiftinin tamamında client_id'ler farklıydı).
  Ayrıca bir bebekte aynı anda EN FAZLA BİR açık kayıt bırakılır (K16.1).
- GET /logs?from=&to=&baby_id=: tarih aralığı sorgusu (started_at'e göre).
- GET /logs?date=YYYY-MM-DD: K14.1 "o günün kayıtları" — gece yarısını aşan ve
  hâlâ açık olan kayıtlar da döner (bkz. _gun_filtresi).
- Uyku kayıtları yanıtta `kategori` + `kategori_etiket` taşır (K19.2): sınıfı
  saat belirler, istemcinin gönderdiği `type` DEĞİL.
- GET /logs/weekly-summary: haftalık agregasyon (mobil grafikleri tüketir).

Hepsi user_id scoped; baby_id kullanıcıya ait değilse o kayıt atlanır (skipped).
"""
import logging
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import and_, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.db import get_db
from api.deps import get_current_user
from api.models import Baby, SleepLog, User
from api.schemas.log import (
    BatchReq, BatchResult, DaySummary, SkippedEntry, SleepLogIn, SleepLogResp,
    SyncedEntry, WeeklySummaryResp,
)
from api.services.plan_adapter import (
    TZ_OFFSET_MIN, UYKU_ETIKETLERI, uyku_sinifi_ham,
)

router = APIRouter(prefix="/logs", tags=["logs"])


def _owned_baby_ids(db: Session, user: User) -> set:
    return {b.id for b in db.query(Baby.id).filter(Baby.user_id == user.id)}


# --- Kayıt bazlı ret sebepleri ----------------------------------------------
# `reason` makine tarafı, `detail` anneye/geliştiriciye gösterilecek Türkçe.
_ALAN_SEBEBI = {
    "type": ("invalid_type", "Kayıt tipi geçersiz"),
    "started_at": ("invalid_time", "Başlangıç zamanı geçersiz"),
    "ended_at": ("invalid_time", "Bitiş zamanı geçersiz"),
    "baby_id": ("invalid_baby", "Bebek kimliği geçersiz"),
    "client_id": ("invalid", "client_id geçersiz"),
    "notes": ("invalid", "Not alanı geçersiz"),
}


def _dogrulama_sebebi(exc: ValidationError) -> tuple[str, str]:
    """Pydantic hatasını (reason, Türkçe detail) çiftine indir.

    İLK hata yeterli: mobil kaydı zaten tümüyle reddedecek, ikinci alanın da
    bozuk olması kararı değiştirmiyor."""
    hatalar = exc.errors()
    if not hatalar:
        return "invalid", "Kayıt geçersiz"
    h = hatalar[0]
    alan = next((str(x) for x in reversed(h.get("loc") or ())
                 if isinstance(x, str)), "")
    if h.get("type") == "missing":
        return "missing_field", f"Zorunlu alan eksik: {alan or 'bilinmiyor'}"
    kod, metin = _ALAN_SEBEBI.get(alan, ("invalid", "Kayıt geçersiz"))
    if alan == "type":
        metin += " (beklenen: sleep, nap, sekerleme, wake, feed, night_wake, "
        metin += "nap_skipped)"
    return kod, metin


def _client_id_oku(ham: dict) -> str | None:
    """Doğrulama patlasa bile mobilin kaydı eşleyebilmesi için client_id.

    Kaydın geri kalanı bozuk olsa da bu alan okunabiliyorsa okunur — yoksa
    mobil hangi yerel satırın reddedildiğini bilemez ve onu sonsuza kadar
    yeniden gönderir."""
    cid = ham.get("client_id")
    return cid if isinstance(cid, str) and cid else None


@router.post("/batch", response_model=BatchResult)
def batch_upsert(req: BatchReq, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """Kayıt bazlı kabul/ret — geçerliler yazılır, geçersizler sebebiyle döner.

    Yanıt DAİMA 200'dür (gövdenin kendisi bozuk değilse). Tüm kalemler geçersiz
    olsa bile 200 + hepsi `skipped` döner: mobil o kayıtları kalıcı reddedip
    kuyruktan düşürebilsin. 422 dönmek, mobili "hangisi bozuktu" diye ikili
    bölmeye zorluyordu."""
    owned = _owned_baby_ids(db, user)
    created = updated = 0
    out: list[SleepLog] = []
    skipped: list[SkippedEntry] = []
    synced: list[SyncedEntry] = []

    for ham in req.logs:
        if not isinstance(ham, dict):
            skipped.append(SkippedEntry(
                reason="invalid",
                detail="Kayıt bir nesne değil (JSON object bekleniyor)"))
            continue
        cid = _client_id_oku(ham)
        try:
            item = SleepLogIn.model_validate(ham)
        except ValidationError as e:
            kod, detay = _dogrulama_sebebi(e)
            skipped.append(SkippedEntry(client_id=cid, reason=kod, detail=detay))
            continue

        if item.baby_id not in owned:            # başka kullanıcının bebeği
            skipped.append(SkippedEntry(
                client_id=cid, reason="not_owned",
                detail="Bu bebek hesabınıza ait değil"))
            continue

        row = None
        kopya = False
        if item.client_id is not None:
            # Birincil idempotency anahtarı. Bebek de süzülüyor: tekillik
            # (baby_id, client_id) üzerinde de tanımlı (K18.1).
            row = (db.query(SleepLog)
                   .filter(SleepLog.user_id == user.id,
                           SleepLog.baby_id == item.baby_id,
                           SleepLog.client_id == item.client_id)
                   .one_or_none())
            if row is None:                       # eski satır başka bebeğe yazılmış olabilir
                row = (db.query(SleepLog)
                       .filter(SleepLog.user_id == user.id,
                               SleepLog.client_id == item.client_id)
                       .one_or_none())
            kopya = row is not None
        else:
            # K18.2 — client_id yok: zaman penceresiyle kopya ara.
            row = _kopya_bul(db, user, item)
            kopya = row is not None

        # Her kayıt KENDİ savepoint'inde yazılır: birinin kısıt ihlali
        # (ör. yarışan aynı client_id) diğerlerini geri almasın.
        try:
            with db.begin_nested():
                if row is None:                  # yeni kayıt
                    row = SleepLog(
                        user_id=user.id, baby_id=item.baby_id, type=item.type,
                        started_at=item.started_at, ended_at=item.ended_at,
                        notes=item.notes, client_id=item.client_id,
                    )
                    db.add(row)
                    created += 1
                else:                            # mevcut → güncelle (idempotent)
                    row.baby_id = item.baby_id
                    row.type = item.type
                    row.started_at = item.started_at
                    row.ended_at = item.ended_at
                    row.notes = item.notes
                    updated += 1
                db.flush()                       # aynı batch'te sonraki aramalar görsün
        except SQLAlchemyError:
            logging.getLogger("tavsan.logs").exception(
                "Batch kaydı yazılamadı (user=%s client_id=%s)", user.id, cid)
            skipped.append(SkippedEntry(
                client_id=cid, reason="db_error",
                detail="Kayıt veritabanına yazılamadı, tekrar denenecek"))
            continue

        out.append(row)
        synced.append(SyncedEntry(client_id=row.client_id, id=row.id))
        if kopya:
            # Spec: kopya HEM synced'de mevcut id ile döner HEM skipped'de
            # işaretlenir. Mobil "duplicate" sebebini BAŞARILI sayıyor
            # (kayıt zaten sunucuda) — kuyruktan düşer, yeniden gönderilmez.
            skipped.append(SkippedEntry(
                client_id=cid, id=row.id, reason="duplicate",
                detail="Bu kayıt zaten kaydedilmişti, güncellendi"))

    # K13.3 — AÇIK SAYACI KAPAT. Anne sayacı başlatıp durdurmuyor, sonra aynı
    # uykuyu elle giriyor; iki kayıt iki ayrı uyku sanılıyordu. Hesap tarafı
    # (K13.1/K13.2) bunu zaten tekilleştiriyor ama kayıt DB'de açık kaldığı
    # sürece mobilde sayaç dönmeye devam ediyor. Burada TEK yazımla kapatılır.
    timer_closed = _acik_sayaclari_kapat(db, user, out)
    # K16.1 — yeni bir AÇIK kayıt geldiyse aynı bebekteki diğer açık kayıtlar
    # kapatılır. Üç açık kaydın üçünün birden "sürüyor" sayılması gerçek vakada
    # günü 5 gündüz uykusuna çıkarıyordu.
    if _tek_acik_kayit_birak(db, user, out):
        timer_closed = True

    db.commit()
    for r in out:
        db.refresh(r)

    # K8 — kayıt girildiği anda plan da yeniden hesaplanır. v1'de bu YOKTU:
    # hesap yalnız Eğitim sekmesi açılınca koşuyordu, dolayısıyla "kaydı girdim
    # ama plan değişmedi" davranışı ortaya çıkıyordu. Hesap ucuzdur (deterministik
    # çizelge matematiği, LLM/ağ YOK) ve içerik değişmediyse DB'ye yazılmaz.
    plan_updated = _plani_tazele(db, user, {r.baby_id for r in out})
    return BatchResult(created=created, updated=updated, skipped=skipped,
                       synced=synced, logs=out, plan_updated=plan_updated,
                       timer_closed=timer_closed)


UYKU_TIPLERI = ("sleep", "nap")

# K18.2 — client_id taşımayan kayıtlarda "aynı kayıt" penceresi.
KOPYA_PENCERE = timedelta(minutes=3)

# K19.2 — yanıtta kategori taşıyacak tipler. `nap_skipped` HARİÇ: o bir uyku
# değil, "bu uykuyu hiç yapmadı" beyanıdır.
UYKU_TIPLERI_TUM = ("sleep", "nap", "sekerleme")


def _kopya_bul(db: Session, user: User, item) -> SleepLog | None:
    """K18.2 — client_id taşımayan kayıt için KOPYA satırı bul.

    Ölçüt: aynı bebek + aynı type + başlangıcı ±3 dk içinde. Birden çok aday
    varsa başlangıcı EN YAKIN olan.

    Yalnız client_id YOKKEN çalışır: client_id gönderen istemcilerde birincil
    anahtar zaten var, orada zaman penceresi uygulamak iki ayrı gerçek uykuyu
    (ör. 10:00 ve 10:02'de başlayan iki beslenme) sessizce birleştirebilirdi."""
    alt = item.started_at - KOPYA_PENCERE
    ust = item.started_at + KOPYA_PENCERE
    adaylar = (db.query(SleepLog)
               .filter(SleepLog.user_id == user.id,
                       SleepLog.baby_id == item.baby_id,
                       SleepLog.type == item.type,
                       SleepLog.started_at >= alt,
                       SleepLog.started_at <= ust)
               .all())
    if not adaylar:
        return None
    return min(adaylar,
               key=lambda r: abs((_as_utc(r.started_at)
                                  - _as_utc(item.started_at)).total_seconds()))


def _tek_acik_kayit_birak(db: Session, user: User,
                          gelenler: list[SleepLog]) -> bool:
    """K16.1 — bir bebekte aynı anda EN FAZLA BİR açık sleep/nap kaydı kalsın.

    Bu batch'te AÇIK bir kayıt geldiyse, aynı bebeğin daha ESKİ açık kayıtları
    kapatılır:
        kapanış = min(yeni kaydın başlangıcı, eski başlangıç + bandın süresi)
    "Yeni kayıt açıldı" bilgisi bebeğin o an uyanık olduğunun kanıtıdır; bandın
    süresi ise üst sınır (bebek 6 saat aralıksız kestirmiyor).

    Bu batch'te gelen kayıtlar BİRBİRİNİ de kapatır — zayıf ağda üç açık kayıt
    tek istekte gelebiliyor.

    Dönen: en az bir kayıt kapatıldı mı."""
    from api.services.plan_service import uyku_sureleri     # döngüsel import önleme

    yeni_acik = [r for r in gelenler
                 if r.type in UYKU_TIPLERI and r.ended_at is None]
    if not yeni_acik:
        return False
    kapatildi = False

    for bebek_id in {r.baby_id for r in yeni_acik}:
        baby = db.get(Baby, bebek_id)
        nap_dk, _gece = uyku_sureleri(baby)
        acik = (db.query(SleepLog)
                .filter(SleepLog.user_id == user.id,
                        SleepLog.baby_id == bebek_id,
                        SleepLog.type.in_(UYKU_TIPLERI),
                        SleepLog.ended_at.is_(None))
                .all())
        if len(acik) < 2:
            continue
        acik.sort(key=lambda r: _as_utc(r.started_at))
        # En YENİ açık kayıt sürüyor sayılır; öncekiler kapatılır.
        for i, eski in enumerate(acik[:-1]):
            bas = _as_utc(eski.started_at)
            kapanis = min(_as_utc(acik[i + 1].started_at),
                          bas + timedelta(minutes=nap_dk))
            if kapanis < bas:
                kapanis = bas
            eski.ended_at = kapanis
            _not_ekle(eski, "otomatik kapatıldı: yeni kayıt açıldı")
            kapatildi = True
            logging.getLogger("tavsan.logs").info(
                "K16.1 açık kayıt kapatıldı: log=%s baby=%s ended_at=%s",
                eski.id, bebek_id, kapanis.isoformat())
    return kapatildi


def _not_ekle(row: SleepLog, metin: str) -> None:
    """notes alanına not EKLE (mevcut notu ezme — anne yazmış olabilir)."""
    mevcut = (row.notes or "").strip()
    if metin in mevcut:
        return
    row.notes = f"{mevcut} | {metin}".strip(" |") if mevcut else metin


def _acik_sayaclari_kapat(db: Session, user: User,
                          gelenler: list[SleepLog]) -> bool:
    """K13.3 — bu batch'teki manuel kayıtla ÖRTÜŞEN açık sayacı kapat.

    Ölçüt: manuel kayıt (kapalı, sleep/nap) açık sayacın BAŞLANGICINI kapsıyor
    (started_at <= sayac.started_at <= ended_at). Kapanış saati manuel kaydın
    `ended_at`'idir.

    NEDEN KAPSAMA ŞARTI VAR: spec "başlangıcından sonra biten manuel kayıt"
    diyor, ama bu tek başına alınırsa sabah 10:01'de unutulan sayaç, öğleden
    sonraki 15:00'te biten BAŞKA bir uykuyla kapatılıp 5 saatlik hayalet bir
    uyku üretirdi. Örtüşme şartı, düzeltilmek istenen durumu (aynı uykunun iki
    kaydı) tam olarak yakalar. Terk edilmiş sayaçlar ayrı bir iştir ve
    scripts/acik_sayac_kapat.py ile 16 saatte kapatılır.

    Sayacın KENDİSİ bu batch'te geldiyse dokunulmaz — anne şu an uyku
    başlatıyordur.

    Dönen: en az bir sayaç kapatıldı mı."""
    manuel = [r for r in gelenler
              if r.type in UYKU_TIPLERI and r.ended_at is not None]
    if not manuel:
        return False
    gelen_idler = {r.id for r in gelenler}
    kapatildi = False

    for bebek_id in {r.baby_id for r in manuel}:
        acik = (db.query(SleepLog)
                .filter(SleepLog.user_id == user.id,
                        SleepLog.baby_id == bebek_id,
                        SleepLog.type.in_(UYKU_TIPLERI),
                        SleepLog.ended_at.is_(None))
                .all())
        for sayac in acik:
            if sayac.id in gelen_idler:
                continue                       # az önce başlatılan sayaç
            bas = _as_utc(sayac.started_at)
            ortusen = [m for m in manuel
                       if m.baby_id == bebek_id
                       and _as_utc(m.started_at) <= bas <= _as_utc(m.ended_at)]
            if not ortusen:
                continue
            # Birden çok aday varsa EN ERKEN biten: sayaç en geç o an bitmiştir.
            kapanis = min(_as_utc(m.ended_at) for m in ortusen)
            sayac.ended_at = kapanis
            kapatildi = True
            logging.getLogger("tavsan.logs").info(
                "K13.3 açık sayaç kapatıldı: log=%s baby=%s ended_at=%s",
                sayac.id, bebek_id, kapanis.isoformat())
    return kapatildi


def _plani_tazele(db: Session, user: User, baby_ids: set) -> bool:
    """Etkilenen bebeklerin bugünkü planını yeniden hesapla. Dönen: değişti mi.

    Hata YUTULUR ama loglanır: uyku kaydı senkronu, plan hesabı patladı diye
    başarısız olmamalıdır (mobil offline kuyruğu tıkanır). Plan bir sonraki
    GET /plans/today'de zaten yeniden hesaplanacaktır."""
    from api.models import Baby
    from api.services import plan_service

    degisti = False
    for bid in baby_ids:
        baby = db.get(Baby, bid)
        if baby is None:
            continue
        # Doğum tarihi kontrolü YOK: ensure_today_plan bantsız yolu da işliyor.
        # Burada elemek, aynı bebek için batch ile GET /plans/today'in FARKLI
        # plan üretmesi demek olurdu — ikisi tek kod yolunu paylaşmalı.
        try:
            onceki = plan_service.plan_for_date(
                db, user, baby, datetime.now(timezone.utc).date())
            onceki_sched = (onceki.content or {}).get("schedule") if onceki else None
            plan = plan_service.ensure_today_plan(db, user, baby)
        except Exception:                      # PlanError dahil → kayıt yine kabul
            logging.getLogger("tavsan.logs").exception(
                "Kayıt sonrası plan hesaplanamadı (baby=%s)", bid)
            continue
        if plan is not None and (plan.content or {}).get("schedule") != onceki_sched:
            degisti = True
    return degisti


def gun_araligi(gun: date, tz_offset_min: int = TZ_OFFSET_MIN
                ) -> tuple[datetime, datetime]:
    """Yerel bir günün UTC sınırları: [00:00, 24:00). Motor UTC+3 ile çalışır."""
    bas = (datetime.combine(gun, time.min, tzinfo=timezone.utc)
           - timedelta(minutes=tz_offset_min))
    return bas, bas + timedelta(days=1)


def gun_filtresi(gun: date, tz_offset_min: int = TZ_OFFSET_MIN):
    """K14.1 — "o günün kayıtları" SQL koşulu.

    Üç durumu birden kapsar; v2.1'e kadar yalnız birincisi vardı ve gece
    yarısını aşan kayıtlar günün listesinden düşüyordu:
      1. started_at o gün, VEYA
      2. ended_at o gün (dün 21:50 başlayıp bugün 07:05 biten gece uykusu), VEYA
      3. HÂLÂ AÇIK ve o günden önce başlamış (dün akşam başlatılıp
         durdurulmamış sayaç — "sürüyor" olarak görünmeli).
    """
    bas, bit = gun_araligi(gun, tz_offset_min)
    return or_(
        and_(SleepLog.started_at >= bas, SleepLog.started_at < bit),
        and_(SleepLog.ended_at.isnot(None),
             SleepLog.ended_at >= bas, SleepLog.ended_at < bit),
        and_(SleepLog.ended_at.is_(None), SleepLog.started_at < bas),
    )


@router.get("", response_model=list[SleepLogResp])
def list_logs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    date_: date | None = Query(
        default=None, alias="date",
        description="Yerel gün (YYYY-MM-DD). Verilirse from/to yok sayılır; "
                    "gece yarısını aşan ve hâlâ açık kayıtlar da döner (K14.1)."),
    baby_id: uuid.UUID | None = Query(default=None),
):
    q = db.query(SleepLog).filter(SleepLog.user_id == user.id)
    if baby_id is not None:
        q = q.filter(SleepLog.baby_id == baby_id)
    if date_ is not None:
        # K14.1 — gün sorgusu from/to ile BİRLEŞTİRİLMEZ: ikisi farklı soruların
        # cevabı ve kesişimleri sessizce boş liste üretirdi.
        q = q.filter(gun_filtresi(date_))
    else:
        if from_ is not None:
            q = q.filter(SleepLog.started_at >= from_)
        if to is not None:
            q = q.filter(SleepLog.started_at <= to)
    return [_kategorili(r) for r in q.order_by(SleepLog.started_at.desc()).all()]


def _kategorili(row: SleepLog) -> SleepLogResp:
    """K19.2 — satırı yanıta çevirirken uyku SINIFINI ekle.

    Sınıf motorla AYNI fonksiyondan (plan_adapter.uyku_sinifi_ham) gelir; iki
    yerde ayrı yazılsaydı mobilin bastığı etiket ile çizelgenin hesabı
    ayrışabilirdi."""
    resp = SleepLogResp.model_validate(row)
    if row.type in UYKU_TIPLERI_TUM:
        resp.kategori = uyku_sinifi_ham(row.started_at, row.ended_at,
                                        TZ_OFFSET_MIN)
        resp.kategori_etiket = UYKU_ETIKETLERI.get(resp.kategori)
    return resp


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@router.get("/weekly-summary", response_model=WeeklySummaryResp)
def weekly_summary(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    baby_id: uuid.UUID = Query(...),
    week_start: date | None = Query(default=None),
):
    """Belirtilen bebeğin 7 günlük özeti. week_start verilmezse son 7 gün (bugün dahil).
    total uyku saati, gece uyanma/besleme sayısı ve gün bazında dağılım döner."""
    baby = db.get(Baby, baby_id)
    if baby is None or baby.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bebek bulunamadı")

    today = datetime.now(timezone.utc).date()
    start = week_start if week_start is not None else today - timedelta(days=6)
    end = start + timedelta(days=6)
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end, time.max, tzinfo=timezone.utc)

    rows = (db.query(SleepLog)
            .filter(SleepLog.user_id == user.id,
                    SleepLog.baby_id == baby_id,
                    SleepLog.started_at >= start_dt,
                    SleepLog.started_at <= end_dt)
            .all())

    # Gün bazında (started_at'in UTC tarihi) topla.
    buckets: dict[date, dict] = defaultdict(
        lambda: {"sleep_hours": 0.0, "naps": 0, "night_wakes": 0, "night_feeds": 0})
    for r in rows:
        d = _as_utc(r.started_at).date()
        b = buckets[d]
        if r.type in ("sleep", "nap") and r.ended_at is not None:
            hours = (_as_utc(r.ended_at) - _as_utc(r.started_at)).total_seconds() / 3600.0
            if hours > 0:
                b["sleep_hours"] += hours
            if r.type == "nap":
                b["naps"] += 1
        elif r.type == "night_wake":
            b["night_wakes"] += 1
        elif r.type == "feed":
            b["night_feeds"] += 1

    days: list[DaySummary] = []
    total_sleep = total_wakes = total_feeds = 0.0
    for i in range(7):
        d = start + timedelta(days=i)
        b = buckets.get(d, {"sleep_hours": 0.0, "naps": 0, "night_wakes": 0, "night_feeds": 0})
        days.append(DaySummary(
            date=d, sleep_hours=round(b["sleep_hours"], 2), naps=b["naps"],
            night_wakes=b["night_wakes"], night_feeds=b["night_feeds"]))
        total_sleep += b["sleep_hours"]
        total_wakes += b["night_wakes"]
        total_feeds += b["night_feeds"]

    return WeeklySummaryResp(
        baby_id=baby_id, from_date=start, to_date=end,
        total_sleep_hours=round(total_sleep, 2),
        total_night_wakes=int(total_wakes), total_night_feeds=int(total_feeds),
        days=days)
