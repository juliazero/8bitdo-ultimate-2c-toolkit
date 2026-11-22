# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only Windows enumeration of the 2.4 GHz dongle's XUSB/HID paths.

The existing 2.4 GHz RF battery telemetry requires a vendor HID channel for
USB command 81 05 05. The original XInput dongle identity is 2DC8:310A
(which is also used by a wired controller); a separate HID MI_02 with usage
page FF7A may coexist with XUSB. This tool checks for compatible HID or
PnP interface is actually exposed, without changing drivers or firmware.

    python -m tools.dongle_xinput_interfaces --output xinput-interfaces.json

Do not install Zadig, WinUSB or another XUSB driver for this investigation.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

_PNP_SCRIPT = r'''
$ErrorActionPreference = 'SilentlyContinue'
$ids = @('VID_045E&PID_028E','VID_2DC8&PID_301C','VID_2DC8&PID_310A')
$results = @(Get-PnpDevice -PresentOnly | Where-Object {
    $id = $_.InstanceId.ToUpperInvariant()
    @($ids | Where-Object { $id.Contains($_) }).Count -gt 0
} | ForEach-Object {
    $parent = Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_Parent'
    $service = Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_Service'
    $hwids = Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_HardwareIds'
    $compatible = Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_CompatibleIds'
    [pscustomobject]@{
        instance_id = $_.InstanceId
        class = $_.Class
        name = $_.FriendlyName
        status = $_.Status
        service = [string]$service.Data
        parent = [string]$parent.Data
        hardware_ids = @($hwids.Data)
        compatible_ids = @($compatible.Data)
    }
})
ConvertTo-Json -InputObject @($results) -Depth 3 -Compress
'''


def hid_interfaces(hid):
    out = []
    for vid, pid in ((0x045E, 0x028E), (0x2DC8, 0x301C), (0x2DC8, 0x310A)):
        for item in hid.enumerate(vid, pid):
            path = item.get('path', '')
            text = path.decode('utf-8', errors='replace') if isinstance(path, bytes) else str(path)
            out.append({
                'vid': f'{vid:04x}', 'pid': f'{pid:04x}',
                'usage_page': f'{item.get("usage_page", 0):04x}',
                'usage': f'{item.get("usage", 0):04x}',
                'release': f'{item.get("release_number", 0):04x}',
                'interface_number': item.get('interface_number'),
                'product': item.get('product_string') or '',
                'manufacturer': item.get('manufacturer_string') or '',
                'path': text,
            })
    return out


def pnp_interfaces(run=subprocess.run):
    completed = run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', _PNP_SCRIPT],
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=25)
    if completed.returncode:
        return {'error': f'PowerShell failed with exit code {completed.returncode}', 'devices': []}
    try:
        data = json.loads(completed.stdout.strip())
    except (ValueError, TypeError):
        return {'error': 'Could not decode PnP device output', 'devices': []}
    return {'devices': data if isinstance(data, list) else [data]}


def diagnose(hid, run=subprocess.run):
    listed = hid_interfaces(hid)
    pnp = pnp_interfaces(run)
    # Do not treat the Windows-created gamepad collection as a command channel.
    vendor = [d for d in listed if d['usage_page'] in ('ff7a', 'ffa0')]
    return {
        'mode': 'read-only',
        'firmware_descriptor_expected': {
            'source': '8BitDo dongle v1.03 XInput config descriptor at 0xF228',
            'interface_count': 3,
            'interface_0': 'ff/5d/01 (Xbox 360)',
            'interface_1': '03/01/01 (HID)',
            'interface_2': '03/00/00 (HID, 64-byte IN 0x83 / OUT 0x06)',
            'note': 'Firmware advertises interface 2; this does not prove Windows exposes it.'
        },
        'xinput_vendor_hid_candidates': vendor,
        'hid': listed, 'pnp': pnp,
        'interpretation': ('A vendor HID path is visible. Verify its parent belongs to the '
                           '2.4 GHz dongle before attempting any memory read.' if vendor else
                           'No known vendor HID interface found. The XUSB gamepad input path '
                           'does not provide access to the custom battery read command.'),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('xinput-interfaces.json'))
    args = parser.parse_args(argv)
    if sys.platform != 'win32':
        parser.error('This diagnostic is Windows-only.')
    try:
        from u2c_customfw.transport.windows import _hid
        result = diagnose(_hid())
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except (ImportError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'XInput interface enumeration failed: {exc}', file=sys.stderr)
        return 1
    print(f'Saved a read-only USB/HID interface report to {args.output}.')
    print(result['interpretation'])
    print('Firmware v1.03 advertises three USB interfaces, including HID interface 2; '
          'the report shows which ones Windows exposes.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
