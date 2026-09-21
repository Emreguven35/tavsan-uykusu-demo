"""
Açık kalmış sayaç kayıtlarını kapat — TEK SEFERLİK düzeltme (v2.2 / Faz 3).

NEDEN VAR: anne uyku sayacını başlatıp durdurmuyor. Kayıt `ended_at = NULL`
kalıyor ve mobilde sayaç dönmeye devam ediyor; motor tarafında da "şu an
sürüyor" sanılıyor. 2026-09-21 taramasında son 7 günde 19 bebeğin 13'ünde
toplam 107 adet 16 saatten uzun süredir açık kayıt vardı.

Bundan SONRASI için akış düzeltildi (K13.3): POST /logs/batch, gelen manuel
kaydın KAPSADIĞI açık sayacı otomatik kapatıyor. Bu betik yalnız GEÇMİŞİ
temizler; kalıcı bir iş değildir.

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
    python scripts/acik_sayac_kapat.py
    python scripts/acik_sayac_kapat.py --uygula

GÜVENLİK: iki adımlıdır. `--uygula` verilmedikçe hiçbir satır değişmez, yalnız
ne yapılacağı listelenir.
"""
import argparse
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
from api.models import Baby, SleepLog                       # noqa: E402
from engine import yas_bantlari                             # noqa: E402
from engine.parameter_engine import hesapla_yas_ay          # noqa: E402

UYKU_TIPLERI = ("sleep", "nap")
TERK_SAAT = 16                 # mutlak tavan: bundan uzun açık kayıt "terk edilmiş"
VARSAYILAN_NAP_DK = 60         # bant çözülemezse (doğum tarihi yok) makul uzunluk
VARSAYILAN_GECE_DK = 12 * 60   # bant çözülemezse gece uykusu üst sınırı
ASGARI_DK = 60                 # kapanış başlangıcın gerisine düşerse


def _bant_sureleri(db, baby_id, _onbellek: dict = {}) -> tuple[int, int]:
    """(planlanan gündüz uykusu dk, gece uykusu ÜST sınırı dk).

    Bant çözülemezse makul varsayılanlara düşülür — uydurma bir 16 saat yerine.
    Bebek başına bir kez hesaplanır."""
    if baby_id in _onbellek:
        return _onbellek[baby_id]
    nap_dk, gece_dk = VARSAYILAN_NAP_DK, VARSAYILAN_GECE_DK
    try:
        baby = db.get(Baby, baby_id)
        if baby is not None and baby.birth_date is not None:
            hafta = int(getattr(baby, "dogum_haftasi", None) or 40)
            ay = hesapla_yas_ay(baby.birth_date.isoformat(), hafta)["duzeltilmis_ay"]
            bant = yas_bantlari.yas_bandi_getir(ay)
            nap_dk = int(yas_bantlari.cizelge_parametreleri(bant)["uyku_suresi_dk"])
            ust = (bant.get("gece_uykusu_dk") or [None, None])[1]
            if ust:
                gece_dk = int(ust)
    except Exception:            # bant tablosu/doğum tarihi sorunlu → varsayılan
        pass
    _onbellek[baby_id] = (max(15, nap_dk), max(60, gece_dk))
    return _onbellek[baby_id]


def _sonraki_kayit_bas(kayitlar: list, bas: datetime) -> datetime | None:
    """Aynı bebeğin bu andan SONRA başlayan ilk kaydının başlangıcı.

    Yeni bir kayıt açılmışsa bebek o an uyanıktır — açık sayaç en geç o zaman
    bitmiştir. Elimizdeki en güçlü kanıt bu."""
    sonrakiler = [_utc(r.started_at) for r in kayitlar
                  if _utc(r.started_at) > bas]
    return min(sonrakiler) if sonrakiler else None


def _utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _adaylar(db, gun: int):
    """(açık kayıt, kapanış, gerekçe) üçlüleri."""
    simdi = datetime.now(timezone.utc)
    sinir = simdi - timedelta(hours=TERK_SAAT)
    basla = simdi - timedelta(days=gun)

    acik = (db.query(SleepLog)
            .filter(SleepLog.type.in_(UYKU_TIPLERI),
                    SleepLog.ended_at.is_(None),
                    SleepLog.started_at >= basla)
            .order_by(SleepLog.baby_id, SleepLog.started_at)
            .all())
    acik = [a for a in acik if _utc(a.started_at) < sinir]
    if not acik:
        return []

    kapali: dict = {}
    tum: dict = {}
    for r in (db.query(SleepLog)
              .filter(SleepLog.started_at >= basla - timedelta(days=1))
              .all()):
        tum.setdefault(r.baby_id, []).append(r)
        if r.type in UYKU_TIPLERI and r.ended_at is not None:
            kapali.setdefault(r.baby_id, []).append(r)

    out = []
    for a in acik:
        bas = _utc(a.started_at)
        ortusen = [_utc(r.ended_at) for r in kapali.get(a.baby_id, [])
                   if _utc(r.started_at) <= bas <= _utc(r.ended_at)
                   and _utc(r.ended_at) > bas]
        if ortusen:
            out.append((a, min(ortusen), "kapsayan manuel kayıt"))
            continue
        out.append((a,) + _tahmini_kapanis(db, a, bas, tum.get(a.baby_id, [])))
    return out


def _tahmini_kapanis(db, kayit, bas: datetime, bebek_kayitlari: list):
    """Kapsayan kayıt yokken kapanış anı ve gerekçesi."""
    nap_dk, gece_dk = _bant_sureleri(db, kayit.baby_id)
    sure_dk = nap_dk if kayit.type == "nap" else gece_dk
    adaylar = [(bas + timedelta(minutes=sure_dk),
                f"bant süresi +{sure_dk} dk"),
               (bas + timedelta(hours=TERK_SAAT), f"tavan +{TERK_SAAT} saat")]
    sonraki = _sonraki_kayit_bas(bebek_kayitlari, bas)
    if sonraki is not None:
        adaylar.append((sonraki, "sonraki kaydın başlangıcı"))
    kapanis, gerekce = min(adaylar, key=lambda x: x[0])
    if kapanis <= bas:
        return bas + timedelta(minutes=ASGARI_DK), f"asgari +{ASGARI_DK} dk"
    return kapanis, gerekce


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gun", type=int, default=7,
                    help="Kaç günlük pencere taransın (varsayılan 7)")
    ap.add_argument("--rapor", action="store_true",
                    help="Yalnız sayımları yazdır (bozuk kayıt türleri dahil)")
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

        for r, kapanis, _g in adaylar:
            r.ended_at = kapanis
        db.commit()
        print(f"\n{len(adaylar)} açık sayaç kaydı kapatıldı.")
    finally:
        db.close()


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
