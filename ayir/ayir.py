#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LED panel icin cevrimdisi sarki analizi.

Kullanim: python ayir.py GIRIS.wav CIKIS.json [--model-klasoru K]
Vokal (BS-Roformer) ve davul/bas (htdemucs_ft) ayirir, kucuk bir JSON harita yazar.
Cikis kodu: 0 basari, 1 hata (stderr'e tek satir neden).
"""
import argparse
import json
import math
import os
import shutil
import sys
import tempfile
import time

HZ = 50
SR_ANALIZ = 44100
VARSAYILAN_MODEL_KLASORU = r"C:\Users\ardak\Desktop\led-panel\kutuphane\_modeller"
MODEL_VOKAL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
MODEL_DAVUL_BAS = "htdemucs_ft.yaml"


def hata(mesaj):
    mesaj = " ".join(str(mesaj).split())
    sys.stderr.write(mesaj + "\n")
    sys.stderr.flush()
    sys.exit(1)


def ilerleme(oran, metin):
    print("ILERLEME %.2f %s" % (oran, metin), flush=True)


# ---------------------------------------------------------------- sinyal isleme

def mono_oku(yol):
    import numpy as np
    import soundfile as sf
    x, sr = sf.read(yol, dtype="float32", always_2d=True)
    return x.mean(axis=1).astype(np.float32), sr


def rms_zarfi(x, sr, hz, n):
    """x'in hz hizinda RMS dizisi (uzunluk n)."""
    import numpy as np
    adim = sr / float(hz)
    out = np.zeros(n, dtype=np.float64)
    for i in range(n):
        a = int(i * adim)
        b = int((i + 1) * adim)
        seg = x[a:b]
        if seg.size:
            out[i] = math.sqrt(float(np.mean(seg.astype(np.float64) ** 2)))
    return out


def db(v):
    import numpy as np
    return 20.0 * np.log10(np.maximum(v, 1e-9))


def sessiz_mi(x):
    import numpy as np
    return x.size == 0 or float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) < 1e-4


VOKAL_KARISIM_DB = -15.0   # vokal, karisik sesin en cok bu kadar altindaysa "var" (sizinti sayilmaz)
VOKAL_SARKI_DB = -20.0     # sarki genelinde vokal bu oranin altindaysa vokalsiz sarki (enstrumantal)


def vokal_analiz(x, sr, n, karisim_db=None):
    """Vokal zarfi (0-1) ve vokal_var (0/1). karisim_db: karisik sesin dB zarfi (ayni uzunluk);
    verilirse vokal yalniz karisima gore yeterince gurken sayilir (Roformer sizintisi vokal sanilmasin)."""
    import numpy as np
    if sessiz_mi(x):
        return [0.0] * n, [0] * n
    d = db(rms_zarfi(x, sr, HZ, n))
    if karisim_db is not None:
        m = np.asarray(karisim_db, dtype=float)[:n]
        fark = d - m
        canli = m > -50.0
        if canli.sum() >= 5 and float(np.percentile(fark[canli], 90)) < VOKAL_SARKI_DB:
            return [0.0] * n, [0] * n          # enstrumantal: vokal stem'i yalniz sizinti
        d = np.where(fark >= VOKAL_KARISIM_DB, d, -120.0)
    d = np.convolve(d, np.ones(3) / 3.0, mode="same")  # hafif yumusatma
    # normalizasyon yalniz sessiz olmayan kareler uzerinden yapilmali
    zemin = -60.0
    aktif = d[d > zemin]
    if aktif.size < 5:
        return [0.0] * n, [0] * n
    lo, hi = np.percentile(aktif, [5, 95])
    if hi - lo < 1.0:
        hi = lo + 1.0
    z = np.clip((d - lo) / (hi - lo), 0.0, 1.0)
    z[d <= zemin] = 0.0
    # vokal_var: histerezis. Esikler mutlak (tepeye gore) ki bozuk sizinti yakalanmasin
    tepe = float(np.percentile(d, 95))
    ac = tepe - 18.0
    kapa = tepe - 24.0
    ac = max(ac, -50.0)
    kapa = max(kapa, -56.0)
    var = np.zeros(n, dtype=np.int8)
    acik = False
    for i in range(n):
        if not acik and d[i] >= ac:
            acik = True
        elif acik and d[i] < kapa:
            acik = False
        var[i] = 1 if acik else 0
    # kisa (<0.25 sn) bosluklari kapat
    max_bosluk = int(round(0.25 * HZ))
    i = 0
    while i < n:
        if var[i] == 0:
            j = i
            while j < n and var[j] == 0:
                j += 1
            if i > 0 and j < n and (j - i) < max_bosluk:
                var[i:j] = 1
            i = j
        else:
            i += 1
    # cok kisa (<0.1 sn) vokal parcaciklarini sil
    i = 0
    while i < n:
        if var[i] == 1:
            j = i
            while j < n and var[j] == 1:
                j += 1
            if (j - i) < max(2, int(round(0.1 * HZ))):
                var[i:j] = 0
            i = j
        else:
            i += 1
    return [round(float(v), 3) for v in z], [int(v) for v in var]


def bas_analiz(x, sr, n):
    import numpy as np
    if sessiz_mi(x):
        return [0.0] * n
    d = db(rms_zarfi(x, sr, HZ, n))
    zemin = float(np.max(d)) - 40.0  # 40 dB dinamik aralik
    z = np.clip((d - zemin) / 40.0, 0.0, 1.0)
    return [round(float(v), 3) for v in z]


def band_filtre(x, sr, lo, hi):
    from scipy import signal
    nyq = sr / 2.0
    if lo is None:
        sos = signal.butter(4, hi / nyq, btype="low", output="sos")
    elif hi is None:
        sos = signal.butter(4, lo / nyq, btype="high", output="sos")
    else:
        sos = signal.butter(4, [lo / nyq, hi / nyq], btype="band", output="sos")
    return signal.sosfiltfilt(sos, x)


def onset_bul(x, sr, lo, hi, esik_oran=0.25, min_aralik=0.12, tepe_dB=-45.0):
    """Banda ozel onset: zarf farki (pozitif akis) + yerel tepe secimi.
    Dondurur: saniye listesi."""
    import numpy as np
    if sessiz_mi(x):
        return []
    y = band_filtre(x.astype(np.float64), sr, lo, hi)
    hop = 256
    pen = 1024
    nfr = (y.size - pen) // hop + 1
    if nfr < 4:
        return []
    kare = np.empty(nfr)
    for i in range(nfr):
        s = y[i * hop:i * hop + pen]
        kare[i] = math.sqrt(float(np.mean(s * s)))
    ld = np.log1p(kare * 1000.0)  # logaritmik sikistirma
    akis = np.maximum(np.diff(ld, prepend=ld[0]), 0.0)
    mx = float(akis.max())
    if mx <= 0:
        return []
    # mutlak seviye kapisi: cok kucuk sizintilari ele
    if db(np.array([kare.max()]))[0] < tepe_dB:
        return []
    esik = esik_oran * mx
    min_kare = max(1, int(min_aralik * sr / hop))
    anlar = []
    son = -10 ** 9
    for i in range(1, nfr - 1):
        if akis[i] >= esik and akis[i] >= akis[i - 1] and akis[i] >= akis[i + 1]:
            if i - son >= min_kare:
                # onset zamani: penceri baslangici + yaklasik yarim hop
                t = (i * hop + pen / 4.0) / sr
                anlar.append(round(t, 3))
                son = i
            elif anlar and akis[i] > akis[son]:
                # ayni aralikta daha guclu tepe: oncekini degistir
                anlar[-1] = round((i * hop + pen / 4.0) / sr, 3)
                son = i
    return anlar


# ---------------------------------------------------------------- ayirma

def cihaz_tespit():
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def ayirici_calistir(model_klasoru, calisma, model_adi, girdi, cikti_klasoru):
    """audio-separator ile modeli calistirir, olusan dosya yollarini dondurur."""
    from audio_separator.separator import Separator
    sep = Separator(
        model_file_dir=model_klasoru,
        output_dir=cikti_klasoru,
        output_format="WAV",
        log_level=40,  # yalniz ERROR
    )
    sep.load_model(model_filename=model_adi)
    dosyalar = sep.separate(girdi)
    yollar = []
    for d in dosyalar:
        p = d if os.path.isabs(d) else os.path.join(cikti_klasoru, d)
        yollar.append(p)
    return yollar


def stem_bul(yollar, anahtar):
    a = anahtar.lower()
    for p in yollar:
        if "(" + a + ")" in os.path.basename(p).lower():
            return p
    return None


def calistir(giris, cikis, model_klasoru):
    t0 = time.time()
    if not os.path.isfile(giris):
        hata("giris dosyasi yok: %s" % giris)
    try:
        import numpy as np
        import soundfile as sf
    except Exception as e:
        hata("eksik paket: %s" % e)
    try:
        info = sf.info(giris)
    except Exception as e:
        hata("giris okunamadi: %s" % e)
    sure = float(info.frames) / float(info.samplerate)
    if sure <= 0:
        hata("giris bos")
    n = int(math.ceil(sure * HZ))
    cihaz = cihaz_tespit()
    os.makedirs(model_klasoru, exist_ok=True)

    cikis_abs = os.path.abspath(cikis)
    cikis_dir = os.path.dirname(cikis_abs) or "."
    os.makedirs(cikis_dir, exist_ok=True)

    tmp = tempfile.mkdtemp(prefix="ayir_")
    try:
        ilerleme(0.02, "basladi cihaz=%s" % cihaz)
        # vokal
        try:
            v_dir = os.path.join(tmp, "vokal")
            os.makedirs(v_dir)
            yol_v = ayirici_calistir(model_klasoru, tmp, MODEL_VOKAL, os.path.abspath(giris), v_dir)
        except Exception as e:
            hata("vokal ayirma basarisiz: %s" % e)
        vokal_wav = stem_bul(yol_v, "vocals")
        if not vokal_wav or not os.path.isfile(vokal_wav):
            hata("vokal stem'i bulunamadi: %s" % [os.path.basename(p) for p in yol_v])
        ilerleme(0.45, "vokal ayrildi")
        vx, vsr = mono_oku(vokal_wav)
        gx, gsr = mono_oku(os.path.abspath(giris))
        karisim_db = db(rms_zarfi(gx, gsr, HZ, n))
        del gx
        vokal, vokal_var = vokal_analiz(vx, vsr, n, karisim_db)
        del vx
        for p in yol_v:
            try:
                os.remove(p)
            except OSError:
                pass

        # davul + bas
        try:
            d_dir = os.path.join(tmp, "davul")
            os.makedirs(d_dir)
            yol_d = ayirici_calistir(model_klasoru, tmp, MODEL_DAVUL_BAS, os.path.abspath(giris), d_dir)
        except Exception as e:
            hata("davul/bas ayirma basarisiz: %s" % e)
        davul_wav = stem_bul(yol_d, "drums")
        bas_wav = stem_bul(yol_d, "bass")
        if not davul_wav or not bas_wav:
            hata("davul/bas stem'i bulunamadi: %s" % [os.path.basename(p) for p in yol_d])
        ilerleme(0.85, "davul ve bas ayrildi")
        dx, dsr = mono_oku(davul_wav)
        bx, bsr = mono_oku(bas_wav)
        kick = onset_bul(dx, dsr, None, 150.0, esik_oran=0.25, min_aralik=0.15)
        snare = onset_bul(dx, dsr, 1500.0, 9000.0, esik_oran=0.25, min_aralik=0.12)
        bas = bas_analiz(bx, bsr, n)
        ilerleme(0.97, "analiz bitti")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    sonuc = {
        "surum": 1,
        "sure": round(sure, 1),
        "hz": HZ,
        "vokal": vokal,
        "vokal_var": vokal_var,
        "bas": bas,
        "kick": kick,
        "snare": snare,
        "modeller": {"vokal": MODEL_VOKAL, "davul_bas": MODEL_DAVUL_BAS},
        "sure_islem_sn": round(time.time() - t0, 1),
        "cihaz": cihaz,
    }
    gecici = cikis_abs + ".tmp"
    try:
        with open(gecici, "w", encoding="utf-8") as f:
            json.dump(sonuc, f, separators=(",", ":"))
        os.replace(gecici, cikis_abs)
    except Exception as e:
        hata("JSON yazilamadi: %s" % e)
    ilerleme(1.0, "tamam")


def main():
    ap = argparse.ArgumentParser(description="LED panel sarki analizi")
    ap.add_argument("giris")
    ap.add_argument("cikis")
    ap.add_argument("--model-klasoru", default=VARSAYILAN_MODEL_KLASORU)
    a = ap.parse_args()
    try:
        calistir(a.giris, a.cikis, a.model_klasoru)
    except SystemExit:
        raise
    except Exception as e:
        hata("beklenmeyen hata: %s: %s" % (type(e).__name__, e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
