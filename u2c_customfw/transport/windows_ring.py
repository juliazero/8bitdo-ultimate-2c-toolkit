# SPDX-License-Identifier: GPL-3.0-or-later
"""Read the optional private ring characteristic from the selected Windows BLE pad."""
import asyncio

from .ring_protocol import SERVICE_UUID, LEVEL_UUID, decode_level
from .windows import _ring_device
from ..web.windows_battery import bluetooth_address


async def _read(address):
    from winrt.windows.devices.bluetooth import BluetoothLEDevice, BluetoothCacheMode, BluetoothConnectionStatus
    from winrt.windows.devices.bluetooth.genericattributeprofile import GattCommunicationStatus
    from winrt.windows.storage import streams  # register the IBuffer projection

    device = await BluetoothLEDevice.from_bluetooth_address_async(address)
    if device is None:
        raise OSError("The Bluetooth controller is unavailable.")
    services = []
    try:
        if device.connection_status != BluetoothConnectionStatus.CONNECTED:
            raise OSError("The Bluetooth controller is no longer connected.")
        result = await device.get_gatt_services_for_uuid_with_cache_mode_async(SERVICE_UUID, BluetoothCacheMode.UNCACHED)
        services = list(result.services or ())
        if result.status != GattCommunicationStatus.SUCCESS or len(services) != 1:
            raise OSError("Install the Bluetooth ring read tweak and pair the controller again.")
        result = await services[0].get_characteristics_for_uuid_with_cache_mode_async(LEVEL_UUID, BluetoothCacheMode.UNCACHED)
        if result.status != GattCommunicationStatus.SUCCESS or len(result.characteristics) != 1:
            raise OSError("The ring characteristic is unavailable. Pair the controller again.")
        result = await result.characteristics[0].read_value_with_cache_mode_async(BluetoothCacheMode.UNCACHED)
        if result.status != GattCommunicationStatus.SUCCESS:
            raise OSError("The controller did not return its ring level.")
        return decode_level(bytearray(result.value))
    finally:
        for service in services:
            service.close()
        device.close()


def read_bluetooth_ring(profile):
    _, path, _ = _ring_device(profile)
    address = bluetooth_address(path)
    try:
        return asyncio.run(asyncio.wait_for(_read(address), timeout=12))
    except TimeoutError as exc:
        raise OSError("Bluetooth ring read timed out.") from exc
    except ImportError as exc:
        raise RuntimeError("Windows BLE support is missing. Rebuild the desktop application.") from exc
