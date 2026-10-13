# Anne Sesi — uzman kayıt listesi

Bu liste uzmanın tek tek seslendireceği **5 ninni + 5 masalı** verir. Her kayıt bir kez yapılır; uygulama her annenin sesini bu kayıtlardan üretir (speech-to-speech). Ezgi, duygu, tempo ve duraklamalar **kayıttan** gelir.

**Nasıl teslim edilir:** telefondan m4a, wav ya da mp3 olarak; dosya adı aşağıdaki **içerik kimliği** (ör. `ninni_dandini.m4a`) ya da başlık olabilir. Dosyalar `data/anne_sesi_referans/` klasörüne konur ve `python tools/referans_hazirla.py` çalıştırılır (sessizlik kırpma, ses seviyesi, 44,1 kHz mono MP3).

**Kayıt kuralları:** sessiz, yankısız oda · fon müziği ve ikinci ses yok · telefon ağızdan 15–20 cm, kayıt boyunca aynı yerde · bağırmadan, ses patlamadan · her içerik tek parça · başta ve sonda uzun sessizlik bırakmayın.

**Hedef süreler:** ninni 1:30–2:30 · masal 4:00–4:30 (en fazla 6:00). 4:30'u aşan kayıt otomatik parçalara bölünür, birleşim yerinde küçük bir geçiş duyulabilir. Ücret ses dakikası başına (her anne için dakikada 1.000 kredi), bu yüzden hedefin üstüne çıkmamak maliyeti doğrudan düşürür.

Süre tahminleri yavaş masal temposuna göredir (~105 kelime/dk). ¶ numaraları kısaltma önerileri içindir, okunmaz.

| # | İçerik kimliği (dosya adı) | Başlık | Hedef süre | Metin (tahmini okuma) |
|---|---|---|---|---|
| 1 | `ninni_dandini` | Dandini Dastana | 1:30–2:30 | 35 kelime |
| 2 | `ninni_ay_isigi` | Ay Işığı Ninnisi | 1:30–2:30 | 39 kelime |
| 3 | `ninni_uyu_yavrum` | Uyu Yavrum | 1:30–2:30 | 37 kelime |
| 4 | uzman seçecek | uzman seçecek | 1:30–2:30 | — |
| 5 | uzman seçecek | uzman seçecek | 1:30–2:30 | — |
| 6 | `masal_aysecik_uyku_perisi` | Ayşecik ile Uyku Perisi | 4:00–4:30 | 511 kelime (~4:52) — kısaltma önerili |
| 7 | `masal_kelogan_sihirli_degnek` | Keloğlan ile Sihirli Değnek | 4:00–4:30 | 577 kelime (~5:29) — kısaltma önerili |
| 8 | `masal_cirkin_ordek_yavrusu` | Çirkin Ördek Yavrusu | 4:00–4:30 | 609 kelime (~5:48) — kısaltma önerili |
| 9 | `masal_uc_kucuk_domuzcuk` | Üç Küçük Domuzcuk | 4:00–4:30 | 619 kelime (~5:53) — kısaltma önerili |
| 10 | `masal_kirmizi_baslikli_kiz` | Kırmızı Başlıklı Kız | 4:00–4:30 | 629 kelime (~5:59) — kısaltma önerili |

---

## Ninniler

Ninni sözleri kısa (~35–40 kelime, bir kez söylenince ~1 dk). Ezgiyle **iki kez** söylenmesi hedef süreyi doldurur; ikinci tekrar daha yavaş ve kısık olabilir.

### 1. Dandini Dastana

- **İçerik kimliği:** `ninni_dandini` → `ninni_dandini.m4a` (ya da .wav/.mp3)
- **Hedef süre:** 1:30–2:30

**Söz:**

> Dandini dandini dastana, danalar girmiş bostana.  
> Kov bostancı danayı, yemesin lahanayı.  
> Uyusun da büyüsün ninni, tıpkı bir melek gibi ninni.  
> Yumuşacık yastığında, annesinin kokusuyla.  
> Uyu yavrum uyu, gözlerini kapa.  
> Ninni yavrum ninni, tatlı tatlı uyu.  

### 2. Ay Işığı Ninnisi

- **İçerik kimliği:** `ninni_ay_isigi` → `ninni_ay_isigi.m4a` (ya da .wav/.mp3)
- **Hedef süre:** 1:30–2:30

**Söz:**

> Ay ışığı pencerede, gümüş gibi süzülür.  
> Küçük bebek yatağında, mışıl mışıl uyusun.  
> Rüzgar bile durmuş, kuşlar susmuş dinliyor.  
> Ninni diyor yaprak yaprak, uyu güzel bebeğim.  
> Gözlerini kapat yavaşça, dünya seninle sakin.  
> Ninni ninni ay ışığı, uyu tatlı yavrum uyu.  

### 3. Uyu Yavrum

- **İçerik kimliği:** `ninni_uyu_yavrum` → `ninni_uyu_yavrum.m4a` (ya da .wav/.mp3)
- **Hedef süre:** 1:30–2:30

**Söz:**

> Uyu yavrum uyu, gecenin sessizliğinde.  
> Ay ışığı üstünde, yıldızlar seni izler.  
> Uyu yavrum uyu, hiçbir şeyden korkma.  
> Annen burada, elini tutuyor.  
> Rüyalarında uç, bulutlara değ.  
> Sabah güneşle uyan, ama şimdi uyu.  
> Ninni ninni ninni, tatlı yavrum ninni.  

### 4. Uzman seçecek

- **İçerik kimliği:** uzman seçince belirlenir (ör. `ninni_<kisa_ad>`); kataloğa (`data/stories.json`) ve `VOICE_PAKET_NINNILER` ayarına eklenir. Dosya adı bu kimlikle aynı olmalı.
- **Başlık:** uzman seçecek
- **Söz:** uzman seçecek — sözlerin tam metni de iletilsin (katalogda metin olarak durur).
- **Hedef süre:** 1:30–2:30

### 5. Uzman seçecek

- **İçerik kimliği:** uzman seçince belirlenir (ör. `ninni_<kisa_ad>`); kataloğa (`data/stories.json`) ve `VOICE_PAKET_NINNILER` ayarına eklenir. Dosya adı bu kimlikle aynı olmalı.
- **Başlık:** uzman seçecek
- **Söz:** uzman seçecek — sözlerin tam metni de iletilsin (katalogda metin olarak durur).
- **Hedef süre:** 1:30–2:30

---

## Masallar

### 6. Ayşecik ile Uyku Perisi

- **İçerik kimliği:** `masal_aysecik_uyku_perisi` → `masal_aysecik_uyku_perisi.m4a` (ya da .wav/.mp3)
- **Hedef süre:** 4:00–4:30 (en fazla 6:00)
- **Metin:** 511 kelime, tahmini okuma ~4:52
- **Önizleme masalı:** ilk 30 sn'si ücretsiz kullanıcıya önizleme olarak dinletilir; ilk 30 sn tamamlanmış bir cümleyle bitmeli.

**4,5 dk'yı aşıyor — kısaltma önerisi:**

- **¶6'yı (Dördüncü durak: göl, 52 kelime) çıkarın**; ¶7'deki "Beşinci durak" → "Dördüncü durak" olur. Kalan ≈ 459 kelime, ~4:22.
- ¶1'e dokunmayın: masalın ilk 30 sn'si ücretsiz önizleme olarak kullanılıyor.

**Metin:**

**¶1** O gece Ayşecik yatağında bir sağa, bir sola döndü. Yorganı ılıktı, yastığı yumuşaktı, ama uyku bir türlü gelmiyordu. Pencerenin dışında ay, gümüş bir tabak gibi duruyordu. Ayşecik gözlerini kapattı, içinden saydı, sonra yine açtı. "Uyku nerede acaba?" diye fısıldadı. Tam o anda cama küçücük bir ışık dokundu. Işık, parmak ucuyla vurulmuş gibi üç kez usulca titredi.

**¶2** Pencerenin önünde, avuç kadar bir peri duruyordu. Kanatları buğulu cam gibi saydamdı, saçları papatya tüyü gibi hafifti. "Ben Uyku Perisi'yim," dedi yavaşça. "Uykun yolu şaşırmış olabilir. Gel, birlikte arayalım." Ayşecik gülümsedi ve elini uzattı. Peri, avucuna ılık bir nefes gibi kondu. O anda oda hafifçe sallandı, sanki bir salıncakta oturuyorlardı. Perde şişti, ay biraz daha yakınlaştı.

**¶3** İlk durak bahçeydi. Papatyalar gündüzden yorulmuştu, taçlarını birer birer topluyorlardı. Uyku Perisi kanadını salladı ve üzerlerine ince bir toz serpti. "Şimdi izle," dedi. Ve orada, bahçedeki bütün papatyalar usulca uykuya daldı. Ayşecik onları sayarken kendi göz kapakları da birazcık ağırlaştı. "Devam edelim mi?" diye sordu peri. Ayşecik başını salladı, çünkü konuşmak bile tatlı bir tembellik gibiydi.

**¶4** İkinci durak derenin kenarıydı. Su, taşların üstünden geçerken kendi ninnisini söylüyordu. Kıyıda çakıl taşları sıra sıra dizilmişti, hepsi ıslak ve serin. Peri parmağını suya değdirdi, dere bir an duraksadı. Ve orada, derenin bütün küçük dalgaları usulca uykuya daldı. Su artık akmıyordu, sadece nefes alıyordu. Ayşecik derin bir esneme çekti, ağzını eliyle kapattı.

**¶5** Üçüncü durak ormanın kenarındaki mis kokulu çayırdı. Bir tavşan yavrusu, annesinin yanına sokulmuş, kulaklarını sırtına yatırmıştı. Yaprakların arasından ay ışığı damla damla süzülüyordu. Peri çok yavaş bir şarkı mırıldandı, sözleri belli değildi. Ve orada, ormandaki bütün küçük kulaklar usulca uykuya daldı. Rüzgâr bile ayaklarının ucuna basarak yürüdü. Ayşecik'in başı, perinin omzu kadar yumuşak bir buluta yaslandı.

**¶6** Dördüncü durak sessiz göldü. Göl, koca bir aynaya benziyordu; içinde ay iki kere duruyordu. Kıyıda bir kayık, ipine yorgun yorgun asılmıştı. Ördekler tüylerinin arasına gagalarını gömmüştü. Peri suya bir öpücük yolladı. Ve orada, gölün üstündeki bütün ışıltılar usulca uykuya daldı. Ayşecik "Ne kadar sakin," diye fısıldadı. Sesi kendi kulağına bile uzaktan geldi.

**¶7** Beşinci durak bulutların arasıydı. Bulutlar, yeni yıkanmış yorganlar gibi kabarıktı. Aralarında dolaşan rüzgâr, artık koşmuyor, sürünüyordu. Uyku Perisi kanatlarını iki kez yavaşça kapadı. Ve orada, gökyüzündeki bütün rüzgârlar usulca uykuya daldı. Yıldızlar da birer birer gözlerini kırpıştırdı. Ayşecik kollarını kendine çekti, ılık ve güvende hissetti.

**¶8** "Sanırım uykunu bulduk," dedi peri gülümseyerek. "Nerede?" diye sordu Ayşecik. Peri, kızın kalbinin üstünü çok hafif işaret etti. "Buradaydı. Sadece biraz beklemek istedi." Sonra ikisi geri döndüler; yol dönüşte daha da yavaştı. Bulutlar, göl, çayır, dere ve bahçe hepsi yerlerinde uyuyordu. Pencere, onları bekleyen bir dost gibi aralıktı.

**¶9** Ayşecik yatağına uzandı. Yorganı çenesine kadar çekti. Yastık, başını avucuna alır gibi kabardı. Uyku Perisi pencere pervazına oturdu ve iki kez esnedi. "Ben de biraz dinleneyim," dedi. Odada saatin tıkırtısı bile yumuşadı.

**¶10** Ve orada, evin içindeki bütün sesler usulca uykuya daldı. Terlikler kapının yanında uyudu. Oyuncak ayı raftan onu izledi, sonra o da uyudu. Ayşecik'in nefesi yavaşladı. Bir kez daha esnedi. Göz kapakları ağırlaştı.

**¶11** Ay pencereden içeri süzüldü. Işık yorganın üstüne yayıldı. Her şey ılıktı. Her şey sessizdi.

**¶12** Uyku Perisi eğildi ve çok hafif fısıldadı: iyi geceler Ayşecik, iyi geceler.

### 7. Keloğlan ile Sihirli Değnek

- **İçerik kimliği:** `masal_kelogan_sihirli_degnek` → `masal_kelogan_sihirli_degnek.m4a` (ya da .wav/.mp3)
- **Hedef süre:** 4:00–4:30 (en fazla 6:00)
- **Metin:** 577 kelime, tahmini okuma ~5:29

**4,5 dk'yı aşıyor — kısaltma önerisi:**

- ¶1: "Yatağında biraz daha uzanır, tavandaki çatlaklara bakar, hayaller kurardı." (−10)
- ¶2: son iki cümle (papatyalar, arı) (−17)
- ¶3: "Kuru bir dal gibi görünüyordu, ama üzerinde küçük gümüş halkalar vardı." (−11)
- ¶6: üç ev işinden yalnız biri kalsın (odunlar) (−12)
- ¶13: "Kimse nasıl olduğunu anlamadı, ama herkes güldü." (−8)
- ¶14: "hamur yoğurmayı … ekmeğin kokusunu duymayı" sayımı tek örneğe insin (−15)
- ¶16: ekmek yapımı iki cümleye insin (hamur yoğruldu, türkü söylendi) (−25)
- ¶19: ilk cümle (değneğin cevabı) çıkabilir (−10)
- Toplam ≈ −108 → ~469 kelime, ~4:28.

**Metin:**

**¶1** Dağların arkasında, ıhlamur kokulu küçük bir köy vardı. O köyün en ucundaki toprak evde Keloğlan annesiyle yaşardı. Keloğlan sabah erken kalkmayı hiç sevmezdi. Yatağında biraz daha uzanır, tavandaki çatlaklara bakar, hayaller kurardı. Ama annesi seslendiğinde hemen doğrulur, yüzünü yıkar, keçesini omzuna atardı. Çünkü Keloğlan rahatına düşkündü, tembel değildi.

**¶2** O sabah annesi, "Oğlum, kuyudan iki testi su getirir misin?" dedi. Keloğlan güldü, "Getiririm anne, hem de şarkı söyleyerek." Testileri aldı, yola çıktı. Yol boyunca papatyalar başını sallıyordu. Bir arı, Keloğlan'ın kulağının yanında yavaşça vızıldadı, sonra kendi işine gitti.

**¶3** Kuyunun yanında, yaşlı bir dut ağacının kökünde parlak bir şey gördü. Eğildi, baktı. Bir değnekti bu. Kuru bir dal gibi görünüyordu, ama üzerinde küçük gümüş halkalar vardı. Keloğlan değneği aldı, tozunu üfledi. Halkalar tıngırdadı, ince bir çıngırak sesi çıktı.

**¶4** "Merhaba," dedi değnek yumuşak bir sesle. "Ben dileklerin değneğiyim. Beni yere üç kez vur, ne istersen söyle."

**¶5** Keloğlan şaşırdı ama korkmadı. Değneği usulca yere vurdu, bir, iki, üç. "Şu testiler kendiliğinden dolsun," dedi. O anda testilerin içinde şırıl şırıl bir ses duyuldu. İkisi de ağzına kadar berrak suyla dolmuştu. Keloğlan kahkaha attı, testileri kaldırdı, eve doğru yürüdü.

**¶6** Annesi suyu görünce sevindi. "Ne çabuk döndün," dedi. Keloğlan omuz silkti, "Yolda hızlı yürüdüm anne." Değneği kuşağına sakladı. Bütün gün küçük dilekler diledi. Odunlar kendi kendine ocağın yanına dizildi. Süpürge kendi başına avluyu süpürdü. Çamaşırlar ipte, rüzgâr esmeden, mis gibi kurudu.

**¶7** Akşam olunca Keloğlan minderine oturdu. Karnı tok, elleri bomboştu. Tuhaf bir sessizlik vardı evde. Annesi ocağın yanında oturuyor, ateşe bakıyordu.

**¶8** "Anne," dedi Keloğlan, "bugün hiç yorulmadım. Ama içim biraz boş."

**¶9** Annesi güldü. "Eskiden odun taşırken bana masal anlatırdın. Bugün anlatmadın."

**¶10** Keloğlan durdu, düşündü. Doğruydu. Odunları değnek taşımıştı, masalı kimse anlatmamıştı.

**¶11** Ertesi sabah Keloğlan değneği yine çıkardı. "Bir dileğim var," dedi. "Ama bu kez benim için değil." Değnek halkalarını şıkırdattı. "Söyle bakalım."

**¶12** "Köyün çeşmesi yıllardır damla damla akıyor. Suyu bol olsun. Herkes kovasını kolayca doldursun."

**¶13** Değnek bir hoş titredi. "Bu güzel bir dilek," dedi. Keloğlan onu üç kez yere vurdu. Uzaktan, çeşmenin oradan, gür bir su sesi duyuldu. Komşular koştu, çocuklar avuçlarını suya tuttu. Kimse nasıl olduğunu anlamadı, ama herkes güldü.

**¶14** O gün Keloğlan eve dönerken düşündü. Değnek her şeyi yapabiliyordu. Ama en çok sevdiği şeyi yapamıyordu. Annesiyle yan yana oturup hamur yoğurmayı, unlu parmaklarla gülüşmeyi, tandırın sıcağında bekleyip ekmeğin kokusunu duymayı yapamıyordu. Onu ancak Keloğlan'ın kendi elleri yapabilirdi.

**¶15** Eve gelince annesine, "Anne, bu akşam ekmek yapalım mı?" dedi. Annesi başını kaldırdı, gözleri parladı. "Yapalım oğlum."

**¶16** Un elendi, su ısıtıldı, hamur teknesi ortaya kondu. Keloğlan kollarını sıvadı. Hamuru yoğurdu, avuçlarıyla bastırdı, çevirdi. Annesi tuzu serpti, ikisi birlikte üstünü örttü. Beklerken annesi eski bir türkü söyledi, Keloğlan da mırıldandı. Ekmek pişince evin içi öyle güzel koktu ki, kedi bile kapının önünde durdu.

**¶17** Keloğlan sıcak ekmeği böldü, yarısını annesine verdi. "Bu, değneğin yapabileceği hiçbir şeye benzemiyor," dedi. Annesi onun kel başını usulca öptü.

**¶18** Gece olduğunda Keloğlan değneği aldı, avluya çıktı. Yıldızlar taze taze parlıyordu. "Sana teşekkür ederim," dedi değneğe. "Sen çeşmeyi bol akıttın, bu bana yeter. Şimdi başka bir çocuk seni bulsun. Belki o da güzel bir dilek diler."

**¶19** Değnek yumuşacık bir sesle, "İyi düşündün Keloğlan," dedi. Keloğlan onu dut ağacının kökündeki eski yerine bıraktı. Toprağı avucuyla düzeltti, üstüne birkaç yaprak koydu.

**¶20** Eve döndüğünde annesi yatağını çoktan sermişti. Keloğlan yorganın altına girdi. Elleri hamur kokuyordu, kolları hoş bir yorgunlukla ağırdı. Uzaktan çeşmenin sesi geliyordu, ince ve düzenli, bir ninni gibi.

**¶21** "İyi geceler anne," dedi. "İyi geceler oğlum," dedi annesi.

**¶22** Ocaktaki köz sessizce kızardı. Rüzgâr dut yapraklarını okşadı. Keloğlan gözlerini kapattı ve mutlu mutlu, derin derin uyudu.

### 8. Çirkin Ördek Yavrusu

- **İçerik kimliği:** `masal_cirkin_ordek_yavrusu` → `masal_cirkin_ordek_yavrusu.m4a` (ya da .wav/.mp3)
- **Hedef süre:** 4:00–4:30 (en fazla 6:00)
- **Metin:** 609 kelime, tahmini okuma ~5:48

**4,5 dk'yı aşıyor — kısaltma önerisi:**

- **¶6 (tarla faresi ailesi, 46 kelime) ve ¶7 (balıkçıl, 43 kelime) çıkarılabilir**: yolculuktaki bilgelik kaplumbağada (¶5) zaten var; ¶8 "Günler kısaldı…" ¶5'in ardından doğal akar.
- ¶9: "Uzaklarda bir tavşan izi gördü…" ile "Bir tarla kuşu ara sıra gelip haber getirdi." arasındaki cümleler (−25); tarla kuşunun "Bahar hazırlanıyor" sözü kalsın.
- ¶14: "Papatyalar nisanda açar … bütün ormanı gölgeler." örnekleri (−17)
- Toplam ≈ −131 → ~478 kelime, ~4:33. Biraz daha gerekirse ¶3'ün son iki cümlesi.

**Metin:**

**¶1** Yaz sonuydu, göl kenarındaki sazlıklar akşam rüzgârında usulca sallanıyordu. Orada, geniş yaprakların altında bir ördek annesi yumurtalarını bekliyordu. Bir sabah kabuklar tık tık sesler çıkardı. Küçük sarı yavrular birer birer dünyaya geldi. Anne onları tek tek saydı, kanadıyla usulca örttü.

**¶2** En büyük yumurta ise biraz daha bekledi. Sonunda o da açıldı. İçinden gri tüylü, uzun boyunlu bir yavru çıktı. Diğerlerinden büyüktü, ayakları daha genişti. Anne ördek ona baktı ve gülümsedi. "Sen sabırlı olanmışsın," dedi yumuşak bir sesle.

**¶3** Yavru gölde yüzmeyi hemen öğrendi. Aslında hepsinden iyi yüzüyordu, suyu hiç yormuyordu. Ama aynada gibi baktığı su yüzeyinde kendini görünce şaşırdı. Kardeşleri küçük ve yuvarlaktı, o ise uzun ve gri. Kimse ona bir şey söylemedi. Kimse gülmedi, kimse kırıcı olmadı. Yalnızca o, içinden "Ben buraya tam oturmuyorum," diye düşündü. Bu düşünce üzgün değil, biraz merakla karışıktı.

**¶4** Bir sabah gölün öbür kıyısını görmek istedi. Anneye sokuldu. "Biraz gezip geleceğim," dedi. Anne ördek onun tüylerini gagasıyla düzeltti. "Yolun açık olsun. Göl her zaman burada," diye yanıtladı.

**¶5** Yavru sazlıkların arasından süzüldü. Sonra bir dere buldu, dereyi izledi. Yol kenarında yaşlı bir kaplumbağa güneşleniyordu. "Nereye böyle acele?" diye sordu kaplumbağa. "Kendime benzeyen birini arıyorum," dedi yavru. Kaplumbağa başını yavaşça sağa yatırdı. "Ben de uzun süre kabuğumu ağır sandım. Sonra anladım, o benim evim. Acele etme, her şey zamanında anlaşılır."

**¶6** Yavru bu sözü kanadının altına koydu, saklar gibi. Dere onu bir kavak korusuna götürdü. Orada bir tarla faresi ailesi buğday tanelerini taşıyordu. Ona da bir tane verdiler. "Bizden büyüksün," dedi en küçük fare, "ama misafirlik boy ölçüsüne bakmaz." Yavru onlarla akşam serinliğine kadar oturdu, hikâyeler dinledi.

**¶7** Sonra bir bataklık kıyısında iri, sakin bir balıkçıl gördü. Balıkçıl tek ayak üzerinde duruyordu, hiç kıpırdamıyordu. "Böyle durunca ne oluyor?" diye sordu yavru. "Sular durulunca," dedi balıkçıl, "kendini daha iyi görüyorsun." Yavru da onun yanında bir süre durdu. Ayakları yoruldu ama içi rahatladı.

**¶8** Günler kısaldı, hava usulca soğudu. Yapraklar altın rengine döndü, sonra sessizce yere indi. Yavru küçük bir kamış gölcüğü buldu. Kıyısında, saz kümelerinin arasında kendine sıcak bir yer yaptı. Kar geldiğinde ortalık bembeyaz oldu. Ses yoktu. Dünya sanki uykuya geçmişti.

**¶9** Kış ona korkutucu gelmedi. Sadece çok sakin geldi. Bulutlar yumuşak yastıklar gibiydi. Kar taneleri hiç acele etmeden iniyordu. Yavru tüylerini kabarttı, gagasını kanadının altına soktu. Uzaklarda bir tavşan izi gördü, tanıdık gibi geldi. Geceler uzundu, yıldızlar buz gibi berraktı. O da uyudu, uyandı, biraz yüzdü, yine uyudu. Bir tarla kuşu ara sıra gelip haber getirdi. "Bahar hazırlanıyor," dedi, "sadece yavaş giyiniyor."

**¶10** Sonra bir gün buzlar çatırdayarak eridi. Söğüt dalları yeşile döndü. Hava ılık ve tatlı kokuyordu. Yavru gölün ortasına doğru yüzdü. Kanatlarını açtı ve şaşırdı: kanatları çok genişti, hiç böyle olmamışlardı.

**¶11** Tam o anda gökten üç beyaz kuş süzüldü. Uzun boyunlu, sessiz, çok zarif. Yavru onları izledi, kalbi yumuşacık çarptı. Onlara doğru yüzdü ve durgun suya baktı.

**¶12** Suda gri bir yavru yoktu. Suda beyaz tüylü, uzun boyunlu, sakin bir kuğu vardı. Kendi yansımasına uzun uzun baktı. Sonra usulca gülümsedi.

**¶13** "Sen bizdensin," dedi kuğulardan biri. "Nerelerdeydin?" Kuğu cevap verdi: "Kendimi arıyordum. Meğer büyümeyi bekliyordum."

**¶14** O gün öğrendi ki hiç kimse ona yanlış bakmamıştı. Sadece o, kendi zamanının biraz daha uzun olduğunu bilmiyordu. Papatyalar nisanda açar, ayçiçekleri ağustosta. Meşe ağacı yıllarca büyür, sonra bütün ormanı gölgeler. Herkesin büyüme zamanı farklıdır ve hiçbir zaman geç değildir.

**¶15** Öğleden sonra ördek gölüne uçtu. Anne ördek onu hemen tanıdı. "Ben senin sabırlı olanını biliyordum," dedi. Kardeşleri etrafında halka oldu, hiç şaşırmadılar. Birlikte akşam güneşinde yüzdüler.

**¶16** Gece indiğinde kuğu sazlıkların arasında yerini buldu. Su hafifçe kıpırdadı, kurbağalar uzakta fısıldadı. Kanatlarını göğsüne yaydı, uzun boynunu usulca kıvırdı. Ay gölün üstünde gümüş bir yol çizdi.

**¶17** Gözleri ağırlaştı. İçinde yalnızca yumuşak bir şükran kaldı. Uykuya dalarken düşündü: her şey tam zamanında oldu. Ve göl, onu bütün gece sevgiyle taşıdı.

### 9. Üç Küçük Domuzcuk

- **İçerik kimliği:** `masal_uc_kucuk_domuzcuk` → `masal_uc_kucuk_domuzcuk.m4a` (ya da .wav/.mp3)
- **Hedef süre:** 4:00–4:30 (en fazla 6:00)
- **Metin:** 619 kelime, tahmini okuma ~5:53

**4,5 dk'yı aşıyor — kısaltma önerisi:**

- ¶16 (yarın bank yapalım, 27 kelime) çıkabilir; ¶17 doğrudan bağlanır.
- ¶13: şarkı söyleme ve Fındık'ın sepet/iskemle cümleleri (−30); Zeytin'in "benim evim de bu evin içinde yaşıyor" sözü kalsın.
- ¶11: Fındık'ın evine gidiş tek cümleye insin (−20)
- ¶12: kurdun son iki tavsiyesi (kapı yönü, ocak) tek cümleye (−15)
- ¶5: sis/rüzgâr tasviri kısalsın (−15); ¶3 ve ¶14'ten birer tasvir cümlesi (−20)
- Toplam ≈ −127 → ~492 kelime, ~4:41; ¶1'in son cümlesi de kısalırsa ~4:35.

**Metin:**

**¶1** Yeşil bir tepenin arkasında, dere kenarında üç küçük domuzcuk yaşarmış. En büyüğünün adı Pamuk, ortancanın adı Fındık, en küçüğünün adı Zeytin'miş. Üçü de yumuşak kulaklı, pembe burunluymuş. O sabah annelerinin verdiği el arabasını doldurmuş, kendilerine birer ev kurmaya karar vermişler.

**¶2** Zeytin, tarlanın kenarındaki altın renkli samanları görmüş. "Ben evimi bunlardan yapacağım," demiş. "Hem kokusu güzel, hem çabuk biter." Gerçekten de akşama kalmadan küçük bir saman evi hazır olmuş. İçi sıcak sarıya boyanmış gibi parlıyormuş.

**¶3** Fındık, ormanın kıyısındaki incecik çöp ve dalları toplamış. Onları üst üste dizmiş, aralarına yosun tıkıştırmış. Ortaya küçük bir pencere bile açmış. "Benim evim daha sağlam," demiş gülerek. "Rüzgâr dalların arasından şarkı söyleyip geçer."

**¶4** Pamuk ise deredeki çamurdan tuğla yapmaya başlamış. Kalıpları doldurmuş, güneşte kurumaya bırakmış. İş yavaş gidiyormuş. Kardeşleri onunla şakalaşmış: "Sen daha temeli kuruyorsun, biz çoktan bittik." Pamuk aldırmamış, sadece elinin tersiyle alnını silmiş.

**¶5** Günler geçmiş, mevsim dönmüş. Bir akşam hava serinlemiş, tepelerin üstüne pamuk gibi bir sis çökmüş. Rüzgâr yumuşakça esiyor, ama içinde kışın ilk soğuğunu taşıyormuş. Zeytin, saman evinin içinde büzülmüş. Samanların arasından ince ince bir serinlik sızıyormuş. Fındık da battaniyesini kulaklarına kadar çekmiş; dalların arasındaki boşluklardan geçen hava, sırtını üşütüyormuş.

**¶6** Tam o sırada dışarıda ağır ve yorgun adımlar duyulmuş. Gelen, gri tüylü bir kurtmuş. Tüyleri çiğden ıslanmış, kuyruğu yorgunluktan sarkıyormuş. Bütün gün ormanda yol aramış, ısınacak bir yer bulamamış. Küçük saman evin ışığını görünce sevinmiş ve kapıyı usulca tıklatmış.

**¶7** "Kim o?" diye sormuş Zeytin.

**¶8** "Ben bir yolcuyum," demiş kurt. Sesi çok kısıktı, çünkü hem üşümüş hem susamış. "Isınabileceğim bir köşe arıyorum. İçeri girmesem de olur, kapının önünde otururum."

**¶9** Zeytin kapıyı azıcık açmış. Kurdun titreyen bıyıklarını, üşümüş pençelerini görünce yüreği yumuşamış. "Gel," demiş, "ama seni uyarayım, benim evim de üşüyor."

**¶10** Kurt içeri girmiş, dizlerini toplayıp oturmuş. Etrafına bakınmış. Samanların arasındaki aralıkları, tabandan gelen serinliği fark etmiş. "Sen bu evi çok güzel yapmışsın," demiş nazikçe. "Ama saman havayı tutamıyor. Sabaha kadar burnun buz gibi olur."

**¶11** Sonra üçü birlikte Fındık'ın çöp evine gitmişler. Orada da aynı şeyi görmüşler: dalların arası ışıklı, ama rüzgâr içinden geçiyor. Kurt elini duvara koymuş, başını sallamış. "Ben yıllardır ormanda dolaşırım," demiş. "Hangi duvarın sıcak tuttuğunu, hangi çatının yağmuru attığını bilirim. İsterseniz size yardım edeyim."

**¶12** Böylece dördü, Pamuk'un yarım kalmış tuğla evine gitmişler. Kurt kollarını sıvamış. Kuruyan tuğlaları taşımış, çamuru karıştırmış. Pamuk sıraları dizmiş, Fındık araları harçla doldurmuş, Zeytin de küçük eliyle yüzeyi düzeltmiş. Kurt onlara duvarın nasıl düz gideceğini, pencerenin nereye açılacağını göstermiş. "Kapıyı rüzgârın gelmediği tarafa koyun," demiş. "Ocağı da şu köşeye kurun, taşlar ısıyı sabaha kadar saklar."

**¶13** Çalışırken aralarında şarkı söylemişler. Kurt eski bir orman ezgisi bilmiş, kardeşler de nakaratı öğrenmiş. Ay tepenin üstüne çıkarken duvarlar yükselmiş. Çatıyı kalın tahtalarla kapatmışlar, üstüne kuru saman ve toprak sermişler. Zeytin, kendi saman evinden getirdiği samanı çatının altına döşemiş. "Bak," demiş, "benim evim de bu evin içinde yaşıyor." Fındık da ince dallarından bir sepet, bir de küçük iskemle örmüş.

**¶14** İşleri bitince ocağı yakmışlar. Alev usul usul canlanmış, taşları ısıtmış. Odanın içi bal rengi bir ışıkla dolmuş. Pamuk, tencerede sıcak elma çorbası hazırlamış. Kurda en büyük kâseyi vermişler. Kurt kâseyi iki eliyle tutmuş, ıslak tüyleri kurumaya başlamış. "Ben bugün yol aradım," demiş yavaşça, "ama arkadaş bulmuşum."

**¶15** Dördü ocağın önüne oturmuşlar. Zeytin, kurdun sıcacık tüylü koluna yaslanmış. Fındık kâsesini dizine koymuş, gözleri ağırlaşmış. Pamuk ateşe son odunu atmış, sonra o da yerine kıvrılmış. Dışarıda rüzgâr geziniyor, ama içeriye giremiyormuş; tuğlalar sıkı sıkı kenetlenmişmiş.

**¶16** Kurt, uykuya dalmadan önce bir şey mırıldanmış. "Yarın," demiş, "kapının önüne küçük bir bank yaparız. Yolcular otursun." Zeytin, "Ben de üstüne yumuşak bir minder koyarım," diye fısıldamış.

**¶17** Ateş kısılmış, ışık turuncudan mora dönmüş. Dört dost, taşların sakladığı sıcaklıkla, derin ve huzurlu bir uykuya dalmışlar. Dere de tepenin altından usulca akmaya devam etmiş. Ve o küçük tuğla ev, o geceden sonra tepenin en sıcak, en sessiz, en güvenli yeri olmuş.

### 10. Kırmızı Başlıklı Kız

- **İçerik kimliği:** `masal_kirmizi_baslikli_kiz` → `masal_kirmizi_baslikli_kiz.m4a` (ya da .wav/.mp3)
- **Hedef süre:** 4:00–4:30 (en fazla 6:00)
- **Metin:** 629 kelime, tahmini okuma ~5:59

**4,5 dk'yı aşıyor — kısaltma önerisi:**

- **¶5 (çiçek sayma, 34 kelime) ve ¶6 (sincap, 27 kelime) çıkarılabilir**; ¶4'ten ¶7'ye (ayak izi) geçiş doğal.
- ¶2: çorbanın malzeme cümlesi (−12)
- ¶9: kurdun çanta/tohum ve bot tasviri kısalsın (−20)
- ¶23: sohbetin son iki cümlesi (−15)
- ¶26: kavanoz ve incir vedası tek cümleye (−10); ¶30 (kapıda durma, 20 kelime) çıkabilir
- ¶31: "Yolda tek bir çiçek daha topladı…" (−10)
- Toplam ≈ −148 → ~481 kelime, ~4:35. Masalın en uzunu; mümkünse ¶18'deki kurdun konuşması da bir cümle kısalsın.

**Metin:**

**¶1** Ormanın kenarındaki küçük evde, kırmızı başlıklı bir kız yaşardı. Başlığını annesi dikmişti, kenarlarına da minik beyaz düğmeler eklemişti. Kız o başlığı çok severdi. Sabah uyanınca ilk işi onu takmak olurdu.

**¶2** O sabah annesi mutfakta bir tencere çorba pişirdi. İçine havuç, patates, biraz mercimek ve bir tutam nane attı. Mutfak öyle güzel koktu ki, pencereden içeri bakan arı bile durdu. Anne çorbayı kapaklı bir sefertasına doldurdu.

**¶3** "Büyükannen dün üşütmüş," dedi anne yumuşak bir sesle. "Bu çorbayı ona götürür müsün?"

**¶4** Kırmızı Başlıklı Kız hemen sepetini aldı. Sefertasını, bir dilim ekmeği ve iki tane kuru inciri içine yerleştirdi. Başlığını taktı, kapıda annesine el salladı.

**¶5** Orman yolu o gün çok sakindi. Ağaçların arasından süzülen ışık, patikaya altın lekeler bırakıyordu. Kız yol boyunca çiçek saydı. Bir, iki, üç papatya. Dört, beş çuha çiçeği. Sonra saymayı bıraktı, çünkü çiçekler çok fazlaydı.

**¶6** Yolda bir sincapla karşılaştı. Sincap bir kozalağı iki eliyle tutmuş, ciddi ciddi çevirip inceliyordu. Kırmızı Başlıklı Kız ona iyi günler diledi. Sincap kulaklarını oynattı, sonra dala tırmandı.

**¶7** Derken kız, patikanın kenarında büyük bir ayak izi gördü. İz yumuşak toprakta belli belirsiz duruyordu. Kız eğilip baktı, merakla gülümsedi. "Buradan biri geçmiş," diye düşündü. "Acaba kim?"

**¶8** Büyükannenin evi dere kenarındaydı. Bahçesinde nane ve kekik yetişirdi. Kız kapıya varınca hafifçe vurdu, sonra tokmağı çevirdi. İçeri girdiğinde durdu ve şaşkınlıkla etrafına bakındı.

**¶9** Salıncaklı koltukta, kocaman gri bir kurt oturuyordu. Başını yana yatırmış, kollarını göğsünde birleştirmiş, derin derin uyuyordu. Üzerinde yeşil bir ormancı yeleği vardı. Yanında bir çanta, çantanın içinde tohum paketleri duruyordu. Ayaklarının dibinde çamurlu botları vardı, botların yanında da bir demet mor yayla çiçeği.

**¶10** Kırmızı Başlıklı Kız korkmadı. Sadece şaşırdı, sonra usulca güldü. Çünkü kurt uyurken burnunu kırıştırıyordu, tıpkı bir yavru köpek gibi.

**¶11** Tam o anda büyükanne yatak odasından çıktı. Boynuna yün atkısını dolamıştı. Torununu görünce gözleri parladı.

**¶12** "Geldin demek," dedi büyükanne. "Misafirimizi de görmüşsün."

**¶13** "Kim bu?" diye fısıldadı kız.

**¶14** "Ormanın bekçisi," dedi büyükanne. "Bu sabah erken geldi. Bana çiçek getirdi, sobamın odununu taşıdı. Sonra koltuğa oturdu ve uyuyakaldı. Anlaşılan çok yorgunmuş."

**¶15** Kurt tam o sırada kirpiklerini araladı. Karşısında kırmızı başlıklı bir kız görünce utandı. Kulakları arkaya yattı, pençelerini yeleğinin kenarına sakladı.

**¶16** "Affedersiniz," dedi kısık bir sesle. "Uyumak istemezdim. Ben sadece... biraz oturayım demiştim."

**¶17** "Merhaba," dedi kız. "Ben çorba getirdim. Sıcacık."

**¶18** Kurt boğazını temizledi. Sesi kalındı ama çok yumuşaktı. "Ben ormanda tek başıma yaşıyorum," dedi. "Fidan dikiyorum, kuş yuvalarını sayıyorum, dereyi temiz tutuyorum. Ama bazen konuşacak kimse olmuyor. Bugün cesaretimi topladım ve kapıyı çaldım."

**¶19** Kırmızı Başlıklı Kız sepetini masaya koydu. "İyi yapmışsın," dedi. "Üç kişiye yeter bu çorba."

**¶20** Büyükanne üç tabak çıkardı. Kız sefertasını açtı, buhar tavana doğru yükseldi. Nane kokusu odayı doldurdu. Kurt tabağını iki eliyle tuttu, çok dikkatli üfledi. Sonra bir kaşık aldı ve gözlerini kapattı.

**¶21** "Hayatımda içtiğim en güzel çorba," dedi.

**¶22** Büyükanne güldü. Kız da güldü. Kurt de utangaç utangaç güldü, kuyruğu koltuğun altında hafifçe sallandı.

**¶23** Yemekten sonra sohbet ettiler. Kurt, ormanın en yaşlı çınarını anlattı. Kovuğunda üç baykuş yavrusunun uyuduğunu söyledi. Dere kenarında yeni açan sarı çiçeklerden bahsetti. Kız her cümlede biraz daha merakla dinledi. Büyükanne örgüsünü aldı, şişleri usul usul oynattı.

**¶24** Güneş pencereden içeri iyice eğildiğinde kurt ayağa kalktı. Botlarını giydi, çantasını omuzladı.

**¶25** "Gitmem gerek," dedi. "Akşam olmadan fidanları sulamalıyım."

**¶26** Kapıya kadar birlikte yürüdüler. Büyükanne ona bir kavanoz kekik reçeli verdi. Kırmızı Başlıklı Kız ise cebindeki incirlerden birini uzattı.

**¶27** "Yine gel," dedi. "Çarşamba günleri buradayım."

**¶28** Kurt başını salladı. Gözleri sevinçle küçüldü. "Gelirim," dedi. "Söz."

**¶29** Sonra patikaya çıktı, ağaçların arasında yavaşça kayboldu. Bir süre kuyruğunun ucu göründü, sonra o da yeşilliğe karıştı.

**¶30** Kız ile büyükanne kapıda biraz daha durdular. Rüzgâr nane kokusunu bahçeden alıp içeri taşıdı. Dere kendi kendine, alçak sesle şırıldıyordu.

**¶31** O akşam Kırmızı Başlıklı Kız eve dönerken hiç acele etmedi. Yolda tek bir çiçek daha topladı ve başlığının kenarına taktı. Ormanın bir yerinde, iyi kalpli bir ormancı fidanlarını suluyordu. Her şey yolundaydı.

**¶32** Ve dünyanın en sakin akşamı, ağaçların üstüne usulca örtüldü.

