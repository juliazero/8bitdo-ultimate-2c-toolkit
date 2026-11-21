# Experimental D-input regression A/B firmware build

This is a diagnostic A/B variant against the original user-provided `2c16a67` branch, **not a confirmed fix**.

The earlier build was reported to support wired D-input diagnostic rumble and automatic entry into firmware update mode. The current app's vendor diagnostic rumble Python module is byte-for-byte unchanged from that earlier branch; however, three firmware blobs have changed:

| Blob | Function | A/B action |
| --- | --- | --- |
| `usbmode.bin` | USB mode-switching / battery-log region at `0x9964` | Restored exact original `2c16a67` bytes (6 bytes differ) |
| `vhooka.bin` | Vendor command hook at `0xf83c` | Restored original `2c16a67` bytes |
| `vhookb.bin` | Vendor command hook at `0xf842` | Restored original `2c16a67` bytes |

The later blobs have been retained as `*-prebaseline.bin` and declared recognized earlier forms. The new `bt_switch2` Y rumble blob and all other patches were not changed.

The changed hooks bypass the upstream 1.2 battery-vendor answer wrapper. Consequently, **USB live battery-answer support added in upstream 1.2 might not work in this diagnostic build**. This variant intentionally prioritizes reproducing the earlier working USB path, not preserving every new feature.

Do not flash without first retaining a complete known-good backup and verifying that the application identifies firmware 1.09. Test wired D-input diagnostic vibration and the automatic transition into update mode separately, and record which one changes. If neither improves, restore the current mainline blobs; the cause lies elsewhere. Do not repeatedly flash merely to resolve enumeration problems.

Validation: 96 selected tests passed, 5 skipped. A broader web suite still has an unrelated existing job-lifecycle failure (`test_battery_log_rejects_dongle_bootloader`); no hardware test has been performed. `firmware-src/hook.s` remains the upstream source for the non-baseline variant; do not regenerate the restored blobs until the A/B diagnosis is complete.
