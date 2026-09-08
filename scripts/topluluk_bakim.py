"""
Topluluk bakım betiği — seed profillerine uzman rozeti + eski test konularını temizleme.

Üretim Postgres'i dışarıya KAPALI (Postgres servisinin DATABASE_PUBLIC_URL'inde TCP
proxy host'u yok). Bu yüzden betik KONTEYNER İÇİNDE koşar:

    railway ssh
    python scripts/topluluk_bakim.py --konulari-listele
    python scripts/topluluk_bakim.py --test-konulari
    python scripts/topluluk_bakim.py --test-konulari --uygula
    python scripts/topluluk_bakim.py --uzman-rozeti

(Railway panel → servis → Console de aynı işi görür.)

GÜVENLİK: silme İKİ ADIMLIDIR. --uygula verilmedikçe hiçbir şey silinmez, yalnız
ne silineceği listelenir. Silme geri alınamaz; önce listeyi okuyun.

Silinen konunun cevapları (replies) DB'de ON DELETE CASCADE ile gider; ancak
likes/reports/moderation_log tabloları polimorfik hedef tutar (threads'e FK YOK),
onlar öksüz kalmasın diye elle temizlenir.
"""
import argparse
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv          # noqa: E402

load_dotenv()

from api.db import SessionLocal         # noqa: E402
from api.models import (                # noqa: E402
    CommunityProfile, Like, ModerationLog, Reply, Report, Thread,
)

# --- 1) Uzman rozeti verilecek seed profilleri -------------------------------
SEED_UZMAN_PROFILLERI = [
    "9041b81a-68fa-4db1-9af6-19f8faafb893",
    "97975a63-72d5-49eb-ae5b-7751b87d1c08",
]

# --- 2) Test konusu sezgileri ------------------------------------------------
# Başlıkta ayrı kelime olarak "test" geçenler (Test 2, Beslenme test, test konusu…)
TEST_DESENI = re.compile(r"(^|[\s\-_/])test([\s\-_/]|\d|$)", re.IGNORECASE)
# "test" geçmeyen ama elle bildirilen eski deneme başlıkları (tam eşleşme, harf
# büyüklüğü ve baştaki/sondaki boşluk önemsiz).
EK_BASLIKLAR = {
    "gece uyanması",
    "gece uyanmasi",
    "beslenme test",
    "test 1", "test 2", "test 3",
    "deneme", "deneme 1", "deneme 2", "asdasd", "aaa",
}


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def test_konusu_mu(t: Thread) -> bool:
    return bool(TEST_DESENI.search(t.title or "")) or _norm(t.title) in EK_BASLIKLAR


def _nick(db, user_id):
    if user_id is None:
        return "(silinmiş kullanıcı)"
    p = db.query(CommunityProfile).filter(CommunityProfile.user_id == user_id).one_or_none()
    return p.nickname if p else "(profilsiz)"


def _yaz_konu(db, t: Thread, isaret: str = " "):
    tarih = t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "?"
    print(f"{isaret} {t.id}  {tarih}  [{t.category:<10}] [{t.status:<9}] "
          f"cevap={t.reply_count:<3} yazar={_nick(db, t.user_id)}")
    print(f"    başlık: {t.title!r}")
    print(f"    gövde : {(t.body or '')[:90]!r}")


# =============================================================================
def komut_listele(db) -> int:
    konular = db.query(Thread).order_by(Thread.created_at).all()
    print(f"Toplam {len(konular)} konu:\n")
    for t in konular:
        _yaz_konu(db, t, "*" if test_konusu_mu(t) else " ")
    print(f"\n('*' = test konusu sezgisine takılan {sum(1 for t in konular if test_konusu_mu(t))} kayıt)")
    return 0


def komut_test_konulari(db, uygula: bool, ekstra_idler: list[str]) -> int:
    adaylar = [t for t in db.query(Thread).order_by(Thread.created_at).all()
               if test_konusu_mu(t)]
    for ham in ekstra_idler:                       # elle eklenen ID'ler
        t = db.get(Thread, uuid.UUID(ham))
        if t is None:
            print(f"UYARI: konu bulunamadı, atlanıyor: {ham}")
        elif t not in adaylar:
            adaylar.append(t)

    if not adaylar:
        print("Silinecek test konusu bulunamadı.")
        return 0

    konu_idleri = [t.id for t in adaylar]
    cevaplar = db.query(Reply).filter(Reply.thread_id.in_(konu_idleri)).all()
    cevap_idleri = [r.id for r in cevaplar]
    hedefler = [("thread", i) for i in konu_idleri] + [("reply", i) for i in cevap_idleri]
    hedef_idleri = konu_idleri + cevap_idleri

    print("=" * 78)
    print(f"SİLİNECEK {len(adaylar)} KONU (+ {len(cevaplar)} cevap)")
    print("=" * 78)
    for t in adaylar:
        _yaz_konu(db, t, "-")
        for r in [r for r in cevaplar if r.thread_id == t.id]:
            print(f"      ↳ cevap {r.id} [{r.status}] {_nick(db, r.user_id)}: "
                  f"{(r.body or '')[:70]!r}")
    print("-" * 78)

    begeni = db.query(Like).filter(Like.target_id.in_(hedef_idleri)).all()
    sikayet = db.query(Report).filter(Report.target_id.in_(hedef_idleri)).all()
    kayit = db.query(ModerationLog).filter(ModerationLog.target_id.in_(hedef_idleri)).all()
    begeni = [x for x in begeni if (x.target_type, x.target_id) in hedefler]
    sikayet = [x for x in sikayet if (x.target_type, x.target_id) in hedefler]
    kayit = [x for x in kayit if (x.target_type, x.target_id) in hedefler]
    print(f"Bağlı kayıtlar: {len(begeni)} beğeni, {len(sikayet)} şikayet, "
          f"{len(kayit)} moderasyon log satırı da silinecek.")

    if not uygula:
        print("\nKURU ÇALIŞMA — hiçbir şey silinmedi.")
        print("Gerçekten silmek için aynı komutu --uygula ile çalıştırın.")
        return 0

    for x in begeni + sikayet + kayit:
        db.delete(x)
    for r in cevaplar:
        db.delete(r)
    db.flush()
    for t in adaylar:
        db.delete(t)
    db.commit()
    print(f"\nSİLİNDİ: {len(adaylar)} konu, {len(cevaplar)} cevap, {len(begeni)} beğeni, "
          f"{len(sikayet)} şikayet, {len(kayit)} log satırı.")
    return 0


def komut_uzman_rozeti(db, profil_idleri: list[str], geri_al: bool) -> int:
    hedef = True and not geri_al
    bulunamayan = 0
    for ham in profil_idleri:
        try:
            pid = uuid.UUID(ham)
        except ValueError:
            print(f"HATA: geçersiz UUID: {ham}")
            bulunamayan += 1
            continue
        prof = db.get(CommunityProfile, pid)
        if prof is None:
            print(f"HATA: profil bulunamadı: {ham}")
            bulunamayan += 1
            continue
        onceki = prof.is_expert
        prof.is_expert = hedef
        print(f"{'OK  ' if onceki != hedef else 'ZATEN'} {prof.nickname!r} ({ham}) "
              f"is_expert: {onceki} → {hedef}")
    db.commit()
    return 1 if bulunamayan else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Topluluk bakımı (uzman rozeti / test temizliği)")
    ap.add_argument("--konulari-listele", action="store_true",
                    help="Bütün konuları dök (test sezgisine takılanlar '*' ile işaretli)")
    ap.add_argument("--test-konulari", action="store_true",
                    help="Test konularını listele (silmek için --uygula ekleyin)")
    ap.add_argument("--konu-id", action="append", default=[],
                    help="Sezgiye takılmayan bir konuyu da silme listesine ekle (tekrarlanabilir)")
    ap.add_argument("--uzman-rozeti", action="store_true",
                    help="Seed profillerine is_expert=true ver")
    ap.add_argument("--profil-id", action="append", default=[],
                    help="Rozet verilecek profil id (varsayılan: iki seed profili)")
    ap.add_argument("--geri-al", action="store_true", help="Rozeti kaldır (is_expert=false)")
    ap.add_argument("--uygula", action="store_true",
                    help="SİLMEYİ GERÇEKTEN YAP (yoksa yalnızca listeler)")
    args = ap.parse_args()

    if not (args.konulari_listele or args.test_konulari or args.uzman_rozeti):
        ap.print_help()
        return 2

    db = SessionLocal()
    kod = 0
    try:
        if args.uzman_rozeti:
            print("### 1) UZMAN ROZETİ ###")
            kod |= komut_uzman_rozeti(db, args.profil_id or SEED_UZMAN_PROFILLERI,
                                      args.geri_al)
            print()
        if args.konulari_listele:
            print("### KONU DÖKÜMÜ ###")
            kod |= komut_listele(db)
            print()
        if args.test_konulari:
            print("### 2) TEST KONULARI ###")
            kod |= komut_test_konulari(db, args.uygula, args.konu_id)
    finally:
        db.close()
    return kod


if __name__ == "__main__":
    raise SystemExit(main())
