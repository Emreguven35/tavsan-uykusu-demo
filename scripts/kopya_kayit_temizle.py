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

İKİNCİ GEÇİŞ — ÇAPRAZ TİP (`--capraz-tip`): prod'da `sleep` + `nap` olarak İKİ
KEZ gönderilmiş aynı uykular da var (27 çift ölçüldü) — mobil aynı uykuyu iki
farklı tiple yolluyor. Varsayılan geçiş "type eşit" dediği için bunlara
dokunmaz; `--capraz-tip` bu çiftleri de tekilleştirir.

Güvenli olmasının sebebi FİZİKSEL: bir bebek 3 dakika arayla İKİ AYRI uykuya
başlayamaz. Dolayısıyla aynı bebekte ±3 dk içinde başlayan iki `sleep`/`nap`
kaydı, tipi ne olursa olsun, AYNI uykudur. (`feed`/`wake`/`night_wake` bu
geçişe GİRMEZ: beslenme gerçekten kısa aralıklarla tekrarlanabilir.)

Motor zaten okuma anında K13 ile tekilleştirdiği için plan çıktısı bu geçiş
olmadan da doğrudur; geçiş DB'deki fazlalığı temizler (haftalık özet ve ham
kayıt listesi de sadeleşir).

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


UYKU_TIPLERI = ("sleep", "nap")


def _plan(db, gun: int, capraz: bool = False):
    """(kume sayısı, [(silinecek, kazanan)]) — hiçbir şey değiştirmez.

    `capraz=False`: aynı bebek + AYNI type (spec K18.3).
    `capraz=True` : aynı bebek, type'a BAKMADAN, yalnız sleep/nap kayıtları."""
    basla = datetime.now(timezone.utc) - timedelta(days=gun)
    rows = db.query(SleepLog).filter(SleepLog.started_at >= basla).all()
    grup = defaultdict(list)
    for r in rows:
        if capraz:
            if r.type in UYKU_TIPLERI:
                grup[r.baby_id].append(r)       # type AYRIMI YOK
        else:
            grup[(r.baby_id, r.type)].append(r)

    kumeler, silinecek = [], []
    for kayitlar in grup.values():
        for kume in _kumele(kayitlar):
            if capraz and len({r.type for r in kume}) == 1:
                continue        # tek tipli küme birinci geçişin işi
            kumeler.append(kume)
            kazanan = _kazanan(kume)
            for r in kume:
                if r.id != kazanan.id:
                    silinecek.append((r, kazanan))
    return kumeler, silinecek


def rapor(db, gun: int, capraz: bool = False) -> None:
    kumeler, silinecek = _plan(db, gun, capraz)
    print(f"=== Son {gun} gün — kopya taraması "
          f"({'ÇAPRAZ TİP' if capraz else 'aynı type'}) ===")
    print(f"  kopya kümesi        : {len(kumeler)}")
    print(f"  silinecek kayıt     : {len(silinecek)}")
    print(f"  etkilenen bebek     : {len({k[0].baby_id for k in silinecek})}")
    print(f"  tip dağılımı        : "
          f"{dict(Counter(r.type for r, _k in silinecek))}")
    acik_silinen = sum(1 for r, _k in silinecek if r.ended_at is None)
    print(f"  bunların açık olanı : {acik_silinen}")
    print(f"  küme boyutu dağılımı: "
          f"{dict(Counter(len(k) for k in kumeler))}")

    if not capraz:
        _c_kume, _c_sil = _plan(db, gun, capraz=True)
        print(f"\n  ÇAPRAZ TİP (sleep+nap aynı saatte): {len(_c_kume)} küme, "
              f"{len(_c_sil)} kayıt")
        print("  Varsayılan geçiş bunlara DOKUNMAZ (spec 'type eşit').")
        print("  Temizlemek için: --capraz-tip")


def temizle(db, gun: int, uygula: bool, capraz: bool = False) -> None:
    kumeler, silinecek = _plan(db, gun, capraz)
    etiket = "ÇAPRAZ TİP" if capraz else "aynı type"
    if not silinecek:
        print(f"Son {gun} günde silinecek kopya yok ({etiket}).")
        return
    print(f"[{etiket}]")

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
        sebep = (f"K18.3 çapraz tip kopya (±3 dk, {r.type} → {kazanan.type})"
                 if capraz else f"K18.3 kopya (±3 dk, type={r.type})")
        db.add(SilinenSleepLog(
            sleep_log_id=r.id, user_id=r.user_id, baby_id=r.baby_id,
            veri=_satir_json(r), sebep=sebep, korunan_log_id=kazanan.id))
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
    ap.add_argument("--capraz-tip", action="store_true",
                    help="İkinci geçiş: aynı saatte FARKLI type (sleep+nap) "
                         "gönderilmiş kopyalar")
    ap.add_argument("--geri-al", metavar="SILINEN_ID",
                    help="silinen_sleep_logs'tan bir kaydı geri yükle")
    ap.add_argument("--uygula", action="store_true",
                    help="Gerçekten uygula (verilmezse yalnız listelenir)")
    a = ap.parse_args()

    db = SessionLocal()
    try:
        _capraz = getattr(a, "capraz_tip", False)
        if a.geri_al:
            geri_al(db, a.geri_al, a.uygula)
        elif a.rapor:
            rapor(db, a.gun, _capraz)
        else:
            temizle(db, a.gun, a.uygula, _capraz)
    finally:
        db.close()


if __name__ == "__main__":
    main()
