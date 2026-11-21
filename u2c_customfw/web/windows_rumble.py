# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded Windows XInput vibration tests, independent of browser haptics."""
import ctypes as C
import sys
import threading


class Vibration(C.Structure):
    _fields_ = [("left", C.c_uint16), ("right", C.c_uint16)]


class XInputRumble:
    def __init__(self):
        self.dll = None
        self.lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.active_index = None
        self.stop_event = threading.Event()

    def stop(self, index):
        with self.state_lock:
            if self.active_index != index:
                return {"ok": True}
            self.stop_event.set()
            error = self._api().XInputSetState(index, C.byref(Vibration(0, 0)))
            return {"ok": not error, **({"error": f"XInput stop failed (error {error})."} if error else {})}

    def _api(self):
        if self.dll is None:
            if sys.platform != "win32":
                raise OSError("XInput vibration requires Windows.")
            for name in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
                try:
                    dll = C.WinDLL(name)
                except OSError:
                    continue
                dll.XInputGetState.argtypes = [C.c_uint32, C.c_void_p]
                dll.XInputGetState.restype = C.c_uint32
                dll.XInputSetState.argtypes = [C.c_uint32, C.POINTER(Vibration)]
                dll.XInputSetState.restype = C.c_uint32
                self.dll = dll
                break
            if self.dll is None:
                raise OSError("Windows XInput library is unavailable.")
        return self.dll

    def status(self):
        try:
            api = self._api()
            connected = [i for i in range(4) if api.XInputGetState(i, C.create_string_buffer(16)) == 0]
        except OSError as exc:
            return {"available": False, "error": str(exc)}
        if len(connected) != 1:
            return {"available": False, "error": "Connect exactly one XInput controller for the native vibration test."}
        return {"available": True, "index": connected[0]}

    def rumble(self, index, locality):
        if type(index) is not int or not 0 <= index < 4 or locality not in ("default", "left", "right"):
            return {"ok": False, "error": "Invalid vibration request."}
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "error": "A vibration test is already running."}
        try:
            status = self.status()
            if not status["available"] or status["index"] != index:
                return {"ok": False, "error": status.get("error", "The selected controller disconnected. Try again.")}
            api = self._api()
            left, right = {"default": (0x5000, 0x5000), "left": (0x5000, 0), "right": (0, 0x5000)}[locality]
            with self.state_lock:
                self.stop_event.clear()
                error = api.XInputSetState(index, C.byref(Vibration(left, right)))
                if error:
                    return {"ok": False, "error": f"XInput vibration failed (error {error})."}
                self.active_index = index
            try:
                self.stop_event.wait(0.45)
            finally:
                with self.state_lock:
                    try:
                        error = api.XInputSetState(index, C.byref(Vibration(0, 0)))
                        if error:  # retry a failed stop, never replay the start
                            error = api.XInputSetState(index, C.byref(Vibration(0, 0)))
                    finally:
                        self.active_index = None
            if error:
                return {"ok": False, "error": f"XInput could not confirm stopping vibration (error {error})."}
            return {"ok": True}
        finally:
            self.lock.release()


RUMBLE = XInputRumble()
