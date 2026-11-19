# -*- coding: utf-8 -*-
"""Yalniz bir uygulamanin (Spotify.exe) sesini yakalar: Windows process loopback.

Saf ctypes (ek paket yok). ActivateAudioInterfaceAsync + VAD\\Process_Loopback,
INCLUDE_TARGET_PROCESS_TREE. Windows 10 2004+ / 11 gerekir.
Not: 64 bit Python varsayilir (vtable isaretci boyutu 8).
"""
import ctypes
import threading
import time
import uuid
from ctypes import wintypes

import numpy as np

_ole32 = ctypes.OleDLL("ole32")
_mmdev = ctypes.OleDLL("Mmdevapi")
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]

S_OK = 0
E_NOINTERFACE = 0x80004002 - (1 << 32)
RPC_E_CHANGED_MODE = 0x80010106 - (1 << 32)


class GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16),
                ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    def __eq__(self, o):
        return bytes(self) == bytes(o)

    def __hash__(self):
        return hash(bytes(self))


def _guid(s):
    u = uuid.UUID(s)
    g = GUID()
    g.a, g.b, g.c = u.time_low, u.time_mid, u.time_hi_version
    for i, v in enumerate(bytes([u.clock_seq_hi_variant, u.clock_seq_low]) + u.node.to_bytes(6, "big")):
        g.d[i] = v
    return g


IID_IUnknown = _guid("00000000-0000-0000-C000-000000000046")
IID_IAgileObject = _guid("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90")
IID_Handler = _guid("41D949AB-9862-444A-80F6-C261334DA5EB")
IID_IAudioClient = _guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
IID_IAudioCaptureClient = _guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317")


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", ctypes.c_uint16), ("nChannels", ctypes.c_uint16),
                ("nSamplesPerSec", ctypes.c_uint32), ("nAvgBytesPerSec", ctypes.c_uint32),
                ("nBlockAlign", ctypes.c_uint16), ("wBitsPerSample", ctypes.c_uint16),
                ("cbSize", ctypes.c_uint16)]


class _PROCLOOP(ctypes.Structure):
    _fields_ = [("TargetProcessId", ctypes.c_uint32), ("ProcessLoopbackMode", ctypes.c_int)]


class _ACTPARAMS(ctypes.Structure):
    _fields_ = [("ActivationType", ctypes.c_int), ("p", _PROCLOOP)]


class _BLOB(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("pBlobData", ctypes.c_void_p)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_uint16), ("r1", ctypes.c_uint16), ("r2", ctypes.c_uint16),
                ("r3", ctypes.c_uint16), ("blob", _BLOB)]


def _vt(obj, idx, *argtypes):
    """COM nesnesinin idx. vtable metodunu cagrilabilir yapar (this otomatik)."""
    vtbl = ctypes.c_void_p.from_address(obj).value
    fn = ctypes.c_void_p.from_address(vtbl + idx * 8).value
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    f = proto(fn)
    return lambda *a: f(obj, *a)


def _release(obj):
    if obj:
        try:
            _vt(obj, 2)()
        except Exception:
            pass


class _PE(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def _surecler():
    """[(pid, ppid, exe_adi_kucuk)] ToolHelp32 ile."""
    k = _k32
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE)]
    h = k.CreateToolhelp32Snapshot(0x2, 0)
    if not h or h == ctypes.c_void_p(-1).value:
        return []
    out = []
    try:
        pe = _PE()
        pe.dwSize = ctypes.sizeof(_PE)
        ok = k.Process32FirstW(h, ctypes.byref(pe))
        while ok:
            out.append((pe.th32ProcessID, pe.th32ParentProcessID, pe.szExeFile.lower()))
            ok = k.Process32NextW(h, ctypes.byref(pe))
    finally:
        k.CloseHandle(h)
    return out


def spotify_pid(exe_adi="Spotify.exe"):
    """Agacin kokunu dondurur (ebeveyni baska bir exe_adi sureci olmayan). Yoksa None."""
    ad = exe_adi.lower()
    try:
        tum = _surecler()
    except Exception:
        return None
    eslesen = {p: pp for p, pp, a in tum if a == ad}
    kokler = [p for p, pp in eslesen.items() if pp not in eslesen]
    if not kokler:
        return None
    if len(kokler) == 1:
        return kokler[0]

    def say(k):  # kokun altindaki surec sayisi
        grup = {k}
        degisti = True
        while degisti:
            degisti = False
            for p, pp, _ in tum:
                if pp in grup and p not in grup:
                    grup.add(p)
                    degisti = True
        return len(grup)
    return max(kokler, key=say)


class UygulamaSesi:
    def __init__(self, exe_adi="Spotify.exe", rate=44100, kanal=2):
        self.exe_adi = exe_adi
        self.rate = int(rate)
        self.kanal = int(kanal)
        self.hata = ""
        self._ac = False
        self._client = 0
        self._cap = 0
        self._pid = None
        self._ham = np.zeros((0, self.kanal), np.float32)
        self._pcm16 = False
        self._com_init = False
        self._handler_ref = None

    @property
    def acik(self):
        return self._ac

    # ---- COM tamamlama isleyicisi (IAgileObject de bildirir) ----
    def _isleyici_yap(self, olay):
        PV = ctypes.c_void_p
        QI = ctypes.WINFUNCTYPE(ctypes.c_long, PV, ctypes.POINTER(GUID), ctypes.POINTER(PV))
        AR = ctypes.WINFUNCTYPE(ctypes.c_ulong, PV)
        AC = ctypes.WINFUNCTYPE(ctypes.c_long, PV, PV)

        def qi(this, riid, ppv):
            g = riid.contents
            if g == IID_IUnknown or g == IID_IAgileObject or g == IID_Handler:
                ppv[0] = this
                return S_OK
            ppv[0] = None
            return E_NOINTERFACE

        def addref(this):
            return 2

        def release(this):
            return 1

        def completed(this, op):
            olay.set()
            return S_OK

        fns = (QI(qi), AR(addref), AR(release), AC(completed))
        vtbl = (ctypes.c_void_p * 4)(*[ctypes.cast(f, ctypes.c_void_p) for f in fns])
        obj = ctypes.c_void_p(ctypes.addressof(vtbl))
        self._handler_ref = (fns, vtbl, obj)  # GC'den korunmali
        return ctypes.addressof(obj)

    def _hatali(self, m):
        self.hata = m
        return False

    def ac(self):
        if self._ac:
            return True
        self.hata = ""
        try:
            if self._ac_ic():
                return True
            self._temizle()
            return False
        except Exception as e:
            self._temizle()
            return self._hatali("istisna: %s: %s" % (type(e).__name__, e))

    def _ac_ic(self):
        pid = spotify_pid(self.exe_adi)
        if pid is None:
            return self._hatali("%s calismiyor" % self.exe_adi)
        self._pid = pid
        hr = _ole32.CoInitializeEx(None, 0)  # MTA
        if hr in (0, 1):
            self._com_init = True
        elif hr != RPC_E_CHANGED_MODE:
            return self._hatali("CoInitializeEx hr=0x%08X" % (hr & 0xFFFFFFFF))

        prm = _ACTPARAMS()
        prm.ActivationType = 1  # PROCESS_LOOPBACK
        prm.p.TargetProcessId = pid
        prm.p.ProcessLoopbackMode = 0  # INCLUDE_TARGET_PROCESS_TREE
        pv = _PROPVARIANT()
        pv.vt = 65  # VT_BLOB
        pv.blob.cbSize = ctypes.sizeof(prm)
        pv.blob.pBlobData = ctypes.addressof(prm)

        olay = threading.Event()
        h = self._isleyici_yap(olay)
        op = ctypes.c_void_p()
        fn = _mmdev.ActivateAudioInterfaceAsync
        fn.restype = ctypes.c_long
        fn.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(GUID), ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        hr = fn("VAD\\Process_Loopback", ctypes.byref(IID_IAudioClient),
                ctypes.byref(pv), h, ctypes.byref(op))
        if hr != 0:
            return self._hatali("ActivateAudioInterfaceAsync hr=0x%08X" % (hr & 0xFFFFFFFF))
        try:
            if not olay.wait(5.0):
                return self._hatali("etkinlestirme zaman asimi")
            ahr = ctypes.c_long()
            unk = ctypes.c_void_p()
            hr = _vt(op.value, 3, ctypes.POINTER(ctypes.c_long),
                     ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(ahr), ctypes.byref(unk))
            if hr != 0 or ahr.value != 0 or not unk.value:
                return self._hatali("GetActivateResult hr=0x%08X act=0x%08X"
                                    % (hr & 0xFFFFFFFF, ahr.value & 0xFFFFFFFF))
            cl = ctypes.c_void_p()
            hr = _vt(unk.value, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
                ctypes.byref(IID_IAudioClient), ctypes.byref(cl))
            _release(unk.value)
            if hr != 0:
                return self._hatali("IAudioClient QI hr=0x%08X" % (hr & 0xFFFFFFFF))
            self._client = cl.value
        finally:
            _release(op.value)

        flags = 0x00020000 | 0x80000000 | 0x08000000  # LOOPBACK|AUTOCONVERTPCM|SRC_QUALITY
        hr = -1
        for pcm16 in (False, True):
            wf = WAVEFORMATEX()
            wf.wFormatTag = 1 if pcm16 else 3
            wf.nChannels = self.kanal
            wf.nSamplesPerSec = self.rate
            wf.wBitsPerSample = 16 if pcm16 else 32
            wf.nBlockAlign = wf.nChannels * wf.wBitsPerSample // 8
            wf.nAvgBytesPerSec = wf.nBlockAlign * wf.nSamplesPerSec
            wf.cbSize = 0
            hr = _vt(self._client, 3, ctypes.c_int, wintypes.DWORD, ctypes.c_longlong,
                     ctypes.c_longlong, ctypes.c_void_p, ctypes.c_void_p)(
                0, flags, 2000000, 0, ctypes.byref(wf), None)
            if hr == 0:
                self._pcm16 = pcm16
                break
        if hr != 0:
            return self._hatali("Initialize hr=0x%08X" % (hr & 0xFFFFFFFF))
        cap = ctypes.c_void_p()
        hr = _vt(self._client, 14, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
            ctypes.byref(IID_IAudioCaptureClient), ctypes.byref(cap))
        if hr != 0:
            return self._hatali("GetService hr=0x%08X" % (hr & 0xFFFFFFFF))
        self._cap = cap.value
        hr = _vt(self._client, 10)()  # Start
        if hr != 0:
            return self._hatali("Start hr=0x%08X" % (hr & 0xFFFFFFFF))
        self._ham = np.zeros((0, self.kanal), np.float32)
        self._ac = True
        return True

    def _surec_canli(self):
        h = _k32.OpenProcess(0x00100000, False, self._pid)  # SYNCHRONIZE
        if not h:
            return False
        try:
            return _k32.WaitForSingleObject(h, 0) == 0x102
        finally:
            _k32.CloseHandle(h)

    def _cek(self):
        """Hazir paketleri _ham'a ekler. False: yakalama olu."""
        sz = ctypes.c_uint32()
        gnps = _vt(self._cap, 5, ctypes.POINTER(ctypes.c_uint32))
        while True:
            if gnps(ctypes.byref(sz)) < 0:
                return False
            if sz.value == 0:
                return True
            data = ctypes.c_void_p()
            nfr = ctypes.c_uint32()
            fl = wintypes.DWORD()
            hr = _vt(self._cap, 3, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32),
                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p)(
                ctypes.byref(data), ctypes.byref(nfr), ctypes.byref(fl), None, None)
            if hr < 0:
                return False
            n = nfr.value
            if n == 0 or hr != 0:  # AUDCLNT_S_BUFFER_EMPTY vb.
                return True
            if (fl.value & 0x2) or not data.value:  # SILENT
                blok = np.zeros((n, self.kanal), np.float32)
            elif self._pcm16:
                b = (ctypes.c_int16 * (n * self.kanal)).from_address(data.value)
                blok = (np.frombuffer(b, np.int16).astype(np.float32) / 32768.0).reshape(n, self.kanal)
            else:
                b = (ctypes.c_float * (n * self.kanal)).from_address(data.value)
                blok = np.array(np.frombuffer(b, np.float32), np.float32).reshape(n, self.kanal)
            _vt(self._cap, 4, ctypes.c_uint32)(n)
            self._ham = np.concatenate([self._ham, blok]) if len(self._ham) else blok

    def oku(self, n=512, zaman_asimi=0.5):
        if not self._ac:
            return None
        try:
            son = time.monotonic() + zaman_asimi
            while True:
                if not self._cek():
                    self.hata = "yakalama durdu"
                    return None
                if len(self._ham) >= n or time.monotonic() >= son:
                    break
                time.sleep(0.004)
            if len(self._ham) >= n:
                cikis, self._ham = self._ham[:n], self._ham[n:]
                return np.ascontiguousarray(cikis, np.float32)
            if not self._surec_canli():
                self.hata = "%s sonlandi" % self.exe_adi
                return None
            # zaman asimi: eldekini ver, kalani sessizlikle doldur
            cikis = np.zeros((n, self.kanal), np.float32)
            k = len(self._ham)
            if k:
                cikis[:k] = self._ham
                self._ham = self._ham[:0]
            return cikis
        except Exception as e:
            self.hata = "oku istisna: %s: %s" % (type(e).__name__, e)
            return None

    def _temizle(self):
        if self._client:
            try:
                _vt(self._client, 11)()  # Stop
            except Exception:
                pass
        _release(self._cap)
        _release(self._client)
        self._cap = self._client = 0
        if self._com_init:
            try:
                _ole32.CoUninitialize()
            except Exception:
                pass
            self._com_init = False
        self._ac = False

    def kapat(self):
        self._temizle()
