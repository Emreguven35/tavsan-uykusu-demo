"""
Topluluk v2 kategorisini (threads.kategori) güncel sınıflandırıcıyla yeniden hesapla.

NEDEN: 0022 migrasyonu mevcut konuları ilk kuralla doldurdu; o kural metinde
geçen bir kelimeyi başlığın önüne koyuyordu ("Bebek gece uyanıp oyun
oynuyorsa" → gündüz) ve eski gelisim/anne_hali/oneri konularını başlığa hiç
bakmadan 'diger' yapıyordu. Sınıflandırıcı düzeldi (başlık önce); bu betik
mevcut konuları onunla yeniden hesaplar.

YALNIZ v2 ÖNCESİ konular (created_at < SINIR): v2 istemcinin açıkça seçtiği
kategori ASLA ezilmez. Eski `category` sütununa dokunulmaz.

    railway ssh "cd /app && PYTHONIOENCODING=utf-8 /opt/venv/bin/python scripts/topluluk_kategori_duzelt.py [--uygula]"
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                              # noqa: E402

load_dotenv(ROOT / ".env")

from api.db import SessionLocal                             # noqa: E402
from api.models import Thread                               # noqa: E402
from api.services.topluluk import kategori_siniflandir      # noqa: E402

SINIR = datetime.fromisoformat("2026-10-01T00:00:00+03:00")  # v2 öncesi


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true", help="verilmezse yalnız listeler")
    a = ap.parse_args()
    db = SessionLocal()
    try:
        degisen = []
        for t in db.query(Thread).all():
            olusma = t.created_at if t.created_at.tzinfo else t.created_at.replace(tzinfo=SINIR.tzinfo)
            if olusma >= SINIR:
                continue
            yeni = kategori_siniflandir(t.category, t.title, t.body)
            if yeni != t.kategori:
                degisen.append((t, t.kategori, yeni))
        for t, eski, yeni in degisen:
            print(f"  {t.category:10s} {eski:16s} → {yeni:16s} | {t.title[:60]}")
        print(f"değişecek konu: {len(degisen)}")
        if not a.uygula:
            print("KURU KOŞU — --uygula verilmedi, hiçbir şey değişmedi")
            return 0
        for t, _e, yeni in degisen:
            t.kategori = yeni
        db.commit()
        print(f"{len(degisen)} konu güncellendi")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
