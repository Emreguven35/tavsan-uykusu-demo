"""
Anne Sesi (ANNE_SESI_STS) üretim kontrolü — SALT OKUNUR.

Son N ses profilini özetler: her içeriğin yöntemi (sts/tts), durumu, gerçek
süresi (MP3 çerçevelerinden), dosyanın depoda olup olmadığı, önizleme süresi
ve profilin ElevenLabs kullanım kaydı (api_usage). Kişisel bilgi YAZDIRILMAZ:
kullanıcı ve profil kimliği yerine 8 haneli kısa hash; voice_id, e-posta,
depo yolu (kullanıcı kimliği taşır) basılmaz.

    python tools/sts_kontrol.py --son 3 --canli         # canlı veritabanı
    python tools/sts_kontrol.py --profil 1a2b3c4d --canli
    python tools/sts_kontrol.py --son 3                 # yerel DATABASE_URL

--canli: canlı DB'ye dışarıdan erişim YOK (postgres.railway.internal). Betik
kendini `railway ssh` ile konteynere stdin'den gönderir ve orada uygulamanın
venv'iyle çalışır; konteynere dosya yazılmaz, deploy gerekmez. Oturum her
durumda rollback edilir.

UYARILAR
  - STS beklenirken tts: profil STS açılışından (--sts-baslangic) SONRA
    başlamış ve içeriğin referans kaydı var ama yontem 'sts' değil.
  - 15 dk'dan uzun hazırlanıyor: üretimi süren profilde dosyası olmayan
    içerik, profil başlangıcından bu yana > --bekleme-dk.
  - Süre sapması: STS çıktısı referansın %80'inden kısa / %120'sinden uzun
    (önizlemede beklenen = min(referans, VOICE_ONIZLEME_SN)).
  - Dosya yok: voice_audios satırı var ama depoda dosya yok.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = (Path(__file__).resolve().parent.parent
        if not __file__.startswith("<") else Path.cwd())

# STS canlıda açıldığı an (Railway deploy f88b250e, v2.9.9). Bundan önce
# başlamış profiller TTS ile üretildi; onlarda tts uyarı değildir.
STS_BASLANGIC = datetime(2026, 11, 3, 13, 11, tzinfo=timezone.utc)
BEKLEME_DK = 15
SAPMA_ALT, SAPMA_UST = 0.80, 1.20
SURUYOR = ("cloning", "generating")


def kisa_hash(x) -> str:
    return hashlib.sha256(str(x).encode()).hexdigest()[:8]


def _utc(t):
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Veri toplama (DB + depo) — SALT OKUNUR
# ---------------------------------------------------------------------------
def topla(db, son: int = 3, profil: str | None = None) -> list[dict]:
    """Profil özet verisi (ham). `profil`: profil ya da kullanıcı kısa hash'i."""
    from sqlalchemy import func
    from api.models import ApiUsage, VoiceAudio, VoiceProfile
    from api.services import mp3, storage, voice_paket
    from api.services import usage as usage_svc

    sorgu = db.query(VoiceProfile).order_by(
        func.coalesce(VoiceProfile.last_cloned_at, VoiceProfile.created_at).desc(),
        VoiceProfile.created_at.desc())
    if profil:
        profiller = [p for p in sorgu.all()
                     if profil in (kisa_hash(p.id), kisa_hash(p.user_id))]
    else:
        profiller = sorgu.limit(max(1, son)).all()

    depo = storage.depo()
    onizleme_sn = voice_paket.onizleme_sn()
    out = []
    for p in profiller:
        baslangic = _utc(p.last_cloned_at or p.created_at)
        bitis = _utc(p.released_at)
        icerikler = []
        for a in (db.query(VoiceAudio)
                  .filter(VoiceAudio.voice_profile_id == p.id).all()):
            veri = None
            try:
                veri = depo.oku(a.storage_path)
            except Exception:
                veri = None
            icerikler.append({
                "id": a.content_id, "yontem": a.yontem,
                "dosya": bool(veri),
                "sure": round(mp3.sure(veri), 1) if veri else None,
                "sure_db": a.duration_sec,
            })
        # Önizleme kopyası (eski paketli premium olmayan için kesik kopya).
        kopya = None
        on_id = voice_paket.onizleme_icerik_id()
        kyol = storage.ses_yolu(p.user_id, p.id,
                                f"{on_id}.onizleme{int(round(onizleme_sn))}")
        try:
            kveri = depo.oku(kyol) if depo.var_mi(kyol) else None
            if kveri:
                kopya = round(mp3.sure(kveri), 1)
        except Exception:
            kopya = None
        # ElevenLabs kullanımı: profilin penceresinde bu kullanıcının satırları.
        son_an = (bitis or datetime.now(timezone.utc)) + timedelta(minutes=5)
        kullanim = {}
        if baslangic is not None:
            for op, adet, toplam in (
                    db.query(ApiUsage.operation, func.count(),
                             func.coalesce(func.sum(ApiUsage.characters), 0))
                    .filter(ApiUsage.user_id == p.user_id,
                            ApiUsage.service == usage_svc.SERVIS_ELEVENLABS,
                            ApiUsage.created_at >= baslangic - timedelta(minutes=1),
                            ApiUsage.created_at <= son_an)
                    .group_by(ApiUsage.operation).all()):
                kullanim[op] = {"adet": int(adet), "toplam": int(toplam)}
        out.append({
            "profil": kisa_hash(p.id), "kullanici": kisa_hash(p.user_id),
            "durum": p.status, "amac": p.amac,
            "baslangic": baslangic, "olusturma": _utc(p.created_at), "bitis": bitis,
            "klon_var": bool(p.elevenlabs_voice_id),
            "ilerleme": (p.progress_done, p.progress_total),
            "uretilemeyen": _uretilemeyen(p.error),
            "icerikler": icerikler, "onizleme_kopyasi": kopya,
            "kullanim": kullanim,
        })
    return out


def _uretilemeyen(hata: str | None) -> list[str]:
    if not hata or not hata.startswith("Üretilemedi:"):
        return []
    return [x.strip() for x in hata.split(":", 1)[1].split(",") if x.strip()]


# ---------------------------------------------------------------------------
# Analiz — SAF (testler doğrudan çağırır)
# ---------------------------------------------------------------------------
def analiz(p: dict, *, beklenen: list[dict], referans_sure: dict[str, float | None],
           onizleme_sn: float, simdi: datetime,
           sts_baslangic: datetime = STS_BASLANGIC,
           bekleme_dk: int = BEKLEME_DK) -> dict:
    """Ham profil verisinden satırlar + uyarılar.

    beklenen: profilin amacına göre üretilecek içerikler (id, type).
    referans_sure: içerik → referans kaydın süresi (yoksa None)."""
    tur = {x["id"]: x.get("type") for x in beklenen}
    onizleme_profili = p.get("amac") == "onizleme"
    suruyor = p["durum"] in SURUYOR
    sts_donemi = p["baslangic"] is not None and p["baslangic"] >= sts_baslangic
    gecen_dk = ((simdi - p["baslangic"]).total_seconds() / 60
                if p["baslangic"] else 0)

    satirlar, uyarilar = [], []
    mevcut = {x["id"]: x for x in p["icerikler"]}
    sira = [x["id"] for x in beklenen] + [c for c in mevcut if c not in tur]
    for cid in sira:
        a = mevcut.get(cid)
        t = tur.get(cid) or ("ninni" if cid.startswith("ninni_") else
                             "masal" if cid.startswith("masal_") else "?")
        if a is None:
            if suruyor:
                durum = "hazirlaniyor"
                if gecen_dk > bekleme_dk:
                    uyarilar.append(f"{cid}: {gecen_dk:.0f} dk'dır hazırlanıyor "
                                    f"(> {bekleme_dk} dk)")
            elif cid in p["uretilemeyen"]:
                durum = "uretilemedi"
            else:
                continue                    # bitmiş pakette sunulmuyor
            satirlar.append({"id": cid, "tur": t, "yontem": None, "durum": durum,
                             "sure": None, "dosya": False, "ref": referans_sure.get(cid)})
            continue
        durum = "hazir" if a["dosya"] else "dosya_yok"
        if not a["dosya"]:
            uyarilar.append(f"{cid}: voice_audios satırı var, depoda dosya YOK")
        ref = referans_sure.get(cid)
        if sts_donemi and ref and a["yontem"] != "sts":
            uyarilar.append(f"{cid}: STS bekleniyordu, yontem={a['yontem'] or 'NULL'}")
        if a["yontem"] == "sts" and ref and a["sure"]:
            hedef = min(ref, onizleme_sn) if onizleme_profili else ref
            oran = a["sure"] / hedef
            if not SAPMA_ALT <= oran <= SAPMA_UST:
                uyarilar.append(f"{cid}: süre {a['sure']:.1f} sn, beklenen "
                                f"~{hedef:.1f} sn (%{oran * 100:.0f})")
        satirlar.append({"id": cid, "tur": t, "yontem": a["yontem"], "durum": durum,
                         "sure": a["sure"], "dosya": a["dosya"], "ref": ref})

    onizleme = None
    if onizleme_profili:
        dolu = [s for s in satirlar if s["sure"]]
        onizleme = dolu[0]["sure"] if dolu else None
    elif p.get("onizleme_kopyasi"):
        onizleme = p["onizleme_kopyasi"]
    return {"satirlar": satirlar, "uyarilar": uyarilar, "onizleme": onizleme,
            "sts_donemi": sts_donemi}


# ---------------------------------------------------------------------------
# Çıktı
# ---------------------------------------------------------------------------
def _zaman(t) -> str:
    if t is None:
        return "-"
    return (t + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M TR")


def _sayi(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def yazdir(p: dict, a: dict, onizleme_sn: float) -> str:
    sat = []
    sat.append("=" * 78)
    sat.append(f"profil {p['profil']}  kullanıcı {p['kullanici']}  amaç "
               f"{p['amac'] or 'paket(eski)'}  durum {p['durum']}  "
               f"ilerleme {p['ilerleme'][0]}/{p['ilerleme'][1]}")
    sat.append(f"  oluşturma {_zaman(p['olusturma'])} · klon başlangıcı "
               f"{_zaman(p['baslangic'])} · bitiş {_zaman(p['bitis'])} · "
               f"ElevenLabs klonu {'DURUYOR' if p['klon_var'] else 'silinmiş'} · "
               f"{'STS dönemi' if a['sts_donemi'] else 'STS öncesi'}")
    sat.append(f"  {'içerik':<30} {'tür':<6} {'yöntem':<7} {'durum':<13} "
               f"{'süre':>7} {'ref':>7} dosya")
    for s in a["satirlar"]:
        sure = f"{s['sure']:.1f}" if s["sure"] else "-"
        ref = f"{s['ref']:.1f}" if s["ref"] else "-"
        sat.append(f"  {s['id']:<30} {s['tur']:<6} {s['yontem'] or '-':<7} "
                   f"{s['durum']:<13} {sure:>7} {ref:>7} "
                   f"{'var' if s['dosya'] else 'YOK'}")
    if a["onizleme"] is not None:
        sat.append(f"  önizleme: {a['onizleme']:.1f} sn (beklenen ~{onizleme_sn:g})")
    k = p["kullanim"]
    if k:
        parca = []
        if "sts" in k:
            parca.append(f"sts {k['sts']['adet']} çağrı / {_sayi(k['sts']['toplam'])} kredi")
        if "tts" in k:
            parca.append(f"tts {k['tts']['adet']} çağrı / {_sayi(k['tts']['toplam'])} karakter")
        if "voice_clone" in k:
            parca.append(f"klon {k['voice_clone']['adet']}")
        for op in sorted(set(k) - {"sts", "tts", "voice_clone"}):
            parca.append(f"{op} {k[op]['adet']}")
        sat.append("  ElevenLabs kaydı: " + ", ".join(parca))
    else:
        sat.append("  ElevenLabs kaydı: yok (api_usage'da bu pencerede satır yok)")
    if a["uyarilar"]:
        for u in a["uyarilar"]:
            sat.append(f"  ! UYARI {u}")
    else:
        sat.append("  uyarı yok")
    return "\n".join(sat)


def calistir(db, son: int, profil: str | None, simdi: datetime | None = None,
             sts_baslangic: datetime = STS_BASLANGIC,
             bekleme_dk: int = BEKLEME_DK) -> tuple[str, int]:
    """Raporu üret: (metin, uyarı sayısı)."""
    from api.services import mp3, voice_paket, voice_sts
    simdi = simdi or datetime.now(timezone.utc)
    onizleme_sn = voice_paket.onizleme_sn()
    ref_sure: dict[str, float | None] = {}

    def referans(cid):
        if cid not in ref_sure:
            yol = voice_sts.referans_yolu(cid)
            ref_sure[cid] = round(mp3.sure(yol.read_bytes()), 1) if yol else None
        return ref_sure[cid]

    veriler = topla(db, son, profil)
    bloklar, toplam_uyari = [], 0
    for p in veriler:
        beklenen = [{"id": x["id"], "type": x.get("type")}
                    for x in voice_paket.uretim_icerikleri(p["amac"])]
        for cid in [x["id"] for x in beklenen] + [x["id"] for x in p["icerikler"]]:
            referans(cid)
        a = analiz(p, beklenen=beklenen, referans_sure=ref_sure,
                   onizleme_sn=onizleme_sn, simdi=simdi,
                   sts_baslangic=sts_baslangic, bekleme_dk=bekleme_dk)
        toplam_uyari += len(a["uyarilar"])
        bloklar.append(yazdir(p, a, onizleme_sn))
    bas = (f"STS {'AÇIK' if voice_sts.acik_mi() else 'kapalı'} · "
           f"önizleme {onizleme_sn:g} sn · STS başlangıcı {_zaman(sts_baslangic)} · "
           f"{len(veriler)} profil")
    if not veriler:
        bloklar.append("(profil bulunamadı)")
    son_satir = f"TOPLAM UYARI: {toplam_uyari}"
    return "\n".join([bas] + bloklar + ["=" * 78, son_satir]), toplam_uyari


# ---------------------------------------------------------------------------
# Canlı: kendini railway ssh ile konteynerde çalıştır
# ---------------------------------------------------------------------------
# LD_LIBRARY_PATH yalnız doluysa export edilir (boş `export` tüm ortamı basar).
UZAK_KABUK = ('L=$(tr "\\000" "\\012" < /proc/1/environ | grep ^LD_LIBRARY_PATH=); '
              '[ -n "$L" ] && export "$L"; cd /app && '
              'PYTHONIOENCODING=utf-8 /opt/venv/bin/python - {argv}')


def railway_yolu() -> str | None:
    """railway.exe (npm paketinin ikilisi) — .cmd kabuğu boşluklu yolda ve
    tırnaklı argümanda bozuluyor; yoksa PATH'teki railway."""
    yol = shutil.which("railway")
    if yol:
        exe = (Path(yol).parent / "node_modules" / "@railway" / "cli" / "bin"
               / "railway.exe")
        if exe.is_file():
            return str(exe)
    return yol


def uzak_komut(argv: list[str], railway: str = "railway") -> list[str]:
    """Konteynerde çalıştırılacak komut; --canli argümanlardan düşer."""
    import shlex
    kalan = [x for x in argv if x != "--canli"]
    return [railway, "ssh", "--", "sh", "-c",
            UZAK_KABUK.format(argv=" ".join(shlex.quote(x) for x in kalan))]


def _canli_calistir(argv: list[str]) -> int:
    railway = railway_yolu()
    if not railway:
        print("railway CLI bulunamadı.")
        return 2
    kaynak = Path(__file__).read_bytes()
    r = subprocess.run(uzak_komut(argv, railway), input=kaynak, cwd=str(ROOT),
                       capture_output=True)
    cikti = r.stdout.decode("utf-8", "replace")
    print("\n".join(s for s in cikti.splitlines() if not s.startswith("Using SSH key")))
    if r.returncode != 0:
        print(r.stderr.decode("utf-8", "replace")[-2000:])
    return r.returncode


def _konteyner_ortami() -> None:
    """Konteynerde ssh oturumu uygulamanın ortamını taşımaz: PID 1'in ortamı
    eksik anahtarlar için os.environ'a alınır (BASILMAZ)."""
    p = Path("/proc/1/environ")
    if not p.exists() or os.getenv("DATABASE_URL"):
        return
    for kv in p.read_bytes().split(b"\0"):
        if b"=" in kv:
            k, v = kv.decode("utf-8", "replace").split("=", 1)
            os.environ.setdefault(k, v)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    a = argparse.ArgumentParser(description="Anne Sesi STS üretim kontrolü (salt okunur)")
    a.add_argument("--son", type=int, default=3, help="son N profil (varsayılan 3)")
    a.add_argument("--profil", help="profil ya da kullanıcı kısa hash'i")
    a.add_argument("--canli", action="store_true", help="canlı DB (railway ssh)")
    a.add_argument("--sts-baslangic", help="ISO zaman (varsayılan v2.9.9 açılışı)")
    a.add_argument("--bekleme-dk", type=int, default=BEKLEME_DK)
    g = a.parse_args(argv)
    if g.canli:
        return _canli_calistir(argv)

    _konteyner_ortami()
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from api.db import SessionLocal
    import api.models  # noqa: F401
    sts_bas = (_utc(datetime.fromisoformat(g.sts_baslangic))
               if g.sts_baslangic else STS_BASLANGIC)
    db = SessionLocal()
    try:
        metin, _ = calistir(db, g.son, g.profil, sts_baslangic=sts_bas,
                            bekleme_dk=g.bekleme_dk)
        print(metin)
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
