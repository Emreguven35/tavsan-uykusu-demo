"""
Paylaşım kartı TEST HESABINI sil (tools/kart_test_hesabi.py'nin kurduğu).

Yalnız şu üç şart BİRLİKTE sağlanırsa siler, aksi hâlde hiçbir şeye dokunmaz:
  1. e-posta KART_TEST_EMAIL (env) ve @example.com ile bitiyor
  2. hesapta premium_haklari.aciklama = 'kart_test' var
  3. hesabın TÜM uyku kayıtlarının client_id'si 'kart_test-' ile başlıyor
Silinen: kullanıcı + cascade ile bebekler, kayıtlar, planlar, değerlendirmeler,
premium hakkı; onaylar (kanıt tablosuna TAŞINMAZ — test hesabı), plan işleri.

  python tools/kart_test_sil.py            # kuru: ne silineceğini sayar
  python tools/kart_test_sil.py --uygula   # siler
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent if "__file__" in globals() and \
    not str(globals().get("__file__", "")).startswith("<") else Path.cwd()
sys.path.insert(0, str(ROOT))

ISARET = "kart_test"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true")
    a = ap.parse_args()
    eposta = (os.getenv("KART_TEST_EMAIL") or "").strip().lower()
    if not eposta.endswith("@example.com"):
        print("HATA: KART_TEST_EMAIL (@example.com) env'de olmalı")
        return 2

    from api.db import SessionLocal
    from api.models import (AiCevapBildirimi, Baby, Consent, EgitimDegerlendirme,
                            PlanUretimIsi, PremiumHak, SleepLog, SleepPlan, User)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == eposta).first()
        if user is None:
            print("Hesap yok — silinecek bir şey yok.")
            return 0
        if not db.query(PremiumHak).filter(PremiumHak.user_id == user.id,
                                           PremiumHak.aciklama == ISARET).count():
            print("DUR: hesapta 'kart_test' işareti yok — dokunulmadı.")
            return 3
        loglar = db.query(SleepLog).filter(SleepLog.user_id == user.id).all()
        yabanci = [r for r in loglar if not (r.client_id or "").startswith(ISARET + "-")]
        if yabanci:
            print(f"DUR: {len(yabanci)} işaretsiz kayıt var (uygulamadan girilmiş olabilir) — "
                  "dokunulmadı. Önce onları gözden geçir.")
            return 3
        sayim = {
            "bebek": db.query(Baby).filter(Baby.user_id == user.id).count(),
            "kayit": len(loglar),
            "plan": db.query(SleepPlan).filter(SleepPlan.user_id == user.id).count(),
            "degerlendirme": db.query(EgitimDegerlendirme)
                             .filter(EgitimDegerlendirme.user_id == user.id).count(),
            "onay": db.query(Consent).filter(Consent.user_id == user.id).count(),
        }
        print("Silinecek:", sayim)
        if not a.uygula:
            print("KURU — silmek için --uygula")
            return 0
        db.query(Consent).filter(Consent.user_id == user.id).delete(synchronize_session=False)
        db.query(PlanUretimIsi).filter(PlanUretimIsi.user_id == str(user.id)) \
            .delete(synchronize_session=False)
        db.query(AiCevapBildirimi).filter(AiCevapBildirimi.user_id == user.id) \
            .delete(synchronize_session=False)
        db.delete(user)                       # relationship cascade + FK ON DELETE CASCADE
        db.commit()
        print("SİLİNDİ.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
