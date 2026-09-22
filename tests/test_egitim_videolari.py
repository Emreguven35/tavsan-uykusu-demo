"""
EĞİTİM VİDEOLARI — katalog, aşama eşlemesi, günün önerisi, ilerleme, Range.
LLM YOK, ağ YOK, prod DB YOK, ffmpeg YOK (sahte mp4 baytları yeter).

Kilitlenen dört davranış:
  • Katalog kategorilere ayrılıyor ve sıra korunuyor; TÜM videolar erişilebilir.
  • Aşama: anne beyanı (babies.mevcut_asama) plandan türetmeyi EZER.
  • todays_pick: aşamayla eşleşen izlenmemiş ilk video → eşleşen ilk →
    "genel" → katalogdaki ilk.
  • /media/videos/{slug}.mp4 Range isteğine 206 + Content-Range dönüyor
    (iOS AVPlayer bunu görmezse videoyu akış olarak kabul etmiyor).

Çalıştırma: python tests/test_egitim_videolari.py
"""
import os
import sys
import tempfile
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DB = Path(tempfile.gettempdir()) / "egitim_videolari_test.db"
if _DB.exists():
    _DB.unlink()
_MEDYA = Path(tempfile.gettempdir()) / "egitim_videolari_medya"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-en-az-otuz-iki-karakter-uzunlugunda"
os.environ["ENVIRONMENT"] = "development"
os.environ["MAIL_PROVIDER"] = "disabled"
os.environ["MEDIA_ROOT"] = str(_MEDYA)

from fastapi.testclient import TestClient                   # noqa: E402
from api.db import Base, SessionLocal, engine               # noqa: E402
import api.models                                           # noqa: E402,F401
from api.models import Baby, EducationVideo                 # noqa: E402
from api.main import app                                    # noqa: E402
from api.services import education, storage                 # noqa: E402

Base.metadata.create_all(bind=engine)
client = TestClient(app)
TODAY = datetime.now(timezone.utc).date()

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), str(detail)))


def _tok(eposta: str) -> dict:
    r = client.post("/api/v1/auth/register",
                    json={"email": eposta, "password": "TestPass123!"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def bebek_kur(H, **alanlar) -> str:
    govde = {"name": "Vid", "birth_date": (TODAY - timedelta(days=270)).isoformat()}
    govde.update(alanlar)
    return client.post("/api/v1/babies", headers=H, json=govde).json()["id"]


def bebek_guncelle(bid: str, **alanlar) -> None:
    db = SessionLocal()
    try:
        row = db.query(Baby).filter(Baby.id == _uuid.UUID(bid)).one()
        for k, v in alanlar.items():
            setattr(row, k, v)
        db.commit()
    finally:
        db.close()


class _SahteBebek:
    """asama_belirle saf fonksiyon; ORM'siz de çağrılabilmeli."""
    def __init__(self, basladi=None, bitti=None, beyan=None):
        self.training_started_at = basladi
        self.training_completed_at = bitti
        self.mevcut_asama = beyan


# =============================================================================
# KATALOG TOHUMLAMA — gerçek videolar.csv ile aynı biçim
# =============================================================================
KAYITLAR = [
    dict(slug="uyku-egitimine-giris", title="Giriş", description=None,
         category="baslarken", order_in_category=1,
         stage_tags=["egitim_oncesi", "genel"]),
    dict(slug="uyku-plani-hazirlama", title="Plan", description=None,
         category="baslarken", order_in_category=2,
         stage_tags=["egitim_oncesi", "genel"]),
    dict(slug="ilk-gunler", title="İlk günler", description=None,
         category="egitim_sirasinda", order_in_category=1,
         stage_tags=["besik_yani"]),
    dict(slug="kademeli-azaltma", title="Kademeli azaltma", description=None,
         category="egitim_sirasinda", order_in_category=4,
         stage_tags=["oda_ortasi", "kapi"]),
    dict(slug="tutarlilik", title="Tutarlılık", description=None,
         category="egitim_sirasinda", order_in_category=7,
         stage_tags=["esik", "bitis"]),
    dict(slug="egitim-sonrasi-duzen", title="Sonrası", description=None,
         category="egitim_sonrasi", order_in_category=1,
         stage_tags=["egitim_sonrasi"]),
]
_db = SessionLocal()
try:
    _sonuc = education.katalog_upsert(
        _db, KAYITLAR, {k["slug"]: 120 for k in KAYITLAR})
finally:
    _db.close()

print("0) Katalog upsert")
check("0) 6 video eklendi", len(_sonuc["eklenen"]) == 6, str(_sonuc))

_db = SessionLocal()
try:
    _sonuc2 = education.katalog_upsert(_db, KAYITLAR,
                                       {k["slug"]: 120 for k in KAYITLAR})
finally:
    _db.close()
check("0a) İkinci koşu hiçbir şey eklemiyor (idempotent upsert)",
      _sonuc2["eklenen"] == [] and _sonuc2["guncellenen"] == [], str(_sonuc2))

_db = SessionLocal()
try:
    _v = _db.query(EducationVideo).filter(
        EducationVideo.slug == "ilk-gunler").one()
    check("0b) URL'ler slug'dan türetildi ve GÖRELİ",
          _v.video_url == "/media/videos/ilk-gunler.mp4"
          and _v.poster_url == "/media/posters/ilk-gunler.jpg",
          f"{_v.video_url} | {_v.poster_url}")
    check("0c) stage_tags liste olarak geri okunuyor",
          _v.stage_tags == ["besik_yani"], str(_v.stage_tags))
    VID_ILK_GUNLER = str(_v.id)
    VID_GIRIS = str(_db.query(EducationVideo).filter(
        EducationVideo.slug == "uyku-egitimine-giris").one().id)
    VID_KADEMELI = str(_db.query(EducationVideo).filter(
        EducationVideo.slug == "kademeli-azaltma").one().id)
finally:
    _db.close()


# =============================================================================
# 1) AŞAMA — plandan türetme ve anne beyanının üstünlüğü
# =============================================================================
print("1) Aşama eşlemesi")
for gun, beklenen in [(1, "besik_yani"), (3, "besik_yani"), (4, "oda_ortasi"),
                      (6, "oda_ortasi"), (7, "kapi"), (9, "kapi"),
                      (10, "esik"), (12, "esik"), (13, "bitis")]:
    b = _SahteBebek(basladi=TODAY - timedelta(days=gun - 1))
    a = education.asama_belirle(b, TODAY)
    check(f"1) Eğitim günü {gun} → {beklenen}",
          a["kod"] == beklenen and a["kaynak"] == "plan", str(a))

check("1a) Eğitim başlamamış → egitim_oncesi",
      education.asama_belirle(_SahteBebek(), TODAY)["kod"] == "egitim_oncesi", "")
check("1b) Eğitim tamamlanmış → egitim_sonrasi",
      education.asama_belirle(
          _SahteBebek(basladi=TODAY - timedelta(days=5), bitti=TODAY),
          TODAY)["kod"] == "egitim_sonrasi", "")
check("1c) 13. günü geçmiş ama tamamlanmamış → bitis'te tutulur",
      education.asama_belirle(
          _SahteBebek(basladi=TODAY - timedelta(days=40)),
          TODAY)["kod"] == "bitis", "")
_beyanli = education.asama_belirle(
    _SahteBebek(basladi=TODAY - timedelta(days=8), beyan="kapi"), TODAY)
check("1d) Anne beyanı planı EZER (plan 'kapi' değil 'kapi'ye denk gelse de kaynak 'anne')",
      _beyanli["kod"] == "kapi" and _beyanli["kaynak"] == "anne", str(_beyanli))
_beyanli2 = education.asama_belirle(
    _SahteBebek(basladi=TODAY - timedelta(days=11), beyan="besik_yani"), TODAY)
check("1e) Plan 12. günde (esik) ama anne 'besik_yani' dedi → beyan kazanır",
      _beyanli2["kod"] == "besik_yani" and _beyanli2["kaynak"] == "anne",
      str(_beyanli2))
check("1f) Geçersiz beyan yok sayılır, plana düşülür",
      education.asama_belirle(
          _SahteBebek(basladi=TODAY, beyan="uzaydan"), TODAY
      )["kaynak"] == "plan", "")
check("1g) Etiket Türkçe geliyor",
      education.asama_belirle(_SahteBebek(basladi=TODAY), TODAY)["etiket"]
      == "Beşik yanı", "")


# =============================================================================
# 2) CANLI KATALOG
# =============================================================================
print("2) GET /education/videos")
H = _tok("video1@example.com")
BID = bebek_kur(H)
bebek_guncelle(BID, training_started_at=TODAY - timedelta(days=0))   # 1. gün

r = client.get("/api/v1/education/videos", headers=H)
check("2) 200", r.status_code == 200, r.text[:200])
k = r.json()
check("2a) Kategoriler sözlükteki SIRAYLA geliyor",
      [c["key"] for c in k["categories"]]
      == ["baslarken", "egitim_sirasinda", "egitim_sonrasi"],
      str([c["key"] for c in k["categories"]]))
check("2b) Kategori başlıkları Türkçe",
      [c["title"] for c in k["categories"]]
      == ["Başlarken", "Eğitim sırasında", "Eğitim sonrası"],
      str([c["title"] for c in k["categories"]]))
check("2c) Kategori içinde sıra korunuyor",
      [v["slug"] for v in k["categories"][1]["videos"]]
      == ["ilk-gunler", "kademeli-azaltma", "tutarlilik"],
      str([v["slug"] for v in k["categories"][1]["videos"]]))
check("2d) Sayaçlar: 6 video, 0 izlenmiş, 12 dakika",
      k["total_count"] == 6 and k["watched_count"] == 0
      and k["total_minutes"] == 12,
      f'{k["total_count"]} / {k["watched_count"]} / {k["total_minutes"]}')
check("2e) Aşama 1. günden türetildi",
      k["asama"] == {"kod": "besik_yani", "etiket": "Beşik yanı",
                     "kaynak": "plan"}, str(k["asama"]))
check("2f) todays_pick aşamayla eşleşen video (ilk-gunler)",
      k["todays_pick"] == VID_ILK_GUNLER, str(k["todays_pick"]))
check("2g) TÜM videolar listede (hiçbiri kilitli değil)",
      sum(len(c["videos"]) for c in k["categories"]) == 6, "")
check("2h) İlerleme alanı her videoda var",
      all(v["progress"] == {"position_sec": 0, "completed": False}
          for c in k["categories"] for v in c["videos"]), "")
check("2i) chapters boş liste olarak dönüyor",
      all(v["chapters"] == [] for c in k["categories"] for v in c["videos"]), "")


# =============================================================================
# 3) İLERLEME — upsert, tamamlanma, todays_pick'i etkileme
# =============================================================================
print("3) POST /education/videos/{id}/progress")
r = client.post(f"/api/v1/education/videos/{VID_ILK_GUNLER}/progress",
                headers=H, json={"position_sec": 30})
check("3) 200 ve konum kaydedildi",
      r.status_code == 200 and r.json()["position_sec"] == 30
      and r.json()["completed"] is False, r.text[:200])

r = client.post(f"/api/v1/education/videos/{VID_ILK_GUNLER}/progress",
                headers=H, json={"position_sec": 65})
check("3a) İkinci çağrı AYNI satırı günceller (upsert)",
      r.status_code == 200 and r.json()["position_sec"] == 65, r.text[:200])
_db = SessionLocal()
try:
    from api.models import VideoProgress
    check("3b) Satır YIĞILMADI (tek kayıt)",
          _db.query(VideoProgress).count() == 1,
          str(_db.query(VideoProgress).count()))
finally:
    _db.close()

r = client.post(f"/api/v1/education/videos/{VID_ILK_GUNLER}/progress",
                headers=H, json={"position_sec": 70, "completed": True})
check("3c) completed=true → izlendi damgası",
      r.json()["completed"] is True, r.text[:200])

r = client.post(f"/api/v1/education/videos/{VID_ILK_GUNLER}/progress",
                headers=H, json={"position_sec": 5})
check("3d) Geri sarmak 'izledim' damgasını SİLMEZ",
      r.json()["completed"] is True and r.json()["position_sec"] == 5,
      r.text[:200])

# Sona gelince mobil `completed` göndermese de izlendi sayılır.
r = client.post(f"/api/v1/education/videos/{VID_GIRIS}/progress",
                headers=H, json={"position_sec": 119})
check("3e) Sürenin %95'i geçildiyse completed kendiliğinden işaretlenir",
      r.json()["completed"] is True, r.text[:200])

r = client.post(f"/api/v1/education/videos/{VID_GIRIS}/progress",
                headers=H, json={"position_sec": 4000})
check("3f) Konum videonun süresini AŞAMAZ (kırpılır)",
      r.json()["position_sec"] == 120, r.text[:200])
check("3f2) Saçma büyük konum şemada reddedilir (>12 saat)",
      client.post(f"/api/v1/education/videos/{VID_GIRIS}/progress",
                  headers=H, json={"position_sec": 99999}).status_code == 422, "")

check("3g) Olmayan video → 404",
      client.post(f"/api/v1/education/videos/{_uuid.uuid4()}/progress",
                  headers=H, json={"position_sec": 1}).status_code == 404, "")
check("3h) Negatif konum → 422",
      client.post(f"/api/v1/education/videos/{VID_GIRIS}/progress",
                  headers=H, json={"position_sec": -5}).status_code == 422, "")
check("3i) Kimliksiz → 401",
      client.post(f"/api/v1/education/videos/{VID_GIRIS}/progress",
                  json={"position_sec": 1}).status_code == 401, "")

k2 = client.get("/api/v1/education/videos", headers=H).json()
check("3j) watched_count 2'ye çıktı", k2["watched_count"] == 2,
      str(k2["watched_count"]))
# Aşamayla eşleşen TEK video o ve izlenmiş: kural "hepsi izlendiyse eşleşen
# ilk" olduğu için öneri yine odur (boş öneri göstermek yerine).
check("3k) Eşleşenlerin hepsi izlenmişse öneri yine eşleşen ilk video",
      k2["todays_pick"] == VID_ILK_GUNLER, str(k2["todays_pick"]))

# Aşamada İKİ video olsa izlenmemiş olana geçer — asıl kural bu.
_db = SessionLocal()
try:
    _tut = _db.query(EducationVideo).filter(
        EducationVideo.slug == "tutarlilik").one()
    _tut.stage_tags = ["besik_yani", "esik", "bitis"]
    _db.commit()
    VID_TUTARLILIK = str(_tut.id)
finally:
    _db.close()
k2b = client.get("/api/v1/education/videos", headers=H).json()
check("3k2) Aşamada izlenmemiş bir video varsa öneri ONA geçer",
      k2b["todays_pick"] == VID_TUTARLILIK, str(k2b["todays_pick"]))
_db = SessionLocal()
try:
    _tut = _db.query(EducationVideo).filter(
        EducationVideo.slug == "tutarlilik").one()
    _tut.stage_tags = ["esik", "bitis"]          # fixture'ı geri al
    _db.commit()
finally:
    _db.close()


# =============================================================================
# 4) todays_pick — eşleşme kuralları
# =============================================================================
print("4) todays_pick seçimi")
H2 = _tok("video2@example.com")
BID2 = bebek_kur(H2)
bebek_guncelle(BID2, training_started_at=TODAY - timedelta(days=4))  # 5. gün
k4 = client.get("/api/v1/education/videos", headers=H2).json()
check("4) 5. gün (oda_ortasi) → kademeli-azaltma seçildi",
      k4["asama"]["kod"] == "oda_ortasi"
      and k4["todays_pick"] == VID_KADEMELI, str(k4["asama"]))

bebek_guncelle(BID2, mevcut_asama="egitim_sonrasi")
k4b = client.get("/api/v1/education/videos", headers=H2).json()
check("4a) Anne beyanı canlı akışta da planı ezer",
      k4b["asama"]["kod"] == "egitim_sonrasi"
      and k4b["asama"]["kaynak"] == "anne", str(k4b["asama"]))

# Hiçbir videonun taşımadığı bir aşama → "genel" etiketli videoya düşer.
bebek_guncelle(BID2, mevcut_asama="kapi", training_started_at=None)
_db = SessionLocal()
try:
    _kad = _db.query(EducationVideo).filter(
        EducationVideo.slug == "kademeli-azaltma").one()
    _kad.stage_tags = ["oda_ortasi"]              # artık "kapi" taşıyan yok
    _db.commit()
finally:
    _db.close()
k4c = client.get("/api/v1/education/videos", headers=H2).json()
check("4b) Aşamayla eşleşen video yoksa 'genel' etiketliye düşülür",
      k4c["todays_pick"] == VID_GIRIS, str(k4c["todays_pick"]))

check("4c) Bebeği olmayan kullanıcıda aşama 'egitim_oncesi'",
      client.get("/api/v1/education/videos",
                 headers=_tok("video3@example.com")).json()["asama"]["kod"]
      == "egitim_oncesi", "")
check("4d) Başkasının bebeği → 404",
      client.get(f"/api/v1/education/videos?baby_id={BID}",
                 headers=H2).status_code == 404, "")


# =============================================================================
# 5) MEDYA SUNUMU — Range 206 (iOS oynatıcı için ŞART)
# =============================================================================
print("5) /media/videos — Range")
_VERI = bytes(range(256)) * 400                    # 102.400 bayt sahte mp4
storage.depo().yaz(storage.video_yolu("ilk-gunler"), _VERI)
storage.depo().yaz(storage.poster_yolu("ilk-gunler"), b"\xff\xd8\xff" + b"x" * 500)

r = client.get("/media/videos/ilk-gunler.mp4")
check("5) Range'siz istek 200 ve tam dosya",
      r.status_code == 200 and len(r.content) == len(_VERI),
      f"{r.status_code} {len(r.content)}")
check("5a) Accept-Ranges: bytes ilan ediliyor",
      r.headers.get("accept-ranges") == "bytes", r.headers.get("accept-ranges"))
check("5b) Cache 1 yıl ve immutable",
      r.headers.get("cache-control") == "public, max-age=31536000, immutable",
      r.headers.get("cache-control"))
check("5c) Doğru içerik türü", r.headers.get("content-type") == "video/mp4",
      r.headers.get("content-type"))

r = client.get("/media/videos/ilk-gunler.mp4", headers={"Range": "bytes=0-1"})
check("5d) AVPlayer'ın ilk yoklaması (bytes=0-1) → 206",
      r.status_code == 206 and r.content == _VERI[0:2], f"{r.status_code}")
check("5e) Content-Range doğru",
      r.headers.get("content-range") == f"bytes 0-1/{len(_VERI)}",
      r.headers.get("content-range"))
check("5f) Content-Length aralık kadar",
      r.headers.get("content-length") == "2", r.headers.get("content-length"))

r = client.get("/media/videos/ilk-gunler.mp4",
               headers={"Range": "bytes=1000-1999"})
check("5g) Ortadan aralık (sarma) doğru baytları veriyor",
      r.status_code == 206 and r.content == _VERI[1000:2000], str(r.status_code))

r = client.get("/media/videos/ilk-gunler.mp4", headers={"Range": "bytes=102000-"})
check("5h) Açık uçlu aralık dosya sonuna kadar gider",
      r.status_code == 206 and r.content == _VERI[102000:]
      and r.headers.get("content-range") == f"bytes 102000-{len(_VERI)-1}/{len(_VERI)}",
      r.headers.get("content-range"))

r = client.get("/media/videos/ilk-gunler.mp4", headers={"Range": "bytes=-500"})
check("5i) Sondan aralık (bytes=-500)",
      r.status_code == 206 and r.content == _VERI[-500:], str(r.status_code))

r = client.get("/media/videos/ilk-gunler.mp4",
               headers={"Range": "bytes=999999-1000000"})
check("5j) Kapsam dışı aralık → 416 + Content-Range: bytes */boyut",
      r.status_code == 416
      and r.headers.get("content-range") == f"bytes */{len(_VERI)}",
      f"{r.status_code} {r.headers.get('content-range')}")

r = client.get("/media/videos/ilk-gunler.mp4", headers={"Range": "sayfa=1-2"})
check("5k) Anlaşılmayan Range yok sayılır (200, tam dosya)",
      r.status_code == 200 and len(r.content) == len(_VERI), str(r.status_code))

check("5l) Poster sunuluyor",
      client.get("/media/posters/ilk-gunler.jpg").status_code == 200, "")
check("5m) Olmayan slug → 404",
      client.get("/media/videos/hic-yok.mp4").status_code == 404, "")
check("5n) Yol kaçışı denemesi slug deseninde takılıyor",
      client.get("/media/videos/..%2F..%2Fetc.mp4").status_code in (400, 404),
      str(client.get("/media/videos/..%2F..%2Fetc.mp4").status_code))
check("5o) Video ucu AUTH İSTEMİYOR (oynatıcı başlık taşımıyor)",
      "authorization" not in {k.lower() for k in
                              client.get("/media/videos/ilk-gunler.mp4")
                              .request.headers}, "")

# İmzalı ses ucu BOZULMADI: /media/{yol:path} yakalayıcısı hâlâ çalışıyor.
check("5p) Ses paketi ucu hâlâ imza istiyor (rota sırası bozulmadı)",
      client.get("/media/voice-audio/a/b/c.mp3").status_code == 403,
      str(client.get("/media/voice-audio/a/b/c.mp3").status_code))


# =============================================================================
# 6) PATCH /babies — mevcut_asama
# =============================================================================
print("6) PATCH /babies mevcut_asama")
r = client.patch(f"/api/v1/babies/{BID}", headers=H,
                 json={"mevcut_asama": "kapi"})
check("6) 200 ve alan yanıtta",
      r.status_code == 200 and r.json().get("mevcut_asama") == "kapi",
      r.text[:200])
check("6a) Katalog yeni aşamayı gösteriyor",
      client.get("/api/v1/education/videos", headers=H).json()["asama"]
      == {"kod": "kapi", "etiket": "Kapı", "kaynak": "anne"}, "")
check("6b) Geçersiz aşama kodu 422",
      client.patch(f"/api/v1/babies/{BID}", headers=H,
                   json={"mevcut_asama": "uzay-istasyonu"}).status_code == 422, "")
r = client.patch(f"/api/v1/babies/{BID}", headers=H, json={"mevcut_asama": None})
check("6c) null'a çekilince plana geri dönülür",
      client.get("/api/v1/education/videos", headers=H).json()["asama"]["kaynak"]
      == "plan", "")


# =============================================================================
# 7) MANİFEST — gerçek videolar.csv okunabiliyor mu
# =============================================================================
print("7) videolar.csv")
_CSV = Path(os.getenv("VIDEO_KAYNAK") or r"C:\Users\Mert KORAL\tavsan-videolar") \
    / "videolar.csv"
if _CSV.exists():
    satirlar = education.manifest_satirlari(_CSV)
    check("7) CSV okundu ve slug'lar tekil",
          len(satirlar) == len({s["slug"] for s in satirlar}) and satirlar,
          f"{len(satirlar)} satır")
    check("7a) Tüm aşama etiketleri geçerli (manifest_satirlari doğruluyor)",
          all(s["stage_tags"] for s in satirlar), "")
    check("7b) Tüm kategoriler bilinen sözlükte",
          {s["category"] for s in satirlar}
          <= {k for k, _ in education.KATEGORILER},
          str({s["category"] for s in satirlar}))
else:
    check("7) CSV bulunamadı — atlandı", True, str(_CSV))

_gecersiz = Path(tempfile.gettempdir()) / "bozuk_manifest.csv"
_gecersiz.write_text(
    "dosya,slug,baslik,kategori,asama_etiketleri,aciklama,sira\n"
    "a.mp4,test,Test,baslarken,uzaydan,,1\n", encoding="utf-8")
try:
    education.manifest_satirlari(_gecersiz)
    check("7c) Bilinmeyen aşama etiketi HATA veriyor", False, "hata atılmadı")
except ValueError as e:
    check("7c) Bilinmeyen aşama etiketi HATA veriyor", "uzaydan" in str(e), str(e))


# --- Özet --------------------------------------------------------------------
print("\n" + "=" * 78)
print("EĞİTİM VİDEOLARI TEST SONUÇLARI")
print("=" * 78)
_gecen = 0
for ad, ok, detay in results:
    if ok:
        _gecen += 1
    else:
        print(f"[FAIL] {ad}\n       {detay}")
print("-" * 78)
print(f"TOPLAM: {_gecen}/{len(results)} geçti")
sys.exit(0 if _gecen == len(results) else 1)
