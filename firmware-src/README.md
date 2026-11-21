# Firmware sources

Our own code for the controller (Telink TC32, firmware 1.09) and the dongle (firmware 1.03). The built binaries are committed in
`u2c_customfw/firmware/blobs/` with a `MANIFEST` of their SHA-256, so users never need this toolchain.

| Blob | Placed at | What it is |
| --- | --- | --- |
| `packer.bin` | image `0xb908` (the stock Bluetooth packer's slot, up to `0xbad4`) | `pack_xbox`: the 16-byte Xbox Series X\|S input report |
| `report-map.bin` | `0x18274` (zero tail after the image) | the Xbox Series X\|S HID report map (283 bytes, captured by xpadneo) |
| `noop.bin` | `0x18390` | `noop_write`: accept output-report writes and ignore them |
| `rumble.bin` | `0x18390` | `rumble_write`: Xbox rumble to the motor setter `0xfa68`, the Xbox duration as its hold; through `rumble_tail` when that code is there. A report with bit 7 of its first byte set is a Home ring command instead |
| `longrumble.bin` | `0x1734c` (the stock Report Map, unused once `bt_xbox` points the attribute at `0x18274`), up to `0x173c0` | Long rumble over Bluetooth (`longrumble.s`): `long_write`, `long_hold` and `rearm`, so a rumble lasts its duration times (loop count + 1), as on an Xbox controller. Linked on its own by `build.sh`, with `rumble_write`'s address, and with the three blobs below |
| `lrhooka.bin`, `lrhookb.bin` | `0xfa72`, `0xfa88` | inside the motor setter: its masking of the hold, and its read of the hold on a zero-hold call, replaced by calls to `long_hold` and `rearm` |
| `lrwrite.bin` | `0x17764` | the output report's write callback, `long_write` instead of `rumble_write` (Thumb-style odd) |
| `led.bin` | image `0x7738`, run address `0x1738` | `main_tick`: LED pins to weak drive strength, then the motor routine `0xfaa0` |
| `hook.bin` | `0xa43c` | the main loop's call, redirected from `0xfaa0` to `main_tick` |
| `ringtail.bin` | `0x18400`, up to `0x18ff0` (blank on stock), run in place | the Home ring and settings: `rumble_tail` first, then `ring_tick` (PWM1 at the level, alerts and pulses, every 200 ms), `main_poll` (the L4 + R4 chord and the end of a pulse, every 100 ms) and the vendor commands. Its state is at RAM `0x841640` (dead RAM code, zeroed at image `0x7640`), the level installed at image `0x76d0`, and the saved level in settings field `+25` |
| `ringhook.bin` | `0xd4f0` | the LED task's call for the ring, redirected from `0xd36c` to `ring_tick` |
| `pollhook.bin` | `0xa484` | the main loop's call, redirected from `0xcfa4` to `main_poll`, which still calls it |
| `vhooka.bin`, `vhookb.bin` | `0xf83c`, `0xf842` | the vendor command checks, redirected from `0xf56c` and `0xf594` to `vendor_a` and `vendor_b`, which still call them |
| `hgamea.bin`, `hgameb.bin` | `0xdce6`, `0xeaf4` | the games' rumble, redirected from `0xfa68` to `game_motor` |
| `hbuzza.bin`, `hbuzzb.bin` | `0xa0f8`, `0xdaa8` | the buzz on connecting, redirected from `0xfa68` to `connect_buzz` |
| `hidle.bin` | `0xd1c8` | `idle_limit`: the idle counter and its limit, from the settings |
| `hstickl.bin`, `hstickr.bin` | image `0x6a90`, `0x6abc`, run address `0xa90`, `0xabc` | each stick's dead zone code, redirected from `0x890` to `stick_left` and `stick_right` |
| `hscan.bin` | `0xc910` | the button scan's call, redirected from `0xc3a8` to `scan_hook` |
| `sram.bin` | image `0x7210`, run address `0x1210` | `scan_hook`, in dead RAM code (the unused suspend routine) |
| `hidletask.bin`, `hunplug.bin`, `hota.bin` | `0xa480`, `0xea88`, `0x178cc` | Fixes: `idle_task`, `unplug_task`, and the Bluetooth update's write handler, now `no_write` |
| `hsweep.bin` | `0xa2dc` | Finer stick movement: `two_sweeps` before each report |
| `wdog.bin` | image `0x6020`, run address `0x20` (dead RAM code: the unused suspend routine's stall) | Processor rest (`rest.c`): `rest_main_poll`, the main loop's 100 ms call, which arms a 3 s watchdog (`0x800620`, capture 549 units of 2^18 ticks) when the flag at `0x18ff2` is 1, feeds it (`8` to `0x800623`) and then calls `main_poll` |
| `resthook.bin` | `0xa484` | the main loop's call, redirected from `main_poll` to `rest_main_poll`; takes over the ring's `pollhook.bin` when installed |
| `stall.bin` | image `0x848c`, run address `0x248c` (dead RAM code: an unused `memcmp`) | `stall_hook`: the Bluetooth branch's `motor_set(0, 0, 0)`, then, on battery with no hold running, the CPU stops for 1 ms or until the radio wakes it (`0x80` to `0x80006f`, wake mask `0x800078`, timer0) |
| `hstall.bin` | `0xa4ae` | the Bluetooth branch's call, redirected from `0xfa68` to `stall_hook` |
| `dinput.bin` | the dongle (firmware 1.03), `0xff48` (zeros after the image) | `link_mode` (`dongle.s`): at link-up, D-input for the controller's `0x8f` packets as in the dongle's 1.00, X-input otherwise. Linked on its own by `build.sh`, with 1.03's addresses |
| `ring02.bin`, `ringhook02.bin`, `rumble02.bin` | not installed | 0.2.0's versions, kept to recognise firmware it installed |
| `ringtail03.bin`, `ringhook03.bin`, `vhooka03.bin`, `vhookb03.bin`, `pollhook03.bin`, `rumble03.bin` | not installed | 0.3.0's versions, kept to recognise firmware it installed |

## Build

Telink's `tc32-elf-gcc` 4.5.1 and binutils come from [flyskywhy/tc32](https://github.com/flyskywhy/tc32). On a Mac, the
`macos` branch (commit `fdb1e7e`, x86-64 binaries, runs with Rosetta) needs no VM, and gave the same bytes as the Linux
binaries (commit `8ec375bb`) for every blob that existed when it was compared:

```bash
git clone --depth 1 -b macos https://github.com/flyskywhy/tc32.git /some/path/tc32
PATH="/some/path/tc32/bin:$PATH" firmware-src/build.sh /tmp/u2c-blobs
PYTHONPATH=. python3 tools/make_constants.py ultimate-2c-controller-v1.09.dat   # when an edit region changes
```

The linker's warning that `.rumble` overlaps a previous section is expected: `noop.bin` and `rumble.bin` are alternatives.

## Test

`python3 firmware-src/test.py` compiles `packer.c` natively (`-DHOST`) and checks every field of the input report
against a model built from `report-map.hex`, the rumble handler against the Xbox rumble report, and `ring_tick` against
the PWM1 and PB7 registers. It also compiles and runs `test_settings.c`, the tests of `settings.c`.

`PYTHONPATH=. python3 firmware-src/test_longrumble.py ultimate-2c-controller-v1.09.dat` builds the image as the app does
and runs Long rumble over Bluetooth in a Thumb-1 interpreter, with the stock rumble handler, motor setter and motor
routine as built, each write through the callback pointer: Android's single long request with and without the tweak, a
loop count honoured with no stop, SDL's resent request, pulses, buzzes, a Home ring command, a disconnection, and
Android's and SDL's longest requests run to their end.

## Experimental USB mode switching

`usbmode.bin` retains `battlog.bin` at `0x9964` and adds USB mode code at
`0x9a70`, within the existing dead soft-float area ending at `0x9cac`.
`usbhook.bin` wraps the original `0xa488` call; `usbvendora.bin` and
`usbvendorb.bin` wrap the ring/settings vendor handlers. State occupies the
20-byte gap `0x84165c..0x841670`. No additional application erase area is used.

`build.sh` generates these via `tools/build_usb_mode_prototype.py --integrated`
after building the existing blobs. The generator asserts code/log boundaries;
`packer.c` asserts the Home ring struct leaves the RAM gap available.
Run `tools/check_usb_mode_prototype.py --backup PATH` for instruction-level
checks on verified stock and integrated ring/settings/log/Xbox combinations.
See `docs/usb-mode-prototype.md` for the experimental gesture and limits.

## Five-second L4 + R4 shortcut

`tools/build_ring_hold.py --compiler PATH_TO_TC32_GCC` compiles the current
`main_poll` and verifies/resolves its three direct-call relocations. It preserves
the published shared block and adds the 148-byte poll at `0x18f50`.
`ringtail5.bin`, `pollhook5.bin` and `wdog5.bin` are the current payloads;
the previous files remain pinned for recognition. `build.sh` first builds the
published layout with `RING_LEGACY_LAYOUT`, then builds this separate poll.
`tools/check_ring_hold.py` executes the actual code with LED ticks, battery
alerts, save requests and both short/long button holds. Source-only native
harness tests also use the five-second function (without the legacy macro).


The Switch 2 pairing/GATT experiment in `switch2-pairing.c` and its journal
in `switch2-bonds.c`, plus `switch2-input.c` `switch2-security.c` and `switch2-memory.c`, are offline-only and are
not part of the installed tweaks. See `docs/switch2-prototype.md` for its TC32
build/check commands and the remaining GATT, persistence and radio work.
