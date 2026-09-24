"""
Ücretsiz ninniyi anlatıcı sesiyle üret (B5) — TEK SEFERLİK, tekrar güvenli.

    railway ssh "cd /app && PYTHONIOENCODING=utf-8 /opt/venv/bin/python scripts/genel_ninni_uret.py"
    ... --zorla        # depoda olsa da yeniden üret (ör. anlatıcı sesi değişti)

Depoda varsa dokunmaz (ElevenLabs kredisi bir kez harcanır). Kural ve yol:
api/services/genel_ses.py.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv                                  # noqa: E402

load_dotenv(ROOT / ".env")

from api.services import genel_ses                              # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Genel (anlatıcı) ninni üret")
    ap.add_argument("--zorla", action="store_true")
    a = ap.parse_args()
    for icerik in genel_ses.GENEL_ICERIKLER:
        print(icerik, genel_ses.uret(icerik, zorla=a.zorla))
    return 0


if __name__ == "__main__":
    sys.exit(main())
