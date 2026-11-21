# SPDX-License-Identifier: GPL-3.0-or-later
"""Read Windows' cached Bluetooth battery percentage for the selected HID device."""
import ctypes as C
import sys
import uuid

from ..transport import windows


class PropertyKey(C.Structure):
    _fields_ = [("guid", C.c_ubyte * 16), ("pid", C.c_uint32)]

    @classmethod
    def make(cls, guid, pid):
        return cls((C.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le), pid)


INSTANCE = PropertyKey.make("78c34fc8-104a-4aca-9ea4-524d52996e57", 256)
BATTERY = PropertyKey.make("104ea319-6ee2-4701-bd47-8ddbf425bbe5", 2)


def _api():
    dll = C.WinDLL("cfgmgr32.dll")
    prop_args = [C.c_void_p, C.POINTER(PropertyKey), C.POINTER(C.c_uint32), C.c_void_p, C.POINTER(C.c_uint32), C.c_uint32]
    dll.CM_Get_Device_Interface_PropertyW.argtypes = [C.c_wchar_p, *prop_args[1:]]
    dll.CM_Get_DevNode_PropertyW.argtypes = [C.c_uint32, *prop_args[1:]]
    dll.CM_Locate_DevNodeW.argtypes = [C.POINTER(C.c_uint32), C.c_wchar_p, C.c_uint32]
    dll.CM_Get_Parent.argtypes = [C.POINTER(C.c_uint32), C.c_uint32, C.c_uint32]
    dll.CM_Get_Device_IDW.argtypes = [C.c_uint32, C.c_wchar_p, C.c_uint32, C.c_uint32]
    for name in ("CM_Get_Device_Interface_PropertyW", "CM_Get_DevNode_PropertyW", "CM_Locate_DevNodeW", "CM_Get_Parent", "CM_Get_Device_IDW"):
        getattr(dll, name).restype = C.c_uint32
    return dll


def _property(function, device, key):
    buffer = C.create_string_buffer(4096)
    size, kind = C.c_uint32(len(buffer)), C.c_uint32()
    if function(device, C.byref(key), C.byref(kind), buffer, C.byref(size), 0) != 0:
        return None
    return kind.value, buffer.raw[:size.value]


def _percentage(path):
    api = _api()
    path = path.decode("utf-8") if isinstance(path, bytes) else path
    instance = _property(api.CM_Get_Device_Interface_PropertyW, path, INSTANCE)
    if not instance or instance[0] != 0x12:  # DEVPROP_TYPE_STRING
        return None
    node = C.c_uint32()
    if api.CM_Locate_DevNodeW(C.byref(node), instance[1].decode("utf-16-le").rstrip("\0"), 0):
        return None
    for _ in range(12):
        value = _property(api.CM_Get_DevNode_PropertyW, node.value, BATTERY)
        if value and value[0] == 3 and len(value[1]) == 1 and value[1][0] <= 100:
            return value[1][0]  # DEVPROP_TYPE_BYTE, including a genuine zero percent
        identifier = C.create_unicode_buffer(512)
        if not api.CM_Get_Device_IDW(node.value, identifier, len(identifier), 0):
            if identifier.value.upper().startswith(("BTHLE\\DEV_", "BTHENUM\\DEV_")):
                break  # Do not wander into the Bluetooth adapter or another device.
        parent = C.c_uint32()
        if api.CM_Get_Parent(C.byref(parent), node.value, 0):
            break
        node = parent
    return None


def snapshot():
    unknown = {"level": -1, "state": "unknown"}
    if sys.platform != "win32":
        return unknown
    try:
        devices = [d for d in windows.list_devices() if "Bluetooth" in d.transport and d.usage_page == 1
                   and (d.vendor, d.product) in ((0x045E, 0x0B13), (0x2DC8, 0x301B))]
        if len(devices) != 1:
            return unknown
        percentage = _percentage(devices[0].handle)
        if percentage is not None:
            return {"level": percentage / 100, "state": "reported", "source": "Windows Bluetooth"}
    except (OSError, ValueError):
        pass
    return unknown
