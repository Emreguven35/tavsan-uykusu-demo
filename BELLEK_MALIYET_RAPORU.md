# Railway bellek maliyeti — gunicorn preload + 2 worker · 2026-09-30

Sürüm **v2.4.6** (commit `3943e37`), `railway up` → SUCCESS, `/health` v2.4.6.

## 0. Neden
Maliyet incelemesi (aynı gün): 09-03 → 09-30 döneminin $23.33'ünün **%70'i
tavsan-api belleği** ($16.44). Ağ çıkışı $0.18, CPU $0.17. 2026-09-22'de uvicorn
`--workers 4`'e geçilince her worker torch + embedding modeli + korpusu AYRI
yükledi. Gidişat aylık ~$43 kullanımdı; hesaptaki $30 sert limit aşılır ve
Railway servisi durdururdu.

## 1. Değişiklik
- `uvicorn --workers 4` → `gunicorn -c gunicorn.conf.py`: `preload_app`,
  `uvicorn_worker.UvicornWorker`, worker sayısı `WEB_CONCURRENCY` (Railway
  env'de 2, koddaki varsayılan da 2).
- `when_ready` (fork öncesi, ana süreç): `chatbot.on_yukle()` modeli,
  embedding'leri ve TF-IDF'i bir kez yükler, ardından `gc.freeze()`.
  Worker'daki `init_index` hazır durumu görür ve hiçbir şey yapmaz.
- Torch + fork: ana süreç tek thread'le yükler (libgomp havuzu fork öncesi
  kurulmasın), `post_fork` özgün değeri geri yükler. Ana süreç encode
  çalıştırmaz. Önbellek bayatsa `on_yukle` False döner (TF-IDF'e düşmez) ve
  her worker eski yoldan kendisi yükler.
- `post_fork`: DB havuzu `dispose(close=False)`.

## 2. Bellek — önce / sonra (konteyner içi, `smaps_rollup` + cgroup)
| | Önce (4 worker, uvicorn) | Sonra (2 worker, preload) |
|---|---|---|
| Worker özel belleği (Private_Dirty) | 733–764 MB × 4 | 37–61 MB boşta; ilk sohbetten sonra ~212 MB × 2 |
| Paylaşılan (ana süreçten, CoW) | yok | ~670–700 MB, üç süreç ortak |
| cgroup toplamı | **3152 MB** (anon 3010) | **1348 MB** boşta → **~1690 MB** yük sonrası (anon ~1166) |
| Railway metriği | 3.30 GB | **1.76 GB** (04:25–04:45 sabit) |

**Paylaşım ÇALIŞIYOR.** Worker'ların RSS'i hâlâ ~1 GB görünüyor, ama bunun
~670 MB'ı paylaşılan sayfa (Shared_Dirty). Sayfa bir kez sayılıyor; bu yüzden
RSS toplamına değil cgroup toplamına bakın. İlk sohbet isteğinden sonra her
worker ~150 MB büyüyor (torch çıkarım tamponları), sonra sabit kalıyor.

İzleme: deploy sonrası ~22 dk (hedef 30 dk'ydı). Yereldeki izleme döngüsü,
yerel makinede bellek azaldığı için Claude Code tarafından durduruldu. Konteyner
tarafında sorun yoktu. 5 dk'lık Railway metriği aralık boyunca 1.76 GB'ta düz.

## 3. Maliyet
| | GB | Aylık (RAM $10/GB-ay) |
|---|---|---|
| Önce (son 7 gün ortalaması) | 3.63 | ~$36 |
| Sonra | 1.76 | **~$17.6** |
| **Kazanç** | | **~$18/ay** |

Hesap geneli aylık tahmin ~$24: tavsan-api ~$18, tavsan Postgres ~$3, diğer
projeler ~$2.5–3. $30 sert limitin altında kalıyor. $20'lik plan tabanının
biraz üstünde.

## 4. Yük ve uçtan uca (konteyner içinden, geçici test hesabıyla — sonunda silindi)
- Plan üretimi (async): **done, 141 sn**, `generated_with=claude`, 5 gün.
- `GET /plans/today`: 200.
- Sohbet: **6 eşzamanlı, 6/6 200**, 3–8.5 sn, 8–11 kaynak. İki worker da
  torch encode çalıştırdı, fork sonrası kilitlenme yok.
- 50 eşzamanlı `GET /plans/today` × 3 tur: **p95 250–292 ms**, 0 hata.
  (4 worker'la eski ölçüm p95 68 ms'ydi; 3 sn hedefinin çok altında kalıyor.)

## 5. Açık kalanlar
1. **AYRI İŞ — torch'suz hafif sorgu modeli (yapılmadı).** Paylaşılan ~700 MB'ın
   çoğu torch + MiniLM. Sorgu embedding'i ONNX/fastembed ya da bir embedding
   API'siyle yapılırsa torch kalkar. Beklenen etki: toplam ~0.6–0.8 GB,
   ek ~$9/ay. Korpus embedding'leri (`data/embeddings.npy`) aynı modelle
   üretilmeli ya da yeniden üretilmeli; retrieval kalitesi karşılaştırılmalı.
2. Diğer Railway projeleri (ida-backend = SEVSAR, muhasebe-backend,
   mellow-education) aynı faturada ~$2.5–3/ay. Kapatma kararı kullanıcının.
3. Sert limit $30. Trafik artarsa limit yükseltilmeli ya da (1) yapılmalı.
