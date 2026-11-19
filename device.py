"""Cihaz katmani: MockDevice ve TuyaDevice ayni arayuzu paylasir.

Ortak arayuz: connect(), start_watch(), set_power(bool), set_rgb(r, g, b),
close(), name, connected, error, initial, mock.
Gonderim metotlari basarida True, hatada False doner; istisna disari cikmaz.
"""
import colorsys
import json
import os
import threading

KALICI_SN = 0.5

YENIDEN_BAGLAN_SN = 5.0


def _hata_metni(x):
    if isinstance(x, dict):
        return str(x.get("Error", x))[:200]
    if isinstance(x, BaseException):
        return f"{type(x).__name__}: {x}"[:200]
    return str(x)[:200]


def _hsv_dp24(metin):
    """Tuya v2 renk DP'si 'hhhhssssvvvv' (hex; s ve v 0-1000) -> (h, s 0-100, v 0-100)."""
    if not isinstance(metin, str) or len(metin) != 12:
        return None
    try:
        h = int(metin[0:4], 16)
        s = int(metin[4:8], 16)
        v = int(metin[8:12], 16)
    except ValueError:
        return None
    return (float(min(360, h)), min(100.0, s / 10.0), min(100.0, v / 10.0))


class MockDevice:
    mock = True

    def __init__(self):
        self.name = "Sanal şerit"
        self.connected = False
        self.error = ""
        self.initial = None
        self.power = False
        self.rgb = (0, 0, 0)

    def connect(self):
        self.connected = True
        self.error = ""
        return True

    def start_watch(self):
        return None

    def set_power(self, on):
        self.power = bool(on)
        return True

    def set_rgb(self, r, g, b):
        self.rgb = (int(r), int(g), int(b))
        return True

    def close(self):
        self.connected = False


class TuyaDevice:
    mock = False

    def __init__(self, dev_id, ip, key, version="3.3", name="LED Şerit"):
        self.name = name
        self.connected = False
        self.error = "Bağlı değil"
        self.initial = None
        self._args = (dev_id, ip, key, version)
        self._bulb = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._watch_thread = None
        self._son_rgb = None
        self._kalici_timer = None

    def connect(self):
        """Cihaza baglanir ve durumunu okur. Basarida True."""
        try:
            import tinytuya

            dev_id, ip, key, version = self._args
            bulb = tinytuya.BulbDevice(dev_id, address=ip, local_key=key, version=float(version),
                                       connection_timeout=3)
            bulb.set_socketPersistent(True)
            bulb.set_socketRetryLimit(1)   # yanlis adreste uzun beklemesin, _ip_bul devreye girsin
            st = bulb.status()
            if not isinstance(st, dict) or "dps" not in st:
                raise RuntimeError(_hata_metni(st))
            dps = st.get("dps") or {}
            guc = dps.get("20")
            self.initial = {
                "power": None if guc is None else bool(guc),
                "hsv": _hsv_dp24(dps.get("24")),
            }
            if self._stop.is_set():
                bulb.close()
                return False
            with self._lock:
                eski = self._bulb
                self._bulb = bulb
                self.connected = True
                self.error = ""
            if eski is not None and eski is not bulb:
                try:
                    eski.close()
                except Exception:
                    pass
            return True
        except Exception as e:
            with self._lock:
                self.connected = False
                self.error = _hata_metni(e)
            # IP degismis olabilir (hotspot yeniden acilinca DHCP yeni adres verir): agda ara.
            self._ip_bul()
            return False

    def _ip_bul(self):
        """Cihazi kimligiyle yerel agda bulur; adres degistiyse gunceller ve config.json'a yazar."""
        try:
            import tinytuya

            dev_id, ip, key, version = self._args
            bulunan = tinytuya.find_device(dev_id) or {}
            yeni = bulunan.get("ip")
            if yeni and yeni != ip:
                self._args = (dev_id, yeni, key, version)
                self.error = f"Şerit yeni adreste bulundu ({yeni}), bağlanılıyor…"
                yol = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
                with open(yol, encoding="utf-8") as f:
                    cfg = json.load(f)
                cfg["ip"] = yeni
                tmp = yol + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                os.replace(tmp, yol)
        except Exception:
            pass

    def start_watch(self):
        """Baglanti kopuksa her 5 sn'de bir yeniden dener."""
        if self._watch_thread is None:
            self._watch_thread = threading.Thread(target=self._watch, daemon=True)
            self._watch_thread.start()

    def _watch(self):
        while not self._stop.wait(YENIDEN_BAGLAN_SN):
            if not self.connected:
                self.connect()

    def _send(self, fn):
        with self._lock:
            if not self.connected or self._bulb is None:
                return False
            try:
                res = fn(self._bulb)
            except Exception as e:
                res = e
            if isinstance(res, BaseException) or (isinstance(res, dict) and "Error" in res):
                self.connected = False
                self.error = _hata_metni(res)
                return False
            return True

    def set_power(self, on):
        if on:
            return self._send(lambda b: b.turn_on(nowait=False))
        return self._send(lambda b: b.turn_off(nowait=False))

    def set_rgb(self, r, g, b):
        """Hizli yol: DP28 (control_data, direct) aninda ve yumusamasiz degistirir ama kalici degil.
        Akis 0.5 sn durunca son renk DP24 ile kalici yazilir (Arda'nin testi 2026-11-17: DP24 10 Hz'te
        yetismedi, DP28 10 ve 20 Hz'te net)."""
        r, g, b = int(r), int(g), int(b)
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        deger = "0%04x%04x%04x%04x%04x" % (int(round(h * 360)) % 360, int(round(s * 1000)),
                                           int(round(v * 1000)), 0, 0)
        ok = self._send(lambda bulb: bulb.set_value(28, deger, nowait=True))
        with self._lock:
            self._son_rgb = (r, g, b)
            if self._kalici_timer is not None:
                self._kalici_timer.cancel()
            self._kalici_timer = threading.Timer(KALICI_SN, self._kalici_yaz)
            self._kalici_timer.daemon = True
            self._kalici_timer.start()
        return ok

    def _kalici_yaz(self):
        with self._lock:
            rgb = self._son_rgb
            self._kalici_timer = None
        if rgb is not None:
            self._send(lambda bulb: bulb.set_colour(rgb[0], rgb[1], rgb[2], nowait=True))

    def close(self):
        self._stop.set()
        with self._lock:
            bulb = self._bulb
            self._bulb = None
            self.connected = False
        if bulb is not None:
            try:
                bulb.close()
            except Exception:
                pass
