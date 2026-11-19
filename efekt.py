"""Kullanici efektleri (Arda, 2026-11-19: "efektler icin de kendin olusturma yeri ekle").

Bir efekt iki bagimsiz hareketten olusur:
  renk hareketi:     sabit | akis (renkler arasi yumusak) | atlama (renkler arasi ani) | gokkusagi (tum cember)
  parlaklik hareketi: sabit | nefes | nabiz (ani yukselis, yavas sonus) | flas | titreme (mum) | testere
Ayarlar: renkler (1-6, 0xRRGGBB), renk_sure (bir renkte / bir turda sn), titresim (ton oynamasi, derece),
parlak_sure (parlaklik dongusu, sn), min_v / max_v (0-100).
Efekt kartindaki hiz surgusu 50'deyken efekt tanimlandigi hizda oynar; 1 yavas (~0,16x), 100 hizli (~6,4x).
Ayni hesap web/index.html'deki efekt duzenleyici onizlemesinde (efAn) vardir.
"""
import colorsys
import math

RENK_HAREKET = ("sabit", "akis", "atlama", "gokkusagi")
PARLAK_HAREKET = ("sabit", "nefes", "nabiz", "flas", "titreme", "testere")
RENK_MAX = 6
EFEKT_MAX = 10


def _sayi(x, lo, hi, vars):
    try:
        d = float(x)
    except (TypeError, ValueError):
        return vars
    if d != d or d in (float("inf"), float("-inf")):
        return vars
    return max(lo, min(hi, d))


def temizle(d):
    """Efekt tanimi sozlugu -> temiz tanim ya da None (renk yoksa)."""
    if not isinstance(d, dict):
        return None
    renkler = []
    for c in (d.get("renkler") or [])[:RENK_MAX]:
        if isinstance(c, str) and c.startswith("#") and len(c) == 7:
            try:
                c = int(c[1:], 16)
            except ValueError:
                continue
        if isinstance(c, bool) or not isinstance(c, (int, float)):
            continue
        c = int(c)
        if 0 <= c <= 0xFFFFFF:
            renkler.append(c)
    if not renkler:
        return None
    lo = round(_sayi(d.get("min_v"), 0, 100, 10), 1)
    hi = round(_sayi(d.get("max_v"), 0, 100, 100), 1)
    if lo > hi:
        lo, hi = hi, lo
    return {
        "renkler": renkler,
        "renk_hareket": d.get("renk_hareket") if d.get("renk_hareket") in RENK_HAREKET else "sabit",
        "renk_sure": round(_sayi(d.get("renk_sure"), 0.1, 60, 2), 2),
        "titresim": round(_sayi(d.get("titresim"), 0, 90, 0), 1),
        "parlak_hareket": d.get("parlak_hareket") if d.get("parlak_hareket") in PARLAK_HAREKET else "nefes",
        "parlak_sure": round(_sayi(d.get("parlak_sure"), 0.05, 30, 2), 2),
        "min_v": lo, "max_v": hi,
    }


def _hash(n):
    x = math.sin(n * 12.9898) * 43758.5453
    return x - math.floor(x)


def _gurultu(x, tohum):
    """Yumusak deger gurultusu 0-1 (tam sayilarda rastgele, arasi smoothstep)."""
    i = math.floor(x)
    f = x - i
    a, b = _hash(i + tohum), _hash(i + 1 + tohum)
    u = f * f * (3 - 2 * f)
    return a + (b - a) * u


def _hsv(c):
    r, g, b = ((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0
    h, s, _ = colorsys.rgb_to_hsv(r, g, b)
    return h * 360.0, s * 100.0


def _karis(a, b, k):
    dh = ((b[0] - a[0] + 180.0) % 360.0) - 180.0
    ha = a[0]
    if a[1] < 1.0:
        ha, dh = b[0], 0.0
    elif b[1] < 1.0:
        dh = 0.0
    return ((ha + dh * k) % 360.0, a[1] + (b[1] - a[1]) * k)


def hiz_carpani(hiz):
    """Efekt karti hiz surgusu (1-100) -> zaman carpani; 50 = 1x."""
    from effects import periyot
    return periyot(50) / periyot(hiz)


def an(e, t):
    """Tanim + zaman (sn) -> (h 0-360, s 0-100, v 0-100)."""
    renkler = [_hsv(c) for c in e["renkler"]]
    n = len(renkler)
    rh, rs = e["renk_hareket"], e["renk_sure"]
    if rh == "akis" and n > 1:
        q = t / rs
        i = int(math.floor(q)) % n
        f = q - math.floor(q)
        h, s = _karis(renkler[i], renkler[(i + 1) % n], f * f * (3 - 2 * f))
    elif rh == "atlama" and n > 1:
        h, s = renkler[int(math.floor(t / rs)) % n]
    elif rh == "gokkusagi":
        h0, s0 = renkler[0]
        h, s = (h0 + 360.0 * t / rs) % 360.0, (s0 if s0 >= 10 else 100.0)
    else:
        h, s = renkler[0]
    if e["titresim"] > 0:
        h = (h + e["titresim"] * (2.0 * _gurultu(t / 0.35, 17.0) - 1.0)) % 360.0
    ph, ps = e["parlak_hareket"], e["parlak_sure"]
    p = (t / ps) % 1.0
    if ph == "nefes":
        k = 0.5 - 0.5 * math.cos(2 * math.pi * p)
    elif ph == "nabiz":
        k = p / 0.12 if p < 0.12 else (1.0 - (p - 0.12) / 0.88) ** 2.2
    elif ph == "flas":
        k = 1.0 if p < 0.5 else 0.0
    elif ph == "titreme":
        k = _gurultu(t / ps, 3.0)
    elif ph == "testere":
        k = p
    else:
        k = 1.0
    v = e["min_v"] + (e["max_v"] - e["min_v"]) * k
    return h, s, v
