"""Isik motoru: parlaklik V, ton H, doygunluk S ve olay etiketi.

Her blokta bir kare uretir. Girdi: Ozellik (oz), RitimDurum (rd), BolumDurum (bd).
Ozellik/RitimDurum/BolumDurum bu dosyada import edilmez; alanlara ad uzerinden erisilir.
Yalniz numpy ve standart kutuphane.
"""
import math
from collections import deque
from dataclasses import dataclass

import numpy as np

MODLAR = ("parti", "konser", "chill")

# Mod parametreleri (ALGORITMA.md 5. bolum). Sayisal alanlar 0.5 sn'de karisir,
# ton_olcu, flas ve strobo mod degisiminde hemen uygulanir.
MOD_AYAR = {
    "parti": {"c": 0.25, "kontrast": 1.0, "taban_ek": 0.0, "bd_carpan": 1.0,
              "ton_olcu": 1, "ton_gecis": 1.0, "yumusatma": 1.0,
              "flas": True, "strobo": True},
    "konser": {"c": 0.35, "kontrast": 1.2, "taban_ek": 0.0, "bd_carpan": 0.6,
               "ton_olcu": 2, "ton_gecis": 1.0, "yumusatma": 0.6,
               "flas": True, "strobo": False},
    "chill": {"c": 0.6, "kontrast": 0.5, "taban_ek": 0.1, "bd_carpan": 1.0,
              "ton_olcu": 4, "ton_gecis": 2.0, "yumusatma": 0.25,
              "flas": False, "strobo": False},
}
SAYISAL = ("c", "kontrast", "taban_ek", "bd_carpan", "ton_gecis", "yumusatma")

# Vokal kurallari (vokal=None ise hic devreye girmez). giris: vokal varken taban eki (1 vurusta yukselir),
# guc/dog: vokal_guc ile taban ve doygunluk eki, tut: tutulan notada nabiz sonumu carpani,
# nakarat: vokal + yuksek enerji + bas, ara: vokal >= 1 olcu susunca taban dususu, ton: perdeye gore ton kaymasi (derece).
VOKAL_AYAR = {
    "parti":  {"giris": 0.20, "guc": 0.10, "dog": 0.08, "tut": 1.6, "nakarat": 0.12, "ara": -0.05, "ton": 20.0},
    "konser": {"giris": 0.25, "guc": 0.14, "dog": 0.12, "tut": 2.0, "nakarat": 0.18, "ara": -0.08, "ton": 20.0},
    "chill":  {"giris": 0.15, "guc": 0.06, "dog": 0.05, "tut": 1.4, "nakarat": 0.08, "ara": -0.04, "ton": 12.0},
}
# Vokal takip (sef, 2026-11-18; Arda: "vokal daha one ciksin, parlaklik vokalin tonuna gore"):
# vokal varken V'nin w kadari dogrudan vokal zarfi + perde yuksekliginden gelir; davul nabzi
# bu kisimda nabiz_pay kadar korunur.
VOKAL_TAKIP = {
    # 2026-11-18 hassasiyet: taban dusuruldu, guc payi artirildi (aralik ~0,15-1,0)
    "parti":  {"w": 0.70, "taban": 0.15, "guc": 0.75, "perde": 0.15, "nabiz_pay": 0.30},
    "konser": {"w": 0.80, "taban": 0.10, "guc": 0.80, "perde": 0.20, "nabiz_pay": 0.20},
    "chill":  {"w": 0.60, "taban": 0.20, "guc": 0.60, "perde": 0.12, "nabiz_pay": 0.15},
}
VOKAL_PERDE_TAU = 0.15           # perde yuksekligi yumusatma
VOKAL_PERDE_ARALIK_TAU = 30.0    # sarkinin vokal perde araligi (alt/ust) bu surede unutulur
VOKAL_ATAK_VURUS = 1.0          # taban eki bir vurusta yukselir (tau = bu / 3)
VOKAL_SONUM_VURUS = 2.0          # ... ve iki vurusta soner
VOKAL_NAKARAT_E = 0.65           # nakarat sayilmasi icin yumusak enerji alt siniri
VOKAL_ARA_OLCU = 4               # vokalsiz ara: en az 1 olcu (4 vurus)
VOKAL_ARA_ON_SN = 5.0            # ara kurali icin sarkida en az bu kadar vokal duyulmus olmali
VOKAL_TON_TAU = 1.0
VOKAL_TABAN_UST = 0.95
VOKAL_ARA_NABIZ = 1.15           # ara'da davul nabzi bu kadar one cikar
VOKAL_TUT_TABAN = 0.03

# Duruma gore nabiz genligi (kick ve altbolum vurusu)
NABIZ_GENLIK = {"AMBIYANS": 0.15, "GROOVE": 0.5, "BUILD": 0.3, "DROP": 0.55, "BREAKDOWN": 0.15}

KONTRAST_CARPAN_MIN = 0.3
KONTRAST_CARPAN_MAX = 2.0
MOD_BLEND_TAU = 0.5
SNARE_AKTIF = False  # sef: snare algilama guvenilmez (A raporu P=0,20)
SNARE_SN = 0.06
SNARE_DOYGUNLUK = 0.4
SNARE_V = 0.2
FLAS_V = 1.0
ANLIK_KISA_SN = 0.20     # anlik patlama: kisa ortalama (tek kick tepesini yutar)
ANLIK_UZUN_VURUS = 8.0   # uzun ortalama = 2 olcu
ANLIK_GUR_DB = 3.0       # gurluk sicramasi
ANLIK_BAS_DB = 10.0       # bas sicramasi
ANLIK_ARA_OLCU = 4.0     # iki patlama arasi en az
PARILTI_GENLIK = 0.05
PARILTI_HZ = 4.0
PARILTI_ESIK = 4.0               # hat olayi/sn: bu uzerinde parilti baslar
PARILTI_TAM = 8.0               # bu hizda parilti tam genlikte
BUILD_STROBO_HZ = 8.0            # parti build strobosu ust siniri
DV_SINIR = 0.6                   # tam aralik sicrama esigi
DV_KIRP = 0.55                   # sinir asildiginda sicrama bu degere kirpilir
TAM_ARALIK_BUTCE = 6             # sn basina en fazla tam aralik gecis (3 cevrim)
TAM_ARALIK_BUTCE_STROBO = 16     # parti build strobosu (8 Hz = 16 gecis/sn)
ENERJI_TAU = 0.5
HAT_TAU = 1.0
DROP_OFS = 150.0                 # DROP aninda ton sicramasi (derece)
DROP_OFS_TAU = 0.5
BREAKDOWN_TON = 250.0            # BREAKDOWN'da mavi-mor cekim merkezi
BREAKDOWN_CEKIM = 0.5
BUILD_TON = 20.0                 # BUILD'de sicak cekim merkezi
TABAN_YAKIN = 60.0               # taban_ton etrafinda dolasma yarim acisi
VARSAYILAN_HOP_SN = 512 / 44100
VARSAYILAN_VURUS = 0.5

# Ton: chroma notalari beşliler çemberinde (C=0, G=1, D=2 ...). Aci = 30 * (7n mod 12).
_TON_RAD = np.array([math.radians(((7 * n) % 12) * 30.0) for n in range(12)])


@dataclass
class IsikKare:
    h: float   # 0-360
    s: float   # 0-1
    v: float   # 0-1
    olay: str  # "", "vurus", "snare", "drop", "flas"


def _sonlu(x, varsayilan=0.0):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return varsayilan
    return v if math.isfinite(v) else varsayilan


def _kisi(x, alt, ust):
    return alt if x < alt else ust if x > ust else x


def _tam(x, varsayilan=0):
    try:
        return int(x)
    except (TypeError, ValueError, OverflowError):
        return varsayilan


def _sar(d):
    # Dairesel fark: (-180, 180] araliginda.
    return (d + 180.0) % 360.0 - 180.0


def _harmonik_aci(chroma):
    # Chroma'nin beşliler çemberindeki agirlikli dairesel ortalamasi (derece).
    # Belirsiz (duz ya da gecersiz) chroma icin None.
    try:
        c = np.asarray(chroma, dtype=float)
    except (TypeError, ValueError):
        return None
    if c.shape != (12,) or not np.all(np.isfinite(c)):
        return None
    c = np.clip(c, 0.0, None)
    toplam = float(c.sum())
    if toplam <= 1e-9:
        return None
    w = c / toplam
    x = float(np.sum(w * np.cos(_TON_RAD)))
    y = float(np.sum(w * np.sin(_TON_RAD)))
    if math.hypot(x, y) < 0.05:
        return None
    return math.degrees(math.atan2(y, x)) % 360.0


def _smooth(u):
    return u * u * (3.0 - 2.0 * u)


class IsikMotoru:
    def __init__(self, mod="parti", yogunluk=1.0):
        if mod not in MODLAR:
            raise ValueError("bilinmeyen mod: %r" % (mod,))
        self._mod = mod
        self._yogunluk = _kisi(_sonlu(yogunluk, 1.0), KONTRAST_CARPAN_MIN, KONTRAST_CARPAN_MAX)
        # Panel entegrasyonu (2026-11-18): gecikme_sn = tahminli vurusta nabzi bu kadar erken tetikle;
        # vokal_agirlik = vokal takip payi (VOKAL_TAKIP w) carpani. Varsayilan 0 / 1: H ile ayni cikti.
        self.gecikme_sn = 0.0
        self.vokal_agirlik = 1.0
        self._p = {k: MOD_AYAR[mod][k] for k in SAYISAL}
        self._hedef = dict(self._p)
        self._ayarla_ayrik(mod)
        self.sifirla()

    def _ayarla_ayrik(self, mod):
        ayar = MOD_AYAR[mod]
        self._ton_olcu = ayar["ton_olcu"]
        self._flas = ayar["flas"]
        self._strobo = ayar["strobo"]

    def ayarla(self, mod=None, yogunluk=None):
        if mod is not None:
            if mod not in MODLAR:
                raise ValueError("bilinmeyen mod: %r" % (mod,))
            self._mod = mod
            self._hedef = {k: MOD_AYAR[mod][k] for k in SAYISAL}
            self._ayarla_ayrik(mod)
        if yogunluk is not None:
            y = _sonlu(yogunluk, 1.0)
            self._yogunluk = _kisi(y, KONTRAST_CARPAN_MIN, KONTRAST_CARPAN_MAX)

    def sifirla(self):
        self._p = dict(self._hedef)
        self._t_son = None
        self._e_yum = 0.0
        self._env = 0.0
        self._k_prev = None
        self._gec_vurus_t = -math.inf
        self._snare_kalan = 0.0
        self._hat_hiz = 0.0
        self._ofs = 0.0
        self._flas_bit = -math.inf
        self._son_patlama = -math.inf
        self._uz_gur = self._uz_bas = self._kisa_gur = self._kisa_bas = None
        self._v_prev = 0.0
        self._gecisler = deque()
        self._strobo_zaman = deque()
        self._ton_bas = None
        self._ton_hedef = None
        self._ton_t0 = 0.0
        self._ton_dur = VARSAYILAN_VURUS
        self._vk_giris = 0.0
        self._vk_nakarat = 0.0
        self._vk_ara = 0.0
        self._vk_guc = 0.0
        self._vk_ton = 0.0
        self._vk_toplam = 0.0
        self._vk_tut = 0.0
        self._vk_perde = 0.0
        self._vk_midi_alt = None
        self._vk_midi_ust = None

    def kare(self, oz, rd, bd, taban_ton=None, vokal=None):
        t = _sonlu(oz.t, 0.0)
        if self._t_son is None:
            dt = VARSAYILAN_HOP_SN
        else:
            dt = _kisi(t - self._t_son, 0.0, 0.5)
        self._t_son = t

        alfa = 1.0 - math.exp(-dt / MOD_BLEND_TAU)
        for k in SAYISAL:
            self._p[k] += alfa * (self._hedef[k] - self._p[k])

        durum = bd.ad
        ilerleme = _kisi(_sonlu(bd.ilerleme, 0.0), 0.0, 1.0)
        e_ham = _kisi(_sonlu(oz.enerji, 0.0), 0.0, 1.0)
        self._e_yum += (1.0 - math.exp(-dt / ENERJI_TAU)) * (e_ham - self._e_yum)
        e = self._e_yum
        va = _sonlu(rd.vurus_araligi, VARSAYILAN_VURUS)
        if va <= 0.0:
            va = VARSAYILAN_VURUS

        # Vurus tetigi: parti build'de faz kesisiminden altbolum, digerlerinde olay.
        sub = self._altbolum(durum, ilerleme, va)
        if sub > 1:
            faz = _kisi(_sonlu(rd.faz, 0.0), 0.0, 0.999999)
            k = int(faz * sub) % sub
            tetik = self._k_prev != k
            self._k_prev = k
            # Kesin ust sinir: her 1 sn penceresinde en fazla STROBO_HZ tetik
            while self._strobo_zaman and t - self._strobo_zaman[0] >= 1.0:
                self._strobo_zaman.popleft()
            if tetik:
                if len(self._strobo_zaman) >= BUILD_STROBO_HZ:
                    tetik = False
                else:
                    self._strobo_zaman.append(t)
        else:
            self._k_prev = None
            gec = _kisi(_sonlu(self.gecikme_sn, 0.0), 0.0, 1.0)
            nv = _sonlu(rd.sonraki_vurus_t, math.nan)
            if rd.tahminli and gec > 0.0 and math.isfinite(nv):
                # Gecikme telafisi: sonraki tahmini vurusun gec sn oncesinde, vurus basina bir kez.
                tetik = False
                if t >= nv - gec and nv > self._gec_vurus_t + 0.5 * va:
                    tetik = True
                    self._gec_vurus_t = nv
            else:
                tetik = bool(rd.vurus) if rd.tahminli else bool(oz.kick_var)

        # Kick nabzi: aninda yukselir, ustel sonum.
        vk = self._vokal_adim(vokal, oz, durum, e, dt, va, t) if vokal is not None else None
        tau = self._p["c"] * va
        if vk is not None:
            tau *= 1.0 + (vk["tut"] - 1.0) * self._vk_tut
        if tau > 0.0:
            self._env *= math.exp(-dt / tau)
        else:
            self._env = 0.0
        if tetik:
            self._env = 1.0
        nabiz = NABIZ_GENLIK.get(durum, 0.15) * self._env
        if vk is not None:
            nabiz *= 1.0 + (VOKAL_ARA_NABIZ - 1.0) * self._vk_ara

        # Snare: 60 ms, GROOVE ve DROP'ta.
        snare_tetik = SNARE_AKTIF and bool(oz.snare_var) and durum in ("GROOVE", "DROP")
        if snare_tetik:
            self._snare_kalan = SNARE_SN
        snare_on = self._snare_kalan > 0.0
        self._snare_kalan = max(0.0, self._snare_kalan - dt)

        # Parilti: hi-hat yogunlugu.
        self._hat_hiz *= math.exp(-dt / HAT_TAU)
        if oz.hat_var:
            self._hat_hiz += 1.0 / HAT_TAU
        parilti = 0.0
        if self._hat_hiz > PARILTI_ESIK:
            g = _kisi((self._hat_hiz - PARILTI_ESIK) / (PARILTI_TAM - PARILTI_ESIK), 0.0, 1.0)
            parilti = PARILTI_GENLIK * g * math.sin(2.0 * math.pi * PARILTI_HZ * t)

        # Taban ve parlaklik.
        taban = self._taban(durum, e, ilerleme)
        if vk is not None:
            taban = _kisi(taban + vk["taban_ek"], 0.0, max(taban, VOKAL_TABAN_UST))
        k_kontrast = self._p["kontrast"] * self._yogunluk
        v_hedef = taban + k_kontrast * (nabiz + (SNARE_V if snare_on else 0.0) + parilti)
        v_hedef = _kisi(v_hedef, 0.0, 1.0)
        if vk is not None and vk["takip"] > 0.0:
            # Vokal takip: parlakligin w kadari vokal zarfi + perdeden; davul nabzi kismen korunur.
            w = vk["takip"]
            vokal_hedef = _kisi(vk["vokal_v"] + vk["nabiz_pay"] * k_kontrast * nabiz, 0.0, 1.0)
            v_hedef = (1.0 - w) * v_hedef + w * vokal_hedef
        a = self._p["yumusatma"]
        v = self._v_prev + a * (v_hedef - self._v_prev)

        # Anlik patlama (sef, 2026-11-17): bolum karari ~yarim olcu gec geliyor; ani
        # gurluk + bas sicramasinda flasi hemen ver, bolum etiketi sonra yetisir.
        bas_db = 10.0 * math.log10(10 ** (_sonlu(oz.bant_db[0], -90.0) / 10.0)
                                   + 10 ** (_sonlu(oz.bant_db[1], -90.0) / 10.0) + 1e-12)
        g_db = _sonlu(oz.gur_db, -90.0)
        if self._uz_gur is None:
            self._uz_gur, self._uz_bas, self._kisa_gur, self._kisa_bas = g_db, bas_db, g_db, bas_db
        ak = 1.0 - math.exp(-dt / ANLIK_KISA_SN)
        au = 1.0 - math.exp(-dt / max(0.5, ANLIK_UZUN_VURUS * va))
        self._kisa_gur += ak * (g_db - self._kisa_gur)
        self._kisa_bas += ak * (bas_db - self._kisa_bas)
        anlik = (not oz.sessiz and durum in ("BUILD", "BREAKDOWN", "GROOVE")
                 and _tam(bd.sure_olcu) >= 3
                 and t - self._son_patlama > ANLIK_ARA_OLCU * 4.0 * va
                 and self._kisa_gur - self._uz_gur >= ANLIK_GUR_DB
                 and self._kisa_bas - self._uz_bas >= ANLIK_BAS_DB)
        self._uz_gur += au * (g_db - self._uz_gur)
        self._uz_bas += au * (bas_db - self._uz_bas)

        # Drop flasi: 1 vurus, beyaz. Anlik patlamadan hemen sonra gelen bolum
        # karari ikinci kez flas yapmaz.
        drop_ani = anlik or (bool(bd.drop_ani) and t - self._son_patlama > 2.0 * 4.0 * va)
        if drop_ani:
            self._son_patlama = t
        if drop_ani and self._flas:
            self._flas_bit = t + va
        flas_on = self._flas and t < self._flas_bit
        if flas_on:
            v = FLAS_V

        v = self._sinir(v, t, durum, sub)

        # Ton (H) ve doygunluk (S).
        if self._ton_hedef is None or (rd.olcu_basi and _tam(rd.olcu_no) % self._ton_olcu == 0):
            self._ton_guncelle(oz, t, va)
        if drop_ani:
            self._ofs = DROP_OFS
        elif durum != "DROP":
            self._ofs *= math.exp(-dt / DROP_OFS_TAU)
        h = self._ton_hesapla(t, taban_ton, durum, ilerleme)
        if vk is not None:
            h = (h + vk["ton"]) % 360.0

        s = 0.7 if durum == "BREAKDOWN" else 0.9
        if vk is not None:
            s = min(1.0, s + vk["dog"])
        if snare_on:
            s = min(s, SNARE_DOYGUNLUK)
        if flas_on:
            s = 0.0

        if drop_ani:
            olay = "drop"
        elif flas_on:
            olay = "flas"
        elif tetik:
            olay = "vurus"
        elif snare_tetik:
            olay = "snare"
        else:
            olay = ""

        self._v_prev = v
        return IsikKare(h=h, s=_kisi(s, 0.0, 1.0), v=_kisi(v, 0.0, 1.0), olay=olay)

    def _vokal_adim(self, vokal, oz, durum, e, dt, va, t):
        """Vokal kurallari: durum (yumusatilmis) ekleri. Girdi alanlarina ad uzerinden erisilir."""
        a = VOKAL_AYAR[self._mod]
        var = bool(getattr(vokal, "vokal_var", False))
        guc = _kisi(_sonlu(getattr(vokal, "vokal_guc", 0.0), 0.0), 0.0, 1.0)
        tut = bool(getattr(vokal, "tutulan", False)) and var
        yok = _sonlu(getattr(vokal, "vokal_yok_sn", 0.0), 0.0)
        perde = _sonlu(getattr(vokal, "perde_hz", 0.0), 0.0)

        def yaklas(x, hedef, tau_yuk, tau_dus):
            tau = tau_yuk if hedef > x else tau_dus
            return x + (1.0 - math.exp(-dt / max(tau, 1e-3))) * (hedef - x)

        if var:
            self._vk_toplam += dt
        # giris: vokal varken taban eki; yukselis 1 vurus, sonum 2 vurus
        self._vk_giris = yaklas(self._vk_giris, 1.0 if var else 0.0, VOKAL_ATAK_VURUS * va / 3.0, VOKAL_SONUM_VURUS * va)
        self._vk_guc = yaklas(self._vk_guc, guc if var else 0.0, 0.15, 0.5)
        self._vk_tut = yaklas(self._vk_tut, 1.0 if tut else 0.0, 0.1, 0.3)
        nakarat = var and e >= VOKAL_NAKARAT_E and durum in ("GROOVE", "DROP")
        self._vk_nakarat = yaklas(self._vk_nakarat, 1.0 if nakarat else 0.0, 0.5, 1.0)
        ara = (not var) and yok >= VOKAL_ARA_OLCU * va and self._vk_toplam >= VOKAL_ARA_ON_SN and durum != "AMBIYANS"
        self._vk_ara = yaklas(self._vk_ara, 1.0 if ara else 0.0, 0.5, 0.3)
        # ton kaymasi: perde sinifina gore +-ton derece, yavas
        if var and perde > 0.0:
            hedef = a["ton"] * math.sin(2.0 * math.pi * math.log2(perde / 261.63))
        else:
            hedef = 0.0
        self._vk_ton = yaklas(self._vk_ton, hedef, VOKAL_TON_TAU, VOKAL_TON_TAU)
        taban_ek = (a["giris"] * self._vk_giris + a["guc"] * self._vk_guc + a["nakarat"] * self._vk_nakarat
                    + a["ara"] * self._vk_ara + VOKAL_TUT_TABAN * self._vk_tut)

        # Perde yuksekligi: sarkinin vokal perde araligina gore 0 (en pes) - 1 (en tiz).
        pn_hedef = self._vk_perde
        if var and perde > 0.0:
            midi = 69.0 + 12.0 * math.log2(perde / 440.0)
            if self._vk_midi_alt is None:
                self._vk_midi_alt, self._vk_midi_ust = midi - 3.0, midi + 3.0
            u = dt / VOKAL_PERDE_ARALIK_TAU
            self._vk_midi_alt = midi if midi < self._vk_midi_alt else self._vk_midi_alt + (midi - self._vk_midi_alt) * u
            self._vk_midi_ust = midi if midi > self._vk_midi_ust else self._vk_midi_ust + (midi - self._vk_midi_ust) * u
            aralik = max(self._vk_midi_ust - self._vk_midi_alt, 5.0)
            pn_hedef = _kisi((midi - self._vk_midi_alt) / aralik, 0.0, 1.0)
        elif not var:
            pn_hedef = 0.0
        self._vk_perde = yaklas(self._vk_perde, pn_hedef, VOKAL_PERDE_TAU, VOKAL_PERDE_TAU)

        tk = VOKAL_TAKIP[self._mod]
        vokal_v = _kisi(tk["taban"] + tk["guc"] * guc + tk["perde"] * self._vk_perde, 0.0, 1.0)
        return {"taban_ek": taban_ek, "dog": a["dog"] * self._vk_guc, "tut": a["tut"], "ton": self._vk_ton,
                "takip": min(tk["w"] * _kisi(_sonlu(self.vokal_agirlik, 1.0), 0.0, 2.0), VOKAL_TABAN_UST) * self._vk_giris, "vokal_v": vokal_v, "nabiz_pay": tk["nabiz_pay"]}

    def _altbolum(self, durum, ilerleme, va):
        # Yalniz parti BUILD'de: vurus -> 8'lik -> 16'lik, en cok 8 Hz.
        if not (self._strobo and durum == "BUILD"):
            return 1
        if ilerleme < 1.0 / 3.0:
            sub = 1
        elif ilerleme < 2.0 / 3.0:
            sub = 2
        else:
            sub = 4
        ust = max(1, int(BUILD_STROBO_HZ * va))
        return min(sub, ust)

    def _taban(self, durum, e, ilerleme):
        if durum == "AMBIYANS":
            v = 0.08 + 0.17 * e
        elif durum == "GROOVE":
            v = 0.2 + 0.3 * e
        elif durum == "BUILD":
            v = 0.25 + 0.35 * ilerleme
        elif durum == "DROP":
            v = 0.45 + 0.25 * e
        else:
            v = (0.1 + 0.15 * e) * self._p["bd_carpan"]
        return _kisi(v + self._p["taban_ek"], 0.0, 1.0)

    def _sinir(self, v, t, durum, sub):
        # Tam aralik (|dv| > 0.6) gecisler: 1 sn icinde butceyi asarsa kirpilir.
        while self._gecisler and t - self._gecisler[0] >= 1.0:
            self._gecisler.popleft()
        dv = v - self._v_prev
        if abs(dv) > DV_SINIR:
            butce = TAM_ARALIK_BUTCE_STROBO if (self._strobo and durum == "BUILD" and sub > 1) else TAM_ARALIK_BUTCE
            if len(self._gecisler) >= butce:
                v = self._v_prev + math.copysign(DV_KIRP, dv)
            else:
                self._gecisler.append(t)
        return v

    def _ton_guncelle(self, oz, t, va):
        h = _harmonik_aci(oz.chroma)
        if h is None:
            return
        if self._ton_hedef is None:
            self._ton_bas = h
            self._ton_hedef = h
            self._ton_t0 = t
            self._ton_dur = VARSAYILAN_VURUS
            return
        self._ton_bas = self._ton_akt(t)
        self._ton_hedef = h
        self._ton_t0 = t
        self._ton_dur = max(0.1, self._p["ton_gecis"] * va)

    def _ton_akt(self, t):
        # Ham (0-360) ton; gecis ease-in-out, kisa yoldan.
        if self._ton_hedef is None:
            return None
        u = _kisi((t - self._ton_t0) / self._ton_dur, 0.0, 1.0)
        return (self._ton_bas + _sar(self._ton_hedef - self._ton_bas) * _smooth(u)) % 360.0

    def _ton_hesapla(self, t, taban_ton, durum, ilerleme):
        ham = self._ton_akt(t)
        if taban_ton is not None and math.isfinite(float(taban_ton)):
            taban = float(taban_ton) % 360.0
            if ham is None:
                h = taban
            else:
                h = taban + TABAN_YAKIN * math.sin(math.radians(ham))
        else:
            h = 0.0 if ham is None else ham
        h += self._ofs
        if durum == "BREAKDOWN":
            h += _sar(BREAKDOWN_TON - h) * BREAKDOWN_CEKIM
        elif durum == "BUILD":
            h += _sar(BUILD_TON - h) * ilerleme
        return h % 360.0
