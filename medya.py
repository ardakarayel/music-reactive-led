"""Calan sarki bilgisi ve kontrolu: Windows'un medya oturumu (GSMTC).

Spotify, tarayicidaki YouTube vb. hangisi calistiriyorsa. Bilgi arka planda 2 sn'de bir okunur;
komutlar (oynat/durdur, sonraki, onceki) istek aninda gonderilir. Paket yoksa ya da oturum yoksa
None / False doner, uygulama dusmez.
"""
import asyncio
import threading
import time
from datetime import datetime, timezone

# 1 sn: sarki degisimi ve seek daha cabuk yakalanir (kutuphane kaydi seek'te iptal eder);
# GSMTC okumasi ucuz.
YOKLAMA_SN = 1.0


def _sinif():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Yonetici,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as Durum,
    )
    return Yonetici, Durum


class Medya:
    def __init__(self):
        self._lock = threading.Lock()
        self._parca = None
        self._dur = threading.Event()
        self._uyandir = threading.Event()
        threading.Thread(target=self._calis, daemon=True).start()

    def parca(self):
        """{"ad", "sanatci", "kaynak", "caliyor", "uygulama", "sure", "konum", "konum_t"} ya da None.

        sure/konum sn (float|None); konum_t: konumun olculdugu an (time.monotonic)."""
        with self._lock:
            return dict(self._parca) if self._parca else None

    def kapat(self):
        self._dur.set()
        self._uyandir.set()

    def komut(self, ad):
        """ad: "oynat_durdur" | "sonraki" | "onceki". Basarida True."""
        sonuc = {"ok": False}

        def is_():
            try:
                Yonetici, _ = _sinif()

                async def gonder():
                    m = await Yonetici.request_async()
                    s = m.get_current_session()
                    if s is None:
                        return False
                    if ad == "oynat_durdur":
                        return bool(await s.try_toggle_play_pause_async())
                    if ad == "sonraki":
                        return bool(await s.try_skip_next_async())
                    if ad == "onceki":
                        return bool(await s.try_skip_previous_async())
                    return False

                sonuc["ok"] = asyncio.run(asyncio.wait_for(gonder(), timeout=3.0))
            except Exception:
                sonuc["ok"] = False

        t = threading.Thread(target=is_, daemon=True)
        t.start()
        t.join(4.0)
        self._uyandir.set()   # bilgiyi hemen tazele
        return bool(sonuc["ok"])

    def _calis(self):
        try:
            Yonetici, Durum = _sinif()
        except Exception:
            return

        async def oku():
            m = await Yonetici.request_async()
            s = m.get_current_session()
            if s is None:
                return None
            bilgi = s.get_playback_info()
            caliyor = bool(bilgi is not None and bilgi.playback_status == Durum.PLAYING)
            p = await s.try_get_media_properties_async()
            ad = (p.title or "").strip()
            if not ad:
                return None
            uyg = s.source_app_user_model_id or ""
            sure = konum = konum_t = None
            try:
                z = s.get_timeline_properties()
                if z is not None:
                    sure = (z.end_time - z.start_time).total_seconds()
                    konum = z.position.total_seconds()
                    if sure <= 0:
                        sure = None
                    gun = z.last_updated_time
                    if konum is not None and gun is not None:
                        if gun.tzinfo is None:
                            gun = gun.replace(tzinfo=timezone.utc)
                        yas = (datetime.now(timezone.utc) - gun).total_seconds()
                        yas = max(0.0, yas)
                        konum_t = time.monotonic() - yas
            except Exception:
                sure = konum = konum_t = None
            return {"ad": ad, "sanatci": (p.artist or "").strip(),
                    "kaynak": uyg, "caliyor": caliyor,
                    "uygulama": uyg, "sure": sure, "konum": konum, "konum_t": konum_t}

        dongu = asyncio.new_event_loop()
        while not self._dur.is_set():
            try:
                sonuc = dongu.run_until_complete(asyncio.wait_for(oku(), timeout=3.0))
            except Exception:
                sonuc = None
            with self._lock:
                self._parca = sonuc
            self._uyandir.wait(YOKLAMA_SN)
            self._uyandir.clear()
        dongu.close()


def simdiki_konum(parca):
    """Calarken konum + gecen sure, degilse konum (sn). Bilinmiyorsa None."""
    try:
        if not parca or parca.get("konum") is None:
            return None
        k = float(parca["konum"])
        t = parca.get("konum_t")
        if parca.get("caliyor") and t is not None:
            k += max(0.0, time.monotonic() - t)
        return k
    except Exception:
        return None
