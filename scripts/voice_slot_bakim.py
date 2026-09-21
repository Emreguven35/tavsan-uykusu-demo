"""
Ses (ElevenLabs) slot bakımı — sahipsiz klon sesleri + aylık sayaç sıfırlama.

NEDEN VAR (2026-09-16..18 arızası): POST /voice/clone'un üretimdeki TÜM
başarısızlıkları ElevenLabs'in şu yanıtıydı —

    "You have reached your maximum amount of custom voices (10 / 10)."

Bu tavan HESAP GENELİdir ve kullanıcı başına aylık 1 klonlama limitiyle ilgisi
YOKTUR; ikisi ayrı sınırlardır. Tavana çarpıldığında kod ElevenLabs'e hiç yeni
ses ekleyemez, dolayısıyla "yeni klon kaydedildikten SONRA eskisini sil"
temizliği de hiç çalışmaz — kilit kendini besler.

Router artık tıkandığında BAYAT sesleri (DB'de yalnız 'replaced' satırlarla
anılanlar) kendi başına geri alıyor. Ama DB'de HİÇ karşılığı olmayan sesleri
(hesapta elle açılmış klonlar, eski demo/test sesleri) OTOMATİK SİLMİYOR:
sahibi bilinmeyen biyometrik veriyi kod kendi başına yok etmemeli. Onları bu
betikle İNSAN gözden geçirir.

Üretim Postgres'i dışarıya KAPALI. Betik KONTEYNER İÇİNDE koşar:

    railway ssh
    python scripts/voice_slot_bakim.py --durum
    python scripts/voice_slot_bakim.py --bayat-sil
    python scripts/voice_slot_bakim.py --bayat-sil --uygula
    python scripts/voice_slot_bakim.py --sil <voice_id> --uygula
    python scripts/voice_slot_bakim.py --sayac-sifirla 7
    python scripts/voice_slot_bakim.py --sayac-sifirla 7 --uygula

GÜVENLİK: silme İKİ ADIMLIDIR. --uygula verilmedikçe hiçbir şey silinmez/
değişmez, yalnız ne yapılacağı listelenir. Silme GERİ ALINAMAZ.

ASLA otomatik silinmeyenler: başka kullanıcıların geçerli ('replaced' olmayan)
sesleri ve ELEVENLABS_VOICE_ID (uygulamanın anlatıcı sesi — DB'de satırı YOKTUR;
sahipsiz sanılıp silinirse tüm masal seslendirmesi çöker).
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                              # noqa: E402

# load_dotenv() ÇAĞIRAN DOSYANIN dizininden yukarı arar, CWD'den değil —
# betik scripts/ altında olduğu için proje .env'i açıkça veriliyor.
load_dotenv(ROOT / ".env")

from api.db import SessionLocal                             # noqa: E402
from api.models import User, VoiceProfile                   # noqa: E402
from api.services import voice as voice_svc                 # noqa: E402


def _sinifla(db):
    """Hesaptaki klon seslerini DB ile karşılaştır → (hesap, sınıflar) döner."""
    liste = voice_svc.list_cloned_voices()
    if not liste.get("ok"):
        print(f"HATA: ElevenLabs ses listesi alınamadı → {liste.get('error')}")
        sys.exit(2)
    hesap = {v["voice_id"]: (v.get("name") or "") for v in liste["voices"]}

    def _idler(sorgu):
        return {vid for (vid,) in sorgu.all() if vid}

    aktif = _idler(db.query(VoiceProfile.elevenlabs_voice_id)
                   .filter(VoiceProfile.status != "replaced",
                           VoiceProfile.elevenlabs_voice_id.isnot(None)))
    bayat = _idler(db.query(VoiceProfile.elevenlabs_voice_id)
                   .filter(VoiceProfile.status == "replaced",
                           VoiceProfile.elevenlabs_voice_id.isnot(None))) - aktif
    anlatici = (os.getenv("ELEVENLABS_VOICE_ID") or "").strip()

    return hesap, {
        "aktif": {v for v in hesap if v in aktif},
        "bayat": {v for v in hesap if v in bayat},
        "anlatici": {anlatici} & set(hesap),
        "sahipsiz": set(hesap) - aktif - bayat - ({anlatici} if anlatici else set()),
    }


def durum(db):
    hesap, s = _sinifla(db)
    print(f"\nElevenLabs hesabındaki KLON sesi: {len(hesap)}")
    print("(Plan tavanı hesap genelindedir; kullanıcı başına aylık limitten AYRIDIR.)\n")
    for etiket, aciklama in (
            ("aktif", "kullanıcıya ait, GEÇERLİ — dokunulmaz"),
            ("anlatici", "ELEVENLABS_VOICE_ID, uygulamanın anlatıcısı — dokunulmaz"),
            ("bayat", "DB'de 'replaced', silinmiş SAYILIYOR — geri alınabilir"),
            ("sahipsiz", "DB'de karşılığı YOK — elle karar verin")):
        idler = sorted(s[etiket])
        print(f"  [{etiket}] {len(idler)} — {aciklama}")
        for vid in idler:
            print(f"      {vid}  {hesap[vid]!r}")
    print(f"\n  Geri alınabilir slot (bayat): {len(s['bayat'])}")
    print(f"  Elle gözden geçirilecek     : {len(s['sahipsiz'])}\n")


def bayat_sil(db, uygula):
    _, s = _sinifla(db)
    hedef = sorted(s["bayat"])
    if not hedef:
        print("Geri alınacak bayat slot yok.")
        return
    print(f"{len(hedef)} bayat ses silinecek: {', '.join(hedef)}")
    if not uygula:
        print("(KURU KOŞU — hiçbir şey silinmedi. Uygulamak için --uygula ekleyin.)")
        return
    for vid in hedef:
        sonuc = voice_svc.delete_voice(vid)
        print(f"  {vid}: {'silindi' if sonuc.get('ok') else 'HATA ' + str(sonuc.get('error'))}")
    print("Bitti.")


def tek_sil(db, voice_id, uygula):
    _, s = _sinifla(db)
    if voice_id in s["aktif"]:
        print(f"REDDEDİLDİ: {voice_id} bir kullanıcının GEÇERLİ sesi. "
              "Bu betikle silinmez.")
        sys.exit(1)
    if voice_id in s["anlatici"]:
        print(f"REDDEDİLDİ: {voice_id} ELEVENLABS_VOICE_ID (anlatıcı sesi). "
              "Silinirse masal seslendirmesi çöker.")
        sys.exit(1)
    if voice_id not in s["bayat"] | s["sahipsiz"]:
        print(f"REDDEDİLDİ: {voice_id} hesaptaki klon sesleri arasında yok.")
        sys.exit(1)
    print(f"Silinecek: {voice_id}")
    if not uygula:
        print("(KURU KOŞU — hiçbir şey silinmedi. Uygulamak için --uygula ekleyin.)")
        return
    sonuc = voice_svc.delete_voice(voice_id)
    print("silindi" if sonuc.get("ok") else f"HATA {sonuc.get('error')}")
    if sonuc.get("ok"):
        n = (db.query(VoiceProfile)
             .filter(VoiceProfile.elevenlabs_voice_id == voice_id)
             .update({"status": "replaced"}, synchronize_session=False))
        db.commit()
        print(f"  {n} DB satırı 'replaced' işaretlendi")


def sayac_sifirla(db, gun, uygula):
    """Son `gun` gün içinde klonlaması olan kullanıcılara aylık hakkı geri ver.

    Aylık sayaç zaten YALNIZ başarılı klonlamayla ilerliyor: voice_profiles
    satırı ElevenLabs'ten voice_id DÖNDÜKTEN sonra açılıyor, başarısız deneme hiç
    satır açmıyor (bkz. tests/test_premium_kapisi.py 9b). Yani bu sıfırlama
    "yanlış sayılmış hakları" düzeltmiyor — slot tavanı yüzünden ses hizmeti
    bozuk çalışırken klonlama yapmış kullanıcılara hakkını İADE ediyor.

    Sıfırlama = last_cloned_at'i 31 gün geriye almak; can_clone yeniden true olur.
    Ek slot tüketmez: kullanıcı yeniden klonlarsa kendi eski sesi silinir."""
    sinir = datetime.now(timezone.utc) - timedelta(days=gun)
    profiller = [p for p in db.query(VoiceProfile).all()
                 if (lambda t: t is not None and (
                     t if t.tzinfo else t.replace(tzinfo=timezone.utc)) >= sinir)(
                     p.last_cloned_at or p.created_at)]
    if not profiller:
        print(f"Son {gun} günde klonlama yapan kullanıcı yok — sıfırlanacak sayaç yok.")
        return
    yeni = datetime.now(timezone.utc) - timedelta(days=31)
    print(f"Son {gun} günde klonlama yapan {len(profiller)} profil "
          f"({len({p.user_id for p in profiller})} kullanıcı) — hak iade edilecek:")
    for p in profiller:
        eposta = (db.get(User, p.user_id) or None)
        print(f"  user={p.user_id} ({getattr(eposta, 'email', '?')}) "
              f"voice={p.elevenlabs_voice_id} son_klon={p.last_cloned_at or p.created_at}")
    if not uygula:
        print("(KURU KOŞU — hiçbir şey değişmedi. Uygulamak için --uygula ekleyin.)")
        return
    for p in profiller:
        p.last_cloned_at = yeni
    db.commit()
    print(f"{len(profiller)} profilin sayacı sıfırlandı (can_clone=true).")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--durum", action="store_true",
                    help="Hesaptaki klon seslerini DB ile karşılaştır ve sınıflandır")
    ap.add_argument("--bayat-sil", action="store_true",
                    help="DB'de 'replaced' olan sesleri ElevenLabs'ten sil")
    ap.add_argument("--sil", metavar="VOICE_ID",
                    help="Sahipsiz/bayat TEK bir sesi sil (aktif ses ve anlatıcı reddedilir)")
    ap.add_argument("--sayac-sifirla", type=int, metavar="GUN",
                    help="Son GUN gün içinde klonlaması olan kullanıcılara aylık hakkı iade et")
    ap.add_argument("--uygula", action="store_true",
                    help="Gerçekten uygula (verilmezse yalnız listelenir)")
    a = ap.parse_args()

    if not any((a.durum, a.bayat_sil, a.sil, a.sayac_sifirla)):
        ap.print_help()
        return

    db = SessionLocal()
    try:
        if a.durum:
            durum(db)
        if a.bayat_sil:
            bayat_sil(db, a.uygula)
        if a.sil:
            tek_sil(db, a.sil, a.uygula)
        if a.sayac_sifirla:
            sayac_sifirla(db, a.sayac_sifirla, a.uygula)
    finally:
        db.close()


if __name__ == "__main__":
    main()
