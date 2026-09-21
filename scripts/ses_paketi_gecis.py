"""
Mevcut klon seslerini "üret ve bırak" modeline geçir — TEK SEFERLİK (v2.3).

v2.3'ten ÖNCE klonlanmış sesler ElevenLabs'te duruyor ve hiçbirinin ses paketi
yok: o kullanıcılar masal dinlemek istediğinde /voice/generate anlık üretim
yapıyordu. Artık o yol yok. Bu betik, mevcut her ses için paketi üretir ve
ardından ElevenLabs'teki sesi serbest bırakır (slot geri döner).

Üretim mantığı TEK yerden gelir: `voice_uretim.uret()`. Bu betik yalnız hangi
profillerin işleneceğini seçer — kural kopyalanmaz, ayrışmaz.

Üretim ONLINE'dır ve kota harcar. Kuru koşu tahmini gösterir:
    railway ssh
    python scripts/ses_paketi_gecis.py
    python scripts/ses_paketi_gecis.py --uygula

`--uygula` verilmedikçe hiçbir şey üretilmez ve hiçbir ses silinmez.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                              # noqa: E402

load_dotenv(ROOT / ".env")

from api.config import ELEVENLABS_FIYATLARI                 # noqa: E402
from api.db import SessionLocal                             # noqa: E402
from api.konusma_metni import masal_metni_hazirla           # noqa: E402
from api import tts                                         # noqa: E402
from api.models import User, VoiceAudio, VoiceProfile       # noqa: E402
from api.services import voice_paket, voice_uretim          # noqa: E402

# İşlenecek durumlar: ElevenLabs'te sesi olan ve paketi henüz üretilmemiş olanlar.
HEDEF_DURUMLAR = ("pending", "ready", "cloning")


def _adaylar(db):
    out = []
    for p in (db.query(VoiceProfile)
              .filter(VoiceProfile.elevenlabs_voice_id.isnot(None))
              .order_by(VoiceProfile.created_at)
              .all()):
        if p.status not in HEDEF_DURUMLAR:
            continue
        hazir = (db.query(VoiceAudio)
                 .filter(VoiceAudio.voice_profile_id == p.id).count())
        if hazir >= voice_paket.paket_boyutu():
            continue                      # paketi zaten tam
        out.append((p, hazir))
    return out


def _paket_karakteri() -> int:
    """Paketin TTS'e gidecek karakter sayısı (duraklama etiketleri dahil)."""
    return sum(len(masal_metni_hazirla(x["text"]))
               for x in voice_paket.paket_icerikleri())


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uygula", action="store_true",
                    help="Gerçekten üret ve sesleri serbest bırak")
    a = ap.parse_args()

    db = SessionLocal()
    try:
        adaylar = _adaylar(db)
        kar = _paket_karakteri()
        fiyat = ELEVENLABS_FIYATLARI.get(tts.TTS_MODEL, 0.00022)
        print(f"Paket: {voice_paket.paket_adi()} "
              f"({voice_paket.paket_boyutu()} içerik, {kar} karakter, "
              f"{kar * fiyat:.4f} USD/kullanıcı)")
        print(f"Geçirilecek ses profili: {len(adaylar)}\n")
        for p, hazir in adaylar:
            u = db.get(User, p.user_id)
            print(f"  {p.id}  {getattr(u, 'email', '?'):32s} "
                  f"durum={p.status:10s} hazır={hazir}/{voice_paket.paket_boyutu()}")
        if not adaylar:
            print("Geçirilecek ses yok.")
            return
        print(f"\nTOPLAM: {len(adaylar) * kar} karakter, "
              f"{len(adaylar) * kar * fiyat:.2f} USD")
        idler = [str(p.id) for p, _h in adaylar]
    finally:
        db.close()

    if not a.uygula:
        print("\n(KURU KOŞU — hiçbir şey üretilmedi. Uygulamak için --uygula "
              "ekleyin.)")
        return

    print("\nÜretim başlıyor (her paket bitince ElevenLabs sesi silinir)...")
    # `ready` v2.2'de "ses klonlandı, kullanılabilir" demekti; v2.3'te "paket
    # hazır" demek ve `uret()` onu BİTMİŞ sayıp hemen dönüyor. Geçişte durumu
    # açıkça `cloning`e çekiyoruz — yeni modelde bu profillerin gerçek durumu
    # budur: ses var, paket henüz yok.
    import uuid as _uuid
    db = SessionLocal()
    try:
        for pid in idler:
            p = db.get(VoiceProfile, _uuid.UUID(pid))
            if p is not None and p.status in HEDEF_DURUMLAR:
                p.status = "cloning"
                p.progress_total = voice_paket.paket_boyutu()
                p.progress_done = 0
        db.commit()
    finally:
        db.close()

    basarili = 0
    for pid in idler:
        sonuc = voice_uretim.uret(pid)
        print(f"  {pid}: {sonuc['uretilen']}/{sonuc['toplam']} → "
              f"{sonuc['status']}")
        basarili += int(sonuc["status"] in ("ready", "released"))
    print(f"\n{basarili}/{len(idler)} paket hazır.")


if __name__ == "__main__":
    main()
