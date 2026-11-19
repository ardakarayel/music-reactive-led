"""DP24 (normal renk) ile DP28 (gercek zamanli kontrol) hiz karsilastirmasi.

Calistir: .venv\\Scripts\\python.exe tools\\hiz_testi.py
Panel kapaliyken calistir (cihaz ayni anda tek baglanti kabul eder).
"""
import json
import os
import time

import tinytuya

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
d = tinytuya.BulbDevice(cfg["device_id"], cfg["ip"], cfg["local_key"], version=float(cfg.get("version", "3.3")))
d.set_socketPersistent(True)

RENKLER = [(0, 1000, 1000), (240, 1000, 1000)]  # kirmizi, mavi (h, s, v)


def dp24(h, s, v):
    return {"24": "%04x%04x%04x" % (h, s, v)}


def dp28(h, s, v):
    # change_mode(0=direct) + h + s + v + bright + temp
    return {"28": "0%04x%04x%04x%04x%04x" % (h, s, v, 0, 0)}


def kosu(ad, yap, hz, sure=4.0):
    aralik = 1.0 / hz
    n = int(sure * hz)
    t0 = time.perf_counter()
    gecen = []
    for i in range(n):
        h, s, v = RENKLER[i % 2]
        a = time.perf_counter()
        d.set_multiple_values(yap(h, s, v), nowait=True)
        gecen.append((time.perf_counter() - a) * 1000)
        kalan = t0 + (i + 1) * aralik - time.perf_counter()
        if kalan > 0:
            time.sleep(kalan)
    print("  %s @ %d Hz: %d komut, ortalama gonderim %.1f ms, en kotu %.1f ms"
          % (ad, hz, n, sum(gecen) / n, max(gecen)))


print("Durum:", d.status())
d.turn_on()
d.set_value(21, "colour")
time.sleep(0.5)

testler = [
    ("A) DP24 normal renk", dp24, 10),
    ("B) DP28 gercek zamanli (direct)", dp28, 10),
    ("C) DP28 gercek zamanli (direct)", dp28, 20),
]
for ad, yap, hz in testler:
    input("\n%s - saniyede %d kez kirmizi/mavi. Seride bak ve Enter'a bas..." % (ad, hz))
    kosu(ad, yap, hz)
    time.sleep(0.6)
    d.set_multiple_values(dp24(30, 0, 300), nowait=True)  # loş beyaz arasi

print("\nBitti. Hangisinde renkler net ve her degisimde aninda (yumusama olmadan) degisti?")
print("Ornek cevap: 'A yumusak, B net, C de net' gibi.")
d.set_multiple_values(dp24(30, 900, 1000), nowait=False)
