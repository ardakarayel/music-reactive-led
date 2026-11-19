"""Muzik modu v2 analiz paketi: dinleme -> ritim -> bolum -> isik.

Gercek zamanli ve laboratuvarda ayni kod. Yalniz numpy.
"""
from .dinleme import Dinleyici, Ozellik
from .ritim import Ritim, RitimDurum
from .bolum import Bolum, BolumDurum, DURUMLAR
from .isik import IsikMotoru, IsikKare, MODLAR
from .vokal import Vokal, VokalDurum
from .dionysos import DionysosMotoru, spektrum_ozeti

RATE = 44100
HOP = 512
NFFT = 2048


class Boru:
    def __init__(self, mod="parti", hassasiyet=50.0, yogunluk=1.0):
        self.dinleyici = Dinleyici(rate=RATE, hop=HOP, nfft=NFFT, hassasiyet=hassasiyet)
        self.ritim = Ritim(hop_sn=HOP / float(RATE))
        self.bolum = Bolum()
        self.dionysos = (mod == "dionysos")
        # Dionysos: ayri isik motoru (bolum hesaplanir ama isiga girmez); diger modlar degismez
        self.isik = DionysosMotoru() if self.dionysos else IsikMotoru(mod=mod, yogunluk=yogunluk)
        self.vokal = Vokal(rate=RATE, hop=HOP, nfft=NFFT)
        self.son_vokal = VokalDurum()
        self.vokal_isik = True   # False: isik motoru vokali kullanmaz (eski davranis)
        self.taban_ton = None
        self.son_viz = None      # Dionysos: gorsellestirme verisi (JSON uyumlu sozluk); diger modlarda None
        # Kutuphane (Arda, 2026-11-19): calan sarkinin Roformer haritasi varsa vokal, kick/snare ve bas
        # canli tahmin yerine haritadan gelir. harita_fn() -> {"vokal","vokal_var","bas","kick","snare"} | None
        self.harita_fn = None
        self.harita_aktif = False
        self._h_var_onceki = False

    def ayarla(self, mod=None, hassasiyet=None, yogunluk=None, taban_ton="degismez", params=None):
        if hassasiyet is not None:
            self.dinleyici.ayarla(hassasiyet)
        if mod is not None or yogunluk is not None:
            self.isik.ayarla(mod=mod, yogunluk=yogunluk)
        if params is not None and self.dionysos:
            self.isik.ayarla(params=params)   # laboratuvar: Dionysos parametreleri (sifirlamadan)
        if taban_ton != "degismez":
            self.taban_ton = taban_ton

    def isle(self, blok):
        oz = self.dinleyici.isle(blok)
        rd = self.ritim.guncelle(oz)
        bd = self.bolum.guncelle(oz, rd)
        merkez = None
        b = getattr(blok, "ndim", 0)
        if b == 2 and blok.shape[1] == 2:
            merkez = self.vokal.stereo_merkez(blok)   # gercek zamanli stereo kancasi (laboratuvar mono)
        self.son_vokal = self.vokal.guncelle(oz, self.dinleyici.son_mel, merkez=merkez, tum=self.dinleyici.son_tum)
        self._harita_uygula(oz)
        if self.dionysos:
            ozet = spektrum_ozeti(self.dinleyici.son_tum, self.dinleyici.son_mel)
            kare = self.isik.kare(oz, rd, self.son_vokal, ozet=ozet)
            self.son_viz = self.isik.son_viz
            return oz, rd, bd, kare
        kare = self.isik.kare(oz, rd, bd, self.taban_ton, vokal=self.son_vokal if self.vokal_isik else None)
        return oz, rd, bd, kare

    def _harita_uygula(self, oz):
        h = None
        if self.harita_fn is not None:
            try:
                h = self.harita_fn()
            except Exception:
                h = None
        self.harita_aktif = h is not None
        if h is None:
            oz.harita_bas = None
            return
        v = self.son_vokal
        var = bool(h.get("vokal_var"))
        v.vokal_guc = float(h.get("vokal", 0.0))
        v.vokal = 1.0 if var else 0.0
        v.vokal_giris = var and not self._h_var_onceki
        v.vokal_cikis = self._h_var_onceki and not var
        v.vokal_var = var
        self._h_var_onceki = var
        oz.kick_var = bool(h.get("kick"))
        oz.snare_var = bool(h.get("snare"))
        oz.harita_bas = float(h.get("bas", 0.0))

    def sifirla(self):
        for p in (self.dinleyici, self.ritim, self.bolum, self.isik, self.vokal):
            p.sifirla()


__all__ = ["Boru", "Vokal", "VokalDurum", "Dinleyici", "Ozellik", "Ritim", "RitimDurum", "Bolum", "BolumDurum",
           "DURUMLAR", "DionysosMotoru", "IsikMotoru", "IsikKare", "MODLAR", "RATE", "HOP", "NFFT"]
