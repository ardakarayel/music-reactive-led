"""Dionysos isik motoru: Spektrum gibi akar; muzik kesilince kararir, donunce patlar;
vokalin duygusu rengi degistirir. Yalniz numpy.

Katmanlar:
  1. Taban (Spektrum, music.py Basit._analiz): parlaklik = sesin gurlugu (oz.enerji) + kick nabzi,
     renk = bas-tiz orani (15 + 200 * oran) ile vokal ton (valence) karisimi, yavas kayar.
  2. Olaylar (seyrek): kesilme (V=0), donus (V=1, o anki renk), vurgu (kisa V dususu).
  3. Vokal duygu: yogunluk (arousal) + ton (valence). Yogunlukta donum noktasi:
     yukari -> ani zit renk (+150..180 derece) + V=1 kisa; asagi -> V %30'a, soguk/yumusak renk.
  4. Guvenlik: saniyede en cok 3 sert gecis (V sicramasi > 0,6 ya da ton sicramasi > 90 derece).
  5. Vokal takip (isik.py VOKAL_TAKIP ile ayni kural): vokal varken tabanin w = min(0,95, 0,70 * vokal_agirlik)
     kadari dogrudan vokalin ses gucu + perde yuksekliginden gelir; kalan (1-w) Spektrum tabani, kick nabzi
     vokal payinda %30 korunur. Olay katmani (kesilme, donus, duygu, vurgu, guvenlik) bunun USTUNDE degismeden calisir.

Aciklanabilirlik: V = sum(v_bas, v_davul, v_melodi, v_vokal) + v_olay; paylar bundan cikar (son_viz).
Beyaz yok: doygunluk her zaman >= 0,75.
"""
import colorsys
import math
import random

import numpy as np

from .isik import IsikKare

RATE = 44100
HOP = 512
NFFT = 2048
HOP_SN = HOP / float(RATE)

# ---- taban (Spektrum ile ayni sabitler) ----
TABAN = 0.04
VURUS_SONUM_SN = 0.13
DUSUS_KATSAYI = 0.35          # Spektrum: hedefin altinda blok basina bu kadar iner
ORAN_TAU_SN = 1.2             # bas-tiz orani yumusakligi (Spektrum 0,4; Dionysos daha yavas)
RENK_TAU_SN = 0.8             # son renk alcak geciren
RENK_EN_AZ_FARK = 90.0        # yeni renk son 3 renkten en az bu kadar uzak
RENK_ARA_SN = 8.0             # donus (drop) kaynakli renk degisimleri arasi en az
UZUN_SESSIZ_SN = 1.5         # bu kadar sessizlik (ya da kesilme karanligi) sonrasi yeni renk
UZUN_SESSIZ_DB = -55.0        # kisa gurluk bunun altindaysa sessiz sayilir
# Parlaklik esigi: isik esigin altina dusup geri cikinca zarla yeni renk (Arda, 2026-11-19:
# "parlaklik esigi koyalim onun altina dusunce %50 kurali devreye girsin"). Diger renk kurallari aynen calisir.
ESIK_RENK_V = 0.10            # bu parlakligin altina dusus sayilir (seride giden son V)
ESIK_RENK_SURE_SN = 0.15      # esik altinda en az bu kadar kalmali (vurus arasi kisa dipler sayilmaz)
ESIK_RENK_TAMPON = 0.03       # geri cikis icin esik + tampon (esik cevresinde titreme iki dusus sayilmaz)
# Palet (Arda, 2026-11-19: "palet olusturalim sadece o renkler arasinda donsun", istege bagli).
# palet_1..palet_8: 0xRRGGBB, -1 = bos. Yalniz ton alinir; canlilik "doygunluk"tan gelir (beyaz yok).
PALET_N = 8
PALET_VARSAYILAN = (0xFF2D55, 0x8E44FF, 0x1E90FF, 0xFF8C1A, -1, -1, -1, -1)
YENI_RENK_DER = (150.0, 210.0, 165.0, 195.0)   # sirayla: her seferinde belirgin farkli renk
VURUS_KAYMA_DER = 4.0        # vurusta renk kaymasi (derece), birikmez
VURUS_KAYMA_SN = 0.15
SAT = 0.9                     # doygunluk (Spektrum 90); yogunlukla 0,8-1,0 arasi
HUE_BAS_TIZ_PAY = 0.6         # renk karisimi: bas-tiz agirligi (kalan duygu)
HUE_SICAK = 25.0              # ton = 1 (nese)
HUE_SOGUK = 235.0             # ton = 0 (huzun)
HUE_YUMUSAK_SOGUK = 215.0     # duygu_asagi sonrasi yeni renk

# ---- kesilme / donus ----
KISA_SN = 0.05                # kisa gurluk
KES_ORT_SN = 2.0              # onceki ortalama penceresi
KES_GECIKME_SN = 0.15         # ortalama bu kadar geriden biter
KES_DB = 15.0               # sozlesme >= 12; gercek kayitlarda 12'de dakikada 4-5 sahte, 15'te <= 2,5
KES_ONCE_DB = 6.0             # 150 ms once hala ortalamanin en fazla bu kadar altinda
KES_YETERLI_DB = -50.0        # onceki 2 sn bundan sessizse kesilme sayilmaz
KES_ARA_SN = 1.0
KES_BEKLE_SN = 3.0            # kesilmeden sonra ses geri gelmezse yumusakca acilir
KES_GERI_DB = 8.0
KES_ACIL_SN = 0.4
DON_PENCERE_SN = 0.3
DON_DB = 10.0
DON_ARA_SN = 4.0              # kesilme sonrasi donus icin
DON_ARA_ACIK_SN = 4.0         # kesilmesiz donus: iki donus arasi en az
DON_ENERJI = 0.6              # varis sarkinin kendi dinamik araliginda yuksek olmali
DON_SUR = 8                  # blok (~93 ms)
DON_SUR_DB = 8.0
DON_SUR_BAS_DB = 5.0
DON_BAS_DB = 6.0              # hizli donusta bas (sub+kick) da bu kadar sicramali
DON_UZUN_SN = 4.0             # kesilme disinda: son ~4 sn ortalamasinin da bu kadar ustunde olmali
DON_UZUN_DB = 8.0
DON_SONUM_SN = 0.17           # ~0,5 sn'de tabana
ANLIK_KISA_SN = 0.20
ANLIK_UZUN_SN = 3.0
ANLIK_GUR_DB = 3.0
ANLIK_BAS_DB = 10.0
ANLIK_ARA_SN = 4.0
ISINMA_SN = 5.0

# ---- vurgu ----
VURGU_ACIK = True
VURGU_SN = 0.07
VURGU_DUSUS = 0.35            # V bu carpanla
VURGU_ARA_SN = 15.0
VURGU_ISINMA_SN = 10.0
VURGU_BAS_MAX_DB = 4.0        # crash: genis bantli, bas sicramasi yok (bas varsa donustur)
VURGU_HAT_ORAN = 6.0
VURGU_DB = 4.0

# ---- vokal duygu ----
YOG_TAU_SN = 1.5
DONUM_ONCE_SN = 4.0
DONUM_SONRA_SN = 1.0
DONUM_ESIK = 0.11
DONUM_ARA_SN = 6.0
YUKARI_OFSET = 165.0
OFSET_TAU_SN = 40.0
YUKARI_SONUM_SN = 0.25
ASAGI_DIM = 0.30
ASAGI_GERI_SN = 4.0
ASAGI_BEKLE_SN = 1.5
ASAGI_DIM_SN = 0.25
TON_TAU_SN = 5.0

# ---- vokal takip (isik.py VOKAL_TAKIP "parti" ile ayni sayilar) ----
VT_W = 0.70                   # vokal_agirlik = 1'de taban parlakligin vokalden gelen payi
VT_UST = 0.95                 # pay ust siniri
VT_TABAN = 0.15               # vokal_v = taban + guc * vokal_guc + perde * perde_norm
VT_GUC = 0.75
VT_PERDE = 0.15
VT_NABIZ_PAY = 0.30           # vokal varken kick nabzinin korunan payi
VT_ATAK_VURUS = 1.0           # giris ~1 vurusta (tau = bu / 3)
VT_SONUM_VURUS = 2.0          # cikis ~2 vurusta
VT_PERDE_TAU = 0.15           # perde_norm yumusatma
VT_ARALIK_TAU = 30.0          # sarkinin vokal perde araligi bu surede unutulur
VT_VURUS_VARSAYILAN = 0.5     # tempo yoksa vurus suresi (sn)

# ---- guvenlik ----
SERT_V = 0.6
SERT_H = 90.0
SERT_PENCERE = 5              # blok (~58 ms; cihaz gonderim adimi 62 ms)
SERT_SAYI = 3
SERT_SURE_SN = 1.0

# ---- ayarlanabilir parametreler (laboratuvar): ornek duzeyi sozluk, varsayilanlar yukaridaki sabitlerdir ----
VARSAYILAN = {
    "taban": TABAN, "tavan": 0.75, "dusus": DUSUS_KATSAYI, "doygunluk": 0.85,
    "vurus_sonum_sn": VURUS_SONUM_SN, "nabiz_alt": 0.3, "vt_nabiz_pay": VT_NABIZ_PAY,
    "oran_tau_sn": ORAN_TAU_SN, "renk_tau_sn": RENK_TAU_SN, "renk_en_az_fark": RENK_EN_AZ_FARK,
    "renk_ara_sn": RENK_ARA_SN, "uzun_sessiz_sn": UZUN_SESSIZ_SN, "uzun_sessiz_db": UZUN_SESSIZ_DB,
    "renk_kayma": VURUS_KAYMA_DER, "renk_kayma_sn": VURUS_KAYMA_SN, "renk_serbest": True,
    "palet_acik": False, "palet_sira": False, **{"palet_%d" % i: float(c) for i, c in enumerate(PALET_VARSAYILAN, 1)},
    "esik_renk_v": ESIK_RENK_V, "esik_renk_olasilik": 0.0, "esik_renk_sure_sn": ESIK_RENK_SURE_SN,
    "kesilme_acik": True, "donus_acik": True, "duygu_acik": True, "vurgu_acik": VURGU_ACIK,
    "kes_db": KES_DB, "kes_once_db": KES_ONCE_DB, "kes_yeterli_db": KES_YETERLI_DB, "kes_ara_sn": KES_ARA_SN,
    "kes_bekle_sn": KES_BEKLE_SN, "kes_geri_db": KES_GERI_DB, "kes_acil_sn": KES_ACIL_SN,
    "kes_ort_sn": KES_ORT_SN, "kes_gecikme_sn": KES_GECIKME_SN,
    "don_pencere_sn": DON_PENCERE_SN, "don_db": DON_DB, "don_ara_sn": DON_ARA_SN, "don_ara_acik_sn": DON_ARA_ACIK_SN,
    "don_enerji": DON_ENERJI, "don_sur": DON_SUR, "don_sur_db": DON_SUR_DB, "don_sur_bas_db": DON_SUR_BAS_DB,
    "don_bas_db": DON_BAS_DB, "don_uzun_sn": DON_UZUN_SN, "don_uzun_db": DON_UZUN_DB, "don_sonum_sn": DON_SONUM_SN,
    "anlik_kisa_sn": ANLIK_KISA_SN, "anlik_uzun_sn": ANLIK_UZUN_SN, "anlik_gur_db": ANLIK_GUR_DB,
    "anlik_bas_db": ANLIK_BAS_DB, "anlik_ara_sn": ANLIK_ARA_SN, "isinma_sn": ISINMA_SN,
    "vurgu_sn": VURGU_SN, "vurgu_dusus": VURGU_DUSUS, "vurgu_ara_sn": VURGU_ARA_SN, "vurgu_isinma_sn": VURGU_ISINMA_SN,
    "vurgu_bas_max_db": VURGU_BAS_MAX_DB, "vurgu_hat_oran": VURGU_HAT_ORAN, "vurgu_db": VURGU_DB,
    "vokal_agirlik": 1.0, "vt_w": VT_W, "vt_ust": VT_UST, "vt_taban": VT_TABAN, "vt_guc": VT_GUC, "vt_perde": VT_PERDE,
    "vt_atak_vurus": VT_ATAK_VURUS, "vt_sonum_vurus": VT_SONUM_VURUS, "vt_perde_tau": VT_PERDE_TAU,
    "vt_aralik_tau": VT_ARALIK_TAU,
    "yog_tau_sn": YOG_TAU_SN, "donum_esik": DONUM_ESIK, "donum_ara_sn": DONUM_ARA_SN,
    "yukari_sonum_sn": YUKARI_SONUM_SN, "asagi_dim": ASAGI_DIM, "asagi_geri_sn": ASAGI_GERI_SN,
    "asagi_bekle_sn": ASAGI_BEKLE_SN, "asagi_dim_sn": ASAGI_DIM_SN,
}
# Dongusu sonsuz/dev olmasin diye halka bellegine (640 blok ~ 7,4 sn) sigmasi gereken sureler
_HALKA_UST = {"kes_ort_sn": 4.0, "kes_gecikme_sn": 0.4, "don_uzun_sn": 6.0, "don_pencere_sn": 0.6}


_F = np.fft.rfftfreq(NFFT, 1.0 / RATE)
_BAS_M = _F < 250.0
_TIZ_M = _F > 2000.0
_VOKAL_M = (_F >= 200.0) & (_F <= 4000.0)
_VOKAL_F = _F[_VOKAL_M]

# Krumhansl-Kessler anahtar profilleri
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _profiller():
    out = []
    for p in (_MAJOR, _MINOR):
        for k in range(12):
            r = np.roll(p, k)
            r = r - r.mean()
            out.append(r / np.linalg.norm(r))
    return np.array(out)


_PROFIL = _profiller()   # (24, 12): 0-11 majorler, 12-23 minorler


def spektrum_ozeti(tum, mel):
    """(bas, tiz, vokal_parlaklik): bas/tiz = Spektrum'daki genlik toplamlari (<250 Hz, >2 kHz);
    parlaklik = melodik spektrumun 200-4000 Hz agirlik merkezi, log olcekte 0-1 (-1: bilinmiyor)."""
    bas = tiz = 0.0
    par = -1.0
    try:
        if tum is not None and len(tum) == _F.size:
            m = np.sqrt(np.maximum(np.asarray(tum, dtype=np.float64), 0.0))
            bas = float(m[_BAS_M].sum())
            tiz = float(m[_TIZ_M].sum())
        if mel is not None and len(mel) == _F.size:
            p = np.asarray(mel, dtype=np.float64)[_VOKAL_M]
            s = float(p.sum())
            if s > 1e-12:
                c = float((_VOKAL_F * p).sum() / s)
                par = min(1.0, max(0.0, math.log2(max(c, 200.0) / 200.0) / math.log2(20.0)))
    except Exception:
        pass
    return bas, tiz, par


def _sonlu(x, v=0.0):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return v
    return x if math.isfinite(x) else v


def _kisi(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _acisal(a):
    """(-180, 180]"""
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def _harmanla(hlar, agirliklar):
    """Dairesel agirlikli ortalama ton (0-360)."""
    x = sum(w * math.cos(math.radians(h)) for h, w in zip(hlar, agirliklar))
    y = sum(w * math.sin(math.radians(h)) for h, w in zip(hlar, agirliklar))
    if abs(x) < 1e-9 and abs(y) < 1e-9:
        return hlar[0] % 360.0
    return math.degrees(math.atan2(y, x)) % 360.0


def _sigmoid(z):
    return 1.0 / (1.0 + math.exp(-_kisi(z, -30.0, 30.0)))


class _Aralik:
    """Yavas tepe/dip ile dB'yi 0-1'e olcekler (dinleme.py _enerji mantigi, egri yok)."""

    def __init__(self, aralik_sn=60.0, pay_db=15.0, en_az_db=14.0):
        self.u = HOP_SN / aralik_sn
        self.pay, self.en_az = pay_db, en_az_db
        self.tepe = self.dip = None

    def sifirla(self):
        self.tepe = self.dip = None

    def adim(self, db):
        if self.tepe is None:
            self.tepe, self.dip = db + self.pay, db - 6.0
        if db > self.tepe:
            self.tepe = db
        else:
            self.tepe += (db - self.tepe) * self.u
        if db < self.dip:
            self.dip = db
        else:
            self.dip += (db - self.dip) * self.u
        ar = max(self.tepe - self.dip, self.en_az)
        return _kisi((db - (self.tepe - ar)) / ar, 0.0, 1.0)


class _Zscore:
    """Kosan ortalama/sapma (tau sn); z -> 0-1 sigmoid."""

    def __init__(self, tau_sn=25.0, min_std=0.05):
        self.a = HOP_SN / tau_sn
        self.min_std = min_std
        self.ort = None
        self.var = 0.0

    def sifirla(self):
        self.ort = None
        self.var = 0.0

    def adim(self, x):
        if self.ort is None:
            self.ort = x
            self.var = self.min_std ** 2
        d = x - self.ort
        self.ort += self.a * d
        self.var += self.a * (d * d - self.var)
        sd = max(math.sqrt(max(self.var, 0.0)), self.min_std)
        return _sigmoid(1.6 * (x - self.ort) / sd)


class DionysosMotoru:
    def __init__(self, mod="dionysos", yogunluk=1.0, params=None):
        self.p = dict(VARSAYILAN)    # ayarlanabilir parametreler (ornek duzeyi); varsayilanlar eski sabitlerle ayni
        self.gecikme_sn = 0.0
        self.taban_ton = None        # kullanici rengi: Dionysos kendi rengini secer
        self.mod = "dionysos"
        if params:
            self.ayarla(params=params)
        self.sifirla()

    # eski nitelik adlari: panel/music.py dogrudan bunlari okur ve yazar
    @property
    def vokal_agirlik(self):
        return self.p["vokal_agirlik"]

    @vokal_agirlik.setter
    def vokal_agirlik(self, v):
        self.p["vokal_agirlik"] = float(v)

    @property
    def renk_kayma(self):
        return self.p["renk_kayma"]    # vurusta renk kaymasi (derece), panelden 0-60 (Arda, 2026-11-18)

    @renk_kayma.setter
    def renk_kayma(self, v):
        self.p["renk_kayma"] = float(v)

    @property
    def vurgu_acik(self):
        return self.p["vurgu_acik"]

    @vurgu_acik.setter
    def vurgu_acik(self, v):
        self.p["vurgu_acik"] = bool(v)

    def ayarla(self, mod=None, yogunluk=None, params=None):
        """Calisirken (sifirlamadan) parametre uygular. params: {id: deger}; bilinmeyen anahtar ve
        sayisal olmayan/sonsuz deger atlanir, deger guvenli araliga kisilir. Donus: uygulanan sayi."""
        if not params or not isinstance(params, dict):
            return 0
        n = 0
        for k, v in params.items():
            if k not in self.p:
                continue
            mevcut = VARSAYILAN[k]
            if isinstance(mevcut, bool):
                if isinstance(v, (bool, int)) or (isinstance(v, float) and math.isfinite(v)):
                    self.p[k] = bool(v)
                    n += 1
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                continue
            v = float(v)
            if k in _HALKA_UST:
                v = _kisi(v, 0.0, _HALKA_UST[k])
            if isinstance(mevcut, int):
                v = int(round(_kisi(v, 1.0, 40.0)))
            self.p[k] = v
            n += 1
        return n

    # ------------------------------------------------------------------ durum
    def sifirla(self):
        p = self.p
        n = 640
        self._n = 0
        self._t = 0.0
        self._ring = np.full(n, -120.0)      # kisa gurluk dB gecmisi
        self._ring_b = np.full(n, -120.0)    # kisa bas (sub+kick) dB gecmisi
        self._kisa_b = None
        self._rn = n
        self._kisa = None
        # kesilme/donus
        self._kes = False
        self._kes_t = 0.0
        self._kes_ref = -120.0
        self._son_kes = -1e9
        self._son_don = -1e9
        self._gate = 1.0
        self._gate_hedef_t = None
        self._boost = 0.0
        self._boost_tau = p["don_sonum_sn"]
        # anlik patlama (kendi kopyamiz: bolume bagli degil)
        self._a_kisa_g = self._a_kisa_b = self._a_uz_g = self._a_uz_b = None
        self._son_patlama = -1e9
        # vurgu
        self._dip_bitis = -1.0
        self._son_vurgu = -1e9
        self._hat_ort = 0.0
        # taban
        self._pulse = 0.0
        self._level = 0.0
        self._base_lvl = 0.0
        self._oran = 0.3
        self._h_yumusak = None
        self._vurus_kayma = 0.0
        # vokal duygu
        self._yog = 0.0
        self._yog_var = False
        self._yog_hist = np.zeros(int(round((DONUM_ONCE_SN + DONUM_SONRA_SN + 0.5) / HOP_SN)))
        self._vok_hist = np.zeros_like(self._yog_hist)
        self._yh = 0
        self._son_donum = -1e9
        self._midi = np.zeros(43)
        self._midi_n = 0
        self._z_perde = _Zscore()
        self._z_parlak = _Zscore()
        self._vib = 0.0
        self._ton = 0.5
        self._ton_hedef = 0.5
        self._chroma = np.full(12, 1.0 / 12.0)
        self._ofset = 0.0
        self._renk = random.uniform(0.0, 360.0)
        self._renk_gecmis = [self._renk]
        self._son_renk = -1e9
        self._sessiz_bas = None
        self._yr_sayac = 0
        self._esik_alt_t = None
        self._palet_imza = None
        self._palet_i = -1
        self._dim_ev = -1e9
        self._sat_dus = 0.0
        self._ton_mod = 0.0
        # vokal takip
        self._vt_giris = 0.0
        self._vt_perde = 0.0
        self._vt_alt = None
        self._vt_ust = None
        # paylar
        self._w = np.array([0.25, 0.25, 0.25, 0.25])    # bas, davul, melodi, vokal
        self._ar_bas = _Aralik()
        self._ar_mel = _Aralik()
        self._bas_n = 0.0
        self._mel_n = 0.0
        # guvenlik
        self._v_gecmis = []
        self._h_gecmis = []
        self._sert_zamanlar = []
        self._son_v = 0.0
        self._son_h = 0.0
        # gorsel
        self._tut = {"kick": 0.0, "snare": 0.0, "hat": 0.0}
        self._olay_etiket = ""
        self._olay_bit = 0.0
        self.son_viz = None
        self.son_kisa_db = -120.0
        self.son_ort2 = None
        self.olay_sayac = {}

    # ------------------------------------------------------------------ yardimcilar
    def _ort_db(self, bas_geri, son_geri):
        """Halka gecmisinde [bas_geri, son_geri) blok onceki araligin dB ortalamasi (bas_geri > son_geri)."""
        i1 = self._n - son_geri
        i0 = self._n - bas_geri
        if i0 < 0:
            i0 = 0
        if i1 <= i0:
            return None
        idx = np.arange(i0, i1) % self._rn
        return float(self._ring[idx].mean())

    def _db_geri(self, k):
        j = self._n - k
        return float(self._ring[j % self._rn]) if j >= 0 else -120.0

    def _anahtar_ton(self, oz, rd, sessiz):
        """Chroma anahtar profili (Krumhansl), tempo ve parlaklikla kaba ton hedefi 0-1."""
        c = self._chroma
        ck = c - c.mean()
        nrm = float(np.linalg.norm(ck))
        if nrm < 1e-9:
            return 0.5
        r = _PROFIL @ (ck / nrm)
        mod = float(r[:12].max() - r[12:].max())
        mod_n = _kisi(mod / 0.15, -1.0, 1.0)
        bpm = _sonlu(getattr(rd, "bpm", 0.0), 0.0)
        tempo_n = _kisi((bpm - 95.0) / 45.0, -1.0, 1.0) if bpm > 0.0 else 0.0
        mk = _sonlu(oz.merkez_hz, 0.0)
        par_n = _kisi(math.log2(max(mk, 100.0) / 900.0) / 1.5, -1.0, 1.0) if mk > 0 else 0.0
        self._ton_mod = mod
        return _kisi(0.5 + 0.5 * (0.55 * mod_n + 0.25 * tempo_n + 0.20 * par_n), 0.0, 1.0)

    def _palet(self):
        """Palet aciksa ve en az 2 renk varsa tonlar (derece) listesi, yoksa bos liste."""
        p = self.p
        if not p["palet_acik"]:
            return []
        tonlar = []
        for i in range(1, PALET_N + 1):
            c = int(p.get("palet_%d" % i, -1))
            if 0 <= c <= 0xFFFFFF:
                r, g, b = ((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0
                tonlar.append(colorsys.rgb_to_hsv(r, g, b)[0] * 360.0)
        return tonlar if len(tonlar) >= 2 else []

    def _renk_degis(self, t, zorla=False):
        """Yeni serbest renk: son 3 renkten en az renk_en_az_fark derece uzak, tum cember.
        zorla=False iken (donus) en az renk_ara_sn gecmis olmali."""
        p = self.p
        palet = self._palet()
        if not p["renk_serbest"] and not palet:
            return    # renk serbest kapali: renk bas-tiz oranindan gelir, rastgele degisim yok
        if not zorla and t - self._son_renk < p["renk_ara_sn"]:
            return
        if palet:
            n = len(palet)
            if p["palet_sira"]:
                self._palet_i = (self._palet_i + 1) % n
            else:   # karisik: hemen onceki renk tekrar gelmez
                self._palet_i = random.choice([i for i in range(n) if i != self._palet_i] or [0])
            aday = palet[self._palet_i]
        else:
            for _ in range(40):
                aday = random.uniform(0.0, 360.0)
                if all(abs(_acisal(aday - g)) >= p["renk_en_az_fark"] for g in self._renk_gecmis[-3:]):
                    break
        self._renk = aday
        self._h_yumusak = aday          # ani gecis
        self._renk_gecmis.append(aday)
        del self._renk_gecmis[:-6]
        self._son_renk = t

    # ------------------------------------------------------------------ ana adim
    def kare(self, oz, rd, vokal=None, tum=None, mel=None, ozet=None):
        p = self.p
        dt = HOP_SN
        self._n += 1
        n = self._n
        t = n * dt
        self._t = t
        sessiz = bool(getattr(oz, "sessiz", False))

        # ---- kisa gurluk (50 ms), dB ----
        p_bant = [10.0 ** (_sonlu(b, -120.0) / 10.0) for b in oz.bant_db]
        p_tot = sum(p_bant)
        ak = 1.0 - math.exp(-dt / KISA_SN)
        if self._kisa is None:
            self._kisa = p_tot
        self._kisa += ak * (p_tot - self._kisa)
        kisa_db = 10.0 * math.log10(self._kisa + 1e-12)
        if sessiz:
            kisa_db = min(kisa_db, -100.0)
        self._ring[n % self._rn] = kisa_db
        pb = p_bant[0] + p_bant[1]
        if self._kisa_b is None:
            self._kisa_b = pb
        self._kisa_b += ak * (pb - self._kisa_b)
        kisa_b_db = 10.0 * math.log10(self._kisa_b + 1e-12)
        self._ring_b[n % self._rn] = kisa_b_db
        isinma = t < p["isinma_sn"]

        olay = ""
        kb = max(1, int(round(p["kes_gecikme_sn"] / dt)))
        ort2 = self._ort_db(int(round((p["kes_ort_sn"] + p["kes_gecikme_sn"]) / dt)), kb)
        ort3 = self._ort_db(int(round((p["don_pencere_sn"] + 2 * dt) / dt)), 2)
        self.son_kisa_db, self.son_ort2 = kisa_db, ort2    # laboratuvar ekolayzir cizgileri

        # ---- anlik patlama (kick+sub >= +10 dB, gurluk >= +3 dB) ----
        bas_db = 10.0 * math.log10(p_bant[0] + p_bant[1] + 1e-12)
        g_db = _sonlu(oz.gur_db, -90.0)
        if self._a_kisa_g is None:
            self._a_kisa_g = self._a_uz_g = g_db
            self._a_kisa_b = self._a_uz_b = bas_db
        a1 = 1.0 - math.exp(-dt / p["anlik_kisa_sn"])
        a2 = 1.0 - math.exp(-dt / p["anlik_uzun_sn"])
        self._a_kisa_g += a1 * (g_db - self._a_kisa_g)
        self._a_kisa_b += a1 * (bas_db - self._a_kisa_b)
        anlik = (not sessiz and not isinma and t - self._son_patlama > p["anlik_ara_sn"]
                 and self._a_kisa_g - self._a_uz_g >= p["anlik_gur_db"]
                 and self._a_kisa_b - self._a_uz_b >= p["anlik_bas_db"])
        self._a_uz_g += a2 * (g_db - self._a_uz_g)
        self._a_uz_b += a2 * (bas_db - self._a_uz_b)
        if isinma:
            self._a_uz_g, self._a_uz_b = self._a_kisa_g, self._a_kisa_b   # isinmada uzun ortalama kisayi izler
        if anlik:
            self._son_patlama = t

        # ---- kesilme ----
        if (p["kesilme_acik"] and not self._kes and not isinma and ort2 is not None
                and ort2 > p["kes_yeterli_db"] and t - self._son_kes > p["kes_ara_sn"]
                and kisa_db < ort2 - p["kes_db"]
                and self._db_geri(kb) > ort2 - p["kes_once_db"]):
            self._kes = True
            self._kes_t = t
            self._kes_ref = ort2
            self._son_kes = t
            self._gate = 0.0
            self._gate_hedef_t = None
            self._boost = 0.0
            olay = "kesilme"

        # ---- donus ----
        i1 = n - 2
        i0 = max(0, n - int(round((p["don_pencere_sn"] + 2 * dt) / dt)))
        bas_ort3 = float(self._ring_b[np.arange(i0, i1) % self._rn].mean()) if i1 > i0 else kisa_b_db
        ort4 = self._ort_db(int(round((p["don_uzun_sn"] + p["don_pencere_sn"]) / dt)), int(round(p["don_pencere_sn"] / dt)))
        # surekli artis: son p["don_sur"] blokta en dusuk deger bile, ondan onceki 300 ms ortalamasinin uzerinde
        # (piyano/gitar vurusu gibi hizla sonen tepeler donus sayilmaz)
        sb1 = n - p["don_sur"]
        sb0 = max(0, sb1 - 26)
        if sb1 > sb0:
            ix = np.arange(sb0, sb1) % self._rn
            tb = float(self._ring[ix].mean())
            tbb = float(self._ring_b[ix].mean())
            sx = np.arange(sb1 + 1, n + 1) % self._rn
            sur_g = float(self._ring[sx].min()) - tb
            sur_b = float(self._ring_b[sx].min()) - tbb
        else:
            sur_g = sur_b = 0.0
        don_hizli = (not isinma and not sessiz and kisa_db > p["kes_yeterli_db"]
                     and (self._kes or _sonlu(oz.enerji, 0.0) >= p["don_enerji"])
                     and ((self._kes and ort3 is not None and kisa_db - ort3 >= p["don_db"]
                           and kisa_b_db - bas_ort3 >= p["don_bas_db"])
                          or (sur_g >= p["don_sur_db"] and sur_b >= p["don_sur_bas_db"]
                              and ort4 is not None and kisa_db - ort4 >= p["don_uzun_db"])))
        don = p["donus_acik"] and (don_hizli or anlik) and t - self._son_don > (p["don_ara_sn"] if self._kes else p["don_ara_acik_sn"]) and not (olay == "kesilme")
        if self._kes and (t - self._kes_t) < 0.1:
            don = False    # kesilmenin kendi dususu donus sayilmaz
        if don:
            self._boost = 1.0
            self._boost_tau = p["don_sonum_sn"]
            self._son_don = t
            self._gate = 1.0
            self._gate_hedef_t = None
            self._kes = False
            olay = "donus"
        elif self._kes and not sessiz:
            if (t - self._kes_t) >= p["kes_bekle_sn"] or (
                    (t - self._kes_t) >= 0.3 and kisa_db > self._kes_ref - p["kes_geri_db"]):
                self._kes = False
                self._gate_hedef_t = t     # yumusak acilis
        if self._gate_hedef_t is not None:
            self._gate = _kisi((t - self._gate_hedef_t) / p["kes_acil_sn"], 0.0, 1.0)
            if self._gate >= 1.0:
                self._gate_hedef_t = None

        # ---- uzun sessizlik sonrasi yeni renk (Arda, 2026-11-18: "uzun sessizlik sonrasi renk
        # kesinlikle farkli baslasin") ----
        sessizlik = sessiz or kisa_db < p["uzun_sessiz_db"] or self._gate <= 0.0
        if sessizlik:
            if self._sessiz_bas is None:
                self._sessiz_bas = t
        else:
            if (self._sessiz_bas is not None and not isinma
                    and t - self._sessiz_bas >= p["uzun_sessiz_sn"]):
                self._yr_sayac += 1
                atla = YENI_RENK_DER[self._yr_sayac % len(YENI_RENK_DER)]
                self._renk_degis(t, zorla=True)
                if not olay:
                    olay = "donus"
            self._sessiz_bas = None

        # ---- vurgu (crash/hit) ----
        hat = _sonlu(oz.hat, 0.0)
        hat_oran = hat / (self._hat_ort + 1e-3)
        self._hat_ort += (hat - self._hat_ort) * (1.0 - math.exp(-dt / 4.0))
        if (p["vurgu_acik"] and not olay and not isinma and t > p["vurgu_isinma_sn"] and not don
                and kisa_b_db - bas_ort3 < p["vurgu_bas_max_db"] and not sessiz and not self._kes
                and oz.hat_var and hat_oran >= p["vurgu_hat_oran"] and ort3 is not None
                and kisa_db - ort3 >= p["vurgu_db"] and kisa_db - ort3 < p["don_db"] and not anlik
                and t - self._son_vurgu > p["vurgu_ara_sn"]
                and t - self._son_don > 1.0):
            self._son_vurgu = t
            self._dip_bitis = t + p["vurgu_sn"]
            olay = "vurgu"
        dip = p["vurgu_dusus"] if t < self._dip_bitis else 1.0

        # ---- spektrum ozeti ----
        if ozet is None:
            ozet = spektrum_ozeti(tum, mel)
        bas_s, tiz_s, parlak = ozet

        # ---- vokal duygu ----
        var = bool(getattr(vokal, "vokal_var", False)) and not sessiz
        guc = _kisi(_sonlu(getattr(vokal, "vokal_guc", 0.0), 0.0), 0.0, 1.0)
        perde = _sonlu(getattr(vokal, "perde_hz", 0.0), 0.0)
        perde_n = 0.5
        if var and perde > 50.0:
            midi = 69.0 + 12.0 * math.log2(perde / 440.0)
            self._midi[:-1] = self._midi[1:]
            self._midi[-1] = midi
            self._midi_n = min(self._midi_n + 1, self._midi.size)
            perde_n = self._z_perde.adim(midi)
            if self._midi_n >= 43 and (n % 4 == 0):
                x = self._midi
                d = x[8:-8] - 0.5 * (x[:-16] + x[16:])
                self._vib = float(np.std(d))
        elif not var:
            self._midi_n = 0
            self._vib = 0.0
        else:
            self._midi_n = 0
        parlak_n = self._z_parlak.adim(parlak) if (var and parlak >= 0.0) else 0.5
        vib_n = _kisi((self._vib - 0.15) / 0.6, 0.0, 1.0) if var else 0.0
        if var:
            ham = 0.45 * guc + 0.20 * perde_n + 0.20 * parlak_n + 0.15 * vib_n
            if not self._yog_var:
                # vokal yeni basladi: gecmis yogunlukla hizli birles
                self._yog = ham if self._yog == 0.0 else self._yog
            self._yog += (ham - self._yog) * (1.0 - math.exp(-dt / p["yog_tau_sn"]))
        self._yog_var = var
        i = self._yh % self._yog_hist.size
        self._yog_hist[i] = self._yog
        self._vok_hist[i] = 1.0 if var else 0.0
        self._yh += 1
        duygu_olay = ""
        nh = self._yog_hist.size
        if (p["duygu_acik"] and self._yh >= nh and not isinma and not self._kes and t - self._son_donum > p["donum_ara_sn"]):
            ns = int(round(DONUM_SONRA_SN / dt))
            no = int(round(DONUM_ONCE_SN / dt))
            ids = (np.arange(self._yh - ns - no - 1, self._yh)) % nh
            seg = self._yog_hist[ids]
            vs = self._vok_hist[ids]
            onceki = seg[:no]
            sonraki = seg[-ns:]
            if vs[-ns:].mean() >= 0.7 and vs[:no].mean() >= 0.4:
                # yalniz vokalli bloklar sayilir; once-sonra farki
                d = float(sonraki.mean() - onceki[vs[:no] > 0.5].mean())
                if d >= p["donum_esik"]:
                    duygu_olay = "duygu_yukari"
                elif d <= -p["donum_esik"]:
                    duygu_olay = "duygu_asagi"
        if duygu_olay:
            self._son_donum = t
            if duygu_olay == "duygu_yukari":
                self._renk_degis(t, zorla=True)
                self._boost = 1.0
                self._boost_tau = p["yukari_sonum_sn"]
                self._dim_ev = -1e9
            else:
                self._dim_ev = t
                self._boost = 0.0
                self._renk_degis(t, zorla=True)
            if not olay:
                olay = duygu_olay

        # ---- ton (valence): chroma yumusatmasi, yavas ----
        if not sessiz:
            cs = float(oz.chroma.sum())
            if cs > 1e-9:
                self._chroma += (np.asarray(oz.chroma) / cs - self._chroma) * (1.0 - math.exp(-dt / 8.0))
            if n % 8 == 0 and t > 3.0:
                self._ton_hedef = self._anahtar_ton(oz, rd, sessiz)
            self._ton += (self._ton_hedef - self._ton) * (1.0 - math.exp(-dt / TON_TAU_SN))

        # ---- taban parlaklik (Spektrum) ----
        gur = 0.0 if sessiz else _kisi(_sonlu(oz.enerji, 0.0), 0.0, 1.0)
        if oz.kick_var and not sessiz:
            self._pulse = 1.0
        else:
            self._pulse *= math.exp(-dt / p["vurus_sonum_sn"])
        base_hedef = 0.0 if sessiz else p["taban"] + (p["tavan"] - p["taban"]) * gur   # tam sessizlikte 0 (Spektrum da sessizde sonuyor)
        hedef = max(self._pulse * (p["nabiz_alt"] + (1.0 - p["nabiz_alt"]) * gur), base_hedef)
        self._level = hedef if hedef > self._level else self._level + (hedef - self._level) * p["dusus"]
        self._base_lvl = (base_hedef if base_hedef > self._base_lvl
                          else self._base_lvl + (base_hedef - self._base_lvl) * p["dusus"])
        base_lvl = min(self._base_lvl, self._level)
        pulse_part = max(0.0, self._level - base_lvl)

        # ---- asagi-duygu kisilmasi ve doygunluk ----
        g = t - self._dim_ev
        if g >= p["asagi_bekle_sn"] + p["asagi_geri_sn"]:
            dim_uyg = 1.0
        elif g < p["asagi_dim_sn"]:
            dim_uyg = 1.0 + (p["asagi_dim"] - 1.0) * g / p["asagi_dim_sn"]
        elif g < p["asagi_bekle_sn"]:
            dim_uyg = p["asagi_dim"]
        else:
            dim_uyg = p["asagi_dim"] + (1.0 - p["asagi_dim"]) * (g - p["asagi_bekle_sn"]) / p["asagi_geri_sn"]
        self._sat_dus = _kisi(1.0 - g / (p["asagi_bekle_sn"] + p["asagi_geri_sn"]), 0.0, 1.0)

        # ---- paylar (sqrt guc): bas, davul, melodi, vokal ----
        p_bas = p_bant[0] + p_bant[1]
        p_dav = max(_sonlu(oz.vurmali, 0.0) - p_bas, 0.0)
        hb = getattr(oz, "harita_bas", None)
        if hb is not None:   # kutuphane haritasi: alt bantta yalniz bas gitar varken bas payi tam, yalniz kick varken dusuk
            p_bas *= 0.3 + 0.7 * _kisi(float(hb), 0.0, 1.0)
        p_mel_t = max(_sonlu(oz.melodik, 0.0), 0.0)
        p_vok = p_mel_t * (0.2 + 0.6 * guc) if var else 0.0
        p_mel = max(p_mel_t - p_vok, 0.0)
        w_ham = np.sqrt(np.array([p_bas, p_dav, p_mel, p_vok]))
        if float(w_ham.sum()) > 1e-9:
            w_ham = w_ham / float(w_ham.sum())
            self._w += (w_ham - self._w) * (1.0 - math.exp(-dt / 0.25))
        w = self._w / max(float(self._w.sum()), 1e-9)

        # ---- vokal takip: vokal varken taban parlakligin w kadari vokal zarfi + perdeden ----
        bpm = _sonlu(getattr(rd, "bpm", 0.0), 0.0)
        vurus = _kisi(60.0 / bpm, 0.3, 1.0) if bpm > 0.0 else VT_VURUS_VARSAYILAN
        tau = max((p["vt_atak_vurus"] * vurus / 3.0) if var else (p["vt_sonum_vurus"] * vurus), 1e-3)
        hedef_g = 1.0 if var else 0.0
        self._vt_giris += (hedef_g - self._vt_giris) * (1.0 - math.exp(-dt / tau))
        pn_hedef = self._vt_perde
        if var and perde > 50.0:
            midi_p = 69.0 + 12.0 * math.log2(perde / 440.0)
            if self._vt_alt is None:
                self._vt_alt, self._vt_ust = midi_p - 3.0, midi_p + 3.0
            u = dt / p["vt_aralik_tau"]
            self._vt_alt = midi_p if midi_p < self._vt_alt else self._vt_alt + (midi_p - self._vt_alt) * u
            self._vt_ust = midi_p if midi_p > self._vt_ust else self._vt_ust + (midi_p - self._vt_ust) * u
            aralik = max(self._vt_ust - self._vt_alt, 5.0)
            pn_hedef = _kisi((midi_p - self._vt_alt) / aralik, 0.0, 1.0)
        elif not var:
            pn_hedef = 0.0
        self._vt_perde += (pn_hedef - self._vt_perde) * (1.0 - math.exp(-dt / p["vt_perde_tau"]))
        vokal_v = _kisi(p["vt_taban"] + p["vt_guc"] * guc + p["vt_perde"] * self._vt_perde, 0.0, 1.0)
        wv = min(p["vt_ust"], p["vt_w"] * _kisi(_sonlu(p["vokal_agirlik"], 1.0), 0.0, 2.0)) * self._vt_giris
        if sessiz:
            wv = 0.0

        # ---- V = toplam ----
        mult = self._gate * dim_uyg * dip
        sp = 1.0 - wv                                  # Spektrum tabaninin kalan payi
        ts = ((base_lvl + pulse_part) * sp + wv * (vokal_v + p["vt_nabiz_pay"] * pulse_part)) * mult   # taban toplam (olaysiz)
        v_bas, v_dav, v_mel, v_vok = (base_lvl * mult * sp * w[0],
                                      base_lvl * mult * sp * w[1] + pulse_part * mult * (sp + wv * p["vt_nabiz_pay"]),
                                      base_lvl * mult * sp * w[2],
                                      base_lvl * mult * sp * w[3] + wv * vokal_v * mult)
        self._boost *= math.exp(-dt / self._boost_tau)
        v_olay = self._boost * max(0.0, 1.0 - ts) if self._gate > 0.0 else 0.0
        v = _kisi(v_bas + v_dav + v_mel + v_vok + v_olay, 0.0, 1.0)

        # ---- renk ----
        bt = bas_s + tiz_s
        if bt > 1e-9 and not sessiz:
            self._oran += (tiz_s / bt - self._oran) * (1.0 - math.exp(-dt / p["oran_tau_sn"]))
        h_bt = (15.0 + 200.0 * self._oran) % 360.0
        h_dy = (HUE_SOGUK + (HUE_SICAK - HUE_SOGUK) * self._ton) % 360.0
        # Renk serbest (Arda, 2026-11-18: "renk kisitlamasi olmasin"): bas-tiz ve duygu rengi
        # belirlemez; renk tum cemberi dolasir, yalniz olay anlarinda degisir (_renk_degis).
        if olay == "donus":
            self._renk_degis(t)
        palet = self._palet()
        imza = tuple(round(x, 1) for x in palet)
        if imza != self._palet_imza:      # palet acildi/degisti: hemen paletten bir renge gec
            self._palet_imza = imza
            if palet:
                self._palet_i = -1
                self._renk_degis(t, zorla=True)
        if p["renk_serbest"] or palet:
            h_taban = self._renk
            h_bt = h_dy = self._renk
        else:
            h_taban = h_bt      # serbest kapali: Spektrum gibi bas-tiz oranindan
        if self._h_yumusak is None:
            self._h_yumusak = h_taban
        else:
            self._h_yumusak = (self._h_yumusak + _acisal(h_taban - self._h_yumusak)
                               * (1.0 - math.exp(-dt / p["renk_tau_sn"]))) % 360.0
        # ofset yavasca sifira iner
        self._ofset *= math.exp(-dt / OFSET_TAU_SN)
        if oz.kick_var and not sessiz:
            self._vurus_kayma = max(0.0, min(60.0, float(p["renk_kayma"])))
        else:
            self._vurus_kayma *= math.exp(-dt / p["renk_kayma_sn"])
        h = (self._h_yumusak + self._ofset + self._vurus_kayma) % 360.0
        # doygunluk: varsayilan 0,85 -> 0,80 + 0,15*yogunluk + 0,05; alt sinir en fazla 0,75 (beyaz yok: asla < 0,6)
        s = _kisi(0.80 + 0.15 * self._yog + round(p["doygunluk"] - 0.80, 9) - 0.10 * self._sat_dus,
                  min(0.75, p["doygunluk"]), 1.0)

        # ---- guvenlik: saniyede <= 3 sert gecis ----
        v, h = self._guvenlik(t, v, h)

        # ---- parlaklik esigi alti sonrasi zarla yeni renk (aralik beklenmez; renk bir sonraki karede oturur) ----
        if p["esik_renk_olasilik"] > 0 and not isinma:
            if v < p["esik_renk_v"]:
                if self._esik_alt_t is None:
                    self._esik_alt_t = t
            elif v > p["esik_renk_v"] + ESIK_RENK_TAMPON:
                if (self._esik_alt_t is not None and t - self._esik_alt_t >= p["esik_renk_sure_sn"]
                        and random.random() * 100.0 < p["esik_renk_olasilik"]):
                    self._renk_degis(t, zorla=True)
                self._esik_alt_t = None
        else:
            self._esik_alt_t = None

        # ---- gorsel veri ----
        self._viz(t, oz, olay, v, h, s, (v_bas, v_dav, v_mel, v_vok, v_olay), ts, guc, var,
                  h_bt, h_dy, p_bas, p_mel_t)
        if olay:
            self.olay_sayac[olay] = self.olay_sayac.get(olay, 0) + 1
        return IsikKare(h=float(h), s=float(s), v=float(v), olay=olay)

    # ------------------------------------------------------------------ guvenlik
    def _guvenlik(self, t, v, h):
        vg, hg = self._v_gecmis, self._h_gecmis
        if len(vg) >= SERT_PENCERE:
            v0, h0 = vg[-SERT_PENCERE], hg[-SERT_PENCERE]
            dv = v - v0
            dh = _acisal(h - h0)
            sert = abs(dv) > SERT_V or abs(dh) > SERT_H
            if sert:
                self._sert_zamanlar = [z for z in self._sert_zamanlar if t - z < SERT_SURE_SN]
                yeni_donem = not vg or (len(self._sert_zamanlar) == 0
                                        or t - self._sert_zamanlar[-1] > SERT_PENCERE * HOP_SN)
                if len(self._sert_zamanlar) >= SERT_SAYI:
                    # sinir doldu: gecisi yumusat (pencere basina en fazla SERT_V / SERT_H)
                    if abs(dv) > SERT_V:
                        v = v0 + math.copysign(SERT_V * 0.98, dv)
                    if abs(dh) > SERT_H:
                        h = (h0 + math.copysign(SERT_H * 0.98, dh)) % 360.0
                elif yeni_donem:
                    self._sert_zamanlar.append(t)
        vg.append(v)
        hg.append(h)
        if len(vg) > SERT_PENCERE + 1:
            del vg[0]
            del hg[0]
        return v, h

    # ------------------------------------------------------------------ gorsellestirme
    def _viz(self, t, oz, olay, v, h, s, pay, ts, guc, var, h_bt, h_dy, p_bas, p_mel):
        dt = HOP_SN
        for ad, bayrak in (("kick", oz.kick_var), ("snare", oz.snare_var), ("hat", oz.hat_var)):
            if bayrak:
                self._tut[ad] = t + 0.1
        if olay:
            self._olay_etiket = olay
            self._olay_bit = t + 1.2
        elif t > self._olay_bit:
            self._olay_etiket = ""
        self._bas_n = self._ar_bas.adim(10.0 * math.log10(p_bas + 1e-12))
        self._mel_n = self._ar_mel.adim(10.0 * math.log10(max(p_mel, 0.0) + 1e-12))
        v_bas, v_dav, v_mel, v_vok, v_olay = pay
        top = v_bas + v_dav + v_mel + v_vok + v_olay
        if top > 1e-6:
            yz = [100.0 * p / top for p in (v_vok, v_bas, v_dav, v_mel, v_olay)]
        else:
            yz = [0.0, 0.0, 0.0, 0.0, 100.0 if self._gate <= 0.0 else 0.0]
        self.son_viz = {
            "vokal": round(float(guc if var else 0.0), 3),
            "vokal_var": bool(var),
            "davul": {"kick": bool(t < self._tut["kick"]), "snare": bool(t < self._tut["snare"]),
                      "hat": bool(t < self._tut["hat"])},
            "bas": round(float(0.0 if oz.sessiz else self._bas_n), 3),
            "melodi": round(float(0.0 if oz.sessiz else self._mel_n), 3),
            "isik": {"h": round(float(h), 1), "s": round(float(s), 3), "v": round(float(v), 3)},
            "katki": {"vokal": round(float(yz[0]), 1), "bas": round(float(yz[1]), 1), "davul": round(float(yz[2]), 1),
                      "melodi": round(float(yz[3]), 1), "olay": round(float(yz[4]), 1)},
            "renk_katki": {"bas_tiz": round(float(h_bt), 1), "duygu": round(float(h_dy), 1)},
            "olay": self._olay_etiket,
            "duygu": {"yogunluk": round(float(self._yog), 3), "ton": round(float(self._ton), 3)},
        }
