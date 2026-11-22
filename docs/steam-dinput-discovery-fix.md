# Steam-style DInput receiver discovery (PID 2DC8:301B)

The experimental `dongle_dinput_steam` firmware tweak changes the dongle's
DInput PID from 301C to 301B. The receiver's DInput `bcdDevice` is 0200;
the normal wired Ultimate 2C DInput controller uses a different revision.

Older Toolkit releases only recognized the receiver at 301B when its paired
**gamepad** HID collection (usage page 1, usage 5) was exposed. With the pad
off, the receiver may expose only a vendor service collection (FF7A or FFA0),
so Toolkit could incorrectly report that no receiver was connected and block
its firmware maintenance screen.

Toolkit now recognizes the idle service collection as `dongle`, the gamepad
collection as `dongle_paired`, and collapses the pair of collections when a
single receiver exposes both. It does not treat a wired controller with PID
301B but another `bcdDevice` as a dongle.

After replacing Toolkit, turn the pad **fully off** and unplug/replug the
receiver before using Firmware > Check First/Install Tweaks. Paired receivers
still cannot be flashed while the controller is connected. The receiver can
be restored by unchecking only the experimental `dongle_dinput_steam` tweak,
while keeping the proven RF battery tweaks selected.

No controller or dongle firmware payload, USB identifier, HID descriptor or
XUSB driver is changed by this Toolkit release. Seeing `XInput Controller #1`
in Steam while in XInput mode is expected; it does not itself indicate two
physical controllers.
