"""
Açık kalmış sayaç kayıtlarını kapat — TEK SEFERLİK düzeltme (v2.2 / Faz 3).

NEDEN VAR: anne uyku sayacını başlatıp durdurmuyor. Kayıt `ended_at = NULL`
kalıyor ve mobilde sayaç dönmeye devam ediyor; motor tarafında da "şu an
sürüyor" sanılıyor. 2026-09-21 taramasında son 7 günde 19 bebeğin 13'ünde
toplam 107 adet 16 saatten uzun süredir açık kayıt vardı.

Bundan SONRASI için akış düzeltildi (K13.3): POST /logs/batch, gelen manuel
kaydın KAPSADIĞI açık sayacı otomatik kapatıyor. 2026-10-12'den beri gece
temizliği (03:30 UTC) 24 saatten eski açık kayıtları da aynı kuralla kapatıyor;
bu betik elle tarama ve onarım içindir.

KURAL:
  1. Açık kaydı KAPSAYAN kapalı bir manuel kayıt varsa (started_at <= açık
     kaydın started_at'i <= ended_at) → o kaydın `ended_at`'i ile kapat.
     Birden çok aday varsa EN ERKEN biten (sayaç en geç o an bitmiştir).
  2. Kapsayan yoksa, kapanış şu üçünün EN ERKENİ:
     • bir SONRAKİ kaydın başlangıcı (bebek uyanmış olmalı ki yeni kayıt açılsın),
     • `nap` ise  started_at + bandın planlanan uyku süresi (ör. 8 ay: 60 dk),
       `sleep` ise started_at + bandın gece uykusu ÜST sınırı (ör. 720 dk),
     • started_at + 16 saat (mutlak tavan).
Kapanış başlangıçtan SONRA olmak zorunda; değilse asgari 60 dk kullanılır
(sıfır süreli kayıt üretmek, düzeltmeye çalıştığımız hatanın ta kendisi).

NEDEN DÜZ "16 SAAT" DEĞİL: ilk sürüm spec'teki tek kuralı (+16 saat)
uyguluyordu. Prod'da ölçülen sonuç kabul edilemezdi:
  • kapsayanı olmayan 81 kaydın 40'ı `nap`'ti → 16 SAATLİK GÜNDÜZ UYKUSU,
  • 41 `sleep` tam 16 saate kapandı ve bunların 76 çakışma çiftinde GERÇEK
    kayıtları eledi (K13.1 "ikisi de kapalıysa uzun olan" kuralı sahte olanı
    seçiyordu: 960 dk'lık yapay kayıt, 78 dk'lık gerçek kaydı yutuyordu),
  • 41'in 35'inde zaten 16 saat dolmadan yeni bir kayıt başlamıştı — yani
    bebeğin uyandığı KANITLIYDI ve bunu görmezden geliyorduk.
Bozuk veriyi başka bir bozuk veriyle değiştirmemek için kural daraltıldı.
Zaten yazılmış tam-16-saatlik kayıtlar `--duzelt-16saat` ile onarılır.

`nap_skipped` kayıtlarına DOKUNULMAZ: onlarda `ended_at = NULL` bir hata değil,
"bu uykuyu hiç yapmadı" beyanıdır (K7).

Üretim Postgres'i dışarıya KAPALI. Betik KONTEYNER İÇİNDE koşar:

    railway ssh
    python scripts/acik_sayac_kapat.py --rapor
    python scripts/acik_sayac_kapat.py --duzelt-gunduz-uzun
    python scripts/acik_sayac_kapat.py
    python scripts/acik_sayac_kapat.py --uygula

GÜVENLİK: iki adımlıdır. `--uygula` verilmedikçe hiçbir satır değişmez, yalnız
ne yapılacağı listelenir.

v1.6 (2026-09-30): kapatılan her kaydın notuna "otomatik kapatıldı" EKLENİR
(annenin notu ezilmez) ve satırların DEĞİŞİKLİKTEN ÖNCEKİ hâli tam olarak
/data/arsiv/acik-sayac-kapat-<zaman>.json'a yazılır (geri almak için). Bütün
geçmişi taramak için: `--gun 3650 --uygula`.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                              # noqa: E402

# load_dotenv() ÇAĞIRAN DOSYANIN dizininden yukarı arar, CWD'den değil.
load_dotenv(ROOT / ".env")

from api.db import SessionLocal                             # noqa: E402
from api.models import SleepLog                             # noqa: E402
from api.services.acik_kayit import (                       # noqa: E402
    KAPANIS_KAYNAGI, KAPATMA_NOTU, TERK_SAAT, TZ_OFFSET_MIN, UYKU_TIPLERI,
    _adaylar, _arsivle, _bant_sureleri, _not_ekle, _tahmini_kapanis, _utc,
)

# Kural ve kapanış hesabı api/services/acik_kayit.py'de (2026-10-12): gece
# temizliği (saklama.gece_temizligi) 24 saatten eski açık kayıtları aynı
# kuralla her gece kapatıyor. Bu betik elle tarama/onarım için duruyor.
# --duzelt-gunduz-uzun: bu betiğin ÖNCEKİ sürümlerinin çalıştığı pencere.
# v2.2'de gündüz başlayan `sleep` kayıtlarına GECE tavanı uygulanıyordu; kapanış
# "sonraki kaydın başlangıcına" kadar uzayıp arka arkaya zincirlenmiş dev
# gündüz uykuları üretti (ölçüldü: 7 kayıt / 4 bebek, 139–601 dk). Onarım
# hedefini bu zaman damgasıyla ayırt ediyoruz: o koşularda dokunulan satırların
# `updated_at`'i bu aralıkta, annenin kendi kayıtlarınınki değil.
ONARIM_PENCERESI = ("2026-09-21T16:45:00+00:00", "2026-09-21T17:30:00+00:00")
# Gece uykusu penceresi (yerel): bu saatten sonra ya da bu saatten önce başlayan
# `sleep` kaydı gece uykusudur; arası gündüz uykusudur.
GECE_BASLANGIC_DK = 18 * 60    # 18:00
GUNDUZ_BASLANGIC_DK = 6 * 60   # 06:00


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gun", type=int, default=7,
                    help="Kaç günlük pencere taransın (varsayılan 7)")
    ap.add_argument("--rapor", action="store_true",
                    help="Yalnız sayımları yazdır (bozuk kayıt türleri dahil)")
    ap.add_argument("--duzelt-gunduz-uzun", action="store_true",
                    help="Bu betiğin önceki sürümünün GÜNDÜZ kayıtlarda "
                         "bıraktığı aşırı uzun kapanışları K17 ile onar")
    ap.add_argument("--duzelt-16saat", action="store_true",
                    help="Daha önce TAM 16 saate kapatılmış kayıtları onar "
                         "(ilk sürümün bıraktığı yapay kayıtlar)")
    ap.add_argument("--uygula", action="store_true",
                    help="Gerçekten kapat (verilmezse yalnız listelenir)")
    a = ap.parse_args()

    db = SessionLocal()
    try:
        if a.rapor:
            _rapor(db, a.gun)
            return
        if getattr(a, "duzelt_16saat", False):
            _duzelt_16saat(db, a.gun, a.uygula)
            return
        if getattr(a, "duzelt_gunduz_uzun", False):
            _duzelt_gunduz_uzun(db, a.uygula)
            return

        adaylar = _adaylar(db, a.gun)
        if not adaylar:
            print(f"Son {a.gun} günde {TERK_SAAT} saatten uzun açık sayaç kaydı yok.")
            return

        from collections import Counter
        sayim = Counter(g for _r, _k, g in adaylar)
        print(f"Son {a.gun} günde {TERK_SAAT} saatten uzun açık kayıt: {len(adaylar)}")
        for g, n in sayim.most_common():
            print(f"  {g:34s}: {n}")
        print(f"  etkilenen bebek                   : "
              f"{len({r.baby_id for r, _k, _g in adaylar})}\n")
        for r, kapanis, gerekce in adaylar:
            sure = (kapanis - _utc(r.started_at)).total_seconds() / 3600
            print(f"  {r.id}  baby={str(r.baby_id)[:8]}  {r.type:5s}  "
                  f"{_utc(r.started_at).isoformat()} → {kapanis.isoformat()}  "
                  f"({sure:.1f} sa, {gerekce})")

        if not a.uygula:
            print("\n(KURU KOŞU — hiçbir kayıt değişmedi. Uygulamak için "
                  "--uygula ekleyin.)")
            return

        yol = _arsivle([r for r, _k, _g in adaylar])
        print(f"\narşiv (eski hâl): {yol}")
        for r, kapanis, _g in adaylar:
            r.ended_at = kapanis
            r.kapanis_kaynagi = KAPANIS_KAYNAGI
            _not_ekle(r, KAPATMA_NOTU)
        db.commit()
        print(f"{len(adaylar)} açık sayaç kaydı kapatıldı.")
        kalan = _adaylar(db, a.gun)
        print(f"Kalan {TERK_SAAT} saatten uzun açık uyku kaydı: {len(kalan)}")
    finally:
        db.close()


def _duzelt_gunduz_uzun(db, uygula: bool) -> None:
    """v2.2 temizliğinin GÜNDÜZ kayıtlarda bıraktığı uzun kapanışları onar.

    Hedef: `updated_at`'i ONARIM_PENCERESI içinde olan, YEREL gündüz saatinde
    (06:00-18:00) başlayan ve süresi bandın planlanan uyku süresini AŞAN
    sleep/nap kayıtları. Yeni bitiş K17'nin kuralıdır: başlangıç + planlanan
    süre.

    Annenin KENDİ girdiği uzun kayıtlara dokunulmaz — onların `updated_at`'i
    bu pencerede değil."""
    bas_p = datetime.fromisoformat(ONARIM_PENCERESI[0])
    bit_p = datetime.fromisoformat(ONARIM_PENCERESI[1])
    rows = (db.query(SleepLog)
            .filter(SleepLog.type.in_(UYKU_TIPLERI),
                    SleepLog.ended_at.isnot(None))
            .all())

    plan = []
    for r in rows:
        guncel = _utc(r.updated_at)
        if guncel is None or not (bas_p <= guncel <= bit_p):
            continue
        bas = _utc(r.started_at)
        yerel = bas + timedelta(minutes=TZ_OFFSET_MIN)
        yerel_dk = yerel.hour * 60 + yerel.minute
        if not (GUNDUZ_BASLANGIC_DK <= yerel_dk < GECE_BASLANGIC_DK):
            continue                       # gece uykusu — 16 saat tavanı geçerli
        nap_dk, _gece = _bant_sureleri(db, r.baby_id)
        sure = (_utc(r.ended_at) - bas).total_seconds() / 60
        if sure <= nap_dk:
            continue
        plan.append((r, bas + timedelta(minutes=nap_dk), sure, nap_dk))

    if not plan:
        print("Onarılacak gündüz kaydı yok.")
        return

    print(f"Onarılacak gündüz kaydı: {len(plan)} "
          f"({len({r.baby_id for r, _k, _s, _n in plan})} bebek)\n")
    for r, yeni, sure, nap_dk in plan:
        y = _utc(r.started_at) + timedelta(minutes=TZ_OFFSET_MIN)
        print(f"  {r.id}  baby={str(r.baby_id)[:8]}  {r.type:5s}  "
              f"{y.strftime('%m-%d %H:%M')}  {sure:.0f} dk → {nap_dk} dk")

    if not uygula:
        print("\n(KURU KOŞU — hiçbir kayıt değişmedi. Uygulamak için "
              "--uygula ekleyin.)")
        return
    for r, yeni, _s, _n in plan:
        r.ended_at = yeni
        r.kapanis_kaynagi = KAPANIS_KAYNAGI
    db.commit()
    print(f"\n{len(plan)} kayıt onarıldı.")


def _duzelt_16saat(db, gun: int, uygula: bool) -> None:
    """Betiğin İLK sürümünün TAM 16 saate kapattığı kayıtları onar.

    Bu kayıtlar gerçek değil, bizim ürettiğimiz yer tutuculardı ve çakışma
    çözümünde (K13.1 "uzun olan kazanır") gerçek kayıtları eliyorlardı.
    Süresi tam 16:00:00 olan bir kaydın kullanıcı tarafından girilmiş olma
    olasılığı yok denecek kadar düşük — ayırt edici imza budur."""
    simdi = datetime.now(timezone.utc)
    basla = simdi - timedelta(days=gun + 1)
    rows = db.query(SleepLog).filter(SleepLog.started_at >= basla).all()
    tum: dict = {}
    for r in rows:
        tum.setdefault(r.baby_id, []).append(r)

    hedef = [r for r in rows
             if r.type in UYKU_TIPLERI and r.ended_at is not None
             and (_utc(r.ended_at) - _utc(r.started_at)) == timedelta(hours=TERK_SAAT)]
    if not hedef:
        print(f"Tam {TERK_SAAT} saatlik yapay kayıt yok.")
        return

    print(f"Tam {TERK_SAAT} saate kapatılmış kayıt: {len(hedef)}\n")
    plan = []
    for r in hedef:
        bas = _utc(r.started_at)
        # Kendisi hariç, aynı bebeğin diğer kayıtları
        digerleri = [x for x in tum.get(r.baby_id, []) if x.id != r.id]
        kapanis, gerekce = _tahmini_kapanis(db, r, bas, digerleri)
        plan.append((r, kapanis, gerekce))
        sure = (kapanis - bas).total_seconds() / 60
        print(f"  {r.id}  baby={str(r.baby_id)[:8]}  {r.type:5s}  "
              f"{bas.isoformat()} → {kapanis.isoformat()}  "
              f"({sure:.0f} dk, {gerekce})")

    if not uygula:
        print("\n(KURU KOŞU — hiçbir kayıt değişmedi. Uygulamak için "
              "--uygula ekleyin.)")
        return
    for r, kapanis, _g in plan:
        r.ended_at = kapanis
        r.kapanis_kaynagi = KAPANIS_KAYNAGI
    db.commit()
    print(f"\n{len(plan)} kayıt onarıldı.")


def _rapor(db, gun: int) -> None:
    """Faz 3 sayımları: bozuk kayıt türlerinin dağılımı (hiçbir şey değişmez)."""
    simdi = datetime.now(timezone.utc)
    basla = simdi - timedelta(days=gun)
    rows = (db.query(SleepLog).filter(SleepLog.started_at >= basla).all())

    sifir = ters = kisa = 0
    kapali_by_baby: dict = {}
    for r in rows:
        if r.type not in UYKU_TIPLERI or r.ended_at is None:
            continue
        dk = (_utc(r.ended_at) - _utc(r.started_at)).total_seconds() / 60
        if dk < 0:
            ters += 1
        elif dk == 0:
            sifir += 1
        elif dk < 5:
            kisa += 1
        kapali_by_baby.setdefault(r.baby_id, []).append(r)

    cift = 0
    for _bid, lst in kapali_by_baby.items():
        lst.sort(key=lambda x: _utc(x.started_at))
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                x, y = lst[i], lst[j]
                if _utc(y.started_at) >= _utc(x.ended_at):
                    break
                ortak = (min(_utc(x.ended_at), _utc(y.ended_at))
                         - max(_utc(x.started_at), _utc(y.started_at))
                         ).total_seconds() / 60
                kis = min((_utc(x.ended_at) - _utc(x.started_at)).total_seconds() / 60,
                          (_utc(y.ended_at) - _utc(y.started_at)).total_seconds() / 60)
                if ortak > 0 and kis > 0 and ortak / kis > 0.5:
                    cift += 1

    adaylar = _adaylar(db, gun)
    print(f"=== Son {gun} gün ===")
    print(f"  kayıt                          : {len(rows)}")
    print(f"  bebek                          : {len({r.baby_id for r in rows})}")
    print(f"  sıfır süreli sleep/nap         : {sifir}")
    print(f"  ters (bitiş < başlangıç)       : {ters}")
    print(f"  5 dk altı                      : {kisa}")
    print(f"  %50+ örtüşen kapalı kayıt çifti: {cift}")
    print(f"  {TERK_SAAT} saatten uzun açık sayaç    : {len(adaylar)}")
    print("\nNOT: sıfır süreli/kısa/örtüşen kayıtlar DB'de DÜZELTİLMEZ —")
    print("motor onları okuma anında yok sayıyor (K12.1/K13) ve sebebini")
    print("adaptation.yok_sayilan_kayitlar'a yazıyor. Yalnız açık sayaçlar")
    print("kapatılır, çünkü onlar mobilde de yanlış durum gösteriyor.")


if __name__ == "__main__":
    main()
