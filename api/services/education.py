"""
Eğitim videoları servisi — katalog, aşama türetme, günün önerisi, ilerleme.

İŞ MANTIĞI BURADA: router yalnız HTTP kabuğudur. `scripts/video_yukle.py` de
kataloğu bu dosyadaki `katalog_upsert` ile günceller — upsert kuralı iki yerde
ayrı yazılırsa betik ile API ayrışır.

AŞAMA (mobilin "şu an neredesin" rozeti):
  • `babies.mevcut_asama` DOLU ise o kullanılır (kaynak "anne"). Annenin beyanı
    hesaptan üstündür: "planım 9. günde ama ben hâlâ kapıdayım" diyebilmeli.
  • NULL ise eğitim gününden türetilir (kaynak "plan").

TÜM VİDEOLAR HER ZAMAN ERİŞİLEBİLİR. Aşama yalnız SIRALAMA/ÖNERİ içindir;
hiçbir video kilitlenmez — anne merak ettiğini istediği an izleyebilmeli.
"""
from __future__ import annotations

import csv
import logging
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from api.db import upsert
from api.models import Baby, EducationVideo, User, VideoProgress
from api.models.education_video import (ASAMA_ETIKETLERI, ASAMA_KODLARI,
                                        KATEGORILER)
from api.services import plan_adapter, storage

logger = logging.getLogger("tavsan.education")

# Eğitim günü → aşama. 13 günlük İlayda protokolünün beş aşaması; plan
# içeriğindeki `content.days` de AYNI beş aşamayı taşır.
GUN_ASAMA = (
    ((1, 3), "besik_yani"),
    ((4, 6), "oda_ortasi"),
    ((7, 9), "kapi"),
    ((10, 12), "esik"),
    ((13, 13), "bitis"),
)

VARSAYILAN_ASAMA = "egitim_oncesi"
GENEL = "genel"


# ---------------------------------------------------------------------------
# Aşama
# ---------------------------------------------------------------------------
def asama_belirle(baby: Baby | None, today: date | None = None) -> dict:
    """{"kod", "etiket", "kaynak"} — kaynak "anne" | "plan"."""
    beyan = (getattr(baby, "mevcut_asama", None) or "").strip() if baby else ""
    if beyan in ASAMA_KODLARI:
        return {"kod": beyan, "etiket": ASAMA_ETIKETLERI[beyan],
                "kaynak": "anne"}

    kod = _plandan_asama(baby, today)
    return {"kod": kod, "etiket": ASAMA_ETIKETLERI[kod], "kaynak": "plan"}


def _plandan_asama(baby: Baby | None, today: date | None) -> str:
    if baby is None or getattr(baby, "training_started_at", None) is None:
        return VARSAYILAN_ASAMA                     # eğitim başlamamış
    if getattr(baby, "training_completed_at", None) is not None:
        return "egitim_sonrasi"
    gun = plan_adapter.egitim_gunu(baby.training_started_at, today)
    if gun is None:
        return VARSAYILAN_ASAMA
    for (bas, bit), kod in GUN_ASAMA:
        if bas <= gun <= bit:
            return kod
    # 13. günü geçmiş ama "tamamlandı" işaretlenmemiş: program bitmiş sayılır,
    # son aşamada tutulur. (Mobil training_completed_at'i set etmeyi unutmuş
    # olabilir; burada sessizce "eğitim öncesi"ne düşmek yanlış olurdu.)
    return "bitis" if gun > 13 else VARSAYILAN_ASAMA


# ---------------------------------------------------------------------------
# Katalog
# ---------------------------------------------------------------------------
def _video_sozlugu(v: EducationVideo, ilerleme: VideoProgress | None) -> dict:
    return {
        "id": str(v.id),
        "slug": v.slug,
        "title": v.title,
        "description": v.description,
        "duration_sec": int(v.duration_sec or 0),
        "video_url": v.video_url,
        "poster_url": v.poster_url,
        "stage_tags": list(v.stage_tags or []),
        "chapters": v.chapters or [],
        "progress": {
            "position_sec": int(getattr(ilerleme, "position_sec", 0) or 0),
            "completed": bool(getattr(ilerleme, "completed_at", None)),
        },
    }


def katalog(db: Session, user: User, baby: Baby | None,
            today: date | None = None) -> dict:
    """GET /education/videos gövdesi."""
    videolar = (db.query(EducationVideo)
                .order_by(EducationVideo.category,
                          EducationVideo.order_in_category,
                          EducationVideo.title).all())
    ilerlemeler = {p.video_id: p for p in
                   db.query(VideoProgress)
                   .filter(VideoProgress.user_id == user.id).all()}

    asama = asama_belirle(baby, today)
    sozlukler = {v.id: _video_sozlugu(v, ilerlemeler.get(v.id)) for v in videolar}

    kategoriler = []
    for kod, baslik in KATEGORILER:
        icerik = [sozlukler[v.id] for v in videolar if v.category == kod]
        if icerik:
            kategoriler.append({"key": kod, "title": baslik, "videos": icerik})
    # Sözlükte olmayan bir kategori gelirse (yeni kategori eklenmiş) kaybolmasın.
    bilinen = {k for k, _ in KATEGORILER}
    for kod in dict.fromkeys(v.category for v in videolar
                             if v.category not in bilinen):
        kategoriler.append({"key": kod, "title": kod,
                            "videos": [sozlukler[v.id] for v in videolar
                                       if v.category == kod]})

    izlenen = sum(1 for s in sozlukler.values() if s["progress"]["completed"])
    toplam_sn = sum(int(v.duration_sec or 0) for v in videolar)
    secim = todays_pick(videolar, ilerlemeler, asama["kod"])

    return {
        "categories": kategoriler,
        "todays_pick": str(secim.id) if secim else None,
        "watched_count": izlenen,
        "total_count": len(videolar),
        "total_minutes": round(toplam_sn / 60),
        "asama": asama,
    }


def todays_pick(videolar: list[EducationVideo],
                ilerlemeler: dict[Any, VideoProgress],
                asama_kodu: str) -> EducationVideo | None:
    """Günün önerisi.

    Sıra: ① aşamayla eşleşen İZLENMEMİŞ ilk video → ② eşleşen ilk video
    (hepsi izlenmişse) → ③ "genel" etiketli ilk video (hiç eşleşme yoksa)
    → ④ katalogdaki ilk video. Sıralama katalog sırasıdır (kategori, sıra).
    """
    if not videolar:
        return None

    def izlendi(v):
        return bool(getattr(ilerlemeler.get(v.id), "completed_at", None))

    eslesen = [v for v in videolar if asama_kodu in (v.stage_tags or [])]
    for v in eslesen:
        if not izlendi(v):
            return v
    if eslesen:
        return eslesen[0]

    genel = [v for v in videolar if GENEL in (v.stage_tags or [])]
    for v in genel:
        if not izlendi(v):
            return v
    if genel:
        return genel[0]
    return videolar[0]


# ---------------------------------------------------------------------------
# İlerleme
# ---------------------------------------------------------------------------
# Oynatıcı videonun son saniyelerinde ilerleme göndermeyi kesebiliyor. Bu oran
# aşıldıysa "izlendi" sayılır; aksi hâlde watched_count mobilin `completed`
# bayrağını göndermesine bağlı kalır ve tek bir unutulan çağrıda sıfırda takılır.
TAMAMLANDI_ORANI = 0.95


def ilerleme_kaydet(db: Session, user: User, video: EducationVideo,
                    position_sec: int, completed: bool | None = None) -> VideoProgress:
    """(user, video) satırını ATOMİK upsert et. Ucuz: tek ifade, tek commit.

    "Önce SEÇ, yoksa EKLE" deseni yarış durumunda UniqueViolation veriyordu
    (Sentry, 2026-09-22): oynatıcı videoyu kapatırken "duraklat" ve "çıkış"
    isteklerini neredeyse aynı anda gönderiyor, ikisi de ilk satırı yazmaya
    çalışıyordu. Artık tek `INSERT ... ON CONFLICT DO UPDATE` var; kısıt
    ihlali hata değil, güncelleme.

    İKİ KURAL ÇAKIŞMADA DA KORUNUR:
      • İLERLEME GERİ GİTMEZ — `GREATEST(yeni, mevcut)`. Geç ulaşan eski bir
        konum (ör. 12 sn) ileri konumu (95 sn) ezemez.
      • "İZLEDİM" DAMGASI GERİ ALINMAZ — `COALESCE(mevcut, yeni)`. Geri sarıp
        yeniden izlemek damgayı silmez; ilk damga kalır."""
    sure = int(video.duration_sec or 0)
    konum = max(0, int(position_sec or 0))
    if sure:
        konum = min(konum, sure)

    bitti = bool(completed)
    if not bitti and sure and konum >= sure * TAMAMLANDI_ORANI:
        bitti = True
    simdi = datetime.now(timezone.utc)

    st = upsert.insert(VideoProgress).values(
        id=uuid.uuid4(), user_id=user.id, video_id=video.id,
        position_sec=konum,
        completed_at=simdi if bitti else None,
        updated_at=simdi,
    )
    st = st.on_conflict_do_update(
        index_elements=["user_id", "video_id"],
        set_={
            "position_sec": upsert.en_buyuk(st.excluded.position_sec,
                                            VideoProgress.position_sec),
            "completed_at": upsert.ilk_dolu(VideoProgress.completed_at,
                                            st.excluded.completed_at),
            "updated_at": simdi,
        },
    )
    db.execute(st)
    db.commit()
    # commit tüm ORM nesnelerini bayatlattı; satır DB'den taze okunur.
    return (db.query(VideoProgress)
            .filter(VideoProgress.user_id == user.id,
                    VideoProgress.video_id == video.id).one())


# ---------------------------------------------------------------------------
# Katalog tohumlama / upsert (CSV → DB)
# ---------------------------------------------------------------------------
def manifest_satirlari(csv_yolu: str | Path) -> list[dict]:
    """videolar.csv → normalize satırlar. Dosya sistemine BAKMAZ."""
    with open(csv_yolu, encoding="utf-8-sig", newline="") as f:
        ham = list(csv.DictReader(f))
    out = []
    for s in ham:
        slug = (s.get("slug") or "").strip()
        if not slug:
            continue                      # slug'ı boş satır tohumlanamaz
        etiketler = [e.strip() for e in
                     (s.get("asama_etiketleri") or "").split(",") if e.strip()]
        bilinmeyen = [e for e in etiketler if e not in ASAMA_KODLARI]
        if bilinmeyen:
            raise ValueError(f"{slug}: bilinmeyen aşama etiketi {bilinmeyen} "
                             f"(geçerli: {', '.join(ASAMA_KODLARI)})")
        out.append({
            "slug": slug,
            "title": (s.get("baslik") or "").strip(),
            "description": (s.get("aciklama") or "").strip() or None,
            "category": (s.get("kategori") or "").strip(),
            "order_in_category": int(s.get("sira") or 0),
            "stage_tags": etiketler or [GENEL],
            "dosya": (s.get("dosya") or "").strip(),
        })
    return out


def katalog_upsert(db: Session, satirlar: Iterable[dict],
                   sureler: dict[str, int] | None = None) -> dict:
    """Slug bazlı upsert. Dönen: {"eklenen": [...], "guncellenen": [...]}.

    `sureler`: slug → duration_sec. Verilmeyen slug'ın süresi DEĞİŞTİRİLMEZ
    (ffprobe koşulmadan da katalog metni güncellenebilsin)."""
    sureler = sureler or {}
    eklenen, guncellenen = [], []
    for s in satirlar:
        v = (db.query(EducationVideo)
             .filter(EducationVideo.slug == s["slug"]).one_or_none())
        yeni = v is None
        if yeni:
            v = EducationVideo(id=uuid.uuid4(), slug=s["slug"], duration_sec=0,
                               chapters=[])
            db.add(v)

        onceki = None if yeni else (v.title, v.description, v.category,
                                    v.order_in_category, list(v.stage_tags or []),
                                    v.duration_sec)
        v.title = s["title"] or v.title or s["slug"]
        v.description = s["description"]
        v.category = s["category"]
        v.order_in_category = s["order_in_category"]
        v.stage_tags = s["stage_tags"]
        v.video_url = storage.video_url(s["slug"])
        v.poster_url = storage.poster_url(s["slug"])
        if s["slug"] in sureler:
            v.duration_sec = int(sureler[s["slug"]])
        if v.chapters is None:
            v.chapters = []

        if yeni:
            eklenen.append(s["slug"])
        elif onceki != (v.title, v.description, v.category, v.order_in_category,
                        list(v.stage_tags or []), v.duration_sec):
            guncellenen.append(s["slug"])

    db.commit()
    logger.info("Katalog upsert: %d eklendi, %d güncellendi",
                len(eklenen), len(guncellenen))
    return {"eklenen": eklenen, "guncellenen": guncellenen}
