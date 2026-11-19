"""Vokal katmani: canli (nedensel) vokal algilama. Yalniz numpy.

Girdi: Ozellik (oz), Dinleyici.son_mel (HPSS melodik guc spektrumu) ve Dinleyici.son_tum (tum guc spektrumu).
Ozellikler (OZELLIK_AD, tek zaman sabiti 50 ms): tum spektrum MFCC (c0 = seviye), sarkinin tepesine gore dB,
f0 izi (80-1000 Hz harmonik toplam: belirginlik, sureklilik, vibrato 4-8 Hz, perde kaymasi), 250-4000 Hz bant
oranlari, tonallik, melodik oran, orta bant zarf modulasyonu. 3 uyeli tanh-MLP topluluk (_vokal_agirlik.py,
testler/vokal_egit.py ile 5 vokalli sarkida egitildi) olasilik verir; histerezisli bayrak (ESIK_AC/KAPA).
Stereo kancasi: stereo_merkez(blok_LR) merkez agirligini verir (orta/yan + kanal tutarliligi);
guncelle(..., merkez=) olasiligi bununla carpar. Laboratuvar kayitlari mono, bu yol birim testle sinanir.
"""
import math
from collections import deque
from dataclasses import dataclass

import numpy as np

RATE = 44100
HOP = 512
NFFT = 2048

F0_ARALIK = (80.0, 1000.0)
F0_ADIM_YARIMTON = 0.25
HARMONIK_SAYI = 6
BANTLAR = ((80.0, 250.0), (250.0, 500.0), (500.0, 1000.0), (1000.0, 2000.0),
           (2000.0, 4000.0), (4000.0, 8000.0))
TOPLAM_ARALIK = (50.0, 8000.0)
VOKAL_ARALIK = (250.0, 4000.0)
HARMONIK_TOPLAM_ARALIK = (80.0, 4000.0)

TAU_HIZLI = 0.05
TAU_YAVAS = 0.40
SES_BELIRGIN_ESIK = 0.10      # harmonik oran: perde sesli sayilir (gercek kayitlarda medyan ~0,1; saf sinus 0,13)
PERDE_ATLAMA = 2.0            # yarimton: bir blokta bundan fazla atlama sureksizlik
VIBRATO_SN = 0.5
VIBRATO_FREKANS = (4.0, 5.0, 6.0, 7.0, 8.0)
TUTULAN_SN = 0.4
TUTULAN_GENISLIK = 1.5        # yarimton: pencere icinde max-min
ZARF_TAU = 0.3

# Siniflandirici (vokal_egit.py cikti). Yok ise ham varsayilan.
ESIK_AC = 0.9
ESIK_KAPA = 0.7
TUT_KAPA_SN = 0.15            # olasilik KAPA altinda bu kadar kalirsa bayrak iner
SESSIZ_RMS_GUC_DB = -70.0

_IZ_AD = ("harm", "kontrast", "sureklilik", "vibrato", "glide", "perde") + tuple("prof%d" % k for k in range(2, HARMONIK_SAYI + 1))
N_IZ = 5   # modele giren izci ozellikleri: harm, kontrast, sureklilik, vibrato, glide
OZELLIK_AD = (
    tuple("mfcc%d" % i for i in range(0, 13))          # tum spektrum MFCC (c0 = seviye)
    + ("goreli_db", "goreli_orta_db")                   # sarkinin tepesine gore tum / 250-4000 Hz dB
    + tuple("T_" + a for a in _IZ_AD[:N_IZ])            # perde izi (tum spektrum)
    + ("r_b0", "r_b1", "r_b2", "r_b3", "r_b4", "r_b5", "r_orta", "tonallik", "mel_oran", "seviye", "zarf_mod")
)
MEL_FILTRE = 16
MEL_ARALIK = (100.0, 8000.0)
N_MFCC = 12
N_OZ = len(OZELLIK_AD)
TEPE_TAU = 20.0
# Vokal gucu hassasiyeti (sef, 2026-11-18; Arda: "vokalin ses gucunun hassasligini artir")
GUC_ARALIK_SN = 15.0        # tepe/dip bu surede unutulur (40 -> 15: son cumleye gore olcekler)
GUC_ARALIK_EN_AZ_DB = 5.0    # 8 -> 5 dB: kucuk ses degisimleri de tam araliga yayilir
GUC_EGRI = 1.4              # >1: yuksek kisimlar belirginlesir, kontrast artar
GUC_ATAK_SN = 0.02          # 0,1 -> 0,02
GUC_SONUM_SN = 0.04         # 0,3 -> 0,04 (0,12'de dB korelasyonu 0,55; 0,04'te 0,63; isik motoru ayrica yumusatiyor)
GUC_SIGMOID_K = 1.6         # z-skoru -> 0-1 egimi

try:
    from . import _vokal_agirlik as _AG
    MODEL = {k: np.asarray(getattr(_AG, k)) for k in ("ORT", "STD", "W1", "B1", "W2", "B2")}
except Exception:  # egitim oncesi: notr model
    MODEL = {"ORT": np.zeros(N_OZ), "STD": np.ones(N_OZ), "W1": np.zeros((1, N_OZ, 1)),
             "B1": np.zeros((1, 1)), "W2": np.zeros((1, 1)), "B2": np.zeros(1)}


try:
    from . import _vokal_guc as _GM
    GUC_MODEL = {k: np.asarray(getattr(_GM, k)) for k in ("ORT", "STD", "W", "B")}
except Exception:  # model yoksa: orta bant dB'sine dus (eski davranis)
    GUC_MODEL = None
N_EK = 2   # guc regresyonuna giren ek ozellikler: harmonik tarak dB ve orani


class GucIzci:
    """Vokal dB tahminini sarkinin son cumlelerine gore 0-1'e olcekler: ortalama/std izleyici (z-skoru) + sigmoid.
    Sert kirpma yok (tepe/dip kirpmasi dB ile korelasyonu 0,72 -> 0,5'e dusuruyordu); atak/sonum ile yumusatilir."""

    def __init__(self, hop_sn):
        self.hop_sn = hop_sn
        self.sifirla()

    def sifirla(self):
        self.ort = None
        self.var2 = 0.0
        self.guc = 0.0

    def adim(self, db, var, pr, aktif=True):
        if aktif:
            sm = GUC_ARALIK_EN_AZ_DB / 4.0
            if self.ort is None:
                self.ort, self.var2 = db, sm * sm
            u = self.hop_sn / GUC_ARALIK_SN
            d = db - self.ort
            self.ort += d * u
            self.var2 += (d * d - self.var2) * u
            z = (db - self.ort) / max(math.sqrt(self.var2), sm)
            hedef = (1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, GUC_SIGMOID_K * z))))) ** GUC_EGRI * (pr ** 0.3)
        else:
            hedef = 0.0
        a = 1.0 - math.exp(-self.hop_sn / (GUC_ATAK_SN if hedef > self.guc else GUC_SONUM_SN))
        self.guc += a * (hedef - self.guc)
        return self.guc


def model_olasilik(m, z):
    """Topluluk MLP: z (N_OZ,) -> 0-1. W1 (K,D,H), B1 (K,H), W2 (K,H), B2 (K,)."""
    a = (z - m["ORT"]) / m["STD"]
    h = np.tanh(np.einsum("d,kdh->kh", a, m["W1"]) + m["B1"])
    lg = np.einsum("kh,kh->k", h, m["W2"]) + m["B2"]
    return float(np.mean(1.0 / (1.0 + np.exp(-np.clip(lg, -30.0, 30.0)))))


@dataclass
class VokalDurum:
    vokal: float = 0.0          # 0-1 olasilik
    vokal_var: bool = False     # histerezisli bayrak
    vokal_guc: float = 0.0      # 0-1, sarkinin vokal dinamigine normalize
    vokal_giris: bool = False   # bu blokta vokal basladi
    vokal_cikis: bool = False   # bu blokta vokal bitti
    perde_hz: float = 0.0       # yoksa 0
    tutulan: bool = False       # >= 0,4 sn ayni perdede nota
    vokal_yok_sn: float = 0.0   # vokal_var False iken susma suresi
    merkez: float = 1.0         # stereo merkez agirligi (mono: 1)


def _sonlu(x, v=0.0):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return v
    return x if math.isfinite(x) else v


class _Izci:
    """Bir guc spektrumunda f0 izi: harmonik belirginlik, sureklilik, vibrato, perde kaymasi, harmonik profil."""

    def __init__(self, v):
        self.v = v
        self.sifirla()

    def sifirla(self):
        v = self.v
        self.midi = deque(maxlen=max(v._nv, v._ntut))
        self.ses = deque(maxlen=max(v._nv, v._ntut))
        self.onceki = None
        self.sureklilik = 0.0
        self.glide = 0.0
        self.son_midi = 0.0
        self.son_sesli = False
        self.comb_db = -120.0
        self.comb_oran = 0.0

    def adim(self, p, orta):
        """-> ozellik dizisi (len(_IZ_AD))."""
        v = self.v
        i0, fr = v._i0, v._fr
        pw = p[i0] * (1.0 - fr) + p[i0 + 1] * fr
        s = (pw * v._w).sum(axis=1)
        j = int(np.argmax(s))
        smax = float(s[j])
        ta, tb = v._harm_top
        top = float(p[ta:tb].sum()) + 1e-20
        harm = min(1.0, smax / (top * v._wsum * 0.5 + 1e-20) * 0.5)
        kontrast = math.log1p(max(0.0, smax / (float(s.mean()) + 1e-20) - 1.0))
        mid = float(v._midi[j])
        if 0 < j < s.size - 1:
            y0, y1, y2 = s[j - 1], s[j], s[j + 1]
            d = y0 - 2.0 * y1 + y2
            if d < 0:
                mid += F0_ADIM_YARIMTON * float(np.clip(0.5 * (y0 - y2) / d, -0.5, 0.5))
        prof = pw[j, 1:] / (float(pw[j].sum()) + 1e-20)
        comb = float(pw[j].sum())
        self.comb_db = 10.0 * math.log10(comb + 1e-12)
        self.comb_oran = comb / top
        sesli = harm >= SES_BELIRGIN_ESIK and orta > 1e-12
        ah = v._a_h
        if sesli and self.onceki is not None and self.ses and self.ses[-1]:
            d = abs(mid - self.onceki)
            self.sureklilik += ah * ((1.0 if d < PERDE_ATLAMA else 0.0) - self.sureklilik)
            if d < PERDE_ATLAMA:
                self.glide += ah * (d - self.glide)
        else:
            self.sureklilik += ah * (0.0 - self.sureklilik)
        self.midi.append(mid)
        self.ses.append(bool(sesli))
        self.onceki = mid if sesli else None
        self.son_midi, self.son_sesli = mid, bool(sesli)
        out = np.empty(len(_IZ_AD))
        out[0], out[1], out[2], out[3], out[4] = harm, kontrast, self.sureklilik, self._vibrato(), self.glide
        out[5] = (mid - 60.0) / 12.0 if sesli else 0.0
        out[6:] = prof
        return out

    def _vibrato(self):
        v = self.v
        if len(self.midi) < v._nv:
            return 0.0
        if not all(list(self.ses)[-v._nv:]):
            return 0.0
        m = np.array(list(self.midi)[-v._nv:])
        if np.max(np.abs(np.diff(m))) > PERDE_ATLAMA:
            return 0.0
        x = m - np.polyval(np.polyfit(np.arange(m.size), m, 1), np.arange(m.size))
        c = v._vib_cos @ x
        sn = v._vib_sin @ x
        return float(min(1.0, (2.0 * np.sqrt(c * c + sn * sn) / m.size).max()))

    def tutulan(self):
        v = self.v
        if len(self.midi) < v._ntut or not all(list(self.ses)[-v._ntut:]):
            return False
        w = np.array(list(self.midi)[-v._ntut:])
        return bool(w.max() - w.min() <= TUTULAN_GENISLIK)


class Vokal:
    def __init__(self, rate=RATE, hop=HOP, nfft=NFFT, model=None, guc_model="varsayilan"):
        self.model = MODEL if model is None else model
        self.guc_model = GUC_MODEL if isinstance(guc_model, str) else guc_model
        self.rate, self.hop, self.nfft = int(rate), int(hop), int(nfft)
        self.hop_sn = self.hop / self.rate
        self.df = self.rate / self.nfft
        self.nb = self.nfft // 2 + 1
        f = np.arange(self.nb) * self.df
        self._f = f
        self._bant = [self._aralik(*b) for b in BANTLAR]
        self._toplam = self._aralik(*TOPLAM_ARALIK)
        self._vokal_bant = self._aralik(*VOKAL_ARALIK)
        self._harm_top = self._aralik(*HARMONIK_TOPLAM_ARALIK)
        n = int(round(12.0 * math.log2(F0_ARALIK[1] / F0_ARALIK[0]) / F0_ADIM_YARIMTON)) + 1
        self._midi = 12.0 * np.log2(F0_ARALIK[0] / 440.0) + 69.0 + F0_ADIM_YARIMTON * np.arange(n)
        self._f0 = 440.0 * 2.0 ** ((self._midi - 69.0) / 12.0)
        k = np.arange(1, HARMONIK_SAYI + 1)
        pos = self._f0[:, None] * k[None, :] / self.df
        self._i0 = np.minimum(np.floor(pos).astype(int), self.nb - 2)
        self._fr = pos - self._i0
        self._w = 1.0 / k[None, :]
        self._wsum = float(self._w.sum())
        self._nv = max(8, int(round(VIBRATO_SN / self.hop_sn)))
        tt = np.arange(self._nv) * self.hop_sn
        self._vib_cos = np.array([np.cos(2 * np.pi * fv * tt) for fv in VIBRATO_FREKANS])
        self._vib_sin = np.array([np.sin(2 * np.pi * fv * tt) for fv in VIBRATO_FREKANS])
        self._fb = self._mel_banka()
        n_f = MEL_FILTRE
        self._dct = np.cos(np.pi / n_f * (np.arange(n_f) + 0.5)[None, :] * np.arange(0, N_MFCC + 1)[:, None])
        self._ntut = max(4, int(round(TUTULAN_SN / self.hop_sn)))
        self._a_h = 1.0 - math.exp(-self.hop_sn / TAU_HIZLI)
        self._a_y = 1.0 - math.exp(-self.hop_sn / TAU_YAVAS)
        self._a_tepe = self.hop_sn / TEPE_TAU
        self._t_ilk = self._aralik(*TOPLAM_ARALIK)
        self._a_zarf = 1.0 - math.exp(-self.hop_sn / ZARF_TAU)
        self._n_kapa = max(1, int(round(TUT_KAPA_SN / self.hop_sn)))
        self._izci = _Izci(self)
        self.sifirla()

    def _aralik(self, lo, hi):
        i = np.nonzero((self._f >= lo) & (self._f < hi))[0]
        return int(i[0]), int(i[-1]) + 1

    def _mel_banka(self):
        mel = lambda f: 2595.0 * np.log10(1.0 + f / 700.0)
        inv = lambda m: 700.0 * (10.0 ** (m / 2595.0) - 1.0)
        nok = inv(np.linspace(mel(MEL_ARALIK[0]), mel(MEL_ARALIK[1]), MEL_FILTRE + 2))
        fb = np.zeros((MEL_FILTRE, self.nb))
        for i in range(MEL_FILTRE):
            lo, mi, hi = nok[i], nok[i + 1], nok[i + 2]
            yuk = np.minimum((self._f - lo) / (mi - lo), (hi - self._f) / (hi - mi))
            fb[i] = np.clip(yuk, 0.0, None)
        return fb

    def sifirla(self):
        self._n = 0
        self._duz = np.zeros(N_OZ)
        self._ilk = True
        self._izci.sifirla()
        self._tepe_db = [None, None]
        self._log_yavas = None
        self._zarf = 0.0
        self._onceki_log = None
        self._var = False
        self._dusuk = 0
        self._yok_sn = 0.0
        self._p = 0.0
        self._gi = GucIzci(self.hop_sn)
        self._ek = np.zeros(N_EK)
        self._ek_ilk = True
        self.son_ek = np.zeros(N_EK)
        self._merkez = 1.0
        self.son_ozellik = np.zeros(N_OZ)
        self.son_ham = np.zeros(N_OZ)
        self.son_durum = VokalDurum()

    def _mfcc(self, p):
        return self._dct @ np.log10(self._fb @ p + 1e-12)

    def _goreli(self, i, db):
        """dB degerini sarkinin (yavasca unutulan) tepesine gore ver."""
        if self._tepe_db[i] is None:
            self._tepe_db[i] = db + 6.0
        if db > self._tepe_db[i]:
            self._tepe_db[i] = db
        else:
            self._tepe_db[i] += (db - self._tepe_db[i]) * self._a_tepe
        return db - self._tepe_db[i]

    def _ozellik(self, oz, p, tum):
        """Ham ozellik vektoru; perde izcisi ve orta bant gucu (vokal_guc icin)."""
        pt = p if tum is None else tum
        e = np.array([float(p[a:b].sum()) for a, b in self._bant])
        ta, tb = self._toplam
        tot = float(p[ta:tb].sum()) + 1e-20
        orta = float(e[1:5].sum())
        x = np.empty(N_OZ)
        x[:13] = self._mfcc(pt)
        a, b = self._vokal_bant
        x[13] = self._goreli(0, 10.0 * math.log10(float(pt[ta:tb].sum()) + 1e-12))
        x[14] = self._goreli(1, 10.0 * math.log10(float(pt[a:b].sum()) + 1e-12))
        orta_t = float(pt[self._bant[1][0]:self._bant[4][1]].sum())
        x[15:15 + N_IZ] = self._izci.adim(pt, orta_t)[:N_IZ]
        k = 15 + N_IZ
        x[k:k + 6] = np.log10(e / tot + 1e-6)
        x[k + 6] = orta / tot
        seg = p[a:b] + 1e-20
        x[k + 7] = 1.0 - math.exp(np.mean(np.log(seg))) / float(np.mean(seg))
        x[k + 8] = oz.melodik / (oz.melodik + oz.vurmali + 1e-20)
        x[k + 9] = math.log10(orta / (oz.melodik + oz.vurmali + 1e-20) + 1e-6)
        le = math.log10(orta + 1e-12)
        if self._log_yavas is None:
            self._log_yavas = le
        self._log_yavas += 0.05 * (le - self._log_yavas)
        self._zarf += self._a_zarf * (abs(le - self._log_yavas) - self._zarf)
        x[k + 10] = self._zarf
        return x, self._izci, orta

    # ---- stereo kancasi ---------------------------------------------------
    def stereo_merkez(self, blok):
        """(hop, 2) L/R blok -> merkez agirligi 0-1: vokal bandinda orta enerji payi x kanal tutarliligi.

        Orta/yan: M=(L+R)/2, S=(L-R)/2. Vokal merkezde: S kucuk, L-R yaklasik ayni fazda.
        Yalniz son nfft ornek icin degil, bloklar uzerinden yavas ortalama (tau 0,3 sn).
        """
        x = np.asarray(blok, dtype=np.float64)
        if x.ndim != 2 or x.shape[1] != 2 or x.shape[0] != self.hop or not np.all(np.isfinite(x)):
            return self._merkez
        if not hasattr(self, "_st_buf"):
            self._st_buf = np.zeros((self.nfft, 2))
            self._st_pen = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(self.nfft) / self.nfft)
            self._st = np.array([0.0, 0.0, 0.0, 0.0])  # LR gercek, MM, SS, LL+RR
        h = self.hop
        self._st_buf[:-h] = self._st_buf[h:]
        self._st_buf[-h:] = x
        L = np.fft.rfft(self._st_buf[:, 0] * self._st_pen)
        R = np.fft.rfft(self._st_buf[:, 1] * self._st_pen)
        a, b = self._vokal_bant
        L, R = L[a:b], R[a:b]
        ll = float(np.sum(np.abs(L) ** 2))
        rr = float(np.sum(np.abs(R) ** 2))
        lr = float(np.sum((L * np.conj(R)).real))
        mm = float(np.sum(np.abs(L + R) ** 2)) / 4.0
        ss = float(np.sum(np.abs(L - R) ** 2)) / 4.0
        if ll + rr < 1e-12:
            return self._merkez
        tutarlilik = max(0.0, lr / math.sqrt(ll * rr + 1e-20))
        orta_pay = mm / (mm + ss + 1e-20)
        ham = tutarlilik * orta_pay
        self._merkez += self._a_y * (ham - self._merkez)
        return float(self._merkez)

    # ---- ana adim ---------------------------------------------------------
    def guncelle(self, oz, spek=None, merkez=None, tum=None) -> VokalDurum:
        """spek: melodik guc spektrumu (Dinleyici.son_mel); tum: tum guc spektrumu (Dinleyici.son_tum);
        merkez: stereo_merkez() ciktisi ya da None."""
        self._n += 1
        t = _sonlu(oz.t, self._n * self.hop_sn)
        sessiz = bool(oz.sessiz)
        if spek is None or sessiz:
            return self._bos_adim(t, sessiz)
        p = np.asarray(spek, dtype=np.float64)
        if p.shape != (self.nb,) or not np.all(np.isfinite(p)):
            return self._bos_adim(t, sessiz)
        if tum is not None:
            tum = np.asarray(tum, dtype=np.float64)
            if tum.shape != (self.nb,) or not np.all(np.isfinite(tum)):
                tum = None
        x, iz, orta = self._ozellik(oz, p, tum)
        self.son_orta = orta
        self.son_ham = x
        if self._ilk:
            self._duz[:] = x
            self._ilk = False
        self._duz += self._a_h * (x - self._duz)
        z = self._duz
        self.son_ozellik = z.copy()
        pr = model_olasilik(self.model, z)
        ek = np.array([iz.comb_db, iz.comb_oran])
        if self._ek_ilk:
            self._ek[:] = ek
            self._ek_ilk = False
        self._ek += self._a_h * (ek - self._ek)
        self.son_ek = self._ek.copy()
        gdb = None
        if self.guc_model is not None:
            gm = self.guc_model
            gdb = float(np.dot(gm["W"], (np.concatenate([x, z, self._ek]) - gm["ORT"]) / gm["STD"]) + gm["B"])
        m = 1.0
        if merkez is not None:
            m = min(1.0, max(0.0, _sonlu(merkez, 1.0)))
            pr *= 0.35 + 0.65 * m
        self._p = pr
        return self._bayrak(t, pr, iz, orta, m, gdb=gdb)

    def _bos_adim(self, t, sessiz):
        self._izci.sifirla()
        return self._bayrak(t, 0.0, None, 0.0, self._merkez, bos=True)

    def _bayrak(self, t, pr, iz, orta, m, bos=False, gdb=None):
        onceki = self._var
        if not self._var:
            if pr >= ESIK_AC:
                self._var = True
                self._dusuk = 0
        else:
            if pr < ESIK_KAPA:
                self._dusuk += 1
                if self._dusuk >= self._n_kapa:
                    self._var = False
            else:
                self._dusuk = 0
        giris = self._var and not onceki
        cikis = onceki and not self._var
        if self._var:
            self._yok_sn = 0.0
        else:
            self._yok_sn += self.hop_sn

        # vokal gucu: (ogrenilmis) vokal dB tahmini, sarkinin son cumlelerine gore olceklenir
        if not bos and orta > 1e-12:
            db = gdb if gdb is not None else 10.0 * math.log10(orta)
            self._gi.adim(db, self._var, pr)
        else:
            self._gi.adim(0.0, self._var, pr, aktif=False)
        self._guc = self._gi.guc

        # perde ve tutulan nota
        perde = 0.0
        tutulan = False
        if self._var and not bos and iz.son_sesli:
            perde = 440.0 * 2.0 ** ((iz.son_midi - 69.0) / 12.0)
            tutulan = iz.tutulan()
        d = VokalDurum(vokal=float(pr), vokal_var=bool(self._var), vokal_guc=float(self._guc),
                       vokal_giris=bool(giris), vokal_cikis=bool(cikis), perde_hz=float(perde),
                       tutulan=tutulan, vokal_yok_sn=float(self._yok_sn), merkez=float(m))
        self.son_durum = d
        return d


def bayrak_dizisi(p, ac=ESIK_AC, kapa=ESIK_KAPA, n_kapa=None, hop_sn=HOP / RATE):
    """Olasilik dizisinden Vokal'in histerezis mantiginin toplu kopyasi (olcum/ayar icin; testle esitligi dogrulanir)."""
    if n_kapa is None:
        n_kapa = max(1, int(round(TUT_KAPA_SN / hop_sn)))
    var = np.zeros(len(p), dtype=bool)
    d, k = False, 0
    for i, x in enumerate(p):
        if not d:
            if x >= ac:
                d, k = True, 0
        elif x < kapa:
            k += 1
            if k >= n_kapa:
                d = False
        else:
            k = 0
        var[i] = d
    return var
