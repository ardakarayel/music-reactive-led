"""Efekt motoru: HSV yardimcilari, efekt tanimlari ve kare dongusu.

Motor ~20 Hz kare uretir; cihaza en cok ~10 Hz ve yalnizca renk degisince gonderir.
"""
import colorsys
import math
import random
import threading
import time

EFFECTS = [
    {"name": "breathe", "label": "Nefes"},
    {"name": "rainbow", "label": "Gökkuşağı"},
    {"name": "candle", "label": "Mum"},
    {"name": "aurora", "label": "Kutup ışığı"},
    {"name": "police", "label": "Polis"},
    {"name": "strobe", "label": "Flaş"},
]
EFFECT_NAMES = [e["name"] for e in EFFECTS]

KARE_SN = 0.02
GONDERME_SN = 0.06  # ~16 Hz; DP28 20 Hz'e de yetisiyor (2026-11-17 testi)


def hsv_to_rgb(h, s, v):
    """h 0-360, s 0-100, v 0-100 -> (r, g, b) 0-255."""
    r, g, b = colorsys.hsv_to_rgb((h % 360.0) / 360.0,
                                  max(0.0, min(100.0, s)) / 100.0,
                                  max(0.0, min(100.0, v)) / 100.0)
    return (round(r * 255), round(g * 255), round(b * 255))


def periyot(hiz):
    """Hiz 1-100 -> saniye: 1 yavas (20 sn), 100 hizli (0.5 sn)."""
    oran = (max(1.0, min(100.0, float(hiz))) - 1.0) / 99.0
    return 20.0 * (0.5 / 20.0) ** oran


def render(name, t, hiz, h, s, v):
    """Efekt adi ve zamana gore (r, g, b) doner. v tavan parlakliktir."""
    p = periyot(hiz)
    faz = (t % p) / p
    if name == "breathe":
        k = 0.1 + 0.9 * (0.5 - 0.5 * math.cos(2 * math.pi * faz))
        return hsv_to_rgb(h, s, v * k)
    if name == "rainbow":
        return hsv_to_rgb(360.0 * faz, max(s, 90), v)
    if name == "candle":
        r = random.random()
        return hsv_to_rgb(18 + 14 * r, 95, v * (0.35 + 0.65 * r))
    if name == "aurora":
        return hsv_to_rgb(120 + 160 * (0.5 - 0.5 * math.cos(2 * math.pi * faz)), 80, v)
    if name == "police":
        return hsv_to_rgb(0 if faz < 0.5 else 240, 100, v)
    if name == "strobe":
        return hsv_to_rgb(h, 0, v) if faz < 0.5 else (0, 0, 0)
    return hsv_to_rgb(h, s, v)


class Engine:
    """frame_fn(t) -> ((r, g, b), level) veya None (kapaliyken).
    send_fn((r, g, b)) -> bool. Renk degisince ve en az GONDERME_SN aralikla gonderir."""

    def __init__(self, frame_fn, send_fn):
        self._frame_fn = frame_fn
        self._send_fn = send_fn
        self.gonderme_sn = GONDERME_SN
        self._lock = threading.Lock()
        self._gen = 0
        self._sent = None
        self._last_send = 0.0
        self._last = ((0, 0, 0), 0.0)
        self._stop_ev = None

    def start(self):
        if self._stop_ev is not None:
            return
        ev = threading.Event()
        self._stop_ev = ev
        threading.Thread(target=self._run, args=(ev,), daemon=True).start()

    def stop(self):
        if self._stop_ev is not None:
            self._stop_ev.set()
            self._stop_ev = None

    def force(self):
        """Bir sonraki karede renk, son gonderilenden bagimsiz olarak yeniden gonderilir."""
        with self._lock:
            self._gen += 1
            self._sent = None

    def frame(self):
        return self._last

    def _run(self, ev):
        while not ev.is_set():
            t0 = time.monotonic()
            try:
                out = self._frame_fn(t0)
            except Exception:
                out = None
            if out is None:
                self._last = ((0, 0, 0), 0.0)
            else:
                rgb, level = out
                self._last = (rgb, level)
                with self._lock:
                    sent, gen = self._sent, self._gen
                if rgb != sent and t0 - self._last_send >= self.gonderme_sn:
                    self._last_send = t0
                    try:
                        ok = bool(self._send_fn(rgb))
                    except Exception:
                        ok = False
                    with self._lock:
                        if gen == self._gen:
                            self._sent = rgb if ok else None
            ev.wait(max(0.0, KARE_SN - (time.monotonic() - t0)))
