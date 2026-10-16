"""
App Review demo hesabını tazele — eğitimin 5. günü + son 5 günün kayıtları bugüne göre.

İnceleme gönderiminden ÖNCE çalıştırılır (eğitim günü takvimle ilerliyor; demo
hesap 2026-10-13'te 5. gündeydi). YALNIZ demo hesaba dokunur:
config.DEMO_EPOSTALARI'daki e-posta + elle verilmiş premium hakkı + tek bebek.

Yaptıkları (--uygula ile):
  1. training_started_at = bugün − 4 (eğitimin 5. günü), training_completed_at = boş
  2. Bebeğin TÜM uyku kayıtları silinir; son 5 günün gece/gündüz kayıtları ve
     gece uyanmaları bugüne göre yeniden yazılır (POST /logs/batch — uygulamanın
     kendi yolu, plan yeniden hesaplanır)
  3. Sor sohbeti silinip 3 hazır soru-cevap eğitimin 2./3./4. gününe yeniden
     tarihlenir (model çağrısı YOK)
  4. /plans/today ile bugünün planı hazırlanır ve kontrol edilir
Hesap, şifre, premium hakkı, onaylar, topluluk profili DEĞİŞMEZ. E-posta gitmez.

Çalıştırma (konteynerde, canlı DB):
    railway ssh "cd /app && PYTHONIOENCODING=utf-8 /opt/venv/bin/python tools/demo_tazele.py"            # kuru
    railway ssh "cd /app && PYTHONIOENCODING=utf-8 /opt/venv/bin/python tools/demo_tazele.py --uygula"   # yazar
(api.main import'u libstdc++ isterse LD_LIBRARY_PATH'i ana süreçten al — bkz.
deploy notları.)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EGITIM_GUNU = 5

# (gün farkı, yatış, sabah, [gece uyanmaları], şekerleme1, şekerleme2) —
# eğitim ilerledikçe gece uyanmaları azalır (eğitimin 1. gününden bir gece önce başlar).
GUNLER = [
    (5, "19:50", "06:35", ["23:10", "02:20", "04:40"], ("09:40", "10:45"), ("14:10", "15:20")),
    (4, "19:40", "06:30", ["22:55", "01:50", "04:30"], ("09:35", "10:20"), ("14:00", "15:05")),
    (3, "19:45", "06:40", ["23:30", "03:10"], ("09:45", "10:55"), ("14:05", "15:15")),
    (2, "19:35", "06:30", ["00:40", "04:15"], ("09:30", "10:40"), ("13:55", "15:10")),
    (1, "19:40", "06:45", ["03:20"], ("09:40", "10:50"), ("14:00", "15:15")),
]

# Hazır Sor soru-cevapları: (kaç gün önce, saat, soru, cevap). Gün farkları
# eğitimin 2., 3. ve 4. gününe denk gelir (bugün 5. gün) — cevaplardaki sayılar
# o günlerin kuralları (gece ilk bekleme 10 / 20 dk, kısa gündüz 15+30).
SOHBET = [
    (3, "21:40", "Deniz gece uyandığında ne kadar beklemeliyim?",
     "Bu gece Deniz uyandığında önce odaya girmeden dışarıda 10 dakika bekleyin; aynı gece "
     "yeniden uyanırsa her seferinde 1 dakika daha uzun bekleyin. Sonra içeri girip beşik "
     "yanında pat-pat, pış-pış ile 45 dakika uyutmayı deneyin; histerik ağlama yoksa kucağa "
     "almayın. Uyumazsa odada 15 dakikalık kısa bir rutin yapın (rutinde beslenme olmasın) ve "
     "yeniden deneyin. Bu gece destek vererek uyutma yok; Deniz uyuyana kadar bu döngüyü "
     "sürdürün. Zorlandığınız an çok doğal — tutarlı kaldıkça geceler kısalacak."),
    (2, "10:20", "Sabah uykusu 30 dakikada bitti, ne yapayım?",
     "Kısa gündüz uykusunda bugün önce dışarıda 15 dakika bekleyin, sonra içeri girip 30 "
     "dakika daha uyutmayı deneyin (toplam 45 dakika). Uyumazsa uykuyu bitmiş sayın ve bir "
     "sonraki uykuyu planınızdaki saate göre yapın; arayı kapatmak için erkene çekmeyin."),
    (1, "19:05", "Bugün oda ortasına geçtik, ne değişiyor?",
     "Eğitimin 4. gününden itibaren oda ortası aşamasındasınız. Deniz'i yatırdıktan sonra "
     "odanın ortasında durun; temassız pış-pış ya da ninniyle sakinleştirin. Çok yoğun "
     "ağlamada yanına gidip temas edebilir, sonra yerinize dönersiniz. Gece uyanmasında bu "
     "gece önce dışarıda 20 dakika bekleyin. Rutin, yatış saati ve tutarlılık aynı kalıyor."),
]


def _an(g: date, saat: str):
    from api.zaman import TR
    h, m = map(int, saat.split(":"))
    return datetime(g.year, g.month, g.day, h, m, tzinfo=TR)


def kayitlar(bugun: date, baby_id: str) -> list[dict]:
    out = []
    for fark, yatis, sabah, uyanma, n1, n2 in GUNLER:
        g = bugun - timedelta(days=fark)
        ertesi = g + timedelta(days=1)
        for i, (bas, bit) in enumerate((n1, n2), 1):
            out.append({"type": "nap", "started_at": _an(g, bas).isoformat(),
                        "ended_at": _an(g, bit).isoformat(),
                        "client_id": f"demo-{g.isoformat()}-nap{i}"})
        out.append({"type": "sleep", "started_at": _an(g, yatis).isoformat(),
                    "ended_at": _an(ertesi, sabah).isoformat(),
                    "client_id": f"demo-{g.isoformat()}-gece"})
        for j, saat in enumerate(uyanma, 1):
            gun_ = g if saat >= "12:00" else ertesi
            out.append({"type": "night_wake", "started_at": _an(gun_, saat).isoformat(),
                        "client_id": f"demo-{g.isoformat()}-uyanma{j}"})
    for x in out:
        x["baby_id"] = baby_id
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--uygula", action="store_true", help="yaz (verilmezse yalnız gösterir)")
    args = ap.parse_args(argv)

    os.environ.pop("SENTRY_DSN", None)
    import logging
    logging.disable(logging.CRITICAL)
    from fastapi.testclient import TestClient
    from api.config import DEMO_EPOSTALARI
    from api.db import SessionLocal
    from api.main import app
    from api.models import Baby, ChatMessage, SleepLog, User
    from api.services import premium as premium_svc
    from api.services.security import create_access_token
    from api.zaman import bugun_tr

    bugun = bugun_tr()
    from api.zaman import simdi_tr
    if simdi_tr().hour < 7:
        # Son gecenin kaydı bu sabah 06:45'te biter — daha erken yazılırsa gelecek
        # tarihli kayıt olurdu.
        print("DUR: Türkiye saatiyle 07:00'den sonra çalıştırın"); return 1
    s = SessionLocal()
    try:
        user = s.query(User).filter(User.email == DEMO_EPOSTALARI[0]).one_or_none()
        if user is None:
            print("DUR: demo hesap yok"); return 1
        if premium_svc.aktif_hak(s, user) is None:
            print("DUR: demo hesabın elle verilmiş premium hakkı yok — bu hesap mı?"); return 1
        bebekler = s.query(Baby).filter(Baby.user_id == user.id).all()
        if len(bebekler) != 1:
            print(f"DUR: demo hesapta {len(bebekler)} bebek var (1 bekleniyor)"); return 1
        bebek = bebekler[0]
        uid, bid = user.id, bebek.id
        eski_log = s.query(SleepLog).filter(SleepLog.baby_id == bid).count()
        eski_sohbet = s.query(ChatMessage).filter(ChatMessage.user_id == uid).count()
        yeni_bas = bugun - timedelta(days=EGITIM_GUNU - 1)
        yeni = kayitlar(bugun, str(bid))
        print(f"Demo hesap: {user.email} | bebek {bebek.name}")
        print(f"  training_started_at: {bebek.training_started_at} → {yeni_bas} (eğitimin {EGITIM_GUNU}. günü)")
        print(f"  uyku kayıtları: {eski_log} silinecek → {len(yeni)} yazılacak "
              f"({(bugun - timedelta(days=5)).isoformat()} … {bugun.isoformat()} sabahı)")
        print(f"  Sor sohbeti: {eski_sohbet} satır silinecek → {len(SOHBET) * 2} satır yazılacak")
        if not args.uygula:
            print("KURU ÇALIŞMA — hiçbir şey yazılmadı (yazmak için --uygula)")
            return 0

        bebek.training_started_at = yeni_bas
        bebek.training_completed_at = None
        s.query(SleepLog).filter(SleepLog.baby_id == bid).delete(synchronize_session=False)
        s.query(ChatMessage).filter(ChatMessage.user_id == uid).delete(synchronize_session=False)
        for fark, saat, soru, cevap in SOHBET:
            t = _an(bugun - timedelta(days=fark), saat).astimezone(timezone.utc)
            s.add(ChatMessage(user_id=uid, role="user", content=soru, cached=False, created_at=t))
            s.add(ChatMessage(user_id=uid, role="assistant", content=cevap, cached=False,
                              created_at=t + timedelta(seconds=20)))
        s.commit()
    finally:
        s.close()

    c = TestClient(app)
    H = {"Authorization": f"Bearer {create_access_token(str(uid))}"}
    r = c.post("/api/v1/logs/batch", headers=H, json={"logs": yeni})
    if r.status_code != 200:
        print("HATA /logs/batch:", r.status_code, r.text[:300]); return 1
    print("  kayıtlar yazıldı:", r.json().get("created"))
    r = c.get(f"/api/v1/plans/today?baby_id={bid}", headers=H)
    a = ((r.json().get("content") or {}).get("adaptation") or {}) if r.status_code == 200 else {}
    print(f"KONTROL /plans/today: HTTP {r.status_code} | eğitim günü {a.get('egitim_gunu')}")
    return 0 if r.status_code == 200 and a.get("egitim_gunu") == EGITIM_GUNU else 1


if __name__ == "__main__":
    raise SystemExit(main())
