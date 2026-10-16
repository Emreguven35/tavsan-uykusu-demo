"""
Beta kapanışı — kayıtlı kullanıcılara süreli hediye premium (premium_haklari).

BETA_MODE kapatılmadan ÖNCE çalıştırılır: beta süresince herkes premium'du;
kapanınca gerçek kademeye düşerler. Bu betik her gerçek kullanıcıya `--ay N`
aylık elle verilmiş hak (kaynak "manual") yazar; beta kapanınca da N ay premium
kalırlar.

Kimler ALMAZ:
  - Test/demo/sistem hesapları: premium.KURUCU_TEST_ALANLARI (@example.com,
    @tavsanduman.com, @tavsansmoke.com, .invalid resmi hesap, DEMO_EPOSTALARI)
    ve env KURUCU_HARIC ile elle çıkarılanlar.
  - Bu hediyeyi zaten almış olan ("Beta teşekkürü" açıklamalı hak satırı var,
    süresi ne olursa olsun) — betik iki kez çalışsa da hak üst üste binmez.
  - Elinde hedef bitişten DAHA GEÇ biten bir hakkı olan (ör. süresiz hediye).
Mağaza abonesi olanlar ALIR (beta kullanıcısıdır); listede ayrıca işaretlenir.

Bitiş: bugünün (Türkiye) N ay sonrası, o gün DAHİL (ertesi gün 00:00 TR'de biter).
Ay sonu taşması kırpılır (31 Ocak + 1 ay → 28/29 Şubat).

Varsayılan KURU ÇALIŞMA: kaç kişiye, hangi bitişle verileceğini listeler
(e-postalar maskeli), hiçbir şey yazmaz. `--uygula` tek işlemde yazar.

Çalıştırma (konteynerde, canlı DB):
    railway ssh "cd /app && PYTHONIOENCODING=utf-8 /opt/venv/bin/python tools/beta_kapanis.py --ay 3"
    railway ssh "cd /app && PYTHONIOENCODING=utf-8 /opt/venv/bin/python tools/beta_kapanis.py --ay 3 --uygula"
(Konteynerde yoksa: ... /opt/venv/bin/python - --ay 3 < tools/beta_kapanis.py)
"""
from __future__ import annotations

import argparse
import calendar
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

_dosya = Path(globals().get("__file__") or "<stdin>")
ROOT = _dosya.resolve().parent.parent if _dosya.is_file() else Path.cwd()
sys.path.insert(0, str(ROOT))


def ay_ekle(g: date, ay: int) -> date:
    y, m = divmod(g.month - 1 + ay, 12)
    yil, ayi = g.year + y, m + 1
    return date(yil, ayi, min(g.day, calendar.monthrange(yil, ayi)[1]))


ACIKLAMA_ONEKI = "Beta teşekkürü"


def aciklama(ay: int) -> str:
    return f"{ACIKLAMA_ONEKI} ({ay} ay)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Beta kapanışı: süreli hediye premium")
    ap.add_argument("--ay", type=int, required=True, help="kaç ay premium (1-24)")
    ap.add_argument("--uygula", action="store_true", help="yaz (verilmezse yalnız listeler)")
    args = ap.parse_args(argv)
    if not 1 <= args.ay <= 24:
        print("DUR: --ay 1 ile 24 arasında olmalı"); return 1

    os.environ.pop("SENTRY_DSN", None)
    import logging
    logging.disable(logging.CRITICAL)
    from api.config import get_settings
    from api.db import SessionLocal
    from api.models import CommunityProfile, PremiumHak, User
    from api.services import premium as premium_svc
    from api.zaman import TR, bugun_tr

    bugun = bugun_tr()
    bitis_gunu = ay_ekle(bugun, args.ay)
    simdi = datetime.now(timezone.utc)
    bitis = datetime.combine(bitis_gunu + timedelta(days=1), time(0, 0), tzinfo=TR
                             ).astimezone(timezone.utc)
    etiket = aciklama(args.ay)
    haric = premium_svc.kurucu_haric_epostalar()

    s = SessionLocal()
    try:
        verilecek, atlanan = [], {"test/demo/sistem": 0, "KURUCU_HARIC": 0,
                                  "zaten aldı": 0, "daha geç biten hakkı var": 0}
        moderator = {p.user_id for p in s.query(CommunityProfile)
                     .filter(CommunityProfile.is_moderator.is_(True)).all()}
        for u in s.query(User).order_by(User.created_at).all():
            eposta = (u.email or "").lower()
            if eposta.endswith(premium_svc.KURUCU_TEST_ALANLARI):
                atlanan["test/demo/sistem"] += 1
                continue
            if eposta in haric:
                atlanan["KURUCU_HARIC"] += 1
                continue
            haklar = s.query(PremiumHak).filter(PremiumHak.user_id == u.id).all()
            if any((h.aciklama or "").startswith(ACIKLAMA_ONEKI) for h in haklar):
                atlanan["zaten aldı"] += 1
                continue
            if any(premium_svc._utc(h.bitis) >= bitis for h in haklar):
                atlanan["daha geç biten hakkı var"] += 1
                continue
            verilecek.append((u, premium_svc.aktif_abonelik(s, u, simdi) is not None,
                              u.id in moderator))

        print(f"BETA_MODE şu an: {'AÇIK' if get_settings().beta_mode else 'KAPALI'}")
        print(f"Hediye: {args.ay} ay premium — başlangıç şimdi, bitiş {bitis_gunu.isoformat()} "
              f"(dahil; {bitis.isoformat()} UTC'de biter), açıklama '{etiket}'")
        print(f"Verilecek: {len(verilecek)} kullanıcı")
        for u, abone, mod in verilecek:
            notlar = [n for n, var in (("mağaza abonesi", abone), ("moderatör", mod)) if var]
            print(f"  {premium_svc.maskele(u.email):32s} kayıt {premium_svc._utc(u.created_at).date()}"
                  + (f"  [{', '.join(notlar)}]" if notlar else ""))
        print("Atlanan: " + ", ".join(f"{k} {v}" for k, v in atlanan.items()))
        if not args.uygula:
            print("KURU ÇALIŞMA — hiçbir şey yazılmadı (yazmak için --uygula)")
            return 0
        for u, _, _ in verilecek:
            s.add(PremiumHak(user_id=u.id, baslangic=simdi, bitis=bitis, veren_user_id=None,
                             aciklama=etiket, created_at=simdi))
        s.commit()
        print(f"YAZILDI: {len(verilecek)} hak satırı")
        # Kontrol: yazılanların kademesi beta'dan bağımsız premium (manual) olmalı.
        ayar = get_settings()
        onceki, ayar.beta_mode = ayar.beta_mode, False
        try:
            yanlis = [u.id for u, _, _ in verilecek
                      if premium_svc.durum(s, u)["source"] not in ("manual", "store")]
        finally:
            ayar.beta_mode = onceki
        print(f"KONTROL (beta kapalı varsayımıyla): {len(verilecek) - len(yanlis)}/{len(verilecek)} premium")
        return 0 if not yanlis else 1
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
