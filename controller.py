"""Api sinifi ve durum mantigi (pywebview js_api).

Kilitler: _dev_lock (cihaz/guc sirasi) -> _lock (durum). _lock altinda cihaza
ya da engine'in gonderim yoluna girilmez. Tum genel metotlar get_state() sozlugu
doner; girdi bozuksa durum degismez, istisna disari cikmaz.
"""
import json
import math
import os
import threading
import time
import uuid

from device import MockDevice, TuyaDevice
from effects import EFFECT_NAMES, EFFECTS, Engine, hsv_to_rgb, render
from music import Music
import sahne as sahne_mod
import efekt as efekt_mod

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SCENES_PATH = os.path.join(BASE_DIR, "scenes.json")
EFEKTLER_PATH = os.path.join(BASE_DIR, "efektler.json")   # kullanici efektleri
EFEKT_ONIZLEME = "__onizleme__"
AYAR_PATH = os.path.join(BASE_DIR, "ayarlar.json")
MODLAR_PATH = os.path.join(BASE_DIR, "modlar.json")   # laboratuvar modlari
DISARI_DIR = os.path.join(BASE_DIR, "disari")          # disa aktarilan modlar

YERLESIK = [
    {"id": "gece", "name": "Gece", "h": 25, "s": 90, "v": 15},
    {"id": "film", "name": "Film", "h": 230, "s": 80, "v": 25},
    {"id": "odak", "name": "Odak", "h": 0, "s": 0, "v": 100},
    {"id": "gun-batimi", "name": "Gün batımı", "h": 15, "s": 95, "v": 70},
    {"id": "okyanus", "name": "Okyanus", "h": 195, "s": 90, "v": 70},
    {"id": "orman", "name": "Orman", "h": 130, "s": 80, "v": 60},
]
YERLESIK_IDLER = {s["id"] for s in YERLESIK}
BASIT_STILLER = ("pulse", "spectrum")
V2_STILLER = ("parti", "konser", "chill")
DIONYSOS_STIL = "dionysos"   # v3: ayri isik motoru (analiz.DionysosMotoru), kendi rengini secer
MUZIK_STILLERI = BASIT_STILLER + V2_STILLER + (DIONYSOS_STIL,)
V2_VARSAYILAN = (1.0, 1.0, 90.0)   # vokal_agirlik, yogunluk, gecikme_ms
DIO_KAYMA_VARSAYILAN = 4.0          # Dionysos vurusta renk kaymasi (derece)
OZEL_STIL = "ozel"                  # laboratuvar modlari: "ozel" (taslak) ya da "ozel:<id>" (kayitli mod)
ZAMANLAYICI_MAX_DK = 1440
SAHNE_AD_MAX = 40


def _sayi(x, lo, hi, varsayilan):
    try:
        d = float(x)
    except (TypeError, ValueError):
        return varsayilan
    if not math.isfinite(d):
        return varsayilan
    return max(lo, min(hi, d))


def _dionysos_stili(stil):
    """Dionysos motoruyla calisan stiller: dionysos ve laboratuvar modlari (ozel, ozel:<id>)."""
    return stil == DIONYSOS_STIL or (isinstance(stil, str) and (stil == OZEL_STIL or stil.startswith(OZEL_STIL + ":")))


def _cihaz_yukle(yol):
    """(cihaz, uyari). config.json yoksa ya da alanlar bossa MockDevice."""
    try:
        with open(yol, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return MockDevice(), ""
    except (OSError, ValueError) as e:
        return MockDevice(), f"config.json okunamadı ({type(e).__name__})"
    if not isinstance(cfg, dict):
        return MockDevice(), "config.json geçersiz"
    dev_id, ip, key = (str(cfg.get(k) or "").strip() for k in ("device_id", "ip", "local_key"))
    if not (dev_id and ip and key):
        return MockDevice(), ""
    version = str(cfg.get("version") or "3.3").strip()
    name = str(cfg.get("name") or "LED Şerit").strip() or "LED Şerit"
    return TuyaDevice(dev_id, ip, key, version, name), ""


def _sahneleri_yukle(yol):
    try:
        with open(yol, encoding="utf-8") as f:
            veri = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(veri, list):
        return []
    temiz = []
    for s in veri:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or s["id"] in YERLESIK_IDLER:
            continue
        try:
            adimlar = sahne_mod.adimlar_temizle(s.get("adimlar"))
            if not adimlar:   # eski bicim: tek renk (h, s, v)
                adimlar = [sahne_mod.hsv_adim(s["h"], s["s"], s["v"])]
            temiz.append(_sahne_kaydi(s["id"], s["name"], adimlar, bool(s.get("dongu", True))))
        except (KeyError, TypeError, ValueError):
            continue
    return temiz


def _efektleri_yukle(yol):
    try:
        with open(yol, encoding="utf-8") as f:
            veri = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for e in veri if isinstance(veri, list) else []:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str):
            continue
        tanim = efekt_mod.temizle(e.get("tanim"))
        if tanim is None:
            continue
        out.append({"id": e["id"], "ad": str(e.get("ad") or "Efekt")[:SAHNE_AD_MAX], "tanim": tanim})
    return out[:efekt_mod.EFEKT_MAX]


def _sahne_kaydi(sid, ad, adimlar, dongu):
    """Kullanici sahnesi: adimlar + dongu; h, s, v ilk adimdan (kart onizlemesi ve eski arayuz icin)."""
    h, s, v = sahne_mod.renk_hsv(adimlar[0]["renk"], adimlar[0]["v"])
    return {"id": sid, "name": str(ad)[:SAHNE_AD_MAX], "adimlar": adimlar, "dongu": bool(dongu),
            "h": float(round(h)), "s": float(round(s)), "v": float(round(v))}


def _sahneleri_yaz(yol, liste):
    tmp = yol + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False, indent=2)
    os.replace(tmp, yol)


class Api:
    def __init__(self, config_path=CONFIG_PATH, scenes_path=SCENES_PATH, device=None,
                 modlar_path=MODLAR_PATH, disari_dir=DISARI_DIR):
        self._lock = threading.Lock()
        self._dev_lock = threading.Lock()
        self._scene_lock = threading.Lock()
        self._scenes_path = scenes_path
        self._modlar_yolu, self._disari_dir = modlar_path, disari_dir
        self._depo_nesne = None      # lab.ModDeposu (tembel: analiz paketi/numpy yoksa Basit stiller etkilenmez)
        self._lab_gecici = None      # {"stil", "tam"}: lab_baslat in tek kullanimlik parametreleri
        self._lab_taslak = None      # son "ozel" (kaydedilmemis) mod icin tam parametreler
        self._kullanildi = False
        self._power = False
        self._mode = "colour"
        self._h, self._s, self._v = 0.0, 0.0, 100.0
        self._effect = None
        self._effect_speed = 50.0
        self._music_sens = 50.0
        self._music_style = "pulse"
        self._timer = None
        self._timer_token = None
        self._timer_end = None
        self._scenes = _sahneleri_yukle(scenes_path)
        self._sahne = None           # calan cok adimli sahne: {"id", "adimlar", "dongu", "t0"}
        self._onizleme_onceki = None  # sahne/efekt duzenleyici onizlemesinden once durum (geri donmek icin)
        self._efektler_yolu = EFEKTLER_PATH if scenes_path == SCENES_PATH else os.path.join(
            os.path.dirname(scenes_path) or ".", "efektler.json")
        self._efektler = _efektleri_yukle(self._efektler_yolu)
        self._efekt_t0 = time.monotonic()
        self._efekt_onizleme = None   # duzenleyicide denenen tanim
        if device is None:
            device, self._uyari = _cihaz_yukle(config_path)
        else:
            self._uyari = ""
        self._device = device
        self._music = Music()
        self._demo = False   # laboratuvar demo modu (seride gonderme yok)
        try:
            from medya import Medya
            self._medya = Medya()
        except Exception:
            self._medya = None
        self._send_hz = 16
        self._spec_shift = 12.0     # spektrum: vurus basina ton kaymasi (derece)
        self._spec_smooth = 0.4     # spektrum: ton yumusakligi (sn)
        self._vokal_agirlik, self._yogunluk, self._gecikme_ms = V2_VARSAYILAN   # muzik v2
        self._dio_kayma = DIO_KAYMA_VARSAYILAN   # Dionysos: vurusta renk kaymasi (derece)
        self._v2_taban = None       # Boru'ya son bildirilen taban_ton
        try:
            with open(AYAR_PATH, encoding="utf-8") as f:
                ayar = json.load(f)
            self._send_hz = int(_sayi(ayar.get("send_hz"), 4, 20, 16))
            self._spec_shift = float(_sayi(ayar.get("spec_shift"), 0, 60, 12.0))
            self._spec_smooth = float(_sayi(ayar.get("spec_smooth"), 0.05, 2.0, 0.4))
            self._vokal_agirlik = float(_sayi(ayar.get("vokal_agirlik"), 0, 2, V2_VARSAYILAN[0]))
            self._dio_kayma = float(_sayi(ayar.get("dio_kayma"), 0, 60, DIO_KAYMA_VARSAYILAN))
            self._yogunluk = float(_sayi(ayar.get("yogunluk"), 0.3, 2, V2_VARSAYILAN[1]))
            self._gecikme_ms = float(_sayi(ayar.get("gecikme_ms"), 0, 300, V2_VARSAYILAN[2]))
        except Exception:
            pass
        self._send_hz_temel = self._send_hz   # kullanicinin kayitli gonderim hizi (lab gecici degistirebilir)
        self._music.ratio_tau = self._spec_smooth
        self._engine = Engine(self._render, self._send)
        self._engine.gonderme_sn = 1.0 / self._send_hz
        self._engine.start()
        threading.Thread(target=self._boot, daemon=True).start()

    # ----- arka plan -----

    def _boot(self):
        """Cihaza baglanir; kullanici henuz bir sey yapmadiysa cihazin durumunu alir."""
        dev = self._device
        ok = dev.connect()
        dev.start_watch()
        if not ok or not dev.initial:
            return
        with self._dev_lock:
            with self._lock:
                if self._kullanildi:
                    return
                hsv = dev.initial.get("hsv")
                if hsv:
                    self._h, self._s = hsv[0], hsv[1]
                    self._v = max(1.0, hsv[2])
                if dev.initial.get("power") is not None:
                    self._power = bool(dev.initial["power"])
                self._engine.force()

    def _render(self, t):
        with self._lock:
            if not self._power:
                return None
            mode, h, s, v = self._mode, self._h, self._s, self._v
            effect, speed = self._effect, self._effect_speed
            style = self._music_style
            tanim = None
            if mode == "effect" and effect and effect not in EFFECT_NAMES:
                tanim = self._efekt_tanim(effect)
            t0 = self._efekt_t0
        if mode == "effect" and tanim is not None:
            # onizlemede hiz carpani 1 (duzenleyicideki gibi); kayitli efektte efekt kartindaki hiz surgusu
            k = 1.0 if effect == EFEKT_ONIZLEME else efekt_mod.hiz_carpani(speed)
            eh, es, ev = efekt_mod.an(tanim, (t - t0) * k)
            return hsv_to_rgb(eh, es, ev * v / 100.0), 0.0
        if mode == "effect" and effect:
            return render(effect, t, speed, h, s, v), 0.0
        if mode == "scene":
            sc = self._sahne
            an = sahne_mod.an(sc["adimlar"], sc["dongu"], t - sc["t0"]) if sc else None
            if an is None:
                return hsv_to_rgb(h, s, v), 0.0
            # ana parlaklik surgusu sahnenin parlakligini oranla kisar (sahne acilinca %100)
            return hsv_to_rgb(an[0], an[1], an[2] * v / 100.0), 0.0
        if mode == "music":
            if style in V2_STILLER:
                return self._render_v2(h, s, v)
            if _dionysos_stili(style):
                return self._render_dionysos(v)
            level, ratio = self._music.values()
            if style == "spectrum":
                ton = (15.0 + 200.0 * ratio + self._spec_shift * self._music.beats()) % 360.0
                return hsv_to_rgb(ton, 90, v * level), level
            return hsv_to_rgb(h, s, v * min(1.0, 0.08 + level)), level
        return hsv_to_rgb(h, s, v), 0.0

    def _render_v2(self, h, s, v):
        """Muzik v2: IsikKare (h, s, v) + kullanici parlakligi tavan, kullanici tonu taban_ton."""
        if self._v2_taban != h:
            self._v2_taban = h
            self._music.ayarla_v2(taban_ton=h)
        kare = self._music.v2_kare()
        if kare is None:
            return hsv_to_rgb(h, s, 0.0), 0.0
        kh, ks, kv = kare
        return hsv_to_rgb(kh, ks * 100.0, kv * v), kv

    def _render_dionysos(self, v):
        """Dionysos: IsikKare (h, s, v); renk motorun kendi secimi, kullanici yalniz parlaklik tavani verir."""
        kare = self._music.v2_kare()
        if kare is None:
            return hsv_to_rgb(0, 0, 0.0), 0.0
        kh, ks, kv = kare
        return hsv_to_rgb(kh, ks * 100.0, kv * v), kv

    def _send(self, rgb):
        with self._dev_lock:
            with self._lock:
                on = self._power
            if not on:
                return True
            if self._demo:
                return True   # Demo modu: renk yalniz ekranda, seride gitmez (Arda, 2026-11-19)
            return self._device.set_rgb(*rgb)

    def medya_komut(self, ad):
        """Calan uygulamaya (Spotify, YouTube...) komut: "oynat_durdur" | "sonraki" | "onceki"."""
        ok = False
        if self._medya is not None and ad in ("oynat_durdur", "sonraki", "onceki"):
            ok = self._medya.komut(ad)
        durum = self.get_state()
        durum["medya_ok"] = ok
        return durum

    def set_lab_demo(self, acik):
        """Laboratuvar demo modu: acikken renkler seride gonderilmez, yalniz panelde gorunur.
        Kapatilinca o anki renk hemen seride gider."""
        self._demo = bool(acik)
        if not self._demo:
            self._engine.force()
        return self.get_state()

    def _guncelle(self, fn, power=None):
        """Durumu fn ile degistirir; power verilirse guc de ayarlanir (cihaz yalniz degisince cagrilir)."""
        with self._dev_lock:
            with self._lock:
                self._kullanildi = True
                fn()
                eski = self._power
                if power is not None:
                    self._power = power
                self._engine.force()
            if power is not None and power != eski:
                self._device.set_power(power)

    def _zamanlayici_bitti(self, token):
        with self._lock:
            if token is not self._timer_token:
                return
            self._timer_token = None
            self._timer_end = None
            self._timer = None
        self._guncelle(lambda: None, power=False)

    def _efekt_tanim(self, ad):
        """self._lock altinda: "ozel:<id>" ya da onizleme adi -> tanim ya da None."""
        if ad == EFEKT_ONIZLEME:
            return self._efekt_onizleme
        if isinstance(ad, str) and ad.startswith("ozel:"):
            eid = ad[5:]
            for e in self._efektler:
                if e["id"] == eid:
                    return e["tanim"]
        return None

    def _sahne_bul(self, sid):
        for s in YERLESIK:
            if s["id"] == sid:
                return s
        with self._lock:
            for s in self._scenes:
                if s["id"] == sid:
                    return dict(s)
        return None

    def _kapat(self):
        """Uygulama cikisinda: engine, muzik, zamanlayici ve cihaz kapanir (sekil korunur)."""
        self._engine.stop()
        self._music.stop()
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = None
            self._timer_token = None
        self._device.close()

    # ----- Api sozlesmesi -----

    def get_state(self):
        dev = self._device
        with self._lock:
            kalan = None
            if self._timer_end is not None:
                kalan = max(0, int(self._timer_end - time.monotonic()))
                if kalan == 0:
                    kalan = None
            eff = {"name": self._effect, "speed": int(round(self._effect_speed))} if self._effect else None
            kullanici = [{"id": s["id"], "name": s["name"], "builtin": False,
                          "h": int(round(s["h"])), "s": int(round(s["s"])), "v": int(round(s["v"])),
                          "adimlar": [dict(a) for a in s["adimlar"]], "dongu": s["dongu"]}
                         for s in self._scenes]
            sahne_aktif = self._sahne["id"] if (self._mode == "scene" and self._sahne) else None
            ozel_efektler = [{"name": "ozel:" + e["id"], "label": e["ad"], "ozel": True, "tanim": dict(e["tanim"])}
                             for e in self._efektler]
            durum = {
                "power": self._power,
                "mode": self._mode,
                "h": int(round(self._h)) % 360,
                "s": int(round(self._s)),
                "v": int(round(self._v)),
                "effect": eff,
                "music_sens": int(round(self._music_sens)),
                "music_style": self._music_style,
                "timer_remaining": kalan,
                "sahne_aktif": sahne_aktif,
                "ozel_efektler": ozel_efektler,
            }
            vokal_agirlik, yogunluk, gecikme_ms = self._vokal_agirlik, self._yogunluk, self._gecikme_ms
        m = self._music
        return {
            "connected": bool(dev.connected),
            "mock": bool(dev.mock),
            "error": dev.error or self._uyari,
            "device_name": dev.name,
            "power": durum["power"],
            "mode": durum["mode"],
            "h": durum["h"],
            "s": durum["s"],
            "v": durum["v"],
            "effect": durum["effect"],
            "music": {"active": bool(m.active), "sensitivity": durum["music_sens"],
                      "style": durum["music_style"], "error": m.error,
                      "vokal_agirlik": vokal_agirlik, "yogunluk": yogunluk, "gecikme_ms": gecikme_ms,
                      "dio_kayma": self._dio_kayma,
                      "ozel_modlar": self._ozel_modlar(),
                      "ozel_id": (durum["music_style"].split(":", 1)[1] if durum["music_style"].startswith(OZEL_STIL + ":") else None),
                      "analiz": m.v2_ozet() if m.active else None,
                      "parca": self._medya.parca() if self._medya is not None else None},
            "timer_remaining": durum["timer_remaining"],
            "scenes": [dict(s, builtin=True) for s in YERLESIK] + kullanici,
            "sahne_aktif": durum.get("sahne_aktif"),
            "sahne_onizleme": self._onizleme_onceki is not None,
            "effects": [dict(e) for e in EFFECTS] + durum["ozel_efektler"],
            "efekt_max": efekt_mod.EFEKT_MAX,
            "send_hz": self._send_hz,
            "lab_demo": self._demo,
            "spec_shift": self._spec_shift,
            "spec_smooth": self._spec_smooth,
        }

    def get_viz(self):
        """Dionysos gorsellestirme verisi (son_viz); muzik kapaliysa ya da baska stildeyse None."""
        m = self._music
        return m.viz() if m.active else None

    def get_lab_viz(self):
        """Laboratuvar ekolayzir verisi (get_viz alanlari + spektrum, seviye, cizgiler); muzik kapaliysa None."""
        m = self._music
        if not m.active:
            return None
        try:
            return m.lab_viz()
        except Exception:
            return None

    def get_frame(self):
        rgb, level = self._engine.frame()
        return {"color": "#%02x%02x%02x" % tuple(rgb), "level": round(float(level), 3)}

    def set_power(self, on):
        self._guncelle(lambda: None, power=bool(on))
        return self.get_state()

    def set_color(self, h, s, v):
        self._music.stop()

        def f():
            self._mode = "colour"
            self._effect = None
            self._h = _sayi(h, 0, 360, self._h)
            self._s = _sayi(s, 0, 100, self._s)
            self._v = _sayi(v, 1, 100, self._v)
        self._guncelle(f, power=True)
        return self.get_state()

    def set_brightness(self, v):
        def f():
            self._v = _sayi(v, 1, 100, self._v)
        self._guncelle(f)
        return self.get_state()

    def start_effect(self, name, speed):
        if not isinstance(name, str):
            return self.get_state()
        if name not in EFFECT_NAMES:
            with self._lock:
                bilinen = name.startswith("ozel:") and self._efekt_tanim(name) is not None
            if not bilinen:
                return self.get_state()
        self._music.stop()
        self._onizleme_onceki = None

        def f():
            self._mode = "effect"
            if self._effect != name:
                self._efekt_t0 = time.monotonic()
            self._effect = name
            self._effect_speed = _sayi(speed, 1, 100, self._effect_speed)
        self._guncelle(f, power=True)
        return self.get_state()

    def stop_effect(self):
        def f():
            if self._mode == "effect":
                self._mode = "colour"
                self._effect = None
        self._guncelle(f)
        return self.get_state()

    def apply_scene(self, sid):
        sc = self._sahne_bul(sid)
        if sc is None:
            return self.get_state()
        self._music.stop()
        self._onizleme_onceki = None
        adimlar = sc.get("adimlar") or []
        if len(adimlar) >= 2:
            self._sahne_oynat(sid, adimlar, sc.get("dongu", True))
            return self.get_state()
        if adimlar:
            h, s, v = sahne_mod.renk_hsv(adimlar[0]["renk"], adimlar[0]["v"])
        else:
            h, s, v = sc["h"], sc["s"], sc["v"]

        def f():
            self._mode = "colour"
            self._effect = None
            self._sahne = None
            self._h, self._s, self._v = float(h), float(s), max(1.0, float(v))
        self._guncelle(f, power=True)
        return self.get_state()

    def _sahne_oynat(self, sid, adimlar, dongu):
        def f():
            self._mode = "scene"
            self._effect = None
            self._sahne = {"id": sid, "adimlar": adimlar, "dongu": bool(dongu), "t0": time.monotonic()}
            self._v = 100.0
        self._guncelle(f, power=True)

    # ----- sahne duzenleyici -----

    def sahne_kaydet(self, sid, ad, adimlar, dongu=True):
        """Yeni sahne (sid bos/None) ya da var olani gunceller. Donus: durum + "sahne_id" (hata: "sahne_hata")."""
        ad = str(ad or "").strip()[:SAHNE_AD_MAX]
        temiz = sahne_mod.adimlar_temizle(adimlar)
        if not ad or not temiz:
            durum = self.get_state()
            durum["sahne_hata"] = "Ad ve en az bir adım gerekli."
            return durum
        if isinstance(sid, str) and sid in YERLESIK_IDLER:
            sid = None
        with self._scene_lock:
            with self._lock:
                yeni_id = sid if isinstance(sid, str) and any(s["id"] == sid for s in self._scenes) else uuid.uuid4().hex[:8]
                kayit = _sahne_kaydi(yeni_id, ad, temiz, dongu)
                self._scenes = [kayit if s["id"] == yeni_id else s for s in self._scenes]
                if not any(s["id"] == yeni_id for s in self._scenes):
                    self._scenes.append(kayit)
                liste = [dict(s) for s in self._scenes]
                if self._sahne and self._sahne["id"] == yeni_id:
                    self._sahne = dict(self._sahne, adimlar=temiz, dongu=bool(dongu))
            self._sahne_kaydet(liste)
        durum = self.get_state()
        durum["sahne_id"] = yeni_id
        return durum

    def sahne_onizle(self, adimlar, dongu=True, bastan=False):
        """Duzenleyicide canli deneme: adimlar seritte (Demo acikken yalniz ekranda) oynar.
        Ilk cagrida onceki durum saklanir; sahne_onizle_bitir(True) ona doner."""
        temiz = sahne_mod.adimlar_temizle(adimlar)
        if not temiz:
            return self.get_state()
        self._onizleme_sakla()
        with self._lock:
            ayni = (self._mode == "scene" and self._sahne and self._sahne["id"] == "__onizleme__"
                    and not bastan)
            t0 = self._sahne["t0"] if ayni else time.monotonic()

        def f():
            self._mode = "scene"
            self._effect = None
            self._sahne = {"id": "__onizleme__", "adimlar": temiz, "dongu": bool(dongu), "t0": t0}
            self._v = 100.0
        self._guncelle(f, power=True)
        return self.get_state()

    def _onizleme_sakla(self):
        """Ilk onizlemede onceki durumu saklar (sahne ve efekt duzenleyici ortak)."""
        if self._onizleme_onceki is None:
            with self._lock:
                self._onizleme_onceki = {"mode": self._mode, "h": self._h, "s": self._s, "v": self._v,
                                         "effect": self._effect, "speed": self._effect_speed,
                                         "sahne": self._sahne, "power": self._power, "t0": self._efekt_t0}
            self._music.stop()

    # ----- efekt duzenleyici -----

    def efekt_kaydet(self, eid, ad, tanim):
        """Yeni efekt (eid bos) ya da var olani gunceller. Donus: durum + "efekt_id" (hata: "efekt_hata")."""
        ad = str(ad or "").strip()[:SAHNE_AD_MAX]
        temiz = efekt_mod.temizle(tanim)
        if not ad or temiz is None:
            durum = self.get_state()
            durum["efekt_hata"] = "Ad ve en az bir renk gerekli."
            return durum
        with self._scene_lock:
            with self._lock:
                var = isinstance(eid, str) and any(e["id"] == eid for e in self._efektler)
                if not var and len(self._efektler) >= efekt_mod.EFEKT_MAX:
                    durum_hata = "En çok %d efekt kaydedilebilir." % efekt_mod.EFEKT_MAX
                else:
                    durum_hata = None
                    yeni_id = eid if var else uuid.uuid4().hex[:8]
                    kayit = {"id": yeni_id, "ad": ad, "tanim": temiz}
                    if var:
                        self._efektler = [kayit if e["id"] == yeni_id else e for e in self._efektler]
                    else:
                        self._efektler.append(kayit)
                    liste = [dict(e) for e in self._efektler]
            if durum_hata is None:
                self._efekt_dosya_yaz(liste)
        durum = self.get_state()
        if durum_hata:
            durum["efekt_hata"] = durum_hata
        else:
            durum["efekt_id"] = yeni_id
        return durum

    def efekt_sil(self, eid):
        with self._scene_lock:
            with self._lock:
                kalan = [e for e in self._efektler if e["id"] != eid]
                degisti = len(kalan) != len(self._efektler)
                self._efektler = kalan
                if self._mode == "effect" and self._effect == "ozel:" + str(eid):
                    self._mode = "colour"
                    self._effect = None
                    self._engine.force()
                liste = [dict(e) for e in kalan]
            if degisti:
                self._efekt_dosya_yaz(liste)
        return self.get_state()

    def _efekt_dosya_yaz(self, liste):
        try:
            _sahneleri_yaz(self._efektler_yolu, liste)
        except OSError as e:
            self._uyari = f"efektler.json yazılamadı ({type(e).__name__})"

    def efekt_onizle(self, tanim, bastan=False):
        """Duzenleyicide canli deneme (sahne_onizle gibi); bitirmek icin sahne_onizle_bitir / efekt_onizle_bitir."""
        temiz = efekt_mod.temizle(tanim)
        if temiz is None:
            return self.get_state()
        self._onizleme_sakla()

        def f():
            if not (self._mode == "effect" and self._effect == EFEKT_ONIZLEME) or bastan:
                self._efekt_t0 = time.monotonic()
            self._mode = "effect"
            self._effect = EFEKT_ONIZLEME
            self._efekt_onizleme = temiz
            self._sahne = None
            self._v = 100.0
        self._guncelle(f, power=True)
        return self.get_state()

    def efekt_onizle_bitir(self, geri_don=True):
        return self.sahne_onizle_bitir(geri_don)

    def sahne_onizle_bitir(self, geri_don=True):
        """Onizlemeyi bitirir. geri_don: duzenleyiciden onceki renge/efekte doner (muzik acikti ise rengine)."""
        once = self._onizleme_onceki
        self._onizleme_onceki = None
        if once is None:
            return self.get_state()
        if not geri_don:
            return self.get_state()

        def f():
            self._efekt_onizleme = None if self._effect != EFEKT_ONIZLEME else self._efekt_onizleme
            mode = once["mode"]
            if mode == "music":
                mode = "colour"
            if mode == "scene" and not once["sahne"]:
                mode = "colour"
            self._mode = mode
            self._h, self._s, self._v = once["h"], once["s"], once["v"]
            self._effect = once["effect"] if mode == "effect" else None
            if mode == "effect" and self._effect == EFEKT_ONIZLEME:
                self._mode, self._effect = "colour", None
            self._effect_speed = once.get("speed", self._effect_speed)
            self._efekt_t0 = once.get("t0", self._efekt_t0)
            self._sahne = once["sahne"] if self._mode == "scene" else None
        self._guncelle(f, power=once["power"])
        return self.get_state()

    def save_scene(self, name):
        ad = str(name or "").strip()[:SAHNE_AD_MAX]
        if not ad:
            return self.get_state()
        with self._scene_lock:
            with self._lock:
                self._scenes.append(_sahne_kaydi(uuid.uuid4().hex[:8], ad,
                                                 [sahne_mod.hsv_adim(self._h, self._s, self._v)], True))
                liste = list(self._scenes)
            self._sahne_kaydet(liste)
        return self.get_state()

    def delete_scene(self, sid):
        with self._scene_lock:
            with self._lock:
                kalan = [s for s in self._scenes if s["id"] != sid]
                degisti = len(kalan) != len(self._scenes)
                self._scenes = kalan
                if self._mode == "scene" and self._sahne and self._sahne["id"] == sid:
                    self._mode = "colour"
                    self._sahne = None
                    self._engine.force()
            if degisti:
                self._sahne_kaydet(kalan)
        return self.get_state()

    def _sahne_kaydet(self, liste):
        try:
            _sahneleri_yaz(self._scenes_path, liste)
        except OSError as e:
            self._uyari = f"scenes.json yazılamadı ({type(e).__name__})"

    def start_music(self, sensitivity, style):
        ozel = None
        if isinstance(style, str) and style != DIONYSOS_STIL and _dionysos_stili(style):
            ozel = self._ozel_coz(style)
        stil = style
        if ozel is None and stil not in MUZIK_STILLERI:
            # bilinmeyen stil: mevcut stil korunur (calisan lab modunun parametrelerine dokunulmaz)
            stil = self._music_style
            if stil != DIONYSOS_STIL and _dionysos_stili(stil):
                if not self._music.active:
                    ozel = self._ozel_coz(stil)
                    if ozel is None:
                        stil = DIONYSOS_STIL
            elif stil not in MUZIK_STILLERI:
                stil = DIONYSOS_STIL
        sens = _sayi(sensitivity, 1, 100, self._music_sens)
        lab_params = None
        if ozel is not None:
            lab = self._lab_modul()
            if "hassasiyet" in ozel:
                sens = _sayi(ozel["hassasiyet"], 1, 100, sens)
            if "send_hz" in ozel:
                self._lab_send_uygula(ozel["send_hz"])
            else:
                self._lab_send_geri()
            lab_params = lab.motor_params(ozel)
        else:
            self._lab_send_geri()
            if stil == DIONYSOS_STIL:
                # onceki laboratuvar modundan kalan parametreleri sifirla; panel ayarlari (vokal, renk kaymasi) korunur
                lab = self._lab_modul()
                if lab is not None:
                    lab_params = lab.varsayilan_motor()
                    lab_params["vokal_agirlik"] = self._vokal_agirlik
                    lab_params["renk_kayma"] = self._dio_kayma
        dio = _dionysos_stili(stil)
        with self._lock:
            self._music_sens = sens
            self._music_style = stil
        v2 = None
        if dio and self._music.active and self._music.v2_dionysos():
            # Dionysos zaten calisiyor: yalniz hassasiyet ve parametreler (analiz durumu korunur)
            self._music.ayarla_v2(hassasiyet=sens, lab_params=lab_params)

            def f3():
                self._mode = "music"
                self._effect = None
            self._guncelle(f3, power=True)
            return self.get_state()
        if (stil in V2_STILLER and self._music.active and self._music.v2_aktif()
                and not self._music.v2_dionysos()):
            # v2 zaten calisiyor: mod/hassasiyet yeniden baslatmadan (analiz durumu korunur)
            self._music.ayarla_v2(mod=stil, hassasiyet=sens)

            def f2():
                self._mode = "music"
                self._effect = None
            self._guncelle(f2, power=True)
            return self.get_state()
        if stil in V2_STILLER or dio:
            with self._lock:
                v2 = {"mod": DIONYSOS_STIL if dio else stil, "yogunluk": self._yogunluk,
                      "vokal_agirlik": self._vokal_agirlik, "gecikme_sn": self._gecikme_ms / 1000.0,
                      "taban_ton": self._h, "renk_kayma": self._dio_kayma}
                if ozel is not None:
                    v2["lab_params"] = lab_params
                self._v2_taban = self._h
        ok = self._music.start(sens, v2)
        if ok:
            def f():
                self._mode = "music"
                self._effect = None
            self._guncelle(f, power=True)
        return self.get_state()

    def stop_music(self):
        self._music.stop()
        self._lab_send_geri()

        def f():
            if self._mode == "music":
                self._mode = "colour"
        self._guncelle(f)
        return self.get_state()

    def set_music_sensitivity(self, x):
        with self._lock:
            self._music_sens = _sayi(x, 1, 100, self._music_sens)
            sens = self._music_sens
        self._music.set_sensitivity(sens)
        return self.get_state()

    def set_music_v2(self, vokal_agirlik, yogunluk, gecikme_ms):
        """Muzik v2: vokal agirligi (0-2), yogunluk (0.3-2), gecikme telafisi (0-300 ms).
        Calisan analize yeniden baslatmadan uygulanir; ayarlar.json'a kaydedilir."""
        with self._lock:
            self._vokal_agirlik = float(_sayi(vokal_agirlik, 0, 2, self._vokal_agirlik))
            self._yogunluk = float(_sayi(yogunluk, 0.3, 2, self._yogunluk))
            self._gecikme_ms = float(_sayi(gecikme_ms, 0, 300, self._gecikme_ms))
            va, yo, gm = self._vokal_agirlik, self._yogunluk, self._gecikme_ms
        self._music.ayarla_v2(yogunluk=yo, vokal_agirlik=va, gecikme_sn=gm / 1000.0)
        self._ayar_kaydet()
        return self.get_state()

    def set_dionysos_renk(self, derece):
        """Dionysos: vurusta renk kaymasi (0-60 derece). Calisan analize hemen uygulanir, kaydedilir."""
        with self._lock:
            self._dio_kayma = float(_sayi(derece, 0, 60, self._dio_kayma))
            dk = self._dio_kayma
        self._music.ayarla_v2(renk_kayma=dk)
        self._ayar_kaydet()
        return self.get_state()

    def set_music_v2_varsayilan(self):
        return self.set_music_v2(*V2_VARSAYILAN)

    def set_send_rate(self, hz):
        """Seride saniyede en cok kac renk gonderilecegi (4-20). ayarlar.json'a kaydedilir."""
        with self._lock:
            self._send_hz = int(round(_sayi(hz, 4, 20, self._send_hz)))
            self._send_hz_temel = self._send_hz
            deger = self._send_hz
        self._engine.gonderme_sn = 1.0 / deger
        self._ayar_kaydet()
        return self.get_state()

    def set_spectrum(self, shift, smooth):
        """Spektrum stili: vurus basina ton kaymasi (0-60 derece) ve ton yumusakligi (0.05-2 sn)."""
        with self._lock:
            self._spec_shift = float(_sayi(shift, 0, 60, self._spec_shift))
            self._spec_smooth = float(_sayi(smooth, 0.05, 2.0, self._spec_smooth))
        self._music.ratio_tau = self._spec_smooth
        self._ayar_kaydet()
        return self.get_state()

    # ----- laboratuvar -----

    def _lab_modul(self):
        try:
            from analiz import lab
            return lab
        except Exception:
            return None

    def _depo(self):
        if self._depo_nesne is None:
            lab = self._lab_modul()
            if lab is None:
                return None
            self._depo_nesne = lab.ModDeposu(self._modlar_yolu, self._disari_dir)
        return self._depo_nesne

    def _ozel_modlar(self):
        try:
            d = self._depo()
            return [{"id": m["id"], "ad": m["ad"]} for m in d.liste()] if d is not None else []
        except Exception:
            return []

    def _ozel_coz(self, stil):
        """"ozel" / "ozel:<id>" -> tam motor parametreleri (+ hassasiyet/send_hz varsa); bilinmiyorsa None.
        lab_baslat in gecici (kaydedilmemis) parametreleri bir kez kullanilir."""
        lab, depo = self._lab_modul(), self._depo()
        if lab is None or depo is None:
            return None
        g, self._lab_gecici = self._lab_gecici, None
        if g is not None and g["stil"] == stil:
            return g["tam"]
        if stil == OZEL_STIL:
            return dict(self._lab_taslak) if self._lab_taslak else lab.tam_params("dionysos")
        if stil.startswith(OZEL_STIL + ":"):
            mod = depo.getir(stil[len(OZEL_STIL) + 1:])
            if mod is not None:
                return lab.tam_params(mod["sablon"], mod["params"])
        return None

    def _lab_send_uygula(self, hz):
        with self._lock:
            self._send_hz = int(round(_sayi(hz, 4, 20, self._send_hz)))
            deger = self._send_hz
        self._engine.gonderme_sn = 1.0 / deger

    def _lab_send_geri(self):
        with self._lock:
            if self._send_hz == self._send_hz_temel:
                return
            self._send_hz = self._send_hz_temel
            deger = self._send_hz
        self._engine.gonderme_sn = 1.0 / deger

    def lab_param_listesi(self):
        """Parametre semasi (arayuz jenerik cizer): [{id, etiket, sekme, seviye, tip, min, max, adim, birim,
        varsayilan, aciklama, cizgi}]."""
        lab = self._lab_modul()
        return lab.parametre_listesi() if lab is not None else []

    def lab_modlar(self):
        d = self._depo()
        return d.liste() if d is not None else []

    def lab_mod_getir(self, mod_id):
        """Tam mod. params her zaman motorun TUM parametrelerini icerir (sablon + kayitli degerler), boylece
        lab_dene(params) calisan motoru eksiksiz bu degerlere dondurur. "dionysos"/"spektrum_gibi" sablon adlari
        da tam mod olarak doner (id = sablon adi)."""
        d, lab = self._depo(), self._lab_modul()
        if d is None or lab is None or not isinstance(mod_id, str):
            return None
        mod = d.getir(mod_id)
        if mod is None and mod_id in lab.SABLONLAR:
            mod = {"id": mod_id, "ad": mod_id, "sablon": mod_id, "params": {}, "olusturma": "", "guncelleme": ""}
        if mod is not None:
            mod["params"] = lab.tam_params(mod["sablon"], mod["params"])
        return mod

    def lab_mod_kaydet(self, mod_id, ad, sablon, params):
        """Modu kaydeder (mod_id None/bilinmiyorsa yeni). Basarida tam mod, hatada {"hata": ...}."""
        d = self._depo()
        if d is None:
            return {"hata": "Laboratuvar kullanılamıyor."}
        return d.kaydet(mod_id if isinstance(mod_id, str) else None, ad, sablon, params)

    def lab_mod_sil(self, mod_id):
        d = self._depo()
        ok = bool(d is not None and isinstance(mod_id, str) and d.sil(mod_id))
        if ok:
            with self._lock:
                if self._music_style == OZEL_STIL + ":" + mod_id:
                    self._music_style = OZEL_STIL   # calisan mod silinse de calismaya devam eder
        return {"ok": ok}

    def lab_mod_disa_aktar(self, mod_id):
        d = self._depo()
        if d is None or not isinstance(mod_id, str):
            return {"hata": "Mod bulunamadı."}
        return d.disa_aktar(mod_id)

    def lab_mod_ice_aktar(self, yol):
        d = self._depo()
        return d.ice_aktar(yol) if d is not None else {"hata": "Laboratuvar kullanılamıyor."}

    def lab_dene(self, params):
        """Calisan laboratuvar moduna parametreleri aninda uygular (kaydetmez). Muzik Dionysos/lab modunda
        calismiyorsa {"ok": False}."""
        lab = self._lab_modul()
        if lab is None:
            return {"ok": False, "neden": "Laboratuvar kullanılamıyor."}
        m = self._music
        if not (m.active and m.v2_dionysos()):
            return {"ok": False, "neden": "Müzik laboratuvar modunda çalışmıyor."}
        d = lab.dogrula(params)
        if "hassasiyet" in d:
            with self._lock:
                self._music_sens = _sayi(d["hassasiyet"], 1, 100, self._music_sens)
                sens = self._music_sens
            m.set_sensitivity(sens)
        if "send_hz" in d:
            self._lab_send_uygula(d["send_hz"])
        m.ayarla_v2(lab_params=lab.motor_params(d))
        return {"ok": True, "uygulanan": len(d)}

    def lab_baslat(self, sablon_ya_da_mod_id, params=None):
        """Muzigi laboratuvar ayarlariyla baslatir. Ilk arguman kayitli mod id'si ya da sablon adi; params
        (kaydedilmemis degisiklikler) ustune bindirilir. get_state() doner; basarisizsa state["lab"]["ok"] False."""
        lab, depo = self._lab_modul(), self._depo()
        hedef = sablon_ya_da_mod_id
        mod = depo.getir(hedef) if (depo is not None and isinstance(hedef, str)) else None
        if mod is not None:
            sablon, taban, stil = mod["sablon"], dict(mod["params"]), OZEL_STIL + ":" + mod["id"]
        elif lab is not None and isinstance(hedef, str) and hedef in lab.SABLONLAR:
            sablon, taban, stil = hedef, {}, OZEL_STIL
        else:
            durum = self.get_state()
            durum["lab"] = {"ok": False, "neden": "Mod ya da şablon bulunamadı."}
            return durum
        taban.update(lab.dogrula(params))
        tam = lab.tam_params(sablon, taban)
        if stil == OZEL_STIL:
            self._lab_taslak = tam
        self._lab_gecici = {"stil": stil, "tam": tam}
        durum = self.start_music(self._music_sens, stil)
        self._lab_gecici = None
        durum["lab"] = {"ok": True}
        return durum

    def _ayar_kaydet(self):
        with self._lock:
            veri = {"send_hz": self._send_hz_temel, "spec_shift": self._spec_shift,
                    "spec_smooth": self._spec_smooth, "vokal_agirlik": self._vokal_agirlik,
                    "yogunluk": self._yogunluk, "gecikme_ms": self._gecikme_ms,
                    "dio_kayma": self._dio_kayma}
        try:
            tmp = AYAR_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(veri, f)
            os.replace(tmp, AYAR_PATH)
        except OSError:
            pass

    def set_timer(self, minutes):
        dk = _sayi(minutes, 0, ZAMANLAYICI_MAX_DK, None)
        if dk is None:
            return self.get_state()
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = None
            self._timer_token = None
            self._timer_end = None
            if dk > 0:
                token = object()
                t = threading.Timer(dk * 60.0, self._zamanlayici_bitti, args=(token,))
                t.daemon = True
                self._timer = t
                self._timer_token = token
                self._timer_end = time.monotonic() + dk * 60.0
                t.start()
        return self.get_state()
