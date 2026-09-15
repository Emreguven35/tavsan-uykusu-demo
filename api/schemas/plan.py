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


class PlanAdaptResp(BaseModel):
    """POST /plans/adapt yanıtı — kaydedilen plan + gün içi hesaplama kararı.

    adjusted: bugünün çizelgesi değişmez ŞABLONDAN farklı mı (gerçek kayıtlara
      göre yeniden hesaplandı mı).
    regenerate_required: yaş bandı ihlali (bant atlama) nedeniyle plan TAM
      YENİDEN ÜRETİLDİ.
    regression_detected: İlayda protokolü — eğitim bitiminden ≥13 gün sonra son 3
      gecenin ≥2'sinde 20dk+ süren gece uyanması (kendine dalamama) görüldü.
    restart_program_suggested: kullanıcıya "Programı baştan başlatalım mı?" kartı
      gösterilir. OTOMATİK HİÇBİR ŞEY ÜRETİLMEZ — onay gelirse mobil
      POST /plans/generate çağırır ve training_started_at'i bugüne PATCH'ler.

    shift_minutes: KULLANIMDAN KALDIRILDI (v2/K1). Çizelgenin tamamını sabit bir
      dakika kadar kaydırma kavramı yok; daima 0 döner. Eski mobil sürümler
      kırılmasın diye alan korunuyor — yeni istemciler `adaptation` gövdesini
      (varsayilan_bloklar, yeniden_hesaplanan_bloklar, uyarilar…) okumalıdır."""
    plan: PlanResp
    adjusted: bool
    shift_minutes: int = 0
    regenerate_required: bool
    regression_detected: bool
    restart_program_suggested: bool
    reasons: list[str]
    # v2 — gün içi hesaplamanın tam izi (K4 şeması). Yenidoğan rehberinde None.
    adaptation: dict[str, Any] | None = None
