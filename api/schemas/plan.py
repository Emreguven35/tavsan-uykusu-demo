"""Sleep plan şemaları — /plans/generate (Claude + parameter_engine → JSONB)."""
import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

# profile_overrides İZİN LİSTESİ (2026-10-07). Yalnız motorun okuduğu anahtarlar
# kabul edilir; kalanı SESSİZCE atılır (eski istemci 422 almasın). Bu sözlük
# yapay zekâ istemine gittiği için kimlik alanları (bebek_ad, dogum_tarihi)
# BİLİNÇLİ OLARAK listede yok — takma ad / yaş korumasını delmesinler. kilo_durumu
# da yok: kilo hiçbir motor girdisine bağlanmaz (karar 2026-10-07).
OVERRIDE_METIN = {
    "beslenme", "destek", "oda", "dayanma_siniri", "deneyim", "gece_uyanma",
    "karartma_perdesi", "oda_sicakligi", "emzik", "son_beslenme_zaman",
    "beyaz_gurultu", "yaklasim_tercihi", "mizac",
}
OVERRIDE_SAYI = {"ogle_yatis_dk", "tek_ogun_uyku_dk", "uyaniklik_penceresi_dk"}
OVERRIDE_BOOL = {"tek_uyku"}
OVERRIDE_SAGLIK = "saglik_problemi"
OVERRIDE_METIN_MAKS = 200
OVERRIDE_SAGLIK_MAKS = 500


def override_suz(ham: dict | None) -> dict | None:
    """İzinli anahtarlar, tip ve uzunluk sınırıyla; kalanı atılır."""
    if not isinstance(ham, dict):
        return None
    out: dict[str, Any] = {}
    for k, v in ham.items():
        if k in OVERRIDE_METIN or k == OVERRIDE_SAGLIK:
            if isinstance(v, bool) or not isinstance(v, (str, int, float)):
                continue
            maks = OVERRIDE_SAGLIK_MAKS if k == OVERRIDE_SAGLIK else OVERRIDE_METIN_MAKS
            out[k] = str(v).strip()[:maks]
        elif k in OVERRIDE_SAYI:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = v
        elif k in OVERRIDE_BOOL:
            if isinstance(v, bool):
                out[k] = v
    return out or None


class PlanGenerateReq(BaseModel):
    baby_id: uuid.UUID
    # Bebekte saklanmayan ama motorun kullanabileceği ek profil alanları (opsiyonel).
    dogum_haftasi: int | None = Field(default=None, ge=24, le=42)
    profile_overrides: dict[str, Any] | None = None

    @field_validator("profile_overrides", mode="after")
    @classmethod
    def _izinli_anahtarlar(cls, v):
        return override_suz(v)


class PlanResp(BaseModel):
    id: uuid.UUID
    baby_id: uuid.UUID
    plan_date: date
    # {markdown, bucket, yas, plan_secimi, uygun_mu, schedule, adapted, ...}
    content: dict[str, Any]
    created_at: datetime
    # B4 — kilit: premium değilse eğitim programı (days + metin) çıkarılır.
    locked: list[str] = []
    premium_required: bool = False

    model_config = {"from_attributes": True}


class PlanJobResp(BaseModel):
    """POST /plans/generate (async) yanıtı — üretim arka planda başlatıldı.

    İstemci job_id ile GET /plans/generate/{job_id}'yi yoklar (polling)."""
    job_id: str
    status: str                  # processing
    # Faz O2: 0 = üretim başladı; >0 = havuz dolu, önünde kaç iş var.
    queue_position: int = 0


class PlanJobStatusResp(BaseModel):
    """GET /plans/generate/{job_id} yanıtı. status='done' ise plan doludur."""
    job_id: str
    status: str                  # processing | done | failed
    # Faz O2: kuyrukta bekleyen iş sayısı (0 = sırası geldi / bitti). İstemci
    # bunu "sıradasınız, N kişi önünüzde" diye gösterebilir.
    queue_position: int = 0
    plan: PlanResp | None = None
    error: str | None = None


class RegresyonCevapReq(BaseModel):
    """POST /plans/regresyon-cevap gövdesi — annenin tek soruya cevabı.

    İlayda (S9): regresyon şüphesinde ÖNCE "çocuk 20 dakika beklerken kendi
    uykuya dönüyor mu?" sorulur. Cevap akışı belirler; bebek üzerinde saklanır
    ki kart her açılışta yeniden sorulmasın."""
    kendi_donuyor: bool


class RegresyonCevapResp(BaseModel):
    """Cevap kaydedildikten SONRAKİ kart durumu — mobil aynı yanıttan okur.

    regresyon_karti None ise kart kapanmıştır ("evet" cevabı, 7 gün sessizlik).
    "hayır" cevabında kart `devam_45` ya da `tibbi_yonlendirme` olarak döner."""
    baby_id: uuid.UUID
    kendi_donuyor: bool
    cevap_at: datetime
    regresyon_karti: dict[str, Any] | None = None
    egitim_baslangic_gunu: int | None = None
    kirkbes_gun_doldu: bool = False


class PlanAdaptResp(BaseModel):
    """POST /plans/adapt yanıtı — kaydedilen plan + gün içi hesaplama kararı.

    adjusted: bugünün çizelgesi değişmez ŞABLONDAN farklı mı (gerçek kayıtlara
      göre yeniden hesaplandı mı).
    regenerate_required: yaş bandı ihlali (bant atlama) nedeniyle plan TAM
      YENİDEN ÜRETİLDİ.
    regression_detected: İlayda protokolü — eğitim bitiminden ≥13 gün sonra son 3
      gecenin ≥2'sinde gece uyanması (kendine dalamama) görüldü.
    regresyon_karti: v1.4 — ÜÇ KADEMELİ akış. `restart_program_suggested`
      ("Programı baştan başlatalım mı?") KALDIRILDI; İlayda o yolu reddetti,
      45 gün dolana kadar eğitime DEVAM ediliyor.
        {"tip": "kendi_donuyor_mu" | "devam_45" | "tibbi_yonlendirme",
         "metin": str, "egitim_gunu": int|None, "kirkbes_gun_doldu": bool}
      Anne cevabı POST /plans/regresyon-cevap ile gelir.
    egitim_baslangic_gunu: eğitimin kaçıncı günü (1'den başlar) — None ise
      eğitim başlamamış.
    kirkbes_gun_doldu: 45 günlük "devam et" penceresi doldu mu.

    shift_minutes: KULLANIMDAN KALDIRILDI (v2/K1). Çizelgenin tamamını sabit bir
      dakika kadar kaydırma kavramı yok; daima 0 döner. Eski mobil sürümler
      kırılmasın diye alan korunuyor — yeni istemciler `adaptation` gövdesini
      (varsayilan_bloklar, yeniden_hesaplanan_bloklar, uyarilar…) okumalıdır."""
    plan: PlanResp
    adjusted: bool
    shift_minutes: int = 0
    regenerate_required: bool
    regression_detected: bool
    regresyon_karti: dict[str, Any] | None = None
    egitim_baslangic_gunu: int | None = None
    kirkbes_gun_doldu: bool = False
    reasons: list[str]
    # v2 — gün içi hesaplamanın tam izi (K4 şeması). Yenidoğan rehberinde None.
    adaptation: dict[str, Any] | None = None
