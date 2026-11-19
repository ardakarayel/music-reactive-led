"""Sarki kutuphanesi: Spotify'da dinlenen sarkiyi kaydeder, arka planda ayirma betigine verir,
sonuc haritasini saklar; sarki tekrar calinca harita senkronlu okunur (ornek()).

Thread'ler: yoklama (durum makinesi), kayit (sarki basina), kuyruk isci (tek). Hicbir hata
panel thread'ini dusurmez; durum/hata alanina yazilir.
"""
import bisect
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import wave
from datetime import datetime

try:
    import numpy as np
except Exception:   # numpy yoksa kayit calismaz, harita okuma calisir
    np = None

try:
    import medya as _medya_modul
except Exception:
    _medya_modul = None

RATE = 44100
KANAL = 2
SEEK_ESIK_SN = 2.5
MIN_YUZDE = 0.90
YENIDEN_EN_COK = 2
_TR = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
                     "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"})


def normalle(metin):
    """Kucuk harf, Turkce karakterler ASCII, alfasayisal disi -> '_', tekrarlar tek."""
    m = unicodedata.normalize("NFKD", (metin or "").translate(_TR).lower())
    m = "".join(c for c in m if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", m).strip("_")


def anahtar_uret(ad, sanatci, sure):
    s = normalle(sanatci)[:60] or "x"
    a = normalle(ad)[:60] or "x"
    return "%s__%s__%d" % (s, a, int(round(float(sure))))


def _simdi_iso():
    return datetime.now().isoformat(timespec="seconds")


def _spotify_mi(parca):
    if not parca:
        return False
    kimlik = (parca.get("uygulama") or parca.get("kaynak") or "").lower()
    return "spotify" in kimlik


class _Kayit:
    """Tek sarkinin kayit thread'i."""

    def __init__(self, anahtar, ad, sanatci, sure, yol, fabrika):
        self.anahtar, self.ad, self.sanatci, self.sure = anahtar, ad, sanatci, sure
        self.yol = yol
        self.fabrika = fabrika
        self.dur = threading.Event()
        self.duraklat = threading.Event()
        self.kare = 0
        self.durum = "basliyor"     # basliyor | calisiyor | olu | bitti
        self.hata = None
        self.thread = threading.Thread(target=self._calis, daemon=True)

    @property
    def yazilan_sn(self):
        return self.kare / float(RATE)

    def _calis(self):
        yakalayici = None
        w = None
        try:
            yakalayici = self.fabrika()
            if not yakalayici.ac():
                self.hata = "ses oturumu acilamadi: %s" % (getattr(yakalayici, "hata", None),)
                self.durum = "olu"
                return
            w = wave.open(self.yol, "wb")
            w.setnchannels(KANAL)
            w.setsampwidth(2)
            w.setframerate(RATE)
            self.durum = "calisiyor"
            while not self.dur.is_set():
                x = yakalayici.oku(512, 0.5)
                if x is None:
                    self.hata = "ses kesildi"
                    self.durum = "olu"
                    return
                if self.duraklat.is_set():
                    continue       # okunur ama yazilmaz (tampon bayatlamasin)
                a = np.asarray(x, dtype=np.float32)
                if a.ndim == 1:
                    a = np.repeat(a[:, None], KANAL, axis=1)
                pcm = (np.clip(a, -1.0, 1.0) * 32767.0).astype("<i2")
                w.writeframes(pcm.tobytes())
                self.kare += pcm.shape[0]
            self.durum = "bitti"
        except Exception as e:
            self.hata = "kayit hatasi: %s" % e
            self.durum = "olu"
        finally:
            try:
                if w is not None:
                    w.close()
            except Exception:
                pass
            try:
                if yakalayici is not None:
                    yakalayici.kapat()
            except Exception:
                pass


class Kutuphane:
    gecikme_sn = 0.08   # ses bize gecikmeyle ulasiyor: harita konumu = simdiki_konum - gecikme_sn

    def __init__(self, klasor, medya, ayir_python, ayir_betik, yakalayici_fabrika=None,
                 ogrenme=True, min_sure=30.0, maks_sure=900.0, bitis_marji=3.0,
                 baslangic_marji=3.0, yoklama_sn=0.5):
        self.klasor = os.path.abspath(klasor)
        self.gecici = os.path.join(self.klasor, "_gecici")
        self.modeller = os.path.join(self.klasor, "_modeller")
        self.medya = medya
        self.ayir_python = ayir_python
        self.ayir_betik = ayir_betik
        self._fabrika = yakalayici_fabrika
        self.min_sure, self.maks_sure = float(min_sure), float(maks_sure)
        self.bitis_marji, self.baslangic_marji = float(bitis_marji), float(baslangic_marji)
        self.yoklama_sn = float(yoklama_sn)
        self._konum_fn = getattr(medya, "simdiki_konum", None) or (
            _medya_modul.simdiki_konum if _medya_modul else (lambda p: None))
        self._kilit = threading.RLock()
        self._kayitlar = {}           # anahtar -> dict (index)
        self._ilerleme = {}           # anahtar -> 0-1 (isleniyor)
        self._dur = threading.Event()
        self._is_var = threading.Event()
        self._threadler = []
        self._rec = None
        self._rec_bekle_sn = 0.0      # ses acilamazsa yeniden deneme zamani (monotonic)
        self._surec = None
        self._parca_son = None
        self._calan = None            # anahtar (hazir ve eslesen)
        self._harita = None           # (anahtar, dict) bellekte
        self._vurus = None            # [anahtar, onceki_konum, kick_idx, snare_idx]
        self._ogrenme = bool(ogrenme)
        self._baslatildi = False
        os.makedirs(self.gecici, exist_ok=True)
        self._ayar_oku()
        self._index_oku()
        self._toparla()

    # ---------------------------------------------------------------- dosya / index
    def _index_yol(self):
        return os.path.join(self.klasor, "index.json")

    def _harita_yol(self, a):
        return os.path.join(self.klasor, a + ".json")

    def _wav_yol(self, a):
        return os.path.join(self.gecici, a + ".wav")

    def _yaz_json(self, yol, veri):
        tmp = yol + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(veri, f, ensure_ascii=False, indent=1)
        os.replace(tmp, yol)

    def _kaydet(self):
        with self._kilit:
            try:
                self._yaz_json(self._index_yol(), {"surum": 1, "kayitlar": self._kayitlar})
            except Exception:
                pass

    def _ayar_oku(self):
        try:
            with open(os.path.join(self.klasor, "ayar.json"), encoding="utf-8") as f:
                self._ogrenme = bool(json.load(f).get("ogrenme", self._ogrenme))
        except Exception:
            pass

    def _index_oku(self):
        try:
            with open(self._index_yol(), encoding="utf-8") as f:
                k = json.load(f).get("kayitlar", {})
            if isinstance(k, dict):
                self._kayitlar = {a: v for a, v in k.items() if isinstance(v, dict)}
        except Exception:
            self._kayitlar = {}

    def _sil_dosya(self, yol):
        try:
            os.remove(yol)
        except OSError:
            pass

    def _toparla(self):
        """Acilista index'i duzelt: kaydediliyor sil, sirada/isleniyor WAV varsa kuyruga al."""
        with self._kilit:
            for a, k in list(self._kayitlar.items()):
                d = k.get("durum")
                if d == "kaydediliyor":
                    self._sil_dosya(self._wav_yol(a))
                    del self._kayitlar[a]
                elif d in ("sirada", "isleniyor"):
                    if os.path.isfile(self._wav_yol(a)):
                        k["durum"] = "sirada"
                    else:
                        del self._kayitlar[a]
                elif d == "hazir":
                    if not os.path.isfile(self._harita_yol(a)):
                        del self._kayitlar[a]
            sirada = {a for a, k in self._kayitlar.items() if k.get("durum") == "sirada"}
            try:
                for ad in os.listdir(self.gecici):      # yetim gecici dosyalar
                    govde = ad.split(".")[0]
                    if ad.endswith((".wav", ".cikti.json", ".tmp")) and not (
                            ad.endswith(".wav") and govde in sirada):
                        self._sil_dosya(os.path.join(self.gecici, ad))
            except OSError:
                pass
            self._kaydet()

    # ---------------------------------------------------------------- eslesme
    def _bul(self, ad, sanatci, sure):
        """Ayni ad+sanatci (normalize) ve |sure farki| <= 2 sn -> anahtar, yoksa None."""
        na, ns = normalle(ad), normalle(sanatci)
        with self._kilit:
            for a, k in self._kayitlar.items():
                try:
                    if (normalle(k.get("ad")) == na and normalle(k.get("sanatci")) == ns
                            and abs(float(k.get("sure", 0)) - float(sure)) <= 2.0):
                        return a
                except Exception:
                    continue
        return None

    # ---------------------------------------------------------------- yasam
    def baslat(self):
        if self._baslatildi:
            return
        self._baslatildi = True
        self._dur.clear()
        for hedef in (self._yoklama_dongu, self._isci_dongu):
            t = threading.Thread(target=hedef, daemon=True)
            t.start()
            self._threadler.append(t)
        if any(k.get("durum") == "sirada" for k in self._kayitlar.values()):
            self._is_var.set()

    def durdur(self):
        self._dur.set()
        self._is_var.set()
        try:
            p = self._surec
            if p is not None and p.poll() is None:
                p.terminate()
        except Exception:
            pass
        for t in self._threadler:
            t.join(3.0)
        self._threadler = []
        self._baslatildi = False
        self._kayit_iptal()
        with self._kilit:
            self._kaydet()

    # ---------------------------------------------------------------- ayar
    @property
    def ogrenme(self):
        return self._ogrenme

    @ogrenme.setter
    def ogrenme(self, deger):
        self._ogrenme = bool(deger)
        try:
            self._yaz_json(os.path.join(self.klasor, "ayar.json"), {"ogrenme": self._ogrenme})
        except Exception:
            pass

    # ---------------------------------------------------------------- arayuz
    def liste(self):
        cikti = []
        with self._kilit:
            for a, k in self._kayitlar.items():
                d = k.get("durum")
                il = None
                if d == "kaydediliyor" and self._rec is not None and self._rec.anahtar == a:
                    il = min(1.0, self._rec.yazilan_sn / max(1.0, float(k.get("sure") or 1)))
                elif d == "isleniyor":
                    il = self._ilerleme.get(a, 0.0)
                cikti.append({"anahtar": a, "ad": k.get("ad"), "sanatci": k.get("sanatci"),
                              "sure": k.get("sure"), "eklenme": k.get("eklenme"), "durum": d,
                              "hata": k.get("hata"), "boyut_kb": k.get("boyut_kb", 0),
                              "ilerleme": il})
        cikti.sort(key=lambda x: x.get("eklenme") or "", reverse=True)
        return cikti

    def sil(self, anahtar):
        with self._kilit:
            if anahtar not in self._kayitlar:
                return False
            if self._rec is not None and self._rec.anahtar == anahtar:
                self._kayit_iptal()
            self._kayitlar.pop(anahtar, None)
            self._ilerleme.pop(anahtar, None)
            self._sil_dosya(self._harita_yol(anahtar))
            if self._calan == anahtar:
                self._calan, self._harita = None, None
            self._kaydet()
        self._sil_dosya(self._wav_yol(anahtar))     # isleniyorsa isci sonucu atar
        return True

    def durum(self):
        with self._kilit:
            kayit = None
            if self._rec is not None:
                k = self._kayitlar.get(self._rec.anahtar, {})
                kayit = {"ad": self._rec.ad, "sanatci": self._rec.sanatci,
                         "yuzde": round(min(100.0, 100.0 * self._rec.yazilan_sn
                                            / max(1.0, float(self._rec.sure))), 1)}
            kuyruk = sum(1 for k in self._kayitlar.values()
                         if k.get("durum") in ("sirada", "isleniyor"))
            boyut = sum(float(k.get("boyut_kb") or 0) for k in self._kayitlar.values())
            return {"ogrenme": self._ogrenme, "kayit": kayit, "kuyruk": kuyruk,
                    "calan": self._calan, "harita_aktif": self._harita is not None,
                    "toplam": len(self._kayitlar), "boyut_mb": round(boyut / 1024.0, 2),
                    "ayir_hazir": self._ayir_hazir()}

    def _ayir_hazir(self):
        return bool(self.ayir_python and self.ayir_betik
                    and os.path.isfile(self.ayir_python) and os.path.isfile(self.ayir_betik))

    def ornek(self):
        try:
            st, parca = self._harita, self._parca_son
            if st is None or parca is None:
                return None
            h = st[1]
            konum = self._konum_fn(parca)
            if konum is None:
                return None
            konum -= self.gecikme_sn
            if konum < 0:
                konum = 0.0
            i = int(konum * h["hz"])
            if i < 0 or i >= len(h["vokal"]):
                return None
            ki = bisect.bisect_right(h["kick"], konum)
            si = bisect.bisect_right(h["snare"], konum)
            v = self._vurus
            kick = snare = False
            if v is None or v[0] != st[0] or konum < v[1] - 0.05 or konum - v[1] > 0.25:
                pass                      # yeni sarki ya da seek: vurus sayma
            else:
                kick, snare = ki > v[2], si > v[3]
            self._vurus = [st[0], konum, ki, si]
            return {"vokal": h["vokal"][i], "vokal_var": bool(h["vokal_var"][i]),
                    "bas": h["bas"][i], "kick": kick, "snare": snare}
        except Exception:
            return None

    # ---------------------------------------------------------------- harita yukleme
    def _harita_yukle(self, anahtar):
        try:
            with open(self._harita_yol(anahtar), encoding="utf-8") as f:
                d = json.load(f)
            hz = float(d.get("hz", 50))
            n = len(d["vokal"])
            vokal = [float(x) for x in d["vokal"]]
            bas = [float(x) for x in d["bas"]][:n]
            var = [int(x) for x in d["vokal_var"]][:n]
            if hz <= 0 or n == 0 or len(bas) < n or len(var) < n:
                raise ValueError("harita tutarsiz")
            return {"hz": hz, "vokal": vokal, "bas": bas, "vokal_var": var,
                    "kick": sorted(float(x) for x in d.get("kick", [])),
                    "snare": sorted(float(x) for x in d.get("snare", []))}
        except Exception as e:
            with self._kilit:
                k = self._kayitlar.get(anahtar)
                if k is not None:
                    k["durum"], k["hata"] = "hata", "harita okunamadi: %s" % e
                    self._kaydet()
            return None

    # ---------------------------------------------------------------- yoklama / durum makinesi
    def _yoklama_dongu(self):
        while not self._dur.is_set():
            try:
                self._tik()
            except Exception:
                pass
            self._dur.wait(self.yoklama_sn)

    def _tik(self):
        parca = self.medya.parca()
        self._parca_son = parca
        spot = _spotify_mi(parca)
        sure = parca.get("sure") if parca else None
        konum = self._konum_fn(parca) if parca else None

        # calan sarki / harita
        calan = None
        if spot and sure:
            a = self._bul(parca["ad"], parca["sanatci"], sure)
            if a is not None and self._kayitlar.get(a, {}).get("durum") == "hazir":
                calan = a
        if calan != self._calan or (calan and (self._harita is None or self._harita[0] != calan)):
            if calan is None:
                self._calan, self._harita = None, None
            else:
                h = self._harita_yukle(calan)
                self._calan = calan if h else None
                self._harita = (calan, h) if h else None

        rec = self._rec
        if rec is not None:
            self._kayit_denetle(rec, parca, spot, sure, konum)
        elif spot and sure and self._ogrenme:
            self._kayit_baslat_dene(parca, sure, konum)

    def _kayit_baslat_dene(self, parca, sure, konum):
        if (not parca.get("caliyor") or konum is None or konum > self.baslangic_marji
                or not (self.min_sure <= sure <= self.maks_sure) or np is None
                or time.monotonic() < self._rec_bekle_sn):
            return
        a = self._bul(parca["ad"], parca["sanatci"], sure)
        yeniden = 0
        if a is not None:
            k = self._kayitlar[a]
            if k.get("durum") != "hata" or int(k.get("yeniden", 0)) >= YENIDEN_EN_COK:
                return
            yeniden = int(k.get("yeniden", 0)) + 1
        else:
            a = anahtar_uret(parca["ad"], parca["sanatci"], sure)
        fabrika = self._fabrika
        if fabrika is None:
            import spotify_ses
            fabrika = spotify_ses.UygulamaSesi
        rec = _Kayit(a, parca["ad"], parca["sanatci"], float(sure), self._wav_yol(a), fabrika)
        with self._kilit:
            self._kayitlar[a] = {"ad": parca["ad"], "sanatci": parca["sanatci"],
                                 "sure": round(float(sure), 1), "eklenme": _simdi_iso(),
                                 "durum": "kaydediliyor", "hata": None, "boyut_kb": 0,
                                 "yeniden": yeniden}
            self._rec = rec
            self._kaydet()
        rec.thread.start()

    def _kayit_denetle(self, rec, parca, spot, sure, konum):
        if rec.durum == "olu":
            self._kayit_bitir(rec, hata=True)
            return
        degisti = (not spot or parca is None or parca.get("ad") != rec.ad
                   or parca.get("sanatci") != rec.sanatci)
        if degisti:
            self._kayit_bitir(rec)
            return
        if konum is None:
            return
        if abs(konum - rec.yazilan_sn) > SEEK_ESIK_SN:
            self._kayit_iptal()
            return
        if parca.get("caliyor"):
            rec.duraklat.clear()
        else:
            rec.duraklat.set()
        if sure and konum >= sure - self.bitis_marji:
            self._kayit_bitir(rec)

    def _kayit_durdur(self, rec):
        rec.dur.set()
        rec.thread.join(3.0)

    def _kayit_iptal(self):
        """Calisan kaydi at: WAV ve index kaydi silinir."""
        with self._kilit:
            rec = self._rec
            if rec is None:
                return
            self._rec = None
        self._kayit_durdur(rec)
        self._sil_dosya(rec.yol)
        with self._kilit:
            self._kayitlar.pop(rec.anahtar, None)
            self._kaydet()

    def _kayit_bitir(self, rec, hata=False):
        """Sarki bitti/degisti: yeterliyse kuyruga, degilse sil."""
        self._kayit_durdur(rec)
        yeterli = (not hata or rec.kare > 0) and rec.yazilan_sn >= MIN_YUZDE * rec.sure
        with self._kilit:
            self._rec = None
            if rec.durum == "olu" and rec.kare == 0 and rec.hata:
                self._rec_bekle_sn = time.monotonic() + 10.0    # ses acilamadi: sik deneme
            if yeterli:
                k = self._kayitlar.get(rec.anahtar)
                if k is not None:
                    k["durum"], k["hata"] = "sirada", None
                    self._kaydet()
                    self._is_var.set()
                    return
            self._sil_dosya(rec.yol)
            self._kayitlar.pop(rec.anahtar, None)
            self._kaydet()

    # ---------------------------------------------------------------- kuyruk isci
    def _isci_dongu(self):
        while not self._dur.is_set():
            try:
                a = self._siradaki() if self._ayir_hazir() else None
                if a is None:
                    self._is_var.wait(2.0)
                    self._is_var.clear()
                    continue
                self._isle(a)
            except Exception:
                self._dur.wait(1.0)

    def _siradaki(self):
        with self._kilit:
            adaylar = [(k.get("eklenme") or "", a) for a, k in self._kayitlar.items()
                       if k.get("durum") == "sirada"]
        return min(adaylar)[1] if adaylar else None

    def _isle(self, a):
        wav = self._wav_yol(a)
        cikti = os.path.join(self.gecici, a + ".cikti.json")
        with self._kilit:
            k = self._kayitlar.get(a)
            if k is None:
                return
            if not os.path.isfile(wav):
                del self._kayitlar[a]
                self._kaydet()
                return
            k["durum"], k["hata"] = "isleniyor", None
            self._ilerleme[a] = 0.0
            self._kaydet()
        bayrak = {"stderr": ""}
        rc = None
        try:
            komut = [self.ayir_python, self.ayir_betik, wav, cikti,
                     "--model-klasoru", self.modeller]
            kw = {}
            if os.name == "nt":
                kw["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                       | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
            p = subprocess.Popen(komut, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                 errors="replace", **kw)
            self._surec = p
            for satir in p.stdout:
                satir = satir.strip()
                if satir.startswith("ILERLEME"):
                    try:
                        self._ilerleme[a] = max(0.0, min(1.0, float(satir.split()[1])))
                    except Exception:
                        pass
                elif satir:
                    bayrak["stderr"] = satir
            rc = p.wait()
        except Exception as e:
            bayrak["stderr"] = "ayirma baslatilamadi: %s" % e
        finally:
            self._surec = None

        with self._kilit:
            self._ilerleme.pop(a, None)
            k = self._kayitlar.get(a)
            if k is None:                       # sirada sil() cagrildi
                self._sil_dosya(cikti)
                self._sil_dosya(self._harita_yol(a))
                return
            if self._dur.is_set() and rc != 0:  # panel kapaniyor: WAV kalsin, tekrar sirada
                k["durum"] = "sirada"
                self._sil_dosya(cikti)
                self._kaydet()
                return
            hata = None
            if rc == 0:
                try:
                    with open(cikti, encoding="utf-8") as f:
                        d = json.load(f)
                    if not (isinstance(d, dict) and d.get("vokal")):
                        raise ValueError("harita bos")
                    os.replace(cikti, self._harita_yol(a))
                except Exception as e:
                    hata = "cikti okunamadi: %s" % e
            else:
                hata = bayrak["stderr"] or ("ayirma kodu %s" % rc)
            if hata:
                k["durum"], k["hata"] = "hata", hata[:300]
            else:
                k["durum"], k["hata"] = "hazir", None
                try:
                    k["boyut_kb"] = round(os.path.getsize(self._harita_yol(a)) / 1024.0, 1)
                except OSError:
                    k["boyut_kb"] = 0
            self._sil_dosya(wav)
            self._sil_dosya(cikti)
            self._kaydet()
        self._is_var.set()
