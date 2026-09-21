"""
voice router — /api/v1/voice/*

- POST /clone: multipart ses (30sn) → ElevenLabs voice clone → {voiceId, sampleUrl};
  voice_profiles'a kaydeder + kısa bir örnek seslendirir (sampleUrl).
- GET /voice-status: kullanıcının klon durumu.
- GET /stories: masal/ninni kataloğu (5 masal + 3 ninni).
- POST /generate: {voiceId, text|storyId, profile?} → ElevenLabs flash v2.5 + TTS
  metin işleme → {audio_url, cached, profile}. Ses data/audio_cache'e yazılır,
  /audio ile sunulur. Profil varsayılanı 'masal' (yavaş, duraklamalı anlatım);
  'sohbet' normal hızdır (bkz. tts.SES_PROFILLERI).

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
from api.schemas.voice import (
    StoriesResp, VoiceCloneResp, VoiceGenerateReq, VoiceGenerateResp, VoiceStatusResp,
)
from api.services import usage as usage_svc
from api.services import voice as voice_svc

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
    if not r.get("ok") and r.get("reason") == voice_svc.HATA_SLOT_DOLU:
        if _slot_kurtar(db, user) > 0:
            r = voice_svc.clone_voice(name, data, dosya_adi, icerik_turu)

    if not r.get("ok"):
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

    # Kısa örnek seslendir (klonun çalıştığının kanıtı + mobil önizleme).
    sample = tts.voice_audio(voice_id, SAMPLE_TEXT, user_id=user.id)
    sample_url = sample.get("audio_url")

    profile = VoiceProfile(user_id=user.id, elevenlabs_voice_id=voice_id,
                           sample_url=sample_url, status="ready",
                           last_cloned_at=datetime.now(timezone.utc))
    db.add(profile)
    db.commit()
    logger.info("Voice clone tamam: user=%s voice_id=%s", user.id, voice_id)

    # ESKİ SESİ TEMİZLE — yeni klon KAYDEDİLDİKTEN sonra. Sıra önemli: silme
    # önce yapılsaydı ve klonlama sonradan patlasaydı kullanıcı sessiz kalırdı.
    # Silme BEST-EFFORT: başarısız olursa yeni ses yine geçerli (bkz. delete_voice).
    _eski_sesleri_temizle(db, user, yeni_voice_id=voice_id)
    return VoiceCloneResp(voiceId=voice_id, sampleUrl=sample_url)


def _eski_sesleri_temizle(db: Session, user: User, yeni_voice_id: str) -> None:
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
            logger.info("Eski klon sesi silindi: user=%s voice_id=%s",
                        user.id, eski.elevenlabs_voice_id)
        else:
            logger.warning("Eski klon sesi SİLİNEMEDİ (user=%s voice_id=%s): %s "
                           "— slot/ücret birikebilir, elle temizlik gerekebilir",
                           user.id, eski.elevenlabs_voice_id, sonuc.get("error"))
    if eskiler:
        db.commit()


@router.get("/voice-status", response_model=VoiceStatusResp)
def voice_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    profile = _son_profil(db, user)
    # Klonlama hakkı POST /clone ile AYNI fonksiyondan hesaplanıyor: mobilin
    # gösterdiği tarih ile sunucunun uyguladığı sınır ayrışamaz.
    durum = _klon_durumu(profile)
    if profile is None:
        return VoiceStatusResp(status="none", can_clone=True)
    return VoiceStatusResp(
        status=profile.status, voiceId=profile.elevenlabs_voice_id,
        sampleUrl=profile.sample_url, created_at=profile.created_at,
        last_cloned_at=_son_klonlama(profile),
        can_clone=durum["can_clone"],
        next_clone_available_at=durum["next_clone_available_at"],
        retry_after_days=durum["retry_after_days"])


@router.get("/stories", response_model=StoriesResp)
def stories(user: User = Depends(get_current_user)):
    """Masal/ninni kataloğu. Metinler /generate'e storyId ile verilir (yanıtta değil)."""
    cat = voice_svc.load_stories()
    return StoriesResp(masallar=cat.get("masallar", []),
                       ninniler=cat.get("ninniler", []))


@router.post("/generate", response_model=VoiceGenerateResp)
def generate(req: VoiceGenerateReq, db: Session = Depends(get_db),
             user: User = Depends(require_premium)):
    # Faz G3: voiceId SAHİPLİK doğrulaması. Önceden gövdedeki voiceId doğrudan
    # ElevenLabs'e gidiyordu; başkasının voice_id'sini bilen onun (klonlu, biyometrik)
    # sesiyle üretim yaptırıp kredi harcatabiliyordu. Artık voiceId, çağıranın kendi
    # voice_profiles kaydıyla eşleşmeli; yoksa 403.
    sahip = (db.query(VoiceProfile)
             .filter(VoiceProfile.user_id == user.id,
                     VoiceProfile.elevenlabs_voice_id == req.voiceId)
             .first())
    if sahip is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Bu ses profili size ait değil")
    # Yenisi alınmış ses ARTIK ELEVENLABS'TE YOK (eski klon siliniyor). Eski
    # voiceId'yi elinde tutan istemci buraya gelirse anlamsız bir upstream
    # hatası yerine net bir yanıt alsın.
    if sahip.status == "replaced":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=("Bu ses kaydı yenilendiği için artık kullanılamıyor. "
                    "Güncel ses kimliğini /voice/voice-status ile alın."))

    # storyId verildiyse katalogdan metni çöz; yoksa doğrudan text.
    if req.storyId:
        story = voice_svc.find_story(req.storyId)
        if story is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Masal/ninni bulunamadı")
        text = story["text"]
    else:
        text = req.text

    # Masal/ninni anlatımı varsayılan: yavaş, sakin, duraklamalı (tts.SES_PROFILLERI).
    # İstemci 'sohbet' göndererek normal hızı seçebilir (şema doğruluyor).
    profil = req.profile or tts.MASAL_PROFILI

    result = tts.voice_audio(req.voiceId, text, profil=profil, user_id=user.id)
    if result.get("audio_url") is None:
        # TTS anahtarı yok / upstream hata → ses üretilemedi.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=SES_SERVIS_MESAJ)
    logger.info("Voice generate: user=%s voice=%s profil=%s cached=%s",
                user.id, req.voiceId, profil, result["cached"])
    return VoiceGenerateResp(audio_url=result["audio_url"],
                             cached=result["cached"], profile=profil)


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


def _slot_kurtar(db: Session, user: User) -> int:
    """Hesap genelindeki klon slotlarından KULLANILMAYANLARI geri al.

    Döner: serbest bırakılan slot sayısı.

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
        return 0
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
            # Satır 'replaced' olmalı: ses artık ElevenLabs'te YOK. Aksi halde
            # /voice-status silinmiş bir voiceId dönüp /generate'i 410 yerine
            # anlamsız bir upstream hatasına sürüklerdi.
            for p in (db.query(VoiceProfile)
                      .filter(VoiceProfile.elevenlabs_voice_id == vid).all()):
                p.status = "replaced"
            logger.info("Slot kurtarma (%s): voice_id=%s silindi", asama, vid)
    if serbest:
        db.commit()
    return serbest
