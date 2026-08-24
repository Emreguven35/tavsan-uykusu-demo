"""
Geriye dönük maliyet TAHMİNİ — api_usage'ın olmadığı dönem için.

api_usage tablosu 2026-08-25'te açıldı. Ondan öncesi için gerçek `usage` verisi
YOK; bu betik mevcut sayaçlardan (chat_messages, sleep_plans, voice_profiles)
kabaca ne harcandığını çıkarır.

BU BİR TAHMİNDİR, FATURA DEĞİLDİR. Nedeni açıkça bilinsin:
  - Token sayısı karakterden türetiliyor (~4 karakter ≈ 1 Türkçe token).
  - Prompt cache indirimi HESABA KATILAMIYOR (o dönemin cache_read verisi yok),
    yani gerçek tutar buradan DÜŞÜK olabilir.
  - TTS'te hangi masalın kaç kez üretildiği kayıtlı değil; ses klonu olan
    kullanıcı başına varsayım kullanılıyor.
Kesin rakam Anthropic/ElevenLabs konsolundaki fatura; bu betik büyüklük mertebesi
verir ve bundan SONRASI için gerçek ölçüm api_usage'da tutulur.

Kullanım (üretim verisiyle):
    railway run python scripts/gecmis_maliyet_tahmini.py
Yerel:
    python scripts/gecmis_maliyet_tahmini.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sqlalchemy import func                                    # noqa: E402

from api.config import ANTHROPIC_FIYATLARI, ELEVENLABS_FIYATLARI  # noqa: E402
from api.db import SessionLocal                                # noqa: E402
from api.models import ApiUsage, ChatMessage, SleepPlan, VoiceProfile  # noqa: E402

KARAKTER_BASINA_TOKEN = 1 / 4.0        # Türkçe için kaba oran

CHAT_MODEL = "claude-haiku-4-5"
PLAN_MODEL = "claude-sonnet-4-6"
TTS_MODEL = "eleven_flash_v2_5"

# Sohbet girdisi = sistem prompt'u + retrieval bağlamı + soru.
# Bağlam: SEM_TOP_K(8) birim × ortalama birim uzunluğu (ölçüldü: ~714 karakter).
SISTEM_PROMPT_KRKTR = 6_142
BAGLAM_KRKTR = 8 * 714

# Plan girdisi: sistem + parametre bloğu (ölçülen tipik büyüklük).
PLAN_GIRDI_KRKTR = 12_000
# Ses klonu olan kullanıcı başına varsayım: klon örneği + 2 masal dinlemesi.
KLON_BASINA_TTS_KRKTR = 60 + 2 * 4_200


def usd(tokenler: float, model: str, cikti: bool = False) -> float:
    f = ANTHROPIC_FIYATLARI[model]
    return tokenler / 1_000_000 * (f["out"] if cikti else f["in"])


def main() -> int:
    db = SessionLocal()
    try:
        # --- Sohbet -------------------------------------------------------
        soru_adet, soru_krktr = (db.query(
            func.count(ChatMessage.id),
            func.coalesce(func.sum(func.length(ChatMessage.content)), 0))
            .filter(ChatMessage.role == "user").one())
        cevap_adet, cevap_krktr = (db.query(
            func.count(ChatMessage.id),
            func.coalesce(func.sum(func.length(ChatMessage.content)), 0))
            .filter(ChatMessage.role == "assistant").one())
        cache_hit = (db.query(func.count(ChatMessage.id))
                     .filter(ChatMessage.role == "assistant",
                             ChatMessage.cached.is_(True)).scalar() or 0)
        llm_cevap = max(int(cevap_adet or 0) - int(cache_hit), 0)

        # Cache HIT'te LLM'e HİÇ gidilmedi → yalnız llm_cevap kadar çağrı sayılır.
        chat_girdi_tok = (llm_cevap * (SISTEM_PROMPT_KRKTR + BAGLAM_KRKTR)
                          + int(soru_krktr or 0)) * KARAKTER_BASINA_TOKEN
        chat_cikti_tok = int(cevap_krktr or 0) * KARAKTER_BASINA_TOKEN * (
            llm_cevap / cevap_adet if cevap_adet else 0)
        chat_usd = usd(chat_girdi_tok, CHAT_MODEL) + usd(chat_cikti_tok, CHAT_MODEL, True)

        # --- Planlar ------------------------------------------------------
        plan_adet = db.query(func.count(SleepPlan.id)).scalar() or 0
        # Plan markdown'ı content JSON'unun içinde; uzunluğu lehçeye bağlı
        # olmadan almak için satırları çekip Python'da ölçüyoruz (adet küçük).
        plan_cikti_krktr = 0
        for (icerik,) in db.query(SleepPlan.content).all():
            if isinstance(icerik, dict):
                plan_cikti_krktr += len(str(icerik.get("markdown") or ""))
        plan_girdi_tok = plan_adet * PLAN_GIRDI_KRKTR * KARAKTER_BASINA_TOKEN
        plan_cikti_tok = plan_cikti_krktr * KARAKTER_BASINA_TOKEN
        plan_usd = usd(plan_girdi_tok, PLAN_MODEL) + usd(plan_cikti_tok, PLAN_MODEL, True)

        # --- Ses ----------------------------------------------------------
        klon_adet = db.query(func.count(VoiceProfile.id)).scalar() or 0
        tts_krktr = klon_adet * KLON_BASINA_TTS_KRKTR
        tts_usd = tts_krktr * ELEVENLABS_FIYATLARI[TTS_MODEL]

        # --- api_usage'da gerçek veri var mı? ------------------------------
        gercek_adet = db.query(func.count(ApiUsage.id)).scalar() or 0
        gercek_usd = (db.query(func.coalesce(
            func.sum(ApiUsage.estimated_cost_usd), 0.0)).scalar() or 0.0)

        toplam = chat_usd + plan_usd + tts_usd
        print("=" * 74)
        print("GERİYE DÖNÜK MALİYET TAHMİNİ (api_usage öncesi dönem)")
        print("=" * 74)
        print(f"  Sohbet     : {soru_adet:>6} soru, {cevap_adet:>6} cevap "
              f"({cache_hit} cache HIT → {llm_cevap} gerçek LLM çağrısı)")
        print(f"               ~{chat_girdi_tok/1000:>8.0f}K girdi + "
              f"{chat_cikti_tok/1000:.0f}K çıktı token → ${chat_usd:.2f}")
        print(f"  Planlar    : {plan_adet:>6} plan, "
              f"{plan_cikti_krktr/1000:.0f}K karakter çıktı → ${plan_usd:.2f}")
        print(f"  Ses (TTS)  : {klon_adet:>6} klon profili, "
              f"~{tts_krktr/1000:.0f}K karakter → ${tts_usd:.2f}")
        print("-" * 74)
        print(f"  TAHMİNİ TOPLAM (bugüne kadar): ${toplam:.2f}")
        print()
        print(f"  api_usage'daki GERÇEK kayıt : {gercek_adet} satır, ${gercek_usd:.4f}")
        print()
        print("  NOT: Tahmin karakter→token oranına dayanır ve prompt cache")
        print("       indirimini GÖREMEZ (o dönemin cache verisi yok), yani")
        print("       gerçek tutar bundan DÜŞÜK olabilir. Kesin rakam faturadadır.")
        print("=" * 74)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
