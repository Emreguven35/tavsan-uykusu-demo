# Kayıt → eğitim akışı — gerçek anne bulguları (build 21) · 2026-09-28

Sürüm **v2.4.5** (commit `ea7435a`), `railway up` → SUCCESS, `/health` v2.4.5 ·
corpus_units 638. Build gerekmez: bütün düzeltmeler sunucuda, build 21 ve öncesi
aynı gövdeleri göndermeye devam ettiği hâlde düzeliyor. Kişisel veri yok; bebekler
denetim raporundaki kalıcı numarayla anılıyor.

## 0. İlayda'nın 3. cevapları
`ilayda-cevaplar-3/RAPOR.md` zaten vardı. Bu işte yalnız S3 ve S4 uygulandı.

- **S3 (gece yatışı) — karar: "geçebilir".** Alıntı: *"Evet, 12'yi geçmeyeceği
  şekilde geçebilir. 12'yi geçerse orada sıkıntı var. Bu sadece alışma evresi diye
  not düşülmeli."* Mutlak tavan **24:00** oldu (22:30 varsayılanı değil: İlayda
  farklı bir saat söyledi). "12" gece yarısı olarak okundu; teyit sorusu açık.
- **S4 (sabah hedefi).** Hedefler yaş tablosunda (`yas_bantlari.json` v1.5,
  `evrensel_kurallar.sabah_hedefi`):
  - 5–8 ay: 06:00
  - 9–10 ay: 07:00 (08:00'e kadar)
  - 11 ay → tek uyku: 06:00 (katı)
  - tek uyku / 18 ay+: 07:00 (08:00'e kadar)
  - 5 ay altı: 07:00 (İlayda saat vermedi)
  - "6.5" diye bir hedef yok.

## 1. Teşhis (prod, son 3 gün)
| Soru | Sonuç |
|---|---|
| A — sıfır süreli sleep/nap, 04:00–12:00 | **11 kayıt, 4 bebek, hepsi `sleep`** (Bebek 8: 4, Bebek 18: 2, Bebek 20: 4, Bebek 23: 1). Sıfır süreli `nap`: 0, `nap_skipped`: 0. |
| B — `/logs/batch` 4xx/5xx | **212 isteğin 212'si 200.** Uygulama logunda batch hatası yok, Railway kenarında da yok. Bebek 18'in cihazı 12:13'te 09:00 cevabını gönderdikten sonra **sunucuya hiç istek atmadı**. 13:02 sayacı sunucuya hiç ulaşmadı. Plan ekranındaki "10:30 kayıt yok / sıradaki 15:00" ifadesi, sunucunun 09:00 cevabını `sifir_sure` diye yok sayıp 07:00 varsayımıyla kurduğu plandı (deploy öncesi anlık görüntüyle birebir doğrulandı). "Tekrar gönder" sunucunun kayıt bazında döndürdüğü `skipped` listesinden geliyor. Bu sebepler hiçbir yerde loglanmıyordu, o yüzden hangi kaydın neden reddedildiği geçmişe dönük bilinemiyor. |
| C — 7 günden eski açık kayıt | **5 açık uyku kaydı, 3 kullanıcı** (Bebek 1, 3, 6). Asıl sebep başka çıktı: `feed` ve `night_wake` nokta olay oldukları için `ended_at`'leri hep boş. Eski filtre "o günden önce başlamış her açık kayıt"ı listeye aldığı için Bebek 20'nin bugün listesine 16–22 Eylül beslenmeleri taşınıyordu (19 kaydın 14'ü). Bebek 19'da da 14 kayıt vardı. |

## 2. Düzeltmeler
1. **Sabah cevabı = uyanma (K12.5).** 04:00–12:00 arasında başlayan sıfır süreli
   sleep/nap artık `wake` sayılıyor. Yok sayılmıyor, gündüz uykusu da sayılmıyor.
   Açık gece uykusu varsa o saatte kapanmış kabul ediliyor. Yeni gövdeyle (`wake`)
   birebir aynı çizelge çıkıyor (testle doğrulandı). Kapalı gece uykusunun
   bitişinden 15 dakikadan daha geç ve 12:00'den önce gelen uyanma artık sabah
   adayı (K10.1 son uyanış kuralı). GET /logs bu kayıtları "Sabah uyanışı"
   etiketiyle döndürüyor.
2. **B.** Her ret (kopya hariç) `tavsan.logs` WARNING olarak loglanıyor: sebep,
   tip ve saatler; not ve kimlik yazılmıyor. İki kalıcı ret yolu kapatıldı:
   - Gelecek saatli `nap_skipped` bugün içinde kabul ediliyor (anne 15:00 uykusunu
     13:00'te "atlandı" diye işaretleyince reddediliyordu).
   - `db_error` artık 503 dönüyor. Mobil bu sebebi kalıcı ret sayıyordu; şimdi
     sağlam kayıtlar yazılıyor ve batch idempotent olarak yeniden gönderiliyor.

   Açık sayaç kapatma ayrı bir savepoint'e alındı. Orada bir hata çıkarsa
   yazılmış kayıtlar artık 500 ile geri alınmıyor.
3. **GET /logs?date= (K14.3).** Listeye yalnız şunlar giriyor: bugün başlayan,
   bugün biten ve dün 18:00'den sonra başlayıp hâlâ süren **uyku**. 16 saatten eski
   açık uyku listeye hiç girmiyor. Nokta olaylar ertesi güne taşınmıyor. Motor da
   bir günden eski açık gece uykusunu bugünün gecesi saymıyor (Bebek 20'de 25.09
   19:38'den kalan sayaç 60 saatlik bir gece uykusu üretiyordu).
4. **Gün kaydırma kilitlendi.**
   - Gün sabah uyanışından kuruluyor; her kayıttan sonra sonraki uyku = son
     uyanış + pencere.
   - 1. uyku bir saat gecikirse günün kalanı da bir saat kayıyor.
   - Gündüz uykusu eksikse akşam penceresinde şekerleme ekleniyor ya da neden
     eklenemediği uyarıda yazıyor.
5. **Yatış tavanı.** Yatış = son uyku bitişi + pencere. Bandın üst ucu artık tavan
   değil; yatış yalnız 24:00'ü geçemiyor. Üst uç aşılınca plana `gec_yatis` izi ve
   "alışma evresine özgü" uyarısı ekleniyor.
6. **Sabah hedefi.** Yeni üretilen şablonlar yaşa göre hedefle kuruluyor. Mevcut
   şablonlar K1 gereği değişmiyor. Plana `adaptation.sabah_hedefi` alanı eklendi.
   Gerçek uyanış kabul edilen en geç saati 30 dakikadan fazla geçerse anneye bilgi
   uyarısı gösteriliyor.
7. **Sor yalnız premium.** `SOR_GUNLUK_UCRETSIZ = 0`, kural `sor_premium`.
   BETA_MODE açık olduğu için şu an kimse etkilenmiyor.

## 3. Testler
- Yeni kalıcı test `tests/test_gercek_gun.py`: **99/99** geçti.
  - İki bebek (14,8 aylık 36 hf prematüre ve 8 aylık), her biri iki ayrı gövdeyle
    (build 21 ve yeni `wake`) oynatılıyor.
  - Saat saat akış: 09:00 → 13:02 sayaç → 14:10 uyandı → kısa son uyku.
  - Her adımda `/plans/today` ve `/logs?date` birlikte kontrol ediliyor.
  - Ayrıca 7 günlük açık sayaç ve eski beslenmelerle bugün listesinin temiz
    kaldığı, 3 günlük açık gece sayacının da bugünün gecesi sayılmadığı
    doğrulanıyor.
- Bütün deterministik suiteler yeşil: 43 dosya.
- Kuralı değişen 10 suite yeni kurala göre güncellendi: W3, W7, 3b, M, L ile Sor
  kotası ve sürüm kontrolleri. Canlı LLM çağıran 5 suite koşturulmadı.

## 4. Canlı sonuç (deploy sonrası, sunucu tarafında yeniden hesaplandı)
**Bebek 18 (14,9 ay düzeltilmiş, 36 hf, 2 uyku)** — önce / sonra:
| | Önce (v2.4.4) | Sonra (v2.4.5) |
|---|---|---|
| Sabah | 07:00 varsayılan, 09:00 kaydı `sifir_sure` | **09:00 kayıttan** |
| 1. uyku | 10:30 | 12:30 |
| 2. uyku | 15:00 | 17:00 |
| Yatış | 19:30 | 21:30 (bant ucu 20:00; "alışma evresi" uyarısı) |
| Uyarı | — | "bu yaşta sabah hedefi 06:00" |
| Liste | 09:00 kaydı "Gündüz uykusu" | 09:00 kaydı "Sabah uyanışı" |

Sıradaki blok "son uykunun bitişi eksik" diyor. Bu doğru: 13:02 sayacı cihazdan
hiç gelmedi. Uygulama açılınca cihaz kuyruğundaki kayıt gidecek.

**Bebek 20 (bugün listesinde 19 kayıt görünen):**
- Liste 19 kayıttan **4 kayda** indi: iki sabah uyanışı, bugünün beslenmesi ve
  11:02–13:06 uykusu.
- Sabah uyanışı 07:00 artık kayıttan geliyor; önceden `sifir_sure` diye yok
  sayılıyordu.
- 3 gün önceden kalan açık gece sayacı artık plana karışmıyor.

**Denetim raporu** 2026-09-28 verisiyle yeniden üretildi: 6 bebek,
`/data/denetim/2026-09-28.html`.

## 5. Açık kalanlar
1. **İlayda'ya sorulacak (tek cümle):** "Gece yatışı 12'yi geçmesin" dediğin gece
   24:00 mı, ve 3 aylık bebekte de 22:51 gibi geç bir yatış kabul mü?
   Neden soruluyor: Bebek 20'de (3,3 ay) bant tavanı kalkınca planlanmış son uyku
   artık kısaltılmıyor, yatış 22:51'e kaydı.
2. **Sabah hedefi uyarısı sık çıkabilir.** 5–8 ay ve 11–15 ay grubunda "katı
   06:00" kuralı yüzünden 06:30'dan geç kalkan her bebeğe her gün bilgi uyarısı
   düşecek. Tolerans (30 dk) benim koyduğum bir değer; İlayda onaylamalı.
3. `gec_yatis.mutlak_tavan` alanı "00:00" olarak biçimleniyor. Anlamı 24:00;
   sonraki deploy'da düzeltilecek küçük bir görünüm sorunu.
4. Mobil (build gerektirir): sabah sorusu `wake` tipinde gönderilmeli.
   `kategori = sabah_uyanisi` için bir etiket/ikon eklenmeli (etiket şu an
   `kategori_etiket` alanından geliyor).
5. Eski açık sayaçların DB'de kapatılmadığı durum: liste ve motor bunları artık
   görmezden geliyor ama satırlar hâlâ açık. Kapatmak için
   `scripts/acik_sayac_kapat.py` ayrı bir işte, onayla koşturulmalı.
