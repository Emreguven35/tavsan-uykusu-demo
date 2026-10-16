# Anne Sesi referans kayıtları

`ANNE_SESI_STS` bayrağı açıkken her annenin ninni ve masalları bu kayıtlardan
speech-to-speech ile (ElevenLabs `eleven_multilingual_sts_v2`) annenin sesine
dönüştürülür. Ezgi, duygu, duraklama ve tempo kayıttan gelir; ses annenin olur.
Dosyası olmayan masal eskisi gibi TTS ile okunur; dosyası olmayan ninni hiç sunulmaz.

## Dosya adları

Uygulamanın okuduğu dosya: `{icerik_id}.mp3` (hazırlama betiğinin çıktısı).

| Dosya | İçerik |
|---|---|
| `ninni_dandini.mp3` | Dandini Dandini Dastana |
| `ninni_uyusun_da_buyusun.mp3` | Uyusun da Büyüsün |
| `ninni_e_bebegim.mp3` | E Bebeğim Ee |
| `ninni_atem_tutam.mp3` | Atem Tutam Ben Seni |
| `ninni_camlibelden.mp3` | Çamlıbel'den Çıktım Yayan |
| `masal_aysecik_uyku_perisi.mp3` | Ayşecik ile Uyku Perisi (önizleme masalı: ilk 30 sn'si kullanılır) |
| `masal_kelogan_sihirli_degnek.mp3` | Keloğlan ile Sihirli Değnek |
| `masal_cirkin_ordek_yavrusu.mp3` | Çirkin Ördek Yavrusu |
| `masal_uc_kucuk_domuzcuk.mp3` | Üç Küçük Domuzcuk |
| `masal_kirmizi_baslikli_kiz.mp3` | Kırmızı Başlıklı Kız |

**Ninni düz okunmaz (2026-10-16):** dosyası burada olmayan ninni pakete
girmez ve uygulamada listelenmez; dosya konunca kendiliğinden girer
(`ANNE_SESI_STS` açıkken). Masalın dosyası yoksa masal TTS ile okunur.

## Hazırlama

Uzman kayıtları telefondan gelir: **m4a, wav ya da mp3** bu klasöre bırakılır ve

    python tools/referans_hazirla.py

çalıştırılır. Betik dosya adını içerik kimliğiyle (ya da başlıkla) eşleştirir,
baş/son sessizliği kırpar, sesi normalize eder (−18 LUFS), 44,1 kHz mono
128 kbps `{icerik_id}.mp3` üretir ve süreleri listeler. Ham dosyalar silinmez,
`ham/` alt klasörüne taşınır (git'e girmez). Uygulama yalnız `{icerik_id}.mp3`
dosyalarını okur.

Gerçek ElevenLabs denemesi (tek çağrı, kredi harcar):
`python tools/sts_deneme.py data/anne_sesi_referans/ninni_dandini.mp3 --voice-id <test_ses>`

## Kayıt kuralları (uzman için)

Ayrıntılı liste, tam metinler ve kısaltma önerileri: `docs/uzman_kayit_listesi.md`.

- **Ortam:** sessiz oda, müzik/fon sesi yok, yankısız (perdeli, eşyalı oda).
  Klima, buzdolabı, trafik sesi dönüşüme taşınır.
- **Tek ses:** yalnız seslendiren kişi. Bebek sesi, ikinci konuşmacı, enstrüman yok.
- **Telefon/mikrofon:** ağızdan 15–20 cm, kayıt boyunca aynı yerde. Ses
  patlamasın; nefesler çok yüksek olmasın.
- **Süre:** ninni 1:30–2:30, masal 4:00–4:30 (en fazla 6:00). Her kayıt tek
  parça. 4:30'u aşan kayıt otomatik bölünüp birleştirilir; bölme noktasında
  küçük bir geçiş duyulabilir.
- **Baş/son sessizlik:** betik kırpar, yine de uzun bırakmayın.
- **Önizleme masalı:** Ayşecik ile Uyku Perisi'nin ilk 30 sn'si (ayar:
  VOICE_ONIZLEME_SN) ücretsiz önizleme olarak kullanılır; bu kısım tamamlanmış
  bir cümleyle bitsin.
