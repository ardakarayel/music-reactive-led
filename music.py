"""Muzik modu: varsayilan hoparlorun loopback kaydindan seviye ve bas/tiz orani.

soundcard, COM'u import edildigi thread'e baglar; bu yuzden import Music() icinde,
ana cagri thread'inde yapilir. Aygit hatasi start() icinde self.error'a yazilir, uygulama dusmez.
"""
import threading
import time
from math import log10

RATE = 44100
BLOK = 512            # ~11.6 ms adim
CIKIS_KONTROL_BLOK = 172   # ~2 sn'de bir varsayilan ses cikisi kontrolu
PENCERE = 2048        # FFT penceresi (bas cozunurlugu ~21 Hz)
VURUS_ALT = 35.0      # vurus (kick) bandi
VURUS_UST = 160.0
VURUS_ARA_SN = 0.11   # iki vurus arasi en az
VURUS_SONUM_SN = 0.13 # vurus parlamasinin sonumu
KISA_SN = 0.12        # kisa sureli gurluk penceresi
ARALIK_SN = 120.0     # sarkinin tepe/dip seviyesi bu surede unutulur (sarki degisince uyum)
ILK_PAY_DB = 15.0     # baslangicta tepe = ilk seviye + bu kadar
ORTA_SN = 3.0         # patlama karsilastirmasi icin orta vadeli gurluk
PATLAMA_DB = 6.0      # kisa gurluk ortayi bu kadar asarsa "drop"
PATLAMA_SONUM_SN = 1.5
ARALIK_EN_AZ_DB = 14.0
EGRI = 1.6            # >1: sessiz kisimlar daha los, yuksekler belirgin
TABAN = 0.04          # en sessiz kisimdaki parlaklik
AKI_GECMIS = 120      # ~1.4 sn esik gecmisi
AKI_TABAN = 0.5       # bundan kucuk bas artisi vurus sayilmaz (gurultu/pad ~0.4'e kadar)
HEDEF_RMS = 0.4
GURULTU = 1e-4
BAS_SINIR = 250.0
TIZ_SINIR = 2000.0
BLOK_SN = BLOK / float(RATE)   # v2: bundan uzun suren Boru.isle yakalamayi geciktirir


def _hata_metni(e):
    return f"{type(e).__name__}: {e}"[:200]


class Music:
    def __init__(self):
        self.active = False
        self.error = ""
        self._sens = 50.0
        self._level = 0.0
        self._ratio = 0.5
        self._env = 0.0
        self._pulse = 0.0
        self._vurus = 0
        self._son_vurus = 1.0
        self._onceki_bas = 0.0
        self._aki_gecmis = []
        self._buf = None
        self.ratio_tau = 0.4
        self._kisa = 0.0
        self._tepe = None
        self._dip = None
        self._orta = 0.0
        self._boost = 0.0
        self._son_patlama = 10.0
        self._lock = threading.Lock()
        self._stop_ev = None
        self._thread = None
        self._imp_hata = ""
        # v2 (analiz.Boru): None iken Basit stiller. _boru_lock Boru'yu yakalama thread'i (isle)
        # ve API thread'i (ayarla) arasinda korur; _lock'tan bagimsizdir.
        self._boru = None
        self._boru_lock = threading.Lock()
        self._v2_kare = None          # (h 0-360, s 0-1, v 0-1)
        self._v2_ozet = None          # {"bpm", "guven", "bolum", "vokal"}
        self._v2_viz = None           # Dionysos: gorsellestirme verisi (Boru.son_viz)
        self._v2_lab = None           # laboratuvar: (spektrum_lineer, kisa_db, ort_db, motor_p) son blok
        self.kayit_acici = None       # test kancasi: callable() -> recorder baglam yoneticisi
        self.blok_istatistik = {"n": 0, "toplam": 0.0, "maks": 0.0, "uzun": 0}
        try:
            import numpy  # noqa: F401
            import soundcard  # noqa: F401
        except Exception as e:
            self._imp_hata = "Ses kütüphanesi yüklenemedi: " + _hata_metni(e)

    def set_sensitivity(self, x):
        with self._lock:
            self._sens = max(1.0, min(100.0, float(x)))
            sens = self._sens
        self.ayarla_v2(hassasiyet=sens)

    def v2_kare(self):
        """v2 calisirken son (h, s, v); degilse None."""
        with self._lock:
            return self._v2_kare

    def v2_ozet(self):
        """v2 calisirken {"bpm", "guven", "bolum", "vokal"}; degilse None."""
        with self._lock:
            return dict(self._v2_ozet) if self._v2_ozet else None

    def v2_aktif(self):
        return self._boru is not None

    def v2_dionysos(self):
        """Calisan Boru Dionysos modundaysa True."""
        b = self._boru
        return b is not None and bool(getattr(b, "dionysos", False))

    def viz(self):
        """Dionysos calisirken son gorsellestirme verisi (JSON uyumlu sozluk); degilse None."""
        with self._lock:
            return self._v2_viz

    def lab_viz(self):
        """Laboratuvar ekolayziri: Dionysos viz alanlari + 32 bant spektrum, seviye/ortalama dB, esik cizgileri.
        Muzik kapaliysa ya da Dionysos calismiyorsa None. Hesap burada (API thread'i); ses thread'ine yalniz
        referans saklatilir."""
        import math
        from analiz import lab as _lab
        with self._lock:
            viz, ozet, lv = self._v2_viz, self._v2_ozet, self._v2_lab
        b = self._boru
        if viz is None or lv is None or b is None:
            return None
        spek, kisa_db, ort_db, p = lv
        db = [round(10.0 * math.log10(max(float(x), 1e-12)), 1) for x in spek]
        ozet = ozet or {}
        out = dict(viz)
        out.update({
            "spektrum_db": db,
            "bant_hz": [round(float(x), 1) for x in b.dinleyici.bant_hz],
            "seviye_db": round(float(kisa_db), 1),
            "ort_db": None if ort_db is None else round(float(ort_db), 1),
            "bpm": float(ozet.get("bpm", 0.0)),
            "vurus": bool((viz.get("davul") or {}).get("kick", False)),    # kick, 0,1 sn tutulur (30 Hz yoklamada kacmaz)
            "bolum": str(ozet.get("bolum", "")),
            "olay": str(viz.get("olay", "")),
            "cizgiler": _lab.cizgiler(p, ort_db, -10.0 * math.log10(b.dinleyici.nfft // 2 + 1)),
        })
        return out

    def ayarla_v2(self, mod=None, hassasiyet=None, yogunluk=None, taban_ton="degismez",
                  vokal_agirlik=None, gecikme_sn=None, renk_kayma=None, lab_params=None):
        """Calisan Boru'yu yeniden baslatmadan ayarlar. v2 kapaliyken etkisiz."""
        with self._boru_lock:
            b = self._boru
            if b is None:
                return
            try:
                b.ayarla(mod=mod, hassasiyet=hassasiyet, yogunluk=yogunluk, taban_ton=taban_ton)
                if vokal_agirlik is not None:
                    b.isik.vokal_agirlik = float(vokal_agirlik)
                if gecikme_sn is not None:
                    b.isik.gecikme_sn = float(gecikme_sn)
                if renk_kayma is not None and hasattr(b.isik, "renk_kayma"):
                    b.isik.renk_kayma = float(renk_kayma)
                if lab_params is not None and hasattr(b.isik, "p"):
                    b.ayarla(params=lab_params)    # laboratuvar: motor parametreleri, sifirlamadan
            except Exception as e:
                self.error = "Analiz ayarı uygulanamadı: " + _hata_metni(e)

    def _boru_kur(self, v2):
        from analiz import Boru   # tembel: Basit stiller analiz paketine bagimli olmaz
        b = Boru(mod=v2["mod"], hassasiyet=self._sens, yogunluk=v2.get("yogunluk", 1.0))
        b.isik.vokal_agirlik = float(v2.get("vokal_agirlik", 1.0))
        b.isik.gecikme_sn = float(v2.get("gecikme_sn", 0.0))
        if hasattr(b.isik, "renk_kayma") and v2.get("renk_kayma") is not None:
            b.isik.renk_kayma = float(v2["renk_kayma"])
        if v2.get("lab_params") and hasattr(b.isik, "p"):
            b.ayarla(params=v2["lab_params"])
        b.taban_ton = v2.get("taban_ton")
        b.sifirla()   # ses basinda temiz durum
        return b

    def values(self):
        """(level 0-1, bas/tiz orani 0-1; 0 = bas, 1 = tiz)."""
        with self._lock:
            return self._level, self._ratio

    def start(self, sensitivity, v2=None):
        """Kaydi baslatir. v2: None (Basit) ya da {"mod", "yogunluk", "vokal_agirlik", "gecikme_sn",
        "taban_ton"} (analiz.Boru). Basarida True; hatada False ve self.error dolu."""
        self.stop()
        self.set_sensitivity(sensitivity)
        self.error = ""
        with self._lock:
            self._tepe = self._dip = None   # yeni dinleme: dinamik aralik sifirdan ogrenilir
            self._v2_kare = None
            self._v2_ozet = None
            self._v2_viz = None
            self._v2_lab = None
        self.blok_istatistik = {"n": 0, "toplam": 0.0, "maks": 0.0, "uzun": 0}
        if self._imp_hata:
            self.error = self._imp_hata
            return False
        if v2 is not None:
            try:
                boru = self._boru_kur(v2)
            except Exception as e:
                self.error = "Analiz paketi yüklenemedi: " + _hata_metni(e)
                return False
            with self._boru_lock:
                self._boru = boru
        else:
            with self._boru_lock:
                self._boru = None
        ev = threading.Event()
        ready = threading.Event()
        result = {}
        th = threading.Thread(target=self._run, args=(ev, ready, result), daemon=True)
        self._stop_ev = ev
        self._thread = th
        th.start()
        ready.wait(8)
        if result.get("ok"):
            self.active = True
            return True
        self.error = result.get("error") or "Ses aygıtı zaman aşımına uğradı."
        self._stop_ev = None
        self._thread = None
        with self._boru_lock:
            self._boru = None
        ev.set()
        return False

    def stop(self):
        ev, th = self._stop_ev, self._thread
        self._stop_ev = None
        self._thread = None
        self.active = False
        with self._lock:
            self._level = 0.0
            self._v2_kare = None
            self._v2_ozet = None
            self._v2_viz = None
            self._v2_lab = None
        if ev is not None:
            ev.set()
        if th is not None and th is not threading.current_thread():
            th.join(2.0)
        if th is None or not th.is_alive():
            with self._boru_lock:
                self._boru = None

    def _run(self, ev, ready, result):
        try:
            import numpy as np
            import soundcard as sc

            def ac():
                """Varsayilan cikisin loopback kaydini acar: (kayit, rec, cikis_kimligi)."""
                if self.kayit_acici is not None:
                    k = self.kayit_acici()
                    return k, k.__enter__(), None
                spk = sc.default_speaker()
                mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
                k = mic.recorder(samplerate=RATE, blocksize=BLOK)
                return k, k.__enter__(), str(spk.id)

            kayit, rec, cikis = ac()
        except Exception as e:
            result["error"] = "Ses aygıtı açılamadı: " + _hata_metni(e)
            ready.set()
            return
        result["ok"] = True
        ready.set()
        v2 = self._boru is not None   # mod bu kayit boyunca sabit
        sayac = 0
        try:
            while not ev.is_set():
                # Varsayilan ses cikisi degisti mi (kulaklik takildi/cikarildi)? ~2 sn'de bir bak;
                # degistiyse kaydi yeni cikista yeniden ac (Arda, 2026-11-19: kulaklikta isik yanmadi).
                sayac += 1
                if cikis is not None and sayac >= CIKIS_KONTROL_BLOK:
                    sayac = 0
                    try:
                        yeni = str(sc.default_speaker().id)
                    except Exception:
                        yeni = cikis
                    if yeni != cikis:
                        try:
                            kayit.__exit__(None, None, None)
                        except Exception:
                            pass
                        kayit, rec, cikis = ac()
                veri = rec.record(numframes=BLOK)
                if v2:
                    self._v2_isle(np, veri)
                else:
                    self._analiz(np, veri)
        except Exception as e:
            if self._stop_ev is ev:
                self.error = "Kayıt durdu: " + _hata_metni(e)
        finally:
            try:
                kayit.__exit__(None, None, None)
            except Exception:
                pass
            if self._stop_ev is ev:
                self.active = False
                self._stop_ev = None
                self._thread = None
                with self._lock:
                    self._level = 0.0
                    self._v2_kare = None
                    self._v2_ozet = None
                    self._v2_viz = None
                    self._v2_lab = None
                with self._boru_lock:
                    self._boru = None

    def _v2_isle(self, np, veri):
        """v2: bloku (mono) Boru'ya verir; son kare ve analiz ozeti saklanir. Boru istisna atarsa
        o kare atlanir, muzik durmaz ve Basit'e dusulmez (hata self.error'a yazilir)."""
        t0 = time.perf_counter()
        try:
            x = np.asarray(veri, dtype=np.float64)
            if x.ndim == 2:
                x = x.mean(axis=1)
            if x.size != BLOK:
                return
            with self._boru_lock:
                b = self._boru
                if b is None:
                    return
                oz, rd, bd, kare = b.isle(x)
                vk = b.son_vokal
                viz = b.son_viz
                spek = getattr(b.dinleyici, "son_spek", None)
                if b.dionysos:
                    lab = (spek, b.isik.son_kisa_db, b.isik.son_ort2, b.isik.p) if spek is not None else None
                else:
                    lab = None
            ozet = {"bpm": float(rd.bpm), "guven": float(rd.guven), "bolum": str(bd.ad),
                    "vokal": bool(getattr(vk, "vokal_var", False))}
            with self._lock:
                self._v2_kare = (float(kare.h), float(kare.s), float(kare.v))
                self._v2_ozet = ozet
                self._v2_viz = viz
                self._v2_lab = lab
                self._level = float(kare.v)
        except Exception as e:
            self.error = "Analiz hatası (kare atlandı): " + _hata_metni(e)
        finally:
            dt = time.perf_counter() - t0
            st = self.blok_istatistik
            st["n"] += 1
            st["toplam"] += dt
            st["maks"] = max(st["maks"], dt)
            if dt > BLOK_SN:
                st["uzun"] += 1

    def _analiz(self, np, veri):
        """Her ~12 ms'lik blokta: bas bandinda vurus (onset) algilama + yavas otomatik kazanc.
        Vurusta seviye 1'e firlar ve VURUS_SONUM_SN ile soner; vurus araliginda seviye
        sarkinin yavas normalize edilmis gurlugunu izler (2026-11-17, Arda: "senkron degil")."""
        x = np.asarray(veri, dtype=np.float64)
        if x.ndim == 2:
            x = x.mean(axis=1)
        if x.size == 0:
            return
        dt = x.size / float(RATE)
        if self._buf is None:
            self._buf = np.zeros(PENCERE)
            self._pencere = np.hanning(PENCERE)
            self._frek = np.fft.rfftfreq(PENCERE, 1.0 / RATE)
            self._bas_m = (self._frek >= VURUS_ALT) & (self._frek <= VURUS_UST)
        n = min(x.size, PENCERE)
        self._buf = np.roll(self._buf, -n)
        self._buf[-n:] = x[-n:]
        rms = float(np.sqrt(np.mean(x * x)))
        mag = np.abs(np.fft.rfft(self._buf * self._pencere))
        bas_e = float(np.log1p(1000.0 * float((mag[self._bas_m] ** 2).sum()) / PENCERE))
        bas = float(mag[self._frek < BAS_SINIR].sum())
        tiz = float(mag[self._frek > TIZ_SINIR].sum())

        with self._lock:
            sens = self._sens
            if rms < GURULTU:
                self._level += (0.0 - self._level) * 0.3
                self._pulse = 0.0
                self._onceki_bas = bas_e
                return
            # Sarkinin dinamik araligi: kisa sureli gurluk (dB) sarkinin en sessiz ve en
            # yuksek seviyesine gore 0-1'e olceklenir. Build-up los, drop tam parlak
            # (2026-11-17, Arda: "patlama oncesi ve patlama ani hissedilmiyor").
            a = 1.0 - pow(2.718281828, -dt / KISA_SN)
            if self._tepe is None:
                self._kisa = rms          # ilk blokta sifirdan tirmanma yok
            self._kisa += (rms - self._kisa) * a
            db = 20.0 * log10(max(self._kisa, 1e-6))
            u = dt / ARALIK_SN
            if self._tepe is None:
                # Sarkinin ileride daha da yukselecegini varsay: giris los baslar.
                self._tepe, self._dip = db + ILK_PAY_DB, db - 6.0
                self._orta = db
            # Ani yukselis (drop): kisa gurluk son ~3 sn ortalamasini asarsa patlama.
            self._orta += (db - self._orta) * (1.0 - pow(2.718281828, -dt / ORTA_SN))
            self._son_patlama += dt
            if db - self._orta > PATLAMA_DB and self._son_patlama > 2.0:
                self._son_patlama = 0.0
                self._boost = 1.0
                self._pulse = 1.0
            else:
                self._boost *= pow(2.718281828, -dt / PATLAMA_SONUM_SN)
            if db > self._tepe:
                self._tepe = db
            else:
                self._tepe += (db - self._tepe) * u          # tepe yavasca iner
            if db < self._dip:
                self._dip = db
            else:
                self._dip += (db - self._dip) * u            # dip yavasca cikar
            aralik = max(self._tepe - self._dip, ARALIK_EN_AZ_DB)
            norm = min(1.0, max(0.0, (db - (self._tepe - aralik)) / aralik))
            gur = max(norm ** EGRI, self._boost)              # kontrast + patlama

            # Vurus: bas enerjisindeki ani artis (spektral aki), uyarlanir esik.
            aki = max(0.0, bas_e - self._onceki_bas)
            self._onceki_bas = bas_e
            self._aki_gecmis.append(aki)
            if len(self._aki_gecmis) > AKI_GECMIS:
                self._aki_gecmis.pop(0)
            g = self._aki_gecmis
            ort = sum(g) / len(g)
            sap = (sum((v - ort) ** 2 for v in g) / len(g)) ** 0.5
            k = 2.6 - 1.9 * (sens / 100.0)          # hassasiyet 1 -> 2.6, 100 -> 0.7
            self._son_vurus += dt
            if (len(g) >= 20 and aki > ort + k * sap and aki > AKI_TABAN and bas_e > 1.0
                    and self._son_vurus >= VURUS_ARA_SN):
                self._son_vurus = 0.0
                self._pulse = 1.0
                self._vurus += 1
            else:
                self._pulse *= pow(2.718281828, -dt / VURUS_SONUM_SN)

            # Vurus parlamasi da sarkinin o anki yogunluguyla olceklenir: sessiz kisimda
            # kucuk nabiz, drop'ta tam patlama.
            hedef = max(self._pulse * (0.3 + 0.7 * gur), TABAN + (0.75 - TABAN) * gur)
            if hedef > self._level:
                self._level = hedef                       # yukselis aninda
            else:
                self._level += (hedef - self._level) * 0.35
            if bas + tiz > 1e-9:
                b = 1.0 - pow(2.718281828, -dt / max(0.02, self.ratio_tau))
                self._ratio += ((tiz / (bas + tiz)) - self._ratio) * b  # renk yumusakligi (panelden)

    def beats(self):
        """Simdiye kadar algilanan vurus sayisi (spektrum stilinde renk kaydirmak icin)."""
        with self._lock:
            return self._vurus
