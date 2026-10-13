# Anne Sesi referans kayıtları

`ANNE_SESI_STS` bayrağı açıkken her annenin ninni ve masalları bu kayıtlardan
speech-to-speech ile (ElevenLabs `eleven_multilingual_sts_v2`) annenin sesine
dönüştürülür. Ezgi, duygu, duraklama ve tempo kayıttan gelir; ses annenin olur.
Dosyası olmayan içerik eskisi gibi TTS ile okunur.

## Dosya adları

Dosya adı içerik kimliğiyle **birebir** aynı olmalı: `{icerik_id}.mp3`.

| Dosya | İçerik |
|---|---|
| `ninni_dandini.mp3` | Dandini Dastana |
| `ninni_ay_isigi.mp3` | Ay Işığı Ninnisi |
| `ninni_uyu_yavrum.mp3` | Uyu Yavrum |
| `masal_aysecik_uyku_perisi.mp3` | Ayşecik ile Uyku Perisi (önizleme masalı: ilk 30 sn'si kullanılır) |
| `masal_kelogan_sihirli_degnek.mp3` | Keloğlan ile Sihirli Değnek |
| `masal_cirkin_ordek_yavrusu.mp3` | Çirkin Ördek Yavrusu |
| `masal_uc_kucuk_domuzcuk.mp3` | Üç Küçük Domuzcuk |
| `masal_kirmizi_baslikli_kiz.mp3` | Kırmızı Başlıklı Kız |

Yeni iki ninni önce `data/stories.json` kataloğuna ve `VOICE_PAKET_NINNILER`
ayarına eklenir; kayıtları da aynı kimlikle buraya konur.

## Kayıt kuralları (uzman için)

- **Biçim:** MP3, 44,1 kHz, 128 kbps (ya da üstü), tercihen mono. WAV/M4A
  kabul edilmez; dışa aktarırken MP3 seçin.
- **Ortam:** sessiz oda, müzik/fon sesi yok, yankısız (perdeli, eşyalı oda).
  Klima, buzdolabı, trafik sesi dönüşüme taşınır.
- **Tek ses:** yalnız seslendiren kişi. Bebek sesi, ikinci konuşmacı, enstrüman yok.
- **Mikrofon:** ağızdan 15–20 cm, kayıt boyunca aynı mesafe. Ses patlamasın
  (tepe seviyesi yaklaşık −6 dB); nefesler çok yüksek olmasın.
- **Süre:** ninni en fazla 3 dk, masal en fazla 6 dk. Her kayıt tek parça.
  4 dk 30 sn'yi aşan kayıt otomatik bölünüp birleştirilir; bölme noktasında
  küçük bir geçiş duyulabilir. Mümkünse masallar da 4 dk 30 sn altında kalsın.
- **Baş/son sessizlik:** 1 sn'yi geçmesin (ücret ses dakikası başınadır).
- **Önizleme masalı:** Ayşecik ile Uyku Perisi'nin ilk 30 sn'si ücretsiz
  önizleme olarak kullanılır. İlk 30 sn masalın girişi gibi tamamlanmış bir
  cümleyle bitsin.
