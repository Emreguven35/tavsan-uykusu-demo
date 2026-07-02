"""
Canlı ElevenLabs TTS denemesi (BİR KEZ). Kısa bir cevabı seslendirir, MP3'ü
data/audio_cache/ altına yazar ve /audio üzerinden servis edilebildiğini doğrular.
Karakter → tahmini saniye → tahmini $ maliyeti raporlar.

Anahtar (.env: ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID) YOKSA canlı çağrı atlanır;
yalnızca projeksiyon (karakter/saniye/maliyet) yazılır.

Çalıştır: python test_tts_canli.py
"""
import os
import sys
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from api import tts  # noqa: E402

# Türkçe konuşma hızı ~ 15 karakter/sn (yaklaşık; 150 kelime/dk).
CHARS_PER_SEC = 15.0

ORNEK = (
    "Gece uyanmalarında bebeğinizi hemen kucağınıza almak yerine kısa aralıklarla "
    "bekleyerek kendi kendine uykuya dönmesine fırsat verin. Ağlaması yoğunlaşırsa "
    "30 saniye, 1 dakika, 1,5 dakika şeklinde kademeli olarak yanına gidebilirsiniz."
)


def main():
    n = len(ORNEK)
    sn = n / CHARS_PER_SEC
    usd = tts.tts_cost(ORNEK)
    print("=" * 66)
    print("CANLI TTS DENEMESİ")
    print("=" * 66)
    print(f"Metin karakter sayısı : {n}")
    print(f"Tahmini ses süresi    : ~{sn:.1f} sn  (~{CHARS_PER_SEC:.0f} krktr/sn)")
    print(f"Tahmini maliyet       : ${usd:.5f}  (${tts.ELEVENLABS_USD_PER_CHAR}/krktr)")
    print("-" * 66)

    if not (os.getenv("ELEVENLABS_API_KEY") and os.getenv("ELEVENLABS_VOICE_ID")):
        print("CANLI ÇAĞRI ATLANDI: ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID .env'de yok.")
        print("Anahtarı ekleyip tekrar çalıştırın; yukarıdaki projeksiyon geçerlidir.")
        return 0

    anahtar = hashlib.sha256(("canli_test||" + ORNEK).encode()).hexdigest()
    r = tts.ensure_audio(anahtar, ORNEK)
    path = tts.audio_path(anahtar)
    ok = path.exists() and path.stat().st_size > 0
    print(f"TTS çağrıldı          : {r['tts_called']}")
    print(f"MP3 oluştu            : {ok}  ({path})")
    if ok:
        print(f"MP3 boyutu            : {path.stat().st_size} bayt")
        print(f"Servis URL            : {r['ses_url']}  (GET /audio ile erişilir)")
        print(f"Gerçekleşen maliyet   : ${r['tts_usd']:.5f}")
    print("=" * 66)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
