"""
voice router — /api/v1/voice/*

v2.3 — "ÜRET VE BIRAK": ElevenLabs'te klon sesi KALICI TUTULMAZ.
  POST /clone → ses klonlanır → paket ARKA PLANDA üretilip depoya yazılır →
  ElevenLabs'teki ses SİLİNİR → anne kendi depomuzdan dinler.
Sebep: hesabın 10 klon slotu var, 94+ kullanıcı. Slot artık yalnız üretim
süresince (dakikalar) tutuluyor.

- POST /clone: multipart ses (30sn) → ElevenLabs voice clone → status=cloning,
  sonra generating. DB yazımı başarısızsa ElevenLabs sesi HEMEN silinir.
- GET /voice-status: status + progress{done,total} + hazır içerik sayısı.
- GET /stories: katalog + her içerik için "hazır mı" + imzalı bağlantı.
- POST /generate: ÜRETİM YAPMAZ. Hazır dosyanın 1 saatlik imzalı bağlantısını
  döner; hazır değilse 409. Yanıt şeması eski istemciler için AYNI.

Hepsi auth korumalı. Dış servis hatası (key yok/kota) → anlamlı JSON + uygun kod.
"""
import logging
import math
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from api import tts
from api.db import get_db
from api.deps import get_current_user, require_premium
from api.models import User, VoiceProfile
from api.models import VoiceAudio
from api.schemas.voice import (
    Progress, StoriesResp, StoryItem, VoiceCloneResp, VoiceGenerateReq,
    VoiceGenerateResp, VoiceStatusResp,
)
from api.services import storage
from api.services import usage as usage_svc
from api.services import voice as voice_svc
from api.services import voice_paket, voice_uretim

logger = logging.getLogger("tavsan.voice.router")
router = APIRouter(prefix="/voice", tags=["voice"])

MAX_CLONE_BYTES = 15 * 1024 * 1024        # ~30sn ses için bol; kötüye kullanımı sınırla
SAMPLE_TEXT = "Merhaba, ben senin sesinim. İyi geceler, tatlı rüyalar."

# --- Kullanıcıya görünen hata metinleri --------------------------------------
# Mobil `detail` alanını OLDUĞU GİBİ ekrana basıyor. Bu yüzden her metin
# Türkçe, tam cümle ve EYLEM ÖNEREN olmak zorunda; boş gövde ya da
# "Bad Gateway" gibi bir şey anneye hiçbir şey anlatmıyordu.
SES_422_MESAJ = "Ses kaydı işlenemedi, lütfen sessiz bir ortamda tekrar deneyin."
SES_KAPASITE_MESAJ = ("Ses kaydı servisi şu anda dolu. Ekibimize bildirildi, "
                      "lütfen kısa süre sonra tekrar deneyin.")
SES_SERVIS_MESAJ = ("Ses servisi şu an kullanılamıyor. Lütfen daha sonra tekrar "
                    "deneyin.")

# --- Aylık klonlama limiti ---------------------------------------------------
# Gizlilik politikası "ses kaydı ayda bir kez yenilenebilir" diyor. Bu kural
# kodda HİÇ zorlanmıyordu: kullanıcı istediği kadar klon açabiliyor, her biri
# ElevenLabs'te slot + ücret tutuyor ve gereksiz biyometrik veri birikiyordu.
# Politika ile davranış ayrışmıştı; sınır burada zorlanıyor.
CLONE_COOLDOWN_DAYS = 30


def _son_klonlama(profile: VoiceProfile | None):
    """Bu profilin klonlama anı — last_cloned_at, yoksa created_at.

    COALESCE bilinçli: 0009 migration'ı öncesi satırlarda last_cloned_at NULL.
    NULL'u "hiç klonlamamış" saymak mevcut her kullanıcıya sessizce fazladan bir
    hak doğururdu; created_at zaten klonlamanın yapıldığı andır."""
    if profile is None:
        return None
    return getattr(profile, "last_cloned_at", None) or profile.created_at


def _klon_durumu(profile: VoiceProfile | None, simdi: datetime | None = None) -> dict:
    """{can_clone, next_clone_available_at, retry_after_days} — tek hesap yeri.

    Hem POST /clone kapısı hem GET /voice-status aynı fonksiyondan besleniyor:
    mobilin gösterdiği tarih ile sunucunun uyguladığı sınır AYRIŞAMAZ."""
    simdi = simdi or datetime.now(timezone.utc)
    son = _son_klonlama(profile)
    if son is None:
        return {"can_clone": True, "next_clone_available_at": None,
                "retry_after_days": 0}
    if son.tzinfo is None:                 # SQLite naive datetime döndürebiliyor
        son = son.replace(tzinfo=timezone.utc)
    musait = son + timedelta(days=CLONE_COOLDOWN_DAYS)
    if simdi >= musait:
        return {"can_clone": True, "next_clone_available_at": None,
                "retry_after_days": 0}
    # Kalan süre GÜNE YUKARI yuvarlanır: 0.2 gün kalmışken "0 gün" demek
    # kullanıcıya "şimdi deneyebilirim" dedirtip tekrar 429 aldırırdı.
    kalan = musait - simdi
    return {"can_clone": False, "next_clone_available_at": musait,
            "retry_after_days": max(1, math.ceil(kalan.total_seconds() / 86400)),
            "_kalan_saniye": max(1, int(kalan.total_seconds()))}


def _son_profil(db: Session, user: User) -> VoiceProfile | None:
    """Kullanıcının GÜNCEL ses profili.

    Sıralama iki ölçüte göre, bu sırayla:
      1. 'replaced' OLMAYAN önce — yenisi alınmış ses artık ElevenLabs'te yok,
         voice-status onu göstermemeli.
      2. Klonlama anı (last_cloned_at, yoksa created_at) AZALAN.

    NEDEN İKİ ÖLÇÜT: eskiden yalnız created_at.desc() vardı ve created_at
    server_default=now() ile SANİYE hassasiyetinde yazılıyor. Aynı saniyede
    açılmış iki profil berabere kalıp sıralama rastgeleleşiyor, /voice-status
    ESKİ (silinmiş) voiceId'yi dönebiliyordu. Aylık limit gelmeden önce
    kullanıcılar peş peşe klon açabildiği için üretimde böyle satırlar VAR."""
    return (db.query(VoiceProfile).filter(VoiceProfile.user_id == user.id)
            .order_by((VoiceProfile.status == "replaced").asc(),
                      func.coalesce(VoiceProfile.last_cloned_at,
                                    VoiceProfile.created_at).desc())
            .first())


@router.post("/clone", response_model=None,
             responses={200: {"model": VoiceCloneResp},
                        403: {"description": "Premium üyelik gerekiyor"},
                        422: {"description": "Ses kaydı işlenemedi"},
                        429: {"description": "Aylık klonlama hakkı dolu"},
                        503: {"description": "Ses servisi/kapasitesi yok"}})
async def clone(audio: UploadFile = File(...), name: str = Form("Kullanıcı Sesi"),
                db: Session = Depends(get_db),
                user: User = Depends(require_premium)):
    # AYLIK LİMİT — ses OKUNMADAN önce kontrol edilir: 15MB'lık gövdeyi boşuna
    # almayalım ve ElevenLabs'e hiç gitmeyelim.
    onceki = _son_profil(db, user)
    durum = _klon_durumu(onceki)
    if not durum["can_clone"]:
        tarih = durum["next_clone_available_at"].strftime("%d.%m.%Y")
        logger.info("Klonlama limiti: user=%s sonraki=%s", user.id, tarih)
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": ("Sesini ayda bir kez kaydedebilirsin. "
                           f"Bir sonraki hakkın: {tarih}"),
                "retry_after_days": durum["retry_after_days"],
                "next_clone_available_at":
                    durum["next_clone_available_at"].isoformat(),
            },
            headers={"Retry-After": str(durum["_kalan_saniye"])},
        )

    data = await audio.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Ses dosyası boş")
    if len(data) > MAX_CLONE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="Ses dosyası çok büyük (max ~15MB)")

    dosya_adi = audio.filename or "sample.mp3"
    icerik_turu = audio.content_type or "audio/mpeg"
    r = voice_svc.clone_voice(name, data, dosya_adi, icerik_turu)

    # SLOT TAVANI — hesap genelinde 10 klon sesi sınırı (kullanıcı başına aylık
    # limitten AYRI bir tavan). Beta annelerinin 2026-09-16..18 arasındaki TÜM
    # başarısızlıkları buydu. Tavana çarpınca eski ses temizliği de hiç
    # çalışmıyordu (temizlik başarılı klondan SONRA yapılıyor), yani kilit
    # kendini besliyordu: yer yok → klon yok → temizlik yok → yer yok.
    # Kurtarma bilinçli olarak klondan SONRA: normal akışta kimsenin sesine
    # dokunulmasın, yalnız gerçekten tıkandığında dokunulsun.
    kurtarma = {"serbest": 0, "asama": None}
    if not r.get("ok") and r.get("reason") == voice_svc.HATA_SLOT_DOLU:
        kurtarma = _slot_kurtar(db, user)
        if kurtarma["serbest"] > 0:
            r = voice_svc.clone_voice(name, data, dosya_adi, icerik_turu)

    if not r.get("ok"):
        # Kullanıcının KENDİ sesini yer açmak için sildik ve klonlama yine
        # tutmadı: hem sesi gitti hem de 30 günlük bekleme yüzünden yenisini
        # kaydedemez durumda kalırdı. Hakkı iade ediliyor — kaybın sebebi
        # kendi davranışı değil, bizim kurtarma denememiz.
        if kurtarma["asama"] == "kendi":
            _klon_hakkini_iade_et(db, user)
        # user KİMLİĞİ loglanıyor: eskiden başarısızlıkta hiç kullanıcı bilgisi
        # yoktu, "kim etkilendi" sorusu loglardan CEVAPLANAMIYORDU.
        logger.warning("Voice clone başarısız: user=%s reason=%s upstream=%s",
                       user.id, r.get("reason"), r.get("error"))
        raise HTTPException(status_code=r.get("status", 502),
                            detail=_klon_hata_mesaji(r))
    voice_id = r["voice_id"]
    # Klonlama karakter değil İŞLEM başına ücretlendirilir; kaç klon yapıldığı
    # raporda görünsün diye ayrı satır açılır (tutar config'ten).
    usage_svc.kaydet(usage_svc.SERVIS_ELEVENLABS, usage_svc.OP_VOICE_CLONE,
                     model="voice-clone", user_id=user.id)

    # --- DB kaydı + ROLLBACK (Faz 2.1) -----------------------------------
    # Satır açılamazsa ElevenLabs'teki ses ÖKSÜZ kalır: kimse ona sahip
    # olmadığı için silinmez, slotu süresiz tutar. O yüzden yazım başarısız
    # olursa ses HEMEN silinir.
    try:
        profile = VoiceProfile(
            user_id=user.id, elevenlabs_voice_id=voice_id, sample_url=None,
            status="cloning", progress_done=0,
            progress_total=voice_paket.paket_boyutu(),
            last_cloned_at=datetime.now(timezone.utc))
        db.add(profile)
        db.commit()
        db.refresh(profile)
    except Exception:
        db.rollback()
        logger.exception("Voice profil satırı yazılamadı — ElevenLabs sesi "
                         "geri alınıyor: user=%s voice_id=%s", user.id, voice_id)
        voice_svc.delete_voice(voice_id)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=SES_SERVIS_MESAJ)

    logger.info("Voice clone tamam: user=%s voice_id=%s profil=%s",
                user.id, voice_id, profile.id)

    # ESKİ SESİ ve ESKİ PAKETİ TEMİZLE (Faz 4.3 — tek aktif ses).
    _eski_sesleri_temizle(db, user, yeni_voice_id=voice_id,
                          yeni_profil_id=profile.id)

    # Paket üretimini arka plana al (en fazla 2 eşzamanlı).
    voice_uretim.kuyruga_al(profile.id)
    # sampleUrl artık ÜRETİLMİYOR: ses dakikalar içinde silineceği için ayrı bir
    # örnek dosya hem kota harcar hem yanıltıcı olur. Paketin kendisi örnektir.
    return VoiceCloneResp(voiceId=voice_id, sampleUrl=None)


def _eski_sesleri_temizle(db: Session, user: User, yeni_voice_id: str,
                          yeni_profil_id=None) -> None:
    """Kullanıcının ÖNCEKİ klon seslerini ElevenLabs'ten sil, satırı işaretle.

    Neden: her klon ElevenLabs'te bir slot tutuyor ve ücretlendiriliyor; ayrıca
    kullanılmayan biyometrik veriyi saklamanın bir gerekçesi yok. Kullanıcı yeni
    sesini kaydettiği anda eskisi gereksizdir.

    Hata YUTULUR: temizlik kullanıcının akışını bozmaz. Silinemezse satır
    'ready' kalır (yalan söylemeyelim) ve uyarı loglanır."""
    eskiler = (db.query(VoiceProfile)
               .filter(VoiceProfile.user_id == user.id,
                       VoiceProfile.elevenlabs_voice_id.isnot(None),
                       VoiceProfile.elevenlabs_voice_id != yeni_voice_id,
                       VoiceProfile.status != "replaced")
               .all())
    for eski in eskiler:
        sonuc = voice_svc.delete_voice(eski.elevenlabs_voice_id)
        if sonuc.get("ok"):
            eski.status = "replaced"
            eski.elevenlabs_voice_id = None
            logger.info("Eski klon sesi silindi: user=%s", user.id)
        else:
            logger.warning("Eski klon sesi SİLİNEMEDİ (user=%s voice_id=%s): %s "
                           "— slot/ücret birikebilir, elle temizlik gerekebilir",
                           user.id, eski.elevenlabs_voice_id, sonuc.get("error"))

    # Faz 4.3 — TEK AKTİF SES: eski paketlerin dosyaları da gider. Aksi hâlde
    # anne yeni ses kaydettikten sonra eski sesiyle üretilmiş masalları
    # dinlemeye devam eder ve depo sonsuza kadar büyür.
    eski_profiller = (db.query(VoiceProfile)
                      .filter(VoiceProfile.user_id == user.id,
                              VoiceProfile.id != yeni_profil_id)
                      .all()) if yeni_profil_id is not None else []
    for eski in eski_profiller:
        try:
            silinen = storage.depo().klasor_sil(
                storage.ses_klasoru(user.id, eski.id))
            if silinen:
                logger.info("Eski ses paketi silindi: profil=%s dosya=%d",
                            eski.id, silinen)
        except Exception:
            logger.exception("Eski ses paketi silinemedi: profil=%s", eski.id)
        db.query(VoiceAudio).filter(
            VoiceAudio.voice_profile_id == eski.id).delete()
    if eskiler or eski_profiller:
        db.commit()


@router.get("/voice-status", response_model=VoiceStatusResp)
def voice_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Faz 3.1 — paket durumu + ilerleme.

    `status` mobilin ekran kararını verir:
      cloning/generating → "hazırlanıyor" + progress çemberi
      ready/released     → dinlenebilir
      failed             → "olmadı, tekrar dene" (hak zaten iade edildi)"""
    profile = _son_profil(db, user)
    # Klonlama hakkı POST /clone ile AYNI fonksiyondan hesaplanıyor: mobilin
    # gösterdiği tarih ile sunucunun uyguladığı sınır ayrışamaz.
    durum = _klon_durumu(profile)
    if profile is None:
        return VoiceStatusResp(status="none", can_clone=True,
                               progress=Progress(done=0, total=0))
    hazir = (db.query(VoiceAudio)
             .filter(VoiceAudio.voice_profile_id == profile.id).count())
    return VoiceStatusResp(
        status=profile.status, voiceId=profile.elevenlabs_voice_id,
        sampleUrl=profile.sample_url, created_at=profile.created_at,
        last_cloned_at=_son_klonlama(profile),
        can_clone=durum["can_clone"],
        next_clone_available_at=durum["next_clone_available_at"],
        retry_after_days=durum["retry_after_days"],
        progress=Progress(done=profile.progress_done or 0,
                          total=profile.progress_total or 0),
        hazir_icerik=hazir,
        error=profile.error,
        released_at=profile.released_at)


@router.get("/stories", response_model=StoriesResp)
def stories(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Faz 3.2 — katalog + her içerik için "hazır mı" + imzalı bağlantı.

    Metinler yanıtta DÖNMEZ (v1'den beri böyle). Hazır içerikler 1 saatlik
    imzalı bağlantı taşır; bağlantı her istekte YENİDEN üretilir, saklanmaz."""
    cat = voice_svc.load_stories()
    profile = _son_profil(db, user)
    hazir_yollar: dict[str, str] = {}
    if profile is not None:
        hazir_yollar = {
            a.content_id: a.storage_path
            for a in db.query(VoiceAudio).filter(
                VoiceAudio.voice_profile_id == profile.id).all()}
    paket_idler = [x["id"] for x in voice_paket.paket_icerikleri()]
    uretilemeyen = set()
    if profile is not None and (profile.error or "").startswith("Üretilemedi:"):
        uretilemeyen = {p.strip() for p in
                        profile.error.split(":", 1)[1].split(",") if p.strip()}

    def _item(x: dict) -> StoryItem:
        yol = hazir_yollar.get(x["id"])
        if yol:
            durum = "hazir"
        elif x["id"] in uretilemeyen:
            durum = "uretilemedi"
        else:
            durum = "hazirlaniyor"
        return StoryItem(
            id=x["id"], type=x.get("type", ""), title=x.get("title", ""),
            duration_hint=x.get("duration_hint"),
            hazir=bool(yol), durum=durum,
            audio_url=storage.imzali_url(yol) if yol else None)

    return StoriesResp(
        masallar=[_item(x) for x in cat.get("masallar", [])],
        ninniler=[_item(x) for x in cat.get("ninniler", [])],
        paket=voice_paket.paket_adi(), paket_icerikleri=paket_idler)


@router.post("/generate", response_model=VoiceGenerateResp,
             responses={409: {"description": "İçerik henüz hazır değil"}})
def generate(req: VoiceGenerateReq, db: Session = Depends(get_db),
             user: User = Depends(require_premium)):
    """Faz 3.3 — ÜRETİM YAPMAZ; hazır dosyanın imzalı bağlantısını döner.

    v2.3'te ses paketi klonlamadan hemen sonra toplu üretiliyor ve ElevenLabs'teki
    klon sesi siliniyor, dolayısıyla istek anında üretim MÜMKÜN DEĞİL. Yanıt
    şeması eski istemciler kırılmasın diye AYNI kaldı (`cached` her zaman True).

    Hazır değilse 409 — 404 DEĞİL: içerik var, yalnız henüz üretilmedi."""
    profile = _son_profil(db, user)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Henüz ses kaydınız yok")
    # Eski istemciler gövdede voiceId gönderiyor. Ses artık silindiği için
    # eşleşme ZORUNLU DEĞİL; ama başkasının voiceId'siyle gelen istek de
    # kabul edilmemeli: paket her zaman ÇAĞIRANIN kendi profilinden okunur.
    content_id = req.storyId
    if not content_id:
        # Eski istemci düz metin gönderdiyse hangi içerik olduğunu bilemeyiz.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Bu içerik henüz hazır değil. Ses paketiniz hazırlandığında "
                    "masal ve ninniler listede görünecek."))
    ses = (db.query(VoiceAudio)
           .filter(VoiceAudio.voice_profile_id == profile.id,
                   VoiceAudio.content_id == content_id)
           .one_or_none())
    if ses is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Bu içerik henüz hazır değil")
    logger.info("Voice sunum: user=%s icerik=%s", user.id, content_id)
    return VoiceGenerateResp(audio_url=storage.imzali_url(ses.storage_path),
                             cached=True, profile=tts.MASAL_PROFILI)


def _klon_hata_mesaji(r: dict) -> str:
    """Upstream hata sınıfı → anneye gösterilecek Türkçe cümle.

    Ham ElevenLabs metni (İngilizce, "custom voices (10 / 10)" gibi) kullanıcıya
    GÖSTERİLMEZ: ne anlaşılır ne de eyleme dönüşür. Sınıf bilinmiyorsa en genel
    ama yine Türkçe olan mesaja düşülür."""
    return {
        voice_svc.HATA_SLOT_DOLU: SES_KAPASITE_MESAJ,
        voice_svc.HATA_SES_GECERSIZ: SES_422_MESAJ,
        voice_svc.HATA_YETKI: SES_SERVIS_MESAJ,
    }.get(r.get("reason"), SES_SERVIS_MESAJ)


def _slot_kurtar(db: Session, user: User) -> dict:
    """Hesap genelindeki klon slotlarından KULLANILMAYANLARI geri al.

    Döner: {"serbest": <açılan slot>, "asama": "bayat"|"kendi"|None}.
    Aşama çağırana lazım: "kendi" ise kullanıcının KENDİ sesi feda edilmiştir
    ve klonlama yine tutmazsa aylık hakkının iadesi gerekir.

    İki aşama, bu sırayla — çalışan bir sese mümkün olduğunca dokunmadan yer
    açmak için:

      1. BAYAT sesler: DB'de yalnız 'replaced' satırlarla anılanlar. Bunlar zaten
         silinmiş SAYILIYOR; temizlik bir noktada başarısız olmuş demektir
         (bkz. _eski_sesleri_temizle). Kimsenin kaybı yok.
      2. Yeterli gelmezse KULLANICININ KENDİ önceki sesleri. Klonlama başarılı
         olsaydı zaten silineceklerdi; sırayı öne almak "önce sil sonra klonla"
         riskini doğurur — tıkanmışken tek çıkış yolu olduğu için göze alınıyor.

    ASLA DOKUNULMAYANLAR: başka kullanıcıların geçerli sesleri ve
    ELEVENLABS_VOICE_ID (uygulamanın anlatıcı sesi — DB'de satırı YOKTUR, sahipsiz
    sanılıp silinirse tüm masal seslendirmesi çöker).

    DB'de HİÇ anılmayan sesler (hesapta elle açılmış klonlar) OTOMATİK SİLİNMEZ:
    sahibi bilinmeyen biyometrik veriyi kendi başımıza yok etmeyiz. Yalnız uyarı
    loglanır, temizliği scripts/voice_slot_bakim.py ile İNSAN yapar."""
    liste = voice_svc.list_cloned_voices()
    if not liste.get("ok"):
        logger.warning("Slot kurtarma: ElevenLabs ses listesi alınamadı (%s)",
                       liste.get("error"))
        return {"serbest": 0, "asama": None}
    hesaptaki = {v["voice_id"] for v in liste["voices"]}

    def _idler(sorgu):
        return {vid for (vid,) in sorgu.all() if vid}

    aktif = _idler(db.query(VoiceProfile.elevenlabs_voice_id)
                   .filter(VoiceProfile.status != "replaced",
                           VoiceProfile.elevenlabs_voice_id.isnot(None)))
    bayat = _idler(db.query(VoiceProfile.elevenlabs_voice_id)
                   .filter(VoiceProfile.status == "replaced",
                           VoiceProfile.elevenlabs_voice_id.isnot(None))) - aktif
    kendi = _idler(db.query(VoiceProfile.elevenlabs_voice_id)
                   .filter(VoiceProfile.user_id == user.id,
                           VoiceProfile.elevenlabs_voice_id.isnot(None)))
    anlatici = (os.getenv("ELEVENLABS_VOICE_ID") or "").strip()
    korunan = {anlatici} if anlatici else set()

    sahipsiz = hesaptaki - aktif - bayat - korunan
    if sahipsiz:
        logger.warning("Slot kurtarma: DB'de karşılığı OLMAYAN %d klon sesi var "
                       "(%s) — otomatik silinmedi, elle gözden geçirin "
                       "(scripts/voice_slot_bakim.py)",
                       len(sahipsiz), ", ".join(sorted(sahipsiz)))

    serbest = 0
    kullanilan_asama = None
    for asama, adaylar in (("bayat", (bayat & hesaptaki) - korunan),
                           ("kendi", (kendi & hesaptaki) - korunan)):
        if serbest:                      # bir slot yeter: fazlasını silme
            break
        for vid in sorted(adaylar):
            sonuc = voice_svc.delete_voice(vid)
            if not sonuc.get("ok"):
                logger.warning("Slot kurtarma (%s) SİLEMEDİ voice_id=%s: %s",
                               asama, vid, sonuc.get("error"))
                continue
            serbest += 1
            kullanilan_asama = asama
            # Satır 'replaced' olmalı: ses artık ElevenLabs'te YOK. Aksi halde
            # /voice-status silinmiş bir voiceId dönüp /generate'i 410 yerine
            # anlamsız bir upstream hatasına sürüklerdi.
            for p in (db.query(VoiceProfile)
                      .filter(VoiceProfile.elevenlabs_voice_id == vid).all()):
                p.status = "replaced"
            logger.info("Slot kurtarma (%s): voice_id=%s silindi", asama, vid)
    if serbest:
        db.commit()
    return {"serbest": serbest, "asama": kullanilan_asama}


def _klon_hakkini_iade_et(db: Session, user: User) -> None:
    """Kullanıcının aylık klonlama hakkını ŞİMDİ kullanılabilir yap.

    last_cloned_at'i tam 30 gün geriye çeker; _klon_durumu bunu "süre doldu"
    olarak okur. NULL yapmak İŞE YARAMAZ: _son_klonlama NULL'da created_at'e
    düşer ve bekleme aynen sürer."""
    geri = datetime.now(timezone.utc) - timedelta(days=CLONE_COOLDOWN_DAYS)
    n = 0
    for p in (db.query(VoiceProfile)
              .filter(VoiceProfile.user_id == user.id).all()):
        p.last_cloned_at = geri
        n += 1
    if n:
        db.commit()
        logger.info("Klonlama hakkı iade edildi (kendi sesi feda edildi ama "
                    "klonlama tutmadı): user=%s profil=%d", user.id, n)
