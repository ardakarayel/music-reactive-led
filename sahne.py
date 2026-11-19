"""Kullanici sahneleri: adimlardan olusan renk akisi (Arda, 2026-11-19: "sahneler kismi icin kendim
olusturabilecegim ortam").

Bir sahne adim listesidir. Her adim: renk (0xRRGGBB), parlaklik v (0-100), gecis (sn) ve gecis tipi
("yumusak" | "ani"), bekle (sn). Adima gecis once yapilir (onceki adimin renginden bu adimin rengine),
sonra adim bekle kadar sabit kalir. Dongu aciksa son adimdan ilk adima gecilip basa donulur; kapaliysa
son renkte kalinir. Yumusak gecis HSV'de en kisa ton yolundan gider (kirmizidan maviye mor uzerinden,
gri/karanlik bir ara renge dusmeden).
"""
import colorsys

ADIM_MAX = 12
BEKLE_MAX = 600.0
GECIS_MAX = 60.0
TIPLER = ("yumusak", "ani")


def _sayi(x, lo, hi, vars):
    try:
        d = float(x)
    except (TypeError, ValueError):
        return vars
    if d != d or d in (float("inf"), float("-inf")):
        return vars
    return max(lo, min(hi, d))


def adim_temizle(a):
    """Tek adim sozlugu -> temiz adim ya da None."""
    if not isinstance(a, dict):
        return None
    renk = a.get("renk")
    if isinstance(renk, str) and renk.startswith("#") and len(renk) == 7:
        try:
            renk = int(renk[1:], 16)
        except ValueError:
            return None
    if isinstance(renk, bool) or not isinstance(renk, (int, float)):
        return None
    renk = int(renk)
    if not 0 <= renk <= 0xFFFFFF:
        return None
    tip = a.get("tip") if a.get("tip") in TIPLER else "yumusak"
    return {"renk": renk, "v": round(_sayi(a.get("v"), 0, 100, 70), 1),
            "bekle": round(_sayi(a.get("bekle"), 0, BEKLE_MAX, 2), 2),
            "gecis": round(_sayi(a.get("gecis"), 0, GECIS_MAX, 1), 2), "tip": tip}


def adimlar_temizle(liste):
    if not isinstance(liste, list):
        return []
    out = []
    for a in liste[:ADIM_MAX]:
        t = adim_temizle(a)
        if t is not None:
            out.append(t)
    return out


def renk_hsv(renk, v):
    """0xRRGGBB + parlaklik (1-100) -> (h 0-360, s 0-100, v 0-100). Rengin kendi parlakligi yok sayilir."""
    r, g, b = ((renk >> 16) & 255) / 255.0, ((renk >> 8) & 255) / 255.0, (renk & 255) / 255.0
    h, s, _ = colorsys.rgb_to_hsv(r, g, b)
    return h * 360.0, s * 100.0, float(v)


def hsv_adim(h, s, v):
    """Eski tek renkli sahne (h, s, v) -> tek adim."""
    r, g, b = colorsys.hsv_to_rgb((float(h) % 360.0) / 360.0, max(0.0, min(100.0, float(s))) / 100.0, 1.0)
    renk = (round(r * 255) << 16) | (round(g * 255) << 8) | round(b * 255)
    return {"renk": renk, "v": max(1.0, min(100.0, float(v))), "bekle": 5.0, "gecis": 0.0, "tip": "ani"}


def _karisim(a, b, k):
    """Iki (h, s, v) arasinda k (0-1) oraninda, ton en kisa yoldan."""
    dh = ((b[0] - a[0] + 180.0) % 360.0) - 180.0
    # renksize (beyaz/gri) ya da renksizden geciste ton atlamasin: renkli olanin tonu kullanilir
    ha, hb = a[0], b[0]
    if a[1] < 1.0:
        ha, dh = hb, 0.0
    elif b[1] < 1.0:
        dh = 0.0
    return ((ha + dh * k) % 360.0, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)


def sure(adimlar, dongu):
    """Bir turun toplam suresi (sn). Dongu kapaliyken ilk adimin gecisi yok sayilir."""
    top = 0.0
    for i, a in enumerate(adimlar):
        if i == 0 and not dongu:
            top += a["bekle"]
        else:
            top += (a["gecis"] if a["tip"] == "yumusak" else 0.0) + a["bekle"]
    return top


def an(adimlar, dongu, t):
    """t saniyedeki (h, s, v). Adim yoksa None."""
    n = len(adimlar)
    if n == 0:
        return None
    hsv = [renk_hsv(a["renk"], a["v"]) for a in adimlar]
    if n == 1:
        return hsv[0]
    tur = sure(adimlar, dongu)
    if tur <= 1e-6:
        return hsv[0]
    if dongu:
        t = t % tur
    elif t >= tur:
        return hsv[-1]
    t = max(0.0, t)
    for i, a in enumerate(adimlar):
        gecis = 0.0 if (i == 0 and not dongu) or a["tip"] == "ani" else a["gecis"]
        if t < gecis:
            onceki = hsv[i - 1]   # i == 0 ve dongu: son adimdan (hsv[-1])
            return _karisim(onceki, hsv[i], t / gecis)
        t -= gecis
        if t < a["bekle"]:
            return hsv[i]
        t -= a["bekle"]
    return hsv[-1]
