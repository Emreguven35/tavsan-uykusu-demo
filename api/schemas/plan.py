"""Sleep plan şemaları — /plans/generate (Claude + parameter_engine → JSONB)."""
import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class PlanGenerateReq(BaseModel):
    baby_id: uuid.UUID
    # Bebekte saklanmayan ama motorun kullanabileceği ek profil alanları (opsiyonel).
    dogum_haftasi: int | None = Field(default=None, ge=24, le=42)
    profile_overrides: dict[str, Any] | None = None


class PlanResp(BaseModel):
    id: uuid.UUID
    baby_id: uuid.UUID
    plan_date: date
    # {markdown, bucket, yas, plan_secimi, uygun_mu, schedule, adapted, ...}
    content: dict[str, Any]
    created_at: datetime

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
