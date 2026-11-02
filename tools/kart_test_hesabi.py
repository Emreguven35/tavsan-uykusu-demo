"""
Paylaşım kartı TEST HESABI — kur / tazele / aktif bebeği değiştir (2026-11-02).

YALNIZ bu hesaba dokunur: KART_TEST_EMAIL (env) e-postalı kullanıcı. Hesap yoksa
oluşturur; varsa ve işaretli değilse (premium_haklari.aciklama='kart_test' yok)
HİÇBİR ŞEY YAPMAZ. Kayıt ucu kullanılmaz (hoş geldin e-postası gitmez).

İŞARET (tek komutla silinebilsin diye, bkz. tools/kart_test_sil.py):
  premium_haklari.aciklama = 'kart_test'    (süresiz manuel premium — kademe "premium")
  consents.kaynak          = 'kart_test'
  sleep_logs.client_id     = 'kart_test-…'
  e-posta @example.com     → denetim/bebek numaraları ve kurucu üyelik DIŞI (TEST_ALANLARI)

BEBEKLER (bugüne göre her çalıştırmada yeniden yazılır):
  Can    ~10 ay. 13 günlük eğitim bitti (başlangıç bugün−16, 13. gün bugün−4),
         egitim_degerlendirmeleri sonuc='mezun'. Eğitim öncesi 3 gece bölünmüş
         (3 parça + 3 uyanma), son geceler tek parça ve uyanmasız.
  Defne  ~9 ay, eğitim yok. Son 15 gün tam gün; geçen hafta 2–3 uyanma/gece,
         bu hafta 0–1 → haftalık kartta vurgu_metni dolu.
  Aktif bebek = EN YENİ bebek (deps.aktif_bebek). Varsayılan Defne; --aktif can
  ile Can'ın created_at'i öne alınır (parametresiz mezuniyet kartı için).

Çalıştırma (konteynerde, canlı DB; env KART_TEST_EMAIL / KART_TEST_SIFRE):
  python tools/kart_test_hesabi.py              # kuru: ne yapacağını yazar
  python tools/kart_test_hesabi.py --uygula     # kur / verileri bugüne göre tazele
  python tools/kart_test_hesabi.py --uygula --aktif can|defne
E-posta ve şifre ekrana YAZILMAZ.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent if "__file__" in globals() and \
    not str(globals().get("__file__", "")).startswith("<") else Path.cwd()
sys.path.insert(0, str(ROOT))

ISARET = "kart_test"
TR = timezone(timedelta(hours=3))


def _an(g: date, hhmm: str) -> datetime:
    s, d = map(int, hhmm.split(":"))
    return datetime(g.year, g.month, g.day, s, d, tzinfo=TR).astimezone(timezone.utc)


def _artir(hhmm: str, dk: int) -> str:
    s, d = map(int, hhmm.split(":"))
    t = (s * 60 + d + dk) % 1440
    return f"{t // 60:02d}:{t % 60:02d}"


# --- Veri üretimi (deterministik, rastgelelik yok) ------------------------------
def defne_kayitlari(bugun: date) -> list[tuple]:
    """(tip, başlangıç, bitiş|None). k = bugünden kaç gün önce; k=0 bu sabah biten gece."""
    out = []
    uyanma_saatleri = ["23:10", "02:20", "04:40"]
    for k in range(15, -1, -1):
        g = bugun - timedelta(days=k)
        bu_hafta = 1 <= k <= 7 or k == 0
        sap = (k * 7) % 15                                   # 0..14 dk oynama
        if bu_hafta:
            yatis, sabah = _artir("19:35", sap), _artir("06:35", sap // 2)
            n = 1 if k in (2, 4, 7) else 0                   # 3 uyanma / 7 gece
        else:
            yatis, sabah = _artir("20:05", sap), _artir("06:15", sap // 2)
            n = 3 if k % 2 == 0 else 2                       # 2–3 uyanma / gece
        out.append(("sleep", _an(g - timedelta(days=1), yatis), _an(g, sabah)))
        for u in uyanma_saatleri[:n]:
            gun = g - timedelta(days=1) if u >= "19:00" else g
            out.append(("night_wake", _an(gun, u), None))   # nokta olay (mobil gibi)
        if k >= 1:                                           # bugünün gündüzü henüz yok
            out.append(("nap", _an(g, _artir("09:40", sap)), _an(g, _artir("10:50", sap))))
            out.append(("nap", _an(g, _artir("14:00", sap)), _an(g, _artir("15:15", sap))))
    return out


def can_kayitlari(bugun: date) -> tuple[list[tuple], date, date]:
    """Eğitim: başlangıç bugün−16, 13. gün (mezuniyet) bugün−4."""
    bas = bugun - timedelta(days=16)
    mezun = bas + timedelta(days=12)
    out = []
    g = bas - timedelta(days=2)
    while g <= bugun:
        onceki = g - timedelta(days=1)
        gece_no = (g - bas).days                 # bu sabah biten gece eğitimin kaçıncı gecesi
        sap = ((g - bas).days * 5) % 12
        if gece_no <= 0:                         # eğitim öncesi: bölünmüş gece
            out += [("sleep", _an(onceki, "20:30"), _an(g, "00:40")),
                    ("night_wake", _an(g, "00:40"), None),
                    ("sleep", _an(g, "01:20"), _an(g, "04:00")),
                    ("night_wake", _an(g, "02:35"), None),
                    ("night_wake", _an(g, "04:00"), None),
                    ("sleep", _an(g, "04:30"), _an(g, "06:20"))]
        else:                                    # eğitim: tek parça, uyanma azalır
            n = [3, 3, 2, 2, 2, 1, 1, 1, 1, 0][gece_no - 1] if gece_no <= 10 else 0
            out.append(("sleep", _an(onceki, _artir("19:40", sap)), _an(g, _artir("06:35", sap))))
            for u in ["23:20", "02:10", "04:30"][:n]:
                gun = onceki if u >= "19:00" else g
                out.append(("night_wake", _an(gun, u), None))
        if g < bugun:
            out.append(("nap", _an(g, _artir("09:50", sap)), _an(g, _artir("11:00", sap))))
            out.append(("nap", _an(g, _artir("14:10", sap)), _an(g, _artir("15:20", sap))))
        g += timedelta(days=1)
    return out, bas, mezun


# --- DB -------------------------------------------------------------------------
def isaretli_mi(db, user) -> bool:
    from api.models import PremiumHak
    return db.query(PremiumHak).filter(PremiumHak.user_id == user.id,
                                       PremiumHak.aciklama == ISARET).count() > 0


def _bebek(db, user, ad: str, dogum: date, **alan):
    from api.models import Baby
    b = db.query(Baby).filter(Baby.user_id == user.id, Baby.name == ad).first()
    if b is None:
        b = Baby(user_id=user.id, name=ad, birth_date=dogum)
        db.add(b)
        db.flush()
    b.birth_date = dogum
    for k, v in alan.items():
        setattr(b, k, v)
    return b


def _kayitlari_yaz(db, user, bebek, kayitlar: list[tuple]) -> int:
    from api.models import SleepLog
    (db.query(SleepLog).filter(SleepLog.baby_id == bebek.id)
     .delete(synchronize_session=False))
    for tip, bas, bit in kayitlar:
        db.add(SleepLog(user_id=user.id, baby_id=bebek.id, type=tip, started_at=bas,
                        ended_at=bit, client_id=f"{ISARET}-{uuid.uuid4().hex[:16]}"))
    return len(kayitlar)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true")
    ap.add_argument("--aktif", choices=("defne", "can"), default=None)
    a = ap.parse_args()

    eposta = (os.getenv("KART_TEST_EMAIL") or "").strip().lower()
    sifre = os.getenv("KART_TEST_SIFRE") or ""
    if not eposta.endswith("@example.com") or len(sifre) < 8:
        print("HATA: KART_TEST_EMAIL (@example.com) ve KART_TEST_SIFRE (≥8) env'de olmalı")
        return 2

    from api.db import SessionLocal
    from api.models import Consent, EgitimDegerlendirme, PremiumHak, User
    from api.models.user import DEFAULT_NOTIFICATION_PREFS
    from api.services.security import hash_password
    from api.config import KVKK_METIN_SURUMLERI
    from api.zaman import bugun_tr

    bugun = bugun_tr()
    simdi = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == eposta).first()
        if user is not None and not isaretli_mi(db, user):
            print("DUR: bu e-postada İŞARETSİZ bir hesap var — dokunulmadı.")
            return 3
        defne_k = defne_kayitlari(bugun)
        can_k, bas, mezun = can_kayitlari(bugun)
        print(f"bugün {bugun} | hesap {'VAR (tazelenecek)' if user else 'YOK (oluşturulacak)'}")
        print(f"Defne: {len(defne_k)} kayıt | Can: {len(can_k)} kayıt, eğitim {bas} → mezun {mezun}")
        if not a.uygula:
            print("KURU — yazmak için --uygula")
            return 0

        if user is None:
            user = User(email=eposta, password_hash=hash_password(sifre),
                        notification_prefs={k: False for k in DEFAULT_NOTIFICATION_PREFS})
            db.add(user)
            db.flush()
            db.add(PremiumHak(user_id=user.id, baslangic=simdi,
                              bitis=datetime(2099, 12, 31, tzinfo=timezone.utc),
                              aciklama=ISARET, created_at=simdi))
            for tur in ("aydinlatma", "acik_riza_saglik"):
                db.add(Consent(user_id=user.id, tur=tur, metin_surumu=KVKK_METIN_SURUMLERI[tur],
                               onay=True, zaman=simdi, kaynak=ISARET))
        else:
            user.password_hash = hash_password(sifre)        # .env ile senkron kalsın

        from api.models import Baby
        yeni = db.query(Baby).filter(Baby.user_id == user.id).count() < 2
        can = _bebek(db, user, "Can", bugun - timedelta(days=305), gender="male",
                     night_wakes=3, night_feeds=1, sleep_method="holding",
                     training_started_at=bas, training_completed_at=mezun)
        defne = _bebek(db, user, "Defne", bugun - timedelta(days=275), gender="female",
                       night_wakes=2, night_feeds=1, sleep_method="holding",
                       training_started_at=None, training_completed_at=None)
        n_d = _kayitlari_yaz(db, user, defne, defne_k)
        n_c = _kayitlari_yaz(db, user, can, can_k)

        (db.query(EgitimDegerlendirme).filter(EgitimDegerlendirme.baby_id == can.id)
         .delete(synchronize_session=False))
        db.add(EgitimDegerlendirme(user_id=user.id, baby_id=can.id, gun=mezun, deneme_no=1,
                                   egitim_baslangic=bas, egitim_gunu=13, kendi_donuyor=True,
                                   yatir_cik=True, uzun_aglama=None, sonuc="mezun"))

        # Sıra HER kurulumda açıkça yazılır: Postgres'te now() işlem başlangıcıdır,
        # aynı işlemde eklenen iki bebeğin created_at'i eşit olur ve "en yeni"
        # belirsizleşir (ilk canlı kurulumda Can seçildi).
        aktif = a.aktif or "defne"
        if a.aktif or yeni:
            once, sonra = (can, defne) if aktif == "defne" else (defne, can)
            once.created_at = simdi - timedelta(minutes=2)
            sonra.created_at = simdi - timedelta(minutes=1)
        db.commit()
        print(f"YAZILDI: Defne {n_d} kayıt, Can {n_c} kayıt + mezun değerlendirmesi; "
              f"aktif bebek: {aktif if (a.aktif or yeni) else '(değişmedi)'}")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
