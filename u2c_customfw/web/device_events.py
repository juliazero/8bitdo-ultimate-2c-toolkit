# SPDX-License-Identifier: GPL-3.0-or-later
"""OS device notifications; no HID enumeration in the notification callback."""
import ctypes
import socket
import sys
import threading


class DeviceEvents:
    def __init__(self):
        self.condition = threading.Condition()
        self.generation = 0
        self.available = False
        self.closed = False
        self.cleanup = None
        try:
            if sys.platform == 'win32':
                self._windows()
            elif sys.platform.startswith('linux'):
                self._linux()
        except (OSError, AttributeError):
            pass

    def changed(self):
        with self.condition:
            self.generation += 1
            self.condition.notify_all()

    def wait(self, generation):
        with self.condition:
            if self.available and generation == self.generation:
                self.condition.wait_for(lambda: self.closed or generation != self.generation, timeout=20)
            return {'generation': self.generation, 'available': self.available}

    def _windows(self):
        from ctypes import wintypes as w
        # CM_NOTIFY_FILTER: four DWORDs and a union whose largest member is
        # WCHAR InstanceId[MAX_DEVICE_ID_LEN] (200 UTF-16 code units).
        class Filter(ctypes.Structure):
            _fields_ = [('size', w.DWORD), ('flags', w.DWORD), ('type', w.DWORD),
                        ('reserved', w.DWORD), ('data', w.WCHAR * 200)]
        callback_type = ctypes.WINFUNCTYPE(w.DWORD, w.HANDLE, ctypes.c_void_p,
                                         w.DWORD, ctypes.c_void_p, w.DWORD)
        @callback_type
        def callback(handle, context, action, data, size):
            if action in (0, 1):  # interface arrival/removal
                self.changed()
            return 0
        cfg = ctypes.WinDLL('cfgmgr32')
        register = cfg.CM_Register_Notification
        register.argtypes = [ctypes.POINTER(Filter), ctypes.c_void_p, callback_type, ctypes.POINTER(w.HANDLE)]
        register.restype = w.DWORD
        unregister = cfg.CM_Unregister_Notification
        unregister.argtypes = [w.HANDLE]
        unregister.restype = w.DWORD
        filt = Filter(); filt.size = ctypes.sizeof(filt); filt.flags = 1  # all interface classes
        handle = w.HANDLE()
        if register(ctypes.byref(filt), None, callback, ctypes.byref(handle)) == 0:
            self.available = True
            self._callback = callback  # must remain alive until unregister
            self.cleanup = lambda: unregister(handle)

    def _linux(self):
        sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, 15)
        try:
            sock.bind((0, 1))
            sock.settimeout(1)
        except OSError:
            sock.close()
            raise
        self.available = True
        self.cleanup = sock.close
        def listen():
            while not self.closed:
                try:
                    fields = set(sock.recv(65536).split(b'\0'))
                except socket.timeout:
                    continue
                except OSError:
                    break
                if fields.intersection({b'SUBSYSTEM=usb', b'SUBSYSTEM=hid', b'SUBSYSTEM=hidraw', b'SUBSYSTEM=input', b'SUBSYSTEM=bluetooth'}) and fields.intersection({b'ACTION=add', b'ACTION=remove', b'ACTION=change', b'ACTION=bind', b'ACTION=unbind'}):
                    self.changed()
        threading.Thread(target=listen, daemon=True).start()

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        if self.cleanup:
            self.cleanup()
            self.cleanup = None
