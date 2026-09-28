"""
Kayıt → eğitim akışı teşhisi (salt okur, hiçbir satırı değiştirmez).

Üç soruya gerçek veriyle cevap verir:
  A) Sabah sorusundan gelmiş olabilecek SIFIR SÜRELİ uyku kayıtları
     (sleep/nap, başlangıç = bitiş, yerel 04:00–12:00) — son N gün.
  B) "Tekrar gönder" — sunucu batch'te kaydı neden elemiş olabilir? Sebepler
     DB'de saklanmıyor; burada eleme koşulları (gelecek zaman, ters kayıt,
     sahipsiz bebek) ile tutarlı iz aranır ve açık kayıt durumu dökülür.
  C) 7 günden eski AÇIK (ended_at NULL) kayıtları olan kullanıcılar ve
     bugünkü GET /logs?date= listesinin bebek başına boyu (eski/yeni filtre).

KİŞİSEL VERİ YOK: bebekler denetim raporundaki kalıcı numarayla ("Bebek 7")
anılır; ad, e-posta, doğum tarihi yazılmaz.

Prod (konteyner içinde):
    railway ssh "cd /app && PYTHONIOENCODING=utf-8 /opt/venv/bin/python scripts/kayit_teshis.py"
    ... scripts/kayit_teshis.py --yeniden-hesapla 18 20   # bu bebeklerin bugünkü
        planı yeniden hesaplanır (plan satırı güncellenir, kayıtlara dokunulmaz)
        ve bugün listesi + çizelge özeti basılır
"""
import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                                # noqa: E402

load_dotenv(ROOT / ".env")

from api.db import SessionLocal                               # noqa: E402
from api.models import Baby, SleepLog, User                   # noqa: E402
from api.routers.logs import gun_filtresi                     # noqa: E402
from api.services import denetim                              # noqa: E402
from api.zaman import bugun_tr                                # noqa: E402

TZ = 180
UYKU = ("sleep", "nap", "sekerleme")


def _utc(t):
    return None if t is None else (t if t.tzinfo else t.replace(tzinfo=timezone.utc))


def _yerel(t) -> datetime:
    return _utc(t) + timedelta(minutes=TZ)


def _hhmm(t) -> str:
    return "—" if t is None else _yerel(t).strftime("%m-%d %H:%M")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gun", type=int, default=3)
    ap.add_argument("--yeniden-hesapla", type=int, nargs="+", metavar="NO",
                    help="bu bebek numaralarının bugünkü planını yeniden hesapla")
    a = ap.parse_args()
    if a.yeniden_hesapla:
        return yeniden_hesapla(a.yeniden_hesapla)

    db = SessionLocal()
    try:
        no = denetim.bebek_numaralari(db)
        simdi = datetime.now(timezone.utc)
        bas = simdi - timedelta(days=a.gun)
        bebekler = {b.id: b for b in db.query(Baby).filter(Baby.id.in_(list(no)))}
        kullanici = {u.id: u for u in db.query(User).filter(
            User.id.in_([b.user_id for b in bebekler.values()]))}

        def etiket(bid) -> str:
            b = bebekler.get(bid)
            if b is None:
                return "test/bilinmeyen"
            metin, yas, bant = denetim._yas_metni(b)
            hafta = int(b.dogum_haftasi or 40)
            u = kullanici.get(b.user_id)
            sur = getattr(u, "app_version", None) if u else None
            ay = f"{yas['duzeltilmis_ay']:.1f} ay düz." if yas else "yaş ?"
            takv = f"/{yas['gercek_ay']:.1f} gerçek" if yas else ""
            return (f"Bebek {no[bid]} ({ay}{takv}, {hafta} hf, "
                    f"{(bant or {}).get('id') or '-'}{'.' + bant['varyant'] if (bant or {}).get('varyant') else ''}"
                    f", sürüm {sur or '?'})")

        # ---- A ----------------------------------------------------------
        print(f"=== A) Sıfır süreli sleep/nap, yerel 04:00–12:00, son {a.gun} gün ===")
        rows = (db.query(SleepLog)
                .filter(SleepLog.baby_id.in_(list(no)),
                        SleepLog.started_at >= bas,
                        SleepLog.type.in_(UYKU),
                        SleepLog.ended_at.isnot(None)).all())
        sifir = [r for r in rows if _utc(r.ended_at) == _utc(r.started_at)
                 and 4 * 60 <= _yerel(r.started_at).hour * 60
                 + _yerel(r.started_at).minute < 12 * 60]
        print(f"toplam {len(sifir)} kayıt · tip: {dict(Counter(r.type for r in sifir))}"
              f" · bebek: {len({r.baby_id for r in sifir})}")
        for r in sorted(sifir, key=lambda r: (no[r.baby_id], r.started_at)):
            print(f"  {etiket(r.baby_id)}  {r.type:6} {_hhmm(r.started_at)}"
                  f"  notes={('var' if r.notes else '-')}")
        tum_sifir = [r for r in rows if _utc(r.ended_at) == _utc(r.started_at)]
        print(f"(tüm saatlerde sıfır süreli uyku: {len(tum_sifir)}; "
              f"04–12 dışı: {len(tum_sifir) - len(sifir)})")
        # nap sıfır — eski 'atlandı' biçimi hâlâ geliyor mu?
        nap_sifir = [r for r in tum_sifir if r.type == "nap"]
        print(f"(sıfır süreli nap: {len(nap_sifir)}; saatleri: "
              f"{sorted(_yerel(r.started_at).strftime('%H:%M') for r in nap_sifir)})")
        skip = (db.query(SleepLog).filter(SleepLog.baby_id.in_(list(no)),
                                          SleepLog.started_at >= bas,
                                          SleepLog.type == "nap_skipped").count())
        print(f"(nap_skipped kaydı: {skip})")

        # ---- B ----------------------------------------------------------
        print(f"\n=== B) Batch eleme izleri, son {a.gun} gün ===")
        tum = (db.query(SleepLog).filter(SleepLog.baby_id.in_(list(no)),
                                         SleepLog.created_at >= bas).all())
        gelecek = [r for r in tum if _utc(r.started_at) > _utc(r.created_at)
                   + timedelta(minutes=5)]
        print(f"yazılmış kayıt: {len(tum)}; oluşturulma anından >5 dk ileri "
              f"başlangıçlı (tolerans sınırında kabul edilmiş): {len(gelecek)}")
        # Bebeği silinmiş / başka bebeğe ait client_id: sahipsiz bebek izi.
        tum_bebek = {b.id: b for b in db.query(Baby)}
        kullanici_bebek = defaultdict(list)
        for b in tum_bebek.values():
            kullanici_bebek[b.user_id].append(b)
        print("birden çok bebeği olan gerçek kullanıcılar (not_owned riski):")
        for uid, bl in kullanici_bebek.items():
            if len(bl) > 1 and any(b.id in no for b in bl):
                print("   ", ", ".join(etiket(b.id) for b in bl))
        # Açık kayıt ve bugünün son kayıtları — bebek başına
        print("\nbebek başına bugünkü kayıtlar (yerel):")
        bugun = bugun_tr()
        for bid in sorted(no, key=lambda x: no[x]):
            kay = (db.query(SleepLog).filter(SleepLog.baby_id == bid,
                                             SleepLog.started_at >= simdi - timedelta(hours=30))
                   .order_by(SleepLog.started_at).all())
            if not kay:
                continue
            print(f"  {etiket(bid)}")
            for r in kay:
                sure = ("açık" if r.ended_at is None else
                        f"{int((_utc(r.ended_at) - _utc(r.started_at)).total_seconds() // 60)} dk")
                print(f"     {r.type:11} {_hhmm(r.started_at)} → {_hhmm(r.ended_at)}"
                      f"  ({sure})  yazıldı {_hhmm(r.created_at)}"
                      f"  güncellendi {_hhmm(getattr(r, 'updated_at', None))}"
                      f"  cid={'var' if r.client_id else '-'}")

        # ---- C ----------------------------------------------------------
        print("\n=== C) 7 günden eski AÇIK kayıtlar ===")
        acik = (db.query(SleepLog).filter(SleepLog.baby_id.in_(list(no)),
                                          SleepLog.ended_at.is_(None),
                                          SleepLog.type.in_(UYKU),
                                          SleepLog.started_at < simdi - timedelta(days=7))
                .all())
        kul = defaultdict(int)
        for r in acik:
            kul[r.baby_id] += 1
        print(f"{len(acik)} kayıt · {len({bebekler[b].user_id for b in kul})} kullanıcı")
        for bid, n in sorted(kul.items(), key=lambda x: -x[1]):
            print(f"  {etiket(bid)}: {n}")
        tum_acik = (db.query(SleepLog).filter(SleepLog.baby_id.in_(list(no)),
                                              SleepLog.ended_at.is_(None),
                                              SleepLog.type.in_(UYKU)).all())
        print(f"(her yaşta açık uyku kaydı: {len(tum_acik)}; 16 saatten eski: "
              f"{sum(1 for r in tum_acik if simdi - _utc(r.started_at) > timedelta(hours=16))})")

        print(f"\nBugünkü GET /logs?date={bugun} listesinin boyu (bebek başına, >3):")
        for bid in sorted(no, key=lambda x: no[x]):
            n = (db.query(SleepLog).filter(SleepLog.baby_id == bid,
                                           gun_filtresi(bugun)).count())
            if n > 3:
                print(f"  {etiket(bid)}: {n}")
    finally:
        db.close()
    return 0


def yeniden_hesapla(numaralar: list[int]) -> int:
    """Bebek N'nin bugünkü planını motorla yeniden kur ve özetle (kişisel veri yok)."""
    from api.routers.logs import _bebek_bantlari, _kategorili
    from api.services import plan_service

    db = SessionLocal()
    try:
        no = denetim.bebek_numaralari(db)
        ters = {v: k for k, v in no.items()}
        bugun = bugun_tr()
        for n in numaralar:
            bid = ters.get(n)
            if bid is None:
                print(f"Bebek {n}: yok")
                continue
            baby = db.get(Baby, bid)
            user = db.get(User, baby.user_id)
            _m, yas, bant = denetim._yas_metni(baby)
            plan = plan_service.ensure_today_plan(db, user, baby)
            c = (plan.content or {}) if plan else {}
            ad = c.get("adaptation") or {}
            print(f"\n=== Bebek {n} ({yas['duzeltilmis_ay'] if yas else '?'} ay düz., "
                  f"{int(baby.dogum_haftasi or 40)} hf, {(bant or {}).get('id')}"
                  f"{'.' + bant['varyant'] if (bant or {}).get('varyant') else ''}) "
                  f"— plan {plan.plan_date if plan else '-'} ===")
            print(f"sabah: gerçek={ad.get('sabah_uyanis_gercek')} "
                  f"kaynak={ad.get('sabah_uyanis_kaynak')} "
                  f"şablon hedefi={ad.get('sabah_uyanis_hedef')} "
                  f"yaş hedefi={ad.get('sabah_hedefi')}")
            for b in c.get("schedule") or []:
                print(f"   {b.get('key'):10} {b.get('time')}–{b.get('end')} "
                      f"kaynak={b.get('kaynak')}"
                      f"{' devam' if b.get('devam') else ''}"
                      f"{' GEÇ YATIŞ ' + str(b.get('gec_yatis')) if b.get('gec_yatis') else ''}")
            print(f"sıradaki: {ad.get('siradaki_blok')}")
            print(f"yok sayılan: {[y.get('kod') for y in ad.get('yok_sayilan_kayitlar') or []]}")
            for u in ad.get("uyarilar") or []:
                print(f"   uyarı: {u}")
            satirlar = (db.query(SleepLog).filter(SleepLog.baby_id == bid,
                                                  gun_filtresi(bugun))
                        .order_by(SleepLog.started_at).all())
            bantlar = _bebek_bantlari(db, {bid})
            print(f"bugün listesi ({len(satirlar)} kayıt):")
            for r in satirlar:
                k = _kategorili(r, bantlar.get(bid))
                print(f"   {r.type:11} {_hhmm(r.started_at)} → {_hhmm(r.ended_at)} "
                      f"{k.kategori_etiket or ''}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
