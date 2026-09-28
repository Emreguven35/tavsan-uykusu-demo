"""
GERÇEK GÜN — mobilin gerçekte gönderdiği gövdelerle saat saat bir gün (kalıcı).

2026-09-28 cihaz bulguları (build 21) bu suite'in sebebi:
  A) "Bebeğiniz bugün kaçta uyandı?" → 09:00 cevabı sunucuya
     `type: sleep, started_at = ended_at = 09:00` olarak geliyor. K12.1 bunu
     sıfır süreli uyku diye yok sayıyordu; gün 07:00 varsayımıyla kuruluyordu.
  C) Ana sayfa "Bugün" listesinde son haftanın açık beslenmeleri görünüyordu.
  D) Bebek 14,8 aylık (düzeltilmiş), 36 haftalık prematüre.

Her bebek için gün DONDURULMUŞ saatle oynatılır ve HER ADIMDA GET /plans/today
ile GET /logs?date= birlikte doğrulanır:
    09:05  sabah cevabı 09:00        → gün 09:00'dan, ilk uyku 09:00 + pencere
    13:02  sayaç başlatıldı (açık)   → uyku sürüyor, sıradaki tahmini
    14:10  "Uyandı" (aynı client_id) → sonraki uyku 14:10 + pencere (kayma)
    akşam  kısa son uyku             → gündüz açığı: şekerleme ya da gerekçeli
                                       uyarı; yatış bant üst ucunu aşabilir,
                                       24:00'ü aşamaz (İlayda 3. cevaplar S3)
Aynı gün build 21 gövdesiyle (sıfır süreli sleep) ve yeni gövdeyle (`wake`)
ayrı bebeklerde oynatılır; iki çizelge BİREBİR aynı olmalı.

LLM YOK (mühür), ağ YOK, prod DB YOK: geçici sqlite + TestClient.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "gercek_gun_test.db"
if _DB.exists():
    _DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(Path(tempfile.gettempdir()) / "gercek_gun_medya")
os.environ["BETA_MODE"] = "true"          # prod gibi: planın days'i kilitsiz
os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy")

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, engine                             # noqa: E402
import api.models                                           # noqa: E402,F401
from api.main import app                                    # noqa: E402
from api.zaman import TR, bugun_tr, saat_sabitle            # noqa: E402
from engine import yas_bantlari                             # noqa: E402

from tests.llm_muhuru import TAM_PROFIL, canli_cagri_sayisi, muhurle  # noqa: E402
muhurle()

Base.metadata.create_all(bind=engine)
client = TestClient(app)

results: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail="") -> None:
    results.append((name, bool(cond), str(detail)))


TODAY = bugun_tr()


def an(dk: int, gun=None) -> datetime:
    """Yerel (TR) duvar dakikası → saat dilimli an."""
    g = gun or TODAY
    return datetime(g.year, g.month, g.day, tzinfo=TR) + timedelta(minutes=dk)


def iso(dk: int, gun=None) -> str:
    return an(dk, gun).astimezone(timezone.utc).isoformat()


def hhmm(dk: int) -> str:
    dk = int(dk) % 1440
    return f"{dk // 60:02d}:{dk % 60:02d}"


tok = client.post("/api/v1/auth/register",
                  json={"email": "gercek_gun@example.com",
                        "password": "TestPass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}


def bebek(ad: str, gun_once: int, hafta: int) -> str:
    r = client.post("/api/v1/babies", headers=H, json={
        **TAM_PROFIL, "name": ad, "night_wakes": 2, "dogum_haftasi": hafta,
        "birth_date": (TODAY - timedelta(days=gun_once)).isoformat()})
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    with saat_sabitle(an(8 * 60)):
        g = client.post("/api/v1/plans/generate?sync=true", headers=H,
                        json={"baby_id": bid})
    assert g.status_code == 201, g.text
    return bid


def gonder(loglar: list[dict]):
    return client.post("/api/v1/logs/batch", headers=H, json={"logs": loglar})


def plan(bid: str) -> dict:
    r = client.get(f"/api/v1/plans/today?baby_id={bid}", headers=H)
    assert r.status_code == 200, r.text
    return r.json()["content"]


def liste(bid: str) -> list[dict]:
    r = client.get(f"/api/v1/logs?date={TODAY.isoformat()}&baby_id={bid}",
                   headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def blok(c: dict, key: str) -> dict | None:
    return next((b for b in c["schedule"] if b.get("key") == key), None)


def naplar(c: dict) -> list[dict]:
    return sorted((b for b in c["schedule"] if b.get("type") == "nap"),
                  key=lambda b: b["start_minute"])


def yalniz_bugun(kayitlar: list[dict]) -> bool:
    """Listedeki her kayıt bugün başlamış ya da bugün bitmiş olmalı."""
    def gun(s):
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TR).date()
    return all(gun(k["started_at"]) == TODAY
               or (k["ended_at"] and gun(k["ended_at"]) == TODAY)
               for k in kayitlar)


def sabah_govdesi(bid: str, surum: str) -> dict:
    """build 21: sleep_end + süre 0 → sıfır süreli sleep. yeni: wake."""
    if surum == "build21":
        return {"baby_id": bid, "type": "sleep", "started_at": iso(9 * 60),
                "ended_at": iso(9 * 60), "client_id": f"sabah-{bid[:8]}"}
    return {"baby_id": bid, "type": "wake", "started_at": iso(9 * 60),
            "client_id": f"sabah-{bid[:8]}"}


def gun_oynat(etiket: str, bid: str, surum: str, yas_ay: float) -> dict:
    """Günü oynat, her adımı doğrula; son adımın çizelgesini döndür."""
    t = f"[{etiket}/{surum}]"
    bant = yas_bantlari.yas_bandi_getir(yas_ay)
    hedef = yas_bantlari.sabah_hedefi(yas_ay)
    sayac = {"baby_id": bid, "type": "sleep", "started_at": iso(13 * 60 + 2),
             "client_id": f"sayac-{bid[:8]}"}

    # --- 09:05 — sabah sorusu 09:00 -------------------------------------
    with saat_sabitle(an(9 * 60 + 5)):
        r = gonder([sabah_govdesi(bid, surum)])
        c1, l1 = plan(bid), liste(bid)
    j = r.json()
    check(f"{t} 1a) Sabah cevabı kabul (200, reddedilen yok)",
          r.status_code == 200 and len(j["synced"]) == 1
          and not [s for s in j["skipped"] if s["reason"] != "duplicate"], r.text[:200])
    a1 = c1["adaptation"]
    ww = a1["uyaniklik_penceresi_dk"]
    check(f"{t} 1b) Sabah uyanışı 09:00, kaynak kayıt",
          a1["sabah_uyanis_gercek"] == "09:00" and a1["sabah_uyanis_kaynak"] == "kayit"
          and blok(c1, "wake")["start_minute"] == 9 * 60,
          f"{a1['sabah_uyanis_gercek']} {a1['sabah_uyanis_kaynak']}")
    check(f"{t} 1c) Kayıt yok sayılmadı, gündüz uykusu da sayılmadı",
          not a1["yok_sayilan_kayitlar"]
          and all(b["start_minute"] > 9 * 60 for b in naplar(c1)),
          f"yok={a1['yok_sayilan_kayitlar']} naplar={[hhmm(b['start_minute']) for b in naplar(c1)]}")
    nap1 = blok(c1, "nap_1")
    check(f"{t} 1d) 1. uyku = 09:00 + pencere ({ww} dk), en erken sınırda",
          nap1["start_minute"] == max(9 * 60 + ww,
                                      9 * 60 + bant["uyaniklik_penceresi_dk"][0]),
          hhmm(nap1["start_minute"]))
    check(f"{t} 1e) Sıradaki blok sabah uyanışı istemiyor",
          (a1.get("siradaki_blok") or {}).get("eksik_kayit") != "sabah_uyanisi",
          str(a1.get("siradaki_blok")))
    check(f"{t} 1f) Plan yaşa göre sabah hedefini taşıyor ({hedef['hedef']})",
          (a1.get("sabah_hedefi") or {}).get("hedef") == hedef["hedef"],
          str(a1.get("sabah_hedefi")))
    check(f"{t} 1g) Bugün listesi: 1 kayıt, 'Sabah uyanışı' (uyku değil)",
          len(l1) == 1 and (surum == "yeni" or l1[0]["kategori"] == "sabah_uyanisi"),
          str([(k["type"], k.get("kategori")) for k in l1]))
    check(f"{t} 1h) Plan bloklarında sıfır uzunluklu uyku yok",
          all(b["end_minute"] > b["start_minute"] for b in naplar(c1)),
          str([(b["key"], hhmm(b["start_minute"])) for b in naplar(c1)]))

    # --- 13:02 — sayaç başlatıldı ---------------------------------------
    with saat_sabitle(an(13 * 60 + 2)):
        r = gonder([sayac])
        c2, l2 = plan(bid), liste(bid)
    check(f"{t} 2a) Sayaç kabul", r.status_code == 200 and len(r.json()["synced"]) == 1,
          r.text[:200])
    n1 = blok(c2, "nap_1")
    check(f"{t} 2b) 1. uyku 13:02'de sürüyor (kayıttan)",
          n1 and n1["start_minute"] == 13 * 60 + 2 and n1.get("kaynak") == "kayit"
          and n1.get("devam") is True, str(n1))
    check(f"{t} 2c) Sabah uyanışı hâlâ 09:00",
          c2["adaptation"]["sabah_uyanis_gercek"] == "09:00",
          c2["adaptation"]["sabah_uyanis_gercek"])
    check(f"{t} 2d) Bugün listesi: sabah + açık sayaç, yalnız bugün",
          len(l2) == 2 and yalniz_bugun(l2)
          and any(k["ended_at"] is None for k in l2),
          str([(k["type"], k["started_at"][11:16], k["ended_at"]) for k in l2]))

    # --- 14:10 — "Uyandı": mobil aynı kaydı bitişle yeniden gönderir -----
    with saat_sabitle(an(14 * 60 + 10)):
        r = gonder([dict(sayac, ended_at=iso(14 * 60 + 10))])
        c3, l3 = plan(bid), liste(bid)
    j = r.json()
    check(f"{t} 3a) Uyandı: aynı client_id güncellendi (yeni satır yok)",
          r.status_code == 200 and j["updated"] == 1 and j["created"] == 0, r.text[:200])
    n1 = blok(c3, "nap_1")
    sonraki = [b for b in naplar(c3) if b["start_minute"] > 14 * 60 + 10]
    check(f"{t} 3b) 1. uyku 13:02–14:10 kayıttan, kapalı",
          n1["start_minute"] == 13 * 60 + 2 and n1["end_minute"] == 14 * 60 + 10
          and not n1.get("devam"), str(n1))
    check(f"{t} 3c) Kayma: sonraki uyku 14:10 + pencere ({ww} dk)",
          sonraki and sonraki[0]["start_minute"] == 14 * 60 + 10 + ww,
          [hhmm(b["start_minute"]) for b in sonraki])
    check(f"{t} 3d) Sıradaki blok kesin (zincir kayıtlarla kapalı)",
          (c3["adaptation"].get("siradaki_blok") or {}).get("guven") == "kesin",
          str(c3["adaptation"].get("siradaki_blok")))
    check(f"{t} 3e) Liste: 2 kayıt, yalnız bugün, açık UYKU yok (wake nokta olaydır)",
          len(l3) == 2 and yalniz_bugun(l3)
          and all(k["ended_at"] for k in l3 if k["type"] != "wake"),
          str([(k["type"], k["ended_at"]) for k in l3]))

    # --- Akşam — son uyku kısa kaldı: gündüz açığı ---------------------
    s2 = sonraki[0]["start_minute"]
    kisa = {"baby_id": bid, "type": "sleep", "started_at": iso(s2),
            "ended_at": iso(s2 + 20), "client_id": f"kisa-{bid[:8]}"}
    with saat_sabitle(an(s2 + 25)):
        r = gonder([kisa])
        c4, l4 = plan(bid), liste(bid)
    a4 = c4["adaptation"]
    gunduz = sum(b["end_minute"] - b["start_minute"] for b in naplar(c4)
                 if b["key"] != "sekerleme" and b.get("kaynak") == "kayit")
    min_gunduz = bant["gunduz_uyku_toplam_dk"][0]
    check(f"{t} 4a) Kısa uyku kabul ve zincirde",
          r.status_code == 200 and any(b["start_minute"] == s2 and b.get("kaynak") == "kayit"
                                       for b in naplar(c4)),
          [hhmm(b["start_minute"]) for b in naplar(c4)])
    eksik = gunduz < min_gunduz
    sek = blok(c4, "sekerleme")
    yer_yok = any("şekerlemesi için yer kalmadı" in u for u in a4["uyarilar"])
    check(f"{t} 4b) Gündüz {gunduz} < {min_gunduz} dk → şekerleme ya da gerekçeli uyarı",
          (not eksik) or sek is not None or yer_yok,
          f"sek={sek} uyarilar={a4['uyarilar']}")
    yatis = blok(c4, "bedtime")
    son_bitis = max(b["end_minute"] for b in naplar(c4) if b["key"] != "sekerleme")
    beklenen = son_bitis + ww
    if sek is not None:
        beklenen = max(beklenen, sek["end_minute"]
                       + int(yas_bantlari.kestirme_protokolu()["gece_uykusuna_gecis_dk"]))
    tavan = yas_bantlari.gece_yatisi_mutlak_tavan()
    # Yatış = son uyku bitişi + pencere; yalnız 24:00'e kırpılır ya da bandın
    # alt ucuna ("Yaşına uygun yatış saatine getirildi") çekilebilir.
    alt_uca_cekildi = (yatis["start_minute"] > beklenen
                       and (yatis.get("note") or "").startswith("Yaşına uygun"))
    check(f"{t} 4c) Yatış = son uyku bitişi + pencere, 24:00'ü aşmıyor",
          (yatis["start_minute"] == min(tavan, beklenen) or alt_uca_cekildi)
          and yatis["start_minute"] <= tavan,
          f"yatış={hhmm(yatis['start_minute'])} beklenen={hhmm(beklenen)} "
          f"not={yatis.get('note')}")
    if yatis.get("gec_yatis"):
        check(f"{t} 4d) Bant üst ucu aşıldı → 'alışma evresi' uyarısı",
              any("alışma evresine özgü" in u for u in a4["uyarilar"]), a4["uyarilar"])
    check(f"{t} 4e) Toplam uyku değerlendirmesi dönüyor (K9)",
          c4.get("toplam_uyku") is None or "durum" in (c4.get("toplam_uyku") or {}),
          str(c4.get("toplam_uyku"))[:120])
    check(f"{t} 4f) Liste: 3 kayıt, yalnız bugün",
          len(l4) == 3 and yalniz_bugun(l4), str([k["type"] for k in l4]))
    return c4


def cizelge_ozu(c: dict) -> list[tuple]:
    return [(b.get("key"), b.get("start_minute"), b.get("end_minute"))
            for b in c["schedule"]]


# 14,8 aylık prematüre (36 hf): gerçek ≈ 15,8 ay → düzeltilmiş 14,8 ay.
# 8 aylık (40 hf).
BEBEKLER = [("14.8ay-prematüre", 481, 36, 14.8), ("8ay", 244, 40, 8.0)]
for ad, gun_once, hafta, yas in BEBEKLER:
    sonuc = {}
    for surum in ("build21", "yeni"):
        bid = bebek(f"{ad}-{surum}", gun_once, hafta)
        sonuc[surum] = gun_oynat(ad, bid, surum, yas)
    check(f"[{ad}] 5) build 21 (sıfır süreli sleep) ile yeni (`wake`) AYNI çizelge",
          cizelge_ozu(sonuc["build21"]) == cizelge_ozu(sonuc["yeni"]),
          f"{cizelge_ozu(sonuc['build21'])} != {cizelge_ozu(sonuc['yeni'])}")

# =============================================================================
# C — 7 günlük unutulmuş açık sayaç + eski açık beslenmeler: bugün listesi temiz
# =============================================================================
bid = bebek("eski-sayacli", 244, 40)
with saat_sabitle(an(10 * 60, TODAY - timedelta(days=7))):
    gonder([{"baby_id": bid, "type": "sleep", "client_id": "eski-sayac",
             "started_at": iso(10 * 60, TODAY - timedelta(days=7))}])
for i in (1, 3, 5):
    with saat_sabitle(an(14 * 60, TODAY - timedelta(days=i))):
        gonder([{"baby_id": bid, "type": "feed", "client_id": f"eski-feed-{i}",
                 "started_at": iso(14 * 60, TODAY - timedelta(days=i))}])
# Dün 23:30 açık kalan gece sayacı + bugünün kayıtları
with saat_sabitle(an(23 * 60 + 30, TODAY - timedelta(days=1))):
    gonder([{"baby_id": bid, "type": "sleep", "client_id": "dun-gece",
             "started_at": iso(23 * 60 + 30, TODAY - timedelta(days=1))}])
with saat_sabitle(an(8 * 60)):
    gonder([{"baby_id": bid, "type": "feed", "client_id": "bugun-feed",
             "started_at": iso(8 * 60)}])
    l_sabah = liste(bid)
with saat_sabitle(an(16 * 60)):
    l_aksam = liste(bid)
_cid = lambda ls: sorted(k["client_id"] for k in ls)   # noqa: E731
check("C1) 08:00 listesi: yalnız dün gece süren sayaç + bugünün beslenmesi",
      _cid(l_sabah) == ["bugun-feed", "dun-gece"], _cid(l_sabah))
check("C2) 7 günlük açık sayaç ve geçmiş günlerin açık beslenmeleri listede YOK",
      not any(c.startswith("eski-") for c in _cid(l_sabah) + _cid(l_aksam)),
      _cid(l_aksam))
check("C3) 16:00'da dün 23:30 sayacı 16 saati geçti → listeden düştü (K17)",
      _cid(l_aksam) == ["bugun-feed"], _cid(l_aksam))

# K14.3 — motor da aynı kuralı uygular: 3 gün önce açık kalan gece sayacı
# bugünün gece uykusu DEĞİLDİR (prod Bebek 20: 60 saatlik "gece uykusu").
bid_eski = bebek("eski-gece-sayacli", 244, 40)
with saat_sabitle(an(19 * 60 + 38, TODAY - timedelta(days=3))):
    gonder([{"baby_id": bid_eski, "type": "sleep", "client_id": "eski-gece",
             "started_at": iso(19 * 60 + 38, TODAY - timedelta(days=3))}])
with saat_sabitle(an(7 * 60 + 5)):
    gonder([{"baby_id": bid_eski, "type": "sleep", "client_id": "sabah-eski",
             "started_at": iso(7 * 60), "ended_at": iso(7 * 60)}])
    c_eski = plan(bid_eski)
_gu = c_eski["adaptation"].get("gece_uykusu") or {}
check("C4) 3 gün önceki açık gece sayacı bugünün gece uykusu sayılmadı",
      (_gu.get("brut_dk") or 0) <= 24 * 60
      and c_eski["adaptation"]["sabah_uyanis_gercek"] == "07:00"
      and not any("başlayan gece uykusunun bitişi girilmemiş" in u
                  for u in c_eski["adaptation"]["uyarilar"]),
      f"gece={_gu} uyarilar={c_eski['adaptation']['uyarilar']}")

check("Z) Canlı LLM çağrısı YOK (mühür)", canli_cagri_sayisi() == 0,
      canli_cagri_sayisi())

# =============================================================================
print("=" * 78)
print("GERÇEK GÜN — kayıt → plan → bugün listesi (build 21 + yeni gövde)")
print("=" * 78)
gecen = 0
for ad, ok, detay in results:
    print(f"  [{'PASS' if ok else 'FAIL'}] {ad}")
    if not ok:
        print(f"         → {detay[:400]}")
    gecen += ok
print("=" * 78)
print(f"TOPLAM: {gecen}/{len(results)} geçti")
print("=" * 78)
sys.exit(0 if gecen == len(results) else 1)
