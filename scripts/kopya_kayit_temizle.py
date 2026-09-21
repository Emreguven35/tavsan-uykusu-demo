"""
Kopya uyku kaydı temizliği — TEK SEFERLİK (v2.2.1 / K18.3).

NEDEN VAR: zayıf ağda mobil aynı kaydı yeniden gönderiyor ve HER GÖNDERİMDE
YENİ bir client_id üretiyor. Bu yüzden (user_id, client_id) tekil kısıtı
kopyaları hiç yakalamıyor — prod'da ölçülen 41 kopya çiftinin TAMAMINDA
client_id'ler farklıydı. Gerçek vakada aynı bebekte 08:24 ve 08:26 başlangıçlı
iki kayıt iki ayrı uyku sanıldı.

Bundan SONRASI için akış düzeltildi (K18.2): client_id gelmeyen kayıtlarda
POST /logs/batch ±3 dk penceresiyle kopyayı bulup GÜNCELLİYOR. Bu betik yalnız
GEÇMİŞİ temizler.

KURAL (spec K18.3): son `--gun` günde, aynı bebekte type EŞİT ve başlangıçları
±3 dk olan kayıtlardan biri kalır:
  1. bitişi DOLU olan kalır (açık olan silinir),
  2. ikisi de doluysa UZUN olan kalır — K13.1 ile aynı ölçüt; kısa olan
     genellikle sayacın erken durdurulup yeniden başlatılmış hâli,
  3. ikisi de açıksa GEÇ olan silinir (erken olan özgün kayıttır).

KÜME MANTIĞI: ±3 dk zincirleme uygulanmaz. Küme, İLK kaydın başlangıcından
itibaren 3 dakikalık pencereyle kapanır; aksi hâlde 3'er dakika kayan uzun bir
zincir tek kümede birleşip gerçek uykuları yutardı.

TİP AYRIMI BİLİNÇLİ: prod'da `sleep` + `nap` olarak İKİ KEZ gönderilmiş aynı
uykular da var. Spec "type eşit" dediği için bu betik onlara DOKUNMAZ; motor
zaten okuma anında K13 ile tekilleştiriyor (çakışma çözümü tipe bakmaz).

GERİ ALINABİLİR: silinen her satır `silinen_sleep_logs` tablosuna TAM JSON
olarak kopyalanır. `notes` alanına not düşmek işe yaramazdı — satırla birlikte
o not da giderdi.

Üretim Postgres'i dışarıya KAPALI. Betik KONTEYNER İÇİNDE koşar:

    railway ssh
    python scripts/kopya_kayit_temizle.py --rapor
    python scripts/kopya_kayit_temizle.py
    python scripts/kopya_kayit_temizle.py --uygula
    python scripts/kopya_kayit_temizle.py --geri-al <silinen_id>

GÜVENLİK: iki adımlıdır. `--uygula` verilmedikçe hiçbir satır silinmez.
"""
import argparse
import json
import sys
import uuid as _uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                              # noqa: E402

load_dotenv(ROOT / ".env")

from api.db import SessionLocal                             # noqa: E402
from api.models import SilinenSleepLog, SleepLog            # noqa: E402

PENCERE = timedelta(minutes=3)
TZ_OFFSET_MIN = 180


def _utc(t):
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _yerel(t) -> str:
    return (_utc(t) + timedelta(minutes=TZ_OFFSET_MIN)).strftime("%m-%d %H:%M")


def _sure_dk(r) -> float | None:
    if r.ended_at is None:
        return None
    return (_utc(r.ended_at) - _utc(r.started_at)).total_seconds() / 60


def _kumele(kayitlar: list) -> list[list]:
    """Aynı (bebek, type) içindeki kayıtları 3 dk'lık pencerelere böl."""
    kayitlar = sorted(kayitlar, key=lambda r: _utc(r.started_at))
    kumeler, aktif = [], []
    for r in kayitlar:
        if aktif and (_utc(r.started_at) - _utc(aktif[0].started_at)) <= PENCERE:
            aktif.append(r)
        else:
            if len(aktif) > 1:
                kumeler.append(aktif)
            aktif = [r]
    if len(aktif) > 1:
        kumeler.append(aktif)
    return kumeler


def _kazanan(kume: list):
    """Kümede TUTULACAK kayıt (K18.3 sıralaması)."""
    kapalilar = [r for r in kume if r.ended_at is not None]
    if kapalilar:
        # Bitişi dolu olan(lar) kalır; birden çoksa UZUN olan.
        return max(kapalilar, key=lambda r: (_sure_dk(r) or 0,
                                             -_utc(r.started_at).timestamp()))
    # Hepsi açık → EN ERKEN başlayan kalır (geç olan yeniden gönderimdir).
    return min(kume, key=lambda r: _utc(r.started_at))


def _satir_json(r) -> str:
    return json.dumps({
        "id": str(r.id), "user_id": str(r.user_id), "baby_id": str(r.baby_id),
        "type": r.type,
        "started_at": _utc(r.started_at).isoformat(),
        "ended_at": _utc(r.ended_at).isoformat() if r.ended_at else None,
        "notes": r.notes, "client_id": r.client_id,
        "created_at": _utc(r.created_at).isoformat() if r.created_at else None,
    }, ensure_ascii=False)


def _plan(db, gun: int):
    """(kume sayısı, [(silinecek, kazanan)]) — hiçbir şey değiştirmez."""
    basla = datetime.now(timezone.utc) - timedelta(days=gun)
    rows = db.query(SleepLog).filter(SleepLog.started_at >= basla).all()
    grup = defaultdict(list)
    for r in rows:
        grup[(r.baby_id, r.type)].append(r)

    kumeler, silinecek = [], []
    for kayitlar in grup.values():
        for kume in _kumele(kayitlar):
            kumeler.append(kume)
            kazanan = _kazanan(kume)
            for r in kume:
                if r.id != kazanan.id:
                    silinecek.append((r, kazanan))
    return kumeler, silinecek


def rapor(db, gun: int) -> None:
    kumeler, silinecek = _plan(db, gun)
    print(f"=== Son {gun} gün — kopya taraması ===")
    print(f"  kopya kümesi        : {len(kumeler)}")
    print(f"  silinecek kayıt     : {len(silinecek)}")
    print(f"  etkilenen bebek     : {len({k[0].baby_id for k in silinecek})}")
    print(f"  tip dağılımı        : "
          f"{dict(Counter(r.type for r, _k in silinecek))}")
    acik_silinen = sum(1 for r, _k in silinecek if r.ended_at is None)
    print(f"  bunların açık olanı : {acik_silinen}")
    print(f"  küme boyutu dağılımı: "
          f"{dict(Counter(len(k) for k in kumeler))}")

    # Spec dışı kalan: aynı saatte FARKLI type ile gönderilmiş kopyalar
    basla = datetime.now(timezone.utc) - timedelta(days=gun)
    rows = db.query(SleepLog).filter(SleepLog.started_at >= basla,
                                     SleepLog.type.in_(("sleep", "nap"))).all()
    capraz = 0
    per_baby = defaultdict(list)
    for r in rows:
        per_baby[r.baby_id].append(r)
    for l in per_baby.values():
        l.sort(key=lambda r: _utc(r.started_at))
        for i in range(len(l)):
            for j in range(i + 1, len(l)):
                if (_utc(l[j].started_at) - _utc(l[i].started_at)) > PENCERE:
                    break
                if l[i].type != l[j].type:
                    capraz += 1
    print(f"\n  NOT — aynı saatte FARKLI type (sleep/nap) kopya çifti: {capraz}")
    print("  Bunlara DOKUNULMUYOR (spec 'type eşit' diyor); motor okuma anında")
    print("  K13 ile zaten tekilleştiriyor.")


def temizle(db, gun: int, uygula: bool) -> None:
    kumeler, silinecek = _plan(db, gun)
    if not silinecek:
        print(f"Son {gun} günde silinecek kopya yok.")
        return

    print(f"Kopya kümesi: {len(kumeler)} | silinecek kayıt: {len(silinecek)} | "
          f"etkilenen bebek: {len({r.baby_id for r, _k in silinecek})}\n")
    for r, kazanan in silinecek:
        print(f"  SİL  {r.id} baby={str(r.baby_id)[:8]} {r.type:5s} "
              f"{_yerel(r.started_at)} → "
              f"{_yerel(r.ended_at) if r.ended_at else 'AÇIK':11s} "
              f"({_sure_dk(r) if _sure_dk(r) is None else round(_sure_dk(r))} dk)")
        print(f"       KALAN {kazanan.id} {_yerel(kazanan.started_at)} → "
              f"{_yerel(kazanan.ended_at) if kazanan.ended_at else 'AÇIK'} "
              f"({_sure_dk(kazanan) if _sure_dk(kazanan) is None else round(_sure_dk(kazanan))} dk)")

    if not uygula:
        print("\n(KURU KOŞU — hiçbir kayıt silinmedi. Uygulamak için "
              "--uygula ekleyin.)")
        return

    for r, kazanan in silinecek:
        db.add(SilinenSleepLog(
            sleep_log_id=r.id, user_id=r.user_id, baby_id=r.baby_id,
            veri=_satir_json(r),
            sebep=f"K18.3 kopya (±3 dk, type={r.type})",
            korunan_log_id=kazanan.id))
        db.delete(r)
    db.commit()
    print(f"\n{len(silinecek)} kopya silindi; tamamı silinen_sleep_logs'a "
          f"arşivlendi (geri alınabilir).")


def geri_al(db, silinen_id: str, uygula: bool) -> None:
    kayit = db.get(SilinenSleepLog, _uuid.UUID(silinen_id))
    if kayit is None:
        print("Arşiv kaydı bulunamadı.")
        sys.exit(1)
    veri = json.loads(kayit.veri)
    print("Geri yüklenecek:", json.dumps(veri, ensure_ascii=False, indent=2))
    if db.get(SleepLog, _uuid.UUID(veri["id"])) is not None:
        print("Bu id zaten var — geri yükleme YAPILMADI.")
        return
    if not uygula:
        print("\n(KURU KOŞU — --uygula ekleyin.)")
        return
    db.add(SleepLog(
        id=_uuid.UUID(veri["id"]), user_id=_uuid.UUID(veri["user_id"]),
        baby_id=_uuid.UUID(veri["baby_id"]), type=veri["type"],
        started_at=datetime.fromisoformat(veri["started_at"]),
        ended_at=(datetime.fromisoformat(veri["ended_at"])
                  if veri["ended_at"] else None),
        notes=veri["notes"], client_id=veri["client_id"]))
    db.delete(kayit)
    db.commit()
    print("Geri yüklendi.")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gun", type=int, default=14,
                    help="Kaç günlük pencere taransın (varsayılan 14)")
    ap.add_argument("--rapor", action="store_true", help="Yalnız sayımlar")
    ap.add_argument("--geri-al", metavar="SILINEN_ID",
                    help="silinen_sleep_logs'tan bir kaydı geri yükle")
    ap.add_argument("--uygula", action="store_true",
                    help="Gerçekten uygula (verilmezse yalnız listelenir)")
    a = ap.parse_args()

    db = SessionLocal()
    try:
        if a.geri_al:
            geri_al(db, a.geri_al, a.uygula)
        elif a.rapor:
            rapor(db, a.gun)
        else:
            temizle(db, a.gun, a.uygula)
    finally:
        db.close()


if __name__ == "__main__":
    main()
