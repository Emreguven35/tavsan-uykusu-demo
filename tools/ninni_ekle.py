"""
"Ninnilerini al" (ANNE_SESI_NINNI_EKLE) — KURU ÇALIŞTIRMA, SALT OKUNUR.

Kim uygun olurdu ve maliyeti ne olurdu: her kullanıcının güncel ses profili
services/ninni_ekle.uygunluk ile değerlendirilir (bayrak DIŞINDA tüm kurallar),
eksik ninnilerin referans süresi × voice_sts.KREDI_SANIYE ile kredi tahmini
çıkar. Hiçbir şey yazılmaz, ElevenLabs çağrılmaz. Kişisel bilgi yok:
kullanıcı/profil 8 haneli hash.

    python tools/ninni_ekle.py --kuru-calistir --canli     # canlı veritabanı
    python tools/ninni_ekle.py --kuru-calistir             # yerel DATABASE_URL

--canli: betik, services/ninni_ekle.py'nin YEREL kopyasıyla birlikte
`railway ssh` üzerinden konteynerde stdin'den çalışır — canlıda henüz deploy
edilmemiş kurallar da aynen uygulanır. Oturum rollback edilir.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

ROOT = (Path(__file__).resolve().parent.parent
        if not __file__.startswith("<") else Path.cwd())


def kisa_hash(x) -> str:
    return hashlib.sha256(str(x).encode()).hexdigest()[:8]


def _sayi(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def kuru_rapor(db) -> tuple[str, dict]:
    """(metin, özet) — özet: {"uygun": n, "kredi": toplam, "profil": n}."""
    from api.models import User, VoiceProfile
    from api.routers import voice as vr
    from api.services import ninni_ekle, ucretsiz_erisim, voice_paket

    satirlar, ozet = [], {"uygun": 0, "kredi": 0, "profil": 0}
    uids = [u for (u,) in db.query(VoiceProfile.user_id).distinct().all()]
    for uid in uids:
        user = db.get(User, uid)
        if user is None:
            continue
        p = vr._son_profil(db, user)
        if p is None:
            continue
        premium = ucretsiz_erisim.premium_mi(db, user)
        d = ninni_ekle.uygunluk(p, ninni_ekle._yontemler(db, p),
                                getattr(user, "ses_ninni_ekle_at", None), premium)
        kredi = ninni_ekle.kredi_tahmini(d["eksik"]) if d["uygun"] else 0
        ozet["profil"] += 1
        if d["uygun"]:
            ozet["uygun"] += 1
            ozet["kredi"] += kredi
        satirlar.append((d["uygun"], kisa_hash(uid), kisa_hash(p.id), p.status,
                         p.amac or "paket(eski)", "premium" if premium else "değil",
                         d["sebep"], d["eksik"], kredi))

    paket_ninni = [x["id"] for x in voice_paket.paket_icerikleri() if x.get("type") == "ninni"]
    out = [f"NİNNİ EKLE — KURU ÇALIŞTIRMA (yazma yok, ElevenLabs yok) · bayrak "
           f"{'AÇIK' if ninni_ekle.acik_mi() else 'kapalı'} (kurallar bayraktan bağımsız)",
           f"pakette STS üretilebilir ninniler: {', '.join(paket_ninni) or '(yok)'}",
           f"{'kullanıcı':<9} {'profil':<9} {'durum':<9} {'amaç':<12} {'premium':<8} "
           f"{'sonuç':<17} {'eksik':>5} {'kredi':>7}  eksik ninniler"]
    for uygun, uh, ph, st, amac, prem, sebep, eksik, kredi in sorted(
            satirlar, key=lambda s: (not s[0], s[6], s[1])):
        out.append(f"{uh:<9} {ph:<9} {st:<9} {amac:<12} {prem:<8} "
                   f"{('UYGUN' if uygun else sebep):<17} {len(eksik):>5} "
                   f"{_sayi(kredi) if uygun else '-':>7}  {', '.join(eksik) if uygun else ''}")
    out.append("-" * 78)
    out.append(f"{ozet['profil']} profil · UYGUN {ozet['uygun']} · tahmini toplam "
               f"{_sayi(ozet['kredi'])} kredi (+ uygun başına 1 geçici klon slotu)")
    return "\n".join(out), ozet


# ---------------------------------------------------------------------------
def _konteyner_ortami() -> None:
    p = Path("/proc/1/environ")
    if not p.exists() or os.getenv("DATABASE_URL"):
        return
    for kv in p.read_bytes().split(b"\0"):
        if b"=" in kv:
            k, v = kv.decode("utf-8", "replace").split("=", 1)
            os.environ.setdefault(k, v)


def uzak_betik(arac: str, modul: str, kredi_saniye: int) -> str:
    """Konteynere gidecek tek betik: önce services/ninni_ekle yerel kopyası
    modül olarak kaydedilir ve STS kredi oranı yerel değere çekilir (canlıdaki
    kod eski formülü taşıyabilir), sonra araç çalışır."""
    return ("import sys as _s, types as _t\n"
            "_s.path.insert(0, '/app')\n"
            "from api.services import voice_sts as _vs\n"
            f"_vs.KREDI_SANIYE = {int(kredi_saniye)}\n"
            "_vs.kredi = lambda sn: max(1, round(sn * _vs.KREDI_SANIYE))\n"
            "import api.services as _svc\n"
            "_m = _t.ModuleType('api.services.ninni_ekle')\n"
            f"exec(compile({modul!r}, 'ninni_ekle.py', 'exec'), _m.__dict__)\n"
            "_s.modules['api.services.ninni_ekle'] = _m\n"
            "_svc.ninni_ekle = _m\n"
            # __future__ yalnız dosya başında olabilir; araçtakini düşür.
            + arac.replace("from __future__ import annotations\n", "", 1))


def _canli_calistir(argv: list[str]) -> int:
    import subprocess
    sys.path.insert(0, str(ROOT))
    from tools.sts_kontrol import railway_yolu, uzak_komut
    railway = railway_yolu()
    if not railway:
        print("railway CLI bulunamadı.")
        return 2
    from api.services import voice_sts
    betik = uzak_betik(Path(__file__).read_text(encoding="utf-8"),
                       (ROOT / "api" / "services" / "ninni_ekle.py").read_text(encoding="utf-8"),
                       voice_sts.KREDI_SANIYE)
    r = subprocess.run(uzak_komut(argv, railway), input=betik.encode("utf-8"),
                       cwd=str(ROOT), capture_output=True)
    print("\n".join(s for s in r.stdout.decode("utf-8", "replace").splitlines()
                    if not s.startswith("Using SSH key")))
    if r.returncode != 0:
        print(r.stderr.decode("utf-8", "replace")[-2000:])
    return r.returncode


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    a = argparse.ArgumentParser(description="Ninnilerini al — kuru çalıştırma")
    a.add_argument("--kuru-calistir", action="store_true", required=True,
                   help="salt okunur rapor (tek desteklenen kip)")
    a.add_argument("--canli", action="store_true", help="canlı DB (railway ssh)")
    g = a.parse_args(argv)
    if g.canli:
        return _canli_calistir(argv)
    _konteyner_ortami()
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from api.db import SessionLocal
    import api.models  # noqa: F401
    db = SessionLocal()
    try:
        print(kuru_rapor(db)[0])
    finally:
        db.rollback()
        db.close()
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
