"""
5 hipotetik bebek profili için Parameter Engine + Plan Generator test çalıştırıcı.

Çalıştırma: `python tests/test_scenarios.py` (tavsan_demo/ kök dizininden)
Çıktı: tests/test_results.json
"""
import sys
import json
from datetime import date, timedelta
from pathlib import Path

# engine modülünü import edebilmek için
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from engine.parameter_engine import parametre_uret  # noqa: E402
from engine.plan_generator import plan_uret  # noqa: E402
from engine.chatbot import retrieve, cevapla, init_index  # noqa: E402


def _dt(months_ago: int) -> str:
    """N ay önceki tarih (ISO format)."""
    today = date.today()
    days = int(months_ago * 30.44)
    return (today - timedelta(days=days)).isoformat()


TEST_PROFILES = [
    {
        "ad": "Test 1 — 6 aylık emziren bebek, aynı odada",
        "profile": {
            "bebek_ad": "Test Bebek 1",
            "anne_ad": "Test Anne",
            "dogum_tarihi": _dt(6),
            "dogum_haftasi": 40,
            "egitim_tarihi": date.today().isoformat(),
            "saglik_problemi": "yok",
            "destek": "Memeyle (emerek)",
            "oda": "Anne-baba ile aynı odada, kendi beşiğinde",
            "gunduz_uyku_sayisi": "3",
            "gunduz_toplam": "3 saat",
            "gece_uyanma": "3-4 kez",
            "karartma_perdesi": "Hayır",
            "beyaz_gurultu": "Hayır",
            "oda_sicakligi": "22°C",
            "oda_sicakligi_num": 22.0,
            "beslenme": "Sadece anne sütü",
            "son_beslenme_zaman": "Uykudan hemen önce (memeyle/mamayla uyuyor)",
            "emzik": "Hayır, hiç kullanmıyor",
            "motor": ["Sırtüstü → yan dönüyor", "Yüzüstü kafasını kaldırıyor"],
            "motor_str": "Sırtüstü → yan dönüyor, Yüzüstü kafasını kaldırıyor",
            "dis": "Hiç dişi yok",
            "mizac": "Sakin / uyumlu",
            "onceki_denedi": "Hayır, ilk kez deneyeceğiz",
            "yaklasim_tercihi": "5 günlük standart plan (daha hızlı, daha çok ağlama olabilir)",
            "dayanma_siniri": "20-30 dakika",
            "en_zor_zaman": "Gece uyanmaları",
            "beklenti": "Gece kesintisiz 6+ saat uyku",
        },
        "beklenen": "5 günlük standart plan. Ön hazırlık: emerek uyuma → sallanma, paravan, karartma perdesi.",
    },
    {
        "ad": "Test 2 — 8 aylık, sık gece uyanma, ek gıdaya başlamış",
        "profile": {
            "bebek_ad": "Test Bebek 2",
            "anne_ad": "Test Anne 2",
            "dogum_tarihi": _dt(8),
            "dogum_haftasi": 40,
            "egitim_tarihi": date.today().isoformat(),
            "saglik_problemi": "yok",
            "destek": "Pat-pat / pış-pış ile",
            "oda": "Kendi odasında, kendi beşiğinde",
            "gunduz_uyku_sayisi": "2",
            "gunduz_toplam": "2.5 saat",
            "gece_uyanma": "5-6 kez",
            "karartma_perdesi": "Evet",
            "beyaz_gurultu": "Evet, sürekli",
            "oda_sicakligi": "21°C",
            "oda_sicakligi_num": 21.0,
            "beslenme": "Anne sütü + ek gıda",
            "son_beslenme_zaman": "45-60 dakika önce",
            "emzik": "Hayır, hiç kullanmıyor",
            "motor": ["Tek başına oturuyor", "Emekliyor"],
            "motor_str": "Tek başına oturuyor, Emekliyor",
            "dis": "1-2 dişi var, şu an çıkarıyor (ağrılı)",
            "mizac": "Huysuz / sürekli ağlayan",
            "onceki_denedi": "Evet, denedim ama başarısız oldu",
            "onceki_yontemler": "Ferber, yatır-bırak",
            "yaklasim_tercihi": "İlayda Hanım'ın önereceği plan (bebeğe göre)",
            "dayanma_siniri": "10-20 dakika",
            "en_zor_zaman": "Gece uyanmaları",
            "beklenti": "Düzeni yeniden kurmak",
        },
        "beklenen": "13 günlük dirençli plan (huysuz mizaç + düşük dayanma). 4+ uyanma → emerek değiştirme hatırlatması.",
    },
    {
        "ad": "Test 3 — 14 aylık, 2 nap'tan tek uyku geçişi",
        "profile": {
            "bebek_ad": "Test Bebek 3",
            "anne_ad": "Test Anne 3",
            "dogum_tarihi": _dt(14),
            "dogum_haftasi": 40,
            "egitim_tarihi": date.today().isoformat(),
            "saglik_problemi": "yok",
            "destek": "Tamamen kendi başına",
            "oda": "Kendi odasında, kendi beşiğinde",
            "gunduz_uyku_sayisi": "2",
            "gunduz_toplam": "2 saat",
            "gece_uyanma": "1 kez",
            "karartma_perdesi": "Evet",
            "beyaz_gurultu": "Hayır",
            "oda_sicakligi": "20°C",
            "oda_sicakligi_num": 20.0,
            "beslenme": "Mama + ek gıda",
            "son_beslenme_zaman": "1 saatten fazla önce",
            "emzik": "Hayır, hiç kullanmıyor",
            "motor": ["Bağımsız yürüyor"],
            "motor_str": "Bağımsız yürüyor",
            "dis": "Birkaç dişi var, şu an çıkarmıyor",
            "mizac": "Aktif / enerjik ama uyumlu",
            "onceki_denedi": "Hayır, ilk kez deneyeceğiz",
            "yaklasim_tercihi": "5 günlük standart plan (daha hızlı, daha çok ağlama olabilir)",
            "dayanma_siniri": "30-45 dakika",
            "en_zor_zaman": "Gündüz uykuları (kısa uyku, uzatma)",
            "beklenti": "Tek uykuya geçiş zamanlaması",
        },
        "beklenen": "5 günlük plan. Tek uyku geçiş yaşına gelmiş (15-18 ay önerisi, henüz erken).",
    },
    {
        "ad": "Test 4 — 3 aylık, eğitim alamayacak yaşta",
        "profile": {
            "bebek_ad": "Test Bebek 4",
            "anne_ad": "Test Anne 4",
            "dogum_tarihi": _dt(3),
            "dogum_haftasi": 40,
            "egitim_tarihi": date.today().isoformat(),
            "saglik_problemi": "yok",
            "destek": "Memeyle (emerek)",
            "oda": "Anne-baba ile aynı odada, kendi beşiğinde",
            "gunduz_uyku_sayisi": "4",
            "gunduz_toplam": "4 saat",
            "gece_uyanma": "3-4 kez",
            "karartma_perdesi": "Kısmen (loş)",
            "beyaz_gurultu": "Hayır",
            "oda_sicakligi": "22°C",
            "oda_sicakligi_num": 22.0,
            "beslenme": "Sadece anne sütü",
            "son_beslenme_zaman": "Uykudan hemen önce (memeyle/mamayla uyuyor)",
            "emzik": "Hayır, hiç kullanmıyor",
            "motor": ["Sırtüstü → yan dönüyor"],
            "motor_str": "Sırtüstü → yan dönüyor",
            "dis": "Hiç dişi yok",
            "mizac": "Hassas / kolay üzülen",
            "onceki_denedi": "Hayır, ilk kez deneyeceğiz",
            "yaklasim_tercihi": "İlayda Hanım'ın önereceği plan (bebeğe göre)",
            "dayanma_siniri": "10 dakikadan az (çok zorlanırım)",
            "en_zor_zaman": "Gece uyanmaları",
            "beklenti": "Gece düzenli uyku",
        },
        "beklenen": "Eğitim UYGUN DEĞİL. 5 ay alt sınır. Sadece saat planlaması yapılabilir.",
    },
    {
        "ad": "Test 5 — Prematüre düzeltme: 7 ay (36 hf doğum → düzeltilmiş 6 ay)",
        "profile": {
            "bebek_ad": "Test Bebek 5",
            "anne_ad": "Test Anne 5",
            "dogum_tarihi": _dt(7),
            "dogum_haftasi": 36,
            "egitim_tarihi": date.today().isoformat(),
            "saglik_problemi": "yok",
            "destek": "Sallanarak (kucakta veya beşikte)",
            "oda": "Kendi odasında, kendi beşiğinde",
            "gunduz_uyku_sayisi": "3",
            "gunduz_toplam": "3 saat",
            "gece_uyanma": "2 kez",
            "karartma_perdesi": "Evet",
            "beyaz_gurultu": "Sadece bazen",
            "oda_sicakligi": "22°C",
            "oda_sicakligi_num": 22.0,
            "beslenme": "Karışık (anne sütü + mama)",
            "son_beslenme_zaman": "30 dakika önce",
            "emzik": "Evet, uykuda kullanıyor (ağzına düşerse anne takıyor)",
            "motor": ["Sırtüstü → yan dönüyor", "Yüzüstü kafasını kaldırıyor"],
            "motor_str": "Sırtüstü → yan dönüyor, Yüzüstü kafasını kaldırıyor",
            "dis": "Hiç dişi yok",
            "mizac": "Sakin / uyumlu",
            "onceki_denedi": "Hayır, ilk kez deneyeceğiz",
            "yaklasim_tercihi": "İlayda Hanım'ın önereceği plan (bebeğe göre)",
            "dayanma_siniri": "20-30 dakika",
            "en_zor_zaman": "Gece uyanmaları",
            "beklenti": "Sallanmadan uyuma",
        },
        "beklenen": "Prematüre düzeltme: 7-1 = ~6 ay. Eğitim UYGUN. Emzik 8 ay altı kuralı + sallanma hazırlığı uyarısı.",
    },
]


def run_parameter_engine_tests() -> list[dict]:
    """Sadece parameter engine'i test et (LLM yok). Her durumda çalışır."""
    results = []
    for test in TEST_PROFILES:
        try:
            param = parametre_uret(test["profile"])
            results.append({
                "test_adi": test["ad"],
                "basarili": True,
                "yas_gercek_ay": param["yas"]["gercek_ay"],
                "yas_duzeltilmis_ay": param["yas"]["duzeltilmis_ay"],
                "yas_bucket": param["bucket"],
                "uygun_mu": param["uygun_mu"],
                "uyari_sayisi": len(param["uyarilar"]),
                "uyarilar": param["uyarilar"],
                "on_hazirlik_sayisi": len(param["on_hazirlik"]),
                "on_hazirlik_basliklar": [h["konu"] for h in param["on_hazirlik"]],
                "plan_tipi": param["plan_secimi"]["tip"],
                "plan_gun_sayisi": param["plan_secimi"]["gunler"],
                "gece_beslenme_yas_grup": param["gece_beslenme"]["yas_grup"] if param["gece_beslenme"] else None,
                "beklenen": test["beklenen"],
            })
        except Exception as e:
            results.append({
                "test_adi": test["ad"],
                "basarili": False,
                "hata": str(e),
            })
    return results


def run_plan_generation_tests() -> list[dict]:
    """Plan generator'ı test et (API key varsa Claude, yoksa fallback)."""
    import os
    has_key = bool(os.getenv("ANTHROPIC_API_KEY"))
    results = []
    for test in TEST_PROFILES:
        try:
            param = parametre_uret(test["profile"])
            plan = plan_uret(param)
            results.append({
                "test_adi": test["ad"],
                "basarili": True,
                "plan_uretici": "claude_api" if has_key else "fallback_markdown",
                "plan_uzunluk_karakter": len(plan),
                "plan_uzunluk_kelime": len(plan.split()),
                "plan_ilk_500": plan[:500],
                "plan_son_500": plan[-500:] if len(plan) > 500 else "",
            })
        except Exception as e:
            results.append({
                "test_adi": test["ad"],
                "basarili": False,
                "hata": str(e),
            })
    return results


def run_chatbot_retrieve_tests() -> list[dict]:
    """Chatbot retrieval'ı test et (LLM çağrısı yok, sadece TF-IDF)."""
    sorular = [
        "Bebeğim gece 3 kez uyanıyor, ne yapmalıyım?",
        "Uyku eğitimine kaç ayda başlamalıyım?",
        "Eğitim sırasında bebek 1 saat ağlarsa ne yapacağım?",
        "Emzik kullanan bebekte eğitim olur mu?",
        "Yatak geçişi nasıl yapılır?",
    ]
    init_index()
    results = []
    for s in sorular:
        retrieved = retrieve(s, top_k=3)
        results.append({
            "soru": s,
            "bulunan_chunk_sayisi": len(retrieved),
            "ilk_chunk_kaynak": retrieved[0]["lesson_id"] if retrieved else None,
            "ilk_chunk_score": retrieved[0]["_score"] if retrieved else None,
            "ilk_chunk_ilk_200": retrieved[0]["text"][:200] if retrieved else None,
        })
    return results


def run_chatbot_full_tests() -> list[dict]:
    """Chatbot'u tam test et (API key varsa Claude)."""
    import os
    has_key = bool(os.getenv("ANTHROPIC_API_KEY"))
    sorular = [
        "Bebeğim 6 aylık, gece her 2 saatte bir uyanıyor. Ne yapmalıyım?",
        "Eğitim sırasında bebek histerik ağlamaya geçerse nasıl müdahale edeyim?",
    ]
    results = []
    for s in sorular:
        try:
            cevap = cevapla(s)
            results.append({
                "soru": s,
                "basarili": True,
                "cevap_uretici": "claude_api" if has_key else "fallback_snippet",
                "cevap_uzunluk": len(cevap),
                "cevap_ozet": cevap[:600] + ("..." if len(cevap) > 600 else ""),
            })
        except Exception as e:
            results.append({
                "soru": s,
                "basarili": False,
                "hata": str(e),
            })
    return results


def main():
    print("=" * 70)
    print("Tavşan Uykusu Premium Demo — Test Senaryoları")
    print("=" * 70)

    print("\n📊 1. Parameter Engine testleri (deterministik)...")
    param_results = run_parameter_engine_tests()
    basarili = sum(1 for r in param_results if r["basarili"])
    print(f"   ✅ {basarili}/{len(param_results)} test başarılı")

    print("\n📝 2. Plan Generator testleri...")
    plan_results = run_plan_generation_tests()
    basarili = sum(1 for r in plan_results if r["basarili"])
    print(f"   ✅ {basarili}/{len(plan_results)} plan üretildi")

    print("\n🔍 3. Chatbot retrieve testleri (TF-IDF)...")
    retrieve_results = run_chatbot_retrieve_tests()
    print(f"   ✅ {len(retrieve_results)} soru sorgulandı")

    print("\n💬 4. Chatbot tam testler...")
    chatbot_results = run_chatbot_full_tests()
    basarili = sum(1 for r in chatbot_results if r["basarili"])
    print(f"   ✅ {basarili}/{len(chatbot_results)} cevap üretildi")

    all_results = {
        "parameter_engine": param_results,
        "plan_generator": plan_results,
        "chatbot_retrieve": retrieve_results,
        "chatbot_full": chatbot_results,
        "ozet": {
            "parameter_engine_basarili": sum(1 for r in param_results if r["basarili"]),
            "parameter_engine_toplam": len(param_results),
            "plan_generator_basarili": sum(1 for r in plan_results if r["basarili"]),
            "plan_generator_toplam": len(plan_results),
            "chatbot_retrieve_toplam": len(retrieve_results),
            "chatbot_full_basarili": sum(1 for r in chatbot_results if r["basarili"]),
            "chatbot_full_toplam": len(chatbot_results),
        },
    }

    out_path = ROOT / "tests" / "test_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\n💾 Sonuçlar kaydedildi: {out_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
