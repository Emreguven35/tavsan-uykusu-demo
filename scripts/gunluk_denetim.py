"""
İlayda günlük denetim raporu — elle üretim (zamanlanmış hâli api/main.py'de,
her sabah 08:00 TR).

    python scripts/gunluk_denetim.py                  # dün
    python scripts/gunluk_denetim.py --tarih 2026-09-23

Üretim Postgres'i dışarıya kapalı; prod için konteyner içinde:
    railway ssh "cd /app && PYTHONIOENCODING=utf-8 /opt/venv/bin/python scripts/gunluk_denetim.py"

Çıktı: DENETIM_ROOT/{tarih}.html (prod /data/denetim) + 7 günlük imzalı
bağlantı (stdout'a ve loga). Kural ve içerik: api/services/denetim.py.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                                # noqa: E402

load_dotenv(ROOT / ".env")

from api.db import SessionLocal                               # noqa: E402
from api.services import denetim                              # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Günlük denetim raporu üret")
    ap.add_argument("--tarih", help="YYYY-MM-DD (varsayılan: dün, TR saati)")
    a = ap.parse_args()
    gun = date.fromisoformat(a.tarih) if a.tarih else denetim.dun()
    db = SessionLocal()
    try:
        yol, baglanti, n = denetim.rapor_yaz(db, gun)
    finally:
        db.close()
    print(f"{gun} · {n} bebek · {yol}")
    print(baglanti)
    return 0


if __name__ == "__main__":
    sys.exit(main())
