# Müzik modu v3: "Dionysos" (plan, kod yok)

Mod adı: **Dionysos** (Arda seçti, 2026-11-18; Yunan mitolojisinde coşku, dans ve gece eğlencesinin tanrısı). Planda geçen "Akış" adı yerine.

Tarih: 2026-11-18. Arda ile konuşularak belirlendi, henüz uygulanmadı. Eski plan: `ALGORITMA-v2-eski.md`.

## Neden v3
- Arda'nın favorisi Spektrum (Basit mod): ışık sesle doğrudan akıyor.
- v2 (Parti/Konser/Chill) sesi "yorumladı": yumuşatmalar, bölüm paletleri, her vuruşta nabız. Arda'ya "çalışmıyor gibi" geldi. Ayrıca canlıda müzikle hiç ölçülmedi.
- Sonuç: temel his Spektrum olacak. Üstüne yalnız müziğin gerçekten dramatik anlarında olaylar eklenecek.

## Arda'nın zevki (kararlar)
1. **Temel his Spektrum:** parlaklık sesin gücünü izler, renk bas-tiz dengesinden gelir (bas → kırmızı-turuncu, tiz → mavi-mor).
2. **Az renk değişimi:** renk çoğunlukla sabit kalır, yavaş kayar. Vuruşta renk kayması düşük olur (Arda'nın ayarı 4°).
3. **Sert anlar seyrek ve müziğe bağlı:** her vuruşta değil, sabit sıklıkta da değil. Müzik ani kesilince ışık kesilir, ani dönünce ışık tam parlaklıkla açılır.
4. **Beyaz yok:** patlamalar o anki renkte tam parlaklıkla olur.
5. **Vokalin duygusu rengi yönetir:** genelde aynı renk ve yavaş değişim. Vokalin duygu tonu değiştiği an ani zıt renge geçiş ya da kapanma/parlaklık düşüşü olur.

## Algoritma: üç katman

### 1. Taban (her an, Spektrum gibi)
- Parlaklık: sesin gücü (şarkının dinamik aralığına göre ölçeklenmiş).
- Renk: bas-tiz dengesi + vokal duygu tonu, yavaş değişir.

### 2. Olaylar (seyrek, müziğin kendisi tetikler)
| Olay | Algılama | Işık |
|---|---|---|
| Müzik ani kesildi (durak, drop öncesi sessizlik, "hey!" arası) | kısa süreli gürlük, son birkaç saniyenin ortalamasının çok altına aniden düşer | kapanır |
| Müzik ani döndü (drop, nakaratın sert girişi, durak sonu) | gürlük ve bas ani sıçrar | o anki renkte tam parlaklıkla açılır |
| Vokal duygusu yükseldi (yumuşaktan güçlüye, bağırma, yüksek nota) | vokal yoğunluğunda dönüm noktası (yukarı) | ani zıt renge geçiş, tam parlaklık |
| Vokal duygusu düştü (fısıltı, kırılgan, falseto, sakinleşme) | vokal yoğunluğunda dönüm noktası (aşağı) | kısılma ya da kapanıp yeni yumuşak renkte açılma |

Olaylar mümkünse vuruşa oturtulur (ritim takibi).

### 3. Güvenlik
Saniyede en fazla 2-3 sert geçiş. Yalnız art arda gelen çok hızlı kesme serilerinde devreye girer.

## Vokal duygusu: iki eksen
- **Yoğunluk (arousal):** sakin ↔ gergin/güçlü. Ses gücü, perde yüksekliği, ses sertliği/parlaklığı, vibrato, nefeslilikten ölçülür. Canlıda güvenilir.
- **Ton (valence):** karanlık/hüzünlü ↔ aydınlık/neşeli. Armoni (minör/majör), tempo, tını. Zor; canlıda kaba, kütüphanede daha iyi (hazır müzik duygu modelleri, ör. Essentia'nın yoğunluk-ton modelleri; Demucs ile ayrılmış vokal üzerinde).
- **Işığa çevirisi:** ton → renk (hüzünlü = soğuk, neşeli = sıcak), yoğunluk → parlaklık ve doygunluk.
- **Asıl önemli olan:** duygunun ne olduğu değil, **değiştiği an** (dönüm noktası). Yön kuralı: yükselişte zıt renk, düşüşte kapanma ya da kısılma.

## Canlı görselleştirici: çok satırlı, renkli, katkı gösteren
Müzik kartındaki tek "seviye" dalgasının yerine. Amaç: ışığın o an **neye** tepki verdiğini görmek.

**Açılıp kapanabilir (Arda: "fixed olmasın"):**
- Görselleştiricinin tamamı panelde gizlenip gösterilebilir (anahtar).
- Her satır ayrı ayrı açılıp kapanır. Yalnız istenen satırlar görünür.
- Seçim kaydedilir, panel yeniden açılınca aynı kalır.
- Kart içi kaydırma yasağı geçerli: açık satır sayısı kart yüksekliğine göre dağılır. 4'lü görünümde sığmayan satırlar yalnız büyütülmüş kartta görünür.

**Satırlar (sağdan sola akan):**
| Satır | Gösterdiği |
|---|---|
| Vokal | şarkıcının ses gücü; söylemiyorsa düz |
| Davul | kick (büyük), snare (orta), hi-hat (küçük) vuruş işaretleri |
| Bas | sub + bas enerjisi |
| Melodi | davul dışı enstrümanların enerjisi |
| Işık | şeride giden gerçek renk ve parlaklık + yığılmış katkı çubuğu |

**Renk ve katkı:**
- Her satırın dalgası, o kaynağın ışığı ittiği renkte boyanır (bas → kırmızı-turuncu, tiz/melodi → mavi-mor, vokal → duygu tonunun rengi).
- Dalganın kalınlığı/parlaklığı o kaynağın o anki katkı payıdır.
- Işık satırında yığılmış çubuk: ör. "Vokal %55 · Bas %30 · Davul %15".
- Olay etiketleri satırların üstünde: "Müzik kesildi", "Drop", "Duygu ↑", "Duygu ↓".
- Bunun için v3 ışık hesabı baştan **açıklanabilir** (parçaların toplamı) kurulur; parlaklık payları kesin, renk payları yaklaşık (renk tonu dairesel).
- Basit modda da analiz arka planda çalışır (blok başına ~1 ms ek).

## Yeniden kullanılacak hazır parçalar
- Ritim takibi (`analiz/ritim.py`, gerçek şarkılarda tempo sabit).
- Vokal algılama ve vokal gücü (`analiz/vokal.py`, gerçek vokalle uyum 0,6-0,7).
- Ani patlama algılama (`analiz/isik.py` içindeki "anlık patlama").
- Hızlı kanal DP28 (saniyede 16 güncelleme).
- Laboratuvar: kayıt, grafik, sanal şerit videosu, Demucs.

Atılacak: v2 ışık motorunun yorumlayan kısmı (bölüm paletleri, sürekli nabız, yumuşatmalar).
Yeni yazılacak tek ciddi parça: vokal duygu ve dönüm noktası algılama.

## Yol haritası
1. **Akış modu, canlı:** Spektrum + olay katmanı. Önce müzik ani kesilip dönünce ışığın kesilip dönmesi, sonra kaba duygu (yoğunluk) ile renk geçişi. Arda canlıda dener. Canlı görselleştirici (açılıp kapanabilir satırlar, katkı renkleri) bu aşamayla birlikte gelir; ayar yaparken neyin ışığı etkilediği görünür.
2. **Kütüphane:** dinlenen şarkılar kaydedilir, çevrimdışı tam analiz yapılır (geleceği bilerek kusursuz zamanlama, daha isabetli duygu), parmak iziyle tanınır, hazır şov senkron oynar.
3. **Zevk ayarı:** Arda'nın "burada duygu değişti" işaretleri ve A/B seçimleriyle ince ayar.
4. **Sadeleştirme:** v2 modları (Parti/Konser/Chill) panelden kaldırılır ya da gizlenir.

## İkinci mod: OtoPilot (plan, kod yok)
Arda'nın kararları (2026-11-18):
- **Ayrı bir mod:** adı **OtoPilot**. Dionysos'tan bağımsız.
- **Temeli Spektrum:** Spektrum modu çalışır; OtoPilot onun ayarlarını şarkı çalarken şarkıya göre **canlı olarak kendisi oynatır**.
- **Elle müdahale yok:** OtoPilot açıkken ayarlar yalnız OtoPilot'tadır. Arda değiştirmek isterse başka bir moda geçer.
- **Ekranda görünür:** sürgüler ve renk çarkındaki düğme kendiliğinden hareket eder.
- **Parlaklık 0'a inebilir:** Spektrum'daki %4 taban parlaklık OtoPilot'ta yok; müzik durduğunda ya da çok sessizleştiğinde ışık tamamen kapanabilir.

**Oynattığı ayarlar ve yönü:**
| Ayar | Şarkıya göre |
|---|---|
| Hassasiyet | sessiz/akustik şarkıda yükselir, gürültülü şarkıda düşer |
| Tepki hızı | hızlı tempoda yükselir (~14-16/sn), yavaşta düşer (~6-8/sn) |
| Vuruşta renk kayması | davul yoğunsa biraz artar, baladda ~0; üst sınır düşük (Arda az renk değişimi seviyor) |
| Renk yumuşaklığı | sakin kısımda uzun ve akan, enerjik kısımda kısa |
| Temel renk | şarkının duygusuna göre yavaş kayar (hüzünlü → soğuk, coşkulu → sıcak) |
| Doygunluk / parlaklık | sakinde loş ve yumuşak, nakaratta parlak ve canlı |
| Karanlık taban (yeni) | sessiz kısımlarda ışığın inebileceği en düşük seviye, %0-%20; baladda biraz ışık kalır, drop'lu şarkıda tam karanlık |

Ayarlar ani zıplamaz, birkaç ölçüde kayar. Ölçüm penceresi son ~8-16 sn.

## Açık sorular
- Yön kuralı (yükseliş = zıt renk, düşüş = kapanma/kısılma) onaylanacak.
- Duygu önce kütüphanede mi, canlıda kaba sürüm mü denenecek.
- Kütüphane kaydı: otomatik arka plan kaydı mı, "bu şarkıyı öğren" komutuyla mı.
- Tanınan şarkıda hazır şov tamamen mi yönetsin, canlı tepkiyle karışık mı.
