"""Ritim katmani (v3, gercek muzik icin): tempo, vurus fazi ve olcu basi. Yalniz numpy, nedensel.

Girdi: Ozellik (dinleme.py). Zaman ekseni Ozellik.t ile gelir.
1. Vurus fonksiyonu (ODF): kick/snare/hat vurmali log-akislari, her biri kendi yavas
   ortalamasina (olcekten bagimsiz) ve bir tabana bolunur, agirlikli toplanir.
2. Tempo: ODF'nin ~6 sn penceresinde otokorelasyon + 1x/2x/3x/4x comb toplami + Rayleigh on
   dagilimi (120 BPM merkez); her 0,5 sn'de bir sonuc, log-tempo uzayinda Viterbi benzeri ileri
   suzgec (yakin tempo tercihli gecis) ile duzlenir. Zayif periyodiklikte gozlem sonuca
   katilmaz: tempo oldugu yerde kalir. Oktav kararini on dagilim ve dans muzigi onceligi verir.
3. Faz: BTrack tarzi kumulatif skor (alfa 0,9, siki 5); sonraki vurus skor sirtinin ileri
   izdusumunden tahmin edilir. Guven yeterliyse vurus tahminle uretilir (tahminli=True), degilse
   kick onset'inde tepki verilir.
4. Olcu basi: A surumundeki gibi, vurus konumlarindaki sub+kick enerjisinin son 8 olcudeki toplami.
"""
from dataclasses import dataclass
import numpy as np

HOP_SN = 512 / 44100
BPM_MIN = 60.0
BPM_MAX = 200.0
PENCERE_SN = 6.0         # tempo penceresi
TEMPO_SN = 0.5           # tempo guncelleme adimi
BIN_ADIM = 0.005         # log-tempo cozunurlugu (%0,5)
ODF_EMA_SN = 2.0         # ODF kanal olcegi (yavas ortalama)
ODF_TABAN = (0.3, 1.5, 3.0)       # kick, snare, hat olcek tabanlari (nat); ambiyans gurultusu sisirilmesin
ODF_AGIRLIK = (1.0, 0.5, 0.35)
COMB_AGIRLIK = (1.0, 1.0, 0.5, 0.5)
RAYLEIGH_SN = 0.5        # on dagilim tepesi (120 BPM)
AC_YANSIZ_TABAN = 0.3    # N-k duzeltmesi alt siniri (gecikme N'nin cogunu yemesin)
GECIS_SIGMA = 0.006      # log-tempo gecisi (0,5 sn adiminda ~%0,6): rampa takibine yeter
GECIS_ATLAMA = 1e-4      # tempo bininden herhangi bir bine atlama tabani
GOZLEM_US = 1.0          # gozlem keskinligi
GOZLEM_TABAN = 0.02
SKOR_ZAYIF = 0.15        # bu comb skorunun altinda gozlem etkisiz
SKOR_GUCLU = 0.50        # bu skor ve ustunde tam etki ve guven 1
KILIT_AC = 0.45          # tempo guveni bunu asinca kilit
KILIT_KAPAT = 0.20       # kilitliyken bunun altina dusunce (KILIT_BEKLE sn sure) birakir
KILIT_BEKLE = 4.0
KICK_EN_AZ = 3           # pencerede bu kadar kick onset'i yoksa (davulsuz) ritim kurulmaz
TUTMA_G = 0.75           # kilitlendikten sonra guven bunun altindayken tempo guncellenmez (oldugu yerde kalir)
KILIT_N = 4              # ust uste bu kadar guncelleme esigin ustunde ve tempo kararli olmali
KILIT_SAPMA = 0.03       # bu guncellemelerdeki tempo en cok %3 oynayabilir
UST_BPM = 190.0          # bunun ustunde on dagilim hizla baskilanir (yari tempo tercihi)
UST_USSU = 24.0
GUVEN_YUMUSAK = 0.5      # guven ustel ortalamasi (guncelleme basina)
CS_ALFA = 0.9
CS_SIKI = 5.0
OLCU_VURUS = 4
OLCU_BAR = 8
OLCU_ISINMA = 2


@dataclass
class RitimDurum:
    bpm: float               # 0 = bilinmiyor
    guven: float             # 0-1
    faz: float               # 0-1, vurus icindeki konum (0 = vurus ani)
    vurus_araligi: float     # saniye (60/bpm), bilinmiyorsa 0.5
    sonraki_vurus_t: float   # tahmini bir sonraki vurus zamani (Ozellik.t ekseninde)
    vurus: bool              # bu blokta vurus (tahmin/ onayli) oldu
    vurus_no: int
    olcu_konum: int          # 0-3 (0 = olcu basi)
    olcu_basi: bool          # bu blokta olcu basi
    olcu_no: int
    tahminli: bool           # True: PLL kilitli, vuruslar tahminle uretiliyor; False: tepki modu


class Ritim:
    def __init__(self, hop_sn=HOP_SN):
        self.hop_sn = float(hop_sn)
        if not self.hop_sn > 0.0:
            raise ValueError("hop_sn pozitif olmali")
        self._pen_n = int(round(PENCERE_SN / self.hop_sn))
        self._tempo_n = max(1, int(round(TEMPO_SN / self.hop_sn)))
        # tempo bin ekseni (log aralikli)
        self._bpms = BPM_MIN * np.exp(np.arange(0.0, np.log(BPM_MAX / BPM_MIN) + 1e-9, BIN_ADIM))
        self._lag = 60.0 / (self._bpms * self.hop_sn)        # kare cinsinden gecikme
        if 4.0 * self._lag.max() >= self._pen_n:
            # comb 4. harmonik pencereyi asiyorsa interp sifir doner; sorun degil ama uyar
            pass
        beta = RAYLEIGH_SN / self.hop_sn
        l = self._lag
        self._prior = (l / beta) * np.exp(0.5 - l * l / (2.0 * beta * beta))
        self._prior = self._prior / (1.0 + (self._bpms / UST_BPM) ** UST_USSU)
        lb = np.log(self._bpms)
        gecis = np.exp(-0.5 * ((lb[:, None] - lb[None, :]) / GECIS_SIGMA) ** 2)
        gecis /= gecis.sum(axis=0, keepdims=True)
        nb = self._bpms.size
        self._gecis = gecis * (1.0 - GECIS_ATLAMA) + GECIS_ATLAMA / nb
        # kumulatif skor halkasi
        self._cs_n = 2 * int(np.ceil(60.0 / (BPM_MIN * self.hop_sn))) + 16
        self._ema_k = np.array(ODF_TABAN) * 0.0
        self._ema_a = 1.0 - np.exp(-self.hop_sn / ODF_EMA_SN)
        self.sifirla()

    # ------------------------------------------------------------------ durum
    def sifirla(self):
        self._n = 0
        self._odf = np.zeros(self._pen_n)         # halka
        self._ema = np.zeros(3)
        self._olcek_hazir = False
        self._p = np.ones(self._bpms.size) / self._bpms.size
        self._bpm = 0.0
        self._skor = 0.0           # son comb skoru (on dagilimsiz tepe)
        self._guven = 0.0
        self._kilit = False
        self._kilitlendi = False   # en az bir kez kilitlendi: bpm cikisi ancak bundan sonra
        self._ust_say = 0
        self._gecmis_bpm = []
        self._alt_t = None
        self._cs =np.zeros(self._cs_n)
        self._P = 0.5 / self.hop_sn
        self._sonraki_n = None      # sonraki tahmini vurus (kare indeksi, kesirli)
        self._son_t = None          # son vurusun zamani
        self._son_kick_t = None
        self._kick_t = []
        self._aralik = 0.5
        self._guc_t = np.full(32, -np.inf)
        self._guc_p = np.zeros(32)
        self._guc_i = 0
        self._bekleyen_enerji = []
        self._vurus_no = 0
        self._hafiza_e = np.zeros(OLCU_BAR * OLCU_VURUS)
        self._hafiza_p = np.zeros(OLCU_BAR * OLCU_VURUS, dtype=int)
        self._hafiza_i = 0
        self._hafiza_sayi = 0
        self._olcu_konum = 0
        self._olcu_no = 0

    # -------------------------------------------------------------------- ODF
    def _odf_hesapla(self, oz):
        if oz.sessiz:
            return 0.0
        v = np.array([max(0.0, float(oz.kick)), max(0.0, float(oz.snare)), max(0.0, float(oz.hat))])
        if not self._olcek_hazir:
            self._ema = v.copy()
            self._olcek_hazir = True
        else:
            self._ema += (v - self._ema) * self._ema_a
        olcek = np.maximum(self._ema, ODF_TABAN)
        return float(np.dot(ODF_AGIRLIK, v / olcek))

    # ------------------------------------------------------------------ tempo
    def _comb(self):
        """Pencere ODF'sinden comb skoru (bin basina, on dagilimsiz) ya da None."""
        e = np.roll(self._odf, -(self._n % self._pen_n))
        e = e - e.mean()
        n = e.size
        v = float(np.dot(e, e))
        if v / n < 1e-6:
            return None
        F = np.fft.rfft(e, 2 * n)
        ac = np.fft.irfft(F.real * F.real + F.imag * F.imag, 2 * n)[:n]
        ac = ac / np.maximum(n - np.arange(n), AC_YANSIZ_TABAN * n)
        ac = ac / ac[0]
        k = np.arange(n)
        s = np.zeros(self._lag.size)
        for h, w in enumerate(COMB_AGIRLIK, start=1):
            s += w * np.interp(h * self._lag, k, ac, right=0.0)
        return np.maximum(s, 0.0)

    def _tempo_guncelle(self, t):
        s = self._comb() if len(self._kick_t) >= KICK_EN_AZ else None
        if s is None:
            self._skor = 0.0
            g = 0.0
        else:
            self._skor = float(s.max())
            g = min(1.0, max(0.0, (self._skor - SKOR_ZAYIF) / (SKOR_GUCLU - SKOR_ZAYIF)))
            sp = s * self._prior
            if g < TUTMA_G and self._kilitlendi:
                sp = None
        if s is not None and sp is not None:
            obs = (sp / (sp.max() + 1e-12) + GOZLEM_TABAN) ** (GOZLEM_US * g)
            p = obs * (self._gecis @ self._p)
            top = float(p.sum())
            if top > 0.0 and np.isfinite(top):
                self._p = p / top
            else:
                self._p = np.ones(self._p.size) / self._p.size
            ib = int(np.argmax(self._p))
            a, b = max(0, ib - 3), min(self._p.size, ib + 4)
            ww = self._p[a:b]
            lg = np.log(self._bpms[a:b])
            bpm = float(np.exp(np.dot(ww, lg) / ww.sum()))
            if g > 0.0 or self._bpm > 0.0:
                self._bpm = bpm
        # guven: skor tabanli, yumusatilmis
        self._guven += GUVEN_YUMUSAK * (g - self._guven)
        # kilit mantigi
        if not self._kilit:
            if self._guven >= KILIT_AC and self._bpm > 0.0:
                self._ust_say += 1
                self._gecmis_bpm = (self._gecmis_bpm + [self._bpm])[-KILIT_N:]
                gb = self._gecmis_bpm
                if self._ust_say >= KILIT_N and max(gb) <= min(gb) * (1.0 + KILIT_SAPMA):
                    self._kilit = True
                    self._kilitlendi = True
                    self._alt_t = None
                    self._sonraki_n = None
            else:
                self._ust_say = 0
                self._gecmis_bpm = []
        else:
            if self._guven < KILIT_KAPAT:
                if self._alt_t is None:
                    self._alt_t = t
                elif t - self._alt_t >= KILIT_BEKLE:
                    self._kilit = False
                    self._ust_say = 0
                    self._sonraki_n = None
            else:
                self._alt_t = None

    # ---------------------------------------------------------- kumulatif skor
    def _pencere_v(self, P):
        vmin = max(1, int(round(0.5 * P)))
        vmax = int(round(2.0 * P))
        v = np.arange(vmin, vmax + 1)
        w = np.exp(-0.5 * (CS_SIKI * np.log(v / P)) ** 2)
        return v, w

    def _cs_guncelle(self, odf):
        P = self._P
        v, w = self._pencere_v(P)
        idx = (self._n - v) % self._cs_n
        m = float(np.max(self._cs[idx] * w))
        self._cs[self._n % self._cs_n] = (1.0 - CS_ALFA) * odf + CS_ALFA * m

    def _projekte(self, kmax):
        """Su anki kareden ileriye kmax kare: odf=0, alfa=1 kumulatif skor izdusumu (fut[1..kmax])."""
        P = self._P
        v, w = self._pencere_v(P)
        gecmis = int(v[-1]) + 1
        idx = (self._n - np.arange(gecmis - 1, -1, -1)) % self._cs_n
        ext = np.concatenate([self._cs[idx], np.zeros(kmax)])
        now = gecmis - 1
        for k in range(1, kmax + 1):
            ext[now + k] = float(np.max(ext[now + k - v] * w))
        return ext[now + 1:]

    def _sonraki_tahmin(self, ilk=False):
        """Sonraki vurus kare indeksi. ilk=False: vurus az once oldu, bir periyot sonrasi civarinda sirt aranir.
        ilk=True: kilitlenirken en yakin sirt (1..P)."""
        P = self._P
        kmax = int(np.ceil(1.5 * P)) if not ilk else int(np.ceil(P))
        fut = self._projekte(kmax)
        k = np.arange(1, kmax + 1)
        if ilk:
            g = np.ones(kmax)
        else:
            g = np.exp(-0.5 * ((k - P) / (0.5 * P)) ** 2)
        sc = fut * g
        if float(sc.max()) <= 0.0:
            return self._n + P
        return self._n + float(k[int(np.argmax(sc))])

    # ------------------------------------------------------------------ olcu
    def _olcu_baslangic(self):
        if self._hafiza_sayi < OLCU_ISINMA * OLCU_VURUS:
            return 0
        toplam = np.bincount(self._hafiza_p, weights=self._hafiza_e, minlength=OLCU_VURUS)
        return int(np.argmax(toplam))

    def _enerji_oku(self, t):
        kalan = []
        for i, tb in self._bekleyen_enerji:
            if t < tb + 0.12:
                kalan.append((i, tb))
                continue
            pen = (self._guc_t >= tb - 0.03) & (self._guc_t <= tb + 0.12)
            self._hafiza_e[i] = float(self._guc_p[pen].max()) if pen.any() else 0.0
        self._bekleyen_enerji = kalan

    def _olay(self, tb):
        self._vurus_no += 1
        p = self._vurus_no % OLCU_VURUS
        i = self._hafiza_i
        self._hafiza_e[i] = 0.0
        self._bekleyen_enerji.append((i, tb))
        self._hafiza_p[i] = p
        self._hafiza_i = (i + 1) % self._hafiza_e.size
        self._hafiza_sayi = min(self._hafiza_sayi + 1, self._hafiza_e.size)
        self._son_t = tb
        k = self._olcu_baslangic()
        self._olcu_konum = (p - k) % OLCU_VURUS
        basi = self._olcu_konum == 0
        if basi:
            self._olcu_no += 1
        return basi

    # ----------------------------------------------------------------- ana adim
    def guncelle(self, oz) -> RitimDurum:
        t = float(oz.t)
        self._n += 1
        n = self._n
        odf = self._odf_hesapla(oz)
        self._odf[n % self._pen_n] = odf
        if oz.kick_var:
            self._son_kick_t = t
            self._kick_t.append(t)
        while self._kick_t and self._kick_t[0] < t - PENCERE_SN:
            self._kick_t.pop(0)
        self._guc_t[self._guc_i] = t
        self._guc_p[self._guc_i] = float(10.0 ** (oz.bant_db[0] / 10.0) + 10.0 ** (oz.bant_db[1] / 10.0))
        self._guc_i = (self._guc_i + 1) % self._guc_p.size
        self._enerji_oku(t)

        if self._bpm > 0.0:
            self._P = 60.0 / (self._bpm * self.hop_sn)
            if self._kilitlendi:
                self._aralik = 60.0 / self._bpm
        self._cs_guncelle(odf)

        erken = (not self._kilitlendi) and bool(oz.kick_var) and len(self._kick_t) == KICK_EN_AZ   # ritim yeni basliyor: beklemeden bak
        if (n % self._tempo_n == 0 or erken) and n >= self._pen_n:
            self._tempo_guncelle(t)

        vurus = False
        basi = False
        if self._kilit and self._bpm > 0.0:
            if self._sonraki_n is None:
                self._sonraki_n = self._sonraki_tahmin(ilk=True)
            if n >= self._sonraki_n:
                tb = t - (n - self._sonraki_n) * self.hop_sn
                vurus = True
                basi = self._olay(tb)
                self._sonraki_n = self._sonraki_tahmin()
        else:
            if oz.kick_var:
                vurus = True
                basi = self._olay(t)

        if self._kilit and self._sonraki_n is not None:
            sonraki = t + (self._sonraki_n - n) * self.hop_sn
            if sonraki <= t:
                sonraki = t + self._aralik
        else:
            sonraki = t + self._aralik
        if self._son_t is None:
            faz = 0.0
        else:
            faz = min(1.0, max(0.0, (t - self._son_t) / self._aralik))
        return RitimDurum(
            bpm=float(self._bpm if self._kilitlendi else 0.0),
            guven=float(min(1.0, max(0.0, self._guven))),
            faz=float(faz),
            vurus_araligi=float(self._aralik),
            sonraki_vurus_t=float(sonraki),
            vurus=bool(vurus),
            vurus_no=int(self._vurus_no),
            olcu_konum=int(self._olcu_konum),
            olcu_basi=bool(basi),
            olcu_no=int(self._olcu_no),
            tahminli=bool(self._kilit),
        )
