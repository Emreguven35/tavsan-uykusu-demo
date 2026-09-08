"""
Plan üretimi eşzamanlılık sınırı ve kuyruk sırası (Faz O2).

NEDEN VAR: plan üretimi ~90-140 sn sürüyor ve o süre boyunca bir thread tutuyor.
Önceden BackgroundTasks ile uvicorn threadpool'unda sınırsız koşuyordu; yoğun
anda hem Anthropic hız sınırına çarpma hem de görünmez uzayan kuyruk riski vardı.
Artık adanmış bir havuz (MAX_ESZAMANLI) var ve istemciye kuyruk sırası dönüyor.

Bu dosya üç şeyi sabitler:
  1. Aynı anda en fazla MAX_ESZAMANLI iş KOŞAR.
  2. Bekleyen iş queue_position>0 döner; sırası gelince 0'a iner.
  3. submit() ÇAĞIRANI BLOKE ETMEZ — istemci 202'yi hemen alır.

Çalıştırma: python tests/test_plan_kuyrugu.py
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:                                   # Windows konsolu (cp1254) Türkçe/ok yazamıyor
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Test ortamı: api.* import'undan ÖNCE env sabitle (config zorunlu alan istiyor).
# DB'ye HİÇ dokunulmuyor — bu dosya yalnız kuyruk/havuz mantığını ölçer.
os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{(Path(tempfile.gettempdir()) / 'plan_kuyrugu_test.db').as_posix()}")
os.environ.setdefault("JWT_SECRET", "test-secret-en-az-otuz-iki-karakter-uzunlugunda")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("MAIL_PROVIDER", "disabled")

from api.services import plan_jobs  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


def is_ac(kullanici="u1", bebek="b1") -> str:
    return plan_jobs.create_job(kullanici, bebek)


# --- 1) Kuyruk sırası --------------------------------------------------------
plan_jobs.reset()
_j = is_ac()
check("1a) Yeni iş kuyrukta görünür (sıra 1)",
      plan_jobs.get_job(_j, "u1")["queue_position"] == 1,
      plan_jobs.get_job(_j, "u1")["queue_position"])

plan_jobs.reset()
_ids = [is_ac() for _ in range(3)]
_siralar = [plan_jobs.get_job(i, "u1")["queue_position"] for i in _ids]
check("1b) Sıra oluşturma sırasına göre artar", _siralar == [1, 2, 3], _siralar)

plan_jobs.reset()
_j = is_ac()
plan_jobs._set(_j, started=True)
check("1c) Başlayan işin sırası 0 olur",
      plan_jobs.get_job(_j, "u1")["queue_position"] == 0, "")

plan_jobs.reset()
_ilk, _ikinci = is_ac(), is_ac()
_once = plan_jobs.get_job(_ikinci, "u1")["queue_position"]
plan_jobs._set(_ilk, started=True)               # ilk iş slotu aldı
_sonra = plan_jobs.get_job(_ikinci, "u1")["queue_position"]
check("1d) Öndeki iş başlayınca arkadaki bir öne kayar",
      (_once, _sonra) == (2, 1), f"{_once} → {_sonra}")

plan_jobs.reset()
_ilk, _ikinci = is_ac(), is_ac()
plan_jobs._set(_ilk, status=plan_jobs.STATUS_DONE, started=True)
check("1e) Biten iş kuyrukta sayılmaz",
      plan_jobs.get_job(_ikinci, "u1")["queue_position"] == 1, "")

plan_jobs.reset()
_j = is_ac()
plan_jobs._set(_j, status=plan_jobs.STATUS_DONE, started=True)
check("1f) Biten işin kendi sırası 0",
      plan_jobs.get_job(_j, "u1")["queue_position"] == 0, "")

plan_jobs.reset()
_ilk, _ikinci = is_ac(), is_ac()
plan_jobs._set(_ilk, status=plan_jobs.STATUS_FAILED, started=True)
check("1g) Başarısız iş de kuyruktan düşer",
      plan_jobs.get_job(_ikinci, "u1")["queue_position"] == 1, "")

# --- 2) Sahiplik (Faz G1 kuralı bozulmasın) ----------------------------------
plan_jobs.reset()
_j = plan_jobs.create_job("u1", "b1")
check("2a) Başka kullanıcının işi görünmez", plan_jobs.get_job(_j, "u2") is None, "")

plan_jobs.reset()
plan_jobs.create_job("u1", "b1")
_benim = plan_jobs.create_job("u2", "b2")
check("2b) Kuyruk GENELdir — başkasının işi de sırayı ileri iter",
      plan_jobs.get_job(_benim, "u2")["queue_position"] == 2,
      plan_jobs.get_job(_benim, "u2")["queue_position"])

# --- 3) Eşzamanlılık sınırı --------------------------------------------------
check("3a) Sınır makul (1 < sınır <= 5)", 1 < plan_jobs.MAX_ESZAMANLI <= 5,
      plan_jobs.MAX_ESZAMANLI)
check("3b) Havuz gerçekten o sınırla kuruldu",
      plan_jobs._EXECUTOR._max_workers == plan_jobs.MAX_ESZAMANLI,
      plan_jobs._EXECUTOR._max_workers)

# Sınır+2 iş verilir; eşzamanlı koşan sayısı sınırı AŞMAMALI.
plan_jobs.reset()
_gercek_uretim = plan_jobs.run_generation
_sinir = plan_jobs.MAX_ESZAMANLI
_kilit = threading.Lock()
_durum = {"anlik": 0, "tepe": 0}
_devam = threading.Event()


def _sahte_uretim(job_id, *a, **kw):
    with _kilit:
        _durum["anlik"] += 1
        _durum["tepe"] = max(_durum["tepe"], _durum["anlik"])
    _devam.wait(timeout=5)
    with _kilit:
        _durum["anlik"] -= 1


plan_jobs.run_generation = _sahte_uretim
for _ in range(_sinir + 2):
    plan_jobs.submit(is_ac(), "b1", None, None)
time.sleep(0.4)                                   # havuzun dolmasını bekle
_tepe = _durum["tepe"]
_devam.set()
time.sleep(0.2)
check("3c) Aynı anda en fazla sınır kadar iş koşar", _tepe <= _sinir,
      f"eşzamanlı tepe={_tepe}, sınır={_sinir}")
check("3d) Sınır kadarı GERÇEKTEN paralel koştu (havuz tek thread'e düşmedi)",
      _tepe == _sinir, f"tepe={_tepe}")

# submit çağıranı bloke etmemeli — 202 gecikmez.
plan_jobs.reset()
_devam2 = threading.Event()
plan_jobs.run_generation = lambda *a, **kw: _devam2.wait(timeout=5)
for _ in range(_sinir):                           # havuzu doldur
    plan_jobs.submit(is_ac(), "b1", None, None)
time.sleep(0.2)
_t0 = time.perf_counter()
plan_jobs.submit(is_ac(), "b1", None, None)       # bu iş kuyrukta kalacak
_gecen = time.perf_counter() - _t0
_devam2.set()
check("3e) Havuz doluyken submit çağıranı bloke etmez", _gecen < 0.5,
      f"{_gecen:.2f} sn blokladı")

# Kuyrukta bekleyen iş, slot açılınca GERÇEKTEN koşmalı (kuyruk tıkanmaz).
plan_jobs.reset()
_kosanlar: list[str] = []
_kilit2 = threading.Lock()


def _kaydet(job_id, *a, **kw):
    with _kilit2:
        _kosanlar.append(job_id)


plan_jobs.run_generation = _kaydet
_hepsi = [is_ac() for _ in range(_sinir + 2)]
for _i in _hepsi:
    plan_jobs.submit(_i, "b1", None, None)
for _ in range(50):                               # en fazla 5 sn bekle
    if len(_kosanlar) == len(_hepsi):
        break
    time.sleep(0.1)
check("3f) Kuyruktaki işler slot açılınca koşar (tıkanma yok)",
      set(_kosanlar) == set(_hepsi),
      f"{len(_kosanlar)}/{len(_hepsi)} koştu")

plan_jobs.run_generation = _gercek_uretim         # gerçek fonksiyonu geri koy

# --- 4) started bayrağı üretim başında set edilir ----------------------------
# Bu olmazsa iş koşarken bile "kuyrukta" görünür ve sıra sayacı bozulur.
plan_jobs.reset()
_j = is_ac()


class _SahteHata(RuntimeError):
    pass


import api.db as _apidb  # noqa: E402

_gercek_session = _apidb.SessionLocal
_apidb.SessionLocal = lambda: (_ for _ in ()).throw(_SahteHata("db yok"))
try:
    plan_jobs.run_generation(_j, "b1", None, None)
except _SahteHata:
    pass
finally:
    _apidb.SessionLocal = _gercek_session
check("4a) run_generation başlarken started=True işaretler",
      plan_jobs._JOBS[_j]["started"] is True, plan_jobs._JOBS[_j])

# --- 5) max_tokens gerçek ihtiyaca göre (Faz O2 ölçümü) ----------------------
from engine import plan_generator as _pg  # noqa: E402

check("5a) max_tokens gözlenen çıktının (≈7.5k) üstünde",
      _pg.MAX_TOKENS >= 10000, _pg.MAX_TOKENS)
check("5b) max_tokens eski 28 günlük tavanın (16384) altına çekildi",
      _pg.MAX_TOKENS < 16384, _pg.MAX_TOKENS)

plan_jobs.reset()

# --- Özet --------------------------------------------------------------------
print("=" * 74)
print("PLAN KUYRUĞU / EŞZAMANLILIK TEST SONUÇLARI")
print("=" * 74)
passed = 0
for name, ok, detail in results:
    mark = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
        print(f"[{mark}] {name}")
    else:
        print(f"[{mark}] {name}\n       {detail}")

print("-" * 74)
print(f"TOPLAM: {passed}/{len(results)} geçti")
sys.exit(0 if passed == len(results) else 1)
