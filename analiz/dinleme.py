"""Dinleme katmani: her blokta (HOP ornek, ~11,6 ms) ses ozelliklerini cikarir. Yalniz numpy.

Olcek: P = olcek * |X|^2, olcek = 2 / (NFFT * sum(hann^2)). Tek tarafli P toplami blok
karesel ortalamasina (sigma^2) yaklasir; bant_db ve gur_db bu olcekte.
Vurus fonksiyonu: vurmali (perkusif) spektrumun log-buyukluk spektral akisi.
Enerji: music.py'deki dB tepe/dip mantigi (ARALIK_SN, ILK_PAY_DB, EGRI ayni).
"""
from dataclasses import dataclass
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

RATE = 44100
HOP = 512
NFFT = 2048

BANT_SINIRLARI = (
    (20.0, 60.0),       # sub
    (60.0, 150.0),      # kick
    (150.0, 500.0),     # altorta
    (500.0, 2000.0),    # orta
    (2000.0, 6000.0),   # parlak
    (6000.0, 16000.0),  # hava
)
KICK_ARALIK = (40.0, 150.0)
SNARE_ARALIK = (200.0, 4000.0)
HAT_ARALIK = (6000.0, 16000.0)
CHROMA_ARALIK = (100.0, 4000.0)
DUZLUK_ARALIK = (40.0, 16000.0)
MERKEZ_ARALIK = (20.0, 16000.0)

HPSS_GENIS = 17          # zaman (kare) ve frekans (bin) medyan penceresi, tek sayi
ESIK_SN = 1.5            # uyarlanir esik penceresi
MAD_TABAN = 1e-3
BEKLEME_SN = {"kick": 0.100, "snare": 0.080, "hat": 0.060, "trans": 0.060}
# Mutlak taban (log-akis toplami, nat). Kick tabani 2,5: sabit bas hattinin not
# degisimleri (~2,2'ye kadar) kick sayilmasin diye.
MUTLAK_TABAN = {"kick": 0.3, "snare": 0.5, "hat": 0.5, "trans": 3.8}
TRANS_ARALIK = (2000.0, 8000.0)   # kick tiki (beater tiklamasi): ham genlik akisi
KICK_TIK_SN = 0.035               # kick, tik akisi bu kadar sn icinde olmali (tik yoksa guclu kick sarti)
KICK_GUCLU = 2.5                  # tiksiz kick icin minimum akis (bas notasi degisimleri < 2,2)
SNARE_DUZLUK_TABAN = 0.3  # snare agirligi = 0,3 + 0,7 * duzluk
LOG_TABAN = 1e-2          # log-buyukluk alt siniri (sessiz binlerde akis patlamasin)
SESSIZ_RMS = 1e-4
GUR_KISA_SN = 0.15
ARALIK_SN = 120.0
ILK_PAY_DB = 15.0
ARALIK_EN_AZ_DB = 14.0
EGRI = 1.6
CHROMA_SN = 0.5
K_EN_AZ = 0.8
K_ILK = 3.0
SPEK_BANT = 32            # laboratuvar ekolayziri: log aralikli bant sayisi
SPEK_ARALIK = (20.0, 16000.0)


def spektrum_agirliklari(frek, nbant=SPEK_BANT, aralik=SPEK_ARALIK):
    """(nbant, len(frek)) ortalama agirlik matrisi ve bant merkez frekanslari (Hz). Bir bantta hic bin yoksa
    (dusuk frekanslar) merkez frekanstaki iki komsu bin dogrusal aradeger alinir."""
    kenar = np.geomspace(aralik[0], aralik[1], nbant + 1)
    merkez = np.sqrt(kenar[:-1] * kenar[1:])
    w = np.zeros((nbant, frek.size))
    for i in range(nbant):
        idx = np.nonzero((frek >= kenar[i]) & (frek < kenar[i + 1]))[0]
        if idx.size:
            w[i, idx] = 1.0 / idx.size
        else:
            j = int(np.searchsorted(frek, merkez[i]))
            j = min(max(j, 1), frek.size - 1)
            a = (merkez[i] - frek[j - 1]) / (frek[j] - frek[j - 1])
            w[i, j - 1], w[i, j] = 1.0 - a, a
    return w, merkez


@dataclass
class Ozellik:
    t: float                 # saniye, ilk bloktan beri (blok sonu)
    bant_db: np.ndarray      # (6,) sub 20-60, kick 60-150, altorta 150-500, orta 500-2k, parlak 2k-6k, hava 6k-16k
    kick: float              # vurus gucu (onset strength, >=0), vurmali spektrumdan
    snare: float
    hat: float
    kick_var: bool           # bu blokta kick onset'i (uyarlanir esik + bekleme)
    snare_var: bool
    hat_var: bool
    vurmali: float           # vurmali enerji (lineer)
    melodik: float           # melodik enerji (lineer)
    gur_db: float            # kisa sureli (~150 ms) gurluk dB
    enerji: float            # 0-1, sarkinin dB tepe/dip araligina gore normalize (music.py mantigi)
    merkez_hz: float         # spektral merkez
    duzluk: float            # spektral duzluk 0-1
    chroma: np.ndarray       # (12,) toplami 1, C=0 ... B=11, ~0,5 sn yumusatilmis
    sessiz: bool             # rms cok dusuk


def _idx(frek, aralik):
    lo, hi = aralik
    return np.nonzero((frek >= lo) & (frek < hi))[0]


def _medyan(a, eksen):
    """Tek uzunlukta eksende medyan (np.partition ile; np.median'dan yaklasik 3-4 kat hizli)."""
    n = a.shape[eksen]
    k = n // 2
    if n % 2:
        return np.take(np.partition(a, k, axis=eksen), k, axis=eksen)
    p = np.partition(a, [k - 1, k], axis=eksen)
    return 0.5 * (np.take(p, k - 1, axis=eksen) + np.take(p, k, axis=eksen))


def _duzluk(p):
    """Geometrik / aritmetik ortalama orani (0-1). Hic guc yoksa 0."""
    if float(np.sum(p)) < 1e-12:
        return 0.0
    p = p + 1e-12
    oran = np.exp(np.mean(np.log(p))) / np.mean(p)
    return float(min(1.0, max(0.0, oran)))


class _Esik:
    """Uyarlanir esik: son ESIK_SN saniyenin medyani + k * MAD, mutlak taban ve bekleme."""

    def __init__(self, n, bekleme, taban):
        self._gecmis = np.zeros(n)
        self._i = 0
        self._bekleme = bekleme
        self._taban = taban
        self._son = -np.inf

    def karar(self, s, t, k, kosul=True):
        med = float(_medyan(self._gecmis, 0))
        mad = float(_medyan(np.abs(self._gecmis - med), 0))
        esik = med + k * max(mad, MAD_TABAN)
        var = s > esik and s > self._taban and (t - self._son) >= self._bekleme and kosul
        self._gecmis[self._i] = s
        self._i = (self._i + 1) % self._gecmis.size
        if var:
            self._son = t
        return bool(var)

    def sifirla(self):
        self._gecmis[:] = 0.0
        self._i = 0
        self._son = -np.inf


class Dinleyici:
    def __init__(self, rate=RATE, hop=HOP, nfft=NFFT, hassasiyet=50.0):
        self.rate = int(rate)
        self.hop = int(hop)
        self.nfft = int(nfft)
        if self.rate <= 0 or self.hop <= 0 or self.nfft < self.hop:
            raise ValueError("rate, hop, nfft pozitif ve nfft >= hop olmali")
        self.hop_sn = self.hop / self.rate

        self._pencere = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(self.nfft) / self.nfft)
        w2 = float(np.sum(self._pencere ** 2))
        self._olcek = 2.0 / (self.nfft * w2)
        f = np.fft.rfftfreq(self.nfft, 1.0 / self.rate)
        self._frek = f
        self._nb = f.size
        self._bant_idx = [_idx(f, b) for b in BANT_SINIRLARI]
        self._kick_idx = _idx(f, KICK_ARALIK)
        self._snare_idx = _idx(f, SNARE_ARALIK)
        self._hat_idx = _idx(f, HAT_ARALIK)
        self._chroma_idx = _idx(f, CHROMA_ARALIK)
        self._trans_idx = _idx(f, TRANS_ARALIK)
        self._duzluk_idx = _idx(f, DUZLUK_ARALIK)
        self._merkez_idx = _idx(f, MERKEZ_ARALIK)
        # chroma: frekans -> nota sinifi (A4 = 440 Hz = 9, C = 0)
        ci = self._chroma_idx
        self._chroma_pc = np.round(12.0 * np.log2(f[ci] / 440.0) + 69.0).astype(int) % 12
        # yaklasik K-agirlik (guc): 38 Hz yuksek geciren, 1-2 kHz arasi +4 dB raf
        fs = np.maximum(f, 1e-9)
        self._kw = (fs * fs / (fs * fs + 38.1 ** 2)) * 10.0 ** (0.4 * np.clip((f - 1000.0) / 1000.0, 0.0, 1.0))
        self._kw[0] = 0.0
        self._n_esik = int(round(ESIK_SN / self.hop_sn))
        self._spek_w, self.bant_hz = spektrum_agirliklari(f)
        self._pad = HPSS_GENIS // 2
        self.ayarla(hassasiyet)
        self.sifirla()

    def ayarla(self, hassasiyet: float):
        """Hassasiyet 1-100. k = 3,0 - 2,2 * (hassasiyet / 100): yuksek hassasiyet = dusuk esik."""
        h = max(1.0, min(100.0, float(hassasiyet)))
        self.hassasiyet = h
        self._k = max(K_EN_AZ, K_ILK - 2.2 * (h / 100.0))

    def sifirla(self):
        self._n = 0
        self._buf = np.zeros(self.nfft)
        self._zaman = np.zeros((HPSS_GENIS, self._nb))
        self._zi = 0
        self._onceki_l = np.full(self._nb, np.log(LOG_TABAN))
        self._onceki_t = np.full(self._nb, np.log(LOG_TABAN))
        self._son_tr = -np.inf
        self._esik = {
            "kick": _Esik(self._n_esik, BEKLEME_SN["kick"], MUTLAK_TABAN["kick"]),
            "snare": _Esik(self._n_esik, BEKLEME_SN["snare"], MUTLAK_TABAN["snare"]),
            "hat": _Esik(self._n_esik, BEKLEME_SN["hat"], MUTLAK_TABAN["hat"]),
            "trans": _Esik(self._n_esik, BEKLEME_SN["trans"], MUTLAK_TABAN["trans"]),
        }
        self._gur_ms = 0.0
        self._gur_ilk = True
        self._tepe = None
        self._dip = None
        self._chroma = np.full(12, 1.0 / 12.0)
        self.son_mel = None
        self.son_tum = None
        self.son_spek = None    # (32,) lineer bant guc ortalamasi; dB'ye cevirme tuketicide

    def _giris(self, blok):
        x = np.asarray(blok)
        if x.dtype.kind not in "iuf":
            raise ValueError("blok sayisal (tamsayi ya da gercek) olmali")
        if x.ndim == 2 and x.shape[0] == self.hop:
            x = x.mean(axis=1)
        if x.ndim != 1 or x.size != self.hop:
            raise ValueError(f"blok {self.hop} ornekli 1 boyutlu olmali, gelen sekil {x.shape}")
        x = x.astype(np.float64)
        return np.where(np.isfinite(x), x, 0.0)

    def _enerji(self, db):
        """music.py: tepe/dip ARALIK_SN'de yavasca unutulur; ilk blokta tepe = db + ILK_PAY_DB."""
        if self._tepe is None:
            self._tepe = db + ILK_PAY_DB
            self._dip = db - 6.0
        u = self.hop_sn / ARALIK_SN
        if db > self._tepe:
            self._tepe = db
        else:
            self._tepe += (db - self._tepe) * u
        if db < self._dip:
            self._dip = db
        else:
            self._dip += (db - self._dip) * u
        aralik = max(self._tepe - self._dip, ARALIK_EN_AZ_DB)
        norm = min(1.0, max(0.0, (db - (self._tepe - aralik)) / aralik))
        return float(norm ** EGRI)

    def isle(self, blok: np.ndarray) -> Ozellik:
        x = self._giris(blok)
        self._n += 1
        t = self._n * self.hop_sn
        h = self.hop
        self._buf[:-h] = self._buf[h:]
        self._buf[-h:] = x

        rms = float(np.sqrt(np.mean(x * x)))
        sessiz = rms < SESSIZ_RMS

        X = np.fft.rfft(self._buf * self._pencere)
        P = self._olcek * (X.real * X.real + X.imag * X.imag)
        mag = np.sqrt(P)

        # HPSS-lite: zaman yonu medyani = melodik, frekans yonu medyani = vurmali (Wiener, p=2)
        self._zaman[self._zi] = mag
        self._zi = (self._zi + 1) % HPSS_GENIS
        med_t = _medyan(self._zaman, 0)
        yol = np.pad(mag, self._pad, mode="edge")
        med_f = _medyan(sliding_window_view(yol, HPSS_GENIS), 1)
        ht = med_t * med_t
        hf = med_f * med_f
        mh = ht / (ht + hf + 1e-20)
        mp = 1.0 - mh
        p_mel = mh * mh * P
        p_perc = mp * mp * P
        self.son_mel = p_mel   # vokal.py kancasi: bu blokun melodik guc spektrumu
        self.son_tum = P       # ... ve tum guc spektrumu
        self.son_spek = self._spek_w @ P   # laboratuvar ekolayzir bantlari (ek FFT yok)

        # vurus fonksiyonlari: vurmali log-buyukluk spektral akisi (yalniz artislar)
        lp = np.log(mp * mag + LOG_TABAN)
        akis = np.maximum(lp - self._onceki_l, 0.0)
        self._onceki_l = lp
        lt = np.log(mag + LOG_TABAN)
        trans_s = float(np.maximum(lt - self._onceki_t, 0.0)[self._trans_idx].sum())
        self._onceki_t = lt
        kick_s = float(akis[self._kick_idx].sum())
        snare_s = float(akis[self._snare_idx].sum()) * (
            SNARE_DUZLUK_TABAN + (1.0 - SNARE_DUZLUK_TABAN) * _duzluk(p_perc[self._snare_idx]))
        hat_s = float(akis[self._hat_idx].sum())
        if sessiz:
            kick_s = snare_s = hat_s = trans_s = 0.0
        tik_var = self._esik["trans"].karar(trans_s, t, self._k)
        if tik_var:
            self._son_tr = t
        kick_var = self._esik["kick"].karar(kick_s, t, self._k, kosul=((t - self._son_tr) <= KICK_TIK_SN or kick_s >= KICK_GUCLU))
        snare_var = self._esik["snare"].karar(snare_s, t, self._k)
        hat_var = self._esik["hat"].karar(hat_s, t, self._k)

        bant = np.empty(len(BANT_SINIRLARI))
        for i, idx in enumerate(self._bant_idx):
            bant[i] = 10.0 * np.log10(float(P[idx].sum()) + 1e-12)

        ms_w = float(np.dot(self._kw, P))
        if self._gur_ilk:
            self._gur_ms = ms_w
            self._gur_ilk = False
        else:
            a = 1.0 - np.exp(-self.hop_sn / GUR_KISA_SN)
            self._gur_ms += (ms_w - self._gur_ms) * a
        gur_db = 10.0 * np.log10(self._gur_ms + 1e-12)
        enerji = 0.0 if sessiz else self._enerji(gur_db)

        mi = self._merkez_idx
        toplam_m = float(mag[mi].sum())
        merkez = float(np.sum(self._frek[mi] * mag[mi]) / toplam_m) if toplam_m > 1e-12 else 0.0
        duzluk = _duzluk(P[self._duzluk_idx])

        cr = np.bincount(self._chroma_pc, weights=p_mel[self._chroma_idx], minlength=12)
        cs = float(cr.sum())
        if cs > 1e-12:
            alfa = 1.0 - np.exp(-self.hop_sn / CHROMA_SN)
            self._chroma += (cr / cs - self._chroma) * alfa
        chroma = self._chroma.copy()

        return Ozellik(
            t=t,
            bant_db=bant,
            kick=kick_s,
            snare=snare_s,
            hat=hat_s,
            kick_var=kick_var,
            snare_var=snare_var,
            hat_var=hat_var,
            vurmali=float(p_perc.sum()),
            melodik=float(p_mel.sum()),
            gur_db=float(gur_db),
            enerji=float(enerji),
            merkez_hz=merkez,
            duzluk=duzluk,
            chroma=chroma,
            sessiz=bool(sessiz),
        )
