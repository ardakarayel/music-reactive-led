"""Bolum durum makinesi: AMBIYANS, GROOVE, BUILD, DROP, BREAKDOWN.

Her blokta olcumler guncellenir; durum karari yalnizca vuruslarda (rd.vurus) verilir.
Her gecis icin en az HISTERIZIS_OLCU olcu beklenir (rd.olcu_no farki).
Ozellik ve RitimDurum bu dosyada import edilmez; alanlara ad uzerinden erisilir.

Ayar (2026-11, gercek sentetik_yapi ile): build ve drop cikisi normalize enerji
(oz.enerji) yerine dB olcutleriyle (gur_db, bas dB) yapilir; enerji sarkinin tepe/dip
araligina gore normalize edildigi icin intro'da da ~0,95 gorunuyor, ayirt etmiyor.
"""
import math
from collections import deque
from dataclasses import dataclass

import numpy as np

DURUMLAR = ("AMBIYANS", "GROOVE", "BUILD", "DROP", "BREAKDOWN")

GUVEN_ESIK = 0.35                 # rd.guven alt siniri (tempo guvenilir)
ENERJI_DUSUK = 0.25               # AMBIYANS / GROOVE girisi icin normalize enerji
DROP_BAS_ORAN = 0.6               # bas orani alt siniri (BUILD ilerleme yolu)
DROP_BAS_SICRAMA_DB = 6.0         # DROP: bas dB, son olcu ortalamasi - onceki olcu ortalamasi
DROP_GUR_SICRAMA_DB = 2.0         # DROP: gur dB ayni fark (ayar2: gercek drop +2,8 dB)
BREAKDOWN_BAS_ORAN = 0.3          # BREAKDOWN icin bas orani ust siniri
BREAKDOWN_GUR_DUSUS_DB = 6.0      # BREAKDOWN icin gurluk, son 8 olcu ortalamasinin altinda
BREAKDOWN_GERI_BAS_ORAN = 0.6     # BREAKDOWN'dan GROOVE'a donus
DROP_MIN_OLCU = 8                 # DROP en az bu kadar olcu surer
DROP_DUSUS_DB = 6.0               # DROP biter: gurluk tepeden bu kadar dB duser
BUILD_MAX_OLCU = 12               # BUILD bu olcuden sonra gurluk artmasa da biter
BUILD_GUR_EGIM = 0.2              # dB / olcu (4 olcu regresyon)
BUILD_YOG_EGIM = 0.8              # vurus / vurus / olcu (4 olcu regresyon); sabit128 tepesi 0,68 (gurultu), 0,5 sahte BUILD uretti
BUILD_MERKEZ_EGIM = 0.005         # goreli (egim / ortalama) / olcu
BUILD_BEKLENEN_ARTIS = 0.3        # BUILD ilerlemesi icin beklenen enerji artisi (normalize)
BUILD_DROP_ILERLEME = 0.9         # BUILD sonu tepe kabul esigi
HISTERIZIS_OLCU = 2               # durum degisimi icin en az olcu
EGIM_PENCERE = 4                  # regresyon penceresi (olcu)
OLCU_GECMIS = 8
VURUS_GECMIS = 4
BAS_ORT_TAU = 60.0                # bas ortalamasi (sn, ustel)
BAS_KISA_TAU = 0.5                # anlik bas (sn, ustel)
ENERJI_TAU = 0.5                  # enerji yumusatma (sn)
GUR_TAU = 0.5                     # gurluk yumusatma (sn)
ORAN_UST = 5.0                    # bas orani ust siniri (sessiz girisle patlamasin)
VARSAYILAN_HOP_SN = 512 / 44100


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


def _egim(seri):
    # Son EGIM_PENCERE degerin dogrusal regresyon egimi (birim: deger/olcu).
    n = len(seri)
    if n < EGIM_PENCERE:
        return 0.0
    son = list(seri)[-EGIM_PENCERE:]
    xo = (EGIM_PENCERE - 1) / 2.0
    yo = sum(son) / EGIM_PENCERE
    pay = 0.0
    payda = 0.0
    for i, y in enumerate(son):
        dx = i - xo
        pay += dx * (y - yo)
        payda += dx * dx
    return pay / payda if payda > 0 else 0.0


def _bas_enerji(bant_db):
    # Sub (20-60) ve kick (60-150) bantlarinin toplam dogrusal enerjisi.
    try:
        b = np.asarray(bant_db, dtype=float)[:2]
    except (TypeError, ValueError):
        return 0.0
    if b.size < 2 or not np.all(np.isfinite(b)):
        return 0.0
    return float(np.sum(10.0 ** (np.clip(b, -120.0, 120.0) / 10.0)))


@dataclass
class BolumDurum:
    ad: str                  # DURUMLAR'dan
    ilerleme: float          # BUILD icinde 0-1 (yoksa 0)
    drop_ani: bool           # bu blokta DROP'a gecildi
    gecis: bool              # bu blokta durum degisti
    sure_olcu: int           # mevcut durumda gecen olcu


class Bolum:
    def __init__(self):
        self.sifirla()

    def sifirla(self):
        self.ad = "AMBIYANS"
        self._giris = None           # durumun basladigi rd.olcu_no
        self._giris_t = 0.0          # durumun basladigi zaman (sn)
        self._acc_t0 = 0.0           # acik olcunun basladigi zaman (sn)
        self._t_son = None
        self._bas_ort = None         # 60 sn ustel ortalama (dogrusal)
        self._bas_kisa = 0.0         # 0,5 sn ustel (dogrusal)
        self._e_yum = 0.0            # yumusatilmis normalize enerji (ilerleme icin)
        self._gur_yum = 0.0          # yumusatilmis gurluk (dB)
        self._pen = deque()          # (t, gur_db, bas dB): son 2 olcu
        self._olcu_e = deque(maxlen=OLCU_GECMIS)
        self._olcu_gur = deque(maxlen=OLCU_GECMIS)
        self._olcu_merkez = deque(maxlen=OLCU_GECMIS)
        self._olcu_yog = deque(maxlen=OLCU_GECMIS)
        self._olcu_acc = [0.0, 0.0, 0.0, 0.0]   # enerji, merkez, gur, n
        self._vurus_say = deque(maxlen=VURUS_GECMIS)
        self._vurus_acc = 0
        self._gur_tepe = 0.0
        self._va = 0.5
        self._build_bas = 0.0
        self._ilerleme = 0.0

    def guncelle(self, oz, rd):
        t = _sonlu(oz.t, 0.0)
        if self._t_son is None:
            dt = VARSAYILAN_HOP_SN
        else:
            dt = _kisi(t - self._t_son, 0.0, 0.5)
        self._t_son = t

        olcu = _tam(rd.olcu_no)
        va = _sonlu(rd.vurus_araligi, 0.5)
        if va <= 0.0:
            va = 0.5
        self._va = va
        if self._giris is None or olcu < self._giris:
            self._giris = olcu
            self._giris_t = t

        e = _kisi(_sonlu(oz.enerji, 0.0), 0.0, 1.0)
        gur = _sonlu(oz.gur_db, -120.0)
        bas = _bas_enerji(oz.bant_db)
        if self._bas_ort is None:
            self._bas_ort = bas
            self._bas_kisa = bas
            self._e_yum = e
            self._gur_yum = gur
        self._bas_ort += (1.0 - math.exp(-dt / BAS_ORT_TAU)) * (bas - self._bas_ort)
        self._bas_kisa += (1.0 - math.exp(-dt / BAS_KISA_TAU)) * (bas - self._bas_kisa)
        self._e_yum += (1.0 - math.exp(-dt / ENERJI_TAU)) * (e - self._e_yum)
        self._gur_yum += (1.0 - math.exp(-dt / GUR_TAU)) * (gur - self._gur_yum)
        oran = self._bas_oran()

        self._pen.append((t, gur, 10.0 * math.log10(self._bas_kisa + 1e-12)))
        while self._pen and t - self._pen[0][0] > 6.0 * self._va:
            self._pen.popleft()

        # Olcu siniri: once onceki olcuyu kapat, sonra bu blogu yeni olcuya say.
        # Olcu kapanisi: rd.olcu_basi, ya da tempo kilitsizken 4 vurus suresi (olcu_no donar).
        kilitsiz = _sonlu(rd.guven, 0.0) < GUVEN_ESIK
        if self._olcu_acc[3] > 0 and (rd.olcu_basi or (kilitsiz and t - self._acc_t0 >= 4.0 * va)):
            a = self._olcu_acc
            self._olcu_e.append(a[0] / a[3])
            self._olcu_merkez.append(a[1] / a[3])
            self._olcu_gur.append(a[2] / a[3])
            self._olcu_yog.append(self._yogunluk())
            self._olcu_acc = [0.0, 0.0, 0.0, 0.0]
            self._acc_t0 = t
        a = self._olcu_acc
        a[0] += e
        a[1] += _sonlu(oz.merkez_hz, 0.0)
        a[2] += gur
        a[3] += 1
        self._vurus_acc += int(bool(oz.kick_var)) + int(bool(oz.snare_var)) + int(bool(oz.hat_var))

        if self.ad == "DROP":
            self._gur_tepe = max(self._gur_tepe, self._gur_yum)

        gecis = False
        drop_ani = False
        if rd.vurus:
            self._vurus_say.append(self._vurus_acc)
            self._vurus_acc = 0
            d = self._olcu_gecen(olcu, t, va)
            yeni = self._karar(d, oran, _kisi(_sonlu(rd.guven, 0.0), 0.0, 1.0))
            if yeni is not None:
                drop_ani = yeni == "DROP"
                gecis = True
                self._durum_degistir(yeni, olcu, t)

        if self.ad == "BUILD":
            self._ilerleme = _kisi((self._e_yum - self._build_bas) / BUILD_BEKLENEN_ARTIS, 0.0, 1.0)
        else:
            self._ilerleme = 0.0

        return BolumDurum(ad=self.ad, ilerleme=self._ilerleme, drop_ani=drop_ani,
                          gecis=gecis, sure_olcu=self._olcu_gecen(olcu, t, va))

    def _olcu_gecen(self, olcu, t, va):
        # Durumda gecen olcu: rd.olcu_no farki, ama olcu_no donarsa (tempo yok, ornegin
        # BREAKDOWN) zamandan hesaplanir: (t - giris_zamani) / (4 * vurus_araligi).
        zaman = (t - self._giris_t) / (4.0 * va)
        return int(max(olcu - self._giris, math.floor(max(0.0, zaman))))

    def _durum_degistir(self, yeni, olcu, t):
        self.ad = yeni
        self._giris = olcu
        self._giris_t = t
        if yeni == "BUILD":
            self._build_bas = self._e_yum
        elif yeni == "DROP":
            self._gur_tepe = self._gur_yum

    def _bas_oran(self):
        return _kisi(self._bas_kisa / max(self._bas_ort, 1e-9), 0.0, ORAN_UST)

    def _yogunluk(self):
        if not self._vurus_say:
            return 0.0
        return sum(self._vurus_say) / len(self._vurus_say)

    def _drop_kosulu(self, oran):
        if self.ad == "BUILD" and self._ilerleme >= BUILD_DROP_ILERLEME and oran > DROP_BAS_ORAN:
            return True
        # Ani sicrama: son olcu ortalamasi, bir onceki olcu ortalamasina gore (bas ve gur birlikte).
        # Tek blok gur sicramalari (13,2 s'deki +6 dB) tek basina yetmez.
        va = self._va
        t_son = self._pen[-1][0] if self._pen else 0.0
        son = [p for p in self._pen if t_son - p[0] < 2.0 * va]
        onc = [p for p in self._pen if 2.0 * va <= t_son - p[0] < 6.0 * va]
        if len(son) < 2 or len(onc) < 2:
            return False
        bas_art = sum(p[2] for p in son) / len(son) - sum(p[2] for p in onc) / len(onc)
        gur_art = sum(p[1] for p in son) / len(son) - sum(p[1] for p in onc) / len(onc)
        return bas_art >= DROP_BAS_SICRAMA_DB and gur_art >= DROP_GUR_SICRAMA_DB

    def _seriler(self):
        # Kapanmis olcular + acik (kismi) olcu. Kismi olcu olmadan karar bir olcu geriden gelir.
        a = self._olcu_acc
        seri_e = list(self._olcu_e)
        seri_m = list(self._olcu_merkez)
        seri_g = list(self._olcu_gur)
        seri_y = list(self._olcu_yog)
        if a[3] > 0:
            seri_e.append(a[0] / a[3])
            seri_m.append(a[1] / a[3])
            seri_g.append(a[2] / a[3])
            seri_y.append(self._yogunluk())
        return seri_e, seri_m, seri_g, seri_y

    def _build_kosulu(self, seri_m, seri_g, seri_y):
        # Uc kosulun tamami: gurluk egimi (dB), yogunluk egimi, spektral merkez egimi.
        # Bas egimi kosul degil: intro->build gecisinde bas da yukseliyor (ayar notu, rapor-ayar.md).
        if len(seri_g) < EGIM_PENCERE:
            return False
        if _egim(seri_g) <= BUILD_GUR_EGIM or _egim(seri_y) <= BUILD_YOG_EGIM:
            return False
        son = seri_m[-EGIM_PENCERE:]
        ort = sum(son) / len(son)
        return ort > 0 and _egim(seri_m) / ort > BUILD_MERKEZ_EGIM

    def _gur_ref(self):
        # Son 8 kapanmis olcunun gurluk ortalamasi (dB); en az 4 olcu yoksa None.
        if len(self._olcu_gur) < EGIM_PENCERE:
            return None
        return sum(self._olcu_gur) / len(self._olcu_gur)

    def _karar(self, d, oran, guven):
        s = self.ad
        e = self._e_yum

        if s == "DROP":
            if d < DROP_MIN_OLCU:
                return None
            if self._gur_yum <= self._gur_tepe - DROP_DUSUS_DB:
                # Gurluk dustu: dogrudan BREAKDOWN (ara GROOVE adimi yok); bas geri gelirse
                # BREAKDOWN -> GROOVE kurali isler
                return "BREAKDOWN"
            return None

        if d < HISTERIZIS_OLCU:
            return None
        if s != "AMBIYANS" and self._drop_kosulu(oran):
            return "DROP"
        if guven < GUVEN_ESIK and e < ENERJI_DUSUK:
            return None if s == "AMBIYANS" else "AMBIYANS"

        seri_e, seri_m, seri_g, seri_y = self._seriler()
        if s == "BUILD":
            if d >= BUILD_MAX_OLCU:
                return "GROOVE"
            return None
        if guven >= GUVEN_ESIK and s in ("GROOVE", "AMBIYANS", "BREAKDOWN"):
            if self._build_kosulu(seri_m, seri_g, seri_y):
                return "BUILD"
        if s == "BREAKDOWN":
            return "GROOVE" if (oran >= BREAKDOWN_GERI_BAS_ORAN and guven >= GUVEN_ESIK) else None
        if s == "GROOVE":
            ref = self._gur_ref()
            if ref is not None and oran < BREAKDOWN_BAS_ORAN and self._gur_yum <= ref - BREAKDOWN_GUR_DUSUS_DB:
                return "BREAKDOWN"
            return None
        if s == "AMBIYANS":
            return "GROOVE" if (guven >= GUVEN_ESIK and e >= ENERJI_DUSUK) else None
        return None
