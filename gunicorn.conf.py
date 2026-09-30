"""Gunicorn ayarı — Railway üretimi (2026-09-30).

NEDEN GUNICORN: uvicorn'un kendi `--workers` modu uygulamayı HER worker'da
baştan import eder. Embedding modeli (torch + paraphrase-multilingual-MiniLM)
ve korpus böylece worker başına ayrı yükleniyordu: 4 worker × ~820 MB = 3,3 GB,
Railway faturasının %70'i (maliyet incelemesi, 2026-09-30). Gunicorn
`preload_app` ile uygulamayı ANA süreçte bir kez yükler, worker'lar fork ile
aynı bellek sayfalarını paylaşır (copy-on-write).

Worker sayısı `WEB_CONCURRENCY` env'inden okunur (varsayılan 2). Sunucu
darboğaz değil: 50 eşzamanlı GET /plans/today → p95 68 ms, CPU ort. 0,01 vCPU.

TORCH + FORK TUZAĞI: torch'un OpenMP havuzu ana süreçte bir kez kurulursa
fork'tan sonra çocukta KİLİTLENEBİLİR (GNU libgomp). Bu yüzden ana süreç
yükleme sırasında tek thread'le çalışır, her worker fork'tan sonra eski
değere döner. Ana süreç asla embed (encode) ÇALIŞTIRMAZ — önbellek bayatsa
yüklemeyi atlar ve eski davranışa düşülür (her worker kendi yükler).
"""
import gc
import logging
import os

bind = f"0.0.0.0:{os.getenv('PORT', '8080')}"
workers = int(os.getenv("WEB_CONCURRENCY") or 2)
worker_class = "uvicorn_worker.UvicornWorker"
preload_app = True
# Railway TLS'i kenarda sonlandırıyor; gerçek istemci IP'si/şeması başlıklarda.
forwarded_allow_ips = "*"
# Senkron plan üretimi (sync=true) ~130 sn sürebiliyor; iş threadpool'da
# koştuğu için event loop nabzı sürer, yine de gunicorn'un 30 sn'lik
# varsayılanı sınırda kalmasın.
timeout = 180
graceful_timeout = 30
keepalive = 5
accesslog = None                  # erişim logu Railway HTTP loglarında zaten var
loglevel = "info"

_log = logging.getLogger("tavsan.gunicorn")
_torch_thread = None               # ana süreçteki özgün değer (worker'da geri yüklenir)


def when_ready(server):
    """Fork ÖNCESİ, ana süreçte: model + embedding + TF-IDF'i bir kez yükle."""
    global _torch_thread
    try:
        import torch
        _torch_thread = torch.get_num_threads()
        torch.set_num_threads(1)
    except Exception:              # torch yoksa chatbot TF-IDF'e düşer
        pass
    from engine import chatbot
    if chatbot.on_yukle():
        server.log.info("Önyükleme: embedding modeli + korpus ana süreçte "
                        "yüklendi (%d birim), worker'lar paylaşacak",
                        chatbot.yuklu_birim_sayisi())
    else:
        server.log.warning("Önyükleme ATLANDI — her worker modeli kendisi "
                           "yükleyecek (bellek paylaşımı yok)")
    # Yüklenen nesneleri GC taramasından çıkar: aksi hâlde worker'daki ilk
    # toplama her nesnenin başlığına yazar ve paylaşılan sayfalar kopyalanır.
    gc.freeze()


def post_fork(server, worker):
    try:
        import torch
        if _torch_thread:
            torch.set_num_threads(_torch_thread)
    except Exception:
        pass
    # Ana süreçte açılmış olabilecek DB bağlantıları worker'a devredilmez.
    from api.db.session import engine
    engine.dispose(close=False)
