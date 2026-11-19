"""Giris noktasi: pywebview penceresi, sistem tepsisi ve genel kisayol.

Pencerenin X'i paneli gercekten kapatir (Arda, 2026-11-19: X tepsiye gizleyince her kisayol
tiklamasi yeni bir panel aciyor, paneller birikip seridi eziyordu). Tek panel kurali: panel zaten
aciksa yeni baslatilan kopya, acik olani one getirip hemen kapanir.
"""
import os
import socket
import sys
import threading

import webview
from PIL import Image, ImageDraw

import pystray
from controller import BASE_DIR, Api

KISAYOL = "ctrl+alt+l"
SIMGE = os.path.join(BASE_DIR, "icon.ico")   # masaustu kisayoluyla ayni simge
# Gorev cubugu: pythonw'nin Python simgesi yerine kendi simgemiz ve ayri grup (Arda, 2026-11-19)
UYGULAMA_KIMLIGI = "ArdaKarayel.MusicReactiveLED"
TEK_PORT = 47391   # yalniz 127.0.0.1: tek panel kilidi ve "one gel" mesaji


def _tek_panel_kilidi():
    """Ilk panel bu portu tutar. Port doluysa baska panel acik: ona "goster" gonderip None doner."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", TEK_PORT))
        s.listen(4)
        return s
    except OSError:
        s.close()
        try:
            with socket.create_connection(("127.0.0.1", TEK_PORT), timeout=2) as c:
                c.sendall(b"goster")
        except OSError:
            pass
        return None


def _kilidi_dinle(sunucu, one_getir):
    while True:
        try:
            c, _ = sunucu.accept()
        except OSError:
            return
        try:
            if c.recv(16).startswith(b"goster"):
                one_getir()
        except OSError:
            pass
        finally:
            c.close()


def _uygulama_kimligi():
    """Windows gorev cubugu bu kimlikle pencereyi python.exe grubundan ayirir, pencere simgesini gosterir."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(UYGULAMA_KIMLIGI)
    except Exception:
        pass


def _simge():
    try:
        return Image.open(SIMGE)
    except Exception:
        pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(255, 140, 40, 255), outline=(255, 255, 255, 255), width=3)
    return img


def _kisayol_kur(ac_kapa):
    """keyboard yoksa ya da yonetici izni gerekiyorsa sessizce atlanir."""
    try:
        import keyboard
        keyboard.add_hotkey(KISAYOL, lambda: threading.Thread(target=ac_kapa, daemon=True).start())
        return keyboard
    except Exception:
        return None


def main():
    kilit = _tek_panel_kilidi()
    if kilit is None:
        sys.exit(0)   # panel zaten acik; one getirildi
    _uygulama_kimligi()
    api = Api()
    durum = {"kapat": False, "icon": None}
    index = os.path.join(BASE_DIR, "web", "index.html")
    pencere = webview.create_window("LED Panel", url=index, js_api=api,
                                    width=1440, height=900, min_size=(400, 640),
                                    maximized=True)

    def ac_kapa():
        api.set_power(not api.get_state()["power"])

    def goster(icon=None, item=None):
        for f in (pencere.show, pencere.restore):
            try:
                f()
            except Exception:
                pass
        try:   # one getir: kisa sure "her zaman ustte" yap, sonra birak
            pencere.on_top = True
            pencere.on_top = False
        except Exception:
            pass

    def tepsi_ac_kapa(icon=None, item=None):
        threading.Thread(target=ac_kapa, daemon=True).start()

    def cikis(icon=None, item=None):
        durum["kapat"] = True
        if durum["icon"] is not None:
            durum["icon"].stop()
        pencere.destroy()

    def kapanirken(*args):
        # X paneli gercekten kapatir (tepsiye gizleme yok).
        durum["kapat"] = True
        if durum["icon"] is not None:
            try:
                durum["icon"].stop()
            except Exception:
                pass
        return True

    def tepsi_calistir():
        menu = pystray.Menu(
            pystray.MenuItem("Göster", goster, default=True),
            pystray.MenuItem("Aç/Kapat", tepsi_ac_kapa),
            pystray.MenuItem("Çıkış", cikis),
        )
        icon = pystray.Icon("led-panel", _simge(), "LED Panel", menu)
        durum["icon"] = icon
        try:
            icon.run()
        except Exception:
            pass

    pencere.events.closing += kapanirken
    threading.Thread(target=_kilidi_dinle, args=(kilit, goster), daemon=True).start()
    threading.Thread(target=tepsi_calistir, daemon=True).start()
    kb = _kisayol_kur(ac_kapa)

    try:
        webview.start(icon=SIMGE if os.path.isfile(SIMGE) else None)
    finally:
        if kb is not None:
            try:
                kb.unhook_all_hotkeys()
            except Exception:
                pass
        if durum["icon"] is not None:
            try:
                durum["icon"].stop()
            except Exception:
                pass
        api._kapat()
        try:
            kilit.close()
        except Exception:
            pass
        os._exit(0)   # arka planda thread kalmasin (ses yakalama, medya, tepsi)


if __name__ == "__main__":
    main()
